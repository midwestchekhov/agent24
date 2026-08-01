"""Single-input DAG runner that always continues to a terminal artifact."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .clients import (
    LLM, LinerSearchAgent, LinerVisualization, MockLLM, MockSearchAgent,
    MockVisualization, OpenAIAgentsLLM, SearchAgent,
)
from .events import EventBus
from .runtime import (
    OFFLINE_PROFILE,
    DeadlineExceeded,
    LiveProfile,
    RunBudget,
    resolve_profile,
)
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
    EvidenceController,
    VisualizationAdapter,
)
from .state import PaperState


@dataclass
class Pipeline:
    stages: list[Stage]
    bus: EventBus
    profile: LiveProfile = OFFLINE_PROFILE

    @classmethod
    def build(cls, llm: LLM | None = None,
              search: SearchAgent | None = None, bus: EventBus | None = None,
              live: bool = False,
              profile: str | LiveProfile | None = None):
        selected_profile = (
            profile if isinstance(profile, LiveProfile)
            else resolve_profile(profile, live=live)
        )
        if selected_profile.live:
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
            llm = llm or OpenAIAgentsLLM(
                timeout_s=selected_profile.openai_timeout_seconds
            )
            search = search or LinerSearchAgent(
                max_references=selected_profile.max_references_per_action,
                max_chunks=selected_profile.max_chunks_per_action,
                max_stream_seconds=selected_profile.liner_stream_seconds,
                max_answer_chars=selected_profile.max_answer_chars,
            )
            visualizer = (
                LinerVisualization() if selected_profile.use_visualization else None
            )
        else:
            llm = llm or MockLLM()
            search = search or MockSearchAgent()
            visualizer = MockVisualization()
        bus = bus or EventBus()
        return cls(
            stages=[
                Parse(),
                ContextAnalyst(llm),
                BuildClaims(llm),
                ScoreInteractions(),
                SelectFrontier(),
                BottleneckMiner(llm),
                EvidenceController(llm, search, profile=selected_profile),
                # Assumptions come before panels: the switchboard panel is
                # built from them, and on every other route they are what the
                # critical note is written from.
                AssumptionMiner(
                    llm, path_limit=selected_profile.assumption_path_limit
                ),
                PanelComposer(llm),
                KoreanEditorial(
                    llm if selected_profile.use_editorial_llm else None
                ),
                Critic(llm),
                VisualizationAdapter(visualizer),
                Render(),
            ],
            bus=bus,
            profile=selected_profile,
        )

    # -- execution --

    def run(self, state: PaperState) -> PaperState:
        """Run to a terminal artifact or refusal without requesting input."""
        budget = RunBudget.start(self.profile)
        self.bus.runtime = budget
        for st in self.stages:
            if budget.remaining_seconds() == 0.0:
                self.bus.decision(
                    "pipeline", "runtime deadline 도달 — partial payload로 종료",
                    **budget.metadata(),
                )
                self._deadline_artifact(state)
                break
            try:
                st(state, self.bus)
            except DeadlineExceeded:
                self.bus.decision(
                    "pipeline", "provider deadline 도달 — partial payload로 종료",
                    stage=st.name, **budget.metadata(),
                )
                self._deadline_artifact(state)
                break
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

    @staticmethod
    def _deadline_artifact(state: PaperState) -> None:
        if state.artifact is not None:
            return
        state.mode = "qualitative"
        state.artifact = {
            "primitive": "partial",
            "mode": state.mode,
            "title": state.source_title or "Paper Playground",
            "message": "실행 시간 제한에 도달해 부분 결과를 반환했습니다.",
            "evidence": {
                "status": state.evidence_ledger.status,
                "stop_reason": state.evidence_ledger.stop_reason,
            },
        }
