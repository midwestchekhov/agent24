# Archived: Second Monitor

이 문서는 기존 explainer/second-monitor transport의 보존 기록이다. 현재 제품의
권위 있는 결과 계약은 `docs/defense-payload.md`의 `DefensePayloadV1`이며, 새
backend는 이 문서의 화면 전용 primitive나 switchboard를 구현 대상으로 삼지 않는다.

The second monitor is served by the unified bridge in `playground/server.py`
(team decision 2026-08-01; the earlier stdlib `bridge.py`/`monitor.py` were
retired in favor of it).

```bash
python -m playground.server            # offline mock run
python -m playground.server --live     # requires OPENAI_API_KEY / LINER_API_KEY
```

Open `http://127.0.0.1:8000/`. Submitting the form calls `POST /api/runs`,
then the page subscribes to `GET /api/runs/{run_id}/events` over SSE.

Channel contract:

- `event: raw` — the data line is the original `Event.to_json()` string.
  The browser renders that string verbatim; parsing is only used for the
  type label, dedupe, and terminal detection.
- `event: status` — human-readable progress for the main UI. Never mixed
  into the raw list.
- `event: complete` / `event: error` — terminal. After them the browser
  closes the stream and fetches `GET /api/runs/{run_id}/payload`
  (`DemoPayload` schema 1.1, or 2.0 for explainer runs).

Reconnect behaviour: the server replays the run's event log from the start;
the browser dedupes by event id, so the list stays correct and ordered.
Transient disconnects show "연결 끊김 · 재연결 중"; malformed data lines are
rendered as error rows with the raw text instead of stopping the list.

Verified by `tests/test_server_stream.py`: raw SSE lines are byte-identical
to `Event.to_json()`, in execution order, with status events excluded.
