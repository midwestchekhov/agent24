"""Pipeline stages.

Do not change reads/writes tuples without updating the stage contract in
CLAUDE.md. Those declarations drive internal recomputation after a critic
revision.
"""

from __future__ import annotations

import re

import pymupdf

from ..clients import LLM, Search
from ..events import EventBus
from ..state import (
    Assumption,
    Attribution,
    Claim,
    Control,
    DocGraph,
    Evidence,
    EvidenceFacet,
    InteractionScore,
    InteractionSpec,
    NumberFact,
    PaperState,
    Span,
    StatusRule,
)
from .base import Stage, StageError

NOVELTY_MARKERS = ("first", "novel", "state-of-the-art", "unprecedented", "최초")
NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|AUC|HR|OR|ms|GB|x)?")


class Parse(Stage):
    """Deterministic. No LLM. Builds the span index and number pool -- every
    later grounding check depends on this being complete."""

    name = "parse"
    writes = ("doc", "number_pool")
    budget_s = 8.0

    #: a block opening with this is a figure/table caption
    CAPTION_RE = re.compile(r"^(fig(?:ure)?|table|tbl)\.?\s*(\d+)", re.I)
    #: units that precede their number in medical prose ("AUC 0.87", "HR 0.62")
    LEADING_UNIT_RE = re.compile(r"\b(AUC|HR|OR)\s*[:=]?\s*$", re.I)

    def run(self, state: PaperState, bus: EventBus) -> None:
        call_id = bus.tool_call("pdf.extract", path=state.source_path)
        try:
            doc = pymupdf.open(state.source_path)
        except Exception as e:  # noqa: BLE001 -- missing/corrupt file
            bus.tool_result(call_id, None, error=str(e))
            raise StageError(f"cannot open {state.source_path}: {e}") from e

        spans: dict[str, Span] = {}
        figures: dict[str, dict] = {}
        try:
            for pno in range(doc.page_count):
                page = doc[pno]
                rects = self._index_page(page, pno, spans)
                self._index_figures(page, pno, spans, rects, figures)
            pages = doc.page_count
        finally:
            doc.close()

        if not spans:
            bus.tool_result(call_id, {"pages": pages, "spans": 0})
            raise StageError("no text layer -- scanned PDF?")

        state.doc = DocGraph(spans=spans, figures=figures)
        state.number_pool = self._index_numbers(spans)

        kinds: dict[str, int] = {}
        for sp in spans.values():
            kinds[sp.kind] = kinds.get(sp.kind, 0) + 1
        bus.tool_result(call_id, {
            "pages": pages, "spans": len(spans), "figures": len(figures),
            "numbers": len(state.number_pool), "kinds": kinds,
        })
        bus.emit_status("원문 색인 완료")
        bus.decision("parse", f"{len(state.number_pool)}개 수치를 근거 풀에 등록",
                     kinds=kinds)

    # -- per-page indexing --

    def _index_page(self, page, pno: int, spans: dict) -> dict:
        """Table cells first, then text blocks that fall outside any table.
        Returns span_id -> Rect for the text blocks, so figures can find their
        caption. Ids are position-derived, so a rerun on the same file
        reproduces them exactly."""
        rects: dict = {}
        boxes = []
        for ti, tab in enumerate(page.find_tables().tables):
            boxes.append(pymupdf.Rect(tab.bbox))
            for ri, row in enumerate(tab.extract()):
                for ci, cell in enumerate(row):
                    text = " ".join((cell or "").split())
                    if not text:
                        continue
                    sid = f"p{pno + 1}_t{ti}r{ri}c{ci}"
                    spans[sid] = Span(sid, pno + 1, "table_cell", text)

        for bi, b in enumerate(page.get_text("blocks", sort=True)):
            if b[6] != 0:  # image block; geometry comes from get_image_info
                continue
            text = " ".join(b[4].split())
            if not text:
                continue
            rect = pymupdf.Rect(b[:4])
            area = rect.get_area()
            if area and any((rect & bx).get_area() > 0.5 * area for bx in boxes):
                continue  # already captured as table cells
            sid = f"p{pno + 1}_b{bi}"
            spans[sid] = Span(sid, pno + 1, self._classify(text), text)
            rects[sid] = rect
        return rects

    def _index_figures(self, page, pno: int, spans: dict, rects: dict,
                       figures: dict) -> None:
        """bbox + page + caption span only. No image decoding."""
        caps = [(sid, rects[sid]) for sid in rects
                if spans[sid].kind == "caption"]
        for fi, info in enumerate(page.get_image_info()):
            bbox = [round(v, 2) for v in info["bbox"]]
            cap_id = self._nearest_caption(bbox, caps)
            figures[self._figure_id(spans.get(cap_id), pno, fi)] = {
                "page": pno + 1, "bbox": bbox, "caption_span_id": cap_id,
            }

    # -- classification helpers --

    def _classify(self, text: str) -> str:
        if self.CAPTION_RE.match(text):
            return "caption"
        if "=" in text and len(text) <= 200:
            dense = sum(c.isalpha() for c in text)
            if dense / max(len(text.replace(" ", "")), 1) < 0.55:
                return "equation"
        return "paragraph"

    @staticmethod
    def _nearest_caption(bbox, caps) -> str | None:
        if not caps:
            return None
        below = [(r.y0 - bbox[3], sid) for sid, r in caps if r.y0 >= bbox[3]]
        if below:
            return min(below)[1]
        return min((abs(r.y0 - bbox[1]), sid) for sid, r in caps)[1]

    def _figure_id(self, cap: Span | None, pno: int, fi: int) -> str:
        if cap is not None:
            m = self.CAPTION_RE.match(cap.text)
            if m and m.group(1).lower().startswith("fig"):
                return f"fig{m.group(2)}"
        return f"p{pno + 1}_f{fi}"

    # -- number pool --

    def _index_numbers(self, spans: dict) -> dict:
        pool: dict[str, NumberFact] = {}
        for sid, sp in spans.items():
            for i, m in enumerate(NUM_RE.finditer(sp.text)):
                nid = f"num_{sid}_{i}"
                pool[nid] = NumberFact(
                    id=nid, value=float(m.group(1)), raw=m.group(0).strip(),
                    span_id=sid, unit=self._unit(sp.text, m),
                    context=self._context(sp.text, m),
                )
        return pool

    def _unit(self, text: str, m) -> str | None:
        if m.group(2):
            return m.group(2)
        lead = self.LEADING_UNIT_RE.search(text[:m.start()])
        return lead.group(1).upper() if lead else None

    @staticmethod
    def _context(text: str, m, width: int = 40) -> str:
        lo, hi = max(0, m.start() - width), min(len(text), m.end() + width)
        return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


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
    reads = ("doc",)
    writes = ("claims",)
    budget_s = 6.0

    #: how much of the span index goes into one call, and how much of a single
    #: span survives. References sit at the tail, so overflow drops from there.
    MAX_PROMPT_CHARS = 24_000
    MAX_SPAN_CHARS = 400
    MAX_ASSUMPTIONS = 5
    FALLBACK_CLAIMS = 3

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        if not state.doc.spans:
            raise StageError("no spans to map claims onto")

        prompt, dropped = self._render_doc(state)
        if dropped:
            bus.decision("claims", f"프롬프트 예산 초과 -> 뒤쪽 span {dropped}개 제외",
                         limit=self.MAX_PROMPT_CHARS, dropped=dropped)
        out = self.llm.structured(
            role="claim_mapper", prompt=prompt, schema_hint="Claim[]", bus=bus,
        )

        raw = out.get("claims")
        if raw is None:
            # No `claims` key at all -- an unconfigured MockLLM, not a model
            # that looked and found nothing. `{"claims": []}` is a real answer
            # and is left alone, so the refused path stays reachable.
            raw = self._fallback(state, bus)

        state.claims = self._accept(raw, state, bus)
        if not state.claims:
            # pipeline turns this into mode="refused" -- the refusal screen is
            # part of the product, not a crash.
            raise StageError("no claim survived span binding")
        bus.emit_status(f"핵심 주장 {len(state.claims)}개 추출")

    # -- prompt --

    def _render_doc(self, state: PaperState) -> tuple[str, int]:
        """The span index as the prompt file documents it. Ids are the payload:
        the model can only cite what it is shown here."""
        lines, used, dropped = ["# spans"], 0, 0
        for sid, sp in state.doc.spans.items():
            text = sp.text
            if len(text) > self.MAX_SPAN_CHARS:
                text = text[:self.MAX_SPAN_CHARS] + "…"
            line = f"{sid} [{sp.kind}] {text}"
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

    def _accept(self, raw, state: PaperState, bus: EventBus) -> list[Claim]:
        """Second half of invariant 2. Nothing reaches state.claims without a
        span id that exists in this document."""
        claims: list[Claim] = []
        seen: set[str] = set()
        raw = raw if isinstance(raw, list) else []
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
            if not span_ids:
                bus.decision("claims", f"{cid}: 근거 span 없음 -> 폐기",
                             claim_id=cid, claim=text[:80])
                continue

            fig = c.get("figure_id")
            if fig is not None and fig not in state.doc.figures:
                bus.decision("claims", f"{cid}: 없는 figure '{fig}' -> 해제",
                             claim_id=cid)
                fig = None

            seen.add(cid)
            claims.append(Claim(
                id=cid, text=text, evidence_span_ids=span_ids,
                assumptions=self._assumptions(c.get("assumptions")),
                figure_id=fig,
                confidence=self._confidence(c.get("confidence")),
                novelty_marker=any(m in text.lower() for m in NOVELTY_MARKERS),
            ))

        bus.decision("claims", f"후보 {len(raw)}개 중 {len(claims)}개 채택 "
                               f"(폐기 {len(raw) - len(claims)}개)",
                     proposed=len(raw), accepted=len(claims))
        return claims

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

    def _fallback(self, state: PaperState, bus: EventBus) -> list[dict]:
        """Keeps the mock DAG runnable without inventing anything: the
        number-densest spans are echoed verbatim as claim candidates, each
        bound to the span it was copied from."""
        ranked = sorted(
            (
                (len(NUM_RE.findall(sp.text)), i, sid, sp)
                for i, (sid, sp) in enumerate(state.doc.spans.items())
                if sp.kind in ("paragraph", "equation") and len(sp.text) > 40
            ),
            key=lambda c: (-c[0], c[1]),
        )
        picked = [c for c in ranked if c[0]][:self.FALLBACK_CLAIMS]
        bus.decision("claims", "모델이 claims를 반환하지 않음 -> 수치 밀집 span을 "
                               "후보로 사용 (오프라인 경로)",
                     spans=[sid for _, _, sid, _ in picked])
        return [
            {"id": f"c{n + 1}", "text": sp.text[:200],
             "evidence_span_ids": [sid], "confidence": 0.6}
            for n, (_, _, sid, sp) in enumerate(picked)
        ]


class ScoreInteractions(Stage):
    """Cheap model or rules. Decides what NOT to visualise -- emit the
    rejections, they are the point."""

    name = "score"
    reads = ("claims", "number_pool")
    writes = ("scores",)
    budget_s = 2.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        for c in state.claims:
            grounded = any(
                n.span_id in c.evidence_span_ids for n in state.number_pool.values()
            )
            s = InteractionScore(
                claim_id=c.id,
                manipulability=0.8 if c.figure_id else 0.3,
                causal_clarity=0.7,
                learning_value=0.7,
                faithfulness=0.9 if grounded else 0.2,
                demo_reliability=0.8,
            )
            state.scores[c.id] = s
            bus.decision("scorer", f"{c.id} score={s.total:.2f}",
                         claim_id=c.id, grounded=grounded)
        if not any(s.total >= 0.5 for s in state.scores.values()):
            state.mode = "qualitative"
            bus.decision("scorer", "정량 재현 가능한 주장 없음 -> qualitative 모드")


class SelectClaim(Stage):
    """Choose one claim without pausing for human input.

    Selection is deliberately deterministic: highest interaction score wins,
    and Python's stable max keeps the source claim order for ties. The full
    pipeline can therefore complete from one initial document input.
    """

    name = "select"
    reads = ("claims", "scores")
    writes = ("selected_claim_id",)
    budget_s = 0.1

    def run(self, state: PaperState, bus: EventBus) -> None:
        candidates = [c for c in state.claims if c.id in state.scores]
        if not candidates:
            raise StageError("no scored claim to select")
        chosen = max(candidates, key=lambda c: state.scores[c.id].total)
        state.selected_claim_id = chosen.id
        bus.decision(
            "selector", f"{chosen.id}: 최고 interaction score로 자동 선택",
            claim_id=chosen.id, score=round(state.scores[chosen.id].total, 3),
            policy="highest_score_then_source_order",
            candidates=[c.id for c in candidates],
        )
        bus.emit_status(f"{chosen.id} 자동 선택")


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
    reads = ("doc", "claims", "number_pool", "selected_claim_id")
    writes = ("assumptions",)
    budget_s = 5.0

    KINDS = ("scope", "measurement", "generalization", "implementation")
    SOURCES = ("paper_explicit", "paper_implicit", "pedagogical")
    MAX_ASSUMPTIONS = 5
    #: no specific consequence fits in fewer characters than this -- the cheap
    #: deterministic stand-in for the prompt's ban on generic weakens_how.
    MIN_WEAKENS_CHARS = 20
    MAX_SPAN_CHARS = 600

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = self._selected(state)
        if claim is None:
            raise StageError("no claim to decompose")

        out = self.llm.structured(
            role="assumption_miner", prompt=self._render(claim, state),
            schema_hint="Assumption[]", bus=bus,
        )
        state.assumptions = self._accept(out.get("assumptions"), claim, state, bus)

        if not state.assumptions:
            # Refusing is right -- a claim with nothing to switch off has no
            # interaction in it -- but the dead end is this claim's, not the
            # paper's, so name the way out.
            alts = [c.id for c in state.claims if c.id != claim.id]
            bus.decision("assumptions",
                         f"{claim.id}: 꺼볼 수 있는 가정 없음 -> 다른 claim 권유",
                         claim_id=claim.id, alternatives=alts)
            raise StageError(f"no assumption survived for {claim.id}")
        bus.emit_status(f"가정 {len(state.assumptions)}개로 분해")

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
        lines = ["# claim", f"{claim.id} {claim.text}", "", "# evidence"]
        for sid in claim.evidence_span_ids:
            sp = state.doc.spans.get(sid)
            if sp is None:
                continue
            text = sp.text
            if len(text) > self.MAX_SPAN_CHARS:
                text = text[:self.MAX_SPAN_CHARS] + "…"
            lines.append(f"{sid} [{sp.kind}] {text}")

        lines += ["", "# numbers"]
        for n in state.number_pool.values():
            if n.span_id in claim.evidence_span_ids:
                lines.append(f"{n.id} {n.raw}  span={n.span_id}  {n.context}")

        lines += ["", "# stated conditions"]
        lines += claim.assumptions or ["(none stated)"]
        return "\n".join(lines)

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

            got = Assumption(
                id=aid, claim_id=claim.id, text=str(a.get("text") or "").strip(),
                kind=kind, source=source, weakens_how=why, span_id=span_id,
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

        bus.decision("assumptions", f"{claim.id}: 후보 {len(raw)}개 중 "
                                    f"{len(kept)}개 채택",
                     claim_id=claim.id, proposed=len(raw), accepted=len(kept))
        return kept


class VerifyExternal(Stage):
    """Four-lens evidence retrieval for the one claim the reader selected.

    Facets describe how we searched, never what the sources prove. Results are
    collected for inspection only: this stage does not aggregate a controversy
    verdict and DesignInteraction does not consume its output.
    """

    name = "external"
    reads = ("claims", "selected_claim_id")
    writes = ("external",)
    budget_s = 25.0
    degrade_to = None  # individual planner/search failures are handled inline

    FACETS: tuple[EvidenceFacet, ...] = (
        "support", "contradict", "boundary", "methodology"
    )
    FALLBACK_SUFFIXES = {
        "support": "independent replication validation supporting evidence",
        "contradict": "conflicting results non-replication contradictory evidence",
        "boundary": "limitations boundary conditions subgroup generalizability",
        "methodology": "methodology measurement bias study design critique",
    }
    STANCES = ("supports", "contradicts", "unclear")

    def __init__(self, llm: LLM, search: Search):
        self.llm = llm
        self.search = search

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = next(
            (c for c in state.claims if c.id == state.selected_claim_id), None
        )
        if claim is None:
            raise StageError("no selected claim to verify")

        queries = self._queries(claim, bus)
        evidence: list[Evidence] = []
        by_url: dict[str, Evidence] = {}
        stances_by_url: dict[str, set[str]] = {}
        counts: dict[str, int] = {}

        for facet in self.FACETS:
            query = queries[facet]
            try:
                raw_hits = self.search.query(q=query, bus=bus)
                if not isinstance(raw_hits, list):
                    raise TypeError(
                        f"search returned {type(raw_hits).__name__}, expected list"
                    )
            except Exception as e:  # noqa: BLE001 -- one lens must not stop four
                counts[facet] = 0
                bus.decision(
                    "verifier", f"{claim.id}/{facet}: 검색 실패",
                    claim_id=claim.id, facet=facet, query=query, hits=None,
                    status="failed", error=str(e),
                )
                continue

            hits = [hit for hit in raw_hits if isinstance(hit, dict)]
            counts[facet] = len(hits)
            status = "found" if hits else "empty"
            bus.decision(
                "verifier", f"{claim.id}/{facet}: 검색 결과 {len(hits)}건",
                claim_id=claim.id, facet=facet, query=query, hits=len(hits),
                status=status, dropped=len(raw_hits) - len(hits),
            )
            for hit in hits:
                self._merge_hit(
                    claim.id, facet, hit, evidence, by_url, stances_by_url
                )

        # Replace even on four empty/failed searches: stale evidence must not
        # survive a recheck and masquerade as the new result.
        state.external[claim.id] = evidence
        bus.decision(
            "verifier", f"{claim.id}: 네 갈래 외부 근거 {len(evidence)}건",
            claim_id=claim.id, counts=counts, evidence=len(evidence),
        )
        bus.emit_status(f"외부 근거 {len(evidence)}건 나열")

    def _queries(self, claim: Claim, bus: EventBus) -> dict[EvidenceFacet, str]:
        fallback = {
            facet: f'"{claim.text}" {self.FALLBACK_SUFFIXES[facet]}'
            for facet in self.FACETS
        }
        try:
            out = self.llm.structured(
                role="external_query_planner",
                prompt=f"# claim\n{claim.id} {claim.text}",
                schema_hint="ExternalQueries",
                bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- retrieval has a no-LLM path
            bus.decision(
                "verifier", f"{claim.id}: 쿼리 생성 실패 -> 템플릿 사용",
                claim_id=claim.id, status="fallback", error=str(e),
                facets=list(self.FACETS),
            )
            return fallback

        raw = out.get("queries") if isinstance(out, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        queries: dict[EvidenceFacet, str] = {}
        sources: dict[EvidenceFacet, str] = {}
        for facet in self.FACETS:
            value = raw.get(facet)
            if isinstance(value, str) and value.strip():
                queries[facet] = value.strip()
                sources[facet] = "llm"
            else:
                queries[facet] = fallback[facet]
                sources[facet] = "template"
                bus.decision(
                    "verifier", f"{claim.id}/{facet}: 쿼리 누락 -> 템플릿 보충",
                    claim_id=claim.id, facet=facet, status="fallback",
                )
        bus.decision(
            "verifier", f"{claim.id}: 외부 검색 쿼리 4개 확정",
            claim_id=claim.id, sources=sources,
        )
        return queries

    def _merge_hit(
        self, claim_id: str, facet: EvidenceFacet, hit: dict,
        evidence: list[Evidence], by_url: dict[str, Evidence],
        stances_by_url: dict[str, set[str]],
    ) -> None:
        url = str(hit.get("url") or "").strip()
        key = url.rstrip("/") if url else ""
        stance = hit.get("stance")
        stance = stance if stance in self.STANCES else "unclear"

        if key and key in by_url:
            item = by_url[key]
            if facet not in item.facets:
                item.facets.append(facet)
            if not item.title:
                item.title = str(hit.get("title") or "").strip()
            if not item.snippet:
                item.snippet = str(hit.get("snippet") or "").strip()
            stances = stances_by_url[key]
            stances.add(stance)
            decisive = stances - {"unclear"}
            item.stance = next(iter(decisive)) if len(decisive) == 1 else "unclear"
            return

        item = Evidence(
            claim_id=claim_id,
            title=str(hit.get("title") or "").strip(),
            url=url,
            snippet=str(hit.get("snippet") or "").strip(),
            stance=stance,
            id=f"ev_{claim_id}_{len(evidence)}",
            facets=[facet],
        )
        evidence.append(item)
        if key:
            by_url[key] = item
            stances_by_url[key] = {stance}


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
             "selected_claim_id")
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
        if self.PRIMITIVE not in self.primitives:
            raise StageError(f"'{self.PRIMITIVE}' is not registered in this "
                             f"domain pack: {list(self.primitives)}")
        claim = self._selected(state)
        if claim is None:
            raise StageError("nothing to design")
        if not state.assumptions:
            raise StageError("a switchboard with no switches")

        out = self.llm.structured(
            role="switchboard_designer", prompt=self._render(claim, state),
            schema_hint="Switchboard", bus=bus,
        )

        state.spec = InteractionSpec(
            claim_id=claim.id,
            primitive=self.PRIMITIVE,
            title=out.get("title") or claim.text[:60],
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
        lines = ["# claim", f"{claim.id} {claim.text}", "", "# assumptions"]
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

            because = str(r.get("because") or "").strip()
            if not because:
                bus.decision("designer", f"{aid}: because 없음 -> 폐기",
                             assumption_id=aid)
                continue

            rules[aid] = StatusRule(
                assumption_id=aid, status=status, because=because,
                attribution=self._attribution(
                    r.get("attribution"), aid, state, evidence_ids, bus),
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
            if span_id in state.doc.spans:
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
            )
        return out


class Critic(Stage):
    """Deterministic prechecks first, LLM only for the soft stuff. An
    ungrounded number is caught by code in microseconds -- do not ask a model."""

    name = "critic"
    reads = ("spec", "number_pool")
    writes = ("verdict",)
    budget_s = 4.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        from ..critic_rules import precheck

        spec = state.spec
        if spec is None:
            raise StageError("no spec to check")
        violations = list(precheck(spec, state))
        for v in violations:
            bus.decision("critic", f"{v.code}: {v.detail}", fatal=v.fatal)
        from ..state import CriticVerdict

        fatal = [v for v in violations if v.fatal]
        state.verdict = CriticVerdict(
            result="REVISE" if fatal else "PASS", violations=violations
        )
        bus.emit_status("정확성 검사 " + ("재설계 필요" if fatal else "통과"))


class Render(Stage):
    """Deterministic. Schema -> artifact payload. Frontend owns the pixels."""

    name = "render"
    reads = ("spec", "verdict", "mode")
    writes = ("artifact",)
    budget_s = 1.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        spec = state.spec
        assert spec is not None
        state.artifact = {
            "primitive": spec.primitive,
            "mode": state.mode,
            "title": spec.title,
            "controls": [c.__dict__ for c in spec.controls],
            "explanation": spec.explanation.get(state.profile.level, ""),
            "warning": spec.fidelity_warning,
            # the frontend evaluates these itself on every toggle -- shipping
            # the table is what keeps invariant 6 payable
            "base_status": spec.base_status,
            "status_rules": [
                {**r.__dict__, "attribution": r.attribution.__dict__}
                for r in spec.status_rules
            ],
            "assumptions": [a.__dict__ for a in state.assumptions],
            "sources": {
                "paper": spec.claim_id,
                "external": len(state.external.get(spec.claim_id, [])),
            },
        }
        bus.emit_status("playground 준비 완료")
