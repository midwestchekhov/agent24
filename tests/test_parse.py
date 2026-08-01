from pathlib import Path

from playground.events import EventBus
from playground.stages import Parse
from playground.state import PaperState

FIXTURE = str(Path(__file__).resolve().parents[1] / "fixtures" / "sample.pdf")


def _parse() -> PaperState:
    st = PaperState(source_path=FIXTURE)
    Parse().run(st, EventBus())
    return st


def test_span_ids_are_stable_across_runs():
    a, b = _parse(), _parse()
    assert list(a.doc.spans) == list(b.doc.spans)
    assert ({k: (v.page, v.kind, v.text) for k, v in a.doc.spans.items()}
            == {k: (v.page, v.kind, v.text) for k, v in b.doc.spans.items()})


def test_number_pool_is_populated_and_grounded():
    st = _parse()
    assert st.number_pool
    # invariant 2: every number terminates in a span that actually exists
    assert all(n.span_id in st.doc.spans for n in st.number_pool.values())
