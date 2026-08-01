"""Single-input DAG runner that always continues to a terminal artifact."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .clients import (
    LLM, LinerSearch, LinerVisualization, MockLLM, MockSearch,
    MockVisualization, OpenAIAgentsLLM, Search,
)
from .events import EventBus
from .stages.base import Stage, StageError
from .stages import (
    AssumptionMiner,
    BuildClaims,
    BottleneckMiner,
    Critic,
    ContextAnalyst,
    KoreanEditorial,
    PanelComposer,
    Parse,
    Render,
    ScoreInteractions,
    SelectFrontier,
    VerifyExternal,
    VisualizationAdapter,
)
from .state import PaperState


@dataclass
class Pipeline:
    stages: list[Stage]
    bus: EventBus

    @classmethod
    def build(cls, llm: LLM | None = None,
              search: Search | None = None, bus: EventBus | None = None,
              live: bool = False):
        if live:
            try:
                from dotenv import load_dotenv
                load_dotenv(Path(__file__).resolve().parents[1] / ".env")
            except ImportError:
                # Environment variables may already be exported; dotenv is a
                # convenience for the local demo, never a runtime requirement.
                pass
            missing = [name for name in ("OPENAI_API_KEY", "LINER_API_KEY")
                       if not os.getenv(name)]
            if missing:
                raise ValueError(
                    "live mode requires API keys: " + ", ".join(missing)
                )
            llm = llm or OpenAIAgentsLLM()
            search = search or LinerSearch()
            visualizer = LinerVisualization()
        else:
            llm = llm or MockLLM()
            search = search or MockSearch()
            visualizer = MockVisualization()
        bus = bus or EventBus()
        return cls(
            stages=[
                Parse(),
                ContextAnalyst(llm, search),
                BuildClaims(llm),
                ScoreInteractions(),
                SelectFrontier(),
                BottleneckMiner(llm),
                # Assumptions come before panels: the switchboard panel is
                # built from them, and on every other route they are what the
                # critical note is written from.
                AssumptionMiner(llm),
                VerifyExternal(llm, search),
                PanelComposer(llm),
                KoreanEditorial(llm),
                Critic(llm),
                VisualizationAdapter(visualizer),
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
