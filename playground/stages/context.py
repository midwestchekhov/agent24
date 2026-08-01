"""One large-context semantic pass over the normalized source."""

from __future__ import annotations

from ..clients import LLM
from ..events import EventBus
from ..state import (
    PaperState,
    Span,
)
from .base import (
    Stage,
    StageError,
)
from .text import (
    NUM_RE,
    _claim_sections,
    _claimworthy_span,
    _falsifiable_claim,
)


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

    def __init__(self, llm: LLM, prompt_chars: int | None = None):
        self.llm = llm
        self.prompt_chars = prompt_chars or self.MAX_PROMPT_CHARS

    def run(self, state: PaperState, bus: EventBus) -> None:
        if not state.doc.spans:
            raise StageError("no spans for context analysis")
        claim_only = bool(state.claim_text) and not any(
            span.origin == "paper" for span in state.doc.spans.values()
        )
        prompt = self._render_context(state)
        out = self.llm.structured(
            role="context_analyst", prompt=prompt,
            schema_hint="ContextAnalysis", bus=bus,
        )
        analysis = dict(out) if isinstance(out, dict) else {}
        if claim_only:
            # Retrieval belongs to EvidenceController.  The analyst only says
            # what facts would be needed to evaluate the explicit claim.
            analysis["claims"] = []
            analysis.setdefault("relations", [])
            analysis.setdefault("mechanisms", [])
            analysis.setdefault("bottleneck", {
                "question": "이 주장을 믿기 위해 어떤 근거를 확인해야 할까?",
                "why_hard": "원문 없이 주장이 직접 입력되어 적용 범위와 반대 근거를 별도로 확인해야 한다.",
                "source_claim_ids": ["c1"],
                "evidence_refs": ["input_claim"],
            })
            analysis.setdefault("assumptions", [])
            analysis.setdefault("quantitative_facts", [])
            analysis.setdefault("limitations", ["claim-only input has no paper context"])
            analysis = self._guard(state, analysis, bus)
            if not analysis.get("search_obligations"):
                analysis["search_obligations"] = self._default_obligations(
                    state.claim_text or "", ["c1"]
                )
            analysis["source_refs"] = ["input_claim"]
            state.context_analysis = analysis
            bus.decision(
                "context", "claim-only 분석 -> 검색 의무만 정의",
                obligations=len(analysis["search_obligations"]),
            )
            bus.emit_status("claim-only context 분석 완료")
            return
        raw_claims = analysis.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            fallback = self._fallback(state, bus)
            # Keep any valid model-level wording, but never let an incomplete
            # response replace the source-bound candidate set.
            fallback.update({
                key: value for key, value in analysis.items()
                if key in {"bottleneck", "mechanisms", "limitations",
                           "search_obligations"}
                and value
            })
            analysis = fallback
            bus.decision("context", "큰 context 분석 응답 없음 -> 원문 bound 분석 사용")
        else:
            analysis["claims"] = raw_claims[:8]
            analysis.setdefault("mechanisms", [])
            analysis.setdefault("limitations", [])
            analysis.setdefault("search_obligations", [])
            bus.decision("context", "큰 context 분석 결과 채택",
                         proposed_claims=len(raw_claims))
        analysis = self._guard(state, analysis, bus)
        if not analysis.get("claims"):
            # A structurally valid model response can still consist entirely
            # of author metadata, table descriptions, or definitions. Those
            # are not claims. Re-select from source prose instead of letting a
            # junk root poison every downstream query and assumption.
            analysis = self._guard(state, self._fallback(state, bus), bus)
            bus.decision(
                "context",
                "모델 claim이 반증 가능한 assertion이 아님 -> 원문 thesis 후보로 교체",
            )
        analysis["source_refs"] = self._source_refs(state, analysis)
        if not analysis.get("search_obligations"):
            analysis["search_obligations"] = self._default_obligations(
                " ".join(str(c.get("text") or "") for c in analysis.get("claims") or []),
                [str(c.get("id")) for c in analysis.get("claims") or [] if c.get("id")],
            )
        state.context_analysis = analysis
        bus.decision(
            "context", "source context semantic envelope 준비",
            claims=len(analysis.get("claims") or []),
            mechanisms=len(analysis.get("mechanisms") or []),
            bottleneck=bool(analysis.get("bottleneck")),
            search_obligations=len(analysis.get("search_obligations") or []),
            source_refs=len(analysis["source_refs"]),
        )
        bus.emit_status("원문 context 분석 완료")

    def _guard(self, state: PaperState, analysis: dict, bus: EventBus) -> dict:
        """Keep model-proposed references inside the parsed source boundary."""
        valid = set(state.doc.spans)
        claim_sections = _claim_sections(state)
        allowed = {
            sid for sid, span in state.doc.spans.items()
            if span.origin == "paper" and span.section in claim_sections
        }
        out = dict(analysis)
        claims = []
        for candidate in out.get("claims") or []:
            if not isinstance(candidate, dict):
                continue
            refs = [str(sid) for sid in candidate.get("evidence_span_ids") or []]
            kept = [sid for sid in refs if sid in allowed]
            evidence = [state.doc.spans[sid] for sid in kept]
            text = str(candidate.get("text") or "").strip()
            if not _falsifiable_claim(text, evidence):
                bus.decision(
                    "context",
                    "claim 후보가 assertion이 아니라서 폐기",
                    claim_id=str(candidate.get("id") or ""),
                    claim=text[:120], evidence_span_ids=kept,
                )
                continue
            claims.append({**candidate, "evidence_span_ids": kept})
        # If the proposed root disappeared, the remaining graph has no
        # trustworthy lineage. Let the deterministic thesis fallback rebuild
        # it as a unit rather than promoting an arbitrary orphan.
        roots = [c for c in claims if not c.get("parent_id")]
        out["claims"] = claims if len(roots) == 1 else []
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
        out["search_obligations"] = self._guard_obligations(
            out.get("search_obligations"),
            [str(c.get("id")) for c in claims if c.get("id")],
        )
        return out

    def _render_context(self, state: PaperState) -> str:
        lines = [
            "# task",
            "Analyze the source as one grounded context. Return structured claims,",
            "relations, mechanism candidates, bottleneck candidates, assumptions,",
            "search obligations, and evidence span ids. Never use references or",
            "acknowledgments as claims. Search obligations are factual questions,",
            "not web queries; do not name papers you have not seen.",
            "# source title", state.source_title or "(unknown)",
            "# explicit user claim", state.claim_text or "(none)",
            "# source spans",
        ]
        used = sum(len(line) + 1 for line in lines)
        for sid, span in state.doc.spans.items():
            if span.origin != "paper":
                if not (state.claim_text and span.origin == "manual"):
                    continue
            elif span.section not in _claim_sections(state):
                continue
            # Table extraction frequently produces chart tick fragments. The
            # semantic pass sees captions and prose, not pixel-shaped cells.
            if span.kind == "table_cell":
                continue
            text = span.text[:900]
            line = f"{sid} [{span.kind} section={span.section}] {text}"
            if used + len(line) + 1 > self.prompt_chars:
                break
            lines.append(line)
            used += len(line) + 1
        lines.append("# number pool")
        for fact in list(state.number_pool.values())[:220]:
            if fact.span_id in state.doc.spans:
                line = f"{fact.id} span={fact.span_id} {fact.raw} {fact.context}"
                if used + len(line) + 1 > self.prompt_chars:
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
        abstract = sorted(
            (item for item in candidates if item[6].section == "abstract"),
            key=lambda item: item[4],
        )
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
                    and span.section in _claim_sections(state):
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
            "search_obligations": self._default_obligations(
                " ".join(c["text"] for c in claims),
                [c["id"] for c in claims],
            ),
        }

    @staticmethod
    def _guard_obligations(raw, claim_ids: list[str]) -> list[dict]:
        allowed_kinds = {"support", "contradict", "boundary", "methodology"}
        valid_claim_ids = set(claim_ids)
        out: list[dict] = []
        seen: set[str] = set()
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            obligation_id = str(item.get("id") or f"ob{index + 1}").strip()
            if not obligation_id or obligation_id in seen:
                continue
            kind = str(item.get("kind") or "support")
            if kind not in allowed_kinds:
                kind = "support"
            refs = [
                str(claim_id) for claim_id in item.get("claim_ids") or []
                if not valid_claim_ids or str(claim_id) in valid_claim_ids
            ]
            out.append({
                "id": obligation_id,
                "question": question[:500],
                "claim_ids": list(dict.fromkeys(refs or claim_ids[:1])),
                "kind": kind,
                "required": bool(item.get("required", True)),
            })
            seen.add(obligation_id)
        return out[:6]

    @staticmethod
    def _default_obligations(context: str, claim_ids: list[str]) -> list[dict]:
        focus = " ".join(context.split())[:500] or "the input claim"
        primary = claim_ids[:1]
        return [
            {
                "id": "ob1", "kind": "support", "required": True,
                "claim_ids": primary,
                "question": f"What independent evidence directly tests this asserted relationship: {focus}",
            },
            {
                "id": "ob2", "kind": "boundary", "required": True,
                "claim_ids": primary,
                "question": f"Under what datasets, populations, or conditions does this relationship fail or weaken: {focus}",
            },
        ]

    @staticmethod
    def _candidate(span: Span, state: PaperState) -> bool:
        if span.origin != "paper" or span.section not in _claim_sections(state):
            return False
        if span.text.strip() == (state.source_title or "").strip():
            return False
        return _claimworthy_span(span)

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
