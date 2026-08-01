// DefensePayloadV1 renderer.
//
// Reads `artifact` and `spans`. It never reads `analysis`: claim ids and
// candidate scores are internal reasoning, and putting them on screen would
// invite the reader to treat the claim graph as the deliverable.
window.DefenseReport = (() => {
  "use strict";

  const ATTACK_TYPES = {
    comparison_fairness: "비교 공정성",
    data_integrity: "데이터 무결성",
    measurement_validity: "측정 타당성",
    statistical_reliability: "통계 신뢰성",
    causal_attribution: "인과 귀속",
    external_validity: "외적 타당성",
    practical_relevance: "실무 관련성",
    implementation_fidelity: "구현 충실도",
  };
  const ORIGINS = {
    paper_explicit: "논문 명시",
    paper_implicit: "논문 암묵",
    analyst_inferred: "분석자 추론",
  };
  const SEVERITY = { high: "높음", medium: "중간", low: "낮음" };
  const RELATIONS = {
    supports: { label: "지지", note: "유사 조건에서 주장을 지지하는 근거" },
    qualifies: { label: "제한", note: "범위·효과 크기·조건을 제한하는 근거" },
    challenges: { label: "도전", note: "결과나 방법론을 직접 문제 삼는 근거" },
    unresolved: {
      label: "근거 확인 안 됨",
      note: "검색으로 찾았지만 본문 chunk가 없어 어느 방향으로도 해석하지 않았습니다.",
    },
  };
  const IMPACT = {
    holds: { label: "유지", note: "모든 가정이 켜져 있어 원문 범위가 그대로 유지됩니다." },
    narrows: { label: "축소", note: "주장이 좁아지지만 일부는 남습니다." },
    unsupported: { label: "지지 불가", note: "이 가정 없이는 주장을 지지할 수 없습니다." },
  };
  const SCOPE_BASIS = {
    paper_only: "원문 근거",
    analyst_inference: "분석자 추론",
  };

  let spanTable = {};

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

  function badge(text, className = "") {
    return element("span", { className: `badge ${className}`.trim(), text });
  }

  function section(kicker, heading, children) {
    return element("section", { className: "rep-section" }, [
      element("div", { className: "rep-section-head" }, [
        kicker ? element("p", { className: "rep-kicker", text: kicker }) : null,
        heading ? element("h3", { className: "rep-heading", text: heading }) : null,
      ]),
      ...children,
    ]);
  }

  // ---- source quotes -------------------------------------------------------
  // Every source_ref is an opaque span id. Rendering the id alone would ask the
  // reader to trust the report; rendering the text lets them check it.

  function quote(spanId) {
    const span = spanTable[spanId];
    if (!span) {
      return element("article", { className: "quote quote-missing" }, [
        element("small", { text: `${spanId} · 원문을 payload에서 찾을 수 없습니다.` }),
      ]);
    }
    return element("article", { className: "quote" }, [
      element("small", { className: "quote-meta",
        text: `${spanId} · p.${span.page} · ${span.section}` }),
      element("p", { text: span.text }),
    ]);
  }

  function quotes(refs, label = "원문") {
    const list = (refs || []).filter((ref) => ref);
    if (!list.length) return null;
    return element("details", { className: "quote-set" }, [
      element("summary", { text: `${label} ${list.length}곳 보기` }),
      element("div", { className: "quote-list" }, list.map(quote)),
    ]);
  }

  // ---- 1. target claim -----------------------------------------------------

  function targetClaim(artifact) {
    const target = artifact.target_claim || {};
    const reason = artifact.selection_reason || {};
    const scores = [
      ["중요도", reason.importance],
      ["취약성", reason.vulnerability],
      ["범위 격차", reason.scope_gap],
      ["원문 근거", reason.source_grounding],
    ].filter(([, value]) => typeof value === "number");

    return section("01 · 공격 지점", "심사자가 먼저 물을 주장", [
      element("blockquote", { className: "target-claim", text: target.text || "" }),
      reason.why_attackable
        ? element("p", { className: "rep-why", text: reason.why_attackable })
        : null,
      scores.length
        ? element("dl", { className: "score-row" }, scores.flatMap(([label, value]) => [
            element("dt", { text: label }),
            element("dd", { text: value.toFixed(2) }),
          ]))
        : null,
      quotes(target.source_refs, "근거 원문"),
    ]);
  }

  // ---- 2. weak point -------------------------------------------------------

  function weakPoint(artifact) {
    if (!artifact.weak_point) return null;
    return section("02 · 약한 고리", "무엇이 무너질 수 있는가", [
      element("p", { className: "weak-point", text: artifact.weak_point }),
    ]);
  }

  // ---- 3. attack questions -------------------------------------------------

  function attackQuestions(artifact) {
    const questions = artifact.attack_questions || [];
    if (!questions.length) return null;
    return section("03 · 예상 질문", "실제로 받게 될 질문", questions.map((q) =>
      element("article", { className: `attack severity-${q.severity || "medium"}` }, [
        element("div", { className: "attack-head" }, [
          badge(SEVERITY[q.severity] || q.severity || "중간", `severity-tag`),
          badge(ATTACK_TYPES[q.attack_type] || q.attack_type || "", "type-tag"),
          (q.assumption_ids || []).length
            ? element("span", { className: "attack-links",
                text: `가정 ${q.assumption_ids.join(", ")}` })
            : null,
        ]),
        element("p", { className: "attack-question", text: q.question || "" }),
        q.why_likely ? element("small", { text: q.why_likely }) : null,
      ])));
  }

  // ---- 4. assumptions ------------------------------------------------------

  function assumptions(artifact) {
    const items = artifact.assumptions || [];
    if (!items.length) return null;
    return section("04 · 숨은 가정", "주장이 기대고 있는 조건", items.map((a) =>
      element("article", { className: `assumption origin-${a.origin || "unknown"}`,
        id: `assumption-${a.id}` }, [
        element("div", { className: "assumption-head" }, [
          element("strong", { className: "assumption-id", text: a.id || "" }),
          badge(ORIGINS[a.origin] || a.origin || "", `origin-tag`),
          // Unlike attack_type, the backend does not constrain an assumption's
          // category to the fixed vocabulary, so an unmapped slug is normal and
          // shows through rather than being dropped.
          a.category ? badge(ATTACK_TYPES[a.category] || a.category, "type-tag") : null,
          a.support_type === "necessary" ? badge("필수", "necessary-tag") : null,
        ]),
        element("p", { text: a.text || "" }),
        a.failure_effect
          ? element("small", { className: "assumption-fail",
              text: `무너지면: ${a.failure_effect}` })
          : null,
        quotes(a.source_span_ids, "근거 원문"),
      ])));
  }

  // ---- 5. external evidence ------------------------------------------------
  // `unresolved` is rendered as its own group and never folded into the
  // positive ones: zero hits and failed searches are not evidence of anything.

  function evidenceCard(item, relation) {
    const chunks = item.chunks || [];
    return element("article", { className: `evidence relation-${relation}` }, [
      element("div", { className: "evidence-head" }, [
        element("a", { className: "evidence-title", href: item.url || "#",
          target: "_blank", rel: "noreferrer noopener",
          text: item.title || item.url || "제목 없음" }),
        typeof item.confidence === "number" && relation !== "unresolved"
          ? badge(`확신도 ${item.confidence.toFixed(2)}`, "confidence-tag")
          : null,
      ]),
      item.summary ? element("p", { className: "evidence-summary", text: item.summary }) : null,
      item.rationale ? element("p", { className: "evidence-rationale", text: item.rationale }) : null,
      chunks.length
        ? element("details", { className: "chunk-set" }, [
            element("summary", { text: `근거 chunk ${chunks.length}개 보기` }),
            element("div", { className: "chunk-list" }, chunks.map((chunk) =>
              element("article", { className: "chunk" }, [
                element("small", { className: "chunk-meta",
                  text: `chunk ${chunk.num ?? "?"} · ${chunk.id || ""}` }),
                element("p", { text: chunk.content || "" }),
              ]))),
          ])
        : element("p", { className: "chunk-empty",
            text: "본문 chunk가 없어 사실 근거로 쓰지 않았습니다." }),
    ]);
  }

  function evidence(artifact) {
    const groups = artifact.external_evidence || {};
    const total = Object.values(groups).reduce((sum, list) => sum + (list || []).length, 0);
    if (!total) {
      return section("05 · 외부 문헌", "학술 문헌 대조", [
        element("p", { className: "rep-empty",
          text: "이 실행에서는 대조할 외부 문헌을 확보하지 못했습니다. 검색 결과 없음은 주장이 옳다는 근거가 아닙니다." }),
      ]);
    }
    return section("05 · 외부 문헌", "학술 문헌이 지지·제한·도전하는 부분",
      Object.keys(RELATIONS).map((relation) => {
        const items = groups[relation] || [];
        if (!items.length) return null;
        const meta = RELATIONS[relation];
        return element("div", { className: `evidence-group group-${relation}` }, [
          element("div", { className: "evidence-group-head" }, [
            element("h4", { text: `${meta.label} · ${items.length}건` }),
            element("small", { text: meta.note }),
          ]),
          ...items.map((item) => evidenceCard(item, relation)),
        ]);
      }));
  }

  // ---- 6. defensible scope -------------------------------------------------

  function defensibleScope(artifact) {
    const scope = artifact.defensible_scope;
    if (!scope || !scope.statement) {
      // A partial report reaches here. Say why the sentence is missing rather
      // than leaving a hole the reader reads as "nothing to defend".
      const reasons = (artifact.limitations || [])
        .filter((line) => line.includes("critic 제한") || line.includes("실행 시간 제한"));
      return section("06 · 방어 범위", "지금 말할 수 있는 최소 문장", [
        element("div", { className: "scope-withheld" }, [
          element("p", { text: "검증을 통과하지 못해 방어 문장을 표시하지 않습니다." }),
          ...reasons.map((line) => element("small", { text: line })),
        ]),
      ]);
    }
    return section("06 · 방어 범위", "지금 말할 수 있는 최소 문장", [
      element("div", { className: "scope" }, [
        element("div", { className: "scope-head" }, [
          badge(SCOPE_BASIS[scope.basis_kind] || scope.basis_kind || "", "basis-tag"),
          badge(`확신도 ${scope.confidence || "low"}`, "confidence-tag"),
        ]),
        // An extrapolation is not a paper finding. It has to look different.
        scope.basis_kind === "analyst_inference"
          ? element("p", { className: "scope-warning",
              text: "이 문장은 원문에 직접 쓰여 있지 않은 분석자 추론입니다. 아래 조건과 함께 읽어야 합니다." })
          : null,
        element("p", { className: "scope-statement", text: scope.statement }),
        (scope.conditions || []).length
          ? element("div", { className: "scope-block" }, [
              element("h4", { text: "이 조건을 전제로" }),
              element("ul", {}, scope.conditions.map((line) => element("li", { text: line }))),
            ])
          : null,
        (scope.excluded_scope || []).length
          ? element("div", { className: "scope-block scope-excluded" }, [
              element("h4", { text: "여기까지는 말하지 않는다" }),
              element("ul", {}, scope.excluded_scope.map((line) => element("li", { text: line }))),
            ])
          : null,
        quotes(scope.source_refs, "근거 원문"),
        (scope.evidence_ids || []).length
          ? element("small", { className: "scope-evidence",
              text: `외부 근거: ${scope.evidence_ids.join(", ")}` })
          : null,
      ]),
    ]);
  }

  // ---- 7. assumption impacts ----------------------------------------------
  // The backend computes one row per assumption and nothing for combinations,
  // so the control is a radio group, not checkboxes: a UI that lets two
  // switches go off at once would have no data to show for that state.
  // Selecting a row only reads the payload -- no fetch, no EventSource.

  function assumptionImpacts(artifact) {
    const impacts = artifact.assumption_impacts || [];
    if (!impacts.length) return null;
    const byId = new Map((artifact.assumptions || []).map((a) => [a.id, a]));
    const output = element("div", { id: "impact-output", className: "impact-output",
      "aria-live": "polite" });

    function show(impactId) {
      const impact = impacts.find((item) => item.assumption_id === impactId);
      const status = impact ? (impact.status_if_off || "narrows") : "holds";
      const meta = IMPACT[status] || IMPACT.narrows;
      output.className = `impact-output impact-${status}`;
      output.replaceChildren(
        element("div", { className: "impact-status" }, [
          element("span", { className: "impact-label", text: "남는 주장 범위" }),
          element("strong", { className: "impact-badge", text: meta.label }),
        ]),
        element("p", { className: "impact-note", text: meta.note }),
        ...(impact
          ? [
              impact.surviving_scope
                ? element("p", { className: "impact-scope", text: impact.surviving_scope })
                : null,
              impact.because
                ? element("small", { className: "impact-because", text: `이유: ${impact.because}` })
                : null,
              quotes(impact.source_refs, "근거 원문"),
              (impact.evidence_ids || []).length
                ? element("small", { text: `외부 근거: ${impact.evidence_ids.join(", ")}` })
                : null,
            ]
          : []),
      );
    }

    function choice(id, labelText, detail) {
      const input = element("input", { type: "radio", name: "assumption-off",
        id: `impact-${id}`, value: id });
      if (id === "__none__") input.checked = true;
      input.addEventListener("change", () => show(id === "__none__" ? null : id));
      return element("article", { className: "impact-choice" }, [
        input,
        element("label", { for: input.id }, [
          element("span", { className: "impact-choice-title", text: labelText }),
          detail ? element("small", { text: detail }) : null,
        ]),
      ]);
    }

    const choices = element("div", { className: "impact-choices", role: "radiogroup",
      "aria-label": "끌 가정 선택" }, [
      choice("__none__", "전부 켜짐", "논문이 쓴 그대로"),
      ...impacts.map((impact) => {
        const assumption = byId.get(impact.assumption_id);
        return choice(impact.assumption_id,
          `${impact.assumption_id} 끄기`,
          (assumption && assumption.text) || "");
      }),
    ]);

    const rendered = section("07 · 가정 영향", "가정을 하나씩 꺼보기", [
      element("p", { className: "rep-note",
        text: "한 번에 하나만 끕니다. 여러 가정을 동시에 끈 조합은 계산하지 않았습니다. 이 표는 브라우저에서만 평가하며 모델이나 검색을 다시 호출하지 않습니다." }),
      choices,
      output,
    ]);
    show(null);
    return rendered;
  }

  // ---- 8. limitations ------------------------------------------------------

  function limitations(artifact) {
    const items = artifact.limitations || [];
    if (!items.length) return null;
    return section("08 · 한계", "이 보고서가 말하지 않는 것", [
      element("ul", { className: "limitations" },
        items.map((line) => element("li", { text: line }))),
    ]);
  }

  // ---- refusal -------------------------------------------------------------

  function refusal(artifact) {
    return [
      element("div", { className: "refusal" }, [
        element("h2", { text: artifact.title || "검증 가능한 방어 보고서를 만들 수 없음" }),
        element("p", { text: artifact.message || "" }),
        element("small", {
          text: `reason: ${artifact.reason_code || "REFUSED"} · stage: ${artifact.failed_stage || "-"}`,
        }),
      ]),
    ];
  }

  // ---- entry ---------------------------------------------------------------

  function renderArtifact(target, payload) {
    const artifact = payload.artifact || {};
    spanTable = payload.spans || {};
    const partial = artifact.primitive === "partial_defense_report";

    if (artifact.primitive === "refusal") {
      target.replaceChildren(...refusal(artifact));
      return;
    }
    target.replaceChildren(
      element("header", { className: "rep-head" }, [
        element("p", { className: "rep-eyebrow",
          text: (payload.run || {}).source_title || "제목 없는 원문" }),
        element("h2", { className: "rep-title", text: "제출 전 디펜스 보고서" }),
        partial
          ? element("p", { className: "rep-partial",
              text: "검증을 통과한 부분만 표시합니다. 방어 문장과 가정 영향은 보류되었습니다." })
          : null,
      ]),
      targetClaim(artifact),
      weakPoint(artifact),
      attackQuestions(artifact),
      assumptions(artifact),
      evidence(artifact),
      defensibleScope(artifact),
      assumptionImpacts(artifact),
      limitations(artifact),
    );
  }

  return { element, renderArtifact };
})();
