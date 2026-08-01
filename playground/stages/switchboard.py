"""Assumption switchboard: the panel a claim's own conditions produce.

This used to be the `design` stage and the whole artifact. It is now one panel
primitive among others -- the one that fits when a claim's conditions, rather
than a quantitative mechanism, are the teachable thing. It is also the only
interaction available when no numbers survive parsing, so a `qualitative` run
still has something to touch.

The switches are not the model's to write: they follow from the assumptions one
for one, so they are built in code. The model only supplies judgement -- how far
each assumption carries the claim, and how to say that to a reader. The rule
table is generated here exactly once so that flipping a switch later costs no
model call (invariant 6).
"""

from __future__ import annotations

from ..clients import LLM
from ..events import EventBus
from ..state import (
    Attribution,
    Claim,
    Control,
    InteractionSpec,
    PanelSpec,
    PaperState,
    StatusRule,
)

#: verb: remove. The reader takes a condition away and watches the claim's
#: status move -- the qualitative twin of a numeric ablation, which is why it
#: shares the part_removal name instead of keeping its own.
PRIMITIVE = "part_removal"
STATUSES = ("conditional", "weak")
BASE_STATUSES = ("strong", "conditional")
MAX_SPAN_CHARS = 600
#: shown when every switch is our own reading rather than the paper's words
PEDAGOGICAL_NOTICE = "이 조건들은 원문에 명시되지 않은 교육적 판단입니다."


def build_panel(state: PaperState, bus: EventBus, llm: LLM | None,
                question: str) -> PanelSpec | None:
    """Return the switchboard panel and leave its rule table on `state.spec`.

    An empty rule table is not raised here. The critic owns that judgement --
    a switchboard with no switches is a deterministic violation, and routing it
    through the critic keeps the safe-map fallback in one place.
    """
    claim = _selected(state)
    if claim is None:
        return None

    if state.assumptions and llm is not None:
        out = llm.structured(
            role="switchboard_designer", prompt=_render(claim, state),
            schema_hint="Switchboard", bus=bus,
        )
    else:
        bus.decision("switchboard", "가정 없음 -> 빈 규칙표로 critic에 판단을 넘김",
                     claim_id=claim.id)
        out = {}

    # The model's explanation is not a second evidence channel.  Keep the
    # reader copy deterministic and explicitly distinguish source-stated
    # conditions from pedagogical checks; otherwise a fluent model can turn a
    # methods detail into an apparently established experimental fact.
    safe_explanation = {
        "novice": "각 스위치는 이 결과를 어느 범위까지 적용할 수 있는지 확인하는 질문입니다.",
        "domain_student": "원문이 직접 보고한 조건과 아직 독립적으로 검증해야 할 조건을 구분합니다.",
        "expert": "각 조건을 끄면 범위가 좁아지지만, 이 패널 자체가 새 실험이나 인과 효과를 증명하지는 않습니다.",
    }
    state.spec = InteractionSpec(
        claim_id=claim.id,
        primitive=PRIMITIVE,
        title=out.get("title") or state.source_title or claim.text[:60],
        learning_goal=out.get("learning_goal", ""),
        misconception=out.get("misconception", ""),
        controls=_switches(state, bus),
        explanation=safe_explanation,
        fidelity_warning=out.get("fidelity_warning"),
        base_status=_base_status(out.get("base_status"), bus),
        status_rules=_accept(out.get("status_rules"), state, bus),
    )
    bus.emit_status(f"가정 스위치보드 — 스위치 "
                    f"{len(state.spec.controls)}개, 규칙 "
                    f"{len(state.spec.status_rules)}개")
    return _panel(state, question)


def _panel(state: PaperState, question: str) -> PanelSpec:
    """Wrap the rule table as a declarative panel the frontend can evaluate."""
    spec = state.spec
    assert spec is not None
    grounded = list(dict.fromkeys(
        a.span_id for a in state.assumptions if a.span_id))
    return PanelSpec(
        primitive=PRIMITIVE,
        question=question,
        model={
            "type": "part_removal",
            "metric": "status",
            "base_status": spec.base_status,
            "rules": [
                {**rule.__dict__, "attribution": rule.attribution.__dict__}
                for rule in spec.status_rules
            ],
            "assumptions": [a.__dict__.copy() for a in state.assumptions],
        },
        controls=[
            {"name": c.name, "kind": c.kind, "provenance": c.provenance,
             "span_id": c.span_id, "default": True}
            for c in spec.controls
        ],
        observables=[{"name": "claim_status", "label": "주장의 상태"}],
        feedback={
            "default": "스위치를 끄면 이 주장이 어디까지 좁아지는지 보여줍니다.",
        },
        provenance=[{
            "kind": "assumption",
            "provenance": "source_stated" if grounded else "illustrative",
            "precision": "qualitative",
            "source_refs": grounded,
        }],
        notice=None if grounded else PEDAGOGICAL_NOTICE,
    )


# -- selection --

def _selected(state: PaperState) -> Claim | None:
    """SelectFrontier is the sole owner of claim choice."""
    if not state.selected_claim_id:
        return None
    return next(
        (c for c in state.claims if c.id == state.selected_claim_id), None
    )


# -- prompt --

def _render(claim: Claim, state: PaperState) -> str:
    lines = ["# claim path", " -> ".join(state.critical_path_ids),
             "", "# claim", f"{claim.id} {claim.text}", "",
             "# assumptions"]
    for a in state.assumptions:
        lines.append(f"{a.id} [{a.kind}/{a.source}] span={a.span_id}")
        lines.append(f"  text: {a.text}")
        lines.append(f"  weakens_how: {a.weakens_how}")

    lines += ["", "# evidence spans"]
    cited = {a.span_id for a in state.assumptions if a.span_id}
    cited.update(claim.evidence_span_ids)
    for sid in sorted(cited):
        sp = state.doc.spans.get(sid)
        if sp is None:
            continue
        text = sp.text
        if len(text) > MAX_SPAN_CHARS:
            text = text[:MAX_SPAN_CHARS] + "…"
        lines.append(f"{sid} [{sp.kind}] {text}")

    lines += ["", f"# reader level: {state.profile.level}"]
    return "\n".join(lines)


# -- switches: derived, not generated --

def _switches(state: PaperState, bus: EventBus) -> list[Control]:
    """One toggle per assumption, on by default. The reader starts from the
    paper's own position and takes it apart from there."""
    controls = [
        Control(
            name=a.id,
            kind="toggle",
            provenance=("pedagogical_simplification"
                        if a.source == "pedagogical" else "assumption"),
            span_id=a.span_id,
            default=True,
        )
        for a in state.assumptions
    ]
    bus.decision("switchboard", f"가정 {len(controls)}개 -> 토글 {len(controls)}개 "
                                f"(모델이 아니라 코드가 생성)",
                 switches=[c.name for c in controls])
    return controls


def _base_status(raw, bus: EventBus) -> str:
    if raw in BASE_STATUSES:
        return raw
    if raw is not None:
        bus.decision("switchboard", f"base_status '{raw}' 사용 불가 -> strong",
                     base_status=raw)
    return "strong"


# -- rule table --

def _accept(raw, state: PaperState, bus: EventBus) -> list[StatusRule]:
    by_id = {a.id: a for a in state.assumptions}
    # External retrieval is evidence-listing only. It must not turn into an
    # automatic status or controversy judgement inside the switchboard.
    evidence_ids: set[str] = set()
    rules: dict[str, StatusRule] = {}

    for i, r in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            bus.decision("switchboard", f"규칙 #{i}: 객체가 아님 -> 폐기")
            continue
        aid = str(r.get("assumption_id") or "").strip()
        if aid not in by_id:
            bus.decision("switchboard", f"규칙 #{i}: 없는 가정 '{aid}' -> 폐기",
                         assumption_id=aid)
            continue
        if aid in rules:
            # the code-side half of "no combination explosion": a second
            # rule on one assumption is how a condition language sneaks in
            bus.decision("switchboard", f"{aid}: 가정당 규칙은 하나 -> 중복 폐기",
                         assumption_id=aid)
            continue

        status = r.get("status")
        if status not in STATUSES:
            bus.decision("switchboard", f"{aid}: status '{status}' 사용 불가 "
                                        f"-> 폐기 (broken 판정은 존재하지 않음)",
                         assumption_id=aid, status=status)
            continue

        assumption = by_id[aid]
        support_type = (
            "necessary" if r.get("support_type") == "necessary"
            else assumption.support_type
        )
        if support_type == "independent" and status == "weak":
            bus.decision(
                "switchboard", f"{aid}: independent 조건의 weak 판정 -> conditional로 제한",
                assumption_id=aid,
            )
            status = "conditional"

        because = str(r.get("because") or "").strip()
        if not because:
            bus.decision("switchboard", f"{aid}: because 없음 -> 폐기",
                         assumption_id=aid)
            continue

        rules[aid] = StatusRule(
            assumption_id=aid, status=status, because=because,
            attribution=_attribution(
                r.get("attribution"), aid, state, evidence_ids, bus),
            support_type=support_type,
        )

    rules.update(_fill_gaps(by_id, rules, bus))
    ordered = [rules[a.id] for a in state.assumptions if a.id in rules]
    bus.decision("switchboard", f"status 규칙 {len(ordered)}개 확정 "
                                f"(가정 {len(by_id)}개)",
                 rules={r.assumption_id: r.status for r in ordered})
    return ordered


def _attribution(raw, aid: str, state: PaperState,
                 evidence_ids: set[str], bus: EventBus) -> Attribution:
    """Invariant 7. An id that does not resolve is not a discard -- it
    demotes to pedagogical, and the interface then says the reasoning is
    ours rather than the paper's."""
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("kind")
    span_id, evidence_id = raw.get("span_id"), raw.get("evidence_id")

    if kind == "paper":
        if (span_id in state.doc.spans
                and state.doc.spans[span_id].origin == "paper"):
            return Attribution(kind="paper", span_id=span_id)
        bus.decision("switchboard", f"{aid}: 원문에 없는 span '{span_id}' "
                                    f"-> pedagogical 강등",
                     assumption_id=aid, span_id=span_id)
    elif kind == "external":
        if evidence_id in evidence_ids:
            return Attribution(kind="external", evidence_id=evidence_id)
        bus.decision("switchboard", f"{aid}: 확인되지 않는 evidence "
                                    f"'{evidence_id}' -> pedagogical 강등",
                     assumption_id=aid, evidence_id=evidence_id)
    elif kind != "pedagogical":
        # no attribution at all: fall back to the assumption's own span
        # before giving up on grounding
        own = state.assumptions and next(
            (a.span_id for a in state.assumptions if a.id == aid), None)
        if own:
            bus.decision("switchboard", f"{aid}: attribution 없음 -> 가정의 "
                                        f"span '{own}' 사용",
                         assumption_id=aid, span_id=own)
            return Attribution(kind="paper", span_id=own)
    return Attribution(kind="pedagogical")


def _fill_gaps(by_id: dict, rules: dict,
               bus: EventBus) -> dict[str, StatusRule]:
    """An assumption with no rule is a switch that does nothing when
    pressed -- exactly the dead control the weakens_how filter exists to
    prevent. Synthesising from weakens_how is not invention: that sentence
    already passed the miner's checks, and conditional is the milder of the
    two values."""
    out: dict[str, StatusRule] = {}
    for aid, a in by_id.items():
        if aid in rules:
            continue
        bus.decision("switchboard", f"{aid}: 규칙 누락 -> weakens_how로 "
                                    f"conditional 규칙 보충",
                     assumption_id=aid)
        out[aid] = StatusRule(
            assumption_id=aid, status="conditional", because=a.weakens_how,
            attribution=(Attribution(kind="paper", span_id=a.span_id)
                         if a.span_id else Attribution(kind="pedagogical")),
            support_type=a.support_type,
        )
    return out
