"""Deterministic prechecks, soft language critique and optional provider art."""

from __future__ import annotations

import json
from dataclasses import asdict

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
             "path_unsafe", "explainer", "evidence_ledger")
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
        # The fast profile deliberately analyzes only the selected frontier
        # assumption path.  Do not turn the intentionally skipped ancestor /
        # sibling analyses into a false fatal; the selected node is the only
        # one that can surface in the reader-facing panel.
        checked_path = list(state.critical_path_ids)
        runtime = getattr(bus, "runtime", None)
        profile = getattr(runtime, "profile", None)
        if profile is not None and profile.assumption_path_limit is not None:
            selected = state.selected_claim_id or state.frontier_claim_id
            checked_path = [selected] if selected else checked_path[:1]
            bus.decision(
                "critic", "fast profile: 선택 frontier만 claim analysis 검사",
                claim_id=selected,
            )
        for claim_id in checked_path:
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
        if not fatal and self.llm:
            violations.extend(self._fidelity_check(state, bus))
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
            _numeric_ok, overlap = checker(rule, assumption, state)
            # Implicit assumptions need materially more lexical support than
            # a coincidental shared term. Otherwise a paper span attribution
            # gives the reader false confidence and the fidelity critic must
            # reject the whole artifact.
            threshold = 0.25 if assumption.source == "paper_implicit" else 0.10
            if overlap < threshold:
                bus.decision(
                    "critic", f"{rule.assumption_id}: span 지지 부족 -> pedagogical 강등",
                    assumption_id=rule.assumption_id,
                    span_id=rule.attribution.span_id,
                    overlap=round(overlap, 3),
                )
                rule.attribution = Attribution(kind="pedagogical")

    def _fidelity_check(self, state: PaperState, bus: EventBus) -> list[Violation]:
        """Judge semantic fidelity only after deterministic checks pass."""
        explainer = state.explainer
        prompt = {
            # Put the auditable object first. The bounded prompt may be
            # truncated later, but the critic must never receive a tail-only
            # evidence dump that hides the panel it is judging.
            "panel_spec": asdict(state.spec) if state.spec else None,
            "panels": [
                {
                    "primitive": panel.primitive,
                    "question": panel.question,
                    "model": panel.model,
                    "provenance": panel.provenance,
                    "notice": panel.notice,
                }
                for panel in (explainer.panels if explainer else [])
            ],
            "critical_note": explainer.critical_note if explainer else None,
            "source_spans": {
                sid: {"text": span.text, "section": span.section,
                      "kind": span.kind}
                for sid, span in state.doc.spans.items()
                if sid in {
                    ref
                    for claim in state.claims
                    for ref in claim.evidence_span_ids
                }
            },
            "assumptions": [asdict(item) for item in state.assumptions],
            "evidence_ledger": asdict(state.evidence_ledger),
            "explainer": asdict(explainer) if explainer else None,
        }
        rendered = json.dumps(prompt, ensure_ascii=False)[:30_000]
        try:
            out = self.llm.structured(
                role="fidelity_critic", prompt=rendered,
                schema_hint="FidelityCritic", bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- safety fallback is deliberate
            return [Violation(
                "FIDELITY_CRITIC_UNAVAILABLE",
                f"fidelity critic failed: {type(e).__name__}",
            )]
        findings = out.get("findings") if isinstance(out, dict) else None
        if findings is None:
            return [Violation(
                "FIDELITY_CRITIC_MALFORMED", "fidelity critic returned no findings"
            )]
        violations: list[Violation] = []
        for finding in findings:
            if not isinstance(finding, dict):
                violations.append(Violation(
                    "FIDELITY_CRITIC_MALFORMED", "finding is not an object"
                ))
                continue
            if finding.get("acceptable") is False:
                code = re_safe_code(finding.get("code"))
                detail = str(finding.get("detail") or "semantic fidelity failed").strip()
                violations.append(Violation(code, detail))
        return violations


def re_safe_code(raw) -> str:
    code = "".join(
        character if character.isalnum() else "_"
        for character in str(raw or "FIDELITY_CRITIC_REJECTED").upper()
    ).strip("_")
    return code[:80] or "FIDELITY_CRITIC_REJECTED"


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
