(() => {
  "use strict";

  const { element, renderArtifact } = window.DefenseReport;
  const MAX_PDF_BYTES = 25 * 1024 * 1024;
  const FIXTURES = ["complete", "complete_necessary", "partial", "partial_deadline", "refusal"];

  let eventSource = null;

  function setStatus(text) {
    document.querySelector("#run-status").textContent = text;
  }

  function setBusy(busy) {
    document.querySelector("#run-button").disabled = busy;
  }

  // ---- raw event stream ----------------------------------------------------
  // The bridge contract: the SSE data line is the original Event.to_json()
  // string. We render that string verbatim; parsing is only for the type
  // label, dedupe, and terminal detection. The server replays from the start
  // on reconnect, so dedupe by event id keeps the list correct.

  const seenEventIds = new Set();

  function resetEvents() {
    seenEventIds.clear();
    document.querySelector("#event-stream").replaceChildren();
  }

  function appendEvent(rawText, parsed) {
    const target = document.querySelector("#event-stream");
    if (parsed && parsed.id) {
      if (seenEventIds.has(parsed.id)) return;
      seenEventIds.add(parsed.id);
    }
    target.append(element("li", { className: `event${parsed ? "" : " event-error"}` }, [
      element("span", { className: "event-type", text: parsed && parsed.type ? parsed.type : "malformed" }),
      element("code", { text: rawText }),
    ]));
    target.lastElementChild.scrollIntoView({ block: "nearest" });
  }

  function renderEvents(events) {
    // Payload-sourced events only exist as objects, so serialization here is
    // unavoidable; the live SSE path never goes through this function.
    (events || []).forEach((event) => appendEvent(JSON.stringify(event), event));
  }

  // ---- audit ---------------------------------------------------------------
  // `analysis` is internal reasoning. It stays out of the report and lives here
  // behind a disclosure so an auditor can still reach it.

  function renderAudit(payload) {
    const run = payload.run || {};
    document.querySelector("#audit").replaceChildren(
      element("details", { className: "audit" }, [
        element("summary", { text: "내부 분석 (감사용)" }),
        element("dl", { className: "audit-run" }, Object.entries(run).flatMap(([key, value]) => [
          element("dt", { text: key }),
          element("dd", { text: typeof value === "object" ? JSON.stringify(value) : String(value) }),
        ])),
        element("pre", { className: "audit-json",
          text: JSON.stringify(payload.analysis || {}, null, 2) }),
      ]),
    );
  }

  // ---- render --------------------------------------------------------------

  function render(payload) {
    if (!payload || payload.schema_version !== "defense/1.0") {
      setStatus(`지원하지 않는 payload schema입니다: ${payload && payload.schema_version}`);
      return;
    }
    renderArtifact(document.querySelector("#report"), payload);
    renderAudit(payload);
    // A live run already streamed every event verbatim; re-rendering from the
    // payload would replace the original strings with re-serialized copies.
    if (seenEventIds.size === 0) renderEvents(payload.raw_events);
  }

  // ---- fixtures ------------------------------------------------------------

  async function loadFixture(name) {
    if (!FIXTURES.includes(name)) {
      setStatus(`알 수 없는 fixture: ${name} (${FIXTURES.join(", ")})`);
      return;
    }
    setStatus(`fixture 로딩 중: ${name}`);
    try {
      const response = await fetch(`fixtures/${name}.json`);
      if (!response.ok) throw new Error(`fixture를 읽을 수 없습니다 (${response.status})`);
      const payload = await response.json();
      resetEvents();
      render(payload);
      const note = (payload.run || {}).fixture_note;
      setStatus(`fixture: ${name}${note ? " · 합성 데이터" : " · 실제 실행 산출물"}`);
    } catch (error) {
      setStatus(error.message || "fixture 로딩 실패");
    }
  }

  // ---- run submission ------------------------------------------------------

  function validate(form) {
    const file = form.querySelector("#pdf-input").files[0];
    const text = form.querySelector("#text-input").value.trim();
    if (!file && !text) return "PDF를 올리거나 원문 텍스트를 붙여넣으세요.";
    if (file && file.size > MAX_PDF_BYTES) return "PDF가 25 MiB 제한을 넘습니다.";
    return null;
  }

  function detail(body) {
    const value = body && body.detail;
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((item) => item.msg || "").join(" · ");
    return "";
  }

  function attach(created) {
    let ended = false;
    eventSource = new EventSource(created.events_url);

    eventSource.addEventListener("status", (message) => {
      setStatus(JSON.parse(message.data).text || "실행 중…");
    });
    eventSource.addEventListener("raw", (message) => {
      // Render the original JSON string as-is; parse only for the label.
      let parsed = null;
      try {
        parsed = JSON.parse(message.data);
      } catch (_error) {
        // Malformed events still get an error row with the raw text.
      }
      appendEvent(message.data, parsed);
    });
    eventSource.addEventListener("complete", async () => {
      ended = true;
      eventSource.close();
      try {
        const result = await fetch(created.payload_url);
        const payload = await result.json();
        if (!result.ok) throw new Error(detail(payload) || "payload를 읽을 수 없습니다.");
        render(payload);
        setStatus(payload.mode === "refused" ? "거절로 종료"
          : payload.mode === "partial" ? "부분 보고서로 완료" : "방어 보고서 완료");
      } catch (error) {
        setStatus(error.message || "payload 로딩 실패");
      }
      setBusy(false);
    });
    eventSource.addEventListener("error", (message) => {
      // A named SSE error carries JSON; a native EventSource error has no data.
      if (message.data) {
        ended = true;
        eventSource.close();
        setStatus(JSON.parse(message.data).message || "run이 실패했습니다.");
        setBusy(false);
      } else if (!ended) {
        setStatus("연결 끊김 · 재연결 중");
      }
    });
  }

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const problem = validate(form);
    if (problem) {
      setStatus(problem);
      return;
    }
    setBusy(true);
    if (eventSource) eventSource.close();

    const body = new FormData(form);
    if (!form.querySelector("#pdf-input").files[0]) body.delete("pdf");
    setStatus("run을 시작하는 중…");
    try {
      const response = await fetch("/api/runs", { method: "POST", body });
      const created = await response.json();
      if (response.status === 409) {
        throw new Error("다른 run이 진행 중입니다. 끝난 뒤 다시 시도하세요.");
      }
      if (!response.ok) throw new Error(detail(created) || "run을 시작할 수 없습니다.");
      resetEvents();
      history.replaceState(null, "", `?run=${created.run_id}`);
      attach(created);
    } catch (error) {
      setStatus(error.message || "요청 실패");
      setBusy(false);
    }
  }

  // ---- reattach ------------------------------------------------------------
  // A reload mid-run used to lose the run entirely. The status route says
  // whether to resubscribe or just fetch the finished payload.

  async function reattach(runId) {
    try {
      const response = await fetch(`/api/runs/${runId}`);
      if (!response.ok) throw new Error("이전 run을 찾을 수 없습니다.");
      const record = await response.json();
      if (record.status === "failed") {
        setStatus("이전 run은 실패로 끝났습니다.");
        return;
      }
      if (record.status === "completed") {
        const result = await fetch(record.payload_url);
        const payload = await result.json();
        if (!result.ok) throw new Error(detail(payload) || "payload를 읽을 수 없습니다.");
        render(payload);
        setStatus("이전 run의 결과를 표시합니다.");
        return;
      }
      setBusy(true);
      setStatus("진행 중인 run에 다시 연결합니다…");
      attach(record);
    } catch (error) {
      setStatus(error.message || "이전 run 복구 실패");
    }
  }

  // ---- boot ----------------------------------------------------------------

  document.querySelector("#run-form").addEventListener("submit", submit);

  const params = new URLSearchParams(location.search);
  if (params.get("fixture")) loadFixture(params.get("fixture"));
  else if (params.get("run")) reattach(params.get("run"));
  else setStatus("PDF 또는 원문 텍스트를 제출하세요.");
})();
