# Paper Playground — working contract

논문 속 주장 하나를 근거·가정·외부증거로 분해하고, 사용자가 가정을 직접
꺼보면서 그 주장이 언제 유지되고 언제 약해지는지 확인하게 하는 에이전트.

논문 전체를 설명하지 않는다. 요약 도구가 아니다.
단위는 논문이 아니라 claim 하나다.

핵심 루프:

1. 논문에서 검증 가능한 주장 후보를 뽑는다
2. interaction score가 가장 높은 claim 하나를 결정론적으로 자동 선택한다
3. 그 주장을 근거(span) / 가정(assumption) / 외부증거(evidence)로 분해한다
4. 파이프라인이 artifact까지 추가 입력 없이 완료된다
5. 사용자가 완성된 화면에서 가정을 끈다(브라우저 로컬 규칙 평가)
6. 주장의 status가 strong ↔ conditional ↔ weak 로 움직인다
7. 왜 움직였는지는 항상 원문 span 또는 외부 evidence를 가리킨다

## 실행

```bash
python -m playground.run              # domain=ml
python -m playground.run --domain med # pack만 med로 전환
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
   선택된 claim 하나는 `support / contradict / boundary / methodology` 네 갈래로
   검색하고, 갈래별 결과가 0건이어도 명시적인 이벤트를 남긴다. 검색 갈래는
   근거를 찾는 렌즈일 뿐 stance나 controversy 판정이 아니다.
   **실행 도중 사람의 선택이나 승인을 기다리지 않는다.** 첫 PDF 입력 뒤 claim은
   score로 자동 선택되고 파이프라인은 artifact 또는 refused까지 진행한다.
   Critic fatal은 재설계나 사람 확인 대신 안전한 읽기 전용 artifact를 만든다.
6. **assumption 토글은 LLM을 호출하지 않는다.** `claim_status_logic` 규칙을
   설계 시점에 한 번 생성하고, 토글은 프론트에서 규칙 평가만 한다.
   토글 한 번에 6초 기다리는 데모는 데모가 아니다.
7. **모든 status 규칙은 attribution을 갖는다.** `kind`가 `paper`면 실재하는
   `span_id`, `external`이면 실재하는 `evidence_id`를 가리켜야 한다.
   `pedagogical`이면 UI에 그렇게 표시된다. 존재하지 않는 id를 가리키는 규칙은
   크리틱에서 fatal이며 verdict는 `UNSAFE_TO_VISUALIZE`다. 이때 switchboard를
   내지 않고 evidence map과 assumption map만 낸다.

## 스테이지 계약

| 스테이지 | LLM | reads | writes | 예산 |
|---|---|---|---|---|
| parse | ✗ | — | doc, number_pool | 8s |
| claims | ✓ | doc | claims | 6s |
| score | 소형 | claims, number_pool | scores | 2s |
| select | ✗ | claims, scores | selected_claim_id | 0.1s |
| assumptions | ✓ | doc, claims, number_pool, selected_claim_id | assumptions | 5s |
| external | 쿼리 ✓ / 검색 ✓ | claims, selected_claim_id | external | 25s |
| design | ✓ | claims, assumptions, scores, profile, mode, selected_claim_id | spec | 6s |
| critic | ✗→✓ | spec, number_pool, doc, claims, assumptions, external | verdict | 4s |
| render | ✗ | spec, verdict, mode, doc, claims, assumptions, external | artifact | 1s |

`reads`/`writes`는 각 스테이지의 상태 의존성을 명시한다. 실행 중 사용자
interrupt API와 Critic 재설계 루프는 두지 않는다.

**claim 자동 선택.** `SelectClaim`은 `InteractionScore.total` 최고점을 고르고,
동점이면 claim 원문 순서를 따른다. 선택은 한 번만 일어나며 추가 입력이나 별도
LLM 호출이 없다.

assumptions 스테이지는 선택된 claim **하나만** 분해한다. 전체 claim을 미리
분해하지 않는다 — 비용이 claim 수에 비례하면 안 된다.

`claim_status_logic`은 design이 `spec` 안에 함께 낸다. 별도 스테이지가 아니다.
설계와 규칙이 갈라지면 컨트롤은 있는데 아무 status도 안 움직이는 화면이 나온다.

외부 검색 결과는 독립된 근거 목록이다. 같은 URL은 하나로 합치되 어떤 검색
갈래에서 발견됐는지 보존한다. design은 이 목록을 읽지 않으며 외부 근거로
status나 controversy를 자동 판정하지 않는다.

Critic의 결정론적 검사에서 fatal violation이 하나라도 나오면 verdict는
`UNSAFE_TO_VISUALIZE`다. 파이프라인은 design을 재실행하지 않고 render까지
계속 진행해, 선택 claim의 원문·외부 근거와 가정만 담은 읽기 전용 artifact를
만든다. 원문 map은 claim 근거 span과 가정 귀속 span의 순서 보존 합집합이다.
이 판정은 `quantitative / qualitative` mode를 바꾸지 않는다.

## claim status

`strong / conditional / weak` 세 단계까지만.

- **strong** — 논문 안 근거로 직접 지지됨
- **conditional** — 특정 가정이 켜져 있을 때만 유지됨
- **weak** — 핵심 가정이 꺼져 주장의 주된 지지가 약해짐

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
- **기본 검증은 smoke다.** 정상/실패 CLI와 기존 pytest를 실행한다. 새 테스트
  파일은 핵심 불변식 회귀가 발견됐을 때만 합의 후 최소로 추가한다.
- **새 의존성은 먼저 물어본다.** 임의로 install하지 않는다.
- **테스트 케이스 최소화.** 시간이 없다.

## 지금 채워야 할 스텁

우선순위 순.

1. `stages/core.py::DecomposeAssumptions` — 선택된 claim을 근거/가정으로 분해.
   각 가정은 원문 span에 묶이거나 명시적으로 `pedagogical`이어야 한다.
2. `stages/core.py::DesignInteraction` — `InteractionSpec`에 더해
   `claim_status_logic` 규칙 생성. 규칙마다 attribution 필수(규칙 7).
3. `clients.py::LinerSearch` — 실제 API. `VerifyExternal`은 선택된 claim의
   네 갈래 쿼리와 0건/실패 이벤트까지 구현되어 있으므로, 이 프로토콜을 실제
   검색 호출에 연결한다.
4. `stages/core.py::Critic` — 결정론적 참조 무결성 검사는 구현됨. 다음 작업은
   `weakens_how` 같은 자연어 품질에 대한 LLM 소프트 검사다.
5. 프론트 — 가정 토글 패널 + 규칙 평가기(LLM 호출 없음) + status 배지.
6. 프론트 — primitive 렌더러 3종. 코어는 손대지 않는다.

## 도메인

기본값은 `ml`이지만 최종 집중 분야는 fixture를 고를 때 확정한다. `med` pack도
대조군과 선택 가능성 때문에 유지한다. 한 번에 한 분야만 깊게 판다.

`domains/__init__.py`의 `PACKS`에 med 엔트리와 `--domain med`는 남겨둔다.
지우면 도메인 격리가 실제로 되는지 검증할 대조군이 사라진다 — 남겨두되
투자하지 않는다. 도메인 추가가 `pipeline.py`나 `stages/`를 건드리게 만들면
설계가 틀린 것이다.

**그래서 이게 정면으로 다뤄야 할 리스크가 됐다.** ML 논문은 figure가 예쁘지만
핵심 수치가 그림 안에만 있는 경우가 많고, 그러면 number_pool이 비어서
`qualitative`로 강등된다. med처럼 표·caption에서 수치를 주워 담는 안전판이
없다. 논문을 고정하기 전에 `scripts/audit_pool.py`로 claim 후보 중 수치가
묶인 비율을 먼저 재고, 그 숫자를 보고 논문을 고른다.

강등 자체는 여전히 기능이다. 최종 선택 분야에서는 정량 경로가 최소 하나는
살아 있는 논문을 fixture로 골라야 한다.

## 하지 않을 것

- 논문 저자가 틀렸다는 판정 (`broken` status 없음)
- 토글마다 LLM 호출
- 선택되지 않은 claim까지 미리 분해
- 모든 figure 자동 vectorization
- 범용 코드 실행
- 논문 전체를 웹사이트로 변환
- multi-user, 음성, 논문 간 메타분석
