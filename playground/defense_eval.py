"""Explicit live acceptance harness for the defense backend.

The normal test suite is provider-free. This module is intentionally opt-in:
``--live`` is required before it will create an OpenAI/Liner run. It writes the
full payload for inspection and emits only a compact pass/fail summary.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .events import EventBus
from .payload import build_payload
from .pipeline import Pipeline
from .state import PaperState


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "tests" / "defense_gold.json"
DEFAULT_FIXTURES = (
    "fixtures/sample.pdf",
    "fixtures/guo17a.pdf",
    "fixtures/Nature_2018_Lee_et_al._Human_glioblastoma_arises_from_subventricular_zone_cells.pdf",
    "fixtures/attention_is_all_you_need.pdf",
    "fixtures/deep_residual_learning_cvpr2016.pdf",
)


def evaluate_payload(payload: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score only deterministic contract properties, never model prose quality."""
    rubric = rubric or {}
    artifact = payload.get("artifact") or {}
    ledger = (payload.get("analysis") or {}).get("evidence_ledger") or {}
    records = ledger.get("records") or []
    score = 0
    checks: dict[str, bool] = {}

    checks["schema"] = payload.get("schema_version") == "defense/1.0"
    checks["single_frontier"] = bool((artifact.get("target_claim") or {}).get("id"))
    checks["source_grounded_frontier"] = bool((artifact.get("target_claim") or {}).get("source_refs"))
    checks["assumptions"] = 3 <= len(artifact.get("assumptions") or []) <= 5
    checks["questions"] = 0 < len(artifact.get("attack_questions") or []) <= 3
    checks["grounded_evidence"] = any(
        record.get("relation") != "unresolved" and record.get("chunks")
        for record in records if isinstance(record, dict)
    )
    scope = artifact.get("defensible_scope") or {}
    checks["bounded_scope"] = bool(scope.get("statement")) and bool(
        scope.get("source_refs") or scope.get("evidence_ids")
    )
    checks["impact_one_per_assumption"] = len(artifact.get("assumption_impacts") or []) == len(
        artifact.get("assumptions") or []
    )
    checks["relation_chunk_grounding"] = all(
        item.get("relation") == "unresolved" or bool(item.get("chunks"))
        for item in records if isinstance(item, dict)
    )
    statement = str(scope.get("statement") or "").lower()
    forbidden = [str(item).lower() for item in rubric.get("forbidden_overclaims") or []]
    checks["no_gold_forbidden_overclaim"] = not any(term in statement for term in forbidden)

    # Contract points are intentionally transparent and stable; this is not a
    # replacement for human semantic review of the three gold papers.
    weights = {
        "schema": 10, "single_frontier": 10, "source_grounded_frontier": 10,
        "assumptions": 10, "questions": 10, "grounded_evidence": 15,
        "bounded_scope": 15, "impact_one_per_assumption": 10,
        "relation_chunk_grounding": 5, "no_gold_forbidden_overclaim": 5,
    }
    score = sum(weight for name, weight in weights.items() if checks.get(name))
    return {"passed": score >= 75, "score": score, "checks": checks,
            "mode": payload.get("mode"), "primitive": artifact.get("primitive")}


def run_fixture(fixture: Path, output: Path) -> dict[str, Any]:
    bus = EventBus()
    started = time.monotonic()
    pipeline = Pipeline.build(bus=bus, live=True, profile="live-fast")
    # Let Parse infer the title from the PDF; the filename is not source
    # provenance and should not replace the paper's own title.
    state = PaperState(source_path=str(fixture))
    pipeline.run(state)
    payload = build_payload(state, bus, run_id=f"gold-{fixture.stem}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8")) if GOLD_PATH.exists() else {}
    result = evaluate_payload(payload, gold.get(fixture.name, {}))
    result["fixture"] = str(fixture)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["payload"] = str(output)
    result["within_deadline"] = result["elapsed_seconds"] <= 120.0
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run explicit live defense gold acceptance")
    parser.add_argument("--live", action="store_true", help="required: use real OpenAI and Liner providers")
    parser.add_argument("--fixture", action="append", dest="fixtures", help="fixture PDF; repeatable")
    parser.add_argument("--out-dir", default="/tmp/paper-defense-gold")
    args = parser.parse_args()
    if not args.live:
        parser.error("this harness never runs mock acceptance; pass --live explicitly")
    fixtures = [Path(item) for item in (args.fixtures or DEFAULT_FIXTURES)]
    results = []
    for fixture in fixtures:
        path = fixture if fixture.is_absolute() else ROOT / fixture
        output = Path(args.out_dir) / f"{path.stem}.json"
        try:
            result = run_fixture(path, output)
        except Exception as exc:  # do not print provider details or keys
            result = {"fixture": str(path), "passed": False,
                      "error_type": type(exc).__name__}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(json.dumps({"passed": bool(results) and all(item.get("passed") and item.get("within_deadline") for item in results),
                      "fixtures": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
