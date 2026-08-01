"""Second-monitor guarantees on the unified server.py SSE bridge.

Replaces tests/test_bridge.py after the team unified transport on
playground/server.py (2026-08-01). The B-side contract stays the same:
raw events reach the browser as the original ``Event.to_json()`` string,
in execution order, with status events kept out of the raw channel.
"""

import time

from fastapi.testclient import TestClient

from playground.events import EventBus
from playground.payload import build_payload
from playground.server import create_app
from playground.state import PaperState


def test_demo_payload_contains_only_raw_events():
    bus = EventBus()
    bus.emit_raw("decision", actor="test", text="done")
    bus.emit_status("friendly")
    state = PaperState(source_path="fixture.pdf")

    payload = build_payload(state, bus, run_id="test-run")
    assert payload["run_id"] == "test-run"
    assert [event["type"] for event in payload["raw_events"]] == ["decision"]


def test_run_status_endpoint_answers_while_payload_still_409s():
    app = create_app(live=False)
    client = TestClient(app)

    created = client.post(
        "/api/runs",
        data={"source_text": "A testable result improves the measured outcome."},
    )
    run_id = created.json()["run_id"]

    # A browser reloading mid-run needs a route that reports progress instead
    # of the 409 that /payload returns while the run is active.
    status = client.get(f"/api/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["run_id"] == run_id
    assert status.json()["status"] in {"queued", "running", "completed", "failed"}

    record = app.state.run_store.records[run_id]
    for _ in range(50):
        if record.status in {"completed", "failed"}:
            break
        time.sleep(0.1)

    done = client.get(f"/api/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["mode"] == client.get(f"/api/runs/{run_id}/payload").json()["mode"]
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_sse_stream_preserves_raw_json_verbatim_and_in_order():
    app = create_app(live=False)
    client = TestClient(app)

    created = client.post(
        "/api/runs",
        data={"source_text": "A testable result improves the measured outcome."},
    )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    raw_lines = []
    saw_complete = False
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        current_event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                current_event = line[len("event: "):]
            elif line.startswith("data: "):
                if current_event == "raw":
                    raw_lines.append(line[len("data: "):])
                elif current_event == "complete":
                    saw_complete = True

    assert saw_complete, "stream must end with a terminal complete event"
    assert raw_lines, "a full mock run must emit raw events"

    # Wait for the worker thread to settle, then compare against the bus log.
    record = app.state.run_store.records[run_id]
    for _ in range(50):
        if record.status in {"completed", "failed"}:
            break
        time.sleep(0.1)
    expected = [event.to_json() for event in record.bus.log
                if event.channel == "raw"]
    assert raw_lines == expected, (
        "SSE data lines must be the original Event.to_json() strings, "
        "in execution order, with no status events mixed in"
    )
