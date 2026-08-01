import json

from playground.bridge import RawEventBridge
from playground.events import EventBus
from playground.payload import to_demo_payload
from playground.state import Claim, PaperState


def test_raw_bridge_replays_raw_events_in_order_and_excludes_status():
    bus = EventBus()
    bridge = RawEventBridge(bus)
    first = bus.emit_raw("tool_call", name="demo", arguments={})
    bus.emit_status("not raw")
    bus.emit_raw("tool_result", call_id=first.id, result={"ok": True})

    stream = bridge.stream()
    first_seq, first_raw = next(stream)
    second_seq, second_raw = next(stream)
    first_json = json.loads(first_raw)
    second_json = json.loads(second_raw)
    assert [first_json["type"], second_json["type"]] == ["tool_call", "tool_result"]
    assert [first_seq, second_seq] == [0, 1]

    bridge.close()
    assert next(stream) is None


def test_raw_bridge_resumes_after_last_event_id():
    bus = EventBus()
    bridge = RawEventBridge(bus)
    bus.emit_raw("tool_call", name="demo", arguments={})
    bus.emit_raw("tool_result", call_id="x", result=None)
    bus.emit_raw("decision", actor="test", text="third")

    # 재연결한 브라우저는 Last-Event-ID(seq=1) 이후 것만 다시 받는다.
    stream = bridge.stream(after=1)
    seq, raw = next(stream)
    assert seq == 2
    assert json.loads(raw)["type"] == "decision"

    bridge.close()
    assert next(stream) is None


def test_demo_payload_contains_only_raw_events():
    bus = EventBus()
    bus.emit_raw("decision", actor="test", text="done")
    bus.emit_status("friendly")
    state = PaperState(source_path="fixture.pdf")
    state.claims = [Claim(id="c1", text="A claim")]
    state.selected_claim_id = "c1"

    payload = to_demo_payload(state, bus, run_id="test-run")
    assert payload["schema_version"] == "1.0"
    assert payload["run_id"] == "test-run"
    assert [event["type"] for event in payload["raw_events"]] == ["decision"]
