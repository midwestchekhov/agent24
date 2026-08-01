#!/usr/bin/env python3
"""Run live defense inputs through independent FastAPI servers in parallel.

This is an acceptance harness, not a mock runner.  Each input gets its own
local server because the demo server intentionally permits only one active run.
Provider errors are summarized by type; response bodies are never printed.

Examples:
    python scripts/live_server_batch.py
    python scripts/live_server_batch.py --input fixtures/guo17a.pdf \
        --input fixtures/demo/01_clinical_sepsis_ews.md
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = sorted((ROOT / "fixtures" / "demo").glob("*.md"))


def _request(method: str, url: str, *, body: bytes | None = None,
             content_type: str | None = None, timeout: float = 15.0) -> bytes:
    headers = {"Content-Type": content_type} if content_type else {}
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _multipart(path: Path, title: str) -> tuple[bytes, str]:
    boundary = f"----paper-defense-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"), b"\r\n",
        ])

    field("source_title", title)
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        mime = mimetypes.guess_type(path.name)[0] or "application/pdf"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="pdf"; filename="{path.name}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode(),
            data, b"\r\n",
        ])
    else:
        field("source_text", data.decode("utf-8"))
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _wait_health(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            _request("GET", f"http://127.0.0.1:{port}/api/health", timeout=2)
            return True
        except (OSError, HTTPError, URLError):
            time.sleep(0.25)
    return False


def _summary(payload: dict, events: str) -> dict:
    artifact = payload.get("artifact") or {}
    evidence = artifact.get("external_evidence") or {}
    ledger = (payload.get("analysis") or {}).get("evidence_ledger") or {}
    scope = artifact.get("defensible_scope") or {}
    critic_events = [
        event for event in payload.get("raw_events") or []
        if event.get("actor") == "defense_critic"
    ]
    fatal_codes = sorted({
        str(code)
        for event in critic_events
        for code in event.get("fatal_codes") or []
    })
    warning_codes = sorted({
        str(code)
        for event in critic_events
        for code in event.get("warning_codes") or []
    })
    return {
        "mode": payload.get("mode"),
        "primitive": artifact.get("primitive"),
        "frontier": bool((artifact.get("target_claim") or {}).get("id")),
        "assumptions": len(artifact.get("assumptions") or []),
        "questions": len(artifact.get("attack_questions") or []),
        "supports": len(evidence.get("supports") or []),
        "qualifies": len(evidence.get("qualifies") or []),
        "challenges": len(evidence.get("challenges") or []),
        "unresolved": len(evidence.get("unresolved") or []),
        "grounded": sum(
            1 for record in ledger.get("records", [])
            if record.get("relation") != "unresolved" and record.get("chunks")
        ),
        "scope": bool(scope.get("statement")),
        "critic_fatal_codes": fatal_codes,
        "critic_warning_codes": warning_codes,
        "sse": {
            name: len(re.findall(rf"^event: {name}$", events, re.MULTILINE))
            for name in ("raw", "status", "complete", "error")
        },
    }


def run_one(path: Path, *, port: int, out_dir: Path) -> dict:
    name = path.stem
    log_path = out_dir / f"{name}.server.log"
    event_path = out_dir / f"{name}.events"
    payload_path = out_dir / f"{name}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "playground.server", "--live-fast", "--port", str(port)],
        cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT,
    )
    started = time.monotonic()
    try:
        if not _wait_health(port, time.monotonic() + 20):
            return {"input": str(path), "mode": "error", "error_type": "server_startup"}
        body, content_type = _multipart(path, path.stem)
        created = json.loads(_request(
            "POST", f"http://127.0.0.1:{port}/api/runs", body=body,
            content_type=content_type, timeout=15,
        ))
        run_id = created.get("run_id")
        if not run_id:
            return {"input": str(path), "mode": "error", "error_type": "run_creation"}
        events = _request(
            "GET", f"http://127.0.0.1:{port}{created['events_url']}", timeout=135,
        ).decode("utf-8", errors="replace")
        payload = json.loads(_request(
            "GET", f"http://127.0.0.1:{port}{created['payload_url']}", timeout=15,
        ))
        event_path.write_text(events, encoding="utf-8")
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = _summary(payload, events)
        result.update({"input": str(path), "elapsed_seconds": round(time.monotonic() - started, 3),
                       "payload": str(payload_path), "within_deadline":
                       (payload.get("run") or {}).get("elapsed_seconds", 999) <= 120})
        return result
    except (OSError, ValueError, KeyError, HTTPError, URLError) as exc:
        return {"input": str(path), "mode": "error", "error_type": type(exc).__name__}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log_handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel live FastAPI defense acceptance")
    parser.add_argument("--input", action="append", dest="inputs",
                        help="PDF or Markdown input; repeatable")
    parser.add_argument("--out-dir", default="/tmp/paper-defense-live-batch")
    parser.add_argument("--base-port", type=int, default=8200)
    args = parser.parse_args()
    paths = [Path(item) for item in args.inputs] if args.inputs else DEFAULT_INPUTS
    paths = [path if path.is_absolute() else ROOT / path for path in paths]
    out_dir = Path(args.out_dir)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(paths) or 1) as executor:
        futures = {
            executor.submit(run_one, path, port=args.base_port + index, out_dir=out_dir): path
            for index, path in enumerate(paths)
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    results.sort(key=lambda item: item.get("input", ""))
    print(json.dumps({
        "inputs": len(results),
        "completed": sum(item.get("mode") in {"complete", "partial"} for item in results),
        "within_deadline": sum(bool(item.get("within_deadline")) for item in results),
        "results": results,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
