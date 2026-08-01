# 열린 항목

작업 중 짚어둔 것들. CLAUDE.md는 계약이고 이 파일은 **계약과 코드가 아직
어긋나 있는 지점**의 목록이다. 항목이 닫히면 지운다.

상태 표기: 🔴 열림 / 🟡 의도적 보류 (결정됨) / 🟢 해결됨 (다음 정리 때 삭제)

---

## 🟢 1. Critic 참조 무결성 검사 — 해결됨

`critic_rules.precheck`가 control, assumption, claim evidence, derived formula,
status rule attribution의 참조를 실제 `doc.spans`, 선택 claim의 assumptions와
external evidence에 대조한다. 존재하지 않는 `span_id`, `assumption_id`,
`evidence_id`는 fatal이며 `UNSAFE_TO_VISUALIZE` verdict로 이어진다.

fatal일 때는 재설계하지 않고 `evidence_assumption_map`을 내므로 검증되지 않은
참조가 switchboard의 토글이나 status 규칙으로 노출되지 않는다.

---

## 🟢 2. `weakens_how` 일반론 필터 — 해결됨

`prompts/assumption_miner.md`는 "주장이 틀린다", "결과를 신뢰할 수 없다"
같은 판정형·공허한 문장을 실격으로 규정하지만, 코드
([core.py:399](playground/stages/core.py:399) `AssumptionMiner`)는
`MIN_WEAKENS_CHARS = 20` 길이 컷만 건다. 20자 넘는 일반론은 다 통과한다.

결정론적 precheck를 먼저 실행하고, 통과한 경우에만 Critic의 구조화된 soft
check가 판정형·공허한 `weakens_how`를 검사한다. 부적합 또는 호출 실패는
fatal violation으로 기록하고 safe map으로 강등한다.

---

## 🟢 3. fixture와 최종 집중 도메인 — 해결됨

최종 집중 분야는 `ml`로 확정했고 `fixtures/guo17a.pdf`를 기본 fixture로 추가했다.
정량 claim 후보 비율은 88.6%다. `sample.pdf`와 `med` pack은 대조군으로 유지한다.

fixture를 고르기 전에 `scripts/audit_pool.py`로 claim 후보
중 수치가 묶인 비율을 먼저 재라 — CLAUDE.md 도메인 절의 절차다. 이 비율이
낮으면 `qualitative`로 강등되므로 최종 데모의 정량 경로가 죽는다.

---

## 🟢 4. MockLLM fixture가 claim을 안 가린다 — 해결됨

`MockLLM`은 role로만 키를 잡으므로([clients.py](playground/clients.py)
`DEFAULT_FIXTURES`), 자동 selector가 c2를 고르게 되면 c1용 가정 4개가 그대로
나온다. 바인딩은 문서 전체 span 색인에 대고 하니 검사는 통과한다.

offline에서는 flat 후보를 c1 root와 c2/c3 child graph로 감싸고, Guo marker 및
claim ID별 assumption/switchboard fixture로 node별 결과가 섞이지 않게 했다.

---

## 🟢 5. external evidence가 나열 전용이던 문제 — 해결됨

`EvidenceController`는 Context Analyst가 만든 obligation을 받아 OpenAI 계획 →
Liner Search Agent reference/chunk 수집 → OpenAI 관계·충분성 해석을 최대 3회
반복한다. 결과는 `EvidenceLedger`로 PanelComposer와 Critic에 전달된다.

---

## 🟢 6. 도달 불가능한 primitive들 — 해결됨

그림 종류 이름(scaling_comparison, generated_schematic, annotated_figure …)은
논문에서 판정 기준이 없어 영원히 도달 불가능하거나 키워드 하드코딩으로만
도달했다. 어휘를 **조작 동사 × 깨는 오해** 5개(rate_compare, threshold_finder,
part_removal, flow_topology, proportion_reveal)로 교체했고, 도달 여부는
`primitives.bind`의 슬롯 충족이 결정한다. 도메인 팩은 같은 이유로 삭제됐다.

남은 격차는 렌더러다: part_removal(status)만 인터랙티브이고 나머지 4개는
정적 카드로 표시된다 (CLAUDE.md "아직 안 된 것" 3번).

---

## 🟢 7. `DesignInteraction.reads`의 `assumptions` — 해결됨

CLAUDE.md 표에는 있었으나 코드에 없었다. 스위치보드 재작성에서 붙였다.

---

## 🟢 8. HIL interrupt 경로 — 해결됨

`SelectFrontier`가 graph node를 자동 선택하고 root→frontier 경로를 만든다.
`until`, pause, `select_claim`, `change_level`, `change_figure` interrupt를 제거해
첫 PDF 입력 뒤 artifact 또는 refused까지 추가 입력 없이 진행한다.

---

## 🟢 12. Claim lineage graph와 frontier — 해결됨

root/parent 누락·중복·cycle node를 결정론적으로 검증하고, 유효 root에 연결된
node만 유지한다. `SelectFrontier`는 지정 가중합과 graph order tie-break로 frontier와
`critical_path_ids`를 만들며, path-level external evidence에는
`covered_claim_ids`를 보존한다.

---

## 🟡 9. `Claim.assumptions`와 `Assumption`이 공존 — 의도적

`Claim.assumptions: list[str]`은 BuildClaims가 채우는 "저자가 명시한 조건"의
원시 목록이고, `AssumptionMiner`는 이걸 프롬프트 입력 힌트로만 받는다.
출력은 `PaperState.assumptions`의 `Assumption` 객체다. 이름이 겹쳐서 헷갈릴
수 있으니 적어둔다.

---

## 🟢 10. degrade 경로의 `AttributeError` — 해결됨

`pipeline.run`의 강등 로그가 `st.mode`를 읽었는데 `Stage`에 그런 속성이
없어서, 모드 강등이 실행되는 순간 죽었다. [406ab06](playground/pipeline.py)
에서 `state.mode`로 고쳤다. 그 경로를 `try/except/else`로 재구성하면서 같이
처리했다.

---

## 참고: 지금 데모에 안 채워진 것 (버그 아님)

- `spec.numbers`가 항상 빈 리스트 → `precheck`의 수치 검사(`UNGROUNDED_NUMBER`,
  `UNTRACEABLE_DERIVATION`, `ILLUSTRATIVE_WITHOUT_WARNING`)가 전부 no-op이다.
  스위치보드는 수치를 직접 렌더하지 않으므로 지금은 맞는 동작이지만,
  **결정론적 검사 3개가 아무것도 안 하고 있다**는 뜻이기도 하다
- claims는 `BuildClaims._fallback`(수치 밀집 span 복사) 경로로 나온다.
  `DEFAULT_FIXTURES`에 `claim_mapper`를 넣지 않은 건 이 폴백을 가리지
  않으려는 의도다

---

## 🟢 11. 실제 Liner client — 해결됨

`LinerSearchAgent`가 `/api/v1/agents/search` SSE의 references와 referenceChunks를
보존하고, key-safe event 및 429/5xx/네트워크 1회 재시도를 지원한다. Deep
Research와 legacy Scholar Search endpoint는 pipeline에서 사용하지 않는다.
`MockSearchAgent`는 offline 기본값이다.

---

## 🟢 12. DemoPayloadV1.1 live bridge — 해결됨

`playground.payload`와 `playground.server`가 payload serialization 및 REST/SSE
transport를 제공한다. frontend는 raw/status/complete/error 순서를 재생하고
최종 payload를 렌더한다.
