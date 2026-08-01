import time

import pytest
from fastapi.testclient import TestClient

from playground.clients import LinerSearch, _as_object
from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.server import create_app
from playground.server import RunRecord, RunStore
from playground.state import PaperState
from playground.stages.base import StageError


class _Response:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


class _BadJsonResponse(_Response):
    def json(self):
        raise ValueError("not json")


def test_liner_search_maps_description_and_preserves_raw_result():
    session = _Session([_Response(200, {
        "request_id": "req_1",
        "results": [{"title": "Paper", "url": "https://example.test/p", "description": "A result"}],
    })])
    bus = EventBus()
    result = LinerSearch(api_key="test-key", session=session).query(q="calibration", bus=bus)
    assert result == [{"title": "Paper", "url": "https://example.test/p", "snippet": "A result"}]
    assert bus.log[-1].payload["result"]["request_id"] == "req_1"
    assert session.calls[0][1]["headers"]["x-api-key"] == "test-key"


def test_liner_search_retries_rate_limit_once():
    session = _Session([
        _Response(429, {}, {"Retry-After": "0"}),
        _Response(200, {"results": []}),
    ])
    result = LinerSearch(api_key="test-key", session=session).query(q="q", bus=EventBus())
    assert result == []
    assert len(session.calls) == 2


def test_liner_search_does_not_wait_on_long_rate_limit():
    session = _Session([_Response(429, {}, {"Retry-After": "60"})])
    with pytest.raises(StageError, match="HTTP 429"):
        LinerSearch(api_key="test-key", session=session).query(q="q", bus=EventBus())
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [
    _Response(401, {"error": "unauthorized"}),
    _Response(500, {"error": "temporary"}),
])
def test_liner_search_provider_errors_are_safe(response):
    bus = EventBus()
    with pytest.raises(StageError):
        LinerSearch(api_key="test-key", session=_Session([response, response])).query(q="q", bus=bus)
    assert "Liner" in bus.log[-1].payload["error"]
    assert "test-key" not in repr(bus.log[-1].payload)


def test_liner_search_rejects_malformed_json():
    bus = EventBus()
    with pytest.raises(StageError, match="invalid JSON"):
        LinerSearch(api_key="test-key", session=_Session([_BadJsonResponse(200, None)])).query(q="q", bus=bus)
    assert bus.log[-1].payload["error"] == "Liner returned invalid JSON"


def test_model_output_object_and_refusal_payload():
    assert _as_object('{"explanation":"ok"}') == {"explanation": "ok"}
    state = PaperState(source_path="missing.pdf")
    bus = EventBus()
    Pipeline.build("ml", bus=bus).run(state)
    payload = build_payload(state, bus, run_id="r1")
    assert payload["schema_version"] == "1.1"
    assert payload["mode"] == "refused"
    assert payload["artifact"]["primitive"] == "refusal"
    assert all(event["type"] != "status" for event in payload["raw_events"])


def _wait_payload(client, url):
    for _ in range(100):
        response = client.get(url)
        if response.status_code != 409:
            return response
        time.sleep(0.01)
    return response


def test_mock_server_claim_run_and_sse():
    with TestClient(create_app(live=False)) as client:
        created = client.post("/api/runs", data={"claim_text": "A claim", "domain": "ml"})
        assert created.status_code == 202
        body = created.json()
        payload = _wait_payload(client, body["payload_url"])
        assert payload.status_code == 200
        assert payload.json()["schema_version"] == "1.1"
        stream = client.get(body["events_url"])
        assert "event: raw" in stream.text
        assert "event: complete" in stream.text


def test_server_rejects_empty_input():
    with TestClient(create_app(live=False)) as client:
        assert client.post("/api/runs", data={"domain": "ml"}).status_code == 422


def test_server_validates_pdf_content_and_size():
    with TestClient(create_app(live=False)) as client:
        malformed = client.post(
            "/api/runs",
            data={"domain": "ml"},
            files={"pdf": ("paper.pdf", b"%PDF-not-a-real-document", "application/pdf")},
        )
        assert malformed.status_code == 422
        oversized = client.post(
            "/api/runs",
            data={"domain": "ml"},
            files={"pdf": ("paper.pdf", b"%PDF-" + b"x" * (25 * 1024 * 1024), "application/pdf")},
        )
        assert oversized.status_code == 413


def test_run_store_allows_only_one_active_run():
    store = RunStore()
    first = RunRecord("first")
    second = RunRecord("second")
    store.reserve(first)
    with pytest.raises(RuntimeError, match="RUN_IN_PROGRESS"):
        store.reserve(second)
    first.status = "completed"
    store.finish(first)
    store.reserve(second)
