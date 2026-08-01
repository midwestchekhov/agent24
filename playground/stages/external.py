"""Bounded OpenAI -> Liner Search Agent -> OpenAI evidence loop."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from ..clients import LLM, SearchAgent
from ..events import EventBus
from ..runtime import DeadlineExceeded, LiveProfile, DEEP_PROFILE
from ..state import (
    Evidence,
    EvidenceChunk,
    EvidenceLedger,
    EvidenceObligation,
    EvidenceRecord,
    EvidenceRound,
    PaperState,
    SearchAction,
)
from .base import Stage, StageError


class EvidenceController(Stage):
    """Let OpenAI control a bounded factual-retrieval loop.

    The controller has one sharp provider boundary: Liner Search Agent retrieves
    references and reference chunks, while OpenAI selects queries, interprets
    how the chunks bear on each obligation, and decides whether the evidence is
    sufficient.  Deterministic guards enforce budgets, deduplication, and the
    rule that a resolved factual relation must have a real returned chunk.
    """

    name = "evidence"
    reads = (
        "context_analysis", "claims", "selected_claim_id", "critical_path_ids",
        "bottleneck",
    )
    writes = ("evidence_ledger", "external")
    budget_s = 60.0
    degrade_to = None

    MAX_ROUNDS = 3
    MAX_ACTIONS_PER_ROUND = 2
    MAX_TOTAL_ACTIONS = 6
    MAX_INTERPRETER_CHARS = 24_000
    RELATIONS = {"supports", "contradicts", "qualifies", "unresolved"}

    def __init__(self, llm: LLM, search: SearchAgent,
                 profile: LiveProfile | None = None,
                 max_interpreter_chars: int | None = None):
        self.llm = llm
        self.search = search
        self.profile = profile or DEEP_PROFILE
        self.max_interpreter_chars = (
            max_interpreter_chars or self.MAX_INTERPRETER_CHARS
        )

    def run(self, state: PaperState, bus: EventBus) -> None:
        obligations = self._obligations(state)
        ledger = EvidenceLedger(obligations=obligations)
        state.evidence_ledger = ledger
        if not obligations:
            ledger.status = "failed"
            ledger.stop_reason = "no_search_obligations"
            bus.decision("evidence", "검색 의무가 없어 evidence loop 종료")
            return

        seen_queries: set[str] = set()
        records_by_url: dict[str, EvidenceRecord] = {}
        total_actions = 0
        next_focus = ""

        max_rounds = self.profile.evidence_max_rounds
        max_actions_per_round = self.profile.evidence_max_actions_per_round
        max_total_actions = self.profile.evidence_max_total_actions
        for round_index in range(1, max_rounds + 1):
            runtime = getattr(bus, "runtime", None)
            if runtime:
                runtime.ensure_available("evidence round")
            missing_before = self._missing_required(ledger)
            plan = self._plan(state, ledger, round_index, missing_before,
                              next_focus, bus)
            actions = self._actions(
                plan, obligations, round_index, missing_before, seen_queries,
                max_total_actions - total_actions, bus,
                max_actions_per_round=max_actions_per_round,
            )
            if not actions:
                ledger.status = "partial"
                ledger.stop_reason = "no_novel_search_action"
                bus.decision(
                    "evidence", "새로운 검색 action이 없어 loop 종료",
                    round=round_index, missing=missing_before,
                )
                break

            total_actions += len(actions)
            retrievals: list[dict[str, Any]] = []
            new_sources = 0
            chunks_before = sum(len(record.chunks) for record in ledger.records)
            failed_actions = 0
            for action in actions:
                try:
                    result = self.search.search(query=action.query, bus=bus)
                    if not isinstance(result, dict):
                        raise TypeError("Search Agent result must be an object")
                except DeadlineExceeded:
                    ledger.status = "partial"
                    ledger.stop_reason = "deadline_exceeded"
                    bus.decision(
                        "evidence", "deadline 도달 — 검색 중단",
                        round=round_index, action_id=action.id,
                    )
                    break
                except Exception as exc:  # one failed action does not erase the round
                    failed_actions += 1
                    bus.decision(
                        "evidence", "Liner Search Agent action 실패",
                        round=round_index, action_id=action.id,
                        obligation_ids=action.obligation_ids,
                        error=type(exc).__name__,
                    )
                    continue
                retrievals.append({"action": action, "result": result})
                new_sources += self._ingest(
                    ledger, records_by_url, action, result, round_index
                )
            new_chunks = (
                sum(len(record.chunks) for record in ledger.records) - chunks_before
            )

            try:
                interpretation = self._interpret(
                    state, ledger, retrievals, round_index, bus
                )
            except DeadlineExceeded:
                ledger.status = "partial"
                ledger.stop_reason = "deadline_exceeded"
                bus.decision("evidence", "deadline 도달 — 근거 해석 중단",
                             round=round_index)
                break
            self._apply_interpretation(ledger, interpretation, bus)
            missing_after = self._missing_required(ledger)
            model_sufficient = bool(interpretation.get("sufficient"))
            sufficient = model_sufficient and not missing_after
            decision = str(interpretation.get("next_focus") or "").strip()
            ledger.rounds.append(EvidenceRound(
                index=round_index,
                actions=actions,
                record_ids=[
                    record.id for record in ledger.records
                    if record.round_index == round_index
                ],
                new_sources=new_sources,
                new_chunks=new_chunks,
                sufficient=sufficient,
                missing_obligation_ids=missing_after,
                decision=decision,
            ))
            bus.decision(
                "evidence", "evidence round 판정",
                round=round_index, actions=len(actions),
                new_sources=new_sources, failed_actions=failed_actions,
                new_chunks=new_chunks,
                model_sufficient=model_sufficient,
                sufficient=sufficient, missing=missing_after,
            )

            if sufficient:
                ledger.status = "sufficient"
                ledger.stop_reason = "required_obligations_satisfied"
                break
            if new_sources == 0 and new_chunks == 0:
                ledger.status = "partial"
                ledger.stop_reason = "no_new_evidence"
                break
            next_focus = decision
            if ledger.status == "partial" and ledger.stop_reason == "deadline_exceeded":
                break
        else:
            ledger.status = "partial"
            ledger.stop_reason = "round_budget_exhausted"

        if ledger.status == "pending":
            ledger.status = "partial"
            ledger.stop_reason = "controller_stopped"
        self._project_legacy_external(state)
        bus.decision(
            "evidence", "evidence ledger 확정",
            status=ledger.status, rounds=len(ledger.rounds),
            obligations=len(ledger.obligations), records=len(ledger.records),
            stop_reason=ledger.stop_reason,
        )
        bus.emit_status(
            f"외부 근거 {len(ledger.records)}건 해석 — {ledger.status}"
        )

    # -- obligations and planning -------------------------------------------------

    def _obligations(self, state: PaperState) -> list[EvidenceObligation]:
        raw = (state.context_analysis or {}).get("search_obligations")
        claim_ids = {claim.id for claim in state.claims}
        selected = state.selected_claim_id or state.frontier_claim_id
        out: list[EvidenceObligation] = []
        seen: set[str] = set()
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            obligation_id = str(item.get("id") or f"ob{index + 1}").strip()
            if not question or not obligation_id or obligation_id in seen:
                continue
            kind = str(item.get("kind") or "support")
            if kind not in {"support", "contradict", "boundary", "methodology"}:
                kind = "support"
            refs = [
                str(claim_id) for claim_id in item.get("claim_ids") or []
                if str(claim_id) in claim_ids
            ]
            if not refs and selected:
                refs = [selected]
            out.append(EvidenceObligation(
                id=obligation_id,
                question=question[:500],
                claim_ids=list(dict.fromkeys(refs)),
                kind=kind,  # type: ignore[arg-type]
                required=bool(item.get("required", True)),
            ))
            seen.add(obligation_id)
        if out:
            return out[:6]

        claim = next((c for c in state.claims if c.id == selected), None)
        focus = claim.text if claim else (state.claim_text or "the selected claim")
        refs = [selected] if selected else []
        return [
            EvidenceObligation(
                "ob1", f"What independent evidence directly tests: {focus}",
                refs, "support", True,
            ),
            EvidenceObligation(
                "ob2", f"Under what conditions does this finding weaken or fail: {focus}",
                refs, "boundary", True,
            ),
        ]

    def _plan(
        self, state: PaperState, ledger: EvidenceLedger, round_index: int,
        missing: list[str], next_focus: str, bus: EventBus,
    ) -> dict[str, Any]:
        claims = {
            claim.id: claim.text for claim in state.claims
            if claim.id in (state.critical_path_ids or [state.selected_claim_id])
        }
        prompt = {
            "round": round_index,
            "source_title": state.source_title or "",
            "bottleneck": asdict(state.bottleneck) if state.bottleneck else {},
            "claims": claims,
            "obligations": [asdict(item) for item in ledger.obligations],
            "missing_required": missing,
            "previous_queries": [
                action.query for item in ledger.rounds for action in item.actions
            ],
            "evidence_so_far": [
                {
                    "id": record.id, "url": record.url,
                    "relation": record.relation,
                    "confidence": record.confidence,
                    "obligation_ids": record.obligation_ids,
                }
                for record in ledger.records
            ],
            "next_focus": next_focus,
        }
        try:
            out = self.llm.structured(
                role="evidence_planner",
                prompt=json.dumps(prompt, ensure_ascii=False),
                schema_hint="EvidencePlan", bus=bus,
            )
            return out if isinstance(out, dict) else {}
        except DeadlineExceeded:
            raise
        except Exception as exc:  # deterministic fallback still respects obligations
            bus.decision("evidence", "검색 계획 모델 실패 -> obligation 질문 사용",
                         round=round_index, error=type(exc).__name__)
            return {"actions": [], "planner_failed": True}

    def _actions(
        self, plan: dict[str, Any], obligations: list[EvidenceObligation],
        round_index: int, missing: list[str], seen_queries: set[str],
        remaining_budget: int, bus: EventBus, *, max_actions_per_round: int,
    ) -> list[SearchAction]:
        by_id = {item.id: item for item in obligations}
        raw_actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
        if plan.get("planner_failed") or plan.get("stop"):
            return []
        if not raw_actions:
            targets = missing or [item.id for item in obligations if item.required]
            raw_actions = [
                {
                    "id": f"r{round_index}q{index + 1}",
                    "obligation_ids": [obligation_id],
                    "query": by_id[obligation_id].question,
                    "rationale": "deterministic obligation fallback",
                }
                for index, obligation_id in enumerate(targets)
                if obligation_id in by_id
            ]

        actions: list[SearchAction] = []
        limit = min(max_actions_per_round, max(0, remaining_budget))
        for index, raw in enumerate(raw_actions):
            if len(actions) >= limit or not isinstance(raw, dict):
                break
            query = " ".join(str(raw.get("query") or "").split())[:700]
            canonical = self._canonical_query(query)
            refs = [
                str(item) for item in raw.get("obligation_ids") or []
                if str(item) in by_id
            ]
            if not query or not refs or canonical in seen_queries:
                bus.decision(
                    "evidence", "검색 action 폐기",
                    round=round_index, reason=("duplicate" if canonical in seen_queries
                                               else "empty_or_unknown_obligation"),
                )
                continue
            action_id = str(raw.get("id") or f"r{round_index}q{index + 1}")
            actions.append(SearchAction(
                id=action_id, obligation_ids=list(dict.fromkeys(refs)),
                query=query, rationale=str(raw.get("rationale") or "").strip(),
            ))
            seen_queries.add(canonical)
        return actions

    # -- retrieval and interpretation -------------------------------------------

    def _ingest(
        self, ledger: EvidenceLedger, by_url: dict[str, EvidenceRecord],
        action: SearchAction, result: dict[str, Any], round_index: int,
    ) -> int:
        refs = result.get("references") if isinstance(result.get("references"), list) else []
        chunks = result.get("reference_chunks")
        chunks = chunks if isinstance(chunks, list) else []
        reference_by_url = {
            self._url(item.get("url")): item for item in refs
            if isinstance(item, dict) and self._url(item.get("url"))
        }
        for item in chunks:
            if not isinstance(item, dict):
                continue
            url = self._url(item.get("source_url"))
            if url and url not in reference_by_url:
                reference_by_url[url] = {
                    "url": url, "title": item.get("source_title") or "",
                    "snippet": "",
                }

        new_sources = 0
        for url, ref in reference_by_url.items():
            # In the fast profile the provider may return a chunk whose URL is
            # absent from its capped reference list.  Do not let that hidden
            # URL inflate the ledger beyond the same per-action source cap;
            # references are inserted first, so the advertised top-N sources
            # win over an orphan chunk source.
            if (url not in by_url
                    and self.profile.max_references_per_action is not None
                    and len(by_url) >= self.profile.max_references_per_action):
                continue
            record = by_url.get(url)
            if record is None:
                record = EvidenceRecord(
                    id=f"ev{len(ledger.records) + 1}",
                    obligation_ids=list(action.obligation_ids),
                    query=action.query,
                    title=str(ref.get("title") or "").strip(),
                    url=url,
                    snippet=str(ref.get("snippet") or ref.get("description") or "").strip(),
                    round_index=round_index,
                )
                ledger.records.append(record)
                by_url[url] = record
                new_sources += 1
            else:
                for obligation_id in action.obligation_ids:
                    if obligation_id not in record.obligation_ids:
                        record.obligation_ids.append(obligation_id)
            existing = {(chunk.num, chunk.content) for chunk in record.chunks}
            for item in chunks:
                if not isinstance(item, dict) or self._url(item.get("source_url")) != url:
                    continue
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                raw_num = item.get("num")
                try:
                    num = int(raw_num) if raw_num is not None else None
                except (TypeError, ValueError):
                    num = None
                if (num, content) in existing:
                    continue
                record.chunks.append(EvidenceChunk(
                    id=f"{record.id}_ch{len(record.chunks) + 1}",
                    content=content, source_url=url,
                    source_title=str(item.get("source_title") or record.title).strip(),
                    num=num,
                ))
                existing.add((num, content))
        return new_sources

    def _interpret(
        self, state: PaperState, ledger: EvidenceLedger,
        retrievals: list[dict[str, Any]], round_index: int, bus: EventBus,
    ) -> dict[str, Any]:
        if not retrievals:
            return {"assessments": [], "sufficient": False,
                    "missing_obligation_ids": self._missing_required(ledger),
                    "next_focus": "retrieval failed"}
        current_urls = {
            self._url(ref.get("url"))
            for item in retrievals
            for ref in (item["result"].get("references") or [])
            if isinstance(ref, dict)
        }
        current_urls.update(
            self._url(chunk.get("source_url"))
            for item in retrievals
            for chunk in (item["result"].get("reference_chunks") or [])
            if isinstance(chunk, dict) and self._url(chunk.get("source_url"))
        )
        records = [record for record in ledger.records if record.url in current_urls]
        prompt = {
            "round": round_index,
            "source_claim": next((
                claim.text for claim in state.claims
                if claim.id == state.selected_claim_id
            ), state.claim_text or ""),
            "obligations": [asdict(item) for item in ledger.obligations],
            "retrieved_sources": [
                {
                    "url": record.url, "title": record.title,
                    "snippet": record.snippet,
                    "action_obligation_ids": record.obligation_ids,
                    "chunks": [
                        {"num": chunk.num, "content": chunk.content[:1800]}
                        for chunk in record.chunks
                    ],
                }
                for record in records
            ],
            "existing_resolved": [
                {
                    "id": record.id, "url": record.url,
                    "relation": record.relation,
                    "confidence": record.confidence,
                    "obligation_ids": record.obligation_ids,
                }
                for record in ledger.records if record.relation != "unresolved"
            ],
        }
        rendered = json.dumps(prompt, ensure_ascii=False)
        if len(rendered) > self.max_interpreter_chars:
            rendered = rendered[:self.max_interpreter_chars]
        try:
            out = self.llm.structured(
                role="evidence_interpreter", prompt=rendered,
                schema_hint="EvidenceInterpretation", bus=bus,
            )
            return out if isinstance(out, dict) else {}
        except DeadlineExceeded:
            raise
        except Exception as exc:
            bus.decision("evidence", "근거 해석 모델 실패 -> unresolved 유지",
                         round=round_index, error=type(exc).__name__)
            return {"assessments": [], "sufficient": False,
                    "missing_obligation_ids": self._missing_required(ledger),
                    "next_focus": "interpretation failed"}

    def _apply_interpretation(
        self, ledger: EvidenceLedger, interpretation: dict[str, Any], bus: EventBus,
    ) -> None:
        by_url = {self._url(record.url): record for record in ledger.records}
        obligation_ids = {item.id for item in ledger.obligations}
        assessments = interpretation.get("assessments")
        for raw in assessments if isinstance(assessments, list) else []:
            if not isinstance(raw, dict):
                continue
            record = by_url.get(self._url(raw.get("source_url")))
            if record is None:
                bus.decision("evidence", "해석이 검색되지 않은 URL을 참조해 폐기")
                continue
            relation = str(raw.get("relation") or "unresolved")
            if relation not in self.RELATIONS:
                relation = "unresolved"
            refs = [
                str(item) for item in raw.get("obligation_ids") or []
                if str(item) in obligation_ids
            ]
            requested_nums = set()
            for value in raw.get("chunk_nums") or []:
                try:
                    requested_nums.add(int(value))
                except (TypeError, ValueError):
                    continue
            available_nums = {chunk.num for chunk in record.chunks if chunk.num is not None}
            grounded = bool(record.chunks) and (
                not requested_nums or bool(requested_nums & available_nums)
            )
            if relation != "unresolved" and not grounded:
                bus.decision(
                    "evidence", "chunk 없는 판정은 unresolved로 강등",
                    evidence_id=record.id, url=record.url,
                )
                relation = "unresolved"
            try:
                confidence = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            record.relation = relation  # type: ignore[assignment]
            record.confidence = max(0.0, min(1.0, confidence)) if grounded else 0.0
            record.rationale = str(raw.get("rationale") or "").strip()[:1200]
            if refs:
                record.obligation_ids = list(dict.fromkeys([
                    *record.obligation_ids, *refs,
                ]))

    # -- deterministic completion and compatibility -----------------------------

    @staticmethod
    def _canonical_query(query: str) -> str:
        return " ".join(sorted(set(re.findall(r"[a-z0-9가-힣]+", query.lower()))))

    @staticmethod
    def _url(raw: Any) -> str:
        return str(raw or "").strip().rstrip("/")

    @staticmethod
    def _missing_required(ledger: EvidenceLedger) -> list[str]:
        covered = {
            obligation_id
            for record in ledger.records
            if record.relation != "unresolved"
            and record.confidence >= 0.5
            and record.chunks
            for obligation_id in record.obligation_ids
        }
        return [
            item.id for item in ledger.obligations
            if item.required and item.id not in covered
        ]

    @staticmethod
    def _project_legacy_external(state: PaperState) -> None:
        key = state.selected_claim_id or state.frontier_claim_id or ""
        if not key:
            return
        obligations = {item.id: item for item in state.evidence_ledger.obligations}
        state.external[key] = [
            Evidence(
                claim_id=key,
                title=record.title,
                url=record.url,
                snippet=(record.chunks[0].content if record.chunks else record.snippet),
                stance=("supports" if record.relation == "supports"
                        else "contradicts" if record.relation == "contradicts"
                        else "unclear"),
                id=record.id,
                facets=list(dict.fromkeys(
                    obligations[item].kind for item in record.obligation_ids
                    if item in obligations
                )),
                covered_claim_ids=list(dict.fromkeys(
                    claim_id for item in record.obligation_ids
                    if item in obligations
                    for claim_id in obligations[item].claim_ids
                )),
            )
            for record in state.evidence_ledger.records
        ]
