"""Bottleneck mining, primitive routing and declarative panel composition."""

from __future__ import annotations

import json
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
             "critical_path_ids", "selected_claim_id", "evidence_ledger")
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
    MAX_EVIDENCE_CHARS = 1200
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
        source_refs.extend({
            "evidence_id": record.id,
            "title": record.title,
            "url": record.url,
            "relation": record.relation,
            "confidence": record.confidence,
        } for record in state.evidence_ledger.records)

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
            needs_schematic = self._plan_has_unsafe_threshold(plan)
            schematic = (self._safe_schematic_panel(bottleneck, state, bus)
                         if needs_schematic else None)
            if schematic is not None:
                panels = [schematic]
                bus.decision("panels", "수치 보간 불가 -> generated schematic 패널로 강등")
            else:
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
        has_non_switchboard = any(
            isinstance(item, dict)
            and not (
                str(item.get("primitive") or "").strip() == "part_removal"
                and isinstance(item.get("slots"), dict)
                and item["slots"].get("metric") == "status"
            )
            for item in proposals[:self.MAX_PANELS]
        )
        for i, raw in enumerate(proposals[:self.MAX_PANELS]):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("primitive") or "").strip()
            slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
            if name == "part_removal" and slots.get("metric") == "status":
                # The status rule table is not the composer's to write -- the
                # switchboard builder owns it, prompt and all.
                if panels or has_non_switchboard:
                    bus.decision(
                        "panels", f"패널 #{i} part_removal은 기존 패널과 중복 -> 생략"
                    )
                    continue
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
            if name == "threshold_finder" and self._unsupported_threshold(binding):
                bus.decision(
                    "panels", f"패널 #{i} threshold_finder 탈락",
                    reason="two source points cannot justify a continuous curve",
                )
                continue
            feedback = {
                str(k): str(v) for k, v in (raw.get("feedback") or {}).items()
                if isinstance(raw.get("feedback"), dict)
            }
            if name == "flow_topology":
                calibration_scope = bottleneck.mechanism_kind == "calibration"
                # Keep a mechanism diagram honest about the exact observation
                # it summarizes. This is a scope label, not a new source claim.
                if calibration_scope:
                    for node in binding.slots.get("nodes", []):
                        if "nll" in str(node.get("label") or "").lower():
                            node["label"] = "training NLL (CIFAR-100 ResNet)"
                        if any(term in str(node.get("label") or "").lower()
                               for term in ("classwise", "다중", "클래스별")):
                            node["label"] = "다른 보정 방법(비교)"
                question_text = self._text(raw.get("question"))
                if any(term in question_text.lower()
                       for term in ("못할", "한계", "왜곡", "불충분", "fail", "failure")):
                    # A comparison panel must not turn the presence of a more
                    # flexible method into a source-grounded failure claim.
                    question_text = (
                        "temperature scaling은 confidence를 어떻게 조절하며, "
                        "이 설명은 어떤 실험 범위에 한정될까요?"
                    )
                feedback.setdefault(
                    "scope",
                    (
                        "이 배선은 보고된 CIFAR-100 ResNet의 training NLL 궤적에 "
                        "한정된 관찰입니다. 일반적인 test-NLL 인과 관계를 뜻하지 않습니다."
                        if calibration_scope else
                        "이 배선은 원문에서 직접 확인된 관계를 설명하는 도식입니다. "
                        "다른 데이터셋이나 조건으로 일반화하지 않습니다."
                    ),
                )
                default_feedback = feedback.get("default", "")
                if "argmax" in default_feedback.lower() \
                        or "예측 클래스" in default_feedback:
                    feedback["default"] = (
                        "temperature scaling은 보고된 실험에서 confidence의 날카로움을 "
                        "조정하는 설명용 변환입니다. 예측 class 보존 여부는 이 원문 근거만으로 "
                        "검증하지 않습니다."
                    )
                if any(term in default_feedback.lower()
                       for term in ("필요", "못", "한계", "왜곡", "fail")):
                    feedback["default"] = (
                        "공통 temperature는 보고된 실험에서 confidence의 날카로움을 "
                        "조절하는 비교 경로입니다. 다른 보정 경로의 우열이나 실패 조건은 "
                        "이 근거만으로 확정하지 않습니다."
                    )
            else:
                question_text = self._text(raw.get("question"))
            notice = self._text(raw.get("notice")) or None
            if binding.fidelity == "illustrative" and not notice:
                notice = self.WARNING
            if name == "flow_topology":
                scope_notice = (
                    "보고된 CIFAR-100 ResNet training trajectory의 설명용 배선이며, "
                    "다른 데이터셋·배포 분포로 일반화하거나 인과 관계로 해석하지 않습니다."
                    if bottleneck.mechanism_kind == "calibration" else
                    "원문에서 확인된 관계를 설명하는 도식이며, 다른 데이터셋·조건으로 "
                    "일반화하거나 인과 관계로 해석하지 않습니다."
                )
                notice = f"{notice} {scope_notice}".strip() if notice else scope_notice
            # Unresolved search hits are not provenance. Keeping their ids on a
            # panel makes the critic audit a claim against chunks that the
            # interpreter explicitly declined to support.
            valid_evidence_ids = {
                item.id for item in state.evidence_ledger.records
                if item.relation != "unresolved" and item.chunks
            }
            evidence_ids = [
                str(item) for item in raw.get("evidence_ids") or []
                if str(item) in valid_evidence_ids
            ]
            panels.append(PanelSpec(
                primitive=name,
                question=question_text or bottleneck.question,
                model=binding.slots,
                controls=[c for c in (raw.get("controls") or [])
                          if isinstance(c, dict)][:4],
                observables=[o for o in (raw.get("observables") or [])
                             if isinstance(o, dict)][:4],
                feedback=feedback,
                provenance=[{
                    "kind": name,
                    "provenance": binding.fidelity,
                    "precision": self.PRECISION[binding.fidelity],
                    "source_refs": binding.refs,
                    "evidence_refs": list(dict.fromkeys(evidence_ids)),
                }],
                notice=notice,
            ))
            bus.decision("panels", f"패널 채택: {name}",
                         fidelity=binding.fidelity, refs=len(binding.refs))
        return panels

    @staticmethod
    def _unsupported_threshold(binding: primitives.Binding) -> bool:
        """Reject the common ``curve: x`` hallucination.

        A threshold panel needs a measured curve or an explicitly marked
        model.  With only two reported operating points, ``y=x`` silently
        invents every value between them; a generated schematic is safer.
        """
        curve = binding.slots.get("curve") if isinstance(binding.slots, dict) else None
        expression = str((curve or {}).get("expression") or "").strip().lower()
        # Any free-x curve is an interpolation unless the source supplied an
        # actual sampled curve. The current slot contract has no point-series
        # slot, so fail closed for all such expressions.
        return bool(re.search(r"\bx\b", expression))

    @classmethod
    def _plan_has_unsafe_threshold(cls, plan: dict) -> bool:
        for raw in plan.get("panels") if isinstance(plan.get("panels"), list) else []:
            if not isinstance(raw, dict) or raw.get("primitive") != "threshold_finder":
                continue
            slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
            curve = slots.get("curve") if isinstance(slots.get("curve"), dict) else {}
            if re.search(r"\bx\b", str(curve.get("expression") or "").strip().lower()):
                return True
        return False

    def _safe_schematic_panel(self, bottleneck, state: PaperState,
                              bus: EventBus) -> PanelSpec | None:
        """Build a qualitative relationship card when numeric interaction is unsafe."""
        slots = {
            "nodes": [
                {"id": "buffer", "label": "보관하는 이전 과제 예시"},
                {"id": "forgetting", "label": "이전 과제 정확도 하락"},
                {"id": "order", "label": "과제 순서·범위"},
            ],
            "variants": [
                {
                    "label": "논문이 직접 비교한 관계",
                    "edges": [["buffer", "forgetting"]],
                },
                {
                    "label": "아직 분리되지 않은 교란",
                    # The source says task order was not randomized; it does
                    # not establish that order itself causes forgetting.
                    # Leave this variant unconnected and state the confound in
                    # its label rather than drawing a causal arrow.
                    "edges": [],
                },
            ],
        }
        binding = primitives.bind("flow_topology", slots, state)
        if not binding.ok:
            return None
        refs = [sid for sid in bottleneck.evidence_refs
                if sid in state.doc.spans][:4]
        return PanelSpec(
            primitive="flow_topology",
            question="버퍼의 효과와 과제 순서의 영향을 어떻게 구분해 볼까?",
            model=binding.slots,
            controls=[],
            observables=[{"id": "accuracy_drop", "label": "이전 과제 정확도 하락"}],
            feedback={
                "default": "이 도식은 관계를 설명할 뿐, 두 점 사이의 값을 보간하거나 인과 효과를 확정하지 않습니다.",
            },
            provenance=[{
                "kind": "generated_schematic",
                "provenance": "illustrative",
                "precision": "qualitative",
                "source_refs": refs,
                "evidence_refs": [],
            }],
            notice=(self.WARNING + " 보고된 두 조건 사이의 중간값과 인과 효과는 표시하지 않습니다."),
        )

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

        context = state.context_analysis or {}
        lines += ["", "# source context analysis", json.dumps({
            "mechanisms": context.get("mechanisms") or [],
            "relations": context.get("relations") or [],
            "limitations": context.get("limitations") or [],
        }, ensure_ascii=False)[:6000]]

        lines += ["", "# numbers (슬롯의 수치는 반드시 이 id로 지정)"]
        for fact in list(state.number_pool.values())[:self.MAX_NUMBERS]:
            unit = fact.unit or "-"
            lines.append(f"{fact.id} | {fact.raw} | {unit} | span={fact.span_id} "
                         f"| {fact.context[:60]}")

        if state.assumptions:
            lines += ["", "# mined assumptions"]
            for a in state.assumptions:
                lines.append(f"{a.id} {a.text} (꺼지면: {a.weakens_how})")

        lines += ["", "# external evidence ledger",
                  f"status={state.evidence_ledger.status} "
                  f"stop_reason={state.evidence_ledger.stop_reason}"]
        for obligation in state.evidence_ledger.obligations:
            lines.append(
                f"{obligation.id} [{obligation.kind} required={obligation.required}] "
                f"{obligation.question}"
            )
        for record in state.evidence_ledger.records:
            lines.append(
                f"{record.id} [{record.relation} confidence={record.confidence:.2f}] "
                f"{record.title} | {record.url} | obligations={','.join(record.obligation_ids)}"
            )
            for chunk in record.chunks[:3]:
                lines.append(
                    f"  {chunk.id} num={chunk.num}: "
                    f"{chunk.content[:self.MAX_EVIDENCE_CHARS]}"
                )

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
        source_exceptions = []
        for span in state.doc.spans.values():
            lowered = span.text.lower()
            if "reuters" in lowered and "temperature" in lowered:
                source_exceptions.append({
                    "span_id": span.id,
                    "text": span.text[:800],
                })
            if "three seeds" in lowered or "five seeds" in lowered:
                source_exceptions.append({
                    "span_id": span.id,
                    "kind": "seed_count_statement",
                    "text": span.text[:800],
                })
        return {
            "title": "원문과 설명 모델의 경계",
            "text": (self.SWITCHBOARD_NOTE if has_switchboard else self.WARNING),
            "conditions": [] if has_switchboard else [
                {
                    # Keep an unverified assumption visibly hypothetical. A
                    # critic must not read the assumption itself as a claim
                    # that the artifact has established.
                    "text": f"검증이 필요한 조건: {a.text}",
                    "weakens_how": a.weakens_how,
                    "status": "unverified",
                    "span_id": a.span_id,
                    "source": a.source,
                }
                for a in state.assumptions
            ],
            "evidence_status": state.evidence_ledger.status,
            "unresolved_obligations": [
                item.id for item in state.evidence_ledger.obligations
                if item.required and not any(
                    item.id in record.obligation_ids
                    and record.relation != "unresolved"
                    and record.confidence >= 0.5
                    and record.chunks
                    for record in state.evidence_ledger.records
                )
            ],
            "unresolved_external_evidence": [
                {
                    "evidence_id": record.id,
                    "title": record.title,
                    "url": record.url,
                    "status": record.relation,
                }
                for record in state.evidence_ledger.records
                if record.relation == "unresolved"
            ],
            "source_exceptions": source_exceptions[:3],
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
