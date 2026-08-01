"""Deterministic prechecks, soft language critique and optional provider art."""

from __future__ import annotations

from ..clients import (
    LLM,
    Visualization,
)
from ..events import EventBus
from ..state import (
    Attribution,
    PaperState,
    Violation,
)
from .base import (
    Stage,
    StageError,
)


class Critic(Stage):
    """Deterministic prechecks first, LLM only for the soft stuff. An
    ungrounded number is caught by code in microseconds -- do not ask a model."""

    name = "critic"
    reads = ("spec", "number_pool", "doc", "claims", "assumptions",
             "external", "claim_analyses", "critical_path_ids",
             "path_unsafe")
    writes = ("verdict",)
    budget_s = 4.0

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        from ..critic_rules import attribution_support, precheck

        spec = state.spec
        if spec is None:
            raise StageError("no spec to check")
        self._repair_weak_attributions(state, bus, attribution_support)
        violations = list(precheck(spec, state))
        if state.path_unsafe:
            violations.append(Violation(
                "UNSAFE_CLAIM_PATH",
                "critical path node analysis failed; interactive frontier is unsafe",
            ))
        for claim_id in state.critical_path_ids:
            analysis = state.claim_analyses.get(claim_id)
            if analysis is None:
                violations.append(Violation(
                    "MISSING_CLAIM_ANALYSIS",
                    f"critical path node '{claim_id}' has no analysis",
                ))
            elif analysis.verification != "verified":
                violations.append(Violation(
                    "UNVERIFIED_CLAIM_NODE",
                    f"critical path node '{claim_id}' is "
                    f"{analysis.verification}",
                ))
        fatal = [v for v in violations if v.fatal]
        if not fatal and self.llm and state.assumptions:
            violations.extend(self._soft_check(state, bus))
        for v in violations:
            bus.decision("critic", f"{v.code}: {v.detail}", fatal=v.fatal)
        from ..state import CriticVerdict

        fatal = [v for v in violations if v.fatal]
        result = "UNSAFE_TO_VISUALIZE" if fatal else "PASS"
        state.verdict = CriticVerdict(
            result=result, violations=violations
        )
        bus.decision(
            "critic", f"verdict {result}", result=result,
            fatal_codes=[v.code for v in fatal],
        )
        bus.emit_status(
            "정확성 검사 " + ("시각화 제한" if fatal else "통과")
        )

    @staticmethod
    def _repair_weak_attributions(state: PaperState, bus: EventBus, checker) -> None:
        if state.spec is None:
            return
        by_id = {a.id: a for a in state.assumptions}
        for rule in state.spec.status_rules:
            if rule.attribution.kind != "paper":
                continue
            assumption = by_id.get(rule.assumption_id)
            if assumption is None:
                continue
            numeric_ok, overlap = checker(rule, assumption, state)
            if numeric_ok and overlap < 0.10:
                bus.decision(
                    "critic", f"{rule.assumption_id}: span 지지 부족 -> pedagogical 강등",
                    assumption_id=rule.assumption_id,
                    span_id=rule.attribution.span_id,
                    overlap=round(overlap, 3),
                )
                rule.attribution = Attribution(kind="pedagogical")

    def _soft_check(self, state: PaperState, bus: EventBus) -> list[Violation]:
        """Ask only about natural-language quality after code checks pass."""
        prompt = ["# task", "Flag only generic weakens_how sentences.", ""]
        for assumption in state.assumptions:
            prompt.append(
                f"{assumption.id}: {assumption.text}\n"
                f"weakens_how: {assumption.weakens_how}"
            )
        try:
            out = self.llm.structured(
                role="critic_soft", prompt="\n".join(prompt),
                schema_hint="CriticSoftCheck", bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- safety fallback is deliberate
            return [Violation(
                "SOFT_CRITIC_UNAVAILABLE",
                f"soft language check failed: {type(e).__name__}",
            )]
        findings = out.get("findings") if isinstance(out, dict) else None
        if findings is None:
            return [Violation("SOFT_CRITIC_MALFORMED", "soft critic returned no findings")]
        valid_ids = {a.id for a in state.assumptions}
        violations: list[Violation] = []
        for finding in findings:
            if not isinstance(finding, dict):
                violations.append(Violation("SOFT_CRITIC_MALFORMED", "finding is not an object"))
                continue
            aid = str(finding.get("assumption_id") or "").strip()
            if aid not in valid_ids:
                violations.append(Violation("SOFT_CRITIC_UNKNOWN_ASSUMPTION", f"unknown assumption '{aid}'"))
                continue
            if finding.get("acceptable") is False:
                detail = str(finding.get("detail") or "weakens_how is too generic").strip()
                violations.append(Violation("GENERIC_WEAKENS_HOW", f"{aid}: {detail}"))
        return violations


class VisualizationAdapter(Stage):
    """Optional external renderer, kept separate from source-grounded panels."""

    name = "visualization"
    reads = ("explainer", "bottleneck", "verdict", "doc", "source_title")
    writes = ("visualization",)
    budget_s = 40.0

    def __init__(self, visualizer: Visualization | None = None):
        self.visualizer = visualizer

    def run(self, state: PaperState, bus: EventBus) -> None:
        if self.visualizer is None or state.explainer is None:
            return
        if state.verdict is None or state.verdict.result != "PASS":
            bus.decision("visualization", "critic 결과가 PASS가 아니어서 provider 시각화 생략")
            return
        bottleneck = state.bottleneck
        refs = bottleneck.evidence_refs if bottleneck else []
        source_lines = [
            state.doc.spans[sid].text[:700]
            for sid in refs if sid in state.doc.spans
        ]
        query = "\n".join([
            "Create an explanatory process/relationship visualization, not a reproduction of a paper figure.",
            f"Title: {state.source_title or state.explainer.title}",
            f"Teaching question: {bottleneck.question if bottleneck else state.explainer.title}",
            "Use only the source-grounded facts below. Mark any interpolation as illustrative.",
            *source_lines,
        ])[:12_000]
        try:
            result = self.visualizer.render(query=query, bus=bus)
        except StageError as exc:
            bus.decision("visualization", "provider visualization 실패 -> local panel 유지",
                         error=type(exc).__name__)
            return
        if result:
            state.visualization = result
            bus.decision("visualization", "외부 설명용 visualization artifact 추가",
                         provider=result.get("provider"), theme=result.get("theme"))
