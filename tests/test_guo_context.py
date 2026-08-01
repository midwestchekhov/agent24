from pathlib import Path

from playground.events import EventBus
from playground.payload import build_payload
from playground.pipeline import Pipeline
from playground.state import BottleneckSpec, Claim, DocGraph, PaperState, Span
from playground.clients import MockLLM
from playground.stages import (
    AssumptionMiner, BottleneckMiner, ContextAnalyst, Parse, PrimitiveRouter,
    VerifyExternal,
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
    assert state.explainer_route == "calibration_explainer"
    assert state.artifact is not None
    assert "claim_graph" not in state.artifact
    assert {panel["primitive"] for panel in state.artifact["panels"]} == {
        "generated_schematic", "scaling_comparison"
    }

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


def test_calibration_queries_are_distinct_and_drop_resnet_accuracy_junk():
    class Planner:
        def structured(self, **kwargs):
            return {"queries": {facet: "ResNet CIFAR values" for facet in (
                "support", "contradict", "boundary", "methodology"
            )}}

    class Search:
        def query(self, *, q, bus):
            if "independent validation" in q:
                useful = {"title": "Independent validation of temperature scaling for calibration", "url": "https://e/s", "snippet": "Empirical calibration study"}
            elif "failure limitations" in q:
                useful = {"title": "Temperature scaling calibration failure and limitations", "url": "https://e/c", "snippet": "The method can fail under shift"}
            elif "distribution shift" in q:
                useful = {"title": "Neural network calibration under dataset distribution shift", "url": "https://e/b", "snippet": "Domain generalization boundary"}
            else:
                useful = {"title": "Expected calibration error binning bias", "url": "https://e/m", "snippet": "ECE estimator measurement bias methodology"}
            return [
                useful,
                {"title": "ResNet-50 accuracy on CIFAR-10", "url": "https://e/junk", "snippet": "Image classification pruning"},
            ]

    claims = [Claim(
        "c1",
        "Modern neural networks can improve accuracy while calibration worsens, and temperature scaling corrects confidence.",
        ["p1"], role="result",
    )]
    stage = VerifyExternal(Planner(), Search())
    bus = EventBus()
    queries = stage._queries(claims, bus)
    assert len(set(queries.values())) == 4
    assert "expected calibration error" in queries["methodology"]

    state = PaperState(claims=claims, selected_claim_id="c1", critical_path_ids=["c1"])
    stage.run(state, bus)
    evidence = state.external["c1"]
    assert {item.url for item in evidence} == {
        "https://e/s", "https://e/c", "https://e/b", "https://e/m",
    }
    assert all(item.url != "https://e/junk" for item in evidence)


def test_router_model_cannot_demote_supported_explainer_to_switchboard():
    class DowngradeRouter:
        def structured(self, **kwargs):
            return {"route": "assumption_switchboard"}

    state = PaperState(
        source_path=str(FIXTURE),
        bottleneck=BottleneckSpec(
            question="Why can accuracy and calibration diverge?",
            why_hard="They are different observables.",
            mechanism_kind="calibration",
        ),
    )
    PrimitiveRouter(DowngradeRouter()).run(state, EventBus())
    assert state.explainer_route == "calibration_explainer"


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
