"""Pipeline stages.

Do not change reads/writes tuples without updating the stage contract in
CLAUDE.md. Those declarations document the state dependencies of each stage.
"""

from __future__ import annotations

import re

import pymupdf

from ..clients import LLM, Search, Visualization
from ..events import EventBus
from ..state import (
    Assumption,
    Attribution,
    BottleneckSpec,
    Claim,
    ClaimAnalysis,
    Control,
    DocGraph,
    Evidence,
    EvidenceFacet,
    InteractionScore,
    InteractionSpec,
    ExplainerSpec,
    PanelSpec,
    NumberFact,
    PaperState,
    Span,
    StatusRule,
    Violation,
)
from .base import Stage, StageError

NOVELTY_MARKERS = ("first", "novel", "state-of-the-art", "unprecedented", "최초")
NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|AUC|HR|OR|ms|GB|x)?")


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
            state.doc = DocGraph(spans=spans)
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

        state.doc = DocGraph(spans=spans, figures=figures)
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
        match = cls.HEADING_RE.match(" ".join(text.split()))
        if not match:
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
        return bool(cls.HEADING_RE.match(" ".join(text.split())))

    @staticmethod
    def _infer_title(spans: dict) -> str | None:
        candidates = []
        for span in spans.values():
            if span.page != 1 or span.section != "abstract" or span.kind != "paragraph":
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


class ContextAnalyst(Stage):
    """One large-context semantic pass over the normalized source.

    This stage deliberately owns the expensive interpretation step: claim
    candidates, their internal relations, and an explainable mechanism are
    proposed together. Later stages only score/select and compose a user
    artifact; they do not repeatedly ask small models to rediscover the paper.
    The deterministic fallback is intentionally conservative and is bound to
    the real span index, so offline runs never invent a second paper.
    """

    name = "context"
    reads = ("doc", "number_pool", "source_title", "source_text", "claim_text")
    writes = ("context_analysis",)
    budget_s = 8.0
    MAX_PROMPT_CHARS = 42_000
    CLAIM_SECTIONS = {"abstract", "intro", "results", "discussion"}
    SIGNALS = (
        "calibrat", "confidence", "miscalibr", "temperature", "ece", "nll",
        "accuracy", "error", "reliability", "probabil",
    )

    def __init__(self, llm: LLM, search: Search | None = None):
        self.llm = llm
        self.search = search

    def run(self, state: PaperState, bus: EventBus) -> None:
        if not state.doc.spans:
            raise StageError("no spans for context analysis")
        if state.claim_text and not any(
            span.origin == "paper" for span in state.doc.spans.values()
        ):
            # A claim-only run may still be enriched with Scholar snippets.
            # They are external context, never paper spans, so BuildClaims
            # keeps the explicit claim as its root.
            external_context: list[dict] = []
            if self.search is not None:
                try:
                    external_context = self.search.query(q=state.claim_text, bus=bus)
                except StageError as exc:
                    bus.decision("context", "claim-only abstract 검색 실패 -> claim context 유지",
                                 error=type(exc).__name__)
            state.context_analysis = {
                "claims": [], "relations": [], "mechanisms": [],
                "bottleneck": {}, "assumptions": [],
                "quantitative_facts": [],
                "limitations": ["claim-only input has no paper context"],
                "external_context": external_context[:5],
                "source_refs": ["input_claim"],
            }
            bus.decision("context", "claim-only 입력 -> abstract/snippet context 보강",
                         external_results=len(external_context))
            bus.emit_status("claim-only context 준비 완료")
            return
        prompt = self._render_context(state)
        out = self.llm.structured(
            role="context_analyst", prompt=prompt,
            schema_hint="ContextAnalysis", bus=bus,
        )
        analysis = dict(out) if isinstance(out, dict) else {}
        raw_claims = analysis.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            fallback = self._fallback(state, bus)
            # Keep any valid model-level wording, but never let an incomplete
            # response replace the source-bound candidate set.
            fallback.update({
                key: value for key, value in analysis.items()
                if key in {"bottleneck", "mechanisms", "limitations"}
                and value
            })
            analysis = fallback
            bus.decision("context", "큰 context 분석 응답 없음 -> 원문 bound 분석 사용")
        else:
            analysis["claims"] = raw_claims[:8]
            analysis.setdefault("mechanisms", [])
            analysis.setdefault("limitations", [])
            bus.decision("context", "큰 context 분석 결과 채택",
                         proposed_claims=len(raw_claims))
        analysis = self._guard(state, analysis, bus)
        analysis["source_refs"] = self._source_refs(state, analysis)
        state.context_analysis = analysis
        bus.decision(
            "context", "source context semantic envelope 준비",
            claims=len(analysis.get("claims") or []),
            mechanisms=len(analysis.get("mechanisms") or []),
            bottleneck=bool(analysis.get("bottleneck")),
            source_refs=len(analysis["source_refs"]),
        )
        bus.emit_status("원문 context 분석 완료")

    def _guard(self, state: PaperState, analysis: dict, bus: EventBus) -> dict:
        """Keep model-proposed references inside the parsed source boundary."""
        valid = set(state.doc.spans)
        claim_sections = self.CLAIM_SECTIONS
        allowed = {
            sid for sid, span in state.doc.spans.items()
            if span.origin == "paper" and span.section in claim_sections
        }
        out = dict(analysis)
        bottleneck = out.get("bottleneck")
        if isinstance(bottleneck, dict):
            refs = [str(sid) for sid in bottleneck.get("evidence_refs") or []]
            kept = [sid for sid in refs if sid in valid and sid in allowed]
            if refs and not kept:
                bus.decision("context", "bottleneck evidence ref가 source 경계 밖 -> 제거")
            out["bottleneck"] = {**bottleneck, "evidence_refs": kept}
        mechanisms = []
        for mechanism in out.get("mechanisms") or []:
            if not isinstance(mechanism, dict):
                continue
            refs = [str(sid) for sid in (
                mechanism.get("evidence_refs") or mechanism.get("source_refs") or []
            )]
            kept = [sid for sid in refs if sid in valid and sid in allowed]
            if refs and not kept:
                bus.decision("context", "mechanism evidence ref가 source 경계 밖 -> 폐기")
                continue
            mechanisms.append({**mechanism, "evidence_refs": kept})
        out["mechanisms"] = mechanisms
        facts = [str(fid) for fid in out.get("quantitative_facts") or []]
        out["quantitative_facts"] = [fid for fid in facts if fid in state.number_pool]
        return out

    def _render_context(self, state: PaperState) -> str:
        lines = [
            "# task",
            "Analyze the source as one grounded context. Return structured claims,",
            "relations, mechanism candidates, bottleneck candidates, assumptions,",
            "and evidence span ids. Never use references or acknowledgments as claims.",
            "# source title", state.source_title or "(unknown)",
            "# explicit user claim", state.claim_text or "(none)",
            "# source spans",
        ]
        used = sum(len(line) + 1 for line in lines)
        for sid, span in state.doc.spans.items():
            if span.origin != "paper" or span.section not in self.CLAIM_SECTIONS:
                continue
            # Table extraction frequently produces chart tick fragments. The
            # semantic pass sees captions and prose, not pixel-shaped cells.
            if span.kind == "table_cell":
                continue
            text = span.text[:900]
            line = f"{sid} [{span.kind} section={span.section}] {text}"
            if used + len(line) + 1 > self.MAX_PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        lines.append("# number pool")
        for fact in list(state.number_pool.values())[:220]:
            if fact.span_id in state.doc.spans:
                line = f"{fact.id} span={fact.span_id} {fact.raw} {fact.context}"
                if used + len(line) + 1 > self.MAX_PROMPT_CHARS:
                    break
                lines.append(line)
                used += len(line) + 1
        return "\n".join(lines)

    def _fallback(self, state: PaperState, bus: EventBus) -> dict:
        candidates = []
        for index, (sid, span) in enumerate(state.doc.spans.items()):
            if not self._candidate(span, state):
                continue
            lowered = span.text.lower()
            signal = sum(lowered.count(token) for token in self.SIGNALS)
            numbers = len(NUM_RE.findall(span.text))
            section_rank = {"abstract": 0, "results": 1, "discussion": 2,
                            "intro": 3}.get(span.section, 9)
            candidates.append((-signal, section_rank, -numbers, -len(span.text),
                               index, sid, span))
        candidates.sort(key=lambda item: item[:5])
        # The abstract is the thesis anchor. It must become the graph root
        # even when a later results paragraph contains more keywords/numbers.
        abstract = [item for item in candidates if item[6].section == "abstract"]
        if abstract:
            root = abstract[0]
            picked = [root] + [item for item in candidates if item is not root][:5]
        else:
            picked = candidates[:6]
        claims = []
        for order, item in enumerate(picked):
            _, _, _, _, _, sid, span = item
            claims.append({
                "id": f"c{order + 1}",
                "text": span.text[:700],
                "evidence_span_ids": [sid],
                "parent_id": None if order == 0 else "c1",
                "role": "result" if order == 0 else "subclaim",
                "order": order,
                "confidence": 0.72 if span.section == "abstract" else 0.62,
                "difficulty": 0.55 + min(0.25, 0.03 * order),
                "pedagogical_gain": min(0.95, 0.65 + 0.04 * order),
                "support_type": "necessary" if order == 0 else "independent",
            })
        if not claims:
            raise StageError("context analysis found no source-bound claim candidate")

        corpus = " ".join(c["text"] for c in claims).lower()
        calibration = any(token in corpus for token in self.SIGNALS[:6])
        refs = [c["evidence_span_ids"][0] for c in claims[:4]]
        # Prefer the explicit temperature equation/prose when available.
        for sid, span in state.doc.spans.items():
            if sid not in refs and "temperature" in span.text.lower() \
                    and span.section in self.CLAIM_SECTIONS:
                refs.append(sid)
                break
        if calibration:
            question = "정확도는 좋아지는데 확률 예측 품질은 왜 나빠질 수 있을까?"
            kind = "calibration"
            mechanism = [{
                "kind": "calibration",
                "question": question,
                "evidence_refs": refs,
                "controls": ["temperature"],
                "observables": ["correctness", "confidence"],
            }]
            bottleneck = {
                "question": question,
                "why_hard": "정확도와 confidence가 서로 다른 성질이라는 점이 여러 정의·결과 문단에 나뉘어 있다.",
                "source_claim_ids": [claims[0]["id"]],
                "evidence_refs": refs,
                "mechanism_kind": kind,
                "candidate_controls": ["temperature"],
                "candidate_observables": ["correctness", "confidence"],
                "learning_payoff": 0.95,
                "data_sufficiency": "sufficient",
                "fidelity": "high",
            }
        else:
            mechanism = []
            bottleneck = {
                "question": "이 결과를 만드는 핵심 관계는 무엇일까?",
                "why_hard": "결과와 그 조건이 서로 다른 문단에 흩어져 있다.",
                "source_claim_ids": [claims[0]["id"]],
                "evidence_refs": refs,
                "mechanism_kind": "unknown",
                "candidate_controls": [],
                "candidate_observables": ["claim_support"],
                "learning_payoff": 0.55,
                "data_sufficiency": "partial",
                "fidelity": "medium",
            }
        bus.decision("context", "원문 기반 context 후보 생성",
                     claims=[c["id"] for c in claims], mechanism=kind if calibration else "unknown")
        return {
            "claims": claims,
            "relations": [],
            "mechanisms": mechanism,
            "bottleneck": bottleneck,
            "assumptions": [],
            "quantitative_facts": [
                fact.id for fact in state.number_pool.values()
                if fact.span_id in refs
            ],
            "limitations": ["figure pixels were not inspected"],
        }

    @staticmethod
    def _candidate(span: Span, state: PaperState) -> bool:
        if span.origin != "paper" or span.section not in ContextAnalyst.CLAIM_SECTIONS:
            return False
        if span.kind not in {"paragraph", "caption", "equation"}:
            return False
        text = " ".join(span.text.split())
        lowered = text.lower()
        if len(text) < 70 or text == (state.source_title or "").strip():
            return False
        excluded = (
            "graduate school", "department of", "correspondence to:",
            "equal contribution", "copyright", "proceedings of the",
            "all rights reserved", "author contributions", "received:",
            "accepted:", "published online", "https://doi.org/",
        )
        if any(marker in lowered for marker in excluded):
            return False
        if re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\s+\d+\s+[A-Z][a-z]+", text):
            return False
        return True

    @staticmethod
    def _source_refs(state: PaperState, analysis: dict) -> list[str]:
        refs: list[str] = []
        for claim in analysis.get("claims") or []:
            if isinstance(claim, dict):
                refs.extend(str(s) for s in claim.get("evidence_span_ids") or [])
        bottleneck = analysis.get("bottleneck")
        if isinstance(bottleneck, dict):
            refs.extend(str(s) for s in bottleneck.get("evidence_refs") or [])
        return list(dict.fromkeys(sid for sid in refs if sid in state.doc.spans))


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
            if sp.origin == "paper" and sp.section not in self.CLAIM_SECTIONS:
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
                and state.doc.spans[sid].section not in self.CLAIM_SECTIONS
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
                and not self._is_claim_candidate(state.doc.spans[sid])
            ]
            if nonclaim:
                bus.decision(
                    "claims", f"{cid}: 저자/메타데이터 span -> claim 근거에서 제거",
                    claim_id=cid, spans=nonclaim,
                )
                span_ids = [sid for sid in span_ids if sid not in nonclaim]
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
        """Keeps the mock DAG runnable without inventing anything: the
        number-densest spans are echoed verbatim as claim candidates, each
        bound to the span it was copied from."""
        section_rank = {"abstract": 0, "intro": 1, "results": 2, "discussion": 3}
        ranked = sorted(
            (
                (section_rank.get(sp.section, 9), -len(NUM_RE.findall(sp.text)), i, sid, sp)
                for i, (sid, sp) in enumerate(state.doc.spans.items())
                if sp.origin == "paper"
                and sp.section in self.CLAIM_SECTIONS
                and sp.kind in ("paragraph", "caption", "equation")
                and len(sp.text) > 40
                and self._is_claim_candidate(sp)
            ),
            key=lambda c: (c[0], c[1], c[2]),
        )
        picked = [c for c in ranked if c[1] < 0][:self.FALLBACK_CLAIMS]
        if not picked:
            textual = [
                (0, 0, i, sid, sp)
                for i, (sid, sp) in enumerate(state.doc.spans.items())
                if sp.origin == "paper" and sp.section == "abstract"
                and sp.kind in ("paragraph", "caption", "equation")
                and len(sp.text) > 40
                and self._is_claim_candidate(sp)
            ]
            picked = textual[:self.FALLBACK_CLAIMS]
            bus.decision("claims", "정량 후보 없음 -> abstract 전용 후보로 재시도",
                         spans=[sid for _, _, _, sid, _ in picked])
        picked.sort(key=lambda c: c[2])
        bus.decision("claims", "모델이 claims를 반환하지 않음 -> 수치 밀집 span을 "
                               "후보로 사용 (오프라인 경로)",
                     spans=[sid for *_, sid, _ in picked])
        claims = [
            {"id": f"c{n + 1}", "text": sp.text[:200],
             "evidence_span_ids": [sid], "confidence": 0.6}
            for n, (*_, sid, sp) in enumerate(picked)
        ]
        return claims, None

    @staticmethod
    def _is_claim_candidate(span: Span) -> bool:
        text = span.text.strip().lower()
        excluded = (
            "graduate school", "department of", "these authors", "author contributions",
            "correspondence", "copyright", "all rights reserved", "received:",
            "accepted:", "published online", "https://doi.org/", "reviewer information",
            "nature |", "vol ",
        )
        if any(marker in text for marker in excluded):
            return False
        # Superscript-heavy author blocks look numeric but contain no claim
        # predicate. Detect repeated ``Name1,8`` citation markers instead of
        # rejecting all comma-heavy scientific prose.
        author_markers = re.findall(
            r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\d+(?:,\d+)+",
            span.text,
        )
        if len(author_markers) >= 2:
            return False
        return True


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


class BottleneckMiner(Stage):
    """Select exactly one teachable bottleneck from the chosen frontier.

    This first implementation is deliberately deterministic. It uses the
    existing claim/span graph as the evidence boundary and leaves model-based
    wording to the optional editorial stage.
    """

    name = "bottleneck"
    reads = ("selected_claim_id", "claims", "doc", "source_path", "source_text",
             "context_analysis")
    writes = ("bottleneck",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = next((c for c in state.claims if c.id == state.selected_claim_id), None)
        if claim is None:
            raise StageError("no claim for bottleneck mining")
        refs = list(claim.evidence_span_ids)
        text = " ".join(
            state.doc.spans[sid].text for sid in refs if sid in state.doc.spans
        )
        context_bottleneck = (state.context_analysis or {}).get("bottleneck")
        if isinstance(context_bottleneck, dict) \
                and str(context_bottleneck.get("question") or "").strip():
            valid_refs = [
                str(sid) for sid in context_bottleneck.get("evidence_refs") or refs
                if str(sid) in state.doc.spans
            ]
            known_claims = {c.id for c in state.claims}
            source_claim_ids = [
                str(cid) for cid in context_bottleneck.get("source_claim_ids") or []
                if str(cid) in known_claims
            ] or [claim.id]
            state.bottleneck = BottleneckSpec(
                question=str(context_bottleneck["question"]).strip(),
                why_hard=str(context_bottleneck.get("why_hard") or "").strip(),
                source_claim_ids=source_claim_ids,
                evidence_refs=list(dict.fromkeys(valid_refs or refs)),
                mechanism_kind=str(context_bottleneck.get("mechanism_kind") or "unknown"),
                candidate_controls=[str(v) for v in context_bottleneck.get("candidate_controls") or []],
                candidate_observables=[str(v) for v in context_bottleneck.get("candidate_observables") or []],
                learning_payoff=self._number(context_bottleneck.get("learning_payoff"), 0.5),
                data_sufficiency=(str(context_bottleneck.get("data_sufficiency") or "partial")
                                  if str(context_bottleneck.get("data_sufficiency") or "partial")
                                  in {"sufficient", "partial", "insufficient"} else "partial"),
                fidelity=(str(context_bottleneck.get("fidelity") or "medium")
                          if str(context_bottleneck.get("fidelity") or "medium")
                          in {"high", "medium", "low"} else "medium"),
            )
            bus.decision("bottleneck", "context analysis의 병목 사용",
                         question=state.bottleneck.question,
                         mechanism_kind=state.bottleneck.mechanism_kind)
            return
        # Do not let a keyword in a bibliography entry route an unrelated
        # paper into the ML calibration explainer. For PDFs, only the title/
        # abstract pages may influence primitive routing; the selected claim
        # remains the primary signal. Plain text has no page boundary.
        document_context = " ".join(
            span.text for span in state.doc.spans.values()
            if span.page == 0 or span.page <= 2
        )
        corpus = f"{claim.text} {text} {document_context}".lower()
        calibration = bool(re.search(
            r"\bcalibrat\w*\b|\bconfidence\b|\btemperature\s+scaling\b|\bece\b|\bnll\b",
            corpus,
            flags=re.I,
        ))
        explicit_deltas = re.findall(
            r"(?:ablation|component)\s*[:：-]?\s*([A-Za-z][\w -]{1,32})\s*(?:delta|drop|change)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*%?",
            corpus,
            flags=re.I,
        )
        state.ablation_components = [
            {"component": name.strip(), "delta": float(value)}
            for name, value in explicit_deltas
        ]
        ablation = bool(state.ablation_components)
        if ablation:
            question = "구성 요소를 하나씩 빼면 결과가 어떻게 달라질까?"
            kind = "ablation"
            controls = ["component"]
            observables = ["metric_delta"]
            payoff = 0.9
            fidelity = "high"
        elif calibration:
            question = "정확도는 좋아지는데 확률 예측 품질은 왜 나빠질 수 있을까?"
            kind = "calibration"
            controls = ["temperature"]
            observables = ["correctness", "confidence"]
            payoff = 0.95
            fidelity = "high"
        else:
            question = "이 주장이 성립하려면 무엇이 함께 맞아야 할까?"
            kind = "claim_conditions"
            controls = []
            observables = ["claim_support"]
            payoff = 0.55
            fidelity = "medium"
        if self.llm:
            try:
                out = self.llm.structured(
                    role="bottleneck_miner",
                    prompt=f"# claim\n{claim.text}\n# evidence\n{text[:8000]}",
                    schema_hint="BottleneckSpec", bus=bus,
                )
                if isinstance(out, dict) and str(out.get("question") or "").strip():
                    question = str(out["question"]).strip()
                if isinstance(out, dict) and str(out.get("mechanism_kind") or "") in {"calibration", "ablation", "claim_conditions"}:
                    kind = str(out["mechanism_kind"])
            except Exception as exc:  # noqa: BLE001 - deterministic fallback is safe
                bus.decision("bottleneck", "모델 병목 제안 실패 -> 규칙 기반 병목 사용",
                             error=type(exc).__name__)
        state.bottleneck = BottleneckSpec(
            question=question,
            why_hard="논문은 결과를 한 문장으로 압축하지만, 그 결과가 만들어지는 조건과 메커니즘은 여러 문단에 흩어져 있다.",
            source_claim_ids=[claim.id],
            evidence_refs=refs,
            mechanism_kind=kind,
            candidate_controls=controls,
            candidate_observables=observables,
            learning_payoff=payoff,
            data_sufficiency="sufficient" if state.source_path or state.source_text else "partial",
            fidelity=fidelity,
        )
        bus.decision("bottleneck", "병목 1개 선택", question=question,
                     claim_id=claim.id, mechanism_kind=kind)

    @staticmethod
    def _number(value, default: float) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return default


class PrimitiveRouter(Stage):
    """Choose a whitelisted primitive using only available evidence."""

    name = "router"
    reads = ("bottleneck", "doc", "source_path", "source_text", "context_analysis")
    writes = ("explainer_route",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        bottleneck = state.bottleneck
        if bottleneck is None:
            raise StageError("bottleneck missing")
        if bottleneck.mechanism_kind == "ablation" and state.ablation_components:
            state.explainer_route = "ablation_explainer"
        elif bottleneck.mechanism_kind == "calibration" and (state.source_path or state.source_text):
            state.explainer_route = "calibration_explainer"
        elif (state.context_analysis or {}).get("mechanisms"):
            # General mechanisms use a schematic composer. The graph is only
            # the evidence boundary; it does not become a set of controls.
            state.explainer_route = "mechanism_explainer"
        else:
            state.explainer_route = "assumption_switchboard"
        if self.llm:
            try:
                out = self.llm.structured(
                    role="primitive_router",
                    prompt=f"mechanism={bottleneck.mechanism_kind}\nsource={bool(state.source_path or state.source_text)}",
                    schema_hint="PrimitiveRoute", bus=bus,
                )
                route = str(out.get("route") or "") if isinstance(out, dict) else ""
                if route == "assumption_switchboard":
                    state.explainer_route = route
                elif route in {"scaling_comparison", "generated_schematic"} \
                        and state.explainer_route == "calibration_explainer":
                    # Both routes are rendered by the calibration explainer
                    # composer; keep the bounded two-panel composition.
                    pass
            except Exception as exc:  # noqa: BLE001
                bus.decision("router", "모델 route 실패 -> 규칙 기반 route 사용",
                             error=type(exc).__name__)
        bus.decision("router", "허용 primitive 선택", route=state.explainer_route,
                     max_panels=3, source_figure_vision=False)


class PanelComposer(Stage):
    """Compose a bounded, declarative artifact; never emits executable code."""

    name = "panels"
    reads = ("bottleneck", "explainer_route", "claims", "doc", "number_pool",
             "source_title", "source_path", "source_text", "context_analysis")
    writes = ("explainer", "spec")
    budget_s = 0.2

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    WARNING = "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다."

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.explainer_route not in {
            "calibration_explainer", "ablation_explainer", "mechanism_explainer"
        }:
            bus.decision("panels", "자료가 부족해 기존 switchboard 경로 유지")
            return
        bottleneck = state.bottleneck
        assert bottleneck is not None
        claim = next(c for c in state.claims if c.id == bottleneck.source_claim_ids[0])
        refs = list(dict.fromkeys(bottleneck.evidence_refs))
        source_refs = [
            {"span_id": sid, "page": state.doc.spans[sid].page,
             "kind": state.doc.spans[sid].kind}
            for sid in refs if sid in state.doc.spans
        ]
        if state.explainer_route == "ablation_explainer":
            panel = PanelSpec(
                primitive="ablation_toggle",
                question="component을 끄면 측정된 지표가 얼마나 변할까?",
                model={
                    "type": "lookup_series",
                    "deltas": [
                        {"component": item["component"], "delta": item["delta"]}
                        for item in state.ablation_components[:5]
                    ],
                },
                controls=[{"name": "component", "kind": "toggle", "provenance": "measured"}],
                observables=[{"name": "metric_delta", "label": "측정된 변화량"}],
                feedback={"default": "각 막대는 원문에 적힌 component별 변화량입니다."},
                provenance=[{
                    "kind": "component_delta", "provenance": "measured",
                    "precision": "approximate", "source_refs": refs,
                }],
            )
            panels = [panel]
        elif state.explainer_route == "mechanism_explainer":
            mechanism = ((state.context_analysis or {}).get("mechanisms") or [{}])[0]
            relation = mechanism.get("relations") if isinstance(mechanism, dict) else None
            panel = PanelSpec(
                primitive="generated_schematic",
                question=str((mechanism or {}).get("question")
                             or bottleneck.question),
                model={
                    "type": "relation_graph",
                    "relations": relation or [],
                    "entities": (mechanism or {}).get("entities", []),
                },
                observables=(mechanism or {}).get("observables", []),
                feedback={
                    "default": "이 도식은 원문에서 확인된 관계를 설명용으로 재구성합니다."
                },
                provenance=[{
                    "kind": "mechanism_relation",
                    "provenance": "source_stated",
                    "precision": "qualitative",
                    "source_refs": refs,
                }],
                notice=self.WARNING,
            )
            panels = [panel]
        else:
            panels = [
            PanelSpec(
                primitive="generated_schematic",
                question="정답 여부와 확신의 정도는 같은 값일까?",
                model={
                    "type": "state_graph",
                    "nodes": ["예측", "정답 여부", "confidence"],
                    "edges": [["예측", "정답 여부"], ["예측", "confidence"]],
                },
                observables=[
                    {"name": "correctness", "label": "맞혔는가"},
                    {"name": "confidence", "label": "얼마나 확신했는가"},
                ],
                feedback={"default": "맞힌 비율과 확신이 잘 맞는지는 별도로 확인해야 합니다."},
                provenance=[{
                    "kind": "caption_direction", "provenance": "source_stated",
                    "precision": "qualitative", "source_refs": refs,
                }],
                notice=self.WARNING,
            ),
            PanelSpec(
                primitive="scaling_comparison",
                question="temperature T를 바꾸면 confidence가 어떻게 달라질까?",
                model={
                    "type": "formula",
                    "expression": "softmax(logits / T)",
                    "parameters": {"T": {"min": 0.5, "max": 5.0, "default": 1.0}},
                    "allowed_ops": ["+", "-", "*", "/", "pow", "min", "max", "log"],
                },
                controls=[{"name": "T", "kind": "slider", "min": 0.5, "max": 5.0, "default": 1.0,
                           "provenance": "illustrative", "precision": "qualitative"}],
                observables=[{"name": "confidence", "label": "confidence"}],
                feedback={
                    "low": "T가 작아지면 분포가 뾰족해져 확신이 커집니다.",
                    "high": "T가 커지면 분포가 평평해져 과한 확신을 누그러뜨립니다.",
                },
                provenance=[{
                    "kind": "formula", "provenance": "source_stated",
                    "precision": "exact", "source_refs": refs,
                }],
                notice="수식에 따른 설명용 모델입니다. 원문 곡선을 재생하지 않습니다.",
            ),
            ]
        # Panel layout is deterministic from the locked mechanism and
        # provenance. A second unconstrained panel-planning call would merely
        # rediscover the context pass and could reintroduce unsupported data.
        state.explainer = ExplainerSpec(
            title=state.source_title or claim.text[:80],
            thesis=claim.text,
            bottleneck=bottleneck,
            panels=panels,
            comparison={"available": False, "reason": "figure 픽셀 수치는 자동 복원하지 않음"},
            glossary=[
                {"term": "calibration", "definition": "예측 확률이 실제 정답 비율과 얼마나 맞는지"},
                {"term": "temperature scaling", "definition": "logit 분포의 날카로움을 T로 조절하는 방법"},
            ],
            summary=[
                "정확도를 잘 맞히는 것과 확률을 믿을 만하게 말하는 것은 다릅니다.",
                "temperature scaling은 confidence의 모양을 조절합니다.",
            ],
            critical_note={
                "title": "원문과 설명 모델의 경계",
                "text": self.WARNING,
            },
            sources=source_refs,
        )
        # Compatibility shell for the existing critic and legacy renderer.
        state.spec = InteractionSpec(
            claim_id=claim.id, primitive="interactive_explainer",
            title=state.explainer.title, learning_goal=bottleneck.question,
            misconception="정확도 하나만 보면 confidence도 자동으로 신뢰할 수 있다고 생각하는 것.",
            fidelity_warning=self.WARNING,
        )
        bus.decision("panels", "설명 패널 구성 완료", panels=len(panels), max_panels=3)


class KoreanEditorial(Stage):
    """Provide Korean-first copy without exposing internal graph vocabulary."""

    name = "editorial"
    reads = ("explainer", "bottleneck")
    writes = ("explainer",)
    budget_s = 0.1

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.explainer is None:
            return
        editorial = {
            "hook": "결과 숫자 하나만 보면 놓치기 쉬운 연결고리를 직접 움직여 봅니다.",
            "instruction": "슬라이더를 움직이고, 무엇이 바뀌는지 한 문장으로 확인하세요.",
            "caveat": state.explainer.critical_note.get("text"),
            "language": "ko",
        }
        if self.llm:
            try:
                out = self.llm.structured(
                    role="korean_editorial",
                    prompt=f"# question\n{state.explainer.bottleneck.question}\n# caveat\n{editorial['caveat']}",
                    schema_hint="KoreanEditorial", bus=bus,
                )
                if isinstance(out, dict):
                    for key in ("hook", "instruction", "caveat"):
                        if str(out.get(key) or "").strip():
                            editorial[key] = str(out[key]).strip()
                    if isinstance(out.get("summary"), list) and out["summary"]:
                        editorial["summary"] = [str(item) for item in out["summary"][:3]]
            except Exception as exc:  # noqa: BLE001
                bus.decision("editorial", "모델 editorial 실패 -> 고정 한국어 문구 사용",
                             error=type(exc).__name__)
        state.explainer.editorial = editorial
        bus.decision("editorial", "한국어 설명 문구 확정", fields=list(state.explainer.editorial))


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
                id=aid, claim_id=claim.id, text=str(a.get("text") or "").strip(),
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

        bus.decision("assumptions", f"{claim.id}: 후보 {len(raw)}개 중 "
                                    f"{len(kept)}개 채택",
                     claim_id=claim.id, proposed=len(raw), accepted=len(kept))
        return kept


class VerifyExternal(Stage):
    """Four-lens evidence retrieval for the selected root-to-frontier path.

    Facets describe how we searched, never what the sources prove. Results are
    collected for inspection only: this stage does not aggregate a controversy
    verdict and DesignInteraction does not consume its output.
    """

    name = "external"
    reads = ("claims", "selected_claim_id", "critical_path_ids")
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
        frontier = next(
            (c for c in state.claims if c.id == state.selected_claim_id), None
        )
        path_ids = state.critical_path_ids or ([state.selected_claim_id]
                                                if state.selected_claim_id else [])
        by_id = {c.id: c for c in state.claims}
        path = [by_id[claim_id] for claim_id in path_ids if claim_id in by_id]
        if frontier is None or not path:
            raise StageError("no selected claim path to verify")

        queries = self._queries(path, bus)
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
                    "verifier", f"{frontier.id}/{facet}: path 검색 실패",
                    claim_id=frontier.id, claim_ids=path_ids, facet=facet,
                    query=query, hits=None,
                    status="failed", error=str(e),
                )
                continue

            hits = [hit for hit in raw_hits if isinstance(hit, dict)]
            counts[facet] = len(hits)
            status = "found" if hits else "empty"
            bus.decision(
                "verifier", f"{frontier.id}/{facet}: path 검색 결과 {len(hits)}건",
                claim_id=frontier.id, claim_ids=path_ids, facet=facet,
                query=query, hits=len(hits),
                status=status, dropped=len(raw_hits) - len(hits),
            )
            for hit in hits:
                self._merge_hit(
                    frontier.id, path_ids, facet, hit, evidence, by_url,
                    stances_by_url,
                )

        # Replace even on four empty/failed searches: stale evidence must not
        # survive a recheck and masquerade as the new result.
        state.external[frontier.id] = evidence
        bus.decision(
            "verifier", f"{frontier.id}: path 네 갈래 외부 근거 {len(evidence)}건",
            claim_id=frontier.id, claim_ids=path_ids,
            counts=counts, evidence=len(evidence),
        )
        bus.emit_status(f"외부 근거 {len(evidence)}건 나열")

    def _queries(self, path: list[Claim], bus: EventBus) -> dict[EvidenceFacet, str]:
        path_ids = [claim.id for claim in path]
        path_text = "\n".join(
            f"{claim.id} [{claim.role}] evidence={','.join(claim.evidence_span_ids)}: "
            f"{claim.text}"
            for claim in path
        )
        fallback = {
            facet: f'"{path[-1].text}" {self.FALLBACK_SUFFIXES[facet]}'
            for facet in self.FACETS
        }
        try:
            out = self.llm.structured(
                role="external_query_planner",
                prompt=f"# critical claim path\n{path_text}",
                schema_hint="ExternalQueries",
                bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- retrieval has a no-LLM path
            bus.decision(
                "verifier", f"{path_ids[-1]}: path 쿼리 생성 실패 -> 템플릿 사용",
                claim_id=path_ids[-1], claim_ids=path_ids,
                status="fallback", error=str(e), facets=list(self.FACETS),
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
                    "verifier", f"{path_ids[-1]}/{facet}: 쿼리 누락 -> 템플릿 보충",
                    claim_id=path_ids[-1], claim_ids=path_ids,
                    facet=facet, status="fallback",
                )
        bus.decision(
            "verifier", f"{path_ids[-1]}: path 외부 검색 쿼리 4개 확정",
            claim_id=path_ids[-1], claim_ids=path_ids, sources=sources,
        )
        return queries

    def _merge_hit(
        self, claim_id: str, covered_claim_ids: list[str], facet: EvidenceFacet,
        hit: dict,
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
            for covered_id in covered_claim_ids:
                if covered_id not in item.covered_claim_ids:
                    item.covered_claim_ids.append(covered_id)
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
            covered_claim_ids=list(covered_claim_ids),
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


class Critic(Stage):
    """Deterministic prechecks first, LLM only for the soft stuff. An
    ungrounded number is caught by code in microseconds -- do not ask a model."""

    name = "critic"
    reads = ("spec", "number_pool", "doc", "claims", "assumptions",
             "external", "claim_analyses", "critical_path_ids",
             "path_unsafe")
    writes = ("verdict",)
    budget_s = 4.0

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def run(self, state: PaperState, bus: EventBus) -> None:
        from ..critic_rules import attribution_support, precheck

        spec = state.spec
        if spec is None:
            raise StageError("no spec to check")
        self._repair_weak_attributions(state, bus, attribution_support)
        violations = list(precheck(spec, state))
        if state.path_unsafe:
            violations.append(Violation(
                "UNSAFE_CLAIM_PATH",
                "critical path node analysis failed; interactive frontier is unsafe",
            ))
        if state.explainer is None:
            for claim_id in state.critical_path_ids:
                analysis = state.claim_analyses.get(claim_id)
                if analysis is None:
                    violations.append(Violation(
                        "MISSING_CLAIM_ANALYSIS",
                        f"critical path node '{claim_id}' has no analysis",
                    ))
                elif analysis.verification != "verified":
                    violations.append(Violation(
                        "UNVERIFIED_CLAIM_NODE",
                        f"critical path node '{claim_id}' is "
                        f"{analysis.verification}",
                    ))
        fatal = [v for v in violations if v.fatal]
        if not fatal and self.llm and state.assumptions:
            violations.extend(self._soft_check(state, bus))
        for v in violations:
            bus.decision("critic", f"{v.code}: {v.detail}", fatal=v.fatal)
        from ..state import CriticVerdict

        fatal = [v for v in violations if v.fatal]
        result = "UNSAFE_TO_VISUALIZE" if fatal else "PASS"
        state.verdict = CriticVerdict(
            result=result, violations=violations
        )
        bus.decision(
            "critic", f"verdict {result}", result=result,
            fatal_codes=[v.code for v in fatal],
        )
        bus.emit_status(
            "정확성 검사 " + ("시각화 제한" if fatal else "통과")
        )

    @staticmethod
    def _repair_weak_attributions(state: PaperState, bus: EventBus, checker) -> None:
        if state.spec is None:
            return
        by_id = {a.id: a for a in state.assumptions}
        for rule in state.spec.status_rules:
            if rule.attribution.kind != "paper":
                continue
            assumption = by_id.get(rule.assumption_id)
            if assumption is None:
                continue
            numeric_ok, overlap = checker(rule, assumption, state)
            if numeric_ok and overlap < 0.10:
                bus.decision(
                    "critic", f"{rule.assumption_id}: span 지지 부족 -> pedagogical 강등",
                    assumption_id=rule.assumption_id,
                    span_id=rule.attribution.span_id,
                    overlap=round(overlap, 3),
                )
                rule.attribution = Attribution(kind="pedagogical")

    def _soft_check(self, state: PaperState, bus: EventBus) -> list[Violation]:
        """Ask only about natural-language quality after code checks pass."""
        prompt = ["# task", "Flag only generic weakens_how sentences.", ""]
        for assumption in state.assumptions:
            prompt.append(
                f"{assumption.id}: {assumption.text}\n"
                f"weakens_how: {assumption.weakens_how}"
            )
        try:
            out = self.llm.structured(
                role="critic_soft", prompt="\n".join(prompt),
                schema_hint="CriticSoftCheck", bus=bus,
            )
        except Exception as e:  # noqa: BLE001 -- safety fallback is deliberate
            return [Violation(
                "SOFT_CRITIC_UNAVAILABLE",
                f"soft language check failed: {type(e).__name__}",
            )]
        findings = out.get("findings") if isinstance(out, dict) else None
        if findings is None:
            return [Violation("SOFT_CRITIC_MALFORMED", "soft critic returned no findings")]
        valid_ids = {a.id for a in state.assumptions}
        violations: list[Violation] = []
        for finding in findings:
            if not isinstance(finding, dict):
                violations.append(Violation("SOFT_CRITIC_MALFORMED", "finding is not an object"))
                continue
            aid = str(finding.get("assumption_id") or "").strip()
            if aid not in valid_ids:
                violations.append(Violation("SOFT_CRITIC_UNKNOWN_ASSUMPTION", f"unknown assumption '{aid}'"))
                continue
            if finding.get("acceptable") is False:
                detail = str(finding.get("detail") or "weakens_how is too generic").strip()
                violations.append(Violation("GENERIC_WEAKENS_HOW", f"{aid}: {detail}"))
        return violations


class VisualizationAdapter(Stage):
    """Optional external renderer, kept separate from source-grounded panels."""

    name = "visualization"
    reads = ("explainer", "bottleneck", "verdict", "doc", "source_title")
    writes = ("visualization",)
    budget_s = 40.0

    def __init__(self, visualizer: Visualization | None = None):
        self.visualizer = visualizer

    def run(self, state: PaperState, bus: EventBus) -> None:
        if self.visualizer is None or state.explainer is None:
            return
        if state.verdict is None or state.verdict.result != "PASS":
            bus.decision("visualization", "critic 결과가 PASS가 아니어서 provider 시각화 생략")
            return
        bottleneck = state.bottleneck
        refs = bottleneck.evidence_refs if bottleneck else []
        source_lines = [
            state.doc.spans[sid].text[:700]
            for sid in refs if sid in state.doc.spans
        ]
        query = "\n".join([
            "Create an explanatory process/relationship visualization, not a reproduction of a paper figure.",
            f"Title: {state.source_title or state.explainer.title}",
            f"Teaching question: {bottleneck.question if bottleneck else state.explainer.title}",
            "Use only the source-grounded facts below. Mark any interpolation as illustrative.",
            *source_lines,
        ])[:12_000]
        try:
            result = self.visualizer.render(query=query, bus=bus)
        except StageError as exc:
            bus.decision("visualization", "provider visualization 실패 -> local panel 유지",
                         error=type(exc).__name__)
            return
        if result:
            state.visualization = result
            bus.decision("visualization", "외부 설명용 visualization artifact 추가",
                         provider=result.get("provider"), theme=result.get("theme"))


class Render(Stage):
    """Deterministic. Schema -> artifact payload. Frontend owns the pixels."""

    name = "render"
    reads = ("spec", "verdict", "mode", "doc", "claims", "assumptions",
             "external", "root_claim_id", "frontier_claim_id",
             "critical_path_ids", "claim_analyses")
    writes = ("artifact",)
    budget_s = 1.0

    def run(self, state: PaperState, bus: EventBus) -> None:
        spec = state.spec
        assert spec is not None
        if state.explainer is not None and state.verdict is not None \
                and state.verdict.result == "PASS":
            state.artifact = self._explainer_payload(state)
            bus.decision("render", "DemoPayloadV2 explainer artifact 생성",
                         primitive="interactive_explainer", panels=len(state.explainer.panels))
            bus.emit_status("explainer payload 준비 완료")
            return
        if (state.verdict is not None
                and state.verdict.result == "UNSAFE_TO_VISUALIZE"):
            state.artifact = self._safe_map(spec, state)
            bus.decision(
                "render",
                "UNSAFE_TO_VISUALIZE -> evidence/assumption map으로 제한",
                primitive="evidence_assumption_map",
            )
            bus.emit_status("근거·가정 map 준비 완료 — 인터랙션 비활성")
            return

        frontier_claim = next(
            (c for c in state.claims if c.id == spec.claim_id), None
        )
        frontier_has_paper = bool(frontier_claim and any(
            state.doc.spans.get(span_id)
            and state.doc.spans[span_id].origin == "paper"
            for span_id in frontier_claim.evidence_span_ids
        ))
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
                "paper": spec.claim_id if frontier_has_paper else None,
                "input": spec.claim_id if state.claim_text else None,
                "external": len(state.external.get(spec.claim_id, [])),
            },
            "external": [
                e.__dict__.copy() for e in state.external.get(spec.claim_id, [])
            ],
        }
        bus.emit_status("playground 준비 완료")

    def _explainer_payload(self, state: PaperState) -> dict:
        exp = state.explainer
        assert exp is not None
        return {
            "primitive": "interactive_explainer",
            "mode": state.mode,
            "title": exp.title,
            "thesis": exp.thesis,
            "bottleneck": {
                **exp.bottleneck.__dict__,
            },
            "panels": [
                {
                    **panel.__dict__,
                    "provenance": list(panel.provenance),
                }
                for panel in exp.panels[:3]
            ],
            "comparison": exp.comparison,
            "glossary": exp.glossary,
            "summary": exp.summary,
            "critical_note": exp.critical_note,
            "editorial": exp.editorial,
            "sources": exp.sources,
            "external_visualization": state.visualization,
        }

    def _safe_map(self, spec: InteractionSpec, state: PaperState) -> dict:
        path_ids = state.critical_path_ids or [spec.claim_id]
        by_id = {c.id: c for c in state.claims}
        analyses = state.claim_analyses
        assumptions: list[dict] = []
        paper_ids: list[str] = []
        input_ids: list[str] = []
        seen_assumptions: set[str] = set()
        for claim_id in path_ids:
            claim = by_id.get(claim_id)
            if claim:
                for span_id in claim.evidence_span_ids:
                    span = state.doc.spans.get(span_id)
                    if span and span.origin == "paper":
                        paper_ids.append(span_id)
                    elif span and span.origin == "manual":
                        input_ids.append(span_id)
            analysis = analyses.get(claim_id)
            if analysis:
                paper_ids.extend(
                    a.span_id for a in analysis.assumptions
                    if a.span_id and state.doc.spans.get(a.span_id)
                    and state.doc.spans[a.span_id].origin == "paper"
                )
                for assumption in analysis.assumptions:
                    if assumption.id not in seen_assumptions:
                        assumptions.append(assumption.__dict__.copy())
                        seen_assumptions.add(assumption.id)
        if not assumptions:
            assumptions = [a.__dict__.copy() for a in state.assumptions]
        paper = []
        claim_input = []
        seen = set()
        for span_id in paper_ids:
            if span_id in seen:
                continue
            seen.add(span_id)
            span = state.doc.spans.get(span_id)
            if span is None:
                continue
            paper.append({
                "span_id": span.id,
                "page": span.page,
                "kind": span.kind,
                "section": span.section,
                "text": span.text,
            })

        for span_id in input_ids:
            if span_id in seen:
                continue
            seen.add(span_id)
            span = state.doc.spans.get(span_id)
            if span is None:
                continue
            claim_input.append({
                "span_id": span.id,
                "page": span.page,
                "kind": span.kind,
                "section": span.section,
                "text": span.text,
            })

        external = [e.__dict__.copy()
                    for e in state.external.get(spec.claim_id, [])]
        return {
            "primitive": "evidence_assumption_map",
            "mode": state.mode,
            "title": spec.title,
            "evidence_map": {
                "claim_id": spec.claim_id,
                "covered_claim_ids": path_ids,
                "paper": paper,
                "claim_input": claim_input,
                "external": external,
            },
            "assumption_map": assumptions,
        }

    @staticmethod
    def _graph_payload(state: PaperState) -> dict:
        nodes = []
        for claim in sorted(state.claims, key=lambda c: (c.order, c.id)):
            score = state.scores.get(claim.id)
            analysis = state.claim_analyses.get(claim.id)
            nodes.append({
                "id": claim.id,
                "text": claim.text,
                "support_type": claim.support_type,
                "parent_id": claim.parent_id,
                "role": claim.role,
                "order": claim.order,
                "evidence_span_ids": list(claim.evidence_span_ids),
                "score": round(score.total, 3) if score else None,
                "frontier_score": (round(score.frontier_total, 3)
                                    if score else None),
                "verification": analysis.verification if analysis else "unverified",
                "explanation": analysis.explanation if analysis else "",
                "visible": bool(analysis and analysis.verification == "verified"),
                "verification_badge": (analysis.verification if analysis else "unverified"),
            })
        return {
            "root_claim_id": state.root_claim_id,
            "frontier_claim_id": state.frontier_claim_id,
            "critical_path_ids": list(state.critical_path_ids),
            "claim_graph": {
                "root_claim_id": state.root_claim_id,
                "frontier_claim_id": state.frontier_claim_id,
                "critical_path_ids": list(state.critical_path_ids),
                "nodes": nodes,
            },
        }
