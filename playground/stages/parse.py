"""PDF and plain-text parsing into the span index and number pool."""

from __future__ import annotations

import re
from math import ceil

import pymupdf

from ..events import EventBus
from ..state import (
    DocGraph,
    NumberFact,
    PaperState,
    Span,
)
from .base import (
    Stage,
    StageError,
)
from .text import NUM_RE


class Parse(Stage):
    """Deterministic optional source enrichment.

    A run may start with a manually supplied claim. In that case a synthetic
    manual span keeps the claim bound and the PDF parser is simply skipped.
    When a PDF is present, the old text-layer parser remains unchanged and can
    enrich an explicit claim without becoming the input contract.
    """

    name = "parse"
    reads = ("source_path", "source_text", "source_title", "claim_text")
    writes = ("doc", "number_pool", "source_title")
    budget_s = 8.0

    #: a block opening with this is a figure/table caption
    CAPTION_RE = re.compile(r"^(fig(?:ure)?|table|tbl)\.?\s*(\d+)", re.I)
    HEADING_RE = re.compile(
        r"^(?:\d+(?:\.\d+)*[.)]?\s*)?"
        r"(abstract|introduction|background|methods?|materials\s+(?:and|&)\s+methods?|"
        r"experimental\s+procedures?|results?|discussion|conclusion|references?|bibliography|"
        r"acknowledg(?:e)?ments?)\s*[:.]?\s*$",
        re.I,
    )
    NUMBERED_HEADING_RE = re.compile(
        r"^\d+(?:\.\d+)*[.)]?\s+(.{2,80}?)\s*[:.]?$", re.I
    )
    #: units that precede their number in medical prose ("AUC 0.87", "HR 0.62")
    LEADING_UNIT_RE = re.compile(r"\b(AUC|HR|OR)\s*[:=]?\s*$", re.I)

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim_text = (state.claim_text or "").strip()
        source_text = (state.source_text or "").strip()
        if source_text:
            spans: dict[str, Span] = {}
            section: str = "abstract"
            for index, block in enumerate(re.split(r"\n\s*\n", source_text)):
                text = " ".join(block.split())
                if not text:
                    continue
                heading = self._section_heading(text)
                if heading:
                    section = heading
                    if self._is_exact_heading(text):
                        continue
                prefix = self._section_prefix(text, section)
                if prefix:
                    section = prefix
                sid = f"text_b{index}"
                spans[sid] = Span(sid, 0, self._classify(text), text,
                                   section=section)  # type: ignore[arg-type]
            if claim_text:
                spans["input_claim"] = Span(
                    "input_claim", 0, "paragraph", claim_text, origin="manual"
                )
            state.doc = DocGraph(
                spans=spans,
                sections=self._section_quality(spans, pages=1, bus=bus),
            )
            if not state.source_title:
                state.source_title = self._infer_title(spans)
            state.number_pool = self._index_numbers(spans)
            bus.decision(
                "parse", "source_text 정규화 완료",
                input_kind="text", spans=len(spans), numbers=len(state.number_pool),
                title=state.source_title,
            )
            bus.emit_status("텍스트 원문 색인 완료")
            return
        if not state.source_path:
            if not claim_text:
                raise StageError("no claim text or PDF source provided")
            state.doc = DocGraph(spans={
                "input_claim": Span(
                    "input_claim", 0, "paragraph", claim_text, origin="manual"
                )
            })
            state.number_pool = {}
            bus.decision(
                "parse", "수동 claim 입력 -> PDF parsing 생략",
                input_kind="claim", span_id="input_claim",
            )
            bus.emit_status("수동 claim 입력 준비 완료")
            return

        call_id = bus.tool_call("pdf.extract", path=state.source_path)
        try:
            doc = pymupdf.open(state.source_path)
        except Exception as e:  # noqa: BLE001 -- missing/corrupt file
            bus.tool_result(call_id, None, error=str(e))
            if claim_text:
                state.doc = DocGraph(spans={
                    "input_claim": Span(
                        "input_claim", 0, "paragraph", claim_text,
                        origin="manual",
                    )
                })
                state.number_pool = {}
                bus.decision(
                    "parse", "PDF enrichment 실패 -> 수동 claim만으로 계속",
                    input_kind="claim", error=str(e), span_id="input_claim",
                )
                bus.emit_status("수동 claim 입력 준비 완료 — PDF enrichment 생략")
                return
            raise StageError(f"cannot open {state.source_path}: {e}") from e

        # PyMuPDF can open an encrypted or truncated document far enough to
        # expose pages, then fail later while extracting blocks. Reject these
        # states up front so a partial parse cannot become a plausible claim.
        if getattr(doc, "needs_pass", False):
            doc.close()
            raise StageError("encrypted PDF requires a password")
        if getattr(doc, "is_repaired", False):
            doc.close()
            raise StageError("PDF is damaged or truncated")

        spans: dict[str, Span] = {}
        figures: dict[str, dict] = {}
        current_section: str = "abstract"
        try:
            for pno in range(doc.page_count):
                page = doc[pno]
                if pno > 0 and current_section == "abstract":
                    current_section = "results"
                rects, current_section = self._index_page(
                    page, pno, spans, current_section
                )
                self._index_figures(page, pno, spans, rects, figures)
            pages = doc.page_count
        finally:
            doc.close()

        if not spans:
            bus.tool_result(call_id, {"pages": pages, "spans": 0})
            if claim_text:
                state.doc = DocGraph(spans={
                    "input_claim": Span(
                        "input_claim", 0, "paragraph", claim_text,
                        origin="manual",
                    )
                })
                state.number_pool = {}
                bus.decision(
                    "parse", "PDF text layer 없음 -> 수동 claim만으로 계속",
                    input_kind="claim", span_id="input_claim",
                )
                bus.emit_status("수동 claim 입력 준비 완료 — PDF enrichment 생략")
                return
            raise StageError("no text layer -- scanned PDF?")

        section_quality = self._section_quality(spans, pages=pages, bus=bus)
        state.doc = DocGraph(
            spans=spans, figures=figures, sections=section_quality
        )
        if not state.source_title:
            state.source_title = self._infer_title(spans)
        if claim_text:
            state.doc.spans["input_claim"] = Span(
                "input_claim", 0, "paragraph", claim_text, origin="manual"
            )
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
                     kinds=kinds, claim_seed=bool(claim_text))

    # -- per-page indexing --

    def _index_page(self, page, pno: int, spans: dict,
                    current_section: str = "other") -> tuple[dict, str]:
        """Table cells first, then text blocks that fall outside any table.
        Returns span_id -> Rect for the text blocks, so figures can find their
        caption. Ids are position-derived, so a rerun on the same file
        reproduces them exactly."""
        rects: dict = {}
        if pno > 0 and current_section == "abstract":
            current_section = "results"
        boxes = []
        for ti, tab in enumerate(page.find_tables().tables):
            boxes.append(pymupdf.Rect(tab.bbox))
            for ri, row in enumerate(tab.extract()):
                for ci, cell in enumerate(row):
                    text = " ".join((cell or "").split())
                    if not text:
                        continue
                    sid = f"p{pno + 1}_t{ti}r{ri}c{ci}"
                    spans[sid] = Span(
                        sid, pno + 1, "table_cell", text,
                        section=current_section,  # type: ignore[arg-type]
                    )

        for bi, b in enumerate(page.get_text("blocks", sort=True)):
            if b[6] != 0:  # image block; geometry comes from get_image_info
                continue
            text = " ".join(b[4].split())
            if not text:
                continue
            heading = self._section_heading(text)
            if heading:
                current_section = heading
                if self._is_exact_heading(text):
                    continue
            prefix = self._section_prefix(text, current_section)
            if prefix:
                current_section = prefix
            rect = pymupdf.Rect(b[:4])
            area = rect.get_area()
            if area and any((rect & bx).get_area() > 0.5 * area for bx in boxes):
                continue  # already captured as table cells
            sid = f"p{pno + 1}_b{bi}"
            spans[sid] = Span(
                sid, pno + 1, self._classify(text), text,
                section=current_section,  # type: ignore[arg-type]
            )
            rects[sid] = rect
        return rects, current_section

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

    @classmethod
    def _section_heading(cls, text: str) -> str | None:
        normalized = " ".join(text.split())
        match = cls.HEADING_RE.match(normalized)
        if not match:
            numbered = cls.NUMBERED_HEADING_RE.match(normalized)
            if not numbered:
                return None
            heading = numbered.group(1).lower()
            if "definition" in heading:
                return "intro"
            if "observing miscalibration" in heading or re.fullmatch(r"results?", heading):
                return "results"
            if "method" in heading or "calibrating" in heading:
                return "methods"
            if "related work" in heading:
                return "other"
            if "discussion" in heading or "conclusion" in heading:
                return "discussion"
            if "reference" in heading or "bibliography" in heading:
                return "references"
            if "acknowledg" in heading:
                return "acknowledgments"
            return None
        value = match.group(1).lower().replace("&", "and")
        if value.startswith("abstract"):
            return "abstract"
        if value.startswith(("intro", "background")):
            return "intro"
        if value.startswith(("method", "material", "experimental")):
            return "methods"
        if value.startswith("result"):
            return "results"
        if value.startswith(("discussion", "conclusion")):
            return "discussion"
        if value.startswith(("reference", "bibliography")):
            return "references"
        if value.startswith("acknow"):
            return "acknowledgments"
        return "other"

    @classmethod
    def _section_prefix(cls, text: str, current: str) -> str | None:
        normalized = " ".join(text.split())
        lowered = normalized.lower()
        if re.match(r"^(?:online content\s+any\s+)?methods?\b", lowered):
            return "methods"
        if re.match(r"^(?:materials\s+(?:and|&)\s+methods|experimental procedures?)\b", lowered):
            return "methods"
        if re.match(r"^(?:acknowledg(?:e)?ments|reviewer information|author contributions|competing interests)\b", lowered):
            return "acknowledgments"
        if re.match(r"^(?:references?|bibliography)\b", lowered):
            return "references"
        # Numbered lists are common in Results/Methods (datasets, cohorts,
        # benchmark splits). They are not bibliography entries. References
        # are switched by an explicit heading; accepting any ``1. Name ...
        # year`` block here caused Guo's dataset list to turn the following
        # results paragraphs into ``references``.
        return None

    @classmethod
    def _is_exact_heading(cls, text: str) -> bool:
        return cls._section_heading(text) is not None

    @staticmethod
    def _section_quality(spans: dict[str, Span], pages: int,
                         bus: EventBus) -> list[dict]:
        """Fail closed when a carried intro label leaks into the paper tail."""
        cutoff = max(2, ceil(pages * 0.45))
        late_intro = sorted({
            span.page for span in spans.values()
            if span.origin == "paper" and span.section == "intro"
            and span.page > cutoff
        })
        if late_intro:
            for span in spans.values():
                if span.origin == "paper" and span.section not in {
                    "abstract", "references", "acknowledgments"
                }:
                    span.section = "other"
            bus.decision(
                "parse",
                "후반부 intro 감지 -> section 분류 불신, claim 후보를 abstract로 제한",
                pages=pages, cutoff=cutoff, late_intro_pages=late_intro,
            )
            return [{
                "claim_sections_reliable": False,
                "reason": "late_intro",
                "late_intro_pages": late_intro,
                "claim_candidate_sections": ["abstract"],
            }]
        return [{
            "claim_sections_reliable": True,
            "claim_candidate_sections": ["abstract", "intro", "results", "discussion"],
        }]

    @staticmethod
    def _infer_title(spans: dict) -> str | None:
        candidates = []
        for span in spans.values():
            # PDF pages are 1-based; source_text spans use page 0. Both can
            # provide a title when the caller omits source_title.
            if span.page not in (0, 1) or span.section != "abstract" or span.kind != "paragraph":
                continue
            text = span.text.strip()
            lowered = text.lower()
            if not 20 <= len(text) <= 220:
                continue
            if lowered.startswith(("http", "letter", "research")):
                continue
            if any(token in lowered for token in ("department", "graduate school", "these authors")):
                continue
            candidates.append(text)
        return candidates[0] if candidates else None

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
