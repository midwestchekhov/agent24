"""Root-to-frontier path analysis: the conditions each claim rests on."""

from __future__ import annotations

from ..clients import LLM
from ..events import EventBus
from ..state import (
    Assumption,
    Claim,
    ClaimAnalysis,
    PaperState,
)
from .base import (
    Stage,
    StageError,
)


class AssumptionMiner(Stage):
    """LLM. Takes the one claim the user picked apart into the conditions it
    rests on -- the switches the reader gets to flip.

    The prompt is prompts/assumption_miner.md and its weight is on what NOT to
    mine: ask a model for assumptions and it returns 'the data is accurate',
    which is true of every paper and moves nothing when toggled. `weakens_how`
    is the filter, enforced here as well as in the prompt -- an assumption that
    cannot say what the claim loses is background, and background makes a dead
    control.
    """

    name = "assumptions"
    reads = ("doc", "claims", "number_pool", "selected_claim_id",
             "critical_path_ids")
    writes = ("assumptions", "claim_analyses", "path_unsafe")
    budget_s = 5.0

    KINDS = ("scope", "measurement", "generalization", "implementation")
    SOURCES = ("paper_explicit", "paper_implicit", "pedagogical")
    MAX_ASSUMPTIONS = 5
    #: no specific consequence fits in fewer characters than this -- the cheap
    #: deterministic stand-in for the prompt's ban on generic weakens_how.
    MIN_WEAKENS_CHARS = 20
    MAX_SPAN_CHARS = 600
    MAX_CONTEXT_CHARS = 12_000
    DEPENDENCY_CUES = (
        "reliable", "valid", "unbiased", "representative", "stable",
        "estimator", "estimate", "captures", "preserves", "generalizes",
        "comparable", "independent", "동일하게", "신뢰할", "추정량", "대표",
    )
    DEFINITION_CUES = (
        "reported values are", "reported percentages are", "is measured with",
        "are measured with", "is interpreted as", "are interpreted as",
        "denotes", "means", "listed datasets and model architectures",
        "다른 것을 뜻", "정의", "로 측정", "로 해석",
    )
    DEFINITION_CONSEQUENCES = (
        "would quantify a different property", "would describe those",
        "would no longer support these exact", "would mean a different",
        "different interpretation", "other metric", "다른 지표", "다른 값",
    )
    CASCADE_CUES = (
        "other subclaim", "other claim", "entire argument", "whole argument",
        "all downstream", "central conclusion", "다른 하위 주장", "전체 논증",
        "후속 주장", "중심 결론",
    )

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.explainer_route and state.explainer_route != "assumption_switchboard":
            state.assumptions = []
            state.claim_analyses = {}
            bus.decision("assumptions", "explainer 경로에서는 switchboard 가정 채굴 생략")
            return
        path = state.critical_path_ids or ([state.selected_claim_id]
                                            if state.selected_claim_id else [])
        if not path:
            raise StageError("no critical claim path to decompose")

        state.claim_analyses = {}
        state.path_unsafe = False
        by_id = {c.id: c for c in state.claims}
        for claim_id in path:
            claim = by_id.get(claim_id)
            if claim is None:
                state.path_unsafe = True
                bus.decision("assumptions", f"{claim_id}: path node 없음 -> 실패",
                             claim_id=claim_id)
                continue
            analysis_failed = False
            try:
                out = self.llm.structured(
                    role="assumption_miner", prompt=self._render(claim, state),
                    schema_hint="Assumption[]", bus=bus,
                )
                assumptions = self._accept(
                    out.get("assumptions") if isinstance(out, dict) else None,
                    claim, state, bus,
                )
            except Exception as e:  # noqa: BLE001 -- preserve path and render map
                assumptions = []
                state.path_unsafe = True
                analysis_failed = True
                bus.decision("assumptions", f"{claim.id}: 분석 실패",
                             claim_id=claim.id, error=str(e))

            explanation = self._explain(claim, state, bus)
            failed = analysis_failed or (
                claim.id == state.selected_claim_id and not assumptions
            )
            if failed:
                state.path_unsafe = True
                bus.decision("assumptions", f"{claim.id}: frontier 가정 없음 -> 안전 map",
                             claim_id=claim.id)
            state.claim_analyses[claim.id] = ClaimAnalysis(
                claim_id=claim.id,
                verification="failed" if failed else "verified",
                explanation=explanation,
                assumptions=assumptions,
                evidence_span_ids=list(claim.evidence_span_ids),
            )

        frontier = state.claim_analyses.get(state.selected_claim_id or "")
        state.assumptions = list(frontier.assumptions) if frontier else []
        bus.emit_status(
            f"핵심 경로 {len(path)}개 node 분석 — frontier 가정 "
            f"{len(state.assumptions)}개"
        )

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
        by_id = {c.id: c for c in state.claims}
        lines = ["# lineage"]
        for claim_id in state.critical_path_ids:
            node = by_id.get(claim_id)
            if node:
                lines.append(f"{node.id} [{node.role}] {node.text}")
        lines += ["", "# claim", f"{claim.id} {claim.text}", "", "# evidence"]
        for sid in claim.evidence_span_ids:
            sp = state.doc.spans.get(sid)
            if sp is None:
                continue
            text = sp.text
            if len(text) > self.MAX_SPAN_CHARS:
                text = text[:self.MAX_SPAN_CHARS] + "…"
            lines.append(f"{sid} [{sp.kind}] {text}")

        if state.source_path or state.source_text:
            lines += ["", "# source context"]
            context_used = 0
            for sid, sp in state.doc.spans.items():
                if sp.origin != "paper":
                    continue
                if sp.section in {"references", "acknowledgments"}:
                    continue
                text = sp.text
                if len(text) > self.MAX_SPAN_CHARS:
                    text = text[:self.MAX_SPAN_CHARS] + "…"
                line = f"{sid} [{sp.kind} section={sp.section}] {text}"
                if context_used + len(line) > self.MAX_CONTEXT_CHARS:
                    break
                lines.append(line)
                context_used += len(line) + 1

        lines += ["", "# numbers"]
        for n in state.number_pool.values():
            if (n.span_id in claim.evidence_span_ids
                    or state.source_path or state.source_text):
                lines.append(f"{n.id} {n.raw}  span={n.span_id}  {n.context}")

        lines += ["", "# stated conditions"]
        lines += claim.assumptions or ["(none stated)"]
        return "\n".join(lines)

    def _explain(self, claim: Claim, state: PaperState, bus: EventBus) -> str:
        """Give every path node a reader-facing explanation, with an offline fallback."""
        path = " -> ".join(state.critical_path_ids)
        evidence = []
        for span_id in claim.evidence_span_ids:
            span = state.doc.spans.get(span_id)
            if span:
                evidence.append(f"{span.id} [{span.kind}] {span.text}")
        prompt = (
            f"# path\n{path}\n\n# claim\n{claim.id} [{claim.role}] {claim.text}\n"
            f"\n# evidence\n{chr(10).join(evidence) or '(none)'}\n"
        )
        try:
            out = self.llm.structured(
                role="claim_explainer", prompt=prompt,
                schema_hint="ClaimExplanation", bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- explanation cannot block maps
            bus.decision("assumptions", f"{claim.id}: 설명 생성 실패 -> 근거 요약",
                         claim_id=claim.id, error=str(e))
            out = {}
        if isinstance(out, dict) and str(out.get("explanation") or "").strip():
            return str(out["explanation"]).strip()
        return f"{claim.text} (근거 span: {', '.join(claim.evidence_span_ids)})"

    # -- acceptance --

    def _accept(self, raw, claim: Claim, state: PaperState,
                bus: EventBus) -> list[Assumption]:
        kept: list[Assumption] = []
        seen: set[str] = set()
        raw = raw if isinstance(raw, list) else []
        for i, a in enumerate(raw):
            if not isinstance(a, dict):
                bus.decision("assumptions", f"#{i}: 객체가 아님 -> 폐기")
                continue
            aid = str(a.get("id") or f"a{i + 1}").strip()
            if aid in seen:
                bus.decision("assumptions", f"{aid}: 중복 id -> 폐기")
                continue

            why = str(a.get("weakens_how") or "").strip()
            if len(why) < self.MIN_WEAKENS_CHARS:
                bus.decision("assumptions",
                             f"{aid}: weakens_how가 없거나 일반론 -> 폐기",
                             weakens_how=why)
                continue

            text = str(a.get("text") or "").strip()
            if self._definition_restatement(text, why):
                bus.decision(
                    "assumptions",
                    f"{aid}: claim의 정의/측정 설정 재진술 -> 폐기",
                    assumption_id=aid, assumption_text=text[:120],
                )
                continue

            kind, source = a.get("kind"), a.get("source")
            if kind not in self.KINDS or source not in self.SOURCES:
                bus.decision("assumptions",
                             f"{aid}: kind/source가 리터럴 밖 -> 폐기",
                             kind=kind, source=source)
                continue

            span_id = a.get("span_id")
            if span_id is not None and span_id not in state.doc.spans:
                bus.decision("assumptions", f"{aid}: 원문에 없는 span "
                                            f"'{span_id}' -> 해제", span_id=span_id)
                span_id = None
            if (source != "pedagogical" and span_id
                    and state.doc.spans[span_id].origin != "paper"):
                bus.decision(
                    "assumptions",
                    f"{aid}: 수동 입력 span은 paper attribution 불가 -> pedagogical",
                    assumption_id=aid, span_id=span_id,
                )
                source, span_id = "pedagogical", None

            got = Assumption(
                id=aid, claim_id=claim.id, text=text,
                kind=kind, source=source, weakens_how=why, span_id=span_id,
                support_type=("necessary" if a.get("support_type") == "necessary"
                              else "independent"),
            )
            errs = got.validate()
            if not got.text:
                errs.append(f"assumption '{aid}': no text")
            if errs:
                bus.decision("assumptions", f"{aid}: {'; '.join(errs)} -> 폐기")
                continue

            seen.add(aid)
            kept.append(got)

        if len(kept) > self.MAX_ASSUMPTIONS:
            bus.decision("assumptions", f"{len(kept)}개 -> 상위 "
                                        f"{self.MAX_ASSUMPTIONS}개만 사용")
            kept = kept[:self.MAX_ASSUMPTIONS]

        self._normalize_support_types(kept, bus, claim.id)

        bus.decision("assumptions", f"{claim.id}: 후보 {len(raw)}개 중 "
                                    f"{len(kept)}개 채택",
                     claim_id=claim.id, proposed=len(raw), accepted=len(kept))
        return kept

    @classmethod
    def _definition_restatement(cls, text: str, weakens_how: str) -> bool:
        combined = f"{text} {weakens_how}".lower()
        if any(cue in combined for cue in cls.DEPENDENCY_CUES):
            return False
        definition = any(cue in combined for cue in cls.DEFINITION_CUES)
        consequence_only_renames = any(
            cue in combined for cue in cls.DEFINITION_CONSEQUENCES
        )
        return definition or consequence_only_renames

    @classmethod
    def _normalize_support_types(cls, assumptions: list[Assumption],
                                 bus: EventBus, claim_id: str) -> None:
        """Necessary means a cascade, not merely 'important to this value'."""
        necessary = [a for a in assumptions if a.support_type == "necessary"]
        if not necessary:
            return
        cascade = [
            a for a in necessary
            if any(cue in f"{a.text} {a.weakens_how}".lower()
                   for cue in cls.CASCADE_CUES)
        ]
        keep_id = cascade[0].id if cascade else None
        changed = []
        for assumption in necessary:
            if assumption.id != keep_id:
                assumption.support_type = "independent"
                changed.append(assumption.id)
        if changed:
            bus.decision(
                "assumptions",
                f"{claim_id}: cascade 근거 없는 necessary를 independent로 재분류",
                claim_id=claim_id, changed=changed,
                kept_necessary=keep_id,
            )
