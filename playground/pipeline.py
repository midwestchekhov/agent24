"""Single-input DAG runner that always continues to a terminal artifact."""

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

    def run(self, state: PaperState) -> PaperState:
        """Run to a terminal artifact or refusal without requesting input."""
        for st in self.stages:
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
        return state
