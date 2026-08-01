"""Deterministic contracts for the paper-defense backend.

These tests deliberately do not call providers. Live quality acceptance is
run separately; this file protects the boundaries that must hold regardless
of model or search output.
"""

import json
import re
from pathlib import Path

from playground.defense import (
    DefenseContextAnalyst,
    DefenseEvidenceController,
    DefenseCritic,
    defense_stages,
    _clean_query,
    _claim_candidate,
)
from playground.defense_eval import evaluate_payload
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


class _DefenseLLM:
    """Small provider double for the deterministic pipeline contract test."""

    def __init__(self):
        self.roles = []

    def structured(self, *, role, prompt, schema_hint, bus):
        self.roles.append(role)
        data = json.loads(prompt)
        if role == "defense_context":
            match = re.search(r"\[(text_b\d+)", data["source_context"])
            span_id = match.group(1)
            return {"claims": [{
                "id": "c1", "text": "Our model improves calibration on held-out validation.",
                "evidence_span_ids": [span_id], "importance": 0.9,
                "vulnerability": 0.8, "scope_gap": 0.7,
                "attack_dimensions": ["measurement_validity"],
                "attack_rationale": "calibration depends on the evaluation definition",
            }], "root_claim_id": "c1", "limitations": []}
        if role == "defense_probe":
            span_id = data["source_spans"][0]["id"]
            return {
                "assumptions": [{
                    "id": "a1", "text": "Calibration is measured on a held-out validation set.",
                    "category": "measurement_validity", "origin": "paper_explicit",
                    "source_span_ids": [span_id],
                    "failure_effect": "Using a different split can change the reported calibration gap.",
                    "support_type": "independent",
                }],
                "attack_questions": [{
                    "id": "q1", "question": "Was calibration measured on a held-out split?",
                    "attack_type": "measurement_validity", "assumption_ids": ["a1"],
                    "severity": "high", "why_likely": "the metric depends on the split",
                }],
                "search_actions": [{
                    "id": "s1", "query": "calibration held-out validation reliability", "question_ids": ["q1"],
                    "rationale": "test the measurement assumption",
                }],
            }
        if role == "defense_evidence_interpreter":
            source = data["retrieved_sources"][0]
            return {"assessments": [{
                "source_url": source["url"], "relation": "supports", "chunk_nums": [1],
                "obligation_ids": ["q1"], "confidence": 0.8,
                "rationale": "the source describes held-out calibration evaluation",
            }], "sufficient": True, "missing_obligation_ids": []}
        if role == "defense_synthesizer":
            span_id = next(iter(data["source_spans"]))
            return {
                "weak_point": "The result is sensitive to calibration measurement choices.",
                "external_evidence": {"supports": [{"evidence_id": "ev_0", "summary": "Held-out evaluation is standard."}]},
                "defensible_scope": {
                    "statement": "The result holds in the reported held-out validation setting.",
                    "confidence": "medium", "basis_kind": "external_corroborated",
                    "conditions": ["held-out validation"], "source_refs": [span_id],
                    "evidence_ids": ["ev_0"], "excluded_scope": ["all datasets"],
                },
                "assumption_impacts": [{
                    "assumption_id": "a1", "surviving_scope": "Only the reported split remains supported.",
                    "because": "Changing the split changes the calibration estimate.",
                    "source_refs": [span_id], "evidence_ids": ["ev_0"],
                }],
            }
        if role == "defense_critic":
            return {"findings": []}
        raise AssertionError(f"unexpected role: {role}")


class _DefenseSearch:
    def search(self, *, query, bus):
        return {
            "references": [{"title": "Calibration methods", "url": "https://example.test/calibration", "description": "source"}],
            "reference_chunks": [{
                "num": 1, "content": "Held-out calibration evaluation is described.",
                "source_url": "https://example.test/calibration", "source_title": "Calibration methods",
            }],
        }


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
    state.defense_questions[0].assumption_ids = ["a1"]
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
    }, {
        "action": {"id": "a2", "query": "calibration reliability", "question_ids": ["q1"]},
        "result": {
            "references": [{"title": "Calibration study", "url": "https://example.test/p", "description": "duplicate"}],
            "reference_chunks": [{"num": 1, "content": "The comparison uses matched validation settings.", "sourceUrl": "https://example.test/p"}],
        },
    }]
    interpretation = {"assessments": [
        {"source_url": "https://example.test/p", "relation": "supports", "chunk_nums": [1],
         "obligation_ids": ["a1"], "confidence": 0.7, "rationale": "matched settings"},
        {"source_url": "https://example.test/p", "relation": "qualifies", "chunk_nums": [2],
         "obligation_ids": ["a1"], "confidence": 0.8, "rationale": "shift boundary"},
    ]}

    controller._record_ledger(
        state.evidence_ledger, results, interpretation, state, EventBus()
    )
    record = state.evidence_ledger.records[0]
    assert len(state.evidence_ledger.records) == 1
    assert record.relation == "qualifies"
    assert len(record.chunks) == 2
    assert record.obligation_ids == ["q1"]
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


def test_empty_search_is_partial_and_never_positive_evidence():
    state = _state_with_claim()
    state.defense_probe = {"search_actions": [
        {"id": "a1", "query": "calibration reliability", "question_ids": ["q1"]},
    ]}
    controller = DefenseEvidenceController.__new__(DefenseEvidenceController)
    controller.profile = FAST_PROFILE
    controller.search = None
    controller.llm = None
    controller.prompt_chars = 14_000
    controller._search_actions = lambda actions, bus: []
    controller._interpret = lambda state, results, bus: {
        "assessments": [], "sufficient": False, "missing_obligation_ids": ["q1"]
    }
    controller.run(state, EventBus())
    assert state.evidence_ledger.status == "partial"
    assert state.evidence_ledger.stop_reason == "partial_evidence"
    assert state.evidence_ledger.records == []


def test_defense_payload_uses_defense_mode_not_legacy_state_mode():
    state = _state_with_claim()
    state.artifact = {"primitive": "defense_report", "mode": "complete"}
    bus = EventBus()
    bus.emit_raw("stage_end", stage="defense_context", seconds=1.25)
    bus.tool_call("llm.structured", role="defense_context")
    payload = build_defense_payload(state, bus, run_id="r1")
    assert payload["schema_version"] == "defense/1.0"
    assert payload["mode"] == "complete"
    assert payload["run"]["stage_elapsed_seconds"] == {"defense_context": 1.25}
    assert payload["run"]["provider_call_counts"] == {"llm.structured": 1}

    state.artifact = {"primitive": "partial_defense_report", "mode": "partial"}
    partial = build_defense_payload(state, EventBus(), run_id="r2")
    assert partial["mode"] == "partial"


def test_defense_payload_keeps_status_channel_out_of_raw_events():
    state = _state_with_claim()
    state.artifact = {"primitive": "partial_defense_report", "mode": "partial"}
    bus = EventBus()
    bus.emit_raw("decision", actor="test", text="partial")
    bus.emit_status("friendly status")
    payload = build_defense_payload(state, bus, run_id="r3")
    assert [event["type"] for event in payload["raw_events"]] == ["decision"]


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


def test_critic_precheck_rejects_scope_that_broadens_claim():
    state = _state_with_claim()
    report = {
        "target_claim": {"id": "c1", "source_refs": ["p1"]},
        "attack_questions": [], "external_evidence": {},
        "assumption_impacts": [],
        "defensible_scope": {"statement": "The model is universally superior in every dataset."},
    }
    codes = {item["code"] for item in DefenseCritic._precheck(state, report)}
    assert "DEFENSE_SCOPE_BROADENED" in codes


def test_critic_allows_cross_language_scope_when_attribution_exists():
    state = _state_with_claim()
    report = {
        "target_claim": {"id": "c1", "source_refs": ["p1"]},
        "attack_questions": [], "external_evidence": {},
        "assumption_impacts": [],
        "defensible_scope": {
            "statement": "보고된 검증 분할 설정에서만 결과를 방어할 수 있습니다.",
            "source_refs": ["p1"], "evidence_ids": [],
        },
    }
    codes = {item["code"] for item in DefenseCritic._precheck(state, report)}
    assert "DEFENSE_SCOPE_UNGROUNDED" not in codes


def test_critic_fatal_partial_hides_unverified_defense_fields():
    report = {
        "primitive": "defense_report",
        "defensible_scope": {"statement": "bounded"},
        "assumption_impacts": [{"assumption_id": "a1"}],
        "limitations": [],
    }
    partial = DefenseCritic._partial(report, [{"code": "EVIDENCE_UNRESOLVED", "detail": "no chunk"}])
    assert partial["primitive"] == "partial_defense_report"
    assert "defensible_scope" not in partial
    assert partial["assumption_impacts"] == []


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


def test_live_defense_pipeline_produces_complete_report_with_grounded_evidence():
    llm = _DefenseLLM()
    bus = EventBus()
    pipeline = Pipeline(
        defense_stages(llm, _DefenseSearch(), FAST_PROFILE), bus, FAST_PROFILE
    )
    state = PaperState(source_text=(
        "Abstract\n\n"
        "Our model improves calibration on held-out validation.\n\n"
        "Results\n\n"
        "The calibration gap decreases after fitting the model."
    ))
    pipeline.run(state)

    assert state.artifact["primitive"] == "defense_report"
    assert state.defense_verdict["result"] == "PASS"
    assert state.evidence_ledger.status == "sufficient"
    assert state.artifact["defensible_scope"]["evidence_ids"] == ["ev_0"]
    assert state.artifact["assumption_impacts"][0]["status_if_off"] == "narrows"
    assert llm.roles == [
        "defense_context", "defense_probe", "defense_evidence_interpreter",
        "defense_synthesizer", "defense_critic",
    ]


def test_acceptance_evaluator_is_deterministic_and_does_not_require_provider():
    llm = _DefenseLLM()
    bus = EventBus()
    pipeline = Pipeline(
        defense_stages(llm, _DefenseSearch(), FAST_PROFILE), bus, FAST_PROFILE
    )
    state = PaperState(source_text=(
        "Abstract\n\nOur model improves calibration on held-out validation."
    ))
    pipeline.run(state)
    payload = build_defense_payload(state, bus, run_id="eval")
    result = evaluate_payload(payload)
    assert result["passed"] is True
    assert result["score"] >= 75
