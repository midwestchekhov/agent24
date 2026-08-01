"""Parse-only audit: how much of a real PDF actually survives into the pool.

Read-only. Runs `Parse` on a fixture and prints three numbers that decide
whether a paper can stay in `quantitative` mode:

  1. spans by kind
  2. number_pool size
  3. of the spans BuildClaims would consider as claim candidates, the share
     that have at least one number bound to them

(3) is the one that matters: a claim candidate with no number in the pool
cannot produce a `variable` control, so a low ratio predicts a demotion to
`qualitative` before the LLM stages ever run.

    python scripts/audit_pool.py [pdf ...]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playground.events import EventBus
from playground.stages.core import Parse
from playground.state import PaperState

DEFAULT_PDFS = ["fixtures/guo17a.pdf"]


#: mirrors the filter BuildClaims._fallback ranks over, so this measures the
#: pipeline's notion of a candidate rather than one invented here.
CANDIDATE_KINDS = ("paragraph", "equation")
MIN_CANDIDATE_CHARS = 40


def is_claim_candidate(span) -> bool:
    return span.kind in CANDIDATE_KINDS and len(span.text) > MIN_CANDIDATE_CHARS


def audit(path: str) -> None:
    state = PaperState(source_path=path)
    Parse().run(state, EventBus())  # bus is silent: nothing subscribes

    spans = state.doc.spans
    kinds = Counter(sp.kind for sp in spans.values())
    with_numbers = {n.span_id for n in state.number_pool.values()}
    candidates = [sid for sid, sp in spans.items() if is_claim_candidate(sp)]
    grounded = [sid for sid in candidates if sid in with_numbers]

    print(f"\n=== {path} ===")

    print(f"1. spans: {len(spans)}")
    for kind, n in kinds.most_common():
        print(f"     {kind:<12} {n:>5}")

    print(f"2. number_pool: {len(state.number_pool)}")

    ratio = len(grounded) / len(candidates) if candidates else 0.0
    print(f"3. claim candidates with >=1 number: "
          f"{len(grounded)}/{len(candidates)} = {ratio:.1%}")


def main() -> None:
    for path in sys.argv[1:] or DEFAULT_PDFS:
        audit(path)


if __name__ == "__main__":
    main()
