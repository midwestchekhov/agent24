"""Bottleneck mining, primitive routing and declarative panel composition."""

from __future__ import annotations

import re

from ..clients import LLM
from ..events import EventBus
from .. import primitives
from ..state import (
    BottleneckSpec,
    InteractionSpec,
    ExplainerSpec,
    PanelSpec,
    PaperState,
)
from . import switchboard
from .base import (
    Stage,
    StageError,
)


class BottleneckMiner(Stage):
    """Select exactly one teachable bottleneck from the chosen frontier.

    This first implementation is deliberately deterministic. It uses the
    existing claim/span graph as the evidence boundary and leaves model-based
    wording to the optional editorial stage.
    """

    name = "bottleneck"
    reads = ("selected_claim_id", "claims", "doc", "source_path", "source_text",
             "context_analysis")
    writes = ("bottleneck",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = next((c for c in state.claims if c.id == state.selected_claim_id), None)
        if claim is None:
            raise StageError("no claim for bottleneck mining")
        refs = list(claim.evidence_span_ids)
        text = " ".join(
            state.doc.spans[sid].text for sid in refs if sid in state.doc.spans
        )
        # Routing is evidence-gated before accepting the model's vocabulary.
        # A model may call the Guo mechanism "training dynamics" or
        # "probabilistic overfitting"; those are still calibration mechanisms
        # and must not fall back to the legacy assumption-toggle artifact.
        document_context = " ".join(
            span.text for span in state.doc.spans.values()
            if span.page == 0 or span.page <= 2
        )
        corpus = f"{claim.text} {text} {document_context}".lower()
        calibration = bool(re.search(
            r"\bcalibrat\w*\b|\bconfidence\b|\btemperature\s+scaling\b|\bece\b|\bnll\b",
            corpus,
            flags=re.I,
        ))
        context_bottleneck = (state.context_analysis or {}).get("bottleneck")
        if isinstance(context_bottleneck, dict) \
                and str(context_bottleneck.get("question") or "").strip():
            valid_refs = [
                str(sid) for sid in context_bottleneck.get("evidence_refs") or refs
                if str(sid) in state.doc.spans
            ]
            known_claims = {c.id for c in state.claims}
            source_claim_ids = [
                str(cid) for cid in context_bottleneck.get("source_claim_ids") or []
                if str(cid) in known_claims
            ] or [claim.id]
            raw_kind = str(context_bottleneck.get("mechanism_kind") or "unknown")
            mechanism_kind = "calibration" if calibration else raw_kind
            if calibration:
                for sid, span in state.doc.spans.items():
                    lowered = span.text.lower()
                    if sid not in valid_refs and "temperature scaling" in lowered \
                            and ("single scalar" in lowered or "softmax" in lowered):
                        valid_refs.append(sid)
                        if len(valid_refs) >= 8:
                            break
            state.bottleneck = BottleneckSpec(
                question=str(context_bottleneck["question"]).strip(),
                why_hard=str(context_bottleneck.get("why_hard") or "").strip(),
                source_claim_ids=source_claim_ids,
                evidence_refs=list(dict.fromkeys(valid_refs or refs)),
                mechanism_kind=mechanism_kind,
                candidate_controls=(
                    ["temperature"] if calibration else
                    [str(v) for v in context_bottleneck.get("candidate_controls") or []]
                ),
                candidate_observables=(
                    ["correctness", "confidence"] if calibration else
                    [str(v) for v in context_bottleneck.get("candidate_observables") or []]
                ),
                learning_payoff=self._number(context_bottleneck.get("learning_payoff"), 0.5),
                data_sufficiency=(str(context_bottleneck.get("data_sufficiency") or "partial")
                                  if str(context_bottleneck.get("data_sufficiency") or "partial")
                                  in {"sufficient", "partial", "insufficient"} else "partial"),
                fidelity=(str(context_bottleneck.get("fidelity") or "medium")
                          if str(context_bottleneck.get("fidelity") or "medium")
                          in {"high", "medium", "low"} else "medium"),
            )
            bus.decision("bottleneck", "context analysis의 병목 사용",
                         question=state.bottleneck.question,
                         mechanism_kind=state.bottleneck.mechanism_kind)
            return
        # Do not let a keyword in a bibliography entry route an unrelated
        # paper into the ML calibration explainer. For PDFs, only the title/
        # abstract pages may influence primitive routing; the selected claim
        # remains the primary signal. Plain text has no page boundary.
        explicit_deltas = re.findall(
            r"(?:ablation|component)\s*[:：-]?\s*([A-Za-z][\w -]{1,32})\s*(?:delta|drop|change)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*%?",
            corpus,
            flags=re.I,
        )
        state.ablation_components = [
            {"component": name.strip(), "delta": float(value)}
            for name, value in explicit_deltas
        ]
        ablation = bool(state.ablation_components)
        if ablation:
            question = "구성 요소를 하나씩 빼면 결과가 어떻게 달라질까?"
            kind = "ablation"
            controls = ["component"]
            observables = ["metric_delta"]
            payoff = 0.9
            fidelity = "high"
        elif calibration:
            question = "정확도는 좋아지는데 확률 예측 품질은 왜 나빠질 수 있을까?"
            kind = "calibration"
            controls = ["temperature"]
            observables = ["correctness", "confidence"]
            payoff = 0.95
            fidelity = "high"
        else:
            question = "이 주장이 성립하려면 무엇이 함께 맞아야 할까?"
            kind = "claim_conditions"
            controls = []
            observables = ["claim_support"]
            payoff = 0.55
            fidelity = "medium"
        if self.llm:
            try:
                out = self.llm.structured(
                    role="bottleneck_miner",
                    prompt=f"# claim\n{claim.text}\n# evidence\n{text[:8000]}",
                    schema_hint="BottleneckSpec", bus=bus,
                )
                if isinstance(out, dict) and str(out.get("question") or "").strip():
                    question = str(out["question"]).strip()
                if isinstance(out, dict) and str(out.get("mechanism_kind") or "") in {"calibration", "ablation", "claim_conditions"}:
                    kind = str(out["mechanism_kind"])
            except Exception as exc:  # noqa: BLE001 - deterministic fallback is safe
                bus.decision("bottleneck", "모델 병목 제안 실패 -> 규칙 기반 병목 사용",
                             error=type(exc).__name__)
        state.bottleneck = BottleneckSpec(
            question=question,
            why_hard="논문은 결과를 한 문장으로 압축하지만, 그 결과가 만들어지는 조건과 메커니즘은 여러 문단에 흩어져 있다.",
            source_claim_ids=[claim.id],
            evidence_refs=refs,
            mechanism_kind=kind,
            candidate_controls=controls,
            candidate_observables=observables,
            learning_payoff=payoff,
            data_sufficiency="sufficient" if state.source_path or state.source_text else "partial",
            fidelity=fidelity,
        )
        bus.decision("bottleneck", "병목 1개 선택", question=question,
                     claim_id=claim.id, mechanism_kind=kind)

    @staticmethod
    def _number(value, default: float) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return default


class PanelComposer(Stage):
    """Turn the bottleneck into panels the reader can touch.

    The model proposes; the slot contract disposes. Each proposal names a
    primitive from `primitives.catalogue()` and fills its slots with number
    pool ids and span ids -- `primitives.bind` then resolves every id
    deterministically. A slot that resolves keeps its fidelity; one that does
    not demotes the panel to illustrative or kills it, with the reason on the
    event bus. The model writes questions and feedback; it never invents the
    model behind a control.

    There is no pre-routing: which primitives fit is decided by which slots
    the paper can fill. When nothing survives, part_removal(status) -- the
    assumption switchboard -- is the floor, because assumptions are mined on
    every run.
    """

    name = "panels"
    reads = ("bottleneck", "claims", "doc", "number_pool", "source_title",
             "context_analysis", "assumptions", "profile",
             "critical_path_ids", "selected_claim_id")
    writes = ("explainer", "spec", "explainer_route")
    budget_s = 6.0

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    WARNING = "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다."
    SWITCHBOARD_NOTE = ("이 화면은 논문의 수치를 재현하지 않습니다. 주장이 어떤 조건에 "
                        "기대고 있는지만 보여줍니다.")
    MAX_PANELS = 3
    MAX_NUMBERS = 80
    MAX_SPAN_CHARS = 700
    #: binding fidelity -> the precision label the critic reads
    PRECISION = {"measured": "exact", "derived": "approximate",
                 "illustrative": "qualitative"}

    def run(self, state: PaperState, bus: EventBus) -> None:
        bottleneck = state.bottleneck
        assert bottleneck is not None
        claim = next(c for c in state.claims if c.id == bottleneck.source_claim_ids[0])
        refs = list(dict.fromkeys(bottleneck.evidence_refs))
        source_refs = [
            {"span_id": sid, "page": state.doc.spans[sid].page,
             "kind": state.doc.spans[sid].kind}
            for sid in refs if sid in state.doc.spans
        ]

        plan: dict = {}
        if self.llm is not None:
            try:
                out = self.llm.structured(
                    role="panel_composer",
                    prompt=self._render(claim, bottleneck, state),
                    schema_hint="PanelPlan", bus=bus,
                )
                plan = out if isinstance(out, dict) else {}
            except Exception as exc:  # noqa: BLE001 -- the floor panel is safe
                bus.decision("panels", "모델 패널 제안 실패 -> 바닥 패널로 진행",
                             error=type(exc).__name__)

        panels = self._accept(plan, bottleneck, state, bus)
        if not panels:
            floor = switchboard.build_panel(state, bus, self.llm,
                                            bottleneck.question)
            if floor is not None:
                panels = [floor]
            bus.decision("panels",
                         "생존 패널 0개 -> part_removal(status) 바닥 패널",
                         floor=bool(panels))
        has_switchboard = any(
            p.primitive == "part_removal"
            and (p.model or {}).get("metric") == "status" for p in panels
        )
        state.explainer_route = panels[0].primitive if panels else None
        state.explainer = ExplainerSpec(
            title=state.source_title or claim.text[:80],
            thesis=claim.text,
            bottleneck=bottleneck,
            panels=panels,
            comparison={"available": False, "reason": "figure 픽셀 수치는 자동 복원하지 않음"},
            glossary=self._pairs(plan.get("glossary")),
            summary=(self._strings(plan.get("summary"))
                     or [bottleneck.question, bottleneck.why_hard]),
            critical_note=self._critical_note(state, has_switchboard),
            sources=source_refs,
        )
        if state.spec is None:
            # Compatibility shell for the critic. The switchboard builder
            # already left a real spec behind, and overwriting it would throw
            # away the rule table the critic has to check.
            state.spec = InteractionSpec(
                claim_id=claim.id, primitive="interactive_explainer",
                title=state.explainer.title, learning_goal=bottleneck.question,
                misconception=self._text(plan.get("misconception")),
                fidelity_warning=self.WARNING,
            )
        bus.decision("panels", "설명 패널 구성 완료", panels=len(panels),
                     max_panels=self.MAX_PANELS,
                     primitives=[p.primitive for p in panels])

    # -- acceptance: the model proposes, bind disposes --

    def _accept(self, plan: dict, bottleneck, state: PaperState,
                bus: EventBus) -> list[PanelSpec]:
        panels: list[PanelSpec] = []
        proposals = plan.get("panels") if isinstance(plan.get("panels"), list) else []
        for i, raw in enumerate(proposals[:self.MAX_PANELS]):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("primitive") or "").strip()
            slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
            if name == "part_removal" and slots.get("metric") == "status":
                # The status rule table is not the composer's to write -- the
                # switchboard builder owns it, prompt and all.
                panel = switchboard.build_panel(
                    state, bus, self.llm,
                    self._text(raw.get("question")) or bottleneck.question)
                if panel is not None:
                    panels.append(panel)
                continue
            binding = primitives.bind(name, slots, state)
            if not binding.ok:
                bus.decision("panels", f"패널 #{i} ({name or '?'}) 탈락",
                             problems=binding.problems)
                continue
            notice = self._text(raw.get("notice")) or None
            if binding.fidelity == "illustrative" and not notice:
                notice = self.WARNING
            panels.append(PanelSpec(
                primitive=name,
                question=self._text(raw.get("question")) or bottleneck.question,
                model=binding.slots,
                controls=[c for c in (raw.get("controls") or [])
                          if isinstance(c, dict)][:4],
                observables=[o for o in (raw.get("observables") or [])
                             if isinstance(o, dict)][:4],
                feedback={str(k): str(v) for k, v in (raw.get("feedback") or {}).items()
                          if isinstance(raw.get("feedback"), dict)},
                provenance=[{
                    "kind": name,
                    "provenance": binding.fidelity,
                    "precision": self.PRECISION[binding.fidelity],
                    "source_refs": binding.refs,
                }],
                notice=notice,
            ))
            bus.decision("panels", f"패널 채택: {name}",
                         fidelity=binding.fidelity, refs=len(binding.refs))
        return panels

    # -- prompt --

    def _render(self, claim, bottleneck, state: PaperState) -> str:
        lines = ["# bottleneck", bottleneck.question, bottleneck.why_hard,
                 "", "# claim", f"{claim.id} {claim.text}", "", "# evidence spans"]
        cited = list(dict.fromkeys(
            [*bottleneck.evidence_refs, *claim.evidence_span_ids]))
        for sid in cited[:12]:
            span = state.doc.spans.get(sid)
            if span is None:
                continue
            lines.append(f"{sid} [{span.kind}] {span.text[:self.MAX_SPAN_CHARS]}")

        lines += ["", "# numbers (슬롯의 수치는 반드시 이 id로 지정)"]
        for fact in list(state.number_pool.values())[:self.MAX_NUMBERS]:
            unit = fact.unit or "-"
            lines.append(f"{fact.id} | {fact.raw} | {unit} | span={fact.span_id} "
                         f"| {fact.context[:60]}")

        if state.assumptions:
            lines += ["", "# mined assumptions"]
            for a in state.assumptions:
                lines.append(f"{a.id} {a.text} (꺼지면: {a.weakens_how})")

        lines += ["", "# available primitives", primitives.catalogue(),
                  "", f"# reader level: {state.profile.level}"]
        return "\n".join(lines)

    # -- small sanitizers --

    @staticmethod
    def _text(raw) -> str:
        return str(raw or "").strip()

    @staticmethod
    def _strings(raw) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw[:3] if str(item).strip()]

    @staticmethod
    def _pairs(raw) -> list[dict[str, str]]:
        out = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("term") and item.get("definition"):
                out.append({"term": str(item["term"]), "definition": str(item["definition"])})
        return out[:6]

    def _critical_note(self, state: PaperState, has_switchboard: bool) -> dict:
        """The one place the mined assumptions reach a non-switchboard reader.

        When a switchboard panel is present the assumptions are its controls.
        Everywhere else they would be mined and thrown away, so they land here
        as the caveat -- the "비판적으로 볼 지점" paragraph, not a toggle.
        """
        return {
            "title": "원문과 설명 모델의 경계",
            "text": self.SWITCHBOARD_NOTE if has_switchboard else self.WARNING,
            "conditions": [] if has_switchboard else [
                {"text": a.text, "weakens_how": a.weakens_how,
                 "span_id": a.span_id, "source": a.source}
                for a in state.assumptions
            ],
        }


class KoreanEditorial(Stage):
    """Provide Korean-first copy without exposing internal graph vocabulary."""

    name = "editorial"
    reads = ("explainer", "bottleneck")
    writes = ("explainer",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.explainer is None:
            return
        editorial = {
            "hook": "결과 숫자 하나만 보면 놓치기 쉬운 연결고리를 직접 움직여 봅니다.",
            "instruction": "슬라이더를 움직이고, 무엇이 바뀌는지 한 문장으로 확인하세요.",
            "caveat": state.explainer.critical_note.get("text"),
            "language": "ko",
        }
        if self.llm:
            try:
                out = self.llm.structured(
                    role="korean_editorial",
                    prompt=f"# question\n{state.explainer.bottleneck.question}\n# caveat\n{editorial['caveat']}",
                    schema_hint="KoreanEditorial", bus=bus,
                )
                if isinstance(out, dict):
                    for key in ("hook", "instruction", "caveat"):
                        if str(out.get(key) or "").strip():
                            editorial[key] = str(out[key]).strip()
                    if isinstance(out.get("summary"), list) and out["summary"]:
                        editorial["summary"] = [str(item) for item in out["summary"][:3]]
            except Exception as exc:  # noqa: BLE001
                bus.decision("editorial", "모델 editorial 실패 -> 고정 한국어 문구 사용",
                             error=type(exc).__name__)
        state.explainer.editorial = editorial
        bus.decision("editorial", "한국어 설명 문구 확정", fields=list(state.explainer.editorial))
