# 열린 항목

작업 중 짚어둔 것들. CLAUDE.md는 계약이고 이 파일은 **계약과 코드가 아직
어긋나 있는 지점**의 목록이다. 항목이 닫히면 지운다.

상태 표기: 🔴 열림 / 🟡 의도적 보류 (결정됨) / 🟢 해결됨 (다음 정리 때 삭제)

---

## 🔴 1. Critic이 존재하지 않는 span_id를 통과시킨다

**불변 규칙 2 구멍.** `Control.validate()`([state.py:68](playground/state.py:68))는
`provenance="variable"`일 때 `span_id`가 **있는지**만 보고 그게 원문에
**실재하는지**는 안 본다. `critic_rules.precheck`도 마찬가지다 —
`state.range_of(span_id)`가 없는 span에 대해 `None`을 돌려주므로
`EXTRAPOLATION_UNMARKED` 검사까지 조용히 건너뛴다.

**누수원은 제거됐고 검사 구멍은 그대로다.** 이 구멍으로 실제로 새던
`span_id="tab2_c3"` 폴백 컨트롤은 `DesignInteraction` 재작성으로 사라졌다 —
이제 컨트롤이 가정에서 결정론적으로 나오므로 span은 항상 실재한다.
하지만 **검사 자체는 여전히 없다.** 다른 경로로 들어온 spec은 똑같이 샌다.

`BuildClaims._bind`는 claim의 span을 실재 여부로 거르고 `AssumptionMiner`,
`DesignInteraction._attribution`도 그렇게 하는데, `precheck`의 컨트롤 검사만
그 대열에 없다. 파이프라인 끝단이라 가장 새면 안 되는 자리다.

이제 검사할 대상이 하나 늘었다. `StatusRule.attribution`도 같은 규칙(7번)을
받는다 — design이 이미 강등으로 방어하지만 Critic 쪽 이중 방어는 없다.

고칠 곳: `critic_rules.precheck`에
- 컨트롤: `c.span_id not in state.doc.spans` → fatal
- `spec.status_rules`: `attribution.kind=="paper"`인데 span 부재,
  `"external"`인데 evidence 부재, `assumption_id`가 실재 가정이 아님 → fatal
- 도달 불가능한 status (예: `weak`를 내는 규칙이 하나도 없음) → non-fatal

`Critic.reads`에 `doc`, `assumptions`, `external`이 필요해진다(승인 사항).

> 세션 중 지적한 항목이 아니라 이 문서를 쓰면서 확인하다 발견했다.

---

## 🔴 2. `weakens_how` 일반론 필터가 길이 컷뿐

`prompts/assumption_miner.md`는 "주장이 틀린다", "결과를 신뢰할 수 없다"
같은 판정형·공허한 문장을 실격으로 규정하지만, 코드
([core.py:399](playground/stages/core.py:399) `AssumptionMiner`)는
`MIN_WEAKENS_CHARS = 20` 길이 컷만 건다. 20자 넘는 일반론은 다 통과한다.

자연어 판정을 결정론으로 하기 어렵고 불변 규칙 3(결정론 먼저)에 걸려서
이렇게 뒀다. 제자리는 **Critic의 LLM 소프트 검사** — precheck가 못 잡는
것만 모델에게 묻는다는 규칙 3의 후반부에 정확히 해당한다.

---

## 🟡 3. fixture와 최종 집중 도메인 — 의도적 보류

`fixtures/sample.pdf`는 sepsis 조기탐지 논문이고 기본 pack은 `ml`이라 현재
내용과 기본 도메인이 맞지 않는다. 최종 집중 분야를 `ml` 또는 `med` 중 고른 뒤
그 분야의 fixture 하나를 새로 넣기로 했으며 이번 계약 정비에서는 교체하지 않는다.

fixture를 고르기 전에 `scripts/audit_pool.py`로 claim 후보
중 수치가 묶인 비율을 먼저 재라 — CLAUDE.md 도메인 절의 절차다. 이 비율이
낮으면 `qualitative`로 강등되므로 최종 데모의 정량 경로가 죽는다.

---

## 🟡 4. MockLLM fixture가 claim을 안 가린다 — 보류

`MockLLM`은 role로만 키를 잡으므로([clients.py](playground/clients.py)
`DEFAULT_FIXTURES`), 자동 selector가 c2를 고르게 되면 c1용 가정 4개가 그대로
나온다. 바인딩은 문서 전체 span 색인에 대고 하니 검사는 통과한다.

현재 sample은 score 동점에서 원문 순서상 c1이 자동 선택되므로 offline 기본
경로에는 드러나지 않는다. 실제 LLM에서는 claim별 입력을 받아 기능 문제가 없다.

fixture를 교체할 때 claim id별 mock을 넣거나 실제 LLM smoke로 닫는다.

---

## 🟢 5. external 스테이지가 선택 시 안 돌던 문제 — 해결됨

`VerifyExternal.reads`에 `selected_claim_id`를 추가했고 전체 claim 순회를
제거했다. 선택된 claim 하나만 네 갈래로 검색하며, 빈 결과와 실패도 갈래별
이벤트로 남긴다. 외부 근거는 나열 전용이라 design 재계산과 status 판정에는
연결되지 않는다.

---

## 🔴 6. 도달 불가능한 primitive들

`DesignInteraction`이 항상 `assumption_switchboard`를 내므로
`threshold_explorer`, `survival_curve_explorer`, `forest_plot_explorer`,
`scaling_comparison`, `ablation_toggle`, `annotated_figure`는 전부 도달
불가능하다. 유지하기로 결정했고([domains/__init__.py](playground/domains/__init__.py)
주석에도 적었다), 팩 조회가 도메인 격리를 증명하는 장치라서 남긴다.

되살릴 계획이 없으면 지우는 게 맞다. 지금은 "선언됐지만 안 쓰임" 상태로
두는 것이 결정이다.

---

## 🟢 7. `DesignInteraction.reads`의 `assumptions` — 해결됨

CLAUDE.md 표에는 있었으나 코드에 없었다. 스위치보드 재작성에서 붙였다.

---

## 🟢 8. HIL interrupt 경로 — 해결됨

`SelectClaim` 스테이지가 최고 score claim을 자동 선택한다. `until`, pause,
`select_claim`, `change_level`, `change_figure` interrupt를 제거해 첫 PDF 입력 뒤
artifact 또는 refused까지 추가 입력 없이 진행한다.

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

## 🟡 11. 실제 Liner client — API 대기

현재 `Search` protocol과 네 갈래 `VerifyExternal`만 있고 실제 `LinerSearch`는
없다. 키와 응답 사양을 받은 뒤 API 담당 브랜치에서 구현한다. 그 전까지
`MockSearch`가 offline 기본값이며 live라고 표시하지 않는다.

---

## 🟡 12. DemoPayloadV1 live bridge — 화면 담당 후속 작업

`COLLABORATION.md`에 프론트-백엔드 payload를 고정했고 정적 frontend fixture도
그 shape를 사용한다. 실제 HTTP/SSE 또는 다른 transport는 아직 없다. 화면
담당자는 payload 필드를 바꾸지 않고 transport adapter와 raw monitor를 붙인다.
