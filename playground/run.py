"""CLI demo.

Runs the DAG offline up to the claim selection stop, prints the candidates,
then resumes -- so the event log shows that picking a claim reruns three
stages and not the whole pipeline.

    python -m playground.run --domain med
    python -m playground.run --domain med --claim c2
"""

from __future__ import annotations

import argparse
import sys

from .events import EventBus
from .pipeline import Pipeline
from .state import PaperState, UserProfile


def ranked(state: PaperState):
    """Candidates worst-to-best -> best first. The pipeline does not choose;
    this is only how the CLI shows the menu."""
    return sorted(
        ((c, state.scores[c.id]) for c in state.claims if c.id in state.scores),
        key=lambda pair: -pair[1].total,
    )


def print_candidates(state: PaperState) -> None:
    print("\n  claim 후보:")
    for c, s in ranked(state):
        print(f"    {c.id:<5} {s.total:.2f}  {c.text[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    # med stays reachable as the control group for domain isolation, but ml is
    # the one we are building for.
    ap.add_argument("--domain", default="ml", choices=["ml", "med"])
    ap.add_argument("--pdf", default="fixtures/sample.pdf")
    ap.add_argument("--claim", help="어떤 claim을 파헤칠지. 생략하면 최고 점수")
    args = ap.parse_args()

    bus = EventBus()
    bus.subscribe(lambda e: print("  RAW  ", e.to_json()), channel="raw")
    bus.subscribe(lambda e: print("STATUS ", e.payload["text"]), channel="status")

    pipe = Pipeline.build(args.domain, bus=bus)
    state = PaperState(source_path=args.pdf)

    print("=== 1. parse -> score ===")
    pipe.run(state, until="score")
    if state.mode == "refused":
        print("\n근거 있는 주장 없음 -> refused")
        return
    print_candidates(state)

    # -- the stop point: the pipeline is done, the choice is not its call --

    if args.claim and args.claim not in state.scores:
        print(f"\n'{args.claim}'는 이 논문의 claim이 아님", file=sys.stderr)
        sys.exit(2)
    cid = args.claim or ranked(state)[0][0].id

    print(f"\n=== 2. claim 선택: {cid} ===")
    bus.decision("cli", f"사용자 선택 시뮬레이션: {cid}", claim_id=cid)
    pipe.interrupt(state, "select_claim", selected_claim_id=cid)
    report_recompute(bus)

    print("\n=== 3. interrupt: expert level ===")
    pipe.interrupt(state, "change_level",
                   profile=UserProfile(level="expert", language="ko"))
    report_recompute(bus)

    print("\nmode:", state.mode)
    print("artifact:", state.artifact)


def report_recompute(bus: EventBus) -> None:
    """The point of the demo is which stages did NOT rerun, and that is hard to
    see in a firehose -- so say it out loud."""
    ev = next((e for e in reversed(bus.log) if e.type == "recompute"), None)
    if ev is None:
        return
    ran = ev.payload["stages"]
    skipped = [s for s in ("parse", "claims", "score") if s not in ran]
    print(f"  ↳ 재실행 {ran}  /  건너뜀 {skipped}")


if __name__ == "__main__":
    main()
