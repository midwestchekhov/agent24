"""Broad deterministic semantic boundary cases for the defense backend.

These cases intentionally use several paper-like domains and do not call
OpenAI or Liner.  They protect the provenance and fail-closed rules while the
live scorer remains responsible for provider quality.
"""

from __future__ import annotations

import pytest

from playground.defense import (
    DefenseContextAnalyst,
    DefenseCritic,
    DefenseProbe,
)
from playground.events import EventBus
from playground.state import (
    AttackQuestion,
    Claim,
    DefenseAssumption,
    DocGraph,
    EvidenceChunk,
    EvidenceLedger,
    EvidenceRecord,
    PaperState,
    Span,
)


def _claim_state(*, span_text: str, section: str = "results", kind: str = "paragraph") -> PaperState:
    state = PaperState(source_title="semantic case")
    state.doc = DocGraph(spans={
        "s1": Span(id="s1", page=2, kind=kind, section=section, text=span_text),
    })
    state.claims = [Claim(
        id="c1", text="The proposed model improves held-out performance.",
        evidence_span_ids=["s1"],
    )]
    state.defense_frontier_id = "c1"
    return state


@pytest.mark.parametrize("domain, section, kind, source, candidate, accepted", [
    (
        "clinical", "abstract", "paragraph",
        "We show that the risk model improves held-out readmission prediction.",
        "The risk model improves held-out readmission prediction.", True,
    ),
    (
        "calibration", "results", "paragraph",
        "We observe lower calibration error on the external validation cohort.",
        "The method lowers calibration error on external validation.", True,
    ),
    (
        "genomics", "discussion", "paragraph",
        "These findings indicate that the mutation pattern is associated with treatment response.",
        "The mutation pattern is associated with treatment response.", True,
    ),
    (
        "nlp", "intro", "paragraph",
        "Prior work suggests attention improves retrieval in long documents.",
        "Attention improves retrieval in long documents.", True,
    ),
    (
        "reference", "references", "paragraph",
        "We demonstrate improved prediction in the cited study.",
        "The cited study demonstrates improved prediction.", False,
    ),
    (
        "protocol", "methods", "paragraph",
        "We demonstrate that the 100 V pulse improves electroporation efficiency.",
        "The 100 V pulse improves electroporation efficiency.", False,
    ),
    (
        "funding", "acknowledgments", "paragraph",
        "We show gratitude to the clinical research staff and participants.",
        "The clinical research staff improve prediction.", False,
    ),
    (
        "table", "results", "table",
        "We show that the proposed model improves the measured outcome.",
        "The proposed model improves the measured outcome.", False,
    ),
    (
        "unrelated", "results", "paragraph",
        "We show that the microscope has a wider field of view.",
        "The model improves held-out performance.", False,
    ),
])
def test_context_claim_candidates_are_section_and_text_grounded(
    domain, section, kind, source, candidate, accepted,
):
    state = _claim_state(span_text=source, section=section, kind=kind)
    result = DefenseContextAnalyst._accept_claims([{
        "id": f"{domain}-c1",
        "text": candidate,
        "evidence_span_ids": ["s1"],
        "importance": 0.8,
        "vulnerability": 0.7,
        "scope_gap": 0.5,
    }], state, EventBus())
    assert bool(result) is accepted


@pytest.mark.parametrize("origin, source, assumption, expected_origin, expected_count", [
    (
        "paper_explicit",
        "The held-out cohort was evaluated after model fitting.",
        "The held-out cohort was evaluated after model fitting.",
        "paper_explicit", 1,
    ),
    (
        "paper_implicit",
        "The study compares matched cohorts under the same evaluation protocol.",
        "The comparison depends on matched cohorts and the same protocol.",
        "paper_implicit", 1,
    ),
    (
        "paper_explicit",
        "The model improves prediction on the held-out cohort.",
        "Finite-sample uncertainty is sufficiently small for the estimate to be stable.",
        "analyst_inferred", 1,
    ),
    (
        "paper_explicit",
        "The model improves prediction on the held-out cohort.",
        "The evaluation uses the held-out cohort.",
        "paper_explicit", 1,
    ),
    (
        "paper_explicit",
        "The model improves prediction on the held-out cohort.",
        "The evaluation uses a missing source condition.",
        "analyst_inferred", 1,
    ),
])
def test_probe_assumption_origin_and_span_rules(
    origin, source, assumption, expected_origin, expected_count,
):
    state = _claim_state(span_text=source)
    state.defense_frontier_id = "c1"
    result = DefenseProbe._assumptions([{
        "id": "a1", "text": assumption,
        "category": "measurement_validity", "origin": origin,
        "source_span_ids": ["s1"],
        "failure_effect": "If this condition fails, the reported comparison may not hold.",
    }], state, EventBus())
    assert len(result) == expected_count
    if result:
        assert result[0].origin == expected_origin


def test_probe_normalizes_all_necessary_assumptions():
    state = _claim_state(
        span_text="The held-out cohort is evaluated with the same protocol and metric."
    )
    raw = [
        {
            "id": f"a{i}", "text": f"Condition {i} is stated for the held-out cohort.",
            "category": "measurement_validity", "origin": "paper_explicit",
            "source_span_ids": ["s1"],
            "failure_effect": "If this condition fails, the reported comparison may not hold.",
            "support_type": "necessary",
        }
        for i in range(1, 4)
    ]
    result = DefenseProbe._assumptions(raw, state, EventBus())
    assert [item.support_type for item in result] == ["necessary", "independent", "independent"]


@pytest.mark.parametrize("question, attack_type, assumption_ids, expected", [
    ("Was the held-out cohort separated before fitting?", "data_integrity", ["a1"], True),
    ("Could an unrelated deployment environment explain the result?", "external_validity", ["a1"], False),
    ("Was the held-out cohort separated before fitting?", "not_allowed", ["a1"], False),
    ("Was the held-out cohort separated before fitting?", "data_integrity", ["unknown"], False),
    ("Was the held-out cohort separated before fitting?", "data_integrity", [], False),
])
def test_probe_questions_require_allowed_type_and_assumption_grounding(
    question, attack_type, assumption_ids, expected,
):
    state = _claim_state(
        span_text="The held-out cohort was separated before fitting the prediction model."
    )
    assumptions = [DefenseAssumption(
        id="a1", claim_id="c1", text="The held-out cohort is separated before fitting.",
        category="data_integrity", origin="paper_explicit", source_span_ids=["s1"],
        failure_effect="If separation fails, leakage can inflate the reported performance.",
    )]
    result = DefenseProbe._questions([{
        "id": "q1", "question": question, "attack_type": attack_type,
        "assumption_ids": assumption_ids,
    }], assumptions, state, EventBus())
    assert bool(result) is expected


def _critic_state() -> PaperState:
    state = _claim_state(
        span_text="The evaluated model improves held-out prediction on the validation cohort."
    )
    state.evidence_ledger = EvidenceLedger(records=[EvidenceRecord(
        id="ev1", obligation_ids=["q1"], query="held out validation leakage",
        title="Validation study", url="https://example.test/validation",
        relation="qualifies", chunks=[EvidenceChunk(
            id="ch1", num=1, content="Leakage can inflate performance on a held-out cohort.",
            source_url="https://example.test/validation",
        )],
    )])
    return state


@pytest.mark.parametrize("report, expected_code", [
    (
        {
            "target_claim": {"id": "wrong", "source_refs": ["s1"]},
            "attack_questions": [], "external_evidence": {}, "assumption_impacts": [],
            "defensible_scope": {"statement": "bounded", "source_refs": ["s1"]},
        },
        "TARGET_CLAIM_MISMATCH",
    ),
    (
        {
            "target_claim": {"id": "c1", "source_refs": ["s1"]},
            "attack_questions": [],
            "external_evidence": {"supports": [{"evidence_id": "ev1", "relation": "supports", "chunk_ids": []}]},
            "assumption_impacts": [],
            "defensible_scope": {"statement": "bounded", "source_refs": ["s1"]},
        },
        "EVIDENCE_WITHOUT_CHUNK",
    ),
    (
        {
            "target_claim": {"id": "c1", "source_refs": ["s1"]},
            "attack_questions": [], "external_evidence": {}, "assumption_impacts": [],
            "defensible_scope": {"statement": "The model is universally superior in every setting."},
        },
        "DEFENSE_SCOPE_BROADENED",
    ),
    (
        {
            "target_claim": {"id": "c1", "source_refs": ["missing"]},
            "attack_questions": [], "external_evidence": {}, "assumption_impacts": [],
            "defensible_scope": {"statement": "bounded"},
        },
        "MISSING_SOURCE_SPAN",
    ),
])
def test_critic_precheck_matrix_is_fail_closed(report, expected_code):
    codes = {item["code"] for item in DefenseCritic._precheck(_critic_state(), report)}
    assert expected_code in codes


def test_critic_precheck_allows_chunk_grounded_qualifying_evidence():
    state = _critic_state()
    report = {
        "target_claim": {"id": "c1", "source_refs": ["s1"]},
        "attack_questions": [],
        "external_evidence": {"qualifies": [{
            "evidence_id": "ev1", "relation": "qualifies", "chunk_ids": ["ch1"],
        }]},
        "assumption_impacts": [],
        "defensible_scope": {"statement": "The model improves held-out prediction.", "source_refs": ["s1"]},
    }
    codes = {item["code"] for item in DefenseCritic._precheck(state, report)}
    assert "EVIDENCE_WITHOUT_CHUNK" not in codes
    assert "MISSING_EVIDENCE_ID" not in codes
