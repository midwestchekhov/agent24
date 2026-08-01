import time
from pathlib import Path

from fastapi.testclient import TestClient

from playground.events import EventBus
from playground.formula import FormulaError, evaluate
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.server import create_app
from playground.state import PaperState
from playground.stages import Parse


TEXT = """Calibration notes

Neural networks can achieve low classification error while becoming poorly calibrated.

Temperature scaling uses q_i = softmax(z_i / T) and changes confidence without changing the predicted class.

Expected calibration error measures confidence quality.
"""


def test_formula_evaluator_is_allow_listed():
    values = evaluate("softmax(logits / T)", {"logits": [2.0, 0.0], "T": 2.0})
    assert len(values) == 2
    assert values[0] > values[1]
    try:
        evaluate("__import__('os').getcwd()", {})
    except FormulaError:
        pass
    else:
        raise AssertionError("unsafe formula operation was accepted")


def test_text_source_builds_v2_with_one_bottleneck_and_two_panels():
    bus = EventBus()
    state = PaperState(source_text=TEXT, source_title="Calibration notes")
    Pipeline.build("ml", bus=bus).run(state)
    payload = build_payload(state, bus, run_id="text-run")

    assert payload["schema_version"] == "2.0"
    assert payload["artifact"]["primitive"] == "interactive_explainer"
    assert payload["artifact"]["bottleneck"]["source_claim_ids"]
    assert len(payload["artifact"]["panels"]) <= 3
    assert {p["primitive"] for p in payload["artifact"]["panels"]} == {
        "generated_schematic", "scaling_comparison"
    }
    assert payload["artifact"]["critical_note"]["text"]
    assert payload["raw_events"]
    assert all(e["type"] != "status" for e in payload["raw_events"])


def test_server_accepts_plain_text_source():
    with TestClient(create_app(live=False)) as client:
        created = client.post(
            "/api/runs",
            data={"source_text": TEXT, "source_title": "Calibration notes", "domain": "ml"},
        )
        assert created.status_code == 202
        url = created.json()["payload_url"]
        for _ in range(100):
            response = client.get(url)
            if response.status_code != 409:
                break
            time.sleep(0.01)
        assert response.status_code == 200
        assert response.json()["schema_version"] == "2.0"


def test_glioblastoma_fixture_does_not_false_route_to_calibration():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / (
        "Nature_2018_Lee_et_al._Human_glioblastoma_arises_from_subventricular_zone_cells.pdf"
    )
    state = PaperState(source_path=str(fixture))
    bus = EventBus()
    Parse().run(state, bus)
    assert len(state.doc.spans) > 0

    Pipeline.build("ml", bus=bus).run(state)
    assert state.explainer_route == "assumption_switchboard"
    assert state.artifact["primitive"] in {"assumption_switchboard", "evidence_assumption_map"}
    assert state.source_title == "Human glioblastoma arises from subventricular zone cells with low-level driver mutations"
    assert state.claims
    assert all(
        state.doc.spans[sid].section in {"abstract", "intro", "results", "discussion"}
        for claim in state.claims
        for sid in claim.evidence_span_ids
    )
    assert len({round(score.total, 3) for score in state.scores.values()}) > 1
    assert all("AUC" not in assumption.text for assumption in state.assumptions)
