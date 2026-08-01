"""Claim graph construction, interaction scoring and frontier selection."""

from __future__ import annotations

from ..clients import LLM
from ..events import EventBus
from ..state import (
    Claim,
    InteractionScore,
    PaperState,
    Span,
)
from .base import (
    Stage,
    StageError,
)
from .context import ContextAnalyst
from .text import (
    NOVELTY_MARKERS,
    _claim_sections,
    _claimworthy_span,
    _falsifiable_claim,
    _looks_like_metadata,
)


class BuildClaims(Stage):
    """LLM. Structure only -- no explanation text is generated here, so that
    importance judgement and prose generation never share a context.

    The prompt is prompts/claim_mapper.md. Invariant 2 is enforced at both
    ends: the prompt states the binding rule, and every returned claim is
    re-bound against the real span index here. A claim the model could not tie
    to the source is dropped -- loudly, as an event, because 'we threw this
    one away' is the judgement worth showing.
    """

    name = "claims"
    reads = ("doc", "claim_text", "source_text", "source_path", "context_analysis")
    writes = ("claims", "root_claim_id")
    budget_s = 6.0

    #: how much of the span index goes into one call, and how much of a single
    #: span survives. References sit at the tail, so overflow drops from there.
    MAX_PROMPT_CHARS = 24_000
    MAX_SPAN_CHARS = 400
    MAX_ASSUMPTIONS = 5
    FALLBACK_CLAIMS = 3
    CLAIM_SECTIONS = {"abstract", "intro", "results", "discussion"}
    HARD_EXCLUDE_SECTIONS = {"methods", "references", "acknowledgments", "other"}

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        if not state.doc.spans:
            raise StageError("no spans to map claims onto")

        claim_text = (state.claim_text or "").strip()
        if claim_text:
            # An explicit claim is already the user's intended root. Do not
            # ask a mapper to rediscover or paraphrase it; PDF spans remain
            # available as optional context when a source was also supplied.
            seed_span = state.doc.spans.get("input_claim")
            if seed_span is None:
                raise StageError("manual claim seed span is missing")
            state.claims = [Claim(
                id="c1", text=claim_text,
                evidence_span_ids=[seed_span.id], role="result", order=0,
            )]
            state.root_claim_id = "c1"
            bus.decision(
                "claims", "수동 claim을 root node로 사용 — claim mapper 생략",
                input_kind="claim", root_claim_id="c1", span_id=seed_span.id,
            )
            bus.emit_status("수동 claim root 준비 완료")
            return

        context = state.context_analysis or {}
        if isinstance(context.get("claims"), list) and context["claims"]:
            # The large-context pass already separated claims and relations;
            # this stage only performs the old span-binding/graph validation.
            out = context
            bus.decision("claims", "context analysis의 구조화 claim 사용",
                         proposed_claims=len(context["claims"]))
        else:
            prompt, dropped = self._render_doc(state)
            if dropped:
                bus.decision("claims", f"프롬프트 예산 초과 -> 뒤쪽 span {dropped}개 제외",
                             limit=self.MAX_PROMPT_CHARS, dropped=dropped)
            out = self.llm.structured(
                role="claim_mapper", prompt=prompt, schema_hint="GraphClaims", bus=bus,
            )

        raw = out.get("claims") if isinstance(out, dict) else None
        root_id = out.get("root_claim_id") if isinstance(out, dict) else None
        if raw is None:
            # No `claims` key at all -- an unconfigured MockLLM, not a model
            # that looked and found nothing. `{"claims": []}` is a real answer
            # and is left alone, so the refused path stays reachable.
            raw, root_id = self._fallback(state, bus)

        state.claims = self._accept(raw, root_id, state, bus)
        if not state.claims and state.context_analysis is not None:
            # A live context model may produce a semantically useful claim
            # whose citation is malformed or points at a truncated span id.
            # Do not discard an otherwise readable source: retry claim
            # selection deterministically from the section-labelled spans.
            bus.decision(
                "claims",
                "context claim span binding 실패 -> 원문 후보로 재시도",
                proposed=len(raw) if isinstance(raw, list) else 0,
            )
            fallback_raw, fallback_root = self._fallback(state, bus)
            state.claims = self._accept(fallback_raw, fallback_root, state, bus)
        if not state.claims:
            # pipeline turns this into mode="refused" -- the refusal screen is
            # part of the product, not a crash.
            raise StageError("no claim survived span binding")
        bus.emit_status(f"claim graph {len(state.claims)}개 node 추출")

    # -- prompt --

    def _render_doc(self, state: PaperState) -> tuple[str, int]:
        """The span index as the prompt file documents it. Ids are the payload:
        the model can only cite what it is shown here."""
        lines, used, dropped = ["# spans"], 0, 0
        for sid, sp in state.doc.spans.items():
            if sp.origin == "paper" and sp.section not in _claim_sections(state):
                continue
            text = sp.text
            if len(text) > self.MAX_SPAN_CHARS:
                text = text[:self.MAX_SPAN_CHARS] + "…"
            line = f"{sid} [{sp.kind} section={sp.section}] {text}"
            if used + len(line) > self.MAX_PROMPT_CHARS:
                dropped += 1
                continue
            used += len(line) + 1
            lines.append(line)

        lines.append("# figures")
        for fid, f in state.doc.figures.items():
            lines.append(f"{fid}  page={f['page']}  caption={f.get('caption_span_id')}")
        return "\n".join(lines), dropped

    # -- acceptance --

    def _accept(self, raw, root_id, state: PaperState,
                bus: EventBus) -> list[Claim]:
        """Second half of invariant 2. Nothing reaches state.claims without a
        span id that exists in this document."""
        claims: list[Claim] = []
        seen: set[str] = set()
        raw = raw if isinstance(raw, list) else []
        graph_response = root_id is not None or any(
            isinstance(c, dict) and "parent_id" in c for c in raw
        )
        for i, c in enumerate(raw):
            if not isinstance(c, dict):
                bus.decision("claims", f"#{i}: 객체가 아님 -> 폐기")
                continue
            cid = str(c.get("id") or f"c{i + 1}").strip()
            text = str(c.get("text") or "").strip()
            if not text:
                bus.decision("claims", f"{cid}: 주장 텍스트 없음 -> 폐기", claim_id=cid)
                continue
            if cid in seen:
                bus.decision("claims", f"{cid}: 중복 id -> 폐기", claim_id=cid)
                continue

            span_ids, unknown = self._bind(c.get("evidence_span_ids"), state)
            if unknown:
                bus.decision("claims", f"{cid}: 원문에 없는 span {unknown} 무시",
                             claim_id=cid, unknown=unknown)
            disallowed = [
                sid for sid in span_ids
                if state.doc.spans[sid].origin == "paper"
                and state.doc.spans[sid].section not in _claim_sections(state)
            ]
            if disallowed:
                bus.decision(
                    "claims", f"{cid}: 금지 section span -> claim 근거에서 제거",
                    claim_id=cid, section_spans=disallowed,
                )
                span_ids = [sid for sid in span_ids if sid not in disallowed]
            nonclaim = [
                sid for sid in span_ids
                if state.doc.spans[sid].origin == "paper"
                and _looks_like_metadata(state.doc.spans[sid])
            ]
            if nonclaim:
                bus.decision(
                    "claims", f"{cid}: 저자/메타데이터 span -> claim 근거에서 제거",
                    claim_id=cid, spans=nonclaim,
                )
                span_ids = [sid for sid in span_ids if sid not in nonclaim]
            paper_evidence = [
                state.doc.spans[sid] for sid in span_ids
                if state.doc.spans[sid].origin == "paper"
            ]
            if paper_evidence and not _falsifiable_claim(text, paper_evidence):
                bus.decision(
                    "claims",
                    f"{cid}: 표 설명/정의/메타데이터이며 반증 가능한 claim이 아님 -> 폐기",
                    claim_id=cid, claim=text[:120],
                    evidence_span_ids=list(span_ids),
                )
                continue
            if not span_ids:
                bus.decision("claims", f"{cid}: 근거 span 없음 -> 폐기",
                             claim_id=cid, claim=text[:80])
                continue

            fig = c.get("figure_id")
            if fig is not None and fig not in state.doc.figures:
                bus.decision("claims", f"{cid}: 없는 figure '{fig}' -> 해제",
                             claim_id=cid)
                fig = None

            role = c.get("role", "subclaim")
            if role not in ("premise", "subclaim", "result", "boundary",
                            "methodology"):
                bus.decision("claims", f"{cid}: role '{role}' 사용 불가 -> subclaim",
                             claim_id=cid, role=role)
                role = "subclaim"
            try:
                order = int(c.get("order", i))
            except (TypeError, ValueError):
                order = i
            parent_id = c.get("parent_id")
            parent_id = str(parent_id).strip() if parent_id else None

            seen.add(cid)
            claims.append(Claim(
                id=cid, text=text, evidence_span_ids=span_ids,
                assumptions=self._assumptions(c.get("assumptions")),
                figure_id=fig,
                confidence=self._confidence(c.get("confidence")),
                novelty_marker=any(m in text.lower() for m in NOVELTY_MARKERS),
                parent_id=parent_id,
                role=role,
                order=order,
                difficulty=self._confidence(c.get("difficulty", 0.5)),
                pedagogical_gain=self._confidence(c.get("pedagogical_gain", 0.5)),
                support_type=("necessary" if c.get("support_type") == "necessary"
                              else "independent"),
            ))

        if graph_response:
            claims = self._validate_graph(claims, root_id, bus)
        else:
            claims = self._wrap_flat(claims, bus)

        state.root_claim_id = next((c.id for c in claims if c.parent_id is None), None)
        bus.decision("claims", f"후보 {len(raw)}개 중 {len(claims)}개 graph node 채택 "
                               f"(폐기 {len(raw) - len(claims)}개)",
                     proposed=len(raw), accepted=len(claims))
        return claims

    @staticmethod
    def _wrap_flat(claims: list[Claim], bus: EventBus) -> list[Claim]:
        """Compatibility path for old flat LLM fixtures and offline fallback."""
        if not claims:
            return []
        root = claims[0]
        root.parent_id = None
        root.role = "result"
        root.order = 0
        for i, claim in enumerate(claims[1:], start=1):
            claim.parent_id = root.id
            claim.role = "subclaim"
            claim.order = i
        bus.decision("claims", "flat claims 응답 -> root/child graph fallback",
                     root_claim_id=root.id, nodes=[c.id for c in claims])
        return claims

    @staticmethod
    def _validate_graph(claims: list[Claim], root_id: str | None,
                        bus: EventBus) -> list[Claim]:
        by_id = {c.id: c for c in claims}
        roots = [c for c in claims if c.parent_id is None]
        explicit_root_missing = root_id is not None and str(root_id) not in by_id
        root = by_id.get(str(root_id)) if root_id else None
        if explicit_root_missing:
            bus.decision("claims", "명시 root_claim_id가 node에 없음 -> graph 폐기",
                         root_claim_id=root_id)
            return []
        if root is None and len(roots) == 1:
            root = roots[0]
            bus.decision("claims", "명시 root 없음 -> 유일한 parent 없는 node 사용",
                         root_claim_id=root.id)
        if root is None:
            bus.decision("claims", "유효한 단일 root 없음 -> graph 폐기",
                         roots=[c.id for c in roots], root_claim_id=root_id)
            return []
        if root.parent_id is not None:
            bus.decision("claims", "root node의 parent_id는 null이어야 함 -> graph 폐기",
                         root_claim_id=root.id, parent_id=root.parent_id)
            return []

        root.parent_id = None
        root.role = "result" if root.role == "subclaim" else root.role
        valid = {root.id}
        changed = True
        while changed:
            changed = False
            for claim in claims:
                if claim.id in valid:
                    continue
                if claim.parent_id == claim.id:
                    continue
                if claim.parent_id in valid:
                    valid.add(claim.id)
                    changed = True

        for claim in claims:
            if claim.id not in valid:
                bus.decision("claims", f"{claim.id}: parent 누락 또는 cycle -> graph에서 폐기",
                             claim_id=claim.id, parent_id=claim.parent_id)
        return sorted(
            (c for c in claims if c.id in valid),
            key=lambda c: (c.order, claims.index(c)),
        )

    @staticmethod
    def _bind(ids, state: PaperState) -> tuple[list[str], list[str]]:
        """Keep ids that name a real span, in order, without duplicates."""
        kept: list[str] = []
        unknown: list[str] = []
        for sid in ids if isinstance(ids, list) else []:
            sid = str(sid).strip()
            if not sid or sid in kept or sid in unknown:
                continue
            (kept if sid in state.doc.spans else unknown).append(sid)
        return kept, unknown

    def _assumptions(self, raw) -> list[str]:
        if not isinstance(raw, list):
            return []
        out = [str(a).strip() for a in raw if str(a).strip()]
        return out[:self.MAX_ASSUMPTIONS]

    @staticmethod
    def _confidence(raw) -> float:
        try:
            return min(1.0, max(0.0, float(raw)))
        except (TypeError, ValueError):
            return 0.5

    # -- offline path --

    def _fallback(self, state: PaperState,
                  bus: EventBus) -> tuple[list[dict], str | None]:
        """Source-bound fallback ranked by assertion strength, not numbers."""
        allowed = _claim_sections(state)
        ranked = []
        for index, (sid, span) in enumerate(state.doc.spans.items()):
            if span.section not in allowed or not _claimworthy_span(span):
                continue
            lowered = span.text.lower()
            signal = sum(lowered.count(token) for token in ContextAnalyst.SIGNALS)
            section_rank = {
                "results": 0, "discussion": 1, "abstract": 2, "intro": 3,
            }.get(span.section, 9)
            ranked.append((-signal, section_rank, -len(span.text), index, sid, span))
        ranked.sort(key=lambda item: item[:4])
        abstract = sorted(
            (item for item in ranked if item[5].section == "abstract"),
            key=lambda item: item[3],
        )
        if not abstract:
            bus.decision("claims", "반증 가능한 abstract claim 없음 -> 실패")
            return [], None
        root = abstract[0]
        children = [item for item in ranked if item is not root][:
            max(0, self.FALLBACK_CLAIMS - 1)
        ]
        picked = [root, *children]
        claims = []
        for order, (*_, sid, span) in enumerate(picked):
            claims.append({
                "id": f"c{order + 1}",
                "text": span.text[:700],
                "evidence_span_ids": [sid],
                "parent_id": None if order == 0 else "c1",
                "role": "result" if order == 0 else "subclaim",
                "order": order,
                "confidence": 0.72 if order == 0 else 0.64,
                "difficulty": min(0.9, 0.55 + 0.08 * order),
                "pedagogical_gain": min(0.95, 0.68 + 0.08 * order),
                "support_type": "independent",
            })
        bus.decision(
            "claims", "모델 claim 사용 불가 -> assertion 중심 원문 fallback",
            spans=[sid for *_, sid, _ in picked],
        )
        return claims, "c1"

    @staticmethod
    def _is_claim_candidate(span: Span) -> bool:
        return _claimworthy_span(span)


class ScoreInteractions(Stage):
    """Cheap model or rules. Decides what NOT to visualise -- emit the
    rejections, they are the point."""

    name = "score"
    reads = ("claims", "number_pool")
    writes = ("scores",)
    budget_s = 2.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        measured_claims = 0
        for c in state.claims:
            evidence_spans = [state.doc.spans[sid] for sid in c.evidence_span_ids
                              if sid in state.doc.spans]
            number_count = sum(
                1 for n in state.number_pool.values()
                if n.span_id in c.evidence_span_ids
            )
            grounded = any(
                n.span_id in c.evidence_span_ids for n in state.number_pool.values()
            )
            if grounded:
                measured_claims += 1
            source_bound = any(
                state.doc.spans.get(span_id) is not None
                and state.doc.spans[span_id].origin == "paper"
                for span_id in c.evidence_span_ids
            )
            manual_seed = any(
                (state.doc.spans.get(span_id) is not None
                 and state.doc.spans[span_id].origin == "manual")
                for span_id in c.evidence_span_ids
            )
            s = InteractionScore(
                claim_id=c.id,
                manipulability=min(1.0, 0.25 + 0.15 * number_count
                                   + (0.35 if c.figure_id else 0.0)
                                   + (0.15 if any(sp.kind == "caption" for sp in evidence_spans) else 0.0)),
                causal_clarity=min(1.0, 0.35
                                   + (0.25 if c.role in {"result", "boundary"} else 0.0)
                                   + (0.15 if any("because" in sp.text.lower() or "therefore" in sp.text.lower()
                                                   for sp in evidence_spans) else 0.0)),
                learning_value=min(1.0, 0.35 + 0.35 * c.pedagogical_gain
                                   + 0.10 * min(number_count, 3)),
                # A manually supplied claim is bound to the user's input, but
                # not promoted to paper-grounded evidence. It may proceed to
                # external verification with a conservative faithfulness floor.
                faithfulness=(0.9 if grounded else 0.75 if source_bound
                              else 0.55 if manual_seed else 0.2),
                demo_reliability=min(1.0, 0.45 + 0.10 * len(evidence_spans)
                                     + (0.15 if number_count else 0.0)),
                difficulty=c.difficulty,
                pedagogical_gain=c.pedagogical_gain,
            )
            state.scores[c.id] = s
            bus.decision("scorer", f"{c.id} score={s.total:.2f} frontier="
                         f"{s.frontier_total:.2f}",
                         claim_id=c.id, grounded=grounded,
                         frontier_score=round(s.frontier_total, 3))
        state.mode = "quantitative" if measured_claims else "qualitative"
        if not measured_claims:
            bus.decision("scorer", "number_pool에 claim과 매칭되는 수치 없음 -> qualitative 모드")
        else:
            bus.decision("scorer", f"{measured_claims}개 claim이 number_pool과 매칭 -> quantitative 모드")


class SelectFrontier(Stage):
    """Choose the most teachable node without pausing for human input.

    The root is the paper thesis; this stage chooses a pedagogic frontier and
    records the root-to-frontier path for the downstream node analysis.
    """

    name = "select"
    reads = ("claims", "scores")
    writes = ("selected_claim_id", "frontier_claim_id", "critical_path_ids")
    budget_s = 0.1

    def run(self, state: PaperState, bus: EventBus) -> None:
        candidates = [c for c in state.claims if c.id in state.scores]
        if not candidates:
            raise StageError("no scored claim to select frontier")
        eligible = [c for c in candidates
                    if state.scores[c.id].faithfulness >= 0.5]
        if not eligible:
            raise StageError("no faithful claim to select frontier")
        children = [c for c in eligible if c.id != state.root_claim_id]
        if children:
            eligible = children
        chosen = max(
            eligible,
            key=lambda c: (state.scores[c.id].frontier_total, -c.order),
        )
        path: list[str] = []
        seen: set[str] = set()
        current: Claim | None = chosen
        by_id = {c.id: c for c in state.claims}
        while current is not None:
            if current.id in seen:
                raise StageError("cycle while building critical claim path")
            seen.add(current.id)
            path.append(current.id)
            current = by_id.get(current.parent_id) if current.parent_id else None
        path.reverse()
        if state.root_claim_id and path[0] != state.root_claim_id:
            raise StageError("frontier path does not reach graph root")

        state.frontier_claim_id = chosen.id
        state.selected_claim_id = chosen.id
        state.critical_path_ids = path
        bus.decision(
            "selector", f"{chosen.id}: pedagogic frontier 자동 선택",
            claim_id=chosen.id, score=round(state.scores[chosen.id].total, 3),
            frontier_score=round(state.scores[chosen.id].frontier_total, 3),
            root_claim_id=state.root_claim_id,
            critical_path_ids=path,
            policy="highest_frontier_score_then_graph_order",
            candidates=[c.id for c in candidates],
        )
        bus.emit_status(f"{chosen.id} pedagogic frontier 자동 선택")


# Existing imports and downstream adapters may still refer to the old stage name.
SelectClaim = SelectFrontier
