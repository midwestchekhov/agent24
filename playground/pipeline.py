"""DAG runner.

Recompute levels are derived, not hardcoded: an interrupt names which state
fields it dirties, and every stage whose `reads` intersect the dirty set --
transitively through `writes` -- reruns. Adding a stage does not require
touching the interrupt table.
"""

from __future__ import annotations

from dataclasses import dataclass

from .clients import LLM, MockLLM, MockSearch, Search
from .domains import get_pack
from .events import EventBus
from .stages.base import Stage, StageError
from .stages.core import (
    BuildClaims,
    Critic,
    DesignInteraction,
    Parse,
    Render,
    ScoreInteractions,
    VerifyExternal,
)
from .state import PaperState

MAX_REVISIONS = 1

#: user interrupt -> which state fields it invalidates
INTERRUPTS = {
    "change_level": ("profile",),
    "change_figure": ("selected_claim_id",),
    "recheck_external": ("external",),
    "new_paper": ("doc",),
}


@dataclass
class Pipeline:
    stages: list[Stage]
    bus: EventBus

    @classmethod
    def build(cls, domain: str, llm: LLM | None = None,
              search: Search | None = None, bus: EventBus | None = None):
        llm = llm or MockLLM()
        search = search or MockSearch()
        bus = bus or EventBus()
        return cls(
            stages=[
                Parse(),
                BuildClaims(llm),
                ScoreInteractions(),
                VerifyExternal(search),
                DesignInteraction(llm, get_pack(domain)),
                Critic(),
                Render(),
            ],
            bus=bus,
        )

    # -- execution --

    def _affected(self, dirty: set[str]) -> list[Stage]:
        """Transitive closure: a stage reruns if it reads a dirty field, and
        rerunning it dirties everything it writes."""
        dirty = set(dirty)
        out = []
        for st in self.stages:
            if dirty & set(st.reads) or set(st.writes) & dirty:
                out.append(st)
                dirty |= set(st.writes)
        return out

    def run(self, state: PaperState, stages: list[Stage] | None = None) -> PaperState:
        for st in stages or self.stages:
            try:
                st(state, self.bus)
            except StageError as e:
                self.bus.emit_raw("stage_error", stage=st.name, error=str(e))
                if st.degrade_to:
                    state.mode = st.degrade_to  # type: ignore[assignment]
                    self.bus.decision(st.name, f"모드 강등 -> {st.mode}")
                    continue
                state.mode = "refused"
                self.bus.emit_status("이 논문으로는 신뢰할 수 있는 인터랙션을 만들 수 없음")
                return state

            if st.name == "critic" and state.verdict and state.verdict.result == "REVISE":
                if state.revise_count >= MAX_REVISIONS:
                    state.mode = "qualitative"
                    self.bus.decision("pipeline", "재설계 한도 초과 -> qualitative")
                else:
                    state.revise_count += 1
                    self.bus.decision("pipeline", "크리틱 REVISE -> 설계 재실행")
                    return self.run(state, self._affected({"spec"}))
        return state

    def interrupt(self, state: PaperState, kind: str, **changes) -> PaperState:
        """Apply a user-driven change and rerun only what it touches."""
        if kind not in INTERRUPTS:
            raise KeyError(f"unknown interrupt '{kind}'")
        for k, v in changes.items():
            setattr(state, k, v)
        dirty = set(INTERRUPTS[kind])
        affected = self._affected(dirty)
        self.bus.emit_raw("recompute", trigger=kind, dirty=sorted(dirty),
                          stages=[s.name for s in affected])
        state.revise_count = 0
        return self.run(state, affected)
