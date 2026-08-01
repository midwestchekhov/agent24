"""Bottleneck mining, primitive routing and declarative panel composition."""

from __future__ import annotations

import re

from ..clients import LLM
from ..events import EventBus
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


class PrimitiveRouter(Stage):
    """Choose a whitelisted primitive using only available evidence."""

    name = "router"
    reads = ("bottleneck", "doc", "source_path", "source_text", "context_analysis")
    writes = ("explainer_route",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        bottleneck = state.bottleneck
        if bottleneck is None:
            raise StageError("bottleneck missing")
        if bottleneck.mechanism_kind == "ablation" and state.ablation_components:
            state.explainer_route = "ablation_explainer"
        elif bottleneck.mechanism_kind == "calibration" and (state.source_path or state.source_text):
            state.explainer_route = "calibration_explainer"
        elif (state.context_analysis or {}).get("mechanisms"):
            # General mechanisms use a schematic composer. The graph is only
            # the evidence boundary; it does not become a set of controls.
            state.explainer_route = "mechanism_explainer"
        else:
            state.explainer_route = "assumption_switchboard"
        if self.llm:
            try:
                out = self.llm.structured(
                    role="primitive_router",
                    prompt=f"mechanism={bottleneck.mechanism_kind}\nsource={bool(state.source_path or state.source_text)}",
                    schema_hint="PrimitiveRoute", bus=bus,
                )
                route = str(out.get("route") or "") if isinstance(out, dict) else ""
                if route == "assumption_switchboard" and state.explainer_route != route:
                    # The graph is an internal analysis boundary, not the
                    # product primitive. Once source evidence supports a
                    # mechanism explainer, an advisory model answer cannot
                    # demote it back to the old claim-toggle UI.
                    bus.decision(
                        "router",
                        "모델의 switchboard 강등 제안 무시 — source-supported explainer 유지",
                        proposed_route=route, kept_route=state.explainer_route,
                    )
                elif route in {"scaling_comparison", "generated_schematic"} \
                        and state.explainer_route == "calibration_explainer":
                    # Both routes are rendered by the calibration explainer
                    # composer; keep the bounded two-panel composition.
                    pass
            except Exception as exc:  # noqa: BLE001
                bus.decision("router", "모델 route 실패 -> 규칙 기반 route 사용",
                             error=type(exc).__name__)
        bus.decision("router", "허용 primitive 선택", route=state.explainer_route,
                     max_panels=3, source_figure_vision=False)


class PanelComposer(Stage):
    """Compose a bounded, declarative artifact; never emits executable code.

    Every route ends here: this stage is the only thing that writes an
    artifact. The assumption switchboard is not a rival composer any more, it
    is the panel this stage builds when no quantitative mechanism is available.
    """

    name = "panels"
    reads = ("bottleneck", "explainer_route", "claims", "doc", "number_pool",
             "source_title", "source_path", "source_text", "context_analysis",
             "assumptions", "profile", "critical_path_ids", "selected_claim_id")
    writes = ("explainer", "spec")
    budget_s = 6.0

    def __init__(self, llm: LLM | None = None, primitives: dict | None = None):
        self.llm = llm
        self.primitives = primitives or {}

    WARNING = "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다."
    SWITCHBOARD_NOTE = ("이 화면은 논문의 수치를 재현하지 않습니다. 주장이 어떤 조건에 "
                        "기대고 있는지만 보여줍니다.")

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
        if state.explainer_route == "ablation_explainer":
            panel = PanelSpec(
                primitive="ablation_toggle",
                question="component을 끄면 측정된 지표가 얼마나 변할까?",
                model={
                    "type": "lookup_series",
                    "deltas": [
                        {"component": item["component"], "delta": item["delta"]}
                        for item in state.ablation_components[:5]
                    ],
                },
                controls=[{"name": "component", "kind": "toggle", "provenance": "measured"}],
                observables=[{"name": "metric_delta", "label": "측정된 변화량"}],
                feedback={"default": "각 막대는 원문에 적힌 component별 변화량입니다."},
                provenance=[{
                    "kind": "component_delta", "provenance": "measured",
                    "precision": "approximate", "source_refs": refs,
                }],
            )
            panels = [panel]
        elif state.explainer_route == "mechanism_explainer":
            mechanism = ((state.context_analysis or {}).get("mechanisms") or [{}])[0]
            relation = mechanism.get("relations") if isinstance(mechanism, dict) else None
            panel = PanelSpec(
                primitive="generated_schematic",
                question=str((mechanism or {}).get("question")
                             or bottleneck.question),
                model={
                    "type": "relation_graph",
                    "relations": relation or [],
                    "entities": (mechanism or {}).get("entities", []),
                },
                observables=(mechanism or {}).get("observables", []),
                feedback={
                    "default": "이 도식은 원문에서 확인된 관계를 설명용으로 재구성합니다."
                },
                provenance=[{
                    "kind": "mechanism_relation",
                    "provenance": "source_stated",
                    "precision": "qualitative",
                    "source_refs": refs,
                }],
                notice=self.WARNING,
            )
            panels = [panel]
        elif state.explainer_route == "calibration_explainer":
            panels = [
            PanelSpec(
                primitive="generated_schematic",
                question="정답 여부와 확신의 정도는 같은 값일까?",
                model={
                    "type": "state_graph",
                    "nodes": ["예측", "정답 여부", "confidence"],
                    "edges": [["예측", "정답 여부"], ["예측", "confidence"]],
                },
                observables=[
                    {"name": "correctness", "label": "맞혔는가"},
                    {"name": "confidence", "label": "얼마나 확신했는가"},
                ],
                feedback={"default": "맞힌 비율과 확신이 잘 맞는지는 별도로 확인해야 합니다."},
                provenance=[{
                    "kind": "caption_direction", "provenance": "source_stated",
                    "precision": "qualitative", "source_refs": refs,
                }],
                notice=self.WARNING,
            ),
            PanelSpec(
                primitive="scaling_comparison",
                question="temperature T를 바꾸면 confidence가 어떻게 달라질까?",
                model={
                    "type": "formula",
                    "expression": "softmax(logits / T)",
                    "parameters": {"T": {"min": 0.5, "max": 5.0, "default": 1.0}},
                    "allowed_ops": ["+", "-", "*", "/", "pow", "min", "max", "log"],
                },
                controls=[{"name": "T", "kind": "slider", "min": 0.5, "max": 5.0, "default": 1.0,
                           "provenance": "illustrative", "precision": "qualitative"}],
                observables=[{"name": "confidence", "label": "confidence"}],
                feedback={
                    "low": "T가 작아지면 분포가 뾰족해져 확신이 커집니다.",
                    "high": "T가 커지면 분포가 평평해져 과한 확신을 누그러뜨립니다.",
                },
                provenance=[{
                    "kind": "formula", "provenance": "source_stated",
                    "precision": "exact", "source_refs": refs,
                }],
                notice="수식에 따른 설명용 모델입니다. 원문 곡선을 재생하지 않습니다.",
            ),
            ]
        else:
            # No quantitative mechanism survived. The claim's own conditions are
            # the teachable thing, so the switchboard becomes this run's panel.
            panel = switchboard.build_panel(
                state, bus, self.llm, self.primitives, bottleneck.question,
            )
            panels = [panel] if panel is not None else []
        # Panel layout is deterministic from the locked mechanism and
        # provenance. A second unconstrained panel-planning call would merely
        # rediscover the context pass and could reintroduce unsupported data.
        state.explainer = ExplainerSpec(
            title=state.source_title or claim.text[:80],
            thesis=claim.text,
            bottleneck=bottleneck,
            panels=panels,
            comparison={"available": False, "reason": "figure 픽셀 수치는 자동 복원하지 않음"},
            glossary=self._glossary(state),
            summary=self._summary(state, bottleneck),
            critical_note=self._critical_note(state),
            sources=source_refs,
        )
        if state.spec is None:
            # Compatibility shell for the critic. The switchboard route already
            # left a real spec behind, and overwriting it would throw away the
            # rule table the critic has to check.
            state.spec = InteractionSpec(
                claim_id=claim.id, primitive="interactive_explainer",
                title=state.explainer.title, learning_goal=bottleneck.question,
                misconception="정확도 하나만 보면 confidence도 자동으로 신뢰할 수 있다고 생각하는 것.",
                fidelity_warning=self.WARNING,
            )
        bus.decision("panels", "설명 패널 구성 완료", panels=len(panels),
                     max_panels=3, route=state.explainer_route)

    def _critical_note(self, state: PaperState) -> dict:
        """The one place the mined assumptions reach a non-switchboard reader.

        On the switchboard route they are the controls. Everywhere else they
        would be mined and thrown away, so they land here as the caveat -- the
        "비판적으로 볼 지점" paragraph, not a toggle.
        """
        switchboard = state.explainer_route == "assumption_switchboard"
        return {
            "title": "원문과 설명 모델의 경계",
            "text": self.SWITCHBOARD_NOTE if switchboard else self.WARNING,
            "conditions": [] if switchboard else [
                {"text": a.text, "weakens_how": a.weakens_how,
                 "span_id": a.span_id, "source": a.source}
                for a in state.assumptions
            ],
        }

    def _glossary(self, state: PaperState) -> list[dict[str, str]]:
        """Only the calibration composer knows what its terms mean. Shipping
        those definitions on an unrelated paper is a factual error, not a
        cosmetic one."""
        if state.explainer_route != "calibration_explainer":
            return []
        return [
            {"term": "calibration", "definition": "예측 확률이 실제 정답 비율과 얼마나 맞는지"},
            {"term": "temperature scaling", "definition": "logit 분포의 날카로움을 T로 조절하는 방법"},
        ]

    def _summary(self, state: PaperState, bottleneck) -> list[str]:
        if state.explainer_route == "calibration_explainer":
            return [
                "정확도를 잘 맞히는 것과 확률을 믿을 만하게 말하는 것은 다릅니다.",
                "temperature scaling은 confidence의 모양을 조절합니다.",
            ]
        return [bottleneck.question, bottleneck.why_hard]


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
