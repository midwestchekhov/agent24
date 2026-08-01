(() => {
  "use strict";

  const rank = { strong: 0, conditional: 1, weak: 2 };
  const sourceMeta = {
    paper_explicit: { label: "논문 명시", icon: "●" },
    paper_implicit: { label: "논문 암묵", icon: "◐" },
    pedagogical: { label: "교육적 판단", icon: "◇" },
  };
  let currentData = null;
  let eventSource = null;

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

  function schemaSupported(data) {
    return data && (data.schema_version === "1.0" || data.schema_version === "1.1");
  }

  function assumptionsFor(data) {
    if (data.artifact?.primitive === "evidence_assumption_map") {
      return data.artifact.assumption_map || [];
    }
    return data.artifact?.assumptions || [];
  }

  function graphFor(data) {
    return data.claim_graph || data.artifact?.claim_graph || null;
  }

  function renderClaims(data) {
    const target = document.querySelector("#claim-cards");
    target.replaceChildren();
    const graph = graphFor(data);
    const claims = graph
      ? graph.nodes.map((node) => ({
        ...(data.claims || []).find((claim) => claim.id === node.id),
        ...node,
      }))
      : (data.claims || []);
    const byId = new Map(claims.map((claim) => [claim.id, claim]));
    const depthOf = (claim) => {
      let depth = 0;
      const seen = new Set();
      let parent = claim.parent_id && byId.get(claim.parent_id);
      while (parent && !seen.has(parent.id)) {
        seen.add(parent.id);
        depth += 1;
        parent = parent.parent_id && byId.get(parent.parent_id);
      }
      return depth;
    };
    claims.forEach((claim) => {
      const active = claim.id === data.selected_claim_id;
      const value = claim.frontier_score ?? claim.score;
      const score = value == null ? "score —" : `${claim.frontier_score != null ? "frontier" : "score"} ${Number(value).toFixed(2)}`;
      const card = element("article", {
        className: `claim-card depth-${depthOf(claim)} ${active ? "is-active" : "is-readonly"}`,
        "aria-label": `${claim.id} ${active ? "자동 선택됨" : "읽기 전용 후보"}`,
      }, [
        element("div", { className: "card-topline" }, [
          element("span", { className: "claim-id", text: claim.id }),
          element("span", { className: "score", text: score }),
        ]),
        element("p", { className: "claim-text", text: claim.text || "" }),
        element("p", { className: "claim-hint", text: active
          ? "pedagogic frontier로 자동 선택됨"
          : `${claim.role || "candidate"} · ${claim.verification || "미상"}` }),
      ]);
      if (graph && claim.explanation) {
        card.append(element("p", { className: "claim-explanation", text: claim.explanation }));
      }
      target.append(card);
    });
    if (!claims.length) target.append(element("p", { className: "map-empty", text: "표시할 claim이 없습니다." }));
  }

  function evidenceCard(evidence, external = false) {
    const children = [
      element("div", { className: "evidence-meta", text: external
        ? `${evidence.id || "external"} · 외부 근거 · ${(evidence.facets || []).join(" / ") || "facet 없음"}`
        : `${evidence.span_id} · p.${evidence.page} · ${evidence.kind}` }),
    ];
    if (external && evidence.url) {
      children.push(element("a", { className: "evidence-link", href: evidence.url, target: "_blank", rel: "noreferrer", text: evidence.title || evidence.url }));
      children.push(element("p", { text: evidence.snippet || "snippet 없음" }));
    } else {
      children.push(element("p", { text: evidence.text || "" }));
    }
    return element("article", { className: `evidence-card${external ? " external-evidence" : ""}` }, children);
  }

  function renderEvidence(data) {
    const target = document.querySelector("#evidence-list");
    target.replaceChildren();
    const safeMap = data.artifact?.primitive === "evidence_assumption_map";
    const selected = (data.claims || []).find((claim) => claim.id === data.selected_claim_id);
    const paper = safeMap
      ? (data.artifact.evidence_map?.paper || [])
      : (selected?.evidence_span_ids || []).map((spanId) => ({ span_id: spanId, ...(data.spans?.[spanId] || {}) }));
    if (safeMap) {
      (data.artifact.evidence_map?.claim_input || []).forEach((span) => target.append(evidenceCard({ ...span, kind: "direct claim" })));
    }
    paper.forEach((span) => target.append(evidenceCard(span)));
    const external = safeMap
      ? (data.artifact.evidence_map?.external || [])
      : (data.external || data.artifact?.external || []);
    external.forEach((item) => target.append(evidenceCard(item, true)));
    if (!target.children.length) target.append(element("p", { className: "map-empty", text: "표시할 검증된 근거가 없습니다." }));
  }

  function attributionText(attribution = {}) {
    if (attribution.kind === "paper") return `논문 원문 · ${attribution.span_id}`;
    if (attribution.kind === "external") return `외부 근거 · ${attribution.evidence_id}`;
    return "교육적 판단 · 원문 밖의 설명";
  }

  function renderStatusCard(data, safeMap, refusal) {
    const target = document.querySelector("#status-card");
    target.className = `status-card ${refusal || safeMap ? "status-unsafe" : `status-${data.artifact?.base_status || "strong"}`}`;
    if (refusal) {
      target.replaceChildren(
        element("div", { className: "status-line" }, [
          element("span", { className: "status-label", text: "REFUSED" }),
          element("strong", { className: "status-badge", text: data.artifact.reason_code || "REFUSED" }),
        ]),
        element("p", { className: "status-summary", text: data.artifact.message || "검증 가능한 인터랙션을 만들 수 없습니다." }),
      );
      return;
    }
    if (safeMap) {
      target.replaceChildren(
        element("div", { className: "status-line" }, [
          element("span", { className: "status-label", text: "CRITIC VERDICT" }),
          element("strong", { className: "status-badge", text: "UNSAFE_TO_VISUALIZE" }),
        ]),
        element("p", { className: "status-summary", text: "검증되지 않은 참조가 있어 인터랙션을 비활성화했습니다." }),
      );
      return;
    }
    updateStatus();
  }

  function renderAssumptions(data, safeMap, refusal) {
    const target = document.querySelector("#assumption-list");
    target.replaceChildren();
    const assumptions = assumptionsFor(data);
    assumptions.forEach((assumption) => {
      const sourceKey = assumption.source || "unknown";
      const meta = sourceMeta[sourceKey] || { label: sourceKey, icon: "◇" };
      const source = element("span", { className: `source-tag source-${sourceKey}`, text: `${meta.icon} ${meta.label}` });
      if (safeMap || refusal) {
        target.append(element("article", { className: `assumption is-readonly source-${sourceKey}` }, [
          element("div", { className: "assumption-map-copy" }, [
            element("span", { className: "assumption-head" }, [source, element("span", { className: "assumption-kind", text: assumption.kind || "" })]),
            element("strong", { text: assumption.text || "" }),
            element("small", { text: `영향: ${assumption.weakens_how || "없음"}` }),
            element("small", { text: `귀속: ${assumption.span_id || "교육적 판단"}` }),
          ]),
        ]));
        return;
      }
      const input = element("input", { id: `toggle-${assumption.id}`, type: "checkbox", checked: "", "aria-describedby": `assumption-detail-${assumption.id}` });
      input.checked = true;
      input.addEventListener("change", updateStatus);
      target.append(element("article", { className: `assumption source-${sourceKey}` }, [
        input,
        element("label", { for: input.id }, [
          element("span", { className: "toggle-ui", "aria-hidden": "true" }),
          element("span", { className: "assumption-copy" }, [
            element("span", { className: "assumption-head" }, [source, element("span", { className: "assumption-kind", text: assumption.kind || "" })]),
            element("strong", { text: assumption.text || "" }),
            element("small", { id: `assumption-detail-${assumption.id}`, text: `꺼지면: ${assumption.weakens_how || "영향 정보 없음"}` }),
          ]),
        ]),
      ]));
    });
    if (!assumptions.length) target.append(element("p", { className: "map-empty", text: refusal ? "거절된 run에는 가정이 없습니다." : "표시할 가정이 없습니다." }));
  }

  function evaluateStatus(offIds) {
    const artifact = currentData?.artifact;
    const rules = artifact?.status_rules || [];
    const fired = rules.filter((rule) => offIds.has(rule.assumption_id));
    const base = artifact?.base_status || "strong";
    const status = [base, ...fired.map((rule) => rule.status)]
      .reduce((weakest, current) => (rank[current] > rank[weakest] ? current : weakest));
    return { status, fired, reasons: fired.filter((rule) => rule.status === status) };
  }

  function updateStatus() {
    const artifact = currentData?.artifact;
    if (!artifact || artifact.primitive !== "assumption_switchboard") return;
    const offIds = new Set(assumptionsFor(currentData)
      .filter((assumption) => {
        const toggle = document.querySelector(`#toggle-${assumption.id}`);
        return toggle && !toggle.checked;
      }).map((assumption) => assumption.id));
    const result = evaluateStatus(offIds);
    const target = document.querySelector("#status-card");
    target.className = `status-card status-${result.status}`;
    const byId = new Map(assumptionsFor(currentData).map((assumption) => [assumption.id, assumption]));
    target.replaceChildren(
      element("div", { className: "status-line" }, [
        element("span", { className: "status-label", text: "CLAIM STATUS" }),
        element("strong", { className: "status-badge", text: result.status }),
      ]),
      element("p", { className: "status-summary", text: result.reasons.length ? "꺼진 가정 때문에 현재 상태가 결정되었습니다." : "모든 가정이 켜져 있어 논문 원문의 지지가 유지됩니다." }),
      element("div", { className: "status-reasons" }, result.reasons.map((rule) => element("article", { className: "reason" }, [
        element("p", { text: rule.because }),
        element("small", { text: `${byId.get(rule.assumption_id)?.id || rule.assumption_id} · ${attributionText(rule.attribution)}` }),
      ]))),
    );
  }

  function renderEvents(events) {
    const target = document.querySelector("#event-stream");
    target.replaceChildren();
    events.filter((event) => event.type !== "status").forEach((event) => {
      target.append(element("li", { className: "event" }, [
        element("span", { className: "event-type", text: event.type }),
        element("code", { text: JSON.stringify(event) }),
      ]));
    });
  }

  function render(data) {
    if (!schemaSupported(data)) {
      document.body.textContent = "지원하지 않는 DemoPayload schema입니다.";
      return;
    }
    currentData = data;
    const artifact = data.artifact || {};
    const safeMap = artifact.primitive === "evidence_assumption_map";
    const refusal = artifact.primitive === "refusal";
    document.querySelector(".page-header h1").textContent = refusal
      ? "검증 가능한 인터랙션을 만들 수 없음"
      : safeMap ? "검증 가능한 근거와 가정" : "주장이 기대는 조건을 꺼보세요";
    document.querySelector(".page-header p:last-child").textContent = refusal
      ? artifact.message || "검증 가능한 claim이 없어 실행을 종료했습니다."
      : safeMap ? "참조 무결성 검사에 실패해 읽기 전용 map을 표시합니다."
      : "토글은 브라우저에서 규칙만 평가합니다.";
    renderClaims(data);
    renderEvidence(data);
    // Build assumption controls before calculating the derived status card so
    // the initial render reflects the same toggle state as later updates.
    renderAssumptions(data, safeMap, refusal);
    renderStatusCard(data, safeMap, refusal);
    renderEvents(data.raw_events || []);
  }

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = document.querySelector("#run-button");
    const status = document.querySelector("#run-status");
    button.disabled = true;
    if (eventSource) eventSource.close();
    const body = new FormData(form);
    const file = document.querySelector("#pdf-input").files[0];
    if (!file) body.delete("pdf");
    status.textContent = "run을 시작하는 중…";
    try {
      const response = await fetch("/api/runs", { method: "POST", body });
      const created = await response.json();
      if (!response.ok) throw new Error(created.detail || "run을 시작할 수 없습니다.");
      const liveEvents = [];
      eventSource = new EventSource(created.events_url);
      eventSource.addEventListener("status", (message) => {
        const payload = JSON.parse(message.data);
        status.textContent = payload.text || "실행 중…";
      });
      eventSource.addEventListener("raw", (message) => {
        liveEvents.push(JSON.parse(message.data));
        renderEvents(liveEvents);
      });
      eventSource.addEventListener("complete", async () => {
        eventSource.close();
        const result = await fetch(created.payload_url);
        const payload = await result.json();
        if (!result.ok) throw new Error(payload.detail || "payload를 읽을 수 없습니다.");
        render(payload);
        status.textContent = payload.mode === "refused" ? "refused로 종료" : "검증 완료";
        button.disabled = false;
      });
      eventSource.addEventListener("error", (message) => {
        // A named SSE error carries JSON; a native EventSource error has no data.
        if (message.data) {
          const payload = JSON.parse(message.data);
          status.textContent = payload.message || "run이 실패했습니다.";
          eventSource.close();
          button.disabled = false;
        }
      });
    } catch (error) {
      status.textContent = error.message || "요청 실패";
      button.disabled = false;
    }
  }

  document.querySelector("#run-form").addEventListener("submit", submit);
  if (window.PLAYGROUND_DATA) render(window.PLAYGROUND_DATA);
})();
