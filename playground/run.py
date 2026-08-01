"""CLI demo. Runs the whole DAG offline, then fires an interrupt so you can
see incremental recompute in the event log.

    python -m playground.run --domain med
"""

from __future__ import annotations

import argparse

from .events import Event, EventBus
from .pipeline import Pipeline
from .state import PaperState, UserProfile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="med", choices=["med", "ml"])
    ap.add_argument("--pdf", default="fixtures/sample.pdf")
    args = ap.parse_args()

    bus = EventBus()
    bus.subscribe(lambda e: print("  RAW  ", e.to_json()), channel="raw")
    bus.subscribe(lambda e: print("STATUS ", e.payload["text"]), channel="status")

    pipe = Pipeline.build(args.domain, bus=bus)
    state = PaperState(source_path=args.pdf)

    print("=== initial run ===")
    pipe.run(state)

    print("\n=== interrupt: expert level ===")
    pipe.interrupt(state, "change_level",
                   profile=UserProfile(level="expert", language="ko"))

    print("\nmode:", state.mode)
    print("artifact:", state.artifact)


if __name__ == "__main__":
    main()
