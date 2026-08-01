"""Small stdlib-only raw EventBus bridge for the second monitor."""

from __future__ import annotations

import json
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from .events import Event, EventBus


class RawEventBridge:
    """Fan out raw EventBus events without changing their JSON payload."""

    def __init__(self, bus: EventBus):
        self._history: list[str] = []
        self._clients: set[queue.Queue[tuple[int, str] | None]] = set()
        self._closed = False
        self._lock = threading.Lock()
        bus.subscribe(self.publish, channel="raw")

    def publish(self, event: Event) -> None:
        if event.channel != "raw":
            return
        line = event.to_json()
        with self._lock:
            seq = len(self._history)
            self._history.append(line)
            clients = tuple(self._clients)
        for client in clients:
            client.put_nowait((seq, line))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            clients = tuple(self._clients)
        for client in clients:
            client.put_nowait(None)

    def stream(self, after: int = -1) -> Iterator[tuple[int, str] | None]:
        """Yield (seq, raw_json) pairs, starting after sequence `after`.

        `after` comes from the SSE Last-Event-ID header so a reconnecting
        browser only receives the events it missed.
        """
        client: queue.Queue[tuple[int, str] | None] = queue.Queue()
        with self._lock:
            history = list(enumerate(self._history))
            closed = self._closed
            if not closed:
                self._clients.add(client)
        for seq, line in history:
            if seq > after:
                yield seq, line
        if closed:
            yield None
            return
        try:
            while True:
                item = client.get()
                if item is not None and item[0] <= after:
                    continue
                yield item
                if item is None:
                    return
        finally:
            with self._lock:
                self._clients.discard(client)


class MonitorHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, *, bridge: RawEventBridge,
                 payload: Callable[[], dict[str, Any]], frontend_dir: Path):
        self.bridge = bridge
        self.payload = payload
        self.frontend_dir = frontend_dir
        super().__init__(address, MonitorRequestHandler)


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server: MonitorHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/events":
            self._events()
        elif path == "/payload":
            self._json(self.server.payload())
        else:
            self._static(path)

    def _events(self) -> None:
        try:
            after = int(self.headers.get("Last-Event-ID", ""))
        except ValueError:
            after = -1
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for item in self.server.bridge.stream(after=after):
                if item is None:
                    break
                seq, raw = item
                self.wfile.write(f"id: {seq}\ndata: {raw}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        if name not in {"index.html", "app.js", "styles.css", "data.js"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = self.server.frontend_dir / name
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }[file_path.suffix]
        if name == "index.html":
            marker = b"</head>"
            content = content.replace(
                marker, b'<script>window.LIVE_MONITOR=true;</script></head>', 1
            )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: Any) -> None:
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_monitor(
    *, host: str, port: int, bridge: RawEventBridge,
    payload: Callable[[], dict[str, Any]], frontend_dir: Path,
) -> MonitorHTTPServer:
    return MonitorHTTPServer(
        (host, port), bridge=bridge, payload=payload, frontend_dir=frontend_dir
    )
