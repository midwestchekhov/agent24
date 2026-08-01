from playground.events import EventBus
from playground.state import (
    BottleneckSpec,
    Claim,
    DocGraph,
    EvidenceChunk,
    EvidenceLedger,
    EvidenceObligation,
    EvidenceRecord,
    PaperState,
    Span,
)
from playground.stages import EvidenceController
from playground.stages.explainer import PanelComposer


class _ChunklessLLM:
    def structured(self, *, role, **kwargs):
        if role == "evidence_planner":
            return {"actions": [{
                "id": "q1", "obligation_ids": ["ob1"],
                "query": "neural calibration validation",
                "rationale": "direct evidence",
            }]}
        return {
            "assessments": [{
                "source_url": "https://example.test/paper",
                "obligation_ids": ["ob1"], "relation": "supports",
                "confidence": 0.99, "rationale": "title looks relevant",
                "chunk_nums": [],
            }],
            "sufficient": True,
            "missing_obligation_ids": [],
            "next_focus": "",
        }


class _ChunklessSearch:
    def search(self, *, query, bus):
        return {
            "answer": "A generated answer is not evidence.",
            "references": [{
                "title": "A relevant-looking paper",
                "url": "https://example.test/paper", "snippet": "",
            }],
            "reference_chunks": [],
        }


def test_chunkless_relation_is_downgraded_and_duplicate_query_stops_loop():
    state = PaperState(
        claims=[Claim("c1", "Temperature scaling improves calibration", ["p1"])],
        selected_claim_id="c1", critical_path_ids=["c1"],
        context_analysis={"search_obligations": [{
            "id": "ob1", "question": "Is the result independently supported?",
            "claim_ids": ["c1"], "kind": "support", "required": True,
        }]},
    )
    bus = EventBus()
    EvidenceController(_ChunklessLLM(), _ChunklessSearch()).run(state, bus)

    assert state.evidence_ledger.status == "partial"
    assert state.evidence_ledger.stop_reason == "no_novel_search_action"
    assert len(state.evidence_ledger.rounds) == 1
    assert state.evidence_ledger.records[0].relation == "unresolved"
    assert state.evidence_ledger.records[0].confidence == 0.0


def test_query_without_claim_anchor_is_repaired_before_search():
    bus = EventBus()
    repaired = EvidenceController._repair_query(
        "independent evidence same conditions",
        ["temperature", "scaling", "calibration", "0.20", "0.05"],
        bus,
        round_index=1,
    )
    assert "temperature" in repaired
    assert "0.20" in repaired
    assert any(
        event.type == "decision" and event.payload.get("actor") == "evidence"
        for event in bus.log
    )


class _PanelLLM:
    def __init__(self):
        self.prompt = ""

    def structured(self, *, role, prompt, **kwargs):
        self.prompt = prompt
        return {"panels": [{
            "primitive": "flow_topology",
            "question": "정확도와 calibration은 같은 연결일까?",
            "slots": {
                "nodes": [{"id": "a", "label": "accuracy"},
                          {"id": "c", "label": "calibration"}],
                "variants": [
                    {"label": "same", "edges": [["a", "c"]]},
                    {"label": "separate", "edges": []},
                ],
            },
            "evidence_ids": ["ev1"],
        }]}


def test_panel_composer_receives_ledger_and_binds_external_provenance():
    llm = _PanelLLM()
    state = PaperState(
        source_title="Calibration paper",
        doc=DocGraph(spans={
            "p1": Span("p1", 1, "paragraph", "Accuracy and calibration differ.",
                       section="abstract"),
        }),
        claims=[Claim("c1", "Accuracy can improve while calibration worsens", ["p1"])],
        selected_claim_id="c1", critical_path_ids=["c1"],
        bottleneck=BottleneckSpec(
            question="왜 둘이 갈라질까?", why_hard="서로 다른 측정이다.",
            source_claim_ids=["c1"], evidence_refs=["p1"],
        ),
        evidence_ledger=EvidenceLedger(
            obligations=[EvidenceObligation(
                "ob1", "Is this independently observed?", ["c1"], "support", True
            )],
            records=[EvidenceRecord(
                "ev1", ["ob1"], "calibration evidence", "External paper",
                "https://example.test/evidence",
                chunks=[EvidenceChunk(
                    "ev1_ch1", "Independent results report the same separation.",
                    "https://example.test/evidence", "External paper", 1,
                )],
                relation="supports", confidence=0.9,
            )],
            status="sufficient", stop_reason="required_obligations_satisfied",
        ),
    )
    PanelComposer(llm).run(state, EventBus())

    assert "ev1 [supports confidence=0.90]" in llm.prompt
    assert "Independent results report the same separation" in llm.prompt
    assert state.explainer is not None
    assert state.explainer.panels[0].provenance[0]["evidence_refs"] == ["ev1"]
