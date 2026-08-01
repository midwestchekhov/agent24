# Second Monitor

Start the live monitor with one PDF input:

```bash
python -m playground.monitor --pdf fixtures/sample.pdf
```

Open the printed URL in the main browser. The page reads `DemoPayloadV1` from
`/payload`; the raw monitor reads only `/events` over SSE. Each SSE data line is
the original `Event.to_json()` object. Status-channel events are never added to
`raw_events` or the raw stream.

The monitor emits `run_end` after normal or failed execution. The browser closes
the stream after that event, shows reconnecting state for transient disconnects,
and renders malformed JSON as an error row instead of stopping the event list.
