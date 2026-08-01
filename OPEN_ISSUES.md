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

## 🔴 3. 픽스처 PDF가 med 논문인데 도메인은 ml

`fixtures/sample.pdf`는 sepsis 조기탐지 논문(AUC, hazard ratio, 운영 임계값)
이다. 도메인을 ml로 좁혔지만([a52a79f](CLAUDE.md)) 픽스처는 그대로다.
`--domain ml`이 바꾸는 건 primitive 팩뿐이라 데모는 돌지만, 지금 화면에
나오는 건 med 논문에 ml primitive를 씌운 것이다.

ml 논문 픽스처가 필요하다. 고르기 전에 `scripts/audit_pool.py`로 claim 후보
중 수치가 묶인 비율을 먼저 재라 — CLAUDE.md 도메인 절의 절차다. 이 비율이
낮으면 `qualitative`로 강등되고, ml만 남긴 이상 매번 강등되면 데모가 성립
하지 않는다.

---

## 🔴 4. MockLLM 픽스처가 claim을 안 가린다

`MockLLM`은 role로만 키를 잡으므로([clients.py](playground/clients.py)
`DEFAULT_FIXTURES`), `--claim c2`로 돌려도 c1용 가정 4개가 그대로 나온다.
바인딩은 문서 전체 span 색인에 대고 하니 검사는 통과한다.

실제 LLM에서는 claim별로 달라지므로 기능 문제는 아니다. 다만 **오프라인
데모에서 claim을 바꿔가며 보여줄 계획이면 티가 난다** — 다른 주장을 골랐는데
같은 가정이 뜬다.

닫는 법: claim id별 픽스처를 넣거나, 데모에서 claim 전환을 안 보여주거나,
그 시점에는 실제 LLM을 붙인다.

---

## 🟡 5. external 스테이지가 아예 안 돈다 — 보류 결정

`VerifyExternal.reads = ("claims",)`
([core.py:538](playground/stages/core.py:538))라 `selected_claim_id`가
더러워져도 재계산 집합에 안 들어온다. 정지 지점이 score 뒤라 첫 패스에서도
안 돌고, 재개 집합은 `[assumptions, design, critic, render]`다. 결과적으로
`state.external`이 계속 비어 있고 artifact의 `sources.external`은 항상 0.

**LinerSearch를 구현해도 호출되는 지점이 없다.** CLAUDE.md 스텁 #3과 불변
규칙 5(선택된 claim은 예외 없이 Liner 검증)가 이 한 줄에 막혀 있다.

고칠 곳 (한 줄, `reads` 변경이라 승인 사항):

```python
class VerifyExternal(Stage):
    reads = ("claims", "selected_claim_id")
```

같이 볼 것: 지금 `run`은 모든 claim을 순회하는데, 규칙 5는 "선택된 claim은
무조건, 나머지 후보만 `_trigger`"다.

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

## 🟡 8. `INTERRUPTS`에 dirty 필드가 같은 키가 둘 — 유지 결정

`select_claim`과 `change_figure` 둘 다 `("selected_claim_id",)`
([pipeline.py:34](playground/pipeline.py:34)). `change_figure`는 호출하는
곳이 없다. 유지하기로 결정했으므로 여기 기록만 남긴다.

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
