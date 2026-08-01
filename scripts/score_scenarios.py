#!/usr/bin/env python3
"""Run the 80-case catalog against the real live pipeline.

Examples:
  python scripts/score_scenarios.py --live --ids A13,C07,C19 --out /tmp/score.json
  python scripts/score_scenarios.py --live --category C --limit 3

There is deliberately no mock default. Use ``--dry-run`` only to inspect the
catalog and generated inputs without making provider calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct ``python scripts/...`` puts only the scripts directory on sys.path.
# Keep the command usable without requiring an editable package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playground.scoring import (
    load_catalog,
    result_json,
    run_catalog,
    state_for_scenario,
    write_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="tests/scenarios.md")
    parser.add_argument("--root", default=".")
    parser.add_argument("--profile", default="live-fast",
                        choices=("live-fast", "live"))
    parser.add_argument("--ids", default=None,
                        help="comma-separated case ids, e.g. A13,C07")
    parser.add_argument("--category", choices=("A", "B", "C", "D"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="score-report.json")
    parser.add_argument("--payload-dir", default=None,
                        help="save each selected full DemoPayload for inspection")
    parser.add_argument("--dry-run", action="store_true",
                        help="list selected inputs without API calls")
    parser.add_argument("--live", action="store_true",
                        help="required for execution; calls OpenAI and Liner")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    selected = catalog
    if args.ids:
        wanted = {item.strip().upper() for item in args.ids.split(",") if item.strip()}
        selected = [item for item in selected if item.id in wanted]
    if args.category:
        selected = [item for item in selected if item.category == args.category]
    if args.limit is not None:
        selected = selected[:max(0, args.limit)]
    if args.dry_run:
        root = Path(args.root).resolve()
        print(json.dumps([
            {"id": item.id, "category": item.category,
             "state": repr(state_for_scenario(item, root))}
            for item in selected
        ], ensure_ascii=False, indent=2))
        return
    if not args.live:
        parser.error("scenario execution requires --live; use --dry-run for inspection")

    results = run_catalog(selected, root=Path(args.root).resolve(),
                          profile=args.profile)
    if args.payload_dir:
        payload_dir = Path(args.payload_dir)
        payload_dir.mkdir(parents=True, exist_ok=True)
        for result in results:
            if result.payload is not None:
                (payload_dir / f"{result.scenario.id}.json").write_text(
                    json.dumps(result.payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
    write_report(results, args.out)
    print(json.dumps({
        "count": len(results),
        "mean_score": round(sum(item.total_score for item in results) / len(results), 3)
        if results else 0.0,
        "mean_elapsed_seconds": round(sum(item.elapsed_seconds for item in results) / len(results), 3)
        if results else 0.0,
        "failed": sum(item.status == "failed" for item in results),
        "out": str(Path(args.out).resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
