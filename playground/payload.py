"""DemoPayload serialization shared by the HTTP bridge and offline tools."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .events import EventBus
from .state import PaperState


SCHEMA_VERSION = "1.1"
EXPLAINER_SCHEMA_VERSION = "2.0"


def build_payload(state: PaperState, bus: EventBus, *, run_id: str) -> dict[str, Any]:
    """Return the stable browser envelope without changing ``PaperState``."""
    artifact = state.artifact
    if artifact is None and state.mode == "refused":
        artifact = _refusal_artifact(state, bus)

    external = _external(state)
    if artifact is not None and state.explainer:
        artifact = {**artifact, "external": external}

    runtime = getattr(bus, "runtime", None)
    run_meta = {
        "run_id": run_id,
        "source_title": state.source_title,
        "input_kind": (
            "pdf+text" if state.source_path and state.source_text
            else "pdf" if state.source_path
            else "text" if state.source_text
            else "claim"
        ),
    }
    if runtime is not None:
        run_meta.update(runtime.metadata())
    return {
        "schema_version": (EXPLAINER_SCHEMA_VERSION if state.explainer
                           else SCHEMA_VERSION),
        "run_id": run_id,
        "run": run_meta,
        "mode": state.mode,
        "spans": {
            sid: {"page": span.page, "kind": span.kind, "section": span.section,
                  "text": span.text}
            for sid, span in state.doc.spans.items()
        },
        "artifact": artifact,
        "external": external,
        "evidence_ledger": asdict(state.evidence_ledger),
        # The claim lineage is internal reasoning, not a deliverable. It lives
        # here so audit and debug consumers can still read it while the
        # frontend has nothing to accidentally render: the reader gets sections
        # and panels, never claim ids or frontier scores.
        "analysis": {
            "claim_graph": _graph(state),
            "claims": _claims(state),
            "selected_claim_id": state.selected_claim_id,
            "root_claim_id": state.root_claim_id,
            "frontier_claim_id": state.frontier_claim_id,
            "critical_path_ids": list(state.critical_path_ids),
            "context": state.context_analysis or {},
        },
        "raw_events": [json.loads(event.to_json()) for event in bus.log
                       if event.channel == "raw"],
    }


def _external(state: PaperState) -> list[dict[str, Any]]:
    key = state.selected_claim_id or state.frontier_claim_id or ""
    return [evidence.__dict__.copy() for evidence in state.external.get(key, [])]


def _claims(state: PaperState) -> list[dict[str, Any]]:
    out = []
    for claim in sorted(state.claims, key=lambda item: (item.order, item.id)):
        score = state.scores.get(claim.id)
        analysis = state.claim_analyses.get(claim.id)
        out.append({
            "id": claim.id,
            "text": claim.text,
            "support_type": claim.support_type,
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
            "visible": bool(analysis and analysis.verification == "verified"),
            "verification_badge": (analysis.verification if analysis else "unverified"),
        })
    return out


def _graph(state: PaperState) -> dict[str, Any]:
    nodes = []
    by_id = {claim["id"]: claim for claim in _claims(state)}
    for claim in by_id.values():
        nodes.append({
            key: claim[key]
            for key in ("id", "text", "support_type", "parent_id", "role", "order",
                        "evidence_span_ids", "score", "frontier_score",
                        "verification", "explanation", "visible",
                        "verification_badge")
        })
    return {
        "root_claim_id": state.root_claim_id,
        "frontier_claim_id": state.frontier_claim_id,
        "critical_path_ids": list(state.critical_path_ids),
        "nodes": nodes,
    }


def _refusal_artifact(state: PaperState, bus: EventBus) -> dict[str, Any]:
    stage = None
    error = ""
    for event in reversed(bus.log):
        if event.type == "stage_error":
            stage = event.payload.get("stage")
            error = str(event.payload.get("error") or "")
            break
    if stage == "parse":
        reason_code = "INPUT_UNREADABLE"
        message = "입력 문서를 읽을 수 없어 검증을 시작하지 못했습니다."
    elif ("401" in error or "Unauthorized" in error
          or "authentication" in error.lower()
          or "invalid_api_key" in error.lower()):
        reason_code = "LIVE_AUTH_FAILED"
        message = "live API 인증에 실패했습니다. .env의 API key와 권한을 확인하세요."
    elif "404" in error or "model_not_found" in error.lower():
        reason_code = "LIVE_MODEL_UNAVAILABLE"
        message = "지정한 live 모델을 사용할 수 없습니다. PLAYGROUND_MODEL 설정을 확인하세요."
    elif "429" in error or "rate limit" in error.lower():
        reason_code = "LIVE_RATE_LIMITED"
        message = "live 모델 호출 한도를 초과했습니다. 잠시 후 다시 시도하세요."
    elif "Connection error" in error or "APIConnectionError" in error:
        reason_code = "LIVE_PROVIDER_UNAVAILABLE"
        message = "live 모델 제공자에 연결할 수 없습니다. 네트워크와 모델 endpoint 설정을 확인하세요."
    elif "not installed" in error or "required for" in error:
        reason_code = "LIVE_DEPENDENCY_MISSING"
        message = "live 실행 의존성이 준비되지 않았습니다. 설치 상태를 확인하세요."
    elif "did not return JSON" in error or "Malformed" in error:
        reason_code = "LIVE_STRUCTURED_OUTPUT_INVALID"
        message = "live 모델이 약속한 구조화 응답을 반환하지 않았습니다."
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
