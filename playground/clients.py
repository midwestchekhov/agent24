"""External clients behind interfaces so the whole pipeline runs offline.

Swap MockLLM -> OpenAIAgentsLLM and MockSearch -> LinerSearch when the keys
land. Nothing else in the codebase changes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from . import prompts
from .events import EventBus
# clients -> stages.base is not a cycle: stages/__init__.py is empty and
# stages.base imports events/state only. The import buys LLMError a StageError
# base, so a dead API takes the pipeline's existing degrade/refuse path instead
# of crashing the demo.
from .stages.base import StageError


def _redact_sensitive(value: Any) -> str:
    """Keep provider diagnostics useful without ever echoing configured keys."""
    text = str(value)
    for name in ("OPENAI_API_KEY", "LINER_API_KEY"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


class LLM(Protocol):
    def structured(
        self, *, role: str, prompt: str, schema_hint: str, bus: EventBus
    ) -> Any: ...


class Search(Protocol):
    def query(self, *, q: str, bus: EventBus) -> list[dict]: ...


#: role -> canned output, for running the DAG with no key. Written against the
#: real span ids of fixtures/sample.pdf, so the acceptance checks bind for real
#: rather than being skipped offline.
#:
#: `claim_mapper` is deliberately absent: BuildClaims answers a missing key by
#: ranking number-dense spans out of the actual PDF, which is the honest
#: offline path, and a canned claim list would shadow it.
DEFAULT_FIXTURES: dict[str, Any] = {
    "critic_soft": {"findings": []},
    # The default ML fixture has a different span index from the original
    # sepsis control fixture.  Prompt markers keep offline analysis claim-aware
    # without changing the public LLM protocol.
    "assumption_miner:guo:c1": {
        "assumptions": [{
            "id": "a1", "text": "비교된 error와 confidence는 동일한 benchmark split에서 산출된다.",
            "kind": "scope", "source": "paper_explicit", "span_id": "p6_b1",
            "weakens_how": "다른 split이나 데이터셋에서 비교하면 표에 보인 error 차이가 calibration 개선으로 일반화되지 않는다.",
        }]
    },
    "assumption_miner:guo:c2": {
        "assumptions": [
            {
                "id": "a1", "text": "평가 지표는 논문이 선택한 calibration bin 설정을 따른다.",
                "kind": "measurement", "source": "paper_explicit", "span_id": "p6_b2",
                "weakens_how": "bin 수와 간격을 바꾸면 ECE와 MCE가 달라져 방법 간 순위가 동일하게 유지된다고 말할 수 없다.",
            },
            {
                "id": "a2", "text": "temperature scaling은 별도 validation 데이터로 fit된다.",
                "kind": "implementation", "source": "paper_explicit", "span_id": "p4_b18",
                "weakens_how": "test 데이터에 temperature를 맞추면 calibration 수치가 낙관적으로 치우쳐 독립 평가라는 주장이 약해진다.",
            },
        ]
    },
    "switchboard_designer:guo": {
        "title": "Calibration evaluation conditions",
        "base_status": "strong",
        "learning_goal": "calibration 지표가 어떤 평가 조건에 기대는지 확인한다.",
        "misconception": "ECE가 낮으면 모든 데이터 분포에서 confidence가 신뢰할 만하다고 읽는 것.",
        "status_rules": [
            {"assumption_id": "a1", "status": "conditional",
             "because": "bin 설정이 바뀌면 ECE와 MCE가 달라져 비교 순위가 다시 계산된다.",
             "attribution": {"kind": "paper", "span_id": "p6_b2"}},
            {"assumption_id": "a2", "status": "weak",
             "because": "validation과 test를 섞으면 calibration 결과가 독립 평가가 아니게 된다.",
             "attribution": {"kind": "paper", "span_id": "p4_b18"}},
        ],
        "explanation": {
            "novice": "스위치를 끄면 calibration 수치가 어떤 조건에서 달라지는지 봅니다.",
            "domain_student": "각 스위치는 지표 계산 또는 calibration fitting 조건입니다.",
            "expert": "binning과 validation/test 분리를 바꿨을 때의 식별 가능성을 점검합니다.",
        },
    },
    "assumption_miner": {
        "assumptions": [
            {
                "id": "a1",
                "text": "보고된 성능은 0.50 운영 임계값에서 측정된 값이다.",
                "kind": "measurement",
                "source": "paper_explicit",
                "span_id": "p1_b1",
                "weakens_how": "임계값이 0.30으로 내려가면 민감도는 94%로 오르지만 "
                               "특이도를 17점 잃어, 주장은 '이 운영점에서'라는 "
                               "단서를 달아야 유지된다.",
            },
            {
                "id": "a2",
                "text": "효과 크기는 세 개 검증 사이트에서 비교 가능하다.",
                "kind": "generalization",
                "source": "paper_implicit",
                "span_id": "p1_b4",
                "weakens_how": "가장 작은 코호트는 312건뿐이고 거기서 효과가 "
                               "감쇠하므로, 사이트 간 비교 가능성이 없으면 결론은 "
                               "대형 사이트에 한정된다.",
            },
            {
                "id": "a3",
                "text": "대조군은 표준 조기경보점수이고 동일 코호트에서 평가됐다.",
                "kind": "scope",
                "source": "paper_explicit",
                "span_id": "p1_b1",
                "weakens_how": "AUC 0.87의 의미는 0.79라는 대조값에 달려 있어서, "
                               "다른 기준선과 비교하면 개선폭 자체가 다시 계산된다.",
            },
            {
                "id": "a4",
                "text": "허위경보 비율 추정은 6% 기저율을 전제한다.",
                "kind": "implementation",
                "source": "paper_explicit",
                "span_id": "p2_b6",
                "weakens_how": "기저율이 낮은 병동에서는 참 1건당 허위경보 3건이라는 "
                               "수치가 커져, 임계값 하향의 운영 비용 주장이 약해진다.",
            },
            {
                # kept in the fixture on purpose: the discard path should show
                # up in the event log on every offline run.
                "id": "a5",
                "text": "데이터가 정확하게 측정되었다.",
                "kind": "measurement",
                "source": "paper_implicit",
                "span_id": "p1_b1",
                "weakens_how": "",
            },
        ]
    },
    "switchboard_designer": {
        "base_status": "strong",
        "learning_goal": "이 주장이 어떤 조건 위에 서 있는지, 그리고 조건이 "
                         "빠질 때 어디까지 좁아지는지 직접 확인한다.",
        "misconception": "AUC 하나로 모델이 모든 상황에서 낫다고 읽는 것.",
        "status_rules": [
            {
                "assumption_id": "a1",
                "status": "conditional",
                "because": "성능은 0.50 운영점에서 잰 값이라, 임계값이 달라지면 "
                           "민감도와 특이도의 교환비가 함께 달라진다.",
                "attribution": {"kind": "paper", "span_id": "p1_b1"},
            },
            {
                "assumption_id": "a2",
                "status": "weak",
                "because": "사이트 간 효과가 비교 가능하지 않으면 결론은 대형 "
                           "사이트에만 남고, 가장 작은 코호트는 뒷받침에서 빠진다.",
                "attribution": {"kind": "paper", "span_id": "p1_b4"},
            },
            {
                "assumption_id": "a3",
                "status": "conditional",
                "because": "개선폭은 대조군이 표준 조기경보점수일 때의 값이라, "
                           "다른 기준선에서는 다시 계산해야 한다.",
                # deliberately unresolvable: external is empty, so this demotes
                # to pedagogical and the demotion shows up in the event log
                "attribution": {"kind": "external", "evidence_id": "ev_c1_0"},
            },
            {
                # deliberately illegal: the discard path for a verdict-shaped
                # status should be visible on every offline run
                "assumption_id": "a4",
                "status": "broken",
                "because": "기저율이 다르면 허위경보 계산이 성립하지 않는다.",
                "attribution": {"kind": "paper", "span_id": "p2_b6"},
            },
        ],
        "explanation": {
            "novice": "스위치를 끄면 그 조건 없이도 주장이 남는지 볼 수 있다.",
            "domain_student": "각 스위치는 논문이 기대고 있는 조건 하나다. "
                              "끄면 주장이 어디까지 좁아지는지 배지가 알려준다.",
            "expert": "운영점, 사이트 간 이질성, 대조군 선택, 기저율 네 축에서 "
                      "주장의 지지 범위를 확인한다.",
        },
    },
}


class MockLLM:
    """Returns canned fixtures keyed by role. Keeps the DAG exercisable before
    any prompt work exists."""

    def __init__(self, fixtures: dict[str, Any] | None = None):
        # `None` means "give me the offline defaults"; `{}` means "answer
        # nothing", which is how the fallback paths get exercised.
        self.fixtures = DEFAULT_FIXTURES if fixtures is None else fixtures

    def structured(self, *, role, prompt, schema_hint, bus):
        call_id = bus.tool_call(
            "llm.structured", role=role, prompt_chars=len(prompt),
            schema=schema_hint,
        )
        out = self.fixtures.get(role, {})
        # The old generic assumption fixture belongs to the legacy medical
        # demo. Never replay it for an unrelated paper: an empty answer must
        # take the safe/refusal path instead of inventing AUC/site conditions.
        if role == "assumption_miner" and not any(
            marker in prompt.lower()
            for marker in ("auc", "early-warning", "early warning", "validation sites")
        ):
            out = {}
        guo_context = any(
            marker in prompt.lower()
            for marker in ("on calibration of modern neural networks", "temperature scaling")
        )
        if guo_context and "p6_b2" in prompt:
            out = self.fixtures.get(f"{role}:guo:c2", self.fixtures.get(f"{role}:guo", out))
        elif guo_context and "p6_b1" in prompt:
            out = self.fixtures.get(f"{role}:guo:c1", out)
        bus.tool_result(call_id, out)
        return json.loads(json.dumps(out))  # deep copy


class MockSearch:
    def __init__(self, results: list[dict] | None = None):
        self.results = results or []

    def query(self, *, q, bus):
        call_id = bus.tool_call("search.query", q=q)
        bus.tool_result(call_id, self.results)
        return list(self.results)


class LinerSearch:
    """Liner Scholar Search behind the small ``Search`` protocol.

    The stage deliberately receives a normalized list, while the raw event
    keeps the provider response intact for the monitor and later inspection.
    One logical query gets one EventBus tool call even when the HTTP request is
    retried, so the event stream describes the pipeline rather than transport
    noise.
    """

    ENDPOINT = "https://platform.liner.com/api/v1/tools/search/scholar"
    RETRYABLE_STATUS = {429, 500, 502}

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_results: int = 5,
        timeout_s: float = 6.0,
        session: Any | None = None,
    ):
        self.api_key = api_key or os.getenv("LINER_API_KEY")
        self.endpoint = endpoint or self.ENDPOINT
        self.max_results = max(1, min(20, int(max_results)))
        self.timeout_s = timeout_s
        self.session = session

    def query(self, *, q, bus):
        if not self.api_key:
            raise StageError("LINER_API_KEY is required for live search")
        try:
            import requests
        except ImportError as e:  # pragma: no cover - dependency guard
            raise StageError("requests is required for LinerSearch") from e

        client = self.session or requests
        call_id = bus.tool_call("search.query", q=q, provider="liner", mode="scholar")
        payload = {"query": q, "max_results": self.max_results}
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        response = None
        last_error = None
        for attempt in range(2):
            try:
                response = client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_s,
                )
            except Exception as e:  # requests exceptions are intentionally duck-typed
                last_error = e
                if attempt == 0:
                    time.sleep(0.25)
                    continue
                bus.tool_result(call_id, None, error="Liner network request failed")
                raise StageError("Liner network request failed") from e

            status = int(getattr(response, "status_code", 0) or 0)
            if status in self.RETRYABLE_STATUS and attempt == 0:
                delay = self._retry_delay(response)
                if delay is not None:
                    time.sleep(delay)
                    continue
            break

        if response is None:  # defensive; the loop either returns or raises
            bus.tool_result(call_id, None, error="Liner request produced no response")
            raise StageError("Liner request produced no response") from last_error

        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            bus.tool_result(call_id, None, error=f"Liner HTTP {status}")
            raise StageError(f"Liner search failed with HTTP {status}")
        try:
            raw = response.json()
        except Exception as e:
            bus.tool_result(call_id, None, error="Liner returned invalid JSON")
            raise StageError("Liner returned invalid JSON") from e
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
            bus.tool_result(call_id, raw, error="Liner response missing results list")
            raise StageError("Liner response missing results list")

        bus.tool_result(call_id, raw)
        return [
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "snippet": str(item.get("description") or "").strip(),
            }
            for item in raw["results"]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _retry_delay(response: Any) -> float | None:
        raw_value = None
        try:
            raw_value = response.headers.get("Retry-After")
        except (AttributeError, TypeError, ValueError):
            raw_value = None
        if raw_value in (None, ""):
            return 0.25
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return 0.25
        # A long provider backoff would violate the stage's bounded search
        # budget; surface it as a facet failure instead of blocking the run.
        return max(0.0, value) if 0.0 <= value <= 2.0 else None


# ---------------------------------------------------------------- real LLM --


class LLMError(StageError):
    """Model call failed, or came back unparseable. StageError subclass on
    purpose: pipeline.run already knows how to degrade or refuse."""


#: role -> system instructions. Structure only. Nothing here asks for prose:
#: importance judgement and explanation writing must not share a context.
#:
#: Roles with a file in prompts/ are NOT listed here -- prompts.load(role) is
#: the source of truth for those, and a duplicate literal would silently win
#: over the file and drift from it. Order at the call site:
#: constructor override > prompts/<role>.md > this table > "_default".
ROLE_INSTRUCTIONS = {
    "_default": (
        "Return structured data only. Do not summarise the source; extract it "
        "and organise it."
    ),
}

#: schema_hint -> literal key shape. The stages construct dataclasses straight
#: from these dicts, so an extra or renamed key is a TypeError at runtime.
SCHEMA_SHAPES = {
    "GraphClaims": (
        '{"root_claim_id": "c1", "claims": [{"id": "c1", '
        '"parent_id": null, "role": "result|premise|subclaim|boundary|methodology", '
        '"order": 0, "text": "...", "evidence_span_ids": ["p3_b2"], '
        '"assumptions": ["..."], "figure_id": "fig4", '
        '"confidence": 0.0, "difficulty": 0.0, "pedagogical_gain": 0.0, '
        '"support_type": "independent|necessary"}]}'
    ),
    "Assumption[]": (
        '{"assumptions": [{"id": "a1", "text": "...", '
        '"kind": "scope|measurement|generalization|implementation", '
        '"source": "paper_explicit|paper_implicit|pedagogical", '
        '"span_id": "p1_b4", "weakens_how": "...", '
        '"support_type": "independent|necessary"}]}'
    ),
    "ExternalQueries": (
        '{"queries": {"support": "...", "contradict": "...", '
        '"boundary": "...", "methodology": "..."}}'
    ),
    "Switchboard": (
        '{"base_status": "strong|conditional", "learning_goal": "...", '
        '"misconception": "...", "status_rules": [{"assumption_id": "a1", '
        '"status": "conditional|weak", "because": "...", '
        '"attribution": {"kind": "paper|pedagogical", '
        '"span_id": "p2_t0r1c3"}}], '
        '"explanation": {"novice": "...", "domain_student": "...", '
        '"expert": "..."}, "fidelity_warning": null}'
    ),
    "ClaimExplanation": '{"explanation": "..."}',
    "CriticSoftCheck": (
        '{"findings": [{"assumption_id": "a1", "acceptable": true, '
        '"detail": "specific consequence present"}]}'
    ),
    "BottleneckSpec": (
        '{"question": "...", "why_hard": "...", "mechanism_kind": "calibration|claim_conditions", '
        '"candidate_controls": [], "candidate_observables": [], "learning_payoff": 0.0}'
    ),
    "PrimitiveRoute": '{"route": "scaling_comparison|generated_schematic|assumption_switchboard"}',
    "PanelComposition": '{"panels": [{"primitive": "...", "question": "...", "notice": "..."}]}',
    "KoreanEditorial": (
        '{"hook": "...", "instruction": "...", "caveat": "...", '
        '"summary": ["..."], "critical_note": "..."}'
    ),
}


# These models are intentionally private adapters.  The public LLM protocol
# remains ``structured(...) -> dict`` so existing stages and MockLLM do not
# change.  Agents SDK validates the model output before it reaches the stage.
try:  # keep importing the offline package possible in a minimal environment
    from pydantic import BaseModel, ConfigDict, Field

    class _GraphClaimModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        id: str
        parent_id: str | None = None
        role: str = "subclaim"
        order: int = 0
        text: str
        evidence_span_ids: list[str] = Field(default_factory=list)
        assumptions: list[str] = Field(default_factory=list)
        figure_id: str | None = None
        confidence: float = 0.5
        difficulty: float = 0.5
        pedagogical_gain: float = 0.5
        support_type: str = "independent"

    class _GraphClaimsModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        root_claim_id: str | None = None
        claims: list[_GraphClaimModel] = Field(default_factory=list)

    class _AssumptionModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        id: str
        text: str
        kind: str
        source: str
        span_id: str | None = None
        weakens_how: str = ""
        support_type: str = "independent"

    class _AssumptionsModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        assumptions: list[_AssumptionModel] = Field(default_factory=list)

    class _ExternalQueriesModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        queries: dict[str, str] = Field(default_factory=dict)

    class _AttributionModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        kind: str = "pedagogical"
        span_id: str | None = None
        evidence_id: str | None = None

    class _StatusRuleModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        assumption_id: str
        status: str
        because: str
        attribution: _AttributionModel = Field(default_factory=_AttributionModel)

    class _SwitchboardModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        title: str = ""
        base_status: str = "strong"
        learning_goal: str = ""
        misconception: str = ""
        status_rules: list[_StatusRuleModel] = Field(default_factory=list)
        explanation: dict[str, str] = Field(default_factory=dict)
        fidelity_warning: str | None = None

    class _ClaimExplanationModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        explanation: str = ""

    class _SoftFindingModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        assumption_id: str
        acceptable: bool = True
        detail: str = ""

    class _CriticSoftModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        findings: list[_SoftFindingModel] = Field(default_factory=list)

    class _BottleneckModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        question: str = ""
        why_hard: str = ""
        mechanism_kind: str = ""
        candidate_controls: list[str] = Field(default_factory=list)
        candidate_observables: list[str] = Field(default_factory=list)
        learning_payoff: float = 0.0

    class _PrimitiveRouteModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        route: str = "assumption_switchboard"

    class _PanelCompositionModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        panels: list[dict[str, Any]] = Field(default_factory=list)

    class _KoreanEditorialModel(BaseModel):
        model_config = ConfigDict(extra="ignore")
        hook: str = ""
        instruction: str = ""
        caveat: str = ""
        summary: list[str] = Field(default_factory=list)
        critical_note: str = ""

    PYDANTIC_OUTPUTS = {
        "GraphClaims": _GraphClaimsModel,
        "Assumption[]": _AssumptionsModel,
        "ExternalQueries": _ExternalQueriesModel,
        "Switchboard": _SwitchboardModel,
        "ClaimExplanation": _ClaimExplanationModel,
        "CriticSoftCheck": _CriticSoftModel,
        "BottleneckSpec": _BottleneckModel,
        "PrimitiveRoute": _PrimitiveRouteModel,
        "PanelComposition": _PanelCompositionModel,
        "KoreanEditorial": _KoreanEditorialModel,
    }
except ImportError:  # pragma: no cover - requirements include pydantic
    PYDANTIC_OUTPUTS = {}

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class EventBusTraceProcessor:
    """Agents SDK tracing -> EventBus raw channel.

    Duck-typed against agents.tracing.TracingProcessor rather than subclassing
    it, so this module still imports with the SDK absent.

    The raw channel carries SDK events unmodified. `export()` is the exact dict
    the SDK ships to its own backend and it goes onto the bus verbatim under
    `sdk` -- nested, not splatted, because the SDK payload has its own `id` and
    `object` keys that would otherwise overwrite the Event envelope. Nothing is
    renamed, dropped, reordered or summarised on the way through.
    """

    def __init__(self, bus: EventBus | None = None):
        self.bus = bus

    def bind(self, bus: EventBus) -> None:
        """Processors are registered process-wide, a bus belongs to one run."""
        self.bus = bus

    def _forward(self, hook: str, obj: Any) -> None:
        bus = self.bus
        if bus is None:  # spans from a run we are not watching
            return
        try:
            payload = obj.export()
        except Exception as e:  # noqa: BLE001 -- a tracing hook must not raise
            bus.emit_raw(f"agents.{hook}", sdk=None, error=str(e))
            return
        bus.emit_raw(f"agents.{hook}", sdk=payload)

    def on_trace_start(self, trace: Any) -> None:
        self._forward("trace_start", trace)

    def on_trace_end(self, trace: Any) -> None:
        self._forward("trace_end", trace)

    def on_span_start(self, span: Any) -> None:
        self._forward("span_start", span)

    def on_span_end(self, span: Any) -> None:
        self._forward("span_end", span)

    def shutdown(self) -> None:
        self.bus = None

    def force_flush(self) -> None:
        """Nothing is buffered -- forwarding is synchronous."""


class OpenAIAgentsLLM:
    """Agents SDK behind MockLLM's structured() signature.

    The SDK is imported lazily: the offline demo and the tests must keep
    running on a machine that has never installed it.

    tracing: 'add' keeps the SDK's own exporter and tees onto the bus,
    'replace' sends spans to the bus only, 'off' registers nothing.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout_s: float | None = 30.0,
        tracing: Literal["add", "replace", "off"] = "replace",
        instructions: dict[str, str] | None = None,
    ):
        self.model = model or os.getenv("PLAYGROUND_MODEL") or "gpt-5.6-luna"
        self.timeout_s = timeout_s
        self.tracing = tracing
        self.instructions = {**ROLE_INSTRUCTIONS, **(instructions or {})}
        self.processor = EventBusTraceProcessor()
        self._agents: Any = None
        self._traced = False

    # -- LLM protocol --

    def structured(self, *, role, prompt, schema_hint, bus):
        agents = self._sdk()
        self.processor.bind(bus)  # SDK spans land on this run's bus
        call_id = bus.tool_call(
            "llm.structured", role=role, prompt_chars=len(prompt),
            schema=schema_hint,
        )
        agent_kwargs = {
            "name": role,
            "instructions": self._instructions(role, schema_hint),
            **({"output_type": self._output_type(agents, schema_hint)}
               if self._output_type(agents, schema_hint) else {}),
            **({"model": self.model} if self.model else {}),
        }
        agent_kwargs.update(self._model_settings(agents))
        agent = agents.Agent(
            **agent_kwargs,
        )
        try:
            out = _as_object(_run_sync(self._call(agents, agent, role, prompt)))
        except Exception as e:  # noqa: BLE001 -- every failure mode is fatal here
            detail = _redact_sensitive(e)
            bus.tool_result(call_id, None, error=f"{type(e).__name__}: {detail}")
            bus.decision(role, "LLM 호출 실패 -> 이 스테이지는 결과 없음",
                         error=detail)
            raise LLMError(f"{role}: {detail}") from e
        bus.tool_result(call_id, out)
        return out

    # -- SDK plumbing --

    def _sdk(self) -> Any:
        if self._agents is None:
            try:
                import agents
            except ImportError as e:
                raise LLMError(
                    "openai-agents is not installed (pip install openai-agents)"
                ) from e
            self._agents = agents
            self._install_tracing(agents)
        return self._agents

    def _install_tracing(self, agents: Any) -> None:
        if self._traced or self.tracing == "off":
            return
        if self.tracing == "replace":
            agents.set_trace_processors([self.processor])
        else:
            agents.add_trace_processor(self.processor)
        self._traced = True

    async def _call(self, agents: Any, agent: Any, role: str, prompt: str) -> Any:
        # one trace per stage call, so the raw log groups the way the DAG does
        with agents.trace(workflow_name=f"playground.{role}"):
            coro = agents.Runner.run(agent, prompt)
            result = await (
                asyncio.wait_for(coro, self.timeout_s) if self.timeout_s else coro
            )
        return result.final_output

    def _instructions(self, role: str, schema_hint: str) -> str:
        base = (
            self.instructions.get(role)
            or prompts.load(role)
            or self.instructions["_default"]
        )
        shape = SCHEMA_SHAPES.get(schema_hint)
        return "\n\n".join(filter(None, [
            base,
            f"Return one JSON object matching {schema_hint}. No prose, no "
            f"markdown fence, no keys outside the shape.",
            f"Exact shape:\n{shape}" if shape else "",
        ]))

    @staticmethod
    def _output_type(agents: Any, schema_hint: str):
        model = PYDANTIC_OUTPUTS.get(schema_hint)
        if model is None:
            return None
        # Dict-valued fields such as Switchboard.explanation and
        # ExternalQueries.queries cannot be represented by the SDK's strict
        # JSON-schema subset. The Pydantic model still validates the shape;
        # this wrapper only relaxes provider schema generation for those maps.
        try:
            return agents.AgentOutputSchema(model, strict_json_schema=False)
        except AttributeError:
            return model

    @staticmethod
    def _model_settings(agents: Any) -> dict[str, Any]:
        """Set the explicit low-latency reasoning policy when supported."""
        effort = os.getenv("PLAYGROUND_REASONING_EFFORT", "none")
        try:
            from openai.types.shared import Reasoning
            return {"model_settings": agents.ModelSettings(
                reasoning=Reasoning(effort=effort), verbosity="low"
            )}
        except (ImportError, AttributeError, TypeError, ValueError):
            return {}


def _run_sync(coro):
    """Runner.run_sync refuses to run inside a live event loop. The stages are
    synchronous but the host serving them may not be."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _as_object(raw: Any) -> dict:
    """final_output is free text unless an output_type was set. Tolerate a
    fence or a sentence of padding, refuse anything that is not an object --
    a half-parsed spec is worse than a refused one."""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        out = raw.model_dump()
        if isinstance(out, dict):
            return out
    if raw is None or not str(raw).strip():
        raise LLMError("empty model output")
    text = str(raw).strip()
    fenced = FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        lo, hi = text.find("{"), text.rfind("}")
        if lo != -1 and hi > lo:
            text = text[lo:hi + 1]
    try:
        out = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"model did not return JSON: {text[:200]}") from e
    if not isinstance(out, dict):
        raise LLMError(f"expected a JSON object, got {type(out).__name__}")
    return out
