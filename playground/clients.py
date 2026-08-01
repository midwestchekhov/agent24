"""External clients behind interfaces so the whole pipeline runs offline.

Swap MockLLM -> OpenAIAgentsLLM and MockSearch -> LinerSearch when the keys
land. Nothing else in the codebase changes.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .events import EventBus


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
