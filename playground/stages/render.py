"""Deterministic schema -> artifact payload. The frontend owns the pixels."""

from __future__ import annotations

from dataclasses import asdict

from ..events import EventBus
from ..state import (
    InteractionSpec,
    PaperState,
)
from .base import Stage


class Render(Stage):
    """Deterministic. Schema -> artifact payload. Frontend owns the pixels."""

    name = "render"
    reads = ("explainer", "spec", "verdict", "mode", "doc", "claims",
             "assumptions", "external", "root_claim_id", "frontier_claim_id",
             "critical_path_ids", "claim_analyses", "evidence_ledger")
    writes = ("artifact",)
    budget_s = 1.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        spec = state.spec
        assert spec is not None
        # Two outcomes only. The explainer is the product; the safe map is what
        # a fatal critic violation leaves behind. There is no third artifact.
        if (state.verdict is not None
                and state.verdict.result == "UNSAFE_TO_VISUALIZE"):
            state.artifact = self._safe_map(spec, state)
            bus.decision(
                "render",
                "UNSAFE_TO_VISUALIZE -> evidence/assumption map으로 제한",
                primitive="evidence_assumption_map",
            )
            bus.emit_status("근거·가정 map 준비 완료 — 인터랙션 비활성")
            return
        assert state.explainer is not None
        state.artifact = self._explainer_payload(state)
        bus.decision("render", "DemoPayloadV2 explainer artifact 생성",
                     primitive="interactive_explainer",
                     panels=len(state.explainer.panels))
        bus.emit_status("explainer payload 준비 완료")

    def _explainer_payload(self, state: PaperState) -> dict:
        exp = state.explainer
        assert exp is not None
        return {
            "primitive": "interactive_explainer",
            "mode": state.mode,
            "title": exp.title,
            "thesis": exp.thesis,
            "bottleneck": {
                **exp.bottleneck.__dict__,
            },
            "panels": [
                {
                    **panel.__dict__,
                    "provenance": list(panel.provenance),
                }
                for panel in exp.panels[:3]
            ],
            "comparison": exp.comparison,
            "glossary": exp.glossary,
            "summary": exp.summary,
            "critical_note": exp.critical_note,
            "editorial": exp.editorial,
            "sources": exp.sources,
            "evidence": asdict(state.evidence_ledger),
            "external_visualization": state.visualization,
        }

    def _safe_map(self, spec: InteractionSpec, state: PaperState) -> dict:
        path_ids = state.critical_path_ids or [spec.claim_id]
        by_id = {c.id: c for c in state.claims}
        analyses = state.claim_analyses
        assumptions: list[dict] = []
        paper_ids: list[str] = []
        input_ids: list[str] = []
        seen_assumptions: set[str] = set()
        for claim_id in path_ids:
            claim = by_id.get(claim_id)
            if claim:
                for span_id in claim.evidence_span_ids:
                    span = state.doc.spans.get(span_id)
                    if span and span.origin == "paper":
                        paper_ids.append(span_id)
                    elif span and span.origin == "manual":
                        input_ids.append(span_id)
            analysis = analyses.get(claim_id)
            if analysis:
                paper_ids.extend(
                    a.span_id for a in analysis.assumptions
                    if a.span_id and state.doc.spans.get(a.span_id)
                    and state.doc.spans[a.span_id].origin == "paper"
                )
                for assumption in analysis.assumptions:
                    if assumption.id not in seen_assumptions:
                        assumptions.append(assumption.__dict__.copy())
                        seen_assumptions.add(assumption.id)
        if not assumptions:
            assumptions = [a.__dict__.copy() for a in state.assumptions]
        paper = []
        claim_input = []
        seen = set()
        for span_id in paper_ids:
            if span_id in seen:
                continue
            seen.add(span_id)
            span = state.doc.spans.get(span_id)
            if span is None:
                continue
            paper.append({
                "span_id": span.id,
                "page": span.page,
                "kind": span.kind,
                "section": span.section,
                "text": span.text,
            })

        for span_id in input_ids:
            if span_id in seen:
                continue
            seen.add(span_id)
            span = state.doc.spans.get(span_id)
            if span is None:
                continue
            claim_input.append({
                "span_id": span.id,
                "page": span.page,
                "kind": span.kind,
                "section": span.section,
                "text": span.text,
            })

        external = [e.__dict__.copy()
                    for e in state.external.get(spec.claim_id, [])]
        return {
            "primitive": "evidence_assumption_map",
            "mode": state.mode,
            "title": spec.title,
            "safety": {
                "reason_codes": [
                    violation.code for violation in (state.verdict.violations if state.verdict else [])
                    if violation.fatal
                ],
                "message": "Critic fatal로 인터랙션 대신 읽기 전용 근거 map을 제공합니다.",
            },
            "evidence_map": {
                "claim_id": spec.claim_id,
                "covered_claim_ids": path_ids,
                "paper": paper,
                "claim_input": claim_input,
                "external": external,
                "ledger": asdict(state.evidence_ledger),
            },
            "assumption_map": assumptions,
        }

    @staticmethod
    def _graph_payload(state: PaperState) -> dict:
        nodes = []
        for claim in sorted(state.claims, key=lambda c: (c.order, c.id)):
            score = state.scores.get(claim.id)
            analysis = state.claim_analyses.get(claim.id)
            nodes.append({
                "id": claim.id,
                "text": claim.text,
                "support_type": claim.support_type,
                "parent_id": claim.parent_id,
                "role": claim.role,
                "order": claim.order,
                "evidence_span_ids": list(claim.evidence_span_ids),
                "score": round(score.total, 3) if score else None,
                "frontier_score": (round(score.frontier_total, 3)
                                    if score else None),
                "verification": analysis.verification if analysis else "unverified",
                "explanation": analysis.explanation if analysis else "",
                "visible": bool(analysis and analysis.verification == "verified"),
                "verification_badge": (analysis.verification if analysis else "unverified"),
            })
        return {
            "root_claim_id": state.root_claim_id,
            "frontier_claim_id": state.frontier_claim_id,
            "critical_path_ids": list(state.critical_path_ids),
            "claim_graph": {
                "root_claim_id": state.root_claim_id,
                "frontier_claim_id": state.frontier_claim_id,
                "critical_path_ids": list(state.critical_path_ids),
                "nodes": nodes,
            },
        }
