"""Single-input DAG runner with internal critic-driven recomputation."""

from __future__ import annotations

from dataclasses import dataclass

from .clients import LLM, MockLLM, MockSearch, Search
from .domains import get_pack
from .events import EventBus
from .stages.base import Stage, StageError
from .stages.core import (
    AssumptionMiner,
    BuildClaims,
    Critic,
    DesignInteraction,
    Parse,
    Render,
    ScoreInteractions,
    SelectClaim,
    VerifyExternal,
)
from .state import PaperState

MAX_REVISIONS = 1


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
                SelectClaim(),
                AssumptionMiner(llm),
                VerifyExternal(llm, search),
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

    def run(self, state: PaperState,
            stages: list[Stage] | None = None) -> PaperState:
        """Run to a terminal artifact or refusal without requesting input."""
        todo = stages or self.stages

        for st in todo:
            try:
                st(state, self.bus)
            except StageError as e:
                self.bus.emit_raw("stage_error", stage=st.name, error=str(e))
                if not st.degrade_to:
                    state.mode = "refused"
                    self.bus.emit_status(
                        "이 논문으로는 신뢰할 수 있는 인터랙션을 만들 수 없음")
                    return state
                state.mode = st.degrade_to  # type: ignore[assignment]
                self.bus.decision(st.name, f"모드 강등 -> {state.mode}")
            else:
                if (st.name == "critic" and state.verdict
                        and state.verdict.result == "REVISE"):
                    if state.revise_count >= MAX_REVISIONS:
                        state.mode = "qualitative"
                        self.bus.decision("pipeline", "재설계 한도 초과 -> qualitative")
                    else:
                        state.revise_count += 1
                        self.bus.decision("pipeline", "크리틱 REVISE -> 설계 재실행")
                        # Internal correction; it never asks the user to act.
                        return self.run(state, self._affected({"spec"}))

        return state
