"""Event bus.

Two consumers with different needs:
  - second screen: raw, unmodified tool_call / tool_result events (contest rule)
  - main UI: coarse human-readable status only

Do not mix them. `emit_raw` is the contest-compliant firehose; `emit_status`
is the friendly one. A subscriber picks a channel.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal

Channel = Literal["raw", "status"]


@dataclass
class Event:
    type: str
    payload: dict
    channel: Channel = "raw"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "ts": self.ts, "type": self.type, **self.payload},
            ensure_ascii=False,
        )


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[Channel | None, Callable[[Event], None]]] = []
        self.log: list[Event] = []
        # Optional per-run runtime context.  Keeping this as an attribute
        # preserves the existing EventBus constructor and method contracts.
        self.runtime = None

    def subscribe(self, fn: Callable[[Event], None], channel: Channel | None = None):
        self._subs.append((channel, fn))

    def _dispatch(self, ev: Event) -> None:
        self.log.append(ev)
        for ch, fn in self._subs:
            if ch is None or ch == ev.channel:
                fn(ev)

    def emit_raw(self, type: str, **payload) -> Event:
        ev = Event(type=type, payload=payload, channel="raw")
        self._dispatch(ev)
        return ev

    def emit_status(self, text: str, **payload) -> Event:
        ev = Event(type="status", payload={"text": text, **payload}, channel="status")
        self._dispatch(ev)
        return ev

    # -- convenience wrappers so stages never hand-roll event shapes --

    def tool_call(self, name: str, **args) -> str:
        ev = self.emit_raw("tool_call", name=name, arguments=args)
        return ev.id

    def tool_result(self, call_id: str, result, error: str | None = None) -> None:
        self.emit_raw("tool_result", call_id=call_id, result=result, error=error)

    def decision(self, actor: str, text: str, **extra) -> None:
        """A judgement the agent made -- including judgements NOT to act.
        'we chose not to search this claim' is a stronger demo beat than
        another search call."""
        self.emit_raw("decision", actor=actor, text=text, **extra)
