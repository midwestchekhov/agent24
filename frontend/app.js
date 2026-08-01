(() => {
  "use strict";

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function loadData() {
    if (!window.LIVE_MONITOR) return window.PLAYGROUND_DATA;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const response = await fetch("/payload", { cache: "no-store" });
        const payload = await response.json();
        if (payload && payload.artifact && (payload.artifact.primitive ||
            (payload.raw_events || []).some((event) => event.type === "run_end"))) return payload;
      } catch (_error) {
        // The event stream reports connection state; keep polling the payload.
      }
      await sleep(250);
    }
    return null;
  }

  loadData().then((data) => render(data));

  function render(data) {
  if (!data || data.schema_version !== "1.0") {
    document.body.textContent = "지원하지 않는 DemoPayload schema입니다.";
    return;
  }
  const rank = { strong: 0, conditional: 1, weak: 2 };
  const sourceMeta = {
    paper_explicit: { label: "논문 명시", icon: "●" },
    paper_implicit: { label: "논문 암묵", icon: "◐" },
    pedagogical: { label: "교육적 판단", icon: "◇" },
  };

  const selectedClaim = data.claims.find((claim) => claim.id === data.selected_claim_id);
  const byId = (items) => new Map(items.map((item) => [item.id, item]));
  const assumptions = data.artifact.assumptions || [];
  const assumptionsById = byId(assumptions);
  const seenEventIds = new Set();

  function element(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(options).forEach(([key, value]) => {
      if (key === "className") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    });
    children.flat().filter(Boolean).forEach((child) => node.append(child));
    return node;
  }

  function evaluateStatus(offIds) {
    const fired = data.artifact.status_rules.filter((rule) => offIds.has(rule.assumption_id));
    const status = [data.artifact.base_status, ...fired.map((rule) => rule.status)]
      .reduce((weakest, current) => (rank[current] > rank[weakest] ? current : weakest));
    return { status, fired, reasons: fired.filter((rule) => rule.status === status) };
  }

  function attributionText(attribution) {
    if (attribution.kind === "paper") return `논문 원문 · ${attribution.span_id}`;
    if (attribution.kind === "external") return `외부 근거 · ${attribution.evidence_id}`;
    return "교육적 판단 · 원문 밖의 설명";
  }

  function renderClaims() {
    const target = document.querySelector("#claim-cards");
    data.claims.forEach((claim) => {
      const active = claim.id === data.selected_claim_id;
      const card = element("article", {
        className: `claim-card ${active ? "is-active" : "is-readonly"}`,
        "aria-label": `${claim.id} ${active ? "자동 선택됨" : "읽기 전용 후보"}`,
      });
      card.append(
        element("div", { className: "card-topline" }, [
          element("span", { className: "claim-id", text: claim.id }),
          element("span", { className: "score", text: claim.score == null ? "claim" : `score ${claim.score.toFixed(2)}` }),
        ]),
        element("p", { className: "claim-text", text: claim.text }),
        element("p", { className: "claim-hint", text: active ? "최고 score로 자동 선택됨" : "읽기 전용 후보" }),
      );
      target.append(card);
    });
  }

  function renderEvidence() {
    const target = document.querySelector("#evidence-list");
    if (!selectedClaim) {
      target.textContent = "선택된 claim이 없습니다.";
      return;
    }
    const spanIds = selectedClaim.evidence_span_ids || [];
    if (!spanIds.length) {
      (data.external || []).filter((item) => item.claim_id === selectedClaim.id).forEach((item) => {
        target.append(element("article", { className: "evidence-card" }, [
          element("div", { className: "evidence-meta", text: `${item.id} · ${item.facets.join(" / ")}` }),
          element("p", { text: item.title || item.url }),
          element("p", { text: item.snippet }),
        ]));
      });
      return;
    }
    spanIds.forEach((spanId) => {
      const span = data.spans[spanId];
      target.append(element("article", { className: "evidence-card" }, [
        element("div", { className: "evidence-meta", text: `${spanId} · p.${span.page} · ${span.kind}` }),
        element("p", { text: span.text }),
      ]));
    });
  }

  function renderAssumptions() {
    const target = document.querySelector("#assumption-list");
    if (data.artifact.experiment) {
      document.querySelector("#assumptions-heading").textContent = "실험 절차";
      const experiment = data.artifact.experiment;
      target.append(element("article", { className: "experiment-card" }, [
        element("p", { className: "claim-text", text: experiment.setup }),
        ...experiment.steps.map((step, index) => element("article", { className: "assumption" }, [
          element("strong", { text: `${index + 1}. ${step.instruction}` }),
          element("small", { text: `관찰할 것: ${step.look_for}` }),
        ])),
        element("h3", { text: "성찰 질문" }),
        ...experiment.reflection_questions.map((question) => element("p", { text: question })),
      ]));
      return;
    }
    assumptions.forEach((assumption) => {
      const meta = sourceMeta[assumption.source];
      const input = element("input", {
        id: `toggle-${assumption.id}`,
        type: "checkbox",
        checked: "",
        "aria-describedby": `assumption-detail-${assumption.id}`,
      });
      input.checked = true;
      input.addEventListener("change", updateStatus);

      const source = element("span", {
        className: `source-tag source-${assumption.source}`,
        text: `${meta.icon} ${meta.label}`,
      });
      const label = element("label", { for: input.id }, [
        element("span", { className: "toggle-ui", "aria-hidden": "true" }),
        element("span", { className: "assumption-copy" }, [
          element("span", { className: "assumption-head" }, [source, element("span", { className: "assumption-kind", text: assumption.kind })]),
          element("strong", { text: assumption.text }),
          element("small", { id: `assumption-detail-${assumption.id}`, text: `꺼지면: ${assumption.weakens_how}` }),
        ]),
      ]);
      target.append(element("article", { className: `assumption source-${assumption.source}` }, [input, label]));
    });
  }

  function updateStatus() {
    if (data.artifact.experiment) {
      const target = document.querySelector("#status-card");
      target.className = "status-card status-conditional";
      target.replaceChildren(
        element("div", { className: "status-line" }, [
          element("span", { className: "status-label", text: "EXPERIMENT" }),
          element("strong", { className: "status-badge", text: "READY" }),
        ]),
        element("p", { className: "status-summary", text: `${(data.external || []).length}개 검색 자료를 바탕으로 생성됨` }),
      );
      return;
    }
    const offIds = new Set(
      assumptions
        .filter((assumption) => !document.querySelector(`#toggle-${assumption.id}`).checked)
        .map((assumption) => assumption.id),
    );
    const result = evaluateStatus(offIds);
    const target = document.querySelector("#status-card");
    target.className = `status-card status-${result.status}`;
    target.replaceChildren(
      element("div", { className: "status-line" }, [
        element("span", { className: "status-label", text: "CLAIM STATUS" }),
        element("strong", { className: "status-badge", text: result.status }),
      ]),
      element("p", { className: "status-summary", text: result.reasons.length ? "꺼진 가정 때문에 현재 상태가 결정되었습니다." : "모든 가정이 켜져 있어 논문 원문의 지지가 유지됩니다." }),
      element("div", { className: "status-reasons" }, result.reasons.map((rule) => {
        const assumption = assumptionsById.get(rule.assumption_id);
        return element("article", { className: "reason" }, [
          element("p", { text: rule.because }),
          element("small", { text: `${assumption.id} · ${attributionText(rule.attribution)}` }),
        ]);
      })),
    );
  }

  function renderEvents() {
    const target = document.querySelector("#event-stream");
    (data.raw_events || []).forEach(appendEvent);
    if (window.LIVE_MONITOR) connectRawStream();
    function appendEvent(event) {
      if (!event || !event.id || seenEventIds.has(event.id)) return;
      seenEventIds.add(event.id);
      target.append(element("li", { className: "event" }, [
        element("span", { className: "event-type", text: event.type || "malformed" }),
        element("code", { text: JSON.stringify(event) }),
      ]));
      target.lastElementChild.scrollIntoView({ block: "nearest" });
    }
    function connectRawStream() {
      const connection = document.querySelector("#connection-status");
      const source = new EventSource("/events");
      let ended = false;
      source.onopen = () => { connection.textContent = "raw stream 연결됨"; };
      source.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          appendEvent(event);
          if (event.type === "run_end") {
            ended = true;
            connection.textContent = "실행 종료 · raw stream 닫힘";
            source.close();
          }
        } catch (error) {
          target.append(element("li", { className: "event event-error", text: `malformed event: ${error.message}` }));
        }
      };
      source.onerror = () => {
        if (!ended) connection.textContent = "연결 끊김 · 재연결 중";
      };
    }
  }

  function renderFailure() {
    document.querySelector("#claim-cards").textContent = "실행이 거절되었거나 실패했습니다.";
    document.querySelector("#status-card").replaceChildren(
      element("strong", { className: "status-badge", text: "REFUSED" }),
      element("p", { className: "status-summary", text: "raw event stream에서 원인을 확인하세요." }),
    );
  }

  renderClaims();
  renderEvidence();
  if (!data.artifact.primitive) {
    renderFailure();
    renderEvents();
    return;
  }
  renderAssumptions();
  renderEvents();
  updateStatus();
  }
})();
