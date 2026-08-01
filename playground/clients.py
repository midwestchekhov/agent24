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


class MockLLM:
    """Returns canned fixtures keyed by role. Keeps the DAG exercisable before
    any prompt work exists."""

    def __init__(self, fixtures: dict[str, Any] | None = None):
        self.fixtures = fixtures or {}

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
ROLE_INSTRUCTIONS = {
    "claim_mapper": (
        "You map a paper's span index onto its checkable claims. Do not "
        "summarise and do not paraphrase into new prose -- a claim is a "
        "pointer, not a retelling. Every id in evidence_span_ids must appear "
        "verbatim in the input; a claim you cannot bind to a span does not "
        "exist and must be dropped. confidence is how sure you are that the "
        "cited span actually supports the claim, not how important it is."
    ),
    "explainer_designer": (
        "You design one manipulable mini-experiment for a single claim. You "
        "emit a schema -- never HTML, code, markup or a chart image. primitive "
        "must be one of the allowed names given in the input. A control with "
        "provenance 'variable' must carry the span_id it was read from. A "
        "number with provenance 'measured' must carry source_id from the "
        "paper's number pool; if you cannot bind it, mark it 'illustrative' "
        "and set fidelity_warning. explanation is keyed by reader level."
    ),
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
    "InteractionSpec": (
        '{"primitive": "...", "title": "...", "learning_goal": "...", '
        '"misconception": "...", "controls": [{"name": "...", '
        '"kind": "slider|toggle|select", '
        '"provenance": "variable|assumption|pedagogical_simplification", '
        '"span_id": "p2_t0r1c3", "min": 0.0, "max": 1.0, "default": 0.5}], '
        '"numbers": [{"value": 0.0, '
        '"provenance": "measured|derived|illustrative", '
        '"source_id": "num_p2_t0r1c3_0", "formula_refs": []}], '
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
        base = self.instructions.get(role) or self.instructions["_default"]
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
