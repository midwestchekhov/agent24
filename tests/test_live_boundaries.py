import time

import pytest
from fastapi.testclient import TestClient

from playground.clients import LinerSearchAgent, LinerVisualization, _as_object
from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.runtime import FAST_PROFILE, DeadlineExceeded, RunBudget, resolve_profile
from playground.server import create_app
from playground.server import RunRecord, RunStore
from playground.state import PaperState
from playground.stages.base import Stage, StageError


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


class _StreamResponse(_Response):
    def __init__(self, status_code, lines, headers=None):
        super().__init__(status_code, None, headers)
        self.lines = lines

    def iter_lines(self, **kwargs):
        return iter(self.lines)


def test_live_fast_profile_has_bounded_external_work():
    profile = resolve_profile("live-fast", live=True)
    assert profile is FAST_PROFILE
    assert profile.deadline_seconds == 120.0
    assert profile.evidence_max_rounds == 1
    # Two actions in the single round. They fan out on the evidence loop's
    # thread pool, so the adversarial and boundary searches both run without
    # a second round of wall time.
    assert profile.evidence_max_total_actions == 2
    assert profile.evidence_max_actions_per_round == 2
    assert profile.max_references_per_action == 5
    assert profile.max_chunks_per_action == 20
    assert profile.liner_stream_seconds == 25.0
    assert profile.use_visualization is False
    assert profile.use_editorial_llm is False


def test_run_budget_reports_deadline_metadata():
    budget = RunBudget.start(FAST_PROFILE)
    metadata = budget.metadata()
    assert metadata["profile"] == "live-fast"
    assert metadata["deadline_seconds"] == 120.0
    assert metadata["elapsed_seconds"] >= 0
    assert metadata["deadline_hit"] is False


def test_liner_search_agent_caps_stream_and_drops_raw_provider_events():
    lines = [
        'data: {"type":"data-search-references","data":{"references":['
        + ",".join(
            '{"title":"Paper %d","url":"https://example.test/%d","description":"d"}'
            % (index, index) for index in range(8)
        ) + ']}}',
        'data: {"type":"data-search-chunks","data":{"referenceChunks":['
        + ",".join(
            '{"num":%d,"content":"chunk %d","sourceTitle":"Paper","sourceUrl":"https://example.test/%d"}'
            % (index, index, index) for index in range(30)
        ) + ']}}',
        'data: {"type":"text-delta","delta":"answer"}',
        'data: [DONE]',
    ]
    bus = EventBus()
    result = LinerSearchAgent(
        api_key="test-key", session=_Session([_StreamResponse(200, lines)]),
        max_references=5, max_chunks=20,
    ).search(query="calibration", bus=bus)
    assert len(result["references"]) == 5
    # Chunks from references beyond the retained top-5 are discarded before
    # the interpreter sees them; the normalized result never exceeds the cap.
    assert len(result["reference_chunks"]) <= 20
    assert result["reference_count"] == 5
    assert result["chunk_count"] == len(result["reference_chunks"])
    assert result["truncated"] is True
    tool_result = next(event for event in bus.log if event.type == "tool_result")
    assert "events" not in tool_result.payload["result"]


def test_payload_includes_fast_runtime_metadata():
    state = PaperState(source_title="test")
    bus = EventBus()
    bus.runtime = RunBudget.start(FAST_PROFILE)
    payload = build_payload(state, bus, run_id="r-fast")
    assert payload["run"]["profile"] == "live-fast"
    assert payload["run"]["deadline_seconds"] == 120.0
    assert payload["run"]["elapsed_seconds"] >= 0


def test_pipeline_deadline_exception_returns_partial_artifact():
    class _DeadlineStage(Stage):
        name = "deadline-stage"

        def run(self, state, bus):
            raise DeadlineExceeded("budget exhausted")

    bus = EventBus()
    pipeline = Pipeline([_DeadlineStage()], bus, FAST_PROFILE)
    state = PaperState(source_title="Guo")
    pipeline.run(state)
    assert state.artifact["primitive"] == "partial_defense_report"
    assert state.artifact["mode"] == "partial"
    assert state.mode == "qualitative"
    assert bus.log[-1].type == "decision"


def test_liner_search_agent_collects_references_chunks_and_answer():
    session = _Session([_StreamResponse(200, [
        'data: {"type":"start","message_metadata":{"request_id":"req_1"}}',
        'data: {"type":"data-search-references","data":{"references":[{"title":"Paper","url":"https://example.test/p","description":"A result"}]}}',
        'data: {"type":"data-search-chunks","data":{"referenceChunks":[{"num":1,"content":"Direct source text","sourceTitle":"Paper","sourceUrl":"https://example.test/p"}]}}',
        'data: {"type":"text-delta","delta":"Grounded answer"}',
        'data: [DONE]',
    ])])
    bus = EventBus()
    result = LinerSearchAgent(api_key="test-key", session=session).search(
        query="calibration", bus=bus
    )
    assert result["references"][0]["snippet"] == "A result"
    assert result["reference_chunks"][0]["content"] == "Direct source text"
    assert result["reference_chunks"][0]["source_title"] == "Paper"
    assert result["reference_chunks"][0]["source_url"] == "https://example.test/p"
    assert result["answer"] == "Grounded answer"
    assert session.calls[0][1]["headers"]["x-api-key"] == "test-key"
    assert session.calls[0][1]["json"]["mode"] == "scholar"
    assert session.calls[0][1]["json"]["messages"][-1]["role"] == "user"


def test_liner_search_agent_keeps_legacy_snake_case_chunk_aliases():
    session = _Session([_StreamResponse(200, [
        'data: {"type":"data-search-chunks","data":{"referenceChunks":[{"num":1,"content":"cached text","source_title":"Cached","source_url":"https://example.test/cached"}]}}',
        'data: [DONE]',
    ])])
    result = LinerSearchAgent(api_key="test-key", session=session).search(
        query="cached", bus=EventBus()
    )
    chunk = result["reference_chunks"][0]
    assert chunk["source_title"] == "Cached"
    assert chunk["source_url"] == "https://example.test/cached"


def test_liner_search_agent_retries_rate_limit_once():
    session = _Session([
        _Response(429, {}, {"Retry-After": "0"}),
        _StreamResponse(200, ['data: [DONE]']),
    ])
    result = LinerSearchAgent(api_key="test-key", session=session).search(
        query="q", bus=EventBus()
    )
    assert result["references"] == []
    assert len(session.calls) == 2


def test_liner_search_agent_does_not_wait_on_long_rate_limit():
    session = _Session([_Response(429, {}, {"Retry-After": "60"})])
    with pytest.raises(StageError, match="HTTP 429"):
        LinerSearchAgent(api_key="test-key", session=session).search(
            query="q", bus=EventBus()
        )
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [
    _Response(401, {"error": "unauthorized"}),
    _Response(500, {"error": "temporary"}),
])
def test_liner_search_agent_provider_errors_are_safe(response):
    bus = EventBus()
    with pytest.raises(StageError):
        LinerSearchAgent(api_key="test-key", session=_Session([response, response])).search(
            query="q", bus=bus
        )
    assert "Liner" in bus.log[-1].payload["error"]
    assert "test-key" not in repr(bus.log[-1].payload)


def test_liner_search_agent_rejects_malformed_sse():
    bus = EventBus()
    response = _StreamResponse(200, ['data: {not-json}'])
    with pytest.raises(StageError, match="malformed SSE"):
        LinerSearchAgent(api_key="test-key", session=_Session([response])).search(
            query="q", bus=bus
        )
    assert bus.log[-1].payload["error"] == "Liner Search Agent returned malformed SSE"


def test_liner_search_agent_redacts_stream_error():
    bus = EventBus()
    response = _StreamResponse(200, [
        'data: {"type":"data-error","data":{"message":"secret-key is invalid"}}',
        'data: [DONE]',
    ])
    with pytest.raises(StageError) as caught:
        LinerSearchAgent(api_key="secret-key", session=_Session([response])).search(
            query="q", bus=bus
        )
    assert "secret-key" not in str(caught.value)
    assert "secret-key" not in repr(bus.log)


def test_liner_visualization_maps_sse_atlas_without_exposing_key():
    session = _Session([_StreamResponse(200, [
        'data: {"type":"start"}',
        'data: {"type":"data-atlas","data":{"atlasArtifact":{"html":"<!doctype html><p>ok</p>","theme":"process","description":"A process"}}}',
        'data: [DONE]',
    ])])
    bus = EventBus()
    result = LinerVisualization(api_key="test-key", session=session).render(
        query="explain calibration", bus=bus
    )
    assert result["kind"] == "atlas_html"
    assert result["theme"] == "process"
    assert result["html"].startswith("<!doctype")
    assert "test-key" not in repr(bus.log)
    assert session.calls[0][1]["headers"]["x-api-key"] == "test-key"


def test_model_output_object_and_refusal_payload():
    assert _as_object('{"explanation":"ok"}') == {"explanation": "ok"}
    state = PaperState(source_path="missing.pdf")
    bus = EventBus()
    Pipeline.build(bus=bus).run(state)
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
        created = client.post(
            "/api/runs",
            data={"source_text": "A testable result improves the measured outcome."},
        )
        assert created.status_code == 202
        body = created.json()
        payload = _wait_payload(client, body["payload_url"])
        assert payload.status_code == 200
        # Offline remains the archived legacy harness. Live defense runs use
        # DefensePayloadV1 and are covered by dedicated acceptance tests.
        assert payload.json()["schema_version"] == "1.1"
        stream = client.get(body["events_url"])
        assert "event: raw" in stream.text
        assert "event: complete" in stream.text


def test_server_rejects_empty_input():
    with TestClient(create_app(live=False)) as client:
        assert client.post("/api/runs", data={}).status_code == 422


def test_server_validates_pdf_content_and_size():
    with TestClient(create_app(live=False)) as client:
        malformed = client.post(
            "/api/runs",
            data={},
            files={"pdf": ("paper.pdf", b"%PDF-not-a-real-document", "application/pdf")},
        )
        assert malformed.status_code == 422
        oversized = client.post(
            "/api/runs",
            data={},
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


def test_run_store_retains_only_recent_completed_runs():
    store = RunStore(max_completed=2)
    for index in range(3):
        record = RunRecord(f"run-{index}")
        store.reserve(record)
        record.status = "completed"
        store.finish(record)
        time.sleep(0.001)
    assert len(store.records) == 2
    assert "run-0" not in store.records
    assert {"run-1", "run-2"}.issubset(store.records)
