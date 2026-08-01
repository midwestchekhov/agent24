(() => {
  "use strict";

  const data = window.PLAYGROUND_DATA;
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
  const assumptionsById = byId(data.artifact.assumptions);

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
          element("span", { className: "score", text: `score ${claim.score.toFixed(2)}` }),
        ]),
        element("p", { className: "claim-text", text: claim.text }),
        element("p", { className: "claim-hint", text: active ? "최고 score로 자동 선택됨" : "읽기 전용 후보" }),
      );
      target.append(card);
    });
  }

  function renderEvidence() {
    const target = document.querySelector("#evidence-list");
    selectedClaim.evidence_span_ids.forEach((spanId) => {
      const span = data.spans[spanId];
      target.append(element("article", { className: "evidence-card" }, [
        element("div", { className: "evidence-meta", text: `${spanId} · p.${span.page} · ${span.kind}` }),
        element("p", { text: span.text }),
      ]));
    });
  }

  function renderAssumptions() {
    const target = document.querySelector("#assumption-list");
    data.artifact.assumptions.forEach((assumption) => {
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
    const offIds = new Set(
      data.artifact.assumptions
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
    data.raw_events.forEach((event) => {
      target.append(element("li", { className: "event" }, [
        element("span", { className: "event-type", text: event.type }),
        element("code", { text: JSON.stringify(event) }),
      ]));
    });
  }

  renderClaims();
  renderEvidence();
  renderAssumptions();
  renderEvents();
  updateStatus();
})();
