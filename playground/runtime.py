"""Runtime budgets for live pipeline executions.

The state/event contracts are intentionally unchanged.  A budget is attached
to the EventBus for the duration of one run so providers and stages can share
one monotonic deadline without threading a new argument through every stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .errors import StageError


@dataclass(frozen=True)
class LiveProfile:
    name: str
    live: bool
    deadline_seconds: float | None
    evidence_max_rounds: int
    evidence_max_actions_per_round: int
    evidence_max_total_actions: int
    max_references_per_action: int | None
    max_chunks_per_action: int | None
    liner_stream_seconds: float | None
    max_answer_chars: int
    use_visualization: bool
    use_editorial_llm: bool
    assumption_path_limit: int | None
    openai_timeout_seconds: float | None


OFFLINE_PROFILE = LiveProfile(
    name="offline", live=False, deadline_seconds=None,
    evidence_max_rounds=1, evidence_max_actions_per_round=1,
    evidence_max_total_actions=1, max_references_per_action=None,
    max_chunks_per_action=None, liner_stream_seconds=None,
    max_answer_chars=12_000, use_visualization=False,
    use_editorial_llm=False, assumption_path_limit=None,
    openai_timeout_seconds=None,
)

DEEP_PROFILE = LiveProfile(
    name="live", live=True, deadline_seconds=None,
    evidence_max_rounds=3, evidence_max_actions_per_round=2,
    evidence_max_total_actions=6, max_references_per_action=None,
    max_chunks_per_action=None, liner_stream_seconds=None,
    max_answer_chars=12_000, use_visualization=True,
    use_editorial_llm=True, assumption_path_limit=None,
    openai_timeout_seconds=30.0,
)

FAST_PROFILE = LiveProfile(
    name="live-fast", live=True, deadline_seconds=120.0,
    evidence_max_rounds=1, evidence_max_actions_per_round=1,
    evidence_max_total_actions=1, max_references_per_action=5,
    max_chunks_per_action=20, liner_stream_seconds=25.0,
    max_answer_chars=4_000, use_visualization=False,
    use_editorial_llm=False, assumption_path_limit=1,
    openai_timeout_seconds=20.0,
)


def resolve_profile(name: str | None = None, *, live: bool = False) -> LiveProfile:
    if name in (None, "offline") and not live:
        return OFFLINE_PROFILE
    if name in ("live-fast", "fast"):
        return FAST_PROFILE
    if name in (None, "live", "deep") and live:
        return DEEP_PROFILE
    raise ValueError(f"unknown pipeline profile: {name}")


class DeadlineExceeded(StageError):
    """A provider or stage cannot start another operation within the budget."""


@dataclass
class RunBudget:
    profile: LiveProfile
    started_at: float
    deadline_at: float | None
    deadline_hit: bool = False

    @classmethod
    def start(cls, profile: LiveProfile) -> "RunBudget":
        started = time.monotonic()
        deadline = (
            started + profile.deadline_seconds
            if profile.deadline_seconds is not None else None
        )
        return cls(profile=profile, started_at=started, deadline_at=deadline)

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def remaining_seconds(self) -> float | None:
        if self.deadline_at is None:
            return None
        remaining = self.deadline_at - time.monotonic()
        if remaining <= 0:
            self.deadline_hit = True
            return 0.0
        return remaining

    def ensure_available(self, operation: str = "operation") -> float | None:
        remaining = self.remaining_seconds()
        if remaining == 0.0:
            self.deadline_hit = True
            raise DeadlineExceeded(
                f"{operation} skipped: {self.profile.name} deadline exceeded"
            )
        return remaining

    def metadata(self) -> dict[str, Any]:
        return {
            "profile": self.profile.name,
            "deadline_seconds": self.profile.deadline_seconds,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "deadline_hit": self.deadline_hit,
        }
