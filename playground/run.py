"""Single-input offline CLI demo.

    python -m playground.run
    python -m playground.run --domain med --pdf fixtures/sample.pdf
"""

from __future__ import annotations

import argparse

from .events import EventBus
from .pipeline import Pipeline
from .state import PaperState
from .status import evaluate


def ranked(state: PaperState):
    """Show candidates best-first using the selector's score."""
    return sorted(
        ((c, state.scores[c.id]) for c in state.claims if c.id in state.scores),
        key=lambda pair: -pair[1].total,
    )


def print_candidates(state: PaperState) -> None:
    print("\n  claim 후보:")
    for c, s in ranked(state):
        print(f"    {c.id:<5} {s.total:.2f}  {c.text[:60]}")


def print_assumptions(state: PaperState) -> None:
    """The switches the reader will get. weakens_how is printed with them
    because an assumption without it is exactly what we refuse to ship."""
    print(f"\n  {state.selected_claim_id}이(가) 성립하는 조건:")
    for a in state.assumptions:
        src = a.span_id or "pedagogical"
        print(f"    {a.id:<4} [{a.kind}/{a.source}] {a.text}")
        print(f"         ↳ 꺼지면: {a.weakens_how}  ({src})")


def main() -> None:
    ap = argparse.ArgumentParser()
    # med stays reachable as the control group for domain isolation, but ml is
    # the one we are building for.
    ap.add_argument("--domain", default="ml", choices=["ml", "med"])
    ap.add_argument("--pdf", default="fixtures/sample.pdf")
    args = ap.parse_args()

    bus = EventBus()
    bus.subscribe(lambda e: print("  RAW  ", e.to_json()), channel="raw")
    bus.subscribe(lambda e: print("STATUS ", e.payload["text"]), channel="status")

    pipe = Pipeline.build(args.domain, bus=bus)
    state = PaperState(source_path=args.pdf)

    print("=== single input: parse -> render ===")
    pipe.run(state)
    if state.mode == "refused":
        print("\n추가 입력 없이 refused로 종료")
        return
    print_candidates(state)
    print(f"\n=== 자동 선택: {state.selected_claim_id} ===")
    print_assumptions(state)
    simulate_toggles(bus, state)

    print("\nmode:", state.mode)
    print("artifact:", state.artifact)


def simulate_toggles(bus: EventBus, state: PaperState) -> None:
    """Invariant 6 made visible: flip every switch and watch the badge move
    without a single model call. The tool_call count before and after is the
    proof, so it is printed rather than asserted in a test."""
    spec = state.spec
    assert spec is not None
    ids = [a.id for a in state.assumptions]
    before = sum(1 for e in bus.log if e.type == "tool_call")

    print("\n  가정을 꺼보면:")
    status, _ = evaluate(spec, set())
    print(f"    (전부 켜짐)          {status}")
    for aid in ids:
        status, fired = evaluate(spec, {aid})
        why = fired[0]
        src = why.attribution.span_id or why.attribution.evidence_id or "—"
        print(f"    {aid} 꺼짐             {status}  [{why.attribution.kind}:{src}]")
        print(f"                         {why.because}")
    status, fired = evaluate(spec, set(ids))
    print(f"    (전부 꺼짐)          {status}  ← 발동 {len(fired)}개 중 가장 약한 것")

    after = sum(1 for e in bus.log if e.type == "tool_call")
    print(f"    ↳ 이 구간 LLM 호출 {after - before}회")


if __name__ == "__main__":
    main()
