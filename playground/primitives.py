"""Panel primitives: the whole vocabulary of things a reader can touch.

A primitive is named after **what the reader does and which misconception that
breaks**, never after what the picture looks like. `scaling_comparison` cannot
be decided from a paper -- there is no test for "is this a scaling comparison".
`rate_compare` can: does the source give one sweepable variable and two
quantities that grow differently in it? That question has an answer, and the
answer is what routes a paper to a panel.

Four verbs, because that is all a reader ever does:

    sweep   move one continuous parameter
    remove  take a discrete part away
    swap    exchange one structure for another
    sample  instantiate a stochastic event

Each primitive declares the slots that must be filled before it may be built.
The model chooses which source ids fill which slot and writes the copy; it
never invents the model. `bind` then resolves every id against the number pool,
the span index and the mined assumptions -- an id that does not resolve costs
the panel its source support, not the reader's trust.

Adding a primitive means adding an entry here and a renderer. It must never
mean touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .state import NumberFact, PaperState

Verb = Literal["sweep", "remove", "swap", "sample"]
Fidelity = Literal["measured", "derived", "illustrative"]

#: expressions a panel model may contain. The browser evaluates these, so the
#: list is a security boundary as much as a fidelity one -- see formula.py.
ALLOWED_OPS = ["+", "-", "*", "/", "pow", "min", "max", "log", "exp", "softmax"]


@dataclass
class Binding:
    """The outcome of resolving one panel's slots against the source."""

    slots: dict[str, Any] = field(default_factory=dict)
    refs: list[str] = field(default_factory=list)
    fidelity: Fidelity = "illustrative"
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Primitive:
    name: str
    verb: Verb
    #: the belief a reader holds until they touch this panel
    misconception: str
    #: what the source must already contain for this panel to be honest
    needs: str
    model_type: str
    binder: Callable[[dict, PaperState], Binding]

    def prompt_line(self) -> str:
        return (f"- `{self.name}` ({self.verb}) — 깨는 오해: {self.misconception}\n"
                f"  필요한 자료: {self.needs}")


# -- shared resolution helpers -------------------------------------------------

def _number(state: PaperState, raw: Any) -> tuple[NumberFact | None, str | None]:
    """Resolve a number-pool id. Returns (fact, problem)."""
    key = str(raw or "").strip()
    if not key:
        return None, "number id is empty"
    fact = state.number_pool.get(key)
    if fact is None:
        return None, f"number '{key}' is not in the number pool"
    return fact, None


def _spans(state: PaperState, raw: Any) -> list[str]:
    return [str(sid) for sid in (raw or []) if str(sid) in state.doc.spans]


def _text(raw: Any, fallback: str = "") -> str:
    value = str(raw or "").strip()
    return value or fallback


def _expression(raw: Any) -> tuple[str, str | None]:
    expr = _text(raw)
    if not expr:
        return "", "expression is empty"
    banned = [tok for tok in ("import", "__", "lambda", "eval", "exec")
              if tok in expr]
    if banned:
        return "", f"expression contains {banned[0]}"
    return expr, None


def _axis(state: PaperState, raw: Any, binding: Binding) -> dict | None:
    """A sweepable variable. Its range is what keeps the slider honest: outside
    the values the paper covers, the reader is extrapolating."""
    raw = raw if isinstance(raw, dict) else {}
    low, low_problem = _number(state, raw.get("min_id"))
    high, high_problem = _number(state, raw.get("max_id"))
    if low is None or high is None:
        # A stated range is preferred but not mandatory: an unbound axis is
        # allowed as long as the panel stops claiming the paper measured it.
        binding.fidelity = "illustrative"
        try:
            lo, hi = float(raw.get("min", 0)), float(raw.get("max", 1))
        except (TypeError, ValueError):
            binding.problems.append(low_problem or high_problem or "axis has no range")
            return None
        binding.refs.extend(_spans(state, raw.get("refs")))
        return {"label": _text(raw.get("label"), "x"), "min": lo, "max": hi,
                "unit": _text(raw.get("unit")) or None, "span_id": None}
    binding.refs.extend([low.span_id, high.span_id])
    return {"label": _text(raw.get("label"), "x"),
            "min": min(low.value, high.value), "max": max(low.value, high.value),
            "unit": low.unit or high.unit, "span_id": low.span_id}


def _curve(state: PaperState, raw: Any, binding: Binding) -> dict | None:
    raw = raw if isinstance(raw, dict) else {}
    expr, problem = _expression(raw.get("expression"))
    if problem:
        binding.problems.append(f"curve '{_text(raw.get('label'), '?')}': {problem}")
        return None
    refs = _spans(state, raw.get("refs"))
    binding.refs.extend(refs)
    if not refs:
        binding.fidelity = "illustrative"
    return {"label": _text(raw.get("label"), "series"), "expression": expr,
            "refs": refs}


# -- per-primitive binders -----------------------------------------------------

def _bind_rate_compare(raw: dict, state: PaperState) -> Binding:
    b = Binding(fidelity="derived")
    axis = _axis(state, raw.get("x"), b)
    series = [_curve(state, item, b) for item in (raw.get("series") or [])]
    series = [s for s in series if s]
    if axis is None:
        b.problems.append("rate_compare needs a sweepable axis")
    if len(series) < 2:
        b.problems.append("rate_compare needs at least two series to compare")
    b.slots = {"type": "rate_compare", "x": axis, "series": series[:3],
               "allowed_ops": ALLOWED_OPS}
    return b


def _bind_threshold_finder(raw: dict, state: PaperState) -> Binding:
    b = Binding(fidelity="derived")
    axis = _axis(state, raw.get("x"), b)
    curve = _curve(state, raw.get("curve"), b)
    boundary = raw.get("boundary") if isinstance(raw.get("boundary"), dict) else {}
    fact, problem = _number(state, boundary.get("value_id"))
    if axis is None:
        b.problems.append("threshold_finder needs a sweepable axis")
    if curve is None:
        b.problems.append("threshold_finder needs a curve")
    if fact is None:
        # The boundary is the whole point: an invented limit teaches a limit
        # the paper never claimed.
        b.problems.append(f"threshold_finder boundary: {problem}")
        bound = None
    else:
        b.refs.append(fact.span_id)
        bound = {"label": _text(boundary.get("label"), "한계"),
                 "value": fact.value, "unit": fact.unit, "span_id": fact.span_id}
    b.slots = {"type": "threshold_finder", "x": axis, "curve": curve,
               "boundary": bound, "allowed_ops": ALLOWED_OPS}
    return b


def _bind_part_removal(raw: dict, state: PaperState) -> Binding:
    """Numeric mode is an ablation table; status mode is the assumption
    switchboard. Same shape -- take a part away, watch the metric move -- so
    they are one primitive with two metric kinds, not two primitives."""
    metric = raw.get("metric") if raw.get("metric") in {"numeric", "status"} else "numeric"
    b = Binding(fidelity="measured" if metric == "numeric" else "illustrative")
    parts = []
    if metric == "numeric":
        for item in (raw.get("parts") or []):
            if not isinstance(item, dict):
                continue
            fact, problem = _number(state, item.get("delta_id"))
            if fact is None:
                b.problems.append(f"part '{_text(item.get('label'), '?')}': {problem}")
                continue
            b.refs.append(fact.span_id)
            parts.append({"id": _text(item.get("id"), f"p{len(parts)}"),
                          "label": _text(item.get("label"), "component"),
                          "delta": fact.value, "unit": fact.unit,
                          "span_id": fact.span_id,
                          "because": _text(item.get("because"))})
        baseline, problem = _number(state, (raw.get("baseline") or {}).get("value_id"))
        if baseline is None:
            b.problems.append(f"part_removal baseline: {problem}")
        else:
            b.refs.append(baseline.span_id)
        b.slots = {
            "type": "part_removal", "metric": "numeric",
            "baseline": None if baseline is None else {
                "label": _text((raw.get("baseline") or {}).get("label"), "기준값"),
                "value": baseline.value, "unit": baseline.unit,
                "span_id": baseline.span_id},
            "parts": parts,
        }
        if not parts:
            b.problems.append("part_removal needs at least one source-bound part")
    else:
        b.slots = {"type": "part_removal", "metric": "status"}
    return b


def _bind_flow_topology(raw: dict, state: PaperState) -> Binding:
    b = Binding(fidelity="illustrative")
    nodes = [{"id": _text(n.get("id"), f"n{i}"), "label": _text(n.get("label"), "?")}
             for i, n in enumerate(raw.get("nodes") or []) if isinstance(n, dict)]
    known = {n["id"] for n in nodes}
    variants = []
    for item in (raw.get("variants") or []):
        if not isinstance(item, dict):
            continue
        edges = [[str(a), str(b_)] for a, b_ in
                 (pair for pair in (item.get("edges") or []) if len(pair) == 2)
                 if str(a) in known and str(b_) in known]
        refs = _spans(state, item.get("refs"))
        b.refs.extend(refs)
        variants.append({"label": _text(item.get("label"), f"variant {len(variants)}"),
                         "edges": edges, "refs": refs})
    if len(nodes) < 2:
        b.problems.append("flow_topology needs at least two nodes")
    if len(variants) < 2:
        # One wiring is a diagram, not an interaction. The reader has to be
        # able to swap something.
        b.problems.append("flow_topology needs two structures to swap between")
    if any(v["refs"] for v in variants):
        b.fidelity = "derived"
    b.slots = {"type": "flow_topology", "nodes": nodes, "variants": variants[:3]}
    return b


def _bind_proportion_reveal(raw: dict, state: PaperState) -> Binding:
    b = Binding(fidelity="measured")
    total, total_problem = _number(state, (raw.get("total") or {}).get("value_id"))
    active, active_problem = _number(state, (raw.get("active") or {}).get("value_id"))
    if total is None or active is None:
        b.problems.append(
            f"proportion_reveal: {total_problem or active_problem}")
        b.slots = {"type": "proportion_reveal"}
        return b
    if active.value > total.value:
        b.problems.append("proportion_reveal active count exceeds the total")
    b.refs.extend([total.span_id, active.span_id])
    b.slots = {
        "type": "proportion_reveal",
        "total": {"label": _text((raw.get("total") or {}).get("label"), "전체"),
                  "value": total.value, "span_id": total.span_id},
        "active": {"label": _text((raw.get("active") or {}).get("label"), "활성"),
                   "value": active.value, "span_id": active.span_id},
    }
    return b


PRIMITIVES: dict[str, Primitive] = {
    p.name: p for p in (
        Primitive(
            name="rate_compare", verb="sweep",
            misconception="입력이 2배면 비용도 2배일 것이다",
            needs="쓸어볼 변수 하나와, 그 변수에서 서로 다르게 자라는 양 2~3개 "
                  "(수식 span 또는 표)",
            model_type="rate_compare", binder=_bind_rate_compare,
        ),
        Primitive(
            name="threshold_finder", verb="sweep",
            misconception="한계에 가까워지면 완만하게 나빠질 것이다",
            needs="쓸어볼 변수 하나, 곡선 하나, 그리고 원문이 명시한 경계값",
            model_type="threshold_finder", binder=_bind_threshold_finder,
        ),
        Primitive(
            name="part_removal", verb="remove",
            misconception="헤드라인 숫자는 헤드라인 아이디어가 만들었다",
            needs="numeric: component별 delta가 적힌 표. "
                  "status: 채굴된 가정 (항상 가능한 바닥)",
            model_type="part_removal", binder=_bind_part_removal,
        ),
        Primitive(
            name="flow_topology", verb="swap",
            misconception="정보는 구조와 무관하게 그냥 흐른다",
            needs="개체 2개 이상과, 서로 바꿔 끼울 수 있는 배선 2가지 이상",
            model_type="flow_topology", binder=_bind_flow_topology,
        ),
        Primitive(
            name="proportion_reveal", verb="sample",
            misconception="전체가 크면 한 번 쓰는 비용도 크다",
            needs="전체 수와 실제로 활성화되는 수, 둘 다 number pool에 있을 것",
            model_type="proportion_reveal", binder=_bind_proportion_reveal,
        ),
    )
}

#: the panel that is always available. Assumptions are mined on every route, so
#: this is what a run degrades to rather than refusing.
FLOOR = "part_removal"


def bind(primitive: str, raw: dict, state: PaperState) -> Binding:
    spec = PRIMITIVES.get(primitive)
    if spec is None:
        return Binding(problems=[f"unknown panel primitive '{primitive}'"])
    binding = spec.binder(raw if isinstance(raw, dict) else {}, state)
    binding.refs = list(dict.fromkeys(r for r in binding.refs if r in state.doc.spans))
    return binding


def catalogue() -> str:
    """The primitive menu, rendered for the composer prompt."""
    return "\n".join(p.prompt_line() for p in PRIMITIVES.values())
