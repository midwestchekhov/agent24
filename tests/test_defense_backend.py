"""Deterministic contracts for the paper-defense backend.

These tests deliberately do not call providers. Live quality acceptance is
run separately; this file protects the boundaries that must hold regardless
of model or search output.
"""

import json
from pathlib import Path

from playground.defense import (
    DefenseContextAnalyst,
    DefenseEvidenceController,
    DefenseCritic,
    _clean_query,
    _claim_candidate,
)
from playground.defense_payload import build_defense_payload
from playground.events import EventBus
from playground.pipeline import Pipeline
from playground.runtime import FAST_PROFILE
from playground.state import (
    AttackQuestion,
    Claim,
    DocGraph,
    EvidenceChunk,
    EvidenceLedger,
    EvidenceObligation,
    EvidenceRound,
    EvidenceRecord,
    PaperState,
    Span,
)


def _state_with_claim() -> PaperState:
    state = PaperState(source_title="fixture")
    state.doc = DocGraph(spans={
        "p1": Span(
            id="p1", page=1, kind="paragraph", section="results",
            text="The evaluated model improves calibration on the held-out test set.",
        ),
        "ref1": Span(
            id="ref1", page=8, kind="paragraph", section="references",
            text="A reference entry that must never become a claim.",
        ),
    })
    state.claims = [Claim(id="c1", text="The model improves calibration.", evidence_span_ids=["p1"])]
    state.defense_frontier_id = "c1"
    state.defense_questions = [AttackQuestion(
        id="q1", question="Was the comparison fair?", attack_type="comparison_fairness",
        assumption_ids=[], severity="high",
    )]
    state.evidence_ledger = EvidenceLedger(
        obligations=[EvidenceObligation(id="q1", question="Was the comparison fair?", claim_ids=["c1"])],
        rounds=[EvidenceRound(index=1)],
    )
    return state


def test_query_cleaner_rejects_filters_and_instruction_prose():
    assert _clean_query("neural calibration before 2017-05-23") == ""
    assert _clean_query("Retrieve the experiment section for calibration") == ""
    assert _clean_query("temperature scaling calibration reliability")


def test_claim_candidate_rejects_reference_and_methods_spans():
    reference = Span(
        id="r", page=9, kind="paragraph", section="references",
        text="We demonstrate a result in this cited reference.",
    )
    methods = Span(
        id="m", page=3, kind="paragraph", section="methods",
        text="We demonstrate pulses improve the electroporation protocol.",
    )
    result = Span(
        id="p", page=4, kind="paragraph", section="results",
        text="We demonstrate that the proposed model improves held-out accuracy.",
    )
    assert not _claim_candidate(reference)
    assert not _claim_candidate(methods)
    assert _claim_candidate(result)


def test_context_analyst_rejects_unrelated_claim_on_existing_span():
    state = _state_with_claim()
    accepted = DefenseContextAnalyst._accept_claims([
        {
            "id": "c-bad",
            "text": "A completely unrelated causal treatment claim.",
            "evidence_span_ids": ["p1"],
            "importance": 1.0,
        },
        {
            "id": "c-good",
            "text": "The evaluated model improves calibration on the held-out test set.",
            "evidence_span_ids": ["p1"],
            "importance": 1.0,
        },
    ], state, EventBus())
    assert [item["id"] for item in accepted] == ["c-good"]


def test_evidence_record_merges_multiple_assessments_for_same_url():
    state = _state_with_claim()
    controller = DefenseEvidenceController.__new__(DefenseEvidenceController)
    results = [{
        "action": {"id": "a1", "query": "calibration", "question_ids": ["q1"]},
        "result": {
            "references": [{"title": "Calibration study", "url": "https://example.test/p", "description": "source"}],
            "reference_chunks": [
                {"num": 1, "content": "The comparison uses matched validation settings.", "sourceUrl": "https://example.test/p"},
                {"num": 2, "content": "The effect weakens under distribution shift.", "sourceUrl": "https://example.test/p"},
            ],
        },
    }]
    interpretation = {"assessments": [
        {"source_url": "https://example.test/p", "relation": "supports", "chunk_nums": [1],
         "obligation_ids": ["q1"], "confidence": 0.7, "rationale": "matched settings"},
        {"source_url": "https://example.test/p", "relation": "qualifies", "chunk_nums": [2],
         "obligation_ids": ["q1"], "confidence": 0.8, "rationale": "shift boundary"},
    ]}

    controller._record_ledger(
        state.evidence_ledger, results, interpretation, state, EventBus()
    )
    record = state.evidence_ledger.records[0]
    assert record.relation == "qualifies"
    assert len(record.chunks) == 2
    assert state.evidence_ledger.status == "sufficient"


def test_fast_evidence_controller_honors_one_action_one_round_cap():
    state = _state_with_claim()
    state.defense_probe = {"search_actions": [
        {"id": "a1", "query": "calibration reliability", "question_ids": ["q1"]},
        {"id": "a2", "query": "temperature scaling validation", "question_ids": ["q1"]},
    ]}
    controller = DefenseEvidenceController.__new__(DefenseEvidenceController)
    controller.profile = FAST_PROFILE
    controller.search = None
    controller.llm = None
    controller.prompt_chars = 14_000
    controller._search_actions = lambda actions, bus: []
    controller._interpret = lambda state, results, bus: {"assessments": []}
    controller.run(state, EventBus())
    assert len(state.evidence_ledger.rounds) == 1
    assert len(state.evidence_ledger.rounds[0].actions) == 1


def test_defense_payload_uses_defense_mode_not_legacy_state_mode():
    state = _state_with_claim()
    state.artifact = {"primitive": "defense_report", "mode": "complete"}
    payload = build_defense_payload(state, EventBus(), run_id="r1")
    assert payload["schema_version"] == "defense/1.0"
    assert payload["mode"] == "complete"

    state.artifact = {"primitive": "partial_defense_report", "mode": "partial"}
    partial = build_defense_payload(state, EventBus(), run_id="r2")
    assert partial["mode"] == "partial"


def test_live_deadline_returns_partial_defense_shape():
    state = _state_with_claim()
    Pipeline._deadline_artifact(state, defense=True)
    assert state.artifact["primitive"] == "partial_defense_report"
    assert state.artifact["mode"] == "partial"
    assert "defensible_scope" not in state.artifact
    assert state.artifact["target_claim"]["id"] == "c1"


def test_critic_precheck_requires_source_and_evidence_boundaries():
    state = _state_with_claim()
    state.defense_assumptions = []
    report = {
        "target_claim": {"id": "c1", "source_refs": ["missing"]},
        "attack_questions": [],
        "external_evidence": {"supports": [{"evidence_id": "ev_missing", "relation": "supports", "chunk_ids": []}]},
        "assumption_impacts": [],
        "defensible_scope": {"statement": "bounded"},
    }
    violations = DefenseCritic._precheck(state, report)
    codes = {item["code"] for item in violations}
    assert "MISSING_SOURCE_SPAN" in codes
    assert "EVIDENCE_WITHOUT_CHUNK" in codes
    assert "MISSING_EVIDENCE_ID" in codes


def test_gold_set_tracks_three_backend_acceptance_fixtures():
    gold = json.loads((Path(__file__).with_name("defense_gold.json")).read_text())
    assert set(gold) == {
        "sample.pdf", "guo17a.pdf",
        "Nature_2018_Lee_et_al._Human_glioblastoma_arises_from_subventricular_zone_cells.pdf",
    }
    for rubric in gold.values():
        assert 1 <= len(rubric["frontier_concepts"]) <= 6
        assert rubric["required_attack_types"]
        assert rubric["required_scope_terms"]
        assert rubric["forbidden_overclaims"]
