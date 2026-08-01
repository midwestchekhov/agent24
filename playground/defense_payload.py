"""DefensePayloadV1 serialization."""

from __future__ import annotations

import json
from dataclasses import asdict
from collections import Counter
from typing import Any

from .events import EventBus
from .state import PaperState


SCHEMA_VERSION = "defense/1.0"


def build_defense_payload(state: PaperState, bus: EventBus, *, run_id: str) -> dict[str, Any]:
    runtime = getattr(bus, "runtime", None)
    run = {"run_id": run_id, "source_title": state.source_title}
    if runtime is not None:
        run.update(runtime.metadata())
    stage_elapsed = {
        str(event.payload.get("stage")): event.payload.get("seconds")
        for event in bus.log
        if event.channel == "raw" and event.type == "stage_end"
        and event.payload.get("stage")
    }
    provider_calls = Counter(
        str(event.payload.get("name"))
        for event in bus.log
        if event.channel == "raw" and event.type == "tool_call"
        and event.payload.get("name")
    )
    run["stage_elapsed_seconds"] = stage_elapsed
    run["provider_call_counts"] = dict(provider_calls)
    artifact = state.artifact or state.defense_report
    if artifact is None and state.mode == "refused":
        artifact = {
            "primitive": "refusal",
            "mode": "refused",
            "title": "검증 가능한 방어 보고서를 만들 수 없음",
            "reason_code": "PIPELINE_REFUSED",
            "message": "현재 입력으로는 원문에 묶인 방어 보고서를 만들 수 없습니다.",
        }
    referenced = _referenced_span_ids(artifact)
    primitive = str((artifact or {}).get("primitive") or "")
    payload_mode = (
        "partial" if primitive == "partial_defense_report"
        else "refused" if primitive == "refusal"
        else "complete"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "run": run,
        "mode": payload_mode,
        "artifact": artifact,
        # Every source_ref in the artifact is an opaque span id. Without the
        # original text the reader cannot check the defense against the paper,
        # which is the one thing this product promises. Only referenced spans
        # travel: the full document would dwarf the report itself.
        "spans": {
            sid: {"page": span.page, "kind": span.kind, "section": span.section,
                  "text": span.text}
            for sid, span in state.doc.spans.items()
            if sid in referenced
        },
        "analysis": {
            "claim_graph": _claim_graph(state),
            "candidate_scores": [
                {
                    "claim_id": score.claim_id,
                    "importance": score.importance,
                    "vulnerability": score.vulnerability,
                    "scope_gap": score.scope_gap,
                    "source_grounding": score.source_grounding,
                    "total": score.total,
                }
                for score in state.defense_scores.values()
            ],
            "evidence_ledger": asdict(state.evidence_ledger),
        },
        "raw_events": [
            json.loads(event.to_json())
            for event in bus.log if event.channel == "raw"
        ],
    }


def _referenced_span_ids(artifact: dict[str, Any] | None) -> set[str]:
    """Span ids the report actually cites, across all four artifact shapes.

    A refusal cites nothing; a deadline partial has no scope or impacts. Each
    lookup is defensive rather than shape-specific so a missing section costs
    nothing.
    """
    if not isinstance(artifact, dict):
        return set()
    out: set[str] = set()

    def collect(container: Any, key: str) -> None:
        if isinstance(container, dict):
            out.update(str(ref) for ref in container.get(key) or [])

    collect(artifact.get("target_claim"), "source_refs")
    collect(artifact.get("defensible_scope"), "source_refs")
    for assumption in artifact.get("assumptions") or []:
        collect(assumption, "source_span_ids")
    for impact in artifact.get("assumption_impacts") or []:
        collect(impact, "source_refs")
    return out


def _claim_graph(state: PaperState) -> dict[str, Any]:
    analyses = state.context_analysis or {}
    nodes = []
    for claim in sorted(state.claims, key=lambda item: (item.order, item.id)):
        score = state.defense_scores.get(claim.id)
        nodes.append({
            "id": claim.id,
            "text": claim.text,
            "parent_id": claim.parent_id,
            "role": claim.role,
            "order": claim.order,
            "evidence_span_ids": list(claim.evidence_span_ids),
            "attack_dimensions": next(
                (item.get("attack_dimensions") or []
                 for item in analyses.get("claims") or [] if item.get("id") == claim.id),
                [],
            ),
            "score": score.total if score else None,
        })
    return {
        "root_claim_id": state.root_claim_id,
        "frontier_claim_id": state.defense_frontier_id,
        "critical_path_ids": list(state.critical_path_ids),
        "nodes": nodes,
    }
