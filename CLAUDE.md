# Paper Playground — working contract

논문 속 주장 하나를 근거·가정·외부증거로 분해하고, 사용자가 가정을 직접
꺼보면서 그 주장이 언제 유지되고 언제 약해지는지 확인하게 하는 에이전트.

논문 전체를 설명하지 않는다. 요약 도구가 아니다.
단위는 논문이 아니라 claim 하나다.

핵심 루프:

1. 논문에서 검증 가능한 주장 후보를 뽑는다
2. 사용자가 그중 하나를 고른다 ← 여기서 파이프라인이 멈춘다
3. 그 주장을 근거(span) / 가정(assumption) / 외부증거(evidence)로 분해한다
4. 사용자가 가정을 끈다
5. 주장의 status가 strong ↔ conditional ↔ weak 로 움직인다
6. 왜 움직였는지는 항상 원문 span 또는 외부 evidence를 가리킨다

## 실행

```bash
python -m playground.run              # domain=ml
python -m playground.run --claim c2   # 특정 claim을 골라서
```

목 클라이언트로 전체 DAG가 오프라인에서 돈다. 키가 생기면 `clients.py`의
`MockLLM` / `MockSearch`만 교체하면 되고 다른 파일은 건드리지 않는다.

## 불변 규칙

이 일곱 개는 리팩터링 중에도 깨지면 안 된다.

1. **모든 상태는 `PaperState` 하나에.** 스테이지가 자체 필드를 들고 있으면
   증분 재계산이 깨진다.
2. **모든 수치와 컨트롤은 `span_id`로 원문에 묶인다.** 묶이지 않은 수치는
   `provenance="illustrative"`여야 하고, 그 경우 `fidelity_warning`이 필수다.
3. **크리틱은 결정론적 검사가 먼저.** `critic_rules.precheck`가 잡을 수 있는
   것을 LLM에게 묻지 않는다.
4. **설계 스테이지는 HTML을 만들지 않는다.** `InteractionSpec` 스키마만 낸다.
   자유 코드 생성은 라이브 데모 최대 리스크.
5. **호출하지 않기로 한 판단도 이벤트로 남긴다.** `bus.decision(...)`.
   검색 남발보다 "이 주장은 검색 불필요"가 심사에서 강하다.
   단 **선택된 claim은 예외 없이 Liner로 검증한다** — 사용자가 파헤치기로 한
   주장에 외부 근거가 비어 있으면 안 된다. `_trigger`는 나머지 후보에만 적용된다.
6. **assumption 토글은 LLM을 호출하지 않는다.** `claim_status_logic` 규칙을
   설계 시점에 한 번 생성하고, 토글은 프론트에서 규칙 평가만 한다.
   토글 한 번에 6초 기다리는 데모는 데모가 아니다.
7. **모든 status 규칙은 attribution을 갖는다.** `kind`가 `paper`면 실재하는
   `span_id`, `external`이면 실재하는 `evidence_id`를 가리켜야 한다.
   `pedagogical`이면 UI에 그렇게 표시된다. 존재하지 않는 id를 가리키는 규칙은
   크리틱에서 fatal이다.

## 스테이지 계약

| 스테이지 | LLM | reads | writes | 예산 |
|---|---|---|---|---|
| parse | ✗ | — | doc, number_pool | 8s |
| claims | ✓ | doc | claims | 6s |
| score | 소형 | claims, number_pool | scores | 2s |
| ⏸ **claim 선택** | — | claims, scores | selected_claim_id | 사용자 |
| assumptions | ✓ | doc, claims, number_pool, selected_claim_id | assumptions | 5s |
| external | 검색 ✓ | claims, assumptions, selected_claim_id | external | 5s |
| design | ✓ | claims, assumptions, scores, external, profile, mode, selected_claim_id | spec | 6s |
| critic | ✗→✓ | spec, number_pool, doc, external | verdict | 4s |
| render | ✗ | spec, verdict, mode | artifact | 1s |

`reads`/`writes`를 바꾸면 증분 재계산 범위가 자동으로 바뀐다. `INTERRUPTS`
테이블은 어떤 필드가 더러워지는지만 선언한다.

**claim 선택 정지 지점.** score까지 돌고 파이프라인은 멈춘다. 후보 claim과
점수를 내보내고 사용자 선택을 기다린다. 자동으로 1등을 고르지 않는다 —
무엇을 파헤칠지는 사용자 결정이고, 이 정지가 제품의 첫 인터랙션이다.
선택은 `selected_claim_id`를 더럽히는 인터럽트로 들어오고, 재개는
`_affected({"selected_claim_id"})`가 알아서 계산한다.

assumptions 스테이지는 선택된 claim **하나만** 분해한다. 전체 claim을 미리
분해하지 않는다 — 비용이 claim 수에 비례하면 안 된다.

`claim_status_logic`은 design이 `spec` 안에 함께 낸다. 별도 스테이지가 아니다.
설계와 규칙이 갈라지면 컨트롤은 있는데 아무 status도 안 움직이는 화면이 나온다.

## claim status

`strong / conditional / weak` 세 단계까지만.

- **strong** — 논문 안 근거로 직접 지지됨
- **conditional** — 특정 가정이 켜져 있을 때만 유지됨
- **weak** — 핵심 가정이 꺼졌거나 외부 증거와 충돌함

**`broken`은 쓰지 않는다.** 우리는 논문 저자가 틀렸다고 단정하지 않는다.
가정이 무너졌을 때 우리가 말할 수 있는 건 "이 주장이 이 지점에서 약해진다"
까지다. 판정이 아니라 조건의 노출이 목적이다.

status 문구는 항상 근거를 동반한다. "weak" 단독으로 표시하지 않고
"이 가정 없이는 약해짐 — p.4 Table 2" 형태로 attribution과 함께 표시한다.

## 모드

`quantitative → qualitative → refused`. 강등은 실패가 아니라 기능이다.
데모에서 반드시 한 번 보여준다. (모드는 인터랙션의 정밀도이고,
claim status는 주장의 강도다. 서로 다른 축이다.)

- 수치 복원 불가 → `qualitative` (정성적 인터랙션, 숫자 생성 금지.
  가정 토글과 status 전이는 여기서도 그대로 동작한다)
- 근거 있는 주장이 하나도 없음 → `refused` (거절 화면도 제품의 일부)

## 작업 규칙

- **한 번에 스텁 하나만 구현한다.** 여러 스테이지를 동시에 건드리지 않는다.
- **`reads`/`writes` 튜플, `PaperState` 필드명, `EventBus` 시그니처는
  승인 없이 바꾸지 않는다.** 이 셋이 증분 재계산과 세컨드 화면의 계약이다.
  바꿔야 할 이유가 보이면 먼저 말하고 승인을 받는다.
- **테스트 코드는 작성하지 않는다.** `python -m playground.run`으로 회귀를
  확인한다. 기존 `tests/`는 남겨두되 확장하지 않는다.
- **새 의존성은 먼저 물어본다.** 임의로 install하지 않는다.

## 지금 채워야 할 스텁

우선순위 순.

1. `stages/core.py::DecomposeAssumptions` — 선택된 claim을 근거/가정으로 분해.
   각 가정은 원문 span에 묶이거나 명시적으로 `pedagogical`이어야 한다.
2. `stages/core.py::DesignInteraction` — `InteractionSpec`에 더해
   `claim_status_logic` 규칙 생성. 규칙마다 attribution 필수(규칙 7).
3. `clients.py::LinerSearch` — 실제 API. 선택된 claim은 무조건 한 번 나가고,
   나머지 후보만 `_trigger`를 탄다(규칙 5). 이걸 붙이려면 `VerifyExternal`이
   재계산 집합에 들어와야 하므로 `reads`에 `selected_claim_id` 추가가 같이
   필요하다 — 승인 사항이니 그때 다시 확인한다.
4. `stages/core.py::Critic` — attribution 검증(존재하지 않는 span_id/evidence_id는
   fatal), 도달 불가능한 status 검사. 그 다음에 LLM 소프트 검사.
5. 프론트 — 가정 토글 패널 + 규칙 평가기(LLM 호출 없음) + status 배지.
6. 프론트 — primitive 렌더러 3종. 코어는 손대지 않는다.

## 도메인

**ml 하나로 간다.** med는 더 작업하지 않는다.

`domains/__init__.py`의 `PACKS`에 med 엔트리와 `--domain med`는 남겨둔다.
지우면 도메인 격리가 실제로 되는지 검증할 대조군이 사라진다 — 남겨두되
투자하지 않는다. 도메인 추가가 `pipeline.py`나 `stages/`를 건드리게 만들면
설계가 틀린 것이다.

**그래서 이게 정면으로 다뤄야 할 리스크가 됐다.** ML 논문은 figure가 예쁘지만
핵심 수치가 그림 안에만 있는 경우가 많고, 그러면 number_pool이 비어서
`qualitative`로 강등된다. med처럼 표·caption에서 수치를 주워 담는 안전판이
없다. 논문을 고정하기 전에 `scripts/audit_pool.py`로 claim 후보 중 수치가
묶인 비율을 먼저 재고, 그 숫자를 보고 논문을 고른다.

강등 자체는 여전히 기능이다. 다만 ml만 남긴 이상 강등이 **매번** 일어나면
데모가 성립하지 않는다. 정량 경로가 최소 하나는 살아 있는 논문이어야 한다.

## 하지 않을 것

- 논문 저자가 틀렸다는 판정 (`broken` status 없음)
- 토글마다 LLM 호출
- 선택되지 않은 claim까지 미리 분해
- 모든 figure 자동 vectorization
- 범용 코드 실행
- 논문 전체를 웹사이트로 변환
- multi-user, 음성, 논문 간 메타분석
