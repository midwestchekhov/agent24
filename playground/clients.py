"""External clients behind interfaces so the whole pipeline runs offline.

Swap MockLLM -> OpenAIAgentsLLM and MockSearch -> LinerSearch when the keys
land. Nothing else in the codebase changes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from . import prompts
from .events import EventBus
# clients -> stages.base is not a cycle: stages/__init__.py is empty and
# stages.base imports events/state only. The import buys LLMError a StageError
# base, so a dead API takes the pipeline's existing degrade/refuse path instead
# of crashing the demo.
from .stages.base import StageError


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
        bus.tool_result(call_id, out)
        return json.loads(json.dumps(out))  # deep copy


class MockSearch:
    def __init__(self, results: list[dict] | None = None):
        self.results = results or []

    def query(self, *, q, bus):
        call_id = bus.tool_call("search.query", q=q)
        bus.tool_result(call_id, self.results)
        return list(self.results)


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
    "Claim[]": (
        '{"claims": [{"id": "c1", "text": "...", '
        '"evidence_span_ids": ["p3_b2"], "assumptions": ["..."], '
        '"figure_id": "fig4", "confidence": 0.0}]}'
    ),
    "Assumption[]": (
        '{"assumptions": [{"id": "a1", "text": "...", '
        '"kind": "scope|measurement|generalization|implementation", '
        '"source": "paper_explicit|paper_implicit|pedagogical", '
        '"span_id": "p1_b4", "weakens_how": "..."}]}'
    ),
    "Switchboard": (
        '{"base_status": "strong|conditional", "learning_goal": "...", '
        '"misconception": "...", "status_rules": [{"assumption_id": "a1", '
        '"status": "conditional|weak", "because": "...", '
        '"attribution": {"kind": "paper|external|pedagogical", '
        '"span_id": "p2_t0r1c3", "evidence_id": null}}], '
        '"explanation": {"novice": "...", "domain_student": "...", '
        '"expert": "..."}, "fidelity_warning": null}'
    ),
}

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
        tracing: Literal["add", "replace", "off"] = "add",
        instructions: dict[str, str] | None = None,
    ):
        self.model = model or os.getenv("PLAYGROUND_MODEL")
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
        agent = agents.Agent(
            name=role,
            instructions=self._instructions(role, schema_hint),
            **({"model": self.model} if self.model else {}),
        )
        try:
            out = _as_object(_run_sync(self._call(agents, agent, role, prompt)))
        except Exception as e:  # noqa: BLE001 -- every failure mode is fatal here
            bus.tool_result(call_id, None, error=f"{type(e).__name__}: {e}")
            bus.decision(role, "LLM 호출 실패 -> 이 스테이지는 결과 없음",
                         error=str(e))
            raise LLMError(f"{role}: {e}") from e
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
