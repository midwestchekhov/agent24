from pathlib import Path

from playground import primitives
from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.state import Claim, DocGraph, PaperState, Span
from playground.clients import MockLLM
from playground.stages import (
    AssumptionMiner, BottleneckMiner, ContextAnalyst, EvidenceController, Parse,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "guo17a.pdf"


def test_guo_uses_one_context_pass_and_separates_graph_from_artifact():
    bus = EventBus()
    state = PaperState(source_path=str(FIXTURE))
    Pipeline.build(bus=bus).run(state)

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
    assert state.explainer_route == "rate_compare"
    assert state.artifact is not None
    assert "claim_graph" not in state.artifact
    assert {panel["primitive"] for panel in state.artifact["panels"]} == {
        "rate_compare", "flow_topology"
    }
    # A static fixture cannot cite real number-pool ids, so the offline panels
    # must be demoted to illustrative and carry a notice.
    for panel in state.artifact["panels"]:
        assert panel["provenance"][0]["provenance"] == "illustrative"
        assert panel["notice"]

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
    Pipeline.build(llm=llm, bus=EventBus()).run(state)

    assert state.mode != "refused"
    assert state.claims
    assert all(sid in state.doc.spans for c in state.claims for sid in c.evidence_span_ids)


def test_guo_sections_and_nonclaim_table_are_fail_closed():
    state = PaperState(source_path=str(FIXTURE))
    Parse().run(state, EventBus())

    assert state.doc.sections[0]["claim_sections_reliable"] is True
    assert state.doc.spans["p3_b43"].section == "results"
    assert state.doc.spans["p6_b1"].section == "methods"
    assert state.doc.spans["p7_b8"].section == "results"

    llm = MockLLM(fixtures={
        "context_analyst": {
            "claims": [
                {
                    "id": "c1", "parent_id": None,
                    "text": "Chuan Guo and colleagues",
                    "evidence_span_ids": ["p1_b1"],
                },
                {
                    "id": "c2", "parent_id": "c1",
                    "text": "Calibration Error Values Across Vision Datasets and Architectures",
                    "evidence_span_ids": ["p6_b1", "p6_b3", "p7_b8", "p2_b22"],
                },
            ]
        }
    })
    ContextAnalyst(llm).run(state, EventBus())

    assert state.context_analysis["claims"]
    assert state.context_analysis["claims"][0]["evidence_span_ids"] == ["p1_b32"]
    assert all(
        "Calibration Error Values Across" not in claim["text"]
        for claim in state.context_analysis["claims"]
    )


def test_assumption_definition_is_rejected_and_necessary_requires_cascade():
    state = PaperState(
        doc=DocGraph(spans={
            "p6_b3": Span(
                "p6_b3", 6, "caption",
                "Table 1. ECE (%) with M = 15 bins.", section="results",
            )
        }),
        claims=[Claim("c1", "15-bin ECE supports a calibration conclusion", ["p6_b3"])],
        selected_claim_id="c1",
        critical_path_ids=["c1"],
    )
    raw = [
        {
            "id": "a1",
            "text": "The reported percentages are ECE values measured with 15 bins.",
            "kind": "measurement", "source": "paper_explicit", "span_id": "p6_b3",
            "weakens_how": "A different definition would quantify a different property than calibration.",
            "support_type": "necessary",
        },
        {
            "id": "a2",
            "text": "15-bin ECE is a reliable estimator of calibration error for these predictions.",
            "kind": "measurement", "source": "paper_implicit", "span_id": "p6_b3",
            "weakens_how": "If binning bias is large, the same reported percentages no longer support the calibration comparison.",
            "support_type": "necessary",
        },
    ]
    kept = AssumptionMiner(MockLLM())._accept(raw, state.claims[0], state, EventBus())

    assert [item.id for item in kept] == ["a2"]
    assert kept[0].support_type == "independent"


def test_evidence_controller_refines_until_required_obligations_are_covered():
    class ControllerLLM:
        def structured(self, *, role, prompt, **kwargs):
            data = __import__("json").loads(prompt)
            if role == "evidence_planner":
                if data["round"] == 1:
                    return {"actions": [{
                        "id": "q1", "obligation_ids": ["ob2"],
                        "query": "temperature scaling calibration distribution shift limitation",
                        "rationale": "check the boundary first",
                    }]}
                return {"actions": [{
                    "id": "q2", "obligation_ids": ["ob1"],
                    "query": "temperature scaling independent calibration validation",
                    "rationale": "fill direct support",
                }]}
            source = data["retrieved_sources"][0]
            obligation_id = source["action_obligation_ids"][0]
            return {
                "assessments": [{
                    "source_url": source["url"],
                    "obligation_ids": [obligation_id],
                    "relation": "qualifies" if obligation_id == "ob2" else "supports",
                    "confidence": 0.9,
                    "rationale": "the returned chunk directly addresses the obligation",
                    "chunk_nums": [1],
                }],
                "sufficient": obligation_id == "ob1",
                "missing_obligation_ids": ([] if obligation_id == "ob1" else ["ob1"]),
                "next_focus": "find independent validation",
            }

    class SearchAgent:
        def search(self, *, query, bus):
            slug = "boundary" if "shift" in query else "support"
            return {
                "answer": "",
                "references": [{
                    "title": f"Calibration {slug}",
                    "url": f"https://e/{slug}", "snippet": "",
                }],
                "reference_chunks": [{
                    "num": 1, "content": f"Direct calibration {slug} evidence",
                    "source_title": f"Calibration {slug}",
                    "source_url": f"https://e/{slug}",
                }],
            }

    claims = [Claim(
        "c1",
        "Modern neural networks can improve accuracy while calibration worsens, and temperature scaling corrects confidence.",
        ["p1"], role="result",
    )]
    stage = EvidenceController(ControllerLLM(), SearchAgent())
    bus = EventBus()
    state = PaperState(
        claims=claims, selected_claim_id="c1", critical_path_ids=["c1"],
        context_analysis={"search_obligations": [
            {"id": "ob1", "question": "Is it independently validated?",
             "claim_ids": ["c1"], "kind": "support", "required": True},
            {"id": "ob2", "question": "Where does it fail?",
             "claim_ids": ["c1"], "kind": "boundary", "required": True},
        ]},
    )
    stage.run(state, bus)
    assert state.evidence_ledger.status == "sufficient"
    assert len(state.evidence_ledger.rounds) == 2
    assert {item.relation for item in state.evidence_ledger.records} == {
        "supports", "qualifies",
    }
    assert {item.url for item in state.external["c1"]} == {
        "https://e/support", "https://e/boundary",
    }


def test_composer_rejects_panels_with_invented_numbers():
    """The model cannot invent the model: a slot naming a number id that is
    not in the pool costs the panel its life, not just its label."""
    state = PaperState(source_path=str(FIXTURE))
    Parse().run(state, EventBus())

    binding = primitives.bind("proportion_reveal", {
        "total": {"label": "전체", "value_id": "not_a_real_number"},
        "active": {"label": "활성", "value_id": "also_fake"},
    }, state)
    assert not binding.ok
    assert any("not in the number pool" in problem for problem in binding.problems)

    # And a threshold without a source-stated boundary is refused outright --
    # an invented limit teaches a limit the paper never claimed.
    binding = primitives.bind("threshold_finder", {
        "x": {"label": "x", "min": 0, "max": 1},
        "curve": {"label": "f", "expression": "x * 2"},
        "boundary": {"label": "한계", "value_id": "fabricated"},
    }, state)
    assert not binding.ok


def test_bottleneck_normalizes_live_model_vocabulary_to_calibration():
    claim = Claim(
        "c1", "NLL optimization can increase confidence after accuracy plateaus.",
        ["p4_b14"], role="result",
    )
    state = PaperState(
        source_path=str(FIXTURE),
        claims=[claim], selected_claim_id="c1",
        doc=DocGraph(spans={
            "p1_b32": Span(
                "p1_b32", 1, "paragraph",
                "Modern neural networks are poorly calibrated and temperature scaling is effective.",
                section="abstract",
            ),
            "p4_b14": Span(
                "p4_b14", 4, "paragraph",
                "NLL and accuracy can become disconnected during training.",
                section="results",
            ),
        }),
        context_analysis={
            "bottleneck": {
                "question": "Why does NLL optimization produce overconfidence?",
                "evidence_refs": ["p4_b14"],
                "source_claim_ids": ["c1"],
                "mechanism_kind": "training_dynamics",
            }
        },
    )
    BottleneckMiner().run(state, EventBus())
    assert state.bottleneck.mechanism_kind == "calibration"
    assert state.bottleneck.candidate_controls == ["temperature"]
