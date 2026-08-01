from playground.critic_rules import precheck
from playground.state import (Control, InteractionSpec, NumberFact,
                              PaperState, SpecNumber)


def _state():
    st = PaperState(source_path="x")
    st.number_pool["n1"] = NumberFact("n1", 0.87, "0.87", "p1_abs", "AUC")
    return st


def test_fabricated_number_is_fatal():
    spec = InteractionSpec("c1", "threshold_explorer", "t", "", "",
                           numbers=[SpecNumber(0.93, "measured", "n_missing")])
    v = list(precheck(spec, _state()))
    assert any(x.code == "UNGROUNDED_NUMBER" and x.fatal for x in v)


def test_grounded_number_passes():
    spec = InteractionSpec("c1", "threshold_explorer", "t", "", "",
                           numbers=[SpecNumber(0.87, "measured", "n1")])
    assert not [x for x in precheck(spec, _state()) if x.fatal]


def test_variable_control_needs_span():
    spec = InteractionSpec("c1", "threshold_explorer", "t", "", "",
                           controls=[Control("thr", "slider", "variable",
                                             min=0, max=1)])
    v = list(precheck(spec, _state()))
    assert any(x.code == "MALFORMED_CONTROL" for x in v)


def test_extrapolation_is_warning_not_fatal():
    spec = InteractionSpec("c1", "threshold_explorer", "t", "", "",
                           controls=[Control("thr", "slider", "variable",
                                             span_id="p1_abs", min=0.0, max=5.0)])
    v = [x for x in precheck(spec, _state()) if x.code == "EXTRAPOLATION_UNMARKED"]
    assert v and not v[0].fatal
