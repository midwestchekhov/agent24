"""Run the pipeline while serving the main UI and second monitor."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from .bridge import RawEventBridge, serve_monitor
from .events import EventBus
from .payload import to_demo_payload
from .pipeline import Pipeline
from .state import PaperState


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default="fixtures/sample.pdf")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--domain", default="ml", choices=["ml", "med"])
    args = parser.parse_args()

    bus = EventBus()
    bridge = RawEventBridge(bus)
    state = PaperState(source_path=args.pdf)
    run_id = "monitor-run"
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    server = serve_monitor(
        host=args.host,
        port=args.port,
        bridge=bridge,
        payload=lambda: to_demo_payload(state, bus, run_id=run_id),
        frontend_dir=frontend_dir,
    )

    def run() -> None:
        try:
            Pipeline.build(args.domain, bus=bus).run(state)
        except Exception as error:  # the monitor must expose client failures
            bus.emit_raw("stage_error", stage="monitor", error=str(error))
        finally:
            bus.emit_raw("run_end", mode=state.mode,
                         status="refused" if state.mode == "refused" else "complete")
            bridge.close()

    threading.Thread(target=run, name="playground-pipeline", daemon=True).start()
    print(f"http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
