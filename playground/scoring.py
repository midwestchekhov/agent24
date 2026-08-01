"""Live scenario scoring for query/evidence/artifact quality.

The catalog is intentionally a markdown contract rather than a second fixture
format. This module turns each row into one real pipeline run and scores the
observable boundary: the search query, grounded evidence, terminal artifact,
and elapsed time. It never substitutes the deterministic mock provider when
``live=True``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .events import EventBus
from .pipeline import Pipeline
from .payload import build_payload
from .state import PaperState


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    input: str
    expected: str
    check: str


@dataclass
class ScenarioResult:
    scenario: Scenario
    elapsed_seconds: float
    mode: str
    primitive: str | None
    query: str
    query_score: float
    judgement_codes: list[str]
    ledger_status: str
    grounded_records: int
    chunk_count: int
    evidence_score: float
    artifact_score: float
    latency_score: float
    total_score: float
    status: str
    failure: str | None = None
    payload: dict[str, Any] | None = None
    # Keep the complete query trace visible to the optimizer.  ``query`` is
    # retained as the first query for backwards-compatible reports.
    queries: list[str] = field(default_factory=list)
    query_flags: list[str] = field(default_factory=list)
    query_overlap: float = 0.0
    duplicate_query_rate: float = 0.0


ROW = re.compile(r"^\|\s*([A-D]\d{2})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|")
HEADING = re.compile(r"^##\s+([A-D])\.")
PATH = re.compile(r"(?:`)?([\w./-]+\.pdf)(?:`)?")
QUOTED = re.compile(r'\("([^"]+)"\)|\'([^\']+)\'\)')


def load_catalog(path: str | Path) -> list[Scenario]:
    category = "?"
    scenarios: list[Scenario] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        heading = HEADING.match(line.strip())
        if heading:
            category = heading.group(1)
            continue
        match = ROW.match(line.strip())
        if not match or match.group(1) == "ID":
            continue
        scenarios.append(Scenario(
            id=match.group(1), category=category,
            input=match.group(2), expected=match.group(3), check=match.group(4),
        ))
    return scenarios


def _unquote(value: str) -> str:
    match = QUOTED.search(value)
    if match:
        return match.group(1) or match.group(2) or value
    return re.sub(r"[`]", "", value).strip()


def state_for_scenario(scenario: Scenario, root: Path) -> PaperState:
    """Build an input without fetching URLs or inventing external files."""
    path_match = PATH.search(scenario.input)
    if path_match:
        relative = Path(path_match.group(1))
        candidate = root / relative
        if not candidate.exists():
            candidate = root / "tests" / "inputs" / relative.name
        if candidate.exists() and candidate.is_file():
            return PaperState(source_path=str(candidate))
    value = _unquote(scenario.input)
    if scenario.category == "C" or scenario.id.startswith("D"):
        return PaperState(claim_text=value)
    # The catalog describes boundary inputs rather than shipping 40 separate
    # text files. Keep the description as explicit source text; the score then
    # tests whether the live pipeline handles it honestly, not a hidden mock.
    return PaperState(
        source_text=value,
        source_title=f"scenario {scenario.id}",
    )


def _search_query(bus: EventBus) -> str:
    queries = _search_queries(bus)
    return queries[0] if queries else ""


def _search_queries(bus: EventBus) -> list[str]:
    """Return every Liner query emitted by this run, in event order."""
    queries: list[str] = []
    for event in bus.log:
        if event.type != "tool_call" or event.payload.get("name") != "search_agent.search":
            continue
        query = str(event.payload.get("arguments", {}).get("query") or "").strip()
        if query:
            queries.append(query)
    return queries


def _query_score(query: str) -> float:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[가-힣]{2,}", query)
    if not words:
        return 0.0
    lowered = query.lower()
    if any(token in lowered for token in (
        "ignore previous", "system prompt", "api key", ".env", "javascript:",
    )):
        return 0.0
    score = 1.0 if len(words) >= 4 else 0.5
    if "http://" in lowered or "https://" in lowered:
        score *= 0.5
    return round(score, 3)


def _tokens(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(
            r"[A-Za-z][A-Za-z0-9-]{2,}|[가-힣]{2,}|\d+(?:\.\d+)?%?", text,
        )
    }


def _query_diagnostics(
    queries: list[str], payload: dict[str, Any],
) -> tuple[float, list[str], float]:
    """Measure whether retrieval stayed attached to the current claim.

    This is a monitor rather than a semantic verdict: lexical overlap is
    evidence that a query contains source anchors, while flags identify cases
    requiring review of the retrieved result.
    """
    if not queries:
        return 0.0, ["NO_QUERY"], 0.0
    analysis = payload.get("analysis") or {}
    claims = analysis.get("claims") or []
    spans = payload.get("spans") or {}
    target = " ".join(
        [str((payload.get("run") or {}).get("source_title") or "")]
        + [str(item.get("text") or "") for item in claims if isinstance(item, dict)]
        + [str((spans.get("input_claim") or {}).get("text") or "")]
    )
    anchors = _tokens(target)
    anchors -= {
        "the", "and", "for", "with", "from", "that", "this", "under",
        "what", "which", "does", "있는", "하는", "대한", "주장",
    }
    query_tokens = set().union(*(_tokens(query) for query in queries))
    overlap = len(anchors & query_tokens) / max(1, min(6, len(anchors)))
    flags: list[str] = []
    joined = " ".join(queries).lower()
    if any(token in joined for token in (
        "ignore previous", "system prompt", "api key", ".env", "javascript:",
    )):
        flags.append("PROMPT_INJECTION_TOKEN")
    if anchors and not anchors.intersection(query_tokens):
        flags.append("NO_SOURCE_ANCHOR")
    numeric_anchors = {
        token for token in anchors
        if re.fullmatch(r"\d+(?:\.\d+)?%?", token)
    }
    if len(numeric_anchors) >= 2 and not numeric_anchors.intersection(query_tokens):
        flags.append("MISSING_NUMERIC_ANCHOR")
    canonical = [" ".join(sorted(set(_tokens(query)))) for query in queries]
    duplicates = len(canonical) - len(set(canonical))
    duplicate_rate = duplicates / max(1, len(canonical))
    if duplicate_rate > 0:
        flags.append("DUPLICATE_QUERY")
    return round(min(1.0, overlap), 3), flags, round(duplicate_rate, 3)


def _evidence_score(payload: dict[str, Any]) -> float:
    ledger = payload.get("evidence_ledger") or {}
    records = ledger.get("records") or []
    if not records:
        return 0.0
    grounded = [
        record for record in records
        if record.get("relation") != "unresolved" and record.get("chunks")
    ]
    status = ledger.get("status")
    base = 1.0 if status == "sufficient" else 0.65 if status == "partial" else 0.25
    return round(base * len(grounded) / len(records), 3)


def _artifact_score(payload: dict[str, Any]) -> float:
    artifact = payload.get("artifact") or {}
    primitive = artifact.get("primitive")
    if primitive == "interactive_explainer":
        panels = artifact.get("panels") or []
        return round(min(1.0, 0.7 + 0.1 * min(3, len(panels))), 3)
    if primitive == "evidence_assumption_map":
        return 0.55
    if primitive == "partial":
        return 0.35
    if primitive == "refusal":
        return 0.25
    return 0.0


def score_payload(payload: dict[str, Any], bus: EventBus,
                  elapsed_seconds: float, scenario: Scenario) -> ScenarioResult:
    queries = _search_queries(bus)
    query = queries[0] if queries else ""
    query_overlap, query_flags, duplicate_query_rate = _query_diagnostics(
        queries, payload,
    )
    query_score = _query_score(query)
    if "PROMPT_INJECTION_TOKEN" in query_flags or "NO_QUERY" in query_flags:
        query_score = 0.0
    elif queries and query_overlap == 0.0:
        query_score *= 0.25
    elif queries:
        query_score *= 0.7 + (0.3 * query_overlap)
    if duplicate_query_rate > 0.5:
        query_score *= 0.8
    query_score = round(query_score, 3)
    evidence_score = _evidence_score(payload)
    ledger = payload.get("evidence_ledger") or {}
    records = ledger.get("records") or []
    grounded_records = sum(
        record.get("relation") != "unresolved" and bool(record.get("chunks"))
        for record in records
    )
    chunk_count = sum(len(record.get("chunks") or []) for record in records)
    judgement_codes = [
        str(event.payload.get("text") or "")
        for event in bus.log
        if event.type == "decision" and event.payload.get("actor") == "critic"
    ]
    artifact_score = _artifact_score(payload)
    latency_score = max(0.0, min(1.0, 1.0 - elapsed_seconds / 120.0))
    total = round(
        query_score * 0.20 + evidence_score * 0.30
        + artifact_score * 0.35 + latency_score * 0.15,
        3,
    )
    return ScenarioResult(
        scenario=scenario, elapsed_seconds=round(elapsed_seconds, 3),
        mode=str(payload.get("mode") or ""),
        primitive=(payload.get("artifact") or {}).get("primitive"),
        query=query, query_score=query_score,
        judgement_codes=judgement_codes,
        ledger_status=str(ledger.get("status") or "pending"),
        grounded_records=grounded_records, chunk_count=chunk_count,
        evidence_score=evidence_score,
        artifact_score=artifact_score, latency_score=round(latency_score, 3),
        total_score=total, status="ok" if payload.get("artifact") else "failed",
        payload=payload,
        queries=queries, query_flags=query_flags,
        query_overlap=query_overlap,
        duplicate_query_rate=duplicate_query_rate,
    )


def run_scenario(scenario: Scenario, *, root: Path, profile: str = "live-fast") -> ScenarioResult:
    started = time.monotonic()
    bus = EventBus()
    try:
        pipeline = Pipeline.build(bus=bus, live=True, profile=profile)
        state = state_for_scenario(scenario, root)
        pipeline.run(state)
        payload = build_payload(state, bus, run_id=f"score-{scenario.id}")
        return score_payload(payload, bus, time.monotonic() - started, scenario)
    except Exception as exc:  # keep the 80-case run going
        elapsed = time.monotonic() - started
        return ScenarioResult(
            scenario=scenario, elapsed_seconds=round(elapsed, 3),
            mode="error", primitive=None, query=_search_query(bus),
            query_score=_query_score(_search_query(bus)), evidence_score=0.0,
            judgement_codes=[], ledger_status="failed",
            grounded_records=0, chunk_count=0,
            artifact_score=0.0, latency_score=0.0, total_score=0.0,
            status="failed", failure=type(exc).__name__,
            queries=_search_queries(bus),
        )


def result_json(result: ScenarioResult) -> dict[str, Any]:
    data = asdict(result)
    # The full payload is useful for a selected case, but a catalog report
    # should stay small and must never become a second raw-event archive.
    data.pop("payload", None)
    return data


def run_catalog(scenarios: Iterable[Scenario], *, root: Path,
                profile: str = "live-fast") -> list[ScenarioResult]:
    return [run_scenario(item, root=root, profile=profile) for item in scenarios]


def write_report(results: Iterable[ScenarioResult], path: str | Path) -> None:
    rows = list(results)
    summary = {
        "count": len(rows),
        "mean_score": round(sum(row.total_score for row in rows) / len(rows), 3)
        if rows else 0.0,
        "mean_elapsed_seconds": round(sum(row.elapsed_seconds for row in rows) / len(rows), 3)
        if rows else 0.0,
        "failed": sum(row.status == "failed" for row in rows),
        "results": [result_json(row) for row in rows],
    }
    Path(path).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
