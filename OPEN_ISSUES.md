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

지금 실제로 새고 있다. `DesignInteraction`의 폴백 컨트롤
([core.py:614](playground/stages/core.py:614))이 `span_id="tab2_c3"`인데
sample.pdf의 span id 형식은 `p2_t0r1c0`이다. 그런 span은 없고, 그런데도
매 실행 `verdict: PASS`가 나온다.

```
control span_id= tab2_c3 | exists? False
verdict: PASS []
```

`BuildClaims._bind`는 claim의 span을 실재 여부로 거르고 `AssumptionMiner`도
그렇게 하는데, spec의 컨트롤만 그 검사를 안 받는다. 파이프라인 끝단이라
가장 새면 안 되는 자리다.

고칠 곳: `critic_rules.precheck`에 `c.span_id not in state.doc.spans` →
fatal `Violation` 추가. `Critic.reads`에 `doc`이 필요해진다(승인 사항).
폴백 컨트롤도 같이 실재하는 span으로 바꾸거나 없애야 한다.

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

## 🟡 6. `DesignInteraction.reads`에 `assumptions`가 없다 — 다음 작업

CLAUDE.md 스테이지 표에는 design이 `assumptions`를 읽는다고 적혀 있지만
코드는 아직 아니다([core.py:579](playground/stages/core.py:579)).
design이 그걸 쓰지 않으므로 안 쓰는 read를 미리 달지 않았다.

재계산 범위에는 영향 없다 — design은 이미 `selected_claim_id`로 재실행된다.
`claim_status_logic` 생성 작업에서 같이 붙인다.

---

## 🟡 7. `INTERRUPTS`에 dirty 필드가 같은 키가 둘 — 유지 결정

`select_claim`과 `change_figure` 둘 다 `("selected_claim_id",)`
([pipeline.py:34](playground/pipeline.py:34)). `change_figure`는 호출하는
곳이 없다. 유지하기로 결정했으므로 여기 기록만 남긴다.

---

## 🟡 8. `Claim.assumptions`와 `Assumption`이 공존 — 의도적

`Claim.assumptions: list[str]`은 BuildClaims가 채우는 "저자가 명시한 조건"의
원시 목록이고, `AssumptionMiner`는 이걸 프롬프트 입력 힌트로만 받는다.
출력은 `PaperState.assumptions`의 `Assumption` 객체다. 이름이 겹쳐서 헷갈릴
수 있으니 적어둔다.

---

## 🟢 9. degrade 경로의 `AttributeError` — 해결됨

`pipeline.run`의 강등 로그가 `st.mode`를 읽었는데 `Stage`에 그런 속성이
없어서, 모드 강등이 실행되는 순간 죽었다. [406ab06](playground/pipeline.py)
에서 `state.mode`로 고쳤다. 그 경로를 `try/except/else`로 재구성하면서 같이
처리했다.

---

## 참고: 지금 데모에 안 채워진 것 (버그 아님)

`MockLLM`에 `explainer_designer` / `claim_mapper` 픽스처가 없어서 생기는
자연스러운 공백이다. 실제 LLM을 붙이면 채워진다.

- `spec.title`이 `Untitled`, `explanation`이 빈 dict
- `spec.numbers`가 빈 리스트 → precheck의 수치 검사가 전부 no-op
- claims는 `BuildClaims._fallback`(수치 밀집 span 복사) 경로로 나온다.
  `DEFAULT_FIXTURES`에 `claim_mapper`를 넣지 않은 건 이 폴백을 가리지
  않으려는 의도다
