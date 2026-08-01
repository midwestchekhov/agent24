"""Local FastAPI/SSE bridge for the Paper Playground demo."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .events import EventBus
from .payload import build_payload
from .pipeline import Pipeline
from .state import PaperState


MAX_PDF_BYTES = 25 * 1024 * 1024
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@dataclass
class RunRecord:
    run_id: str
    bus: EventBus = field(default_factory=EventBus)
    status: str = "queued"
    payload: dict | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    pdf_path: str | None = None
    completed_at: float = 0.0


class RunStore:
    """Small in-memory store; persistence and multi-user are out of scope."""

    def __init__(self, max_completed: int = 20):
        self.max_completed = max_completed
        self.records: dict[str, RunRecord] = {}
        self.lock = threading.Lock()
        self.active_id: str | None = None

    def reserve(self, record: RunRecord) -> None:
        with self.lock:
            if self.active_id is not None:
                raise RuntimeError("RUN_IN_PROGRESS")
            self.records[record.run_id] = record
            self.active_id = record.run_id

    def finish(self, record: RunRecord) -> None:
        with self.lock:
            if self.active_id == record.run_id:
                self.active_id = None
            record.completed_at = time.monotonic()
            completed = [r for r in self.records.values()
                         if r.status in {"completed", "failed"}]
            if len(completed) > self.max_completed:
                for old in sorted(completed, key=lambda r: r.completed_at)[:-self.max_completed]:
                    self.records.pop(old.run_id, None)


def create_app(*, live: bool = False, live_fast: bool = False) -> FastAPI:
    app = FastAPI(title="Paper Playground", version="1.1")
    store = RunStore()
    profile = "live-fast" if live_fast else "live" if live else "offline"
    app.state.playground_live = live or live_fast
    app.state.playground_profile = profile
    app.state.run_store = store

    @app.get("/api/health")
    async def health():
        return {"ok": True, "live": live or live_fast, "profile": profile}

    @app.post("/api/runs", status_code=202)
    async def create_run(
        claim_text: str | None = Form(default=None),
        source_text: str | None = Form(default=None),
        source_title: str | None = Form(default=None),
        pdf: UploadFile | None = File(default=None),
    ):
        claim_text = (claim_text or "").strip() or None
        source_text = (source_text or "").strip() or None
        source_title = (source_title or "").strip() or None
        if claim_text:
            raise HTTPException(422, "claim_text-only input is not supported; upload a PDF or provide source_text")
        if not source_text and pdf is None:
            raise HTTPException(422, "source_text or pdf is required")
        pdf_path = None
        if pdf is not None:
            data = await pdf.read(MAX_PDF_BYTES + 1)
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(413, "PDF exceeds 25 MiB limit")
            if not data.startswith(b"%PDF-"):
                raise HTTPException(422, "uploaded file is not a PDF")
            fd, pdf_path = tempfile.mkstemp(prefix="paper-playground-", suffix=".pdf")
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            try:
                import fitz
                document = fitz.open(pdf_path)
                try:
                    if getattr(document, "needs_pass", False):
                        raise ValueError("encrypted PDF")
                    if getattr(document, "is_repaired", False):
                        raise ValueError("damaged or truncated PDF")
                finally:
                    document.close()
            except Exception:
                Path(pdf_path).unlink(missing_ok=True)
                raise HTTPException(422, "uploaded file could not be opened as a PDF")

        record = RunRecord(run_id=uuid.uuid4().hex, pdf_path=pdf_path)
        try:
            store.reserve(record)
        except RuntimeError:
            if pdf_path:
                Path(pdf_path).unlink(missing_ok=True)
            raise HTTPException(409, "RUN_IN_PROGRESS")

        thread = threading.Thread(
            target=_run_record,
            args=(record, store, profile, claim_text, source_text, source_title),
            name=f"paper-playground-{record.run_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {
            "run_id": record.run_id,
            "status": "queued",
            "events_url": f"/api/runs/{record.run_id}/events",
            "payload_url": f"/api/runs/{record.run_id}/payload",
        }

    # `/payload` answers 409 while a run is active, so it cannot tell a browser
    # whether to keep waiting or give up. This does, which is what a reload
    # mid-run needs to reattach instead of starting over.
    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        record = store.records.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")
        with record.lock:
            return {
                "run_id": record.run_id,
                "status": record.status,
                "mode": (record.payload or {}).get("mode"),
                "events_url": f"/api/runs/{record.run_id}/events",
                "payload_url": f"/api/runs/{record.run_id}/payload",
            }

    @app.get("/api/runs/{run_id}/payload")
    async def get_payload(run_id: str):
        record = store.records.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")
        with record.lock:
            if record.status not in {"completed", "failed"}:
                raise HTTPException(409, "run is still active")
            if record.payload is None:
                raise HTTPException(500, record.error or "run failed")
            return JSONResponse(record.payload)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str):
        record = store.records.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")

        async def stream():
            cursor = 0
            terminal_sent = False
            while not terminal_sent:
                with record.lock:
                    events_now = list(record.bus.log)
                    status_now = record.status
                    error_now = record.error
                while cursor < len(events_now):
                    event = events_now[cursor]
                    cursor += 1
                    name = "raw" if event.channel == "raw" else "status"
                    yield f"event: {name}\ndata: {event.to_json()}\n\n"
                if status_now == "completed":
                    yield f"event: complete\ndata: {{\"run_id\": \"{run_id}\"}}\n\n"
                    terminal_sent = True
                elif status_now == "failed":
                    yield (
                        "event: error\n"
                        f"data: {{\"run_id\": \"{run_id}\", "
                        f"\"message\": \"{error_now or 'run failed'}\"}}\n\n"
                    )
                    terminal_sent = True
                else:
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # API routes are declared before the static mount. Directly opening the
    # server root still keeps the existing offline fixture useful.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


def _run_record(
    record: RunRecord,
    store: RunStore,
    profile: str,
    claim_text: str | None,
    source_text: str | None = None,
    source_title: str | None = None,
) -> None:
    with record.lock:
        record.status = "running"
    try:
        pipeline = Pipeline.build(
            bus=record.bus,
            live=profile != "offline",
            profile=profile,
        )
        state = PaperState(
            source_path=record.pdf_path,
            claim_text=claim_text,
            source_text=source_text,
            source_title=source_title,
        )
        pipeline.run(state)
        payload = build_payload(state, record.bus, run_id=record.run_id)
        with record.lock:
            record.payload = payload
            record.status = "completed"
    except Exception:
        # Do not expose local paths, provider responses, or credentials through
        # the HTTP error channel. The raw stage_error events remain available.
        with record.lock:
            record.status = "failed"
            record.error = "pipeline execution failed"
    finally:
        if record.pdf_path:
            Path(record.pdf_path).unlink(missing_ok=True)
            record.pdf_path = None
        store.finish(record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-fast", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    import uvicorn
    if args.live and args.live_fast:
        parser.error("--live and --live-fast are mutually exclusive")
    uvicorn.run(
        create_app(live=args.live, live_fast=args.live_fast),
        host=args.host, port=args.port,
    )


app = create_app(live=False)


if __name__ == "__main__":
    main()
