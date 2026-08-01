"""The seven stages. Every one of these is a stub with the right shape --
Claude Code fills the bodies. Do not change the reads/writes tuples without
updating the recompute levels in pipeline.py.
"""

from __future__ import annotations

import re

import pymupdf

from ..clients import LLM, Search
from ..events import EventBus
from ..state import (
    Claim,
    Control,
    DocGraph,
    Evidence,
    InteractionScore,
    InteractionSpec,
    NumberFact,
    PaperState,
    Span,
    SpecNumber,
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
    importance judgement and prose generation never share a context."""

    name = "claims"
    reads = ("doc",)
    writes = ("claims",)
    budget_s = 6.0

    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        out = self.llm.structured(
            role="claim_mapper",
            prompt=f"spans={list(state.doc.spans)}",
            schema_hint="Claim[]",
            bus=bus,
        )
        raw = out.get("claims") or [
            {"id": "c1", "text": "The method is the first to reach AUC 0.87",
             "evidence_span_ids": ["p1_abs"], "figure_id": "fig4",
             "confidence": 0.8},
        ]
        state.claims = [
            Claim(
                id=c["id"], text=c["text"],
                evidence_span_ids=c.get("evidence_span_ids", []),
                assumptions=c.get("assumptions", []),
                figure_id=c.get("figure_id"),
                confidence=c.get("confidence", 0.5),
                novelty_marker=any(m in c["text"].lower() for m in NOVELTY_MARKERS),
            )
            for c in raw
        ]
        if not state.claims:
            raise StageError("no claims extracted")
        bus.emit_status(f"핵심 주장 {len(state.claims)}개 추출")


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


class VerifyExternal(Stage):
    """Conditional. Search is expensive; the trigger rule is explicit and the
    decision not to search is logged."""

    name = "external"
    reads = ("claims",)
    writes = ("external",)
    budget_s = 5.0
    degrade_to = None  # failure here is non-fatal, handled inline

    def __init__(self, search: Search):
        self.search = search

    def _trigger(self, c: Claim) -> str | None:
        if c.novelty_marker:
            return "novelty_crosscheck"
        if c.confidence < 0.6:
            return "low_confidence_grounding"
        return None

    def run(self, state: PaperState, bus: EventBus) -> None:
        for c in state.claims:
            reason = self._trigger(c)
            if not reason:
                bus.decision("verifier", f"{c.id}: 외부 검색 불필요", claim_id=c.id)
                continue
            try:
                hits = self.search.query(q=c.text, bus=bus)
            except Exception as e:  # noqa: BLE001
                bus.decision("verifier", f"{c.id}: 검색 실패, 미검증으로 진행",
                             error=str(e))
                continue
            state.external[c.id] = [
                Evidence(c.id, h.get("title", ""), h.get("url", ""),
                         h.get("snippet", ""), h.get("stance", "unclear"))
                for h in hits
            ]
            bus.decision("verifier", f"{c.id}: {reason}", claim_id=c.id,
                         hits=len(hits))


class DesignInteraction(Stage):
    """LLM. Emits a schema, never HTML. Free-form code generation is the single
    biggest live-demo risk."""

    name = "design"
    reads = ("claims", "scores", "external", "profile", "mode",
             "selected_claim_id")
    writes = ("spec",)
    budget_s = 6.0

    def __init__(self, llm: LLM, primitives: dict):
        self.llm = llm
        self.primitives = primitives

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.selected_claim_id:
            best = state.scores.get(state.selected_claim_id)
        else:
            best = max(state.scores.values(), key=lambda s: s.total, default=None)
        if best is None:
            raise StageError("nothing to design")
        out = self.llm.structured(
            role="explainer_designer",
            prompt=f"claim={best.claim_id} level={state.profile.level} "
                   f"allowed={list(self.primitives)}",
            schema_hint="InteractionSpec",
            bus=bus,
        )
        prim = out.get("primitive") or next(iter(self.primitives))
        if prim not in self.primitives:
            bus.decision("designer", f"미등록 primitive '{prim}' -> annotated_figure")
            prim = "annotated_figure"
        state.spec = InteractionSpec(
            claim_id=best.claim_id,
            primitive=prim,
            title=out.get("title", "Untitled"),
            learning_goal=out.get("learning_goal", ""),
            misconception=out.get("misconception", ""),
            controls=[Control(**c) for c in out.get("controls", [])] or [
                Control(name="threshold", kind="slider", provenance="variable",
                        span_id="tab2_c3", min=0.3, max=0.7, default=0.5)
            ],
            numbers=[SpecNumber(**n) for n in out.get("numbers", [])],
            explanation=out.get("explanation", {}),
            fidelity_warning=out.get("fidelity_warning"),
        )
        bus.emit_status("인터랙션 설계 완료")


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
            "sources": {
                "paper": spec.claim_id,
                "external": len(state.external.get(spec.claim_id, [])),
            },
        }
        bus.emit_status("playground 준비 완료")
