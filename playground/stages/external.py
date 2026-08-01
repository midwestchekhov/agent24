"""Four-facet external evidence retrieval for the critical claim path."""

from __future__ import annotations

import re

from ..clients import (
    LLM,
    Search,
)
from ..events import EventBus
from ..state import (
    Claim,
    Evidence,
    EvidenceFacet,
    PaperState,
)
from .base import (
    Stage,
    StageError,
)


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
        "support": "independent replication validation empirical evidence",
        "contradict": "failure limitation conflicting findings does not improve",
        "boundary": "distribution shift dataset domain boundary generalization",
        "methodology": "measurement estimator metric bias methodology",
    }
    FACET_TERMS = {
        "support": ("replication", "validation", "evidence", "empirical", "study"),
        "contradict": ("failure", "limitation", "conflict", "does not", "worse", "bias"),
        "boundary": ("distribution shift", "out-of-distribution", "domain shift", "subgroup", "boundary", "generaliz"),
        "methodology": ("measurement", "estimator", "metric bias", "expected calibration error", "methodological", "binning"),
    }
    QUERY_STOPWORDS = {
        "about", "after", "before", "between", "could", "from", "have",
        "modern", "network", "networks", "paper", "reported", "results",
        "that", "their", "these", "this", "through", "using", "with",
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
            relevant = [
                hit for hit in hits if self._relevant_hit(hit, facet, path)
            ]
            counts[facet] = len(relevant)
            status = "found" if relevant else "empty"
            bus.decision(
                "verifier", f"{frontier.id}/{facet}: 관련 검색 결과 {len(relevant)}건",
                claim_id=frontier.id, claim_ids=path_ids, facet=facet,
                query=query, hits=len(relevant), retrieved=len(hits),
                status=status, dropped=len(raw_hits) - len(relevant),
                dropped_irrelevant=len(hits) - len(relevant),
            )
            for hit in relevant:
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
        seed = self._query_seed(path)
        fallback = self._fallback_queries(seed)
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
        seen: set[str] = set()
        for facet in self.FACETS:
            value = raw.get(facet)
            candidate = value.strip() if isinstance(value, str) else ""
            canonical = self._canonical_query(candidate)
            if self._query_is_distinct(candidate, facet, seed) and canonical not in seen:
                queries[facet] = candidate
                sources[facet] = "llm"
            else:
                queries[facet] = fallback[facet]
                sources[facet] = "template"
                bus.decision(
                    "verifier", f"{path_ids[-1]}/{facet}: 쿼리 누락/중복/렌즈 불명확 -> 템플릿 보충",
                    claim_id=path_ids[-1], claim_ids=path_ids,
                    facet=facet, status="fallback",
                )
            seen.add(self._canonical_query(queries[facet]))
        bus.decision(
            "verifier", f"{path_ids[-1]}: path 외부 검색 쿼리 4개 확정",
            claim_id=path_ids[-1], claim_ids=path_ids, sources=sources,
        )
        return queries

    @classmethod
    def _query_seed(cls, path: list[Claim]) -> str:
        text = " ".join(claim.text for claim in path)
        lowered = text.lower()
        if any(token in lowered for token in (
            "calibrat", "miscalibrat", "temperature scaling", "confidence", "ece"
        )):
            # Dataset/model inventories are accidental attractors. Search the
            # scientific relationship instead of copying a flattened table.
            parts = ["neural network confidence calibration"]
            if any(token in lowered for token in ("accuracy", "error", "miscalibrat")):
                parts.append("accuracy miscalibration")
            if "temperature" in lowered:
                parts.append("temperature scaling")
            if "ece" in lowered or "expected calibration error" in lowered:
                parts.append("expected calibration error")
            return " ".join(parts)
        cleaned = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", " ", path[-1].text)
        cleaned = re.sub(r"\b-?\d+(?:\.\d+)?%?\b", " ", cleaned)
        cleaned = " ".join(cleaned.split())
        return cleaned[:260]

    @classmethod
    def _fallback_queries(cls, seed: str) -> dict[EvidenceFacet, str]:
        queries = {
            facet: f'"{seed}" {cls.FALLBACK_SUFFIXES[facet]}'
            for facet in cls.FACETS
        }
        if "calibrat" in seed.lower():
            queries["methodology"] = (
                '"expected calibration error" binning bias reliable estimator methodology'
            )
            queries["contradict"] = (
                '"temperature scaling" calibration failure limitations comparison'
            )
            queries["boundary"] = (
                '"neural network calibration" distribution shift dataset domain generalization'
            )
            queries["support"] = (
                '"temperature scaling" neural network calibration independent validation'
            )
        return queries

    @classmethod
    def _canonical_query(cls, query: str) -> str:
        return " ".join(sorted(set(re.findall(r"[a-z0-9]+", query.lower()))))

    @classmethod
    def _anchors(cls, seed: str) -> set[str]:
        return {
            token for token in re.findall(r"[a-z][a-z-]{3,}", seed.lower())
            if token not in cls.QUERY_STOPWORDS
        }

    @classmethod
    def _query_is_distinct(cls, query: str, facet: EvidenceFacet, seed: str) -> bool:
        lowered = query.lower()
        anchors = cls._anchors(seed)
        has_anchor = any(anchor in lowered for anchor in anchors)
        has_lens = any(term in lowered for term in cls.FACET_TERMS[facet])
        return bool(query.strip() and has_anchor and has_lens)

    @classmethod
    def _relevant_hit(cls, hit: dict, facet: EvidenceFacet,
                      path: list[Claim]) -> bool:
        haystack = " ".join(str(hit.get(key) or "")
                            for key in ("title", "snippet")).lower()
        seed = cls._query_seed(path)
        if "calibrat" in seed.lower():
            if not any(term in haystack for term in (
                "calibrat", "expected calibration error", "temperature scaling",
                "confidence estimation", "reliability diagram",
            )):
                return False
        else:
            anchors = cls._anchors(seed)
            if anchors and not any(anchor in haystack for anchor in anchors):
                return False
        if facet == "support":
            return True
        return any(term in haystack for term in cls.FACET_TERMS[facet])

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
