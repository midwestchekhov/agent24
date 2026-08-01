"""Backend defense-simulator pipeline.

The archived explainer stages remain available for the old offline harness.  A
live build uses this smaller path: source-grounded claim selection, one attack
probe, bounded Liner evidence, synthesis, and a fail-closed critic.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any

from .clients import LLM, SearchAgent
from .events import EventBus
from .errors import StageError
from .runtime import DeadlineExceeded, LiveProfile
from .state import (
    AttackQuestion,
    Claim,
    DefenseAssumption,
    DefenseScore,
    Evidence,
    EvidenceChunk,
    EvidenceLedger,
    EvidenceObligation,
    EvidenceRecord,
    EvidenceRound,
    PaperState,
    SearchAction,
)
from .stages.base import Stage
from .stages.parse import Parse


ATTACK_TYPES = {
    "comparison_fairness",
    "data_integrity",
    "measurement_validity",
    "statistical_reliability",
    "causal_attribution",
    "external_validity",
    "practical_relevance",
    "implementation_fidelity",
}
ORIGINS = {"paper_explicit", "paper_implicit", "analyst_inferred"}
ALLOWED_SECTIONS = {"abstract", "intro", "results", "discussion"}
RELATIONS = {"supports", "contradicts", "qualifies", "unresolved"}


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _source_context(state: PaperState, limit: int = 30_000) -> str:
    chunks: list[str] = []
    for span in state.doc.spans.values():
        if span.section == "references" or span.section == "acknowledgments":
            continue
        chunks.append(
            f"[{span.id} page={span.page} kind={span.kind} section={span.section}]\n"
            f"{span.text[:900]}"
        )
    return "\n\n".join(chunks)[:limit]


def _claim_candidate(span) -> bool:
    if span.origin != "paper" or span.section not in ALLOWED_SECTIONS:
        return False
    if span.kind not in {"paragraph", "caption", "equation"}:
        return False
    text = span.text.strip()
    if len(text) < 45 or text.endswith(":"):
        return False
    lowered = text.lower()
    cues = (
        "show", "find", "demonstrate", "suggest", "improv", "increase",
        "decrease", "associated", "predict", "outperform", "we report",
        "we observe", "indicate", "evidence", "결과", "개선", "증가",
        "감소", "보여", "나타났", "관련",
    )
    return any(cue in lowered for cue in cues)


def _token_set(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[가-힣]{2,}", text)
    }


def _claim_is_grounded(text: str, refs: list[str], state: PaperState) -> bool:
    claim_tokens = _token_set(text)
    source_tokens = _token_set(" ".join(state.doc.spans[ref].text for ref in refs))
    overlap = claim_tokens & source_tokens
    # Paraphrases are allowed, but a generated claim must share at least two
    # substantive tokens with its cited source span. This blocks a valid-span
    # id from being used to smuggle in an unrelated claim.
    return len(overlap) >= 2


def _fallback_claims(state: PaperState, max_claims: int = 7) -> list[dict[str, Any]]:
    candidates = [span for span in state.doc.spans.values() if _claim_candidate(span)]
    if not candidates:
        candidates = [
            span for span in state.doc.spans.values()
            if span.origin == "paper" and span.section in ALLOWED_SECTIONS
            and len(span.text.strip()) >= 45
        ]
    candidates = candidates[:max_claims]
    if not candidates:
        return []
    out = []
    for index, span in enumerate(candidates):
        out.append({
            "id": f"c{index + 1}",
            "parent_id": None if index == 0 else "c1",
            "role": "result" if index == 0 else "subclaim",
            "order": index,
            "text": span.text[:700],
            "evidence_span_ids": [span.id],
            "importance": 0.9 if index == 0 else max(0.35, 0.8 - index * 0.06),
            "vulnerability": 0.55,
            "scope_gap": 0.45,
            "attack_dimensions": ["external_validity"],
            "attack_rationale": "원문 범위와 외부 적용 범위를 확인해야 합니다.",
        })
    return out


class DefenseContextAnalyst(Stage):
    name = "defense_context"
    reads = ("doc", "number_pool", "source_title")
    writes = ("claims", "root_claim_id", "context_analysis")
    budget_s = 30.0

    def __init__(self, llm: LLM, *, prompt_chars: int = 30_000):
        self.llm = llm
        self.prompt_chars = prompt_chars

    def run(self, state: PaperState, bus: EventBus) -> None:
        if not state.doc.spans:
            raise StageError("no source spans")
        context = json.dumps({
            "source_title": state.source_title or "",
            "source_context": _source_context(state, self.prompt_chars),
            "number_pool": [asdict(item) for item in list(state.number_pool.values())[:250]],
        }, ensure_ascii=False)
        prompt = context[:self.prompt_chars]
        try:
            raw = self.llm.structured(
                role="defense_context", prompt=prompt,
                schema_hint="DefenseContext", bus=bus,
            )
        except Exception as exc:
            bus.decision("defense_context", "structured context 실패 -> source fallback",
                         error=type(exc).__name__)
            raw = {}
        raw_claims = raw.get("claims") if isinstance(raw, dict) else None
        claims = self._accept_claims(raw_claims, state, bus)
        if not claims:
            claims = _fallback_claims(state)
            bus.decision("defense_context", "usable claim 없음 -> source-bound fallback",
                         claim_count=len(claims))
        if not claims:
            raise StageError("no verifiable paper claim")
        state.claims = [self._claim(item, index) for index, item in enumerate(claims)]
        state.root_claim_id = str(
            (raw.get("root_claim_id") if isinstance(raw, dict) else None)
            or state.claims[0].id
        )
        if state.root_claim_id not in {item.id for item in state.claims}:
            state.root_claim_id = state.claims[0].id
        state.context_analysis = {
            "claims": claims,
            "root_claim_id": state.root_claim_id,
            "limitations": list(raw.get("limitations") or []) if isinstance(raw, dict) else [],
            "source_title": state.source_title or "",
        }
        bus.decision(
            "defense_context", "핵심 주장 구조화 완료",
            claim_count=len(state.claims), root_claim_id=state.root_claim_id,
        )
        bus.emit_status(f"핵심 주장 {len(state.claims)}개 구조화 완료")

    @staticmethod
    def _accept_claims(raw: Any, state: PaperState, bus: EventBus) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        span_ids = set(state.doc.spans)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or f"c{index + 1}").strip()
            text = str(item.get("text") or "").strip()
            refs = [
                str(ref) for ref in item.get("evidence_span_ids") or []
                if str(ref) in span_ids
                and state.doc.spans[str(ref)].origin == "paper"
                and state.doc.spans[str(ref)].section in ALLOWED_SECTIONS
            ]
            if (not cid or cid in seen or not text or not refs
                    or not _claim_is_grounded(text, refs, state)):
                bus.decision("defense_context", "claim 후보 폐기", claim_id=cid)
                continue
            dims = [dim for dim in item.get("attack_dimensions") or [] if dim in ATTACK_TYPES]
            out.append({
                "id": cid,
                "parent_id": str(item.get("parent_id")) if item.get("parent_id") else None,
                "role": str(item.get("role") or "subclaim"),
                "order": int(item.get("order") or index),
                "text": text[:900],
                "evidence_span_ids": refs[:6],
                "importance": _clamp(item.get("importance"), 0.5),
                "vulnerability": _clamp(item.get("vulnerability"), 0.5),
                "scope_gap": _clamp(item.get("scope_gap"), 0.5),
                "attack_dimensions": dims[:4],
                "attack_rationale": str(item.get("attack_rationale") or "").strip()[:600],
            })
            seen.add(cid)
            if len(out) >= 7:
                break
        ids = {item["id"] for item in out}
        for item in out:
            if item["parent_id"] not in ids or item["parent_id"] == item["id"]:
                item["parent_id"] = None if item["id"] == out[0]["id"] else out[0]["id"]
        return out

    @staticmethod
    def _claim(item: dict[str, Any], index: int) -> Claim:
        return Claim(
            id=item["id"], text=item["text"],
            evidence_span_ids=item["evidence_span_ids"],
            parent_id=item.get("parent_id"), role=(item.get("role") or "subclaim"),
            order=int(item.get("order") or index),
            confidence=0.8,
            support_type="independent",
        )


class DefenseFrontier(Stage):
    name = "defense_frontier"
    reads = ("claims", "context_analysis", "doc")
    writes = ("defense_scores", "defense_frontier_id", "selected_claim_id",
              "frontier_claim_id", "critical_path_ids")
    budget_s = 0.2

    def run(self, state: PaperState, bus: EventBus) -> None:
        raw_by_id = {
            str(item.get("id")): item
            for item in (state.context_analysis or {}).get("claims") or []
            if isinstance(item, dict)
        }
        for claim in state.claims:
            item = raw_by_id.get(claim.id, {})
            sections = {
                state.doc.spans[sid].section for sid in claim.evidence_span_ids
                if sid in state.doc.spans
            }
            grounding = 1.0 if sections & {"abstract", "results", "discussion"} else 0.5
            score = DefenseScore(
                claim_id=claim.id,
                importance=_clamp(item.get("importance"), 0.5),
                vulnerability=_clamp(item.get("vulnerability"), 0.5),
                scope_gap=_clamp(item.get("scope_gap"), 0.5),
                source_grounding=grounding,
            )
            state.defense_scores[claim.id] = score
            bus.decision(
                "defense_scorer", f"{claim.id} defense score={score.total:.3f}",
                claim_id=claim.id, importance=score.importance,
                vulnerability=score.vulnerability, scope_gap=score.scope_gap,
                source_grounding=score.source_grounding,
                total=score.total,
            )
        candidates = [
            claim for claim in state.claims
            if state.defense_scores[claim.id].source_grounding >= 0.5
        ]
        if not candidates:
            raise StageError("no grounded defense claim")
        chosen = max(
            candidates,
            key=lambda claim: (
                state.defense_scores[claim.id].total,
                state.defense_scores[claim.id].importance,
                -claim.order,
            ),
        )
        state.defense_frontier_id = state.selected_claim_id = state.frontier_claim_id = chosen.id
        state.critical_path_ids = self._path(chosen, state.claims)
        distinct = {round(state.defense_scores[item.id].total, 4) for item in candidates}
        if len(distinct) <= 1:
            bus.decision("defense_scorer", "LOW_SCORE_VARIANCE", candidate_count=len(candidates))
        bus.decision(
            "defense_selector", "공격 frontier 선택", claim_id=chosen.id,
            score=state.defense_scores[chosen.id].total,
            critical_path_ids=state.critical_path_ids,
            candidates=[item.id for item in candidates],
        )
        bus.emit_status("가장 공격받기 쉬운 핵심 주장 선택 완료")

    @staticmethod
    def _path(chosen: Claim, claims: list[Claim]) -> list[str]:
        by_id = {claim.id: claim for claim in claims}
        path: list[str] = []
        current: Claim | None = chosen
        seen: set[str] = set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            path.append(current.id)
            current = by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))


class DefenseProbe(Stage):
    name = "defense_probe"
    reads = ("doc", "claims", "defense_frontier_id", "defense_scores")
    writes = ("defense_assumptions", "defense_questions", "defense_probe")
    budget_s = 25.0

    def __init__(self, llm: LLM, *, prompt_chars: int = 20_000):
        self.llm = llm
        self.prompt_chars = prompt_chars

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = next((item for item in state.claims if item.id == state.defense_frontier_id), None)
        if claim is None:
            raise StageError("defense frontier missing")
        context_claim = next(
            (item for item in (state.context_analysis or {}).get("claims") or []
             if item.get("id") == claim.id),
            {},
        )
        prompt = json.dumps({
            "claim": context_claim or {"id": claim.id, "text": claim.text},
            "source_spans": [
                {"id": sid, "section": state.doc.spans[sid].section,
                 "text": state.doc.spans[sid].text[:700]}
                for sid in claim.evidence_span_ids if sid in state.doc.spans
            ],
        }, ensure_ascii=False)[:self.prompt_chars]
        try:
            raw = self.llm.structured(
                role="defense_probe", prompt=prompt,
                schema_hint="DefenseProbe", bus=bus,
            )
        except Exception as exc:
            bus.decision("defense_probe", "probe 실패 -> 부분 보고서", error=type(exc).__name__)
            raw = {}
        assumptions = self._assumptions(raw.get("assumptions"), state, bus)
        questions = self._questions(raw.get("attack_questions"), assumptions, bus)
        actions = self._actions(raw.get("search_actions"), questions, bus)
        state.defense_assumptions = assumptions
        state.defense_questions = questions
        state.defense_probe = {
            "assumptions": [asdict(item) for item in assumptions],
            "attack_questions": [asdict(item) for item in questions],
            "search_actions": actions,
            "limitations": list(raw.get("limitations") or []) if isinstance(raw, dict) else [],
        }
        bus.decision(
            "defense_probe", "가정·공격 질문 준비", assumptions=len(assumptions),
            questions=len(questions), actions=len(actions),
        )
        bus.emit_status(f"숨은 가정 {len(assumptions)}개와 예상 질문 {len(questions)}개 추출")

    @staticmethod
    def _assumptions(raw: Any, state: PaperState, bus: EventBus) -> list[DefenseAssumption]:
        if not isinstance(raw, list):
            return []
        span_ids = set(state.doc.spans)
        out: list[DefenseAssumption] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            aid = str(item.get("id") or "").strip()
            text = str(item.get("text") or "").strip()
            failure = str(item.get("failure_effect") or "").strip()
            origin = str(item.get("origin") or "analyst_inferred")
            refs = [str(ref) for ref in item.get("source_span_ids") or [] if str(ref) in span_ids]
            support = "necessary" if item.get("support_type") == "necessary" else "independent"
            if (not aid or aid in seen or not text or len(failure) < 20
                    or origin not in ORIGINS or not str(item.get("category") or "").strip()):
                bus.decision("defense_probe", "assumption 폐기", assumption_id=aid)
                continue
            if origin != "analyst_inferred" and not refs:
                bus.decision("defense_probe", "paper assumption span 없음 -> 폐기", assumption_id=aid)
                continue
            out.append(DefenseAssumption(
                id=aid, claim_id=state.defense_frontier_id or "",
                text=text[:700], category=str(item["category"]), origin=origin,
                source_span_ids=refs[:6], failure_effect=failure[:900],
                support_type=support,
            ))
            seen.add(aid)
            if len(out) >= 5:
                break
        if out and all(item.support_type == "necessary" for item in out):
            for item in out[1:]:
                item.support_type = "independent"
            bus.decision("defense_probe", "all necessary assumptions normalized")
        return out

    @staticmethod
    def _questions(raw: Any, assumptions: list[DefenseAssumption], bus: EventBus) -> list[AttackQuestion]:
        if not isinstance(raw, list):
            return []
        ids = {item.id for item in assumptions}
        out: list[AttackQuestion] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id") or "").strip()
            question = str(item.get("question") or "").strip()
            attack_type = str(item.get("attack_type") or "").strip()
            refs = [str(ref) for ref in item.get("assumption_ids") or [] if str(ref) in ids]
            severity = str(item.get("severity") or "medium")
            if severity not in {"high", "medium", "low"}:
                severity = "medium"
            if (not qid or qid in seen or not question or attack_type not in ATTACK_TYPES):
                continue
            out.append(AttackQuestion(
                id=qid, question=question[:500], attack_type=attack_type,
                assumption_ids=refs, severity=severity,
                why_likely=str(item.get("why_likely") or "").strip()[:500],
            ))
            seen.add(qid)
            if len(out) >= 3:
                break
        return out

    @staticmethod
    def _actions(raw: Any, questions: list[AttackQuestion], bus: EventBus) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        qids = {item.id for item in questions}
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            action_id = str(item.get("id") or "").strip()
            query = _clean_query(item.get("query"))
            linked = [str(ref) for ref in item.get("question_ids") or [] if str(ref) in qids]
            if not action_id or action_id in seen or not query:
                continue
            out.append({
                "id": action_id, "query": query,
                "question_ids": linked,
                "rationale": str(item.get("rationale") or "").strip()[:500],
            })
            seen.add(action_id)
            if len(out) >= 2:
                break
        return out


def _clean_query(raw: Any) -> str:
    query = " ".join(str(raw or "").split())[:300]
    if re.search(r"\b(before|after|published|publication|priority|prior art|scholarly literature|\d{4}-\d{2}-\d{2})\b", query, re.I):
        return ""
    if re.match(r"^(retrieve|find|search|locate|look for|check|identify|provide)\b", query, re.I):
        return ""
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[가-힣]{2,}", query)
    return query if len(words) >= 3 else ""


class DefenseEvidenceController(Stage):
    name = "defense_evidence"
    reads = ("claims", "defense_frontier_id", "defense_assumptions", "defense_questions", "defense_probe")
    writes = ("evidence_ledger", "external")
    budget_s = 45.0

    def __init__(self, llm: LLM, search: SearchAgent, *, profile: LiveProfile,
                 prompt_chars: int = 14_000):
        self.llm = llm
        self.search = search
        self.profile = profile
        self.prompt_chars = prompt_chars

    def run(self, state: PaperState, bus: EventBus) -> None:
        max_actions = max(0, self.profile.evidence_max_total_actions)
        max_rounds = max(1, self.profile.evidence_max_rounds)
        actions = list(state.defense_probe.get("search_actions") or [])[
            :min(max_actions, self.profile.evidence_max_actions_per_round)
        ]
        ledger = EvidenceLedger()
        selected = state.defense_frontier_id or ""
        for index, question in enumerate(state.defense_questions):
            ledger.obligations.append(EvidenceObligation(
                id=question.id, question=question.question,
                claim_ids=[selected], kind=self._kind(question.attack_type),
                required=question.severity in {"high", "medium"},
            ))
        if not actions:
            ledger.status = "partial"
            ledger.stop_reason = "no_search_action"
            state.evidence_ledger = ledger
            state.external[selected] = []
            bus.decision("defense_evidence", "검색 action 없음 -> partial")
            return
        results = self._search_actions(actions, bus)
        ledger.rounds.append(EvidenceRound(
            index=1,
            actions=[SearchAction(
                id=str(action["id"]), obligation_ids=list(action.get("question_ids") or []),
                query=str(action["query"]), rationale=str(action.get("rationale") or ""),
            ) for action in actions],
        ))
        interpretation = self._interpret(state, results, bus)
        if (interpretation.get("missing_obligation_ids")
                and interpretation.get("next_focus")
                and len(actions) < max_actions
                and len(ledger.rounds) < max_rounds
                and self._can_continue(bus)):
            query = _clean_query(interpretation.get("next_focus"))
            if query:
                refinement = {"id": "refine_1", "query": query,
                              "question_ids": list(interpretation.get("missing_obligation_ids") or []),
                              "rationale": "unresolved high-value attack question refinement"}
                bus.decision("defense_evidence", "미해결 공격 질문 refinement 검색", query=query)
                results.extend(self._search_actions([refinement], bus))
                ledger.rounds.append(EvidenceRound(
                    index=2,
                    actions=[SearchAction(
                        id="refine_1", obligation_ids=refinement["question_ids"], query=query,
                        rationale=refinement["rationale"],
                    )],
                ))
                interpretation = self._interpret(state, results, bus)
        self._record_ledger(ledger, results, interpretation, state, bus)

    def _search_actions(self, actions: list[dict[str, Any]], bus: EventBus) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(2, len(actions))) as executor:
            futures = {
                executor.submit(self.search.search, query=action["query"], bus=bus): action
                for action in actions
            }
            for future in as_completed(futures):
                action = futures[future]
                try:
                    raw = future.result()
                    results.append({"action": action, "result": raw if isinstance(raw, dict) else {}})
                except Exception as exc:
                    bus.decision("defense_evidence", "Liner action 실패", action_id=action["id"], error=type(exc).__name__)
                    results.append({"action": action, "result": {}, "error": type(exc).__name__})
        return results

    def _interpret(self, state: PaperState, results: list[dict[str, Any]], bus: EventBus) -> dict[str, Any]:
        retrieved = []
        for item in results:
            result = item.get("result") or {}
            references = [ref for ref in result.get("references") or [] if isinstance(ref, dict)]
            chunks = [chunk for chunk in result.get("reference_chunks") or [] if isinstance(chunk, dict)]
            by_url: dict[str, dict[str, Any]] = {}
            for ref in references:
                url = str(ref.get("url") or "")
                if url:
                    by_url.setdefault(url, {**ref, "chunks": []})
            for chunk in chunks[:20]:
                url = str(chunk.get("source_url") or chunk.get("sourceUrl") or "")
                if url in by_url and len(by_url[url]["chunks"]) < 3:
                    by_url[url]["chunks"].append({
                        "num": chunk.get("num"),
                        "content": str(chunk.get("content") or "")[:2200],
                    })
            for source in by_url.values():
                retrieved.append({
                    "action_id": item["action"]["id"],
                    "question_ids": item["action"].get("question_ids") or [],
                    "url": source.get("url"),
                    "title": source.get("title") or source.get("source_title") or "",
                    "snippet": source.get("snippet") or source.get("description") or "",
                    "chunks": source.get("chunks") or [],
                })
        prompt = json.dumps({
            "claim": next((asdict(item) for item in state.claims if item.id == state.defense_frontier_id), {}),
            "questions": [asdict(item) for item in state.defense_questions],
            "retrieved_sources": retrieved[:12],
        }, ensure_ascii=False)[:self.prompt_chars]
        try:
            out = self.llm.structured(
                role="defense_evidence_interpreter", prompt=prompt,
                schema_hint="EvidenceInterpretation", bus=bus,
            )
            return out if isinstance(out, dict) else {}
        except Exception as exc:
            bus.decision("defense_evidence", "evidence interpreter 실패", error=type(exc).__name__)
            return {"assessments": [], "sufficient": False,
                    "missing_obligation_ids": [item.id for item in state.defense_questions]}

    def _record_ledger(self, ledger: EvidenceLedger, results: list[dict[str, Any]],
                       interpretation: dict[str, Any], state: PaperState, bus: EventBus) -> None:
        records: list[EvidenceRecord] = []
        external: list[Evidence] = []
        assessments_by_url: dict[str, list[dict[str, Any]]] = {}
        for item in interpretation.get("assessments") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("source_url") or "")
            if url:
                assessments_by_url.setdefault(url, []).append(item)
        index = 0
        seen_urls: set[str] = set()
        valid_obligation_ids = {item.id for item in ledger.obligations}
        assumption_to_obligations: dict[str, set[str]] = {}
        for question in state.defense_questions:
            for assumption_id in question.assumption_ids:
                assumption_to_obligations.setdefault(assumption_id, set()).add(question.id)

        def normalized_obligation_ids(candidate: dict[str, Any], fallback: list[str]) -> list[str]:
            normalized: set[str] = set()
            for raw_id in candidate.get("obligation_ids") or []:
                obligation_id = str(raw_id)
                if obligation_id in valid_obligation_ids:
                    normalized.add(obligation_id)
                else:
                    normalized.update(assumption_to_obligations.get(obligation_id, set()))
            return sorted(normalized) or list(fallback)

        for item in results:
            action = item["action"]
            result = item.get("result") or {}
            refs = [ref for ref in result.get("references") or [] if isinstance(ref, dict)]
            chunks = [chunk for chunk in result.get("reference_chunks") or [] if isinstance(chunk, dict)]
            for ref in refs:
                url = str(ref.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                assessments = assessments_by_url.get(url, [])
                nums = {
                    num for assessment in assessments
                    for num in assessment.get("chunk_nums") or []
                    if isinstance(num, int)
                }
                source_chunks = [
                    EvidenceChunk(
                        id=f"ch_{index}_{chunk.get('num')}",
                        content=str(chunk.get("content") or "")[:2200],
                        source_url=url,
                        source_title=str(ref.get("title") or ""),
                        num=chunk.get("num") if isinstance(chunk.get("num"), int) else None,
                    )
                    for chunk in chunks
                    if chunk.get("num") in nums
                ]
                grounded = [
                    assessment for assessment in assessments
                    if any(
                        isinstance(num, int) and any(source.num == num for source in source_chunks)
                        for num in assessment.get("chunk_nums") or []
                    )
                ]
                relation_priority = {"contradicts": 3, "qualifies": 2, "supports": 1, "unresolved": 0}
                assessment = max(
                    grounded or assessments or [{}],
                    key=lambda item: relation_priority.get(str(item.get("relation") or "unresolved"), 0),
                )
                relation = str(assessment.get("relation") or "unresolved")
                if relation not in RELATIONS or not source_chunks:
                    relation = "unresolved"
                obligation_ids = sorted({
                    obligation_id
                    for candidate in (grounded or assessments)
                    for obligation_id in normalized_obligation_ids(
                        candidate, list(action.get("question_ids") or [])
                    )
                }) or list(action.get("question_ids") or [])
                rationale = " ".join(dict.fromkeys(
                    str(candidate.get("rationale") or "").strip()
                    for candidate in (grounded or assessments)
                    if str(candidate.get("rationale") or "").strip()
                ))
                record_id = f"ev_{index}"
                record = EvidenceRecord(
                    id=record_id, obligation_ids=obligation_ids,
                    query=str(action["query"]), title=str(ref.get("title") or ""), url=url,
                    snippet=str(ref.get("snippet") or ref.get("description") or "")[:1200],
                    chunks=source_chunks, relation=relation,
                    confidence=_clamp(assessment.get("confidence"), 0.0),
                    rationale=rationale[:900],
                    round_index=2 if action["id"] == "refine_1" else 1,
                )
                records.append(record)
                public_relation = "challenges" if relation == "contradicts" else relation
                external.append(Evidence(
                    claim_id=state.defense_frontier_id or "", id=record_id,
                    title=record.title, url=record.url, snippet=record.snippet,
                    stance=("supports" if public_relation == "supports" else
                            "contradicts" if public_relation == "challenges" else "unclear"),
                    facets=[], covered_claim_ids=[state.defense_frontier_id or ""],
                ))
                index += 1
        ledger.records = records
        grounded = [record for record in records if record.relation != "unresolved" and record.chunks]
        required = {item.id for item in ledger.obligations if item.required}
        covered = {oid for record in grounded for oid in record.obligation_ids}
        missing = sorted(required - covered)
        ledger.status = "sufficient" if not missing and grounded else "partial"
        ledger.stop_reason = "required_attack_questions_covered" if not missing and grounded else "partial_evidence"
        ledger.rounds[-1].record_ids = [record.id for record in records]
        ledger.rounds[-1].new_sources = len({record.url for record in records})
        ledger.rounds[-1].new_chunks = sum(len(record.chunks) for record in records)
        ledger.rounds[-1].missing_obligation_ids = missing
        ledger.rounds[-1].sufficient = ledger.status == "sufficient"
        state.evidence_ledger = ledger
        state.external[state.defense_frontier_id or ""] = external
        bus.decision("defense_evidence", "evidence ledger 확정", status=ledger.status,
                     records=len(records), grounded=len(grounded), missing=missing,
                     rounds=len(ledger.rounds))
        bus.emit_status(f"외부 문헌 {len(grounded)}건을 공격 근거로 해석")

    @staticmethod
    def _kind(attack_type: str) -> str:
        return "methodology" if attack_type in {
            "comparison_fairness", "data_integrity", "implementation_fidelity",
            "statistical_reliability",
        } else "boundary" if attack_type in {"external_validity", "practical_relevance"} else "contradict"

    @staticmethod
    def _can_continue(bus: EventBus) -> bool:
        runtime = getattr(bus, "runtime", None)
        if runtime is None:
            return True
        remaining = runtime.remaining_seconds()
        return remaining is None or remaining > 20.0


class DefenseSynthesizer(Stage):
    name = "defense_synthesizer"
    reads = ("claims", "defense_frontier_id", "defense_assumptions", "defense_questions", "evidence_ledger")
    writes = ("defense_report",)
    budget_s = 25.0

    def __init__(self, llm: LLM, *, prompt_chars: int = 20_000):
        self.llm = llm
        self.prompt_chars = prompt_chars

    def run(self, state: PaperState, bus: EventBus) -> None:
        claim = next((item for item in state.claims if item.id == state.defense_frontier_id), None)
        if claim is None:
            raise StageError("defense frontier missing for synthesis")
        evidence = [
            {"id": record.id, "title": record.title, "url": record.url,
             "relation": record.relation, "snippet": record.snippet,
             "chunks": [asdict(chunk) for chunk in record.chunks],
             "rationale": record.rationale}
            for record in state.evidence_ledger.records
        ]
        prompt = json.dumps({
            "claim": asdict(claim),
            "source_spans": {
                sid: {"section": state.doc.spans[sid].section, "text": state.doc.spans[sid].text}
                for sid in claim.evidence_span_ids if sid in state.doc.spans
            },
            "assumptions": [asdict(item) for item in state.defense_assumptions],
            "attack_questions": [asdict(item) for item in state.defense_questions],
            "evidence": evidence[:12],
        }, ensure_ascii=False)[:self.prompt_chars]
        try:
            raw = self.llm.structured(
                role="defense_synthesizer", prompt=prompt,
                schema_hint="DefenseSynthesis", bus=bus,
            )
        except Exception as exc:
            bus.decision("defense_synthesizer", "synthesis 실패 -> partial", error=type(exc).__name__)
            raw = {}
        state.defense_report = self._report(state, raw if isinstance(raw, dict) else {})
        bus.decision("defense_synthesizer", "방어 보고서 초안 생성",
                     assumptions=len(state.defense_assumptions),
                     questions=len(state.defense_questions),
                     evidence=len(state.evidence_ledger.records))

    @staticmethod
    def _report(state: PaperState, raw: dict[str, Any]) -> dict[str, Any]:
        claim = next(item for item in state.claims if item.id == state.defense_frontier_id)
        score = state.defense_scores.get(claim.id)
        evidence_groups: dict[str, list[dict[str, Any]]] = {
            "supports": [], "qualifies": [], "challenges": [], "unresolved": [],
        }
        for record in state.evidence_ledger.records:
            group = "challenges" if record.relation == "contradicts" else record.relation
            if group not in evidence_groups:
                group = "unresolved"
            evidence_groups[group].append({
                "evidence_id": record.id, "title": record.title, "url": record.url,
                "snippet": record.snippet, "relation": group,
                "confidence": record.confidence, "rationale": record.rationale,
                "chunk_ids": [chunk.id for chunk in record.chunks],
            })
        # The model may add a concise source-grounded summary, but it cannot add
        # new evidence ids or move a record between guarded relation groups.
        supplied = raw.get("external_evidence") if isinstance(raw, dict) else {}
        for group in evidence_groups:
            summaries = supplied.get(group) if isinstance(supplied, dict) else None
            if not isinstance(summaries, list):
                continue
            by_id = {item["evidence_id"]: item for item in evidence_groups[group]}
            for item in summaries:
                if not isinstance(item, dict):
                    continue
                evidence_id = str(item.get("evidence_id") or "")
                if evidence_id in by_id and item.get("summary"):
                    by_id[evidence_id]["summary"] = str(item["summary"])[:800]
        impacts_by_id = {
            str(item.get("assumption_id")): item
            for item in raw.get("assumption_impacts") or []
            if isinstance(item, dict)
        } if isinstance(raw, dict) else {}
        impacts = []
        for assumption in state.defense_assumptions:
            item = impacts_by_id.get(assumption.id, {})
            impacts.append({
                "assumption_id": assumption.id,
                "status_if_off": (
                    "unsupported" if assumption.support_type == "necessary" else "narrows"
                ),
                "surviving_scope": str(
                    item.get("surviving_scope") or item.get("impact") or ""
                )[:1000],
                "because": str(
                    item.get("because") or item.get("impact") or assumption.failure_effect
                )[:1000],
                "source_refs": [ref for ref in item.get("source_refs") or [] if ref in state.doc.spans],
                "evidence_ids": [
                    ref for ref in item.get("evidence_ids") or []
                    if any(record.id == ref for record in state.evidence_ledger.records)
                ],
            })
        scope = raw.get("defensible_scope") if isinstance(raw, dict) else {}
        scope = scope if isinstance(scope, dict) else {}
        return {
            "primitive": "defense_report",
            "mode": "complete",
            "target_claim": {
                "id": claim.id, "text": claim.text,
                "source_refs": list(claim.evidence_span_ids),
            },
            "selection_reason": {
                "importance": score.importance if score else None,
                "vulnerability": score.vulnerability if score else None,
                "scope_gap": score.scope_gap if score else None,
                "source_grounding": score.source_grounding if score else None,
                "score": score.total if score else None,
                "why_attackable": next(
                    (item.get("attack_rationale") for item in (state.context_analysis or {}).get("claims") or []
                     if item.get("id") == claim.id), "",
                ),
            },
            "weak_point": str(raw.get("weak_point") or "").strip()[:1000],
            "assumptions": [asdict(item) for item in state.defense_assumptions],
            "attack_questions": [asdict(item) for item in state.defense_questions],
            "external_evidence": evidence_groups,
            "defensible_scope": {
                "statement": str(
                    scope.get("statement") or scope.get("claim") or scope.get("text") or ""
                ).strip()[:1600],
                "confidence": str(scope.get("confidence") or "low"),
                "basis_kind": str(scope.get("basis_kind") or "paper_only"),
                "conditions": [str(item) for item in scope.get("conditions") or []],
                "source_refs": [ref for ref in scope.get("source_refs") or [] if ref in state.doc.spans],
                "evidence_ids": [
                    ref for ref in scope.get("evidence_ids") or []
                    if any(record.id == ref for record in state.evidence_ledger.records)
                ],
                "excluded_scope": [str(item)[:500] for item in scope.get("excluded_scope") or []],
            },
            "assumption_impacts": impacts,
            "limitations": list(dict.fromkeys(
                [str(item) for item in (state.context_analysis or {}).get("limitations") or []]
                + [str(item) for item in state.defense_probe.get("limitations") or []]
                + ["외부 문헌은 검색 chunk 범위에서만 해석했습니다."]
            )),
        }


class DefenseCritic(Stage):
    name = "defense_critic"
    reads = ("defense_report", "claims", "doc", "defense_assumptions", "defense_questions", "evidence_ledger")
    writes = ("defense_verdict", "defense_report")
    budget_s = 15.0

    def __init__(self, llm: LLM, *, prompt_chars: int = 20_000):
        self.llm = llm
        self.prompt_chars = prompt_chars

    def run(self, state: PaperState, bus: EventBus) -> None:
        report = state.defense_report or {}
        violations = self._precheck(state, report)
        if not violations:
            prompt = json.dumps({
                "report": report,
                "source_spans": {
                    sid: {"section": state.doc.spans[sid].section, "text": state.doc.spans[sid].text}
                    for sid in (report.get("target_claim") or {}).get("source_refs") or []
                    if sid in state.doc.spans
                },
            }, ensure_ascii=False)[:self.prompt_chars]
            try:
                raw = self.llm.structured(
                    role="defense_critic", prompt=prompt,
                    schema_hint="DefenseCritic", bus=bus,
                )
                for finding in raw.get("findings") or [] if isinstance(raw, dict) else []:
                    if isinstance(finding, dict) and finding.get("acceptable") is False:
                        violations.append({
                            "code": str(finding.get("code") or "DEFENSE_FIDELITY"),
                            "detail": str(finding.get("detail") or ""),
                        })
            except Exception as exc:
                violations.append({"code": "DEFENSE_CRITIC_UNAVAILABLE", "detail": type(exc).__name__})
        fatal = bool(violations)
        state.defense_verdict = {
            "result": "UNSAFE_TO_DEFEND" if fatal else "PASS",
            "violations": violations,
        }
        if fatal:
            state.defense_report = self._partial(report, violations)
        bus.decision("defense_critic", "defense verdict", result=state.defense_verdict["result"],
                     fatal_codes=[item["code"] for item in violations])
        bus.emit_status("방어 범위 검증 " + ("제한" if fatal else "통과"))

    @staticmethod
    def _precheck(state: PaperState, report: dict[str, Any]) -> list[dict[str, str]]:
        violations: list[dict[str, str]] = []
        target = report.get("target_claim") or {}
        if target.get("id") != state.defense_frontier_id:
            violations.append({"code": "TARGET_CLAIM_MISMATCH", "detail": "target claim is not selected frontier"})
        for ref in target.get("source_refs") or []:
            span = state.doc.spans.get(ref)
            if span is None or span.origin != "paper":
                violations.append({"code": "MISSING_SOURCE_SPAN", "detail": str(ref)})
        assumption_ids = {item.id for item in state.defense_assumptions}
        for assumption in state.defense_assumptions:
            if assumption.origin != "analyst_inferred" and not assumption.source_span_ids:
                violations.append({"code": "ASSUMPTION_UNGROUNDED", "detail": assumption.id})
            if len(assumption.failure_effect) < 20:
                violations.append({"code": "ASSUMPTION_EMPTY_FAILURE", "detail": assumption.id})
        for question in report.get("attack_questions") or []:
            if not set(question.get("assumption_ids") or []).issubset(assumption_ids):
                violations.append({"code": "QUESTION_ASSUMPTION_MISMATCH", "detail": str(question.get("id"))})
        valid_evidence = {record.id for record in state.evidence_ledger.records}
        for group in (report.get("external_evidence") or {}).values():
            for item in group or []:
                if item.get("relation") != "unresolved" and not item.get("chunk_ids"):
                    violations.append({"code": "EVIDENCE_WITHOUT_CHUNK", "detail": str(item.get("evidence_id"))})
                if item.get("evidence_id") not in valid_evidence:
                    violations.append({"code": "MISSING_EVIDENCE_ID", "detail": str(item.get("evidence_id"))})
        impacts = report.get("assumption_impacts") or []
        if len(impacts) != len(state.defense_assumptions):
            violations.append({"code": "ASSUMPTION_IMPACT_COUNT", "detail": "one impact row per assumption required"})
        for impact in impacts:
            if not str(impact.get("surviving_scope") or "").strip():
                violations.append({"code": "ASSUMPTION_SURVIVING_SCOPE_MISSING", "detail": str(impact.get("assumption_id"))})
            if not str(impact.get("because") or "").strip():
                violations.append({"code": "ASSUMPTION_IMPACT_REASON_MISSING", "detail": str(impact.get("assumption_id"))})
        scope = report.get("defensible_scope") or {}
        if not scope.get("statement"):
            violations.append({"code": "DEFENSE_SCOPE_MISSING", "detail": "no defensible scope statement"})
        else:
            claim = next(
                (item for item in state.claims if item.id == state.defense_frontier_id),
                None,
            )
            statement = str(scope.get("statement") or "")
            if claim is not None:
                claim_tokens = _token_set(claim.text)
                scope_tokens = _token_set(statement)
                if (claim_tokens and scope_tokens and len(claim_tokens & scope_tokens) < 2
                        and not scope.get("source_refs")
                        and not scope.get("evidence_ids")):
                    violations.append({
                        "code": "DEFENSE_SCOPE_UNGROUNDED",
                        "detail": "scope statement shares too little terminology with target claim",
                    })
                broad_markers = ("all ", "every ", "universal", "모든 ", "항상 ", "모든 환경")
                if (any(marker in statement.lower() for marker in broad_markers)
                        and not any(marker in claim.text.lower() for marker in broad_markers)):
                    violations.append({
                        "code": "DEFENSE_SCOPE_BROADENED",
                        "detail": "scope introduces universal language absent from the paper claim",
                    })
        return violations

    @staticmethod
    def _partial(report: dict[str, Any], violations: list[dict[str, str]]) -> dict[str, Any]:
        partial = dict(report)
        partial["primitive"] = "partial_defense_report"
        partial["mode"] = "partial"
        partial.pop("defensible_scope", None)
        partial["assumption_impacts"] = []
        partial["limitations"] = list(partial.get("limitations") or []) + [
            f"방어 문장은 critic 제한으로 숨김: {item['code']}" for item in violations
        ]
        return partial


class DefenseRender(Stage):
    name = "defense_render"
    reads = ("defense_report", "defense_verdict", "mode")
    writes = ("artifact",)
    budget_s = 0.5

    def run(self, state: PaperState, bus: EventBus) -> None:
        if state.defense_report:
            state.artifact = state.defense_report
        else:
            state.mode = "refused"
            state.artifact = {
                "primitive": "refusal", "mode": "refused",
                "title": "검증 가능한 방어 보고서를 만들 수 없음",
                "reason_code": "NO_DEFENSE_REPORT",
                "message": "원문에 묶을 수 있는 핵심 주장이 없어 분석을 종료했습니다.",
            }
        bus.decision("defense_render", "DefensePayloadV1 artifact 생성",
                     primitive=state.artifact.get("primitive"))
        bus.emit_status("논문 디펜스 보고서 준비 완료")


def defense_stages(llm: LLM, search: SearchAgent, profile: LiveProfile) -> list[Stage]:
    return [
        Parse(),
        DefenseContextAnalyst(llm, prompt_chars=profile.context_prompt_chars or 30_000),
        DefenseFrontier(),
        DefenseProbe(llm, prompt_chars=profile.assumption_prompt_chars or 20_000),
        DefenseEvidenceController(llm, search, profile=profile,
                                  prompt_chars=profile.evidence_prompt_chars or 14_000),
        DefenseSynthesizer(llm, prompt_chars=profile.evidence_prompt_chars or 20_000),
        DefenseCritic(llm, prompt_chars=profile.evidence_prompt_chars or 20_000),
        DefenseRender(),
    ]
