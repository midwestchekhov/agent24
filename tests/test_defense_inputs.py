"""Input-boundary cases for the defense product contract."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from playground.defense import _fallback_claims
from playground.defense import defense_stages
from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.runtime import FAST_PROFILE
from playground.server import create_app
from playground.stages import Parse
from playground.state import PaperState
from playground.errors import StageError


def _parse_text(text: str) -> PaperState:
    state = PaperState(source_text=text)
    Parse().run(state, EventBus())
    return state


def test_source_text_sections_keep_methods_and_references_out_of_claim_candidates():
    state = _parse_text(
        "Abstract\n\nWe find that the proposed model improves calibration.\n\n"
        "Methods\n\nWe used 15 bins and 100 bootstrap samples.\n\n"
        "Results\n\nThe held-out result improves reliability.\n\n"
        "References\n\nWe demonstrate a result in a cited paper."
    )
    assert {span.section for span in state.doc.spans.values()} >= {
        "abstract", "methods", "results", "references",
    }
    claims = _fallback_claims(state)
    assert claims
    assert all(
        state.doc.spans[ref].section in {"abstract", "intro", "results", "discussion"}
        for claim in claims for ref in claim["evidence_span_ids"]
    )
    assert not any("15 bins" in claim["text"] for claim in claims)
    assert not any("cited paper" in claim["text"] for claim in claims)


def test_empty_or_claim_only_live_server_input_is_rejected_before_worker_start():
    app = create_app(live=True, live_fast=True)
    with TestClient(app) as client:
        assert client.post("/api/runs", data={}).status_code == 422
        response = client.post(
            "/api/runs", data={"claim_text": "The model improves prediction."}
        )
        assert response.status_code == 422
        assert "claim_text" in response.text
        assert not app.state.run_store.records


def test_source_title_is_optional_and_parse_infers_one_from_text():
    state = _parse_text(
        "On Calibration of Modern Neural Networks\n\n"
        "Abstract\n\nWe find a measurable calibration result."
    )
    assert state.source_title
    assert state.doc.spans


@pytest.mark.parametrize("filename, title_prefix", [
    ("attention_is_all_you_need.pdf", "Attention Is All You Need"),
    ("deep_residual_learning_cvpr2016.pdf", "Deep Residual Learning for Image Recognition"),
])
def test_landmark_pdf_title_inference_skips_license_and_author_blocks(filename, title_prefix):
    state = PaperState(source_path=str(Path(__file__).parents[1] / "fixtures" / filename))
    Parse().run(state, EventBus())
    assert state.source_title.startswith(title_prefix)


class _EmptyLLM:
    def structured(self, *, role, prompt, schema_hint, bus):
        bus.tool_call("llm.structured", role=role, schema=schema_hint)
        return {}


class _NoSearch:
    def search(self, *, query, bus):
        return {"references": [], "reference_chunks": []}


def test_references_only_source_returns_defense_refusal():
    bus = EventBus()
    pipeline = Pipeline(defense_stages(_EmptyLLM(), _NoSearch(), FAST_PROFILE), bus, FAST_PROFILE)
    state = PaperState(source_text=(
        "References\n\nThis bibliography entry is deliberately long enough to be a block "
        "but cannot serve as a falsifiable paper claim."
    ))
    pipeline.run(state)
    payload = build_payload(state, bus, run_id="refusal")
    assert payload["mode"] == "refused"
    assert payload["artifact"]["primitive"] == "refusal"


@pytest.mark.parametrize("filename", [
    "07_scanned_no_text_layer.pdf",
    "08_encrypted.pdf",
    "09_empty.pdf",
    "10_not_a_pdf.pdf",
    "11_truncated.pdf",
    "12_blank_page.pdf",
])
def test_malformed_or_nontext_pdf_fails_closed(filename):
    path = Path(__file__).parent / "inputs" / filename
    with pytest.raises(StageError):
        Parse().run(PaperState(source_path=str(path)), EventBus())


@pytest.mark.parametrize("filename", ["08_encrypted.pdf", "11_truncated.pdf"])
def test_server_rejects_encrypted_or_truncated_pdf_before_run(filename):
    path = Path(__file__).parent / "inputs" / filename
    app = create_app(live=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            data={},
            files={"pdf": (filename, path.read_bytes(), "application/pdf")},
        )
    assert response.status_code == 422
    assert not app.state.run_store.records
