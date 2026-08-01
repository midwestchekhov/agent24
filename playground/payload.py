"""Adapter from backend state and EventBus to DemoPayloadV1.

This module is intentionally read-only with respect to the pipeline. It does
not alter EventBus events or merge the raw and status channels.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from .events import EventBus
from .state import PaperState


def to_demo_payload(
    state: PaperState, bus: EventBus, *, run_id: str | None = None
) -> dict[str, Any]:
    """Return the stable snake_case envelope consumed by the main UI."""

    claims = []
    for claim in state.claims:
        item = asdict(claim)
        score = state.scores.get(claim.id)
        item["score"] = round(score.total, 3) if score else None
        claims.append(item)

    external = [
        asdict(evidence)
        for evidence_list in state.external.values()
        for evidence in evidence_list
    ]
    raw_events = [
        json.loads(event.to_json())
        for event in bus.log
        if event.channel == "raw"
    ]

    return {
        "schema_version": "1.0",
        "run_id": run_id or uuid.uuid4().hex,
        "mode": state.mode,
        "selected_claim_id": state.selected_claim_id,
        "claims": claims,
        "spans": {sid: asdict(span) for sid, span in state.doc.spans.items()},
        "artifact": state.artifact or {},
        "external": external,
        "raw_events": raw_events,
    }


def is_supported_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("schema_version") == "1.0"
