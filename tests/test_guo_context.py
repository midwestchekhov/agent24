from pathlib import Path

from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.state import PaperState
from playground.clients import MockLLM


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "guo17a.pdf"


def test_guo_uses_one_context_pass_and_separates_graph_from_artifact():
    bus = EventBus()
    state = PaperState(source_path=str(FIXTURE))
    Pipeline.build("ml", bus=bus).run(state)

    assert state.context_analysis is not None
    assert any(
        event.type == "tool_call"
        and event.payload.get("name") == "llm.structured"
        and event.payload.get("arguments", {}).get("role") == "context_analyst"
        for event in bus.log
    )
    assert state.claims
    assert state.doc.spans[state.claims[0].evidence_span_ids[0]].section == "abstract"
    assert all(
        state.doc.spans[sid].kind != "table_cell"
        and state.doc.spans[sid].section != "references"
        for claim in state.claims
        for sid in claim.evidence_span_ids
    )
    assert state.explainer_route == "calibration_explainer"
    assert state.artifact is not None
    assert "claim_graph" not in state.artifact
    assert {panel["primitive"] for panel in state.artifact["panels"]} == {
        "generated_schematic", "scaling_comparison"
    }

    payload = build_payload(state, bus, run_id="guo-context")
    assert payload["analysis"]["claim_graph"]["nodes"]
    assert payload["artifact"]["bottleneck"]["mechanism_kind"] == "calibration"


def test_invalid_context_citations_fall_back_to_real_source_spans():
    llm = MockLLM(fixtures={
        "context_analyst": {
            "claims": [{
                "id": "c1", "text": "A claim with a stale citation",
                "evidence_span_ids": ["missing_span"],
            }]
        }
    })
    state = PaperState(source_path=str(FIXTURE))
    Pipeline.build("ml", llm=llm, bus=EventBus()).run(state)

    assert state.mode != "refused"
    assert state.claims
    assert all(sid in state.doc.spans for c in state.claims for sid in c.evidence_span_ids)
