"""Stage contract.

Every stage declares which PaperState fields it reads and writes. Those tuples
are the executable data-flow contract for the autonomous pipeline.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..errors import StageError
from ..events import EventBus
from ..state import PaperState

__all__ = ["Stage", "StageError"]


class Stage(ABC):
    name: str = "unnamed"
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    budget_s: float = 10.0
    #: if the stage fails, drop to this mode instead of aborting (None = abort)
    degrade_to: str | None = None

    @abstractmethod
    def run(self, state: PaperState, bus: EventBus) -> None:
        """Mutate state in place. Emit at least one raw event."""

    def __call__(self, state: PaperState, bus: EventBus) -> float:
        t0 = time.perf_counter()
        bus.emit_raw("stage_start", stage=self.name)
        try:
            self.run(state, bus)
        finally:
            dt = time.perf_counter() - t0
            bus.emit_raw(
                "stage_end", stage=self.name, seconds=round(dt, 3),
                over_budget=dt > self.budget_s,
            )
        return dt
