"""DemoPayload serialization shared by the HTTP bridge and offline tools."""

from __future__ import annotations

import json
from typing import Any

from .events import EventBus
from .state import PaperState


SCHEMA_VERSION = "1.1"


def build_payload(state: PaperState, bus: EventBus, *, run_id: str) -> dict[str, Any]:
    """Return the stable browser envelope without changing ``PaperState``."""
    artifact = state.artifact
    if artifact is None and state.mode == "refused":
        artifact = _refusal_artifact(state, bus)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": state.mode,
        "selected_claim_id": state.selected_claim_id,
        "root_claim_id": state.root_claim_id,
        "frontier_claim_id": state.frontier_claim_id,
        "critical_path_ids": list(state.critical_path_ids),
        "claims": _claims(state),
        "spans": {
            sid: {"page": span.page, "kind": span.kind, "text": span.text}
            for sid, span in state.doc.spans.items()
        },
        "claim_graph": (artifact or {}).get("claim_graph") or _graph(state),
        "artifact": artifact,
        "external": [
            evidence.__dict__.copy()
            for evidence in state.external.get(
                state.selected_claim_id or state.frontier_claim_id or "", []
            )
        ],
        "raw_events": [json.loads(event.to_json()) for event in bus.log
                       if event.channel == "raw"],
    }


def _claims(state: PaperState) -> list[dict[str, Any]]:
    out = []
    for claim in sorted(state.claims, key=lambda item: (item.order, item.id)):
        score = state.scores.get(claim.id)
        analysis = state.claim_analyses.get(claim.id)
        out.append({
            "id": claim.id,
            "text": claim.text,
            "score": round(score.total, 3) if score else None,
            "frontier_score": round(score.frontier_total, 3) if score else None,
            "parent_id": claim.parent_id,
            "role": claim.role,
            "order": claim.order,
            "difficulty": claim.difficulty,
            "pedagogical_gain": claim.pedagogical_gain,
            "evidence_span_ids": list(claim.evidence_span_ids),
            "verification": analysis.verification if analysis else "unverified",
            "explanation": analysis.explanation if analysis else "",
        })
    return out


def _graph(state: PaperState) -> dict[str, Any]:
    nodes = []
    by_id = {claim["id"]: claim for claim in _claims(state)}
    for claim in by_id.values():
        nodes.append({
            key: claim[key]
            for key in ("id", "text", "parent_id", "role", "order",
                        "evidence_span_ids", "score", "frontier_score",
                        "verification", "explanation")
        })
    return {
        "root_claim_id": state.root_claim_id,
        "frontier_claim_id": state.frontier_claim_id,
        "critical_path_ids": list(state.critical_path_ids),
        "nodes": nodes,
    }


def _refusal_artifact(state: PaperState, bus: EventBus) -> dict[str, Any]:
    stage = None
    for event in reversed(bus.log):
        if event.type == "stage_error":
            stage = event.payload.get("stage")
            break
    if stage == "parse":
        reason_code = "INPUT_UNREADABLE"
        message = "입력 문서를 읽을 수 없어 검증을 시작하지 못했습니다."
    elif stage in {"claims", "score", "select"}:
        reason_code = "NO_VERIFIABLE_CLAIM"
        message = "원문에 묶을 수 있는 검증 가능한 claim이 없어 거절했습니다."
    else:
        reason_code = "PIPELINE_REFUSED"
        message = "현재 입력으로는 신뢰할 수 있는 인터랙션을 만들 수 없습니다."
    return {
        "primitive": "refusal",
        "mode": "refused",
        "title": "검증 가능한 인터랙션을 만들 수 없음",
        "reason_code": reason_code,
        "failed_stage": stage,
        "message": message,
    }
