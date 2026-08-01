"""Legacy switchboard design: assumption toggles and claim status rules."""

from __future__ import annotations

from ..clients import LLM
from ..events import EventBus
from ..state import (
    Attribution,
    Claim,
    Control,
    InteractionSpec,
    PaperState,
    StatusRule,
)
from .base import (
    Stage,
    StageError,
)


class DesignInteraction(Stage):
    """LLM. Emits a schema, never HTML. Free-form code generation is the single
    biggest live-demo risk.

    Narrowed to one primitive: the assumption switchboard. The output that
    matters is the status rule table, generated here exactly once so that
    flipping a switch later costs no model call (invariant 6).

    The switches themselves are not the model's to write -- they follow from
    the assumptions one for one, so they are built in code. The model only
    supplies judgement: how far each assumption carries the claim, and how to
    say that to a reader.
    """

    name = "design"
    reads = ("claims", "assumptions", "scores", "profile", "mode",
             "selected_claim_id", "root_claim_id", "critical_path_ids",
             "claim_analyses", "path_unsafe")
    writes = ("spec",)
    budget_s = 6.0

    PRIMITIVE = "assumption_switchboard"
    STATUSES = ("conditional", "weak")
    BASE_STATUSES = ("strong", "conditional")
    MAX_SPAN_CHARS = 600

    def __init__(self, llm: LLM, primitives: dict):
        self.llm = llm
        self.primitives = primitives

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.explainer is not None:
            bus.decision("designer", "설명 패널이 이미 구성되어 legacy switchboard 설계 생략")
            return
        if self.PRIMITIVE not in self.primitives:
            raise StageError(f"'{self.PRIMITIVE}' is not registered in this "
                             f"domain pack: {list(self.primitives)}")
        claim = self._selected(state)
        if claim is None:
            raise StageError("nothing to design")
        if not state.assumptions and not state.path_unsafe:
            raise StageError("a switchboard with no switches")

        if state.assumptions:
            out = self.llm.structured(
                role="switchboard_designer", prompt=self._render(claim, state),
                schema_hint="Switchboard", bus=bus,
            )
        else:
            bus.decision("designer", "path unsafe -> 빈 switchboard spec으로 safe map 준비",
                         claim_id=claim.id)
            out = {}

        state.spec = InteractionSpec(
            claim_id=claim.id,
            primitive=self.PRIMITIVE,
            title=out.get("title") or state.source_title or claim.text[:60],
            learning_goal=out.get("learning_goal", ""),
            misconception=out.get("misconception", ""),
            controls=self._switches(state, bus),
            explanation=out.get("explanation", {}),
            fidelity_warning=out.get("fidelity_warning"),
            base_status=self._base_status(out.get("base_status"), bus),
            status_rules=self._accept(out.get("status_rules"), state, bus),
        )
        bus.emit_status(f"스위치보드 설계 완료 — 스위치 "
                        f"{len(state.spec.controls)}개, 규칙 "
                        f"{len(state.spec.status_rules)}개")

    # -- selection --

    def _selected(self, state: PaperState) -> Claim | None:
        """SelectClaim is the sole owner of claim choice."""
        if not state.selected_claim_id:
            return None
        return next(
            (c for c in state.claims if c.id == state.selected_claim_id), None
        )

    # -- prompt --

    def _render(self, claim: Claim, state: PaperState) -> str:
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
            if len(text) > self.MAX_SPAN_CHARS:
                text = text[:self.MAX_SPAN_CHARS] + "…"
            lines.append(f"{sid} [{sp.kind}] {text}")

        lines += ["", f"# reader level: {state.profile.level}"]
        return "\n".join(lines)

    # -- switches: derived, not generated --

    def _switches(self, state: PaperState, bus: EventBus) -> list[Control]:
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
        bus.decision("designer", f"가정 {len(controls)}개 -> 토글 {len(controls)}개 "
                                 f"(모델이 아니라 코드가 생성)",
                     switches=[c.name for c in controls])
        return controls

    def _base_status(self, raw, bus: EventBus) -> str:
        if raw in self.BASE_STATUSES:
            return raw
        if raw is not None:
            bus.decision("designer", f"base_status '{raw}' 사용 불가 -> strong",
                         base_status=raw)
        return "strong"

    # -- rule table --

    def _accept(self, raw, state: PaperState, bus: EventBus) -> list[StatusRule]:
        by_id = {a.id: a for a in state.assumptions}
        # External retrieval is evidence-listing only. It must not turn into an
        # automatic status or controversy judgement inside the switchboard.
        evidence_ids: set[str] = set()
        rules: dict[str, StatusRule] = {}

        for i, r in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(r, dict):
                bus.decision("designer", f"규칙 #{i}: 객체가 아님 -> 폐기")
                continue
            aid = str(r.get("assumption_id") or "").strip()
            if aid not in by_id:
                bus.decision("designer", f"규칙 #{i}: 없는 가정 '{aid}' -> 폐기",
                             assumption_id=aid)
                continue
            if aid in rules:
                # the code-side half of "no combination explosion": a second
                # rule on one assumption is how a condition language sneaks in
                bus.decision("designer", f"{aid}: 가정당 규칙은 하나 -> 중복 폐기",
                             assumption_id=aid)
                continue

            status = r.get("status")
            if status not in self.STATUSES:
                bus.decision("designer", f"{aid}: status '{status}' 사용 불가 "
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
                    "designer", f"{aid}: independent 조건의 weak 판정 -> conditional로 제한",
                    assumption_id=aid,
                )
                status = "conditional"

            because = str(r.get("because") or "").strip()
            if not because:
                bus.decision("designer", f"{aid}: because 없음 -> 폐기",
                             assumption_id=aid)
                continue

            rules[aid] = StatusRule(
                assumption_id=aid, status=status, because=because,
                attribution=self._attribution(
                    r.get("attribution"), aid, state, evidence_ids, bus),
                support_type=support_type,
            )

        rules.update(self._fill_gaps(by_id, rules, bus))
        ordered = [rules[a.id] for a in state.assumptions if a.id in rules]
        bus.decision("designer", f"status 규칙 {len(ordered)}개 확정 "
                                 f"(가정 {len(by_id)}개)",
                     rules={r.assumption_id: r.status for r in ordered})
        return ordered

    def _attribution(self, raw, aid: str, state: PaperState,
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
            bus.decision("designer", f"{aid}: 원문에 없는 span '{span_id}' "
                                     f"-> pedagogical 강등",
                         assumption_id=aid, span_id=span_id)
        elif kind == "external":
            if evidence_id in evidence_ids:
                return Attribution(kind="external", evidence_id=evidence_id)
            bus.decision("designer", f"{aid}: 확인되지 않는 evidence "
                                     f"'{evidence_id}' -> pedagogical 강등",
                         assumption_id=aid, evidence_id=evidence_id)
        elif kind != "pedagogical":
            # no attribution at all: fall back to the assumption's own span
            # before giving up on grounding
            own = state.assumptions and next(
                (a.span_id for a in state.assumptions if a.id == aid), None)
            if own:
                bus.decision("designer", f"{aid}: attribution 없음 -> 가정의 "
                                         f"span '{own}' 사용",
                             assumption_id=aid, span_id=own)
                return Attribution(kind="paper", span_id=own)
        return Attribution(kind="pedagogical")

    def _fill_gaps(self, by_id: dict, rules: dict,
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
            bus.decision("designer", f"{aid}: 규칙 누락 -> weakens_how로 "
                                     f"conditional 규칙 보충",
                         assumption_id=aid)
            out[aid] = StatusRule(
                assumption_id=aid, status="conditional", because=a.weakens_how,
                attribution=(Attribution(kind="paper", span_id=a.span_id)
                             if a.span_id else Attribution(kind="pedagogical")),
                support_type=a.support_type,
            )
        return out
