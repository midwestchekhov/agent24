(() => {
  "use strict";

  const rank = { strong: 0, conditional: 1, weak: 2 };
  const sourceMeta = {
    paper_explicit: { label: "논문 명시", icon: "●" },
    paper_implicit: { label: "논문 암묵", icon: "◐" },
    pedagogical: { label: "교육적 판단", icon: "◇" },
  };
  //: the one primitive with a real renderer. Everything else is described,
  //: not driven -- an honest static card beats a control that does nothing.
  const INTERACTIVE = "assumption_switchboard";
  let currentData = null;
  let currentModel = null;
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
    return data && ["1.0", "1.1", "2.0"].includes(data.schema_version);
  }

  function section(kicker, heading, children) {
    return element("section", { className: "exp-section" }, [
      kicker ? element("p", { className: "exp-kicker", text: kicker }) : null,
      heading ? element("h3", { className: "exp-heading", text: heading }) : null,
      ...children,
    ]);
  }

  function attributionText(attribution = {}) {
    if (attribution.kind === "paper") return `논문 원문 · ${attribution.span_id}`;
    if (attribution.kind === "external") return `외부 근거 · ${attribution.evidence_id}`;
    return "교육적 판단 · 원문 밖의 설명";
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

  // ---- switchboard panel: the only interactive primitive ----

  function assumptionsOf(model) {
    return (model && model.assumptions) || [];
  }

  function evaluateStatus(offIds) {
    const rules = (currentModel && currentModel.rules) || [];
    const fired = rules.filter((rule) => offIds.has(rule.assumption_id));
    const base = (currentModel && currentModel.base_status) || "strong";
    const status = [base, ...fired.map((rule) => rule.status)]
      .reduce((weakest, current) => (rank[current] > rank[weakest] ? current : weakest));
    return { status, fired, reasons: fired.filter((rule) => rule.status === status) };
  }

  function updateStatus() {
    if (!currentModel) return;
    const offIds = new Set(assumptionsOf(currentModel)
      .filter((assumption) => {
        const toggle = document.querySelector(`#toggle-${assumption.id}`);
        return toggle && !toggle.checked;
      }).map((assumption) => assumption.id));
    const result = evaluateStatus(offIds);
    const target = document.querySelector("#status-card");
    if (!target) return;
    target.className = `status-card status-${result.status}`;
    const byId = new Map(assumptionsOf(currentModel).map((a) => [a.id, a]));
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

  function assumptionRow(assumption, readonly) {
    const sourceKey = assumption.source || "unknown";
    const meta = sourceMeta[sourceKey] || { label: sourceKey, icon: "◇" };
    const source = element("span", { className: `source-tag source-${sourceKey}`, text: `${meta.icon} ${meta.label}` });
    if (readonly) {
      return element("article", { className: `assumption is-readonly source-${sourceKey}` }, [
        element("div", { className: "assumption-map-copy" }, [
          element("span", { className: "assumption-head" }, [source, element("span", { className: "assumption-kind", text: assumption.kind || "" })]),
          element("strong", { text: assumption.text || "" }),
          element("small", { text: `영향: ${assumption.weakens_how || "없음"}` }),
          element("small", { text: `귀속: ${assumption.span_id || "교육적 판단"}` }),
        ]),
      ]);
    }
    const input = element("input", { id: `toggle-${assumption.id}`, type: "checkbox", "aria-describedby": `assumption-detail-${assumption.id}` });
    input.checked = true;
    input.addEventListener("change", updateStatus);
    return element("article", { className: `assumption source-${sourceKey}` }, [
      input,
      element("label", { for: input.id }, [
        element("span", { className: "toggle-ui", "aria-hidden": "true" }),
        element("span", { className: "assumption-copy" }, [
          element("span", { className: "assumption-head" }, [source, element("span", { className: "assumption-kind", text: assumption.kind || "" })]),
          element("strong", { text: assumption.text || "" }),
          element("small", { id: `assumption-detail-${assumption.id}`, text: `꺼지면: ${assumption.weakens_how || "영향 정보 없음"}` }),
        ]),
      ]),
    ]);
  }

  function switchboardPanel(panel) {
    currentModel = panel.model || {};
    const assumptions = assumptionsOf(currentModel);
    const list = element("div", { id: "assumption-list", className: "assumption-list" },
      assumptions.length
        ? assumptions.map((a) => assumptionRow(a, false))
        : [element("p", { className: "map-empty", text: "표시할 가정이 없습니다." })]);
    return [
      element("div", { id: "status-card", className: "status-card", "aria-live": "polite" }),
      list,
      element("p", { className: "panel-foot", text: "토글은 브라우저에서 규칙만 평가합니다. 모델을 호출하지 않습니다." }),
    ];
  }

  // ---- every other primitive: described, not driven ----

  function modelSummary(panel) {
    const model = panel.model || {};
    if (model.type === "formula") return `모델: ${model.expression}`;
    if (model.type === "relation_graph") return `관계 ${(model.relations || []).length}개로 구성된 도식`;
    if (model.type === "state_graph") return `노드: ${(model.nodes || []).join(" · ")}`;
    if (model.type === "lookup_series") return `원문에 적힌 component 변화량 ${(model.deltas || []).length}개`;
    return model.type ? `모델 종류: ${model.type}` : "";
  }

  function staticPanel(panel) {
    const refs = (panel.provenance || []).flatMap((item) => item.source_refs || []);
    const feedback = Object.values(panel.feedback || {});
    return [
      element("p", { className: "panel-note", text: "이 패널은 아직 조작 렌더러가 없어 내용만 표시합니다." }),
      modelSummary(panel) ? element("p", { className: "panel-model", text: modelSummary(panel) }) : null,
      ...feedback.map((line) => element("p", { className: "panel-feedback", text: line })),
      refs.length ? element("p", { className: "panel-refs", text: `출처 span: ${[...new Set(refs)].join(", ")}` }) : null,
    ];
  }

  function renderPanel(panel, index) {
    const body = panel.primitive === INTERACTIVE ? switchboardPanel(panel) : staticPanel(panel);
    return section(`패널 ${index + 1} · ${panel.primitive}`, panel.question, [
      element("div", { className: "panel-body" }, body),
      panel.notice ? element("p", { className: "panel-notice", text: panel.notice }) : null,
    ]);
  }

  // ---- explainer ----

  function renderExplainer(artifact, data) {
    const target = document.querySelector("#explainer");
    const bottleneck = artifact.bottleneck || {};
    const note = artifact.critical_note || {};
    const conditions = note.conditions || [];
    const external = data.external || artifact.external || [];
    const sources = artifact.sources || [];

    target.replaceChildren(
      element("header", { className: "exp-head" }, [
        element("h2", { className: "exp-title", text: artifact.title || "설명 자료" }),
        artifact.thesis ? element("p", { className: "exp-thesis", text: artifact.thesis }) : null,
        artifact.editorial?.hook ? element("p", { className: "exp-hook", text: artifact.editorial.hook }) : null,
      ]),
      bottleneck.question ? section("막힘", bottleneck.question, [
        bottleneck.why_hard ? element("p", { text: bottleneck.why_hard }) : null,
      ]) : null,
      ...(artifact.panels || []).map(renderPanel),
      (artifact.summary || []).length ? section("여기까지만 알아도 충분", "요약", [
        element("ul", { className: "exp-list" }, artifact.summary.map((line) => element("li", { text: line }))),
      ]) : null,
      section("비판적으로 볼 지점", note.title || "원문과 설명의 경계", [
        note.text ? element("p", { text: note.text }) : null,
        ...conditions.map((cond) => element("article", { className: "condition" }, [
          element("strong", { text: cond.text || "" }),
          element("small", { text: `이 조건이 무너지면: ${cond.weakens_how || "영향 정보 없음"}` }),
          element("small", { text: `귀속: ${cond.span_id || "교육적 판단"}` }),
        ])),
      ]),
      (sources.length || external.length) ? section("출처", "원문과 외부 근거", [
        element("div", { className: "evidence-list" }, [
          ...sources.map((src) => evidenceCard({
            span_id: src.span_id, page: src.page, kind: src.kind,
            text: data.spans?.[src.span_id]?.text || "",
          })),
          ...external.map((item) => evidenceCard(item, true)),
        ]),
      ]) : null,
    );
    updateStatus();
  }

  function renderSafeMap(artifact, data) {
    const target = document.querySelector("#explainer");
    currentModel = null;
    const map = artifact.evidence_map || {};
    target.replaceChildren(
      element("header", { className: "exp-head" }, [
        element("h2", { className: "exp-title", text: artifact.title || "검증 가능한 근거와 가정" }),
        element("p", { className: "exp-thesis", text: artifact.safety?.message || "참조 무결성 검사에 실패해 읽기 전용 map을 표시합니다." }),
        element("p", { className: "exp-hook", text: `사유: ${(artifact.safety?.reason_codes || []).join(", ") || "UNSAFE_TO_VISUALIZE"}` }),
      ]),
      section("가정", "주장이 기대는 조건 (읽기 전용)",
        (artifact.assumption_map || []).length
          ? (artifact.assumption_map || []).map((a) => assumptionRow(a, true))
          : [element("p", { className: "map-empty", text: "표시할 가정이 없습니다." })]),
      section("근거", "원문과 외부 근거", [
        element("div", { className: "evidence-list" }, [
          ...(map.claim_input || []).map((span) => evidenceCard({ ...span, kind: "direct claim" })),
          ...(map.paper || []).map((span) => evidenceCard(span)),
          ...(map.external || []).map((item) => evidenceCard(item, true)),
        ]),
      ]),
    );
  }

  function renderRefusal(artifact) {
    currentModel = null;
    document.querySelector("#explainer").replaceChildren(
      element("header", { className: "exp-head" }, [
        element("h2", { className: "exp-title", text: artifact.title || "검증 가능한 인터랙션을 만들 수 없음" }),
        element("p", { className: "exp-thesis", text: artifact.message || "" }),
        element("p", { className: "exp-hook", text: `reason: ${artifact.reason_code || "REFUSED"} · stage: ${artifact.failed_stage || "-"}` }),
      ]),
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
    currentModel = null;
    const artifact = data.artifact || {};
    if (artifact.primitive === "refusal") renderRefusal(artifact);
    else if (artifact.primitive === "evidence_assumption_map") renderSafeMap(artifact, data);
    else renderExplainer(artifact, data);
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
