# Paper Playground — working contract

이해하기 어려운 자료를 **만져보면서 이해하는 설명 자료**로 바꾸는 에이전트.
`example/kimi-k3-explainer.html`이 결과물의 기준이다.

논문 전체를 설명하지 않는다. 요약 도구가 아니다.
산출 단위는 병목(bottleneck) 하나이고, interactive 단위는 그 병목의 패널이다.

핵심 루프:

1. 논문에서 하나의 root thesis와 하위 claim graph를 뽑는다
2. 모든 node를 원문 span에 묶고 frontier 후보를 score한다
3. root에서 pedagogic frontier까지 핵심 경로만 상세 검증·설명한다
4. frontier를 근거(span) / 가정(assumption) / 외부증거(evidence)로 분해한다
5. 가장 가르칠 값이 큰 병목 하나를 고르고 패널로 구성한다
6. 파이프라인이 artifact까지 추가 입력 없이 완료된다
7. 사용자가 완성된 화면에서 패널을 조작한다(브라우저 로컬 평가)
8. 무엇이 왜 그렇게 반응하는지는 항상 원문 span 또는 외부 evidence를 가리킨다

**claim graph는 내부 추론 산출물이다.** 계보는 프론트엔드에 노출되지 않으며
세 가지에만 쓰인다: (a) thesis 재정의, (b) 어떤 병목을 몇 개 다룰지 선택,
(c) 비판 지점 문단. 사용자는 claim id도, frontier score도, graph도 보지 않는다.
payload에서는 `analysis` 아래에만 실린다.

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
4. **설계 스테이지는 HTML을 만들지 않는다.** `PanelSpec` 스키마만 낸다.
   자유 코드 생성은 라이브 데모 최대 리스크.
5. **호출하지 않기로 한 판단도 이벤트로 남긴다.** `bus.decision(...)`.
   root→frontier 핵심 경로를 하나의 context로 묶어 `support / contradict /
   boundary / methodology` 네 갈래로 검색하고, 갈래별 결과가 0건이어도 명시적인 이벤트를 남긴다. 검색 갈래는
   근거를 찾는 렌즈일 뿐 stance나 controversy 판정이 아니다.
   **실행 도중 사람의 선택이나 승인을 기다리지 않는다.** 첫 claim 입력은 직접
   주거나 PDF에서 얻을 수 있고, 이후 graph/frontier부터 artifact 또는 refused까지
   자동 진행한다.
   Critic fatal은 재설계나 사람 확인 대신 안전한 읽기 전용 artifact를 만든다.
6. **패널 인터랙션은 LLM을 호출하지 않는다.** 규칙표(`status_rules`)와
   허용 연산 수식(`allowed_ops`)을 설계 시점에 한 번 생성하고, 조작은 프론트에서
   평가만 한다. 토글 한 번에 6초 기다리는 데모는 데모가 아니다.
7. **모든 status 규칙과 패널 provenance는 attribution을 갖는다.** `kind`가
   `paper`면 실재하는 `span_id`, `external`이면 실재하는 `evidence_id`를
   가리켜야 한다. `pedagogical`/`illustrative`면 UI에 그렇게 표시된다.
   존재하지 않는 id를 가리키는 규칙은 크리틱에서 fatal이며 verdict는
   `UNSAFE_TO_VISUALIZE`다. 이때 패널을 내지 않고 evidence map과
   assumption map만 낸다.

## 스테이지 계약

실행 순서대로다. 분기는 없다 — 모든 입력이 같은 스테이지 열을 통과한다.

| 스테이지 | LLM | reads | writes | 예산 |
|---|---|---|---|---|
| parse/enrich | ✗ | source_path, source_text, source_title, claim_text | doc, number_pool | 8s |
| context | ✓ | doc, number_pool, source_title, source_text, claim_text | context_analysis | 8s |
| claims graph | ✓/직접 seed | doc, claim_text, context_analysis | claims, root_claim_id | 6s |
| score | 소형 | claims, number_pool | scores | 2s |
| select/frontier | ✗ | claims, scores | selected_claim_id, frontier_claim_id, critical_path_ids | 0.1s |
| bottleneck | ✓ | selected_claim_id, claims, doc, context_analysis | bottleneck | 0.1s |
| router | ✓ | bottleneck, doc, context_analysis | explainer_route | 0.1s |
| path analysis | ✓ | doc, claims, critical_path_ids | claim_analyses, assumptions, path_unsafe | 5s × path |
| external | 쿼리 ✓ / 검색 ✓ | claims, critical_path_ids | external | 25s |
| panels | ✓ | bottleneck, explainer_route, claims, doc, number_pool, assumptions, context_analysis | explainer, spec | 6s |
| editorial | ✓ | explainer, bottleneck | explainer | 0.1s |
| critic | ✗→✓ | explainer, spec, graph, path analyses, external | verdict | 4s |
| render | ✗ | explainer, spec, verdict, external | artifact | 1s |

**path analysis가 panels보다 먼저다.** 가정은 스위치보드 패널의 컨트롤이 되고,
다른 route에서는 비판 지점 문단의 재료가 된다. 순서를 뒤집으면 스위치보드
패널이 빈 채로 나온다.

**panels는 artifact를 내는 유일한 스테이지다.** `assumption_switchboard`는
경쟁 artifact가 아니라 panels가 고르는 패널 primitive 중 하나이며, 정량
메커니즘이 하나도 복원되지 않았을 때의 선택지다.

`reads`/`writes`는 각 스테이지의 상태 의존성을 명시한다. 실행 중 사용자
interrupt API와 Critic 재설계 루프는 두지 않는다.

**입력 경계.** `PaperState`는 `claim_text`, `source_text`, `source_path` 중
하나만 있어도
시작할 수 있다. `claim_text`가 있으면 그것을 `c1` root로 직접 사용하고 mapper를
호출하지 않는다. PDF가 함께 있으면 Parse가 원문 span을 optional context로
추가하지만 root claim은 바꾸지 않는다. PDF가 없을 때는 `input_claim`이라는
수동 span만 만들며 이를 paper attribution으로 표시하지 않는다. figure image
decoding/OCR은 Parse의 책임이 아니며 나중에 별도 vision provider로 붙인다.

**frontier 자동 선택.** `SelectFrontier`는 faithfulness 하한을 통과한 graph node
중 교육 가치·난이도·조작 가능성을 포함한 frontier score가 가장 높은 node를
고른다. root에서 해당 node까지의 `critical_path_ids`를 코드로 역추적하며,
동점이면 graph order를 따른다.

path analysis는 root→frontier의 각 node를 순서대로 분해·설명한다. graph의 나머지
node는 span binding과 구조 검증만 하고 상세 분석하지 않는다.

status 규칙표는 panels가 스위치보드 패널의 `model` 안에 함께 낸다. 별도
스테이지가 아니다. 설계와 규칙이 갈라지면 컨트롤은 있는데 아무 status도 안
움직이는 화면이 나온다.

외부 검색 결과는 독립된 근거 목록이다. 같은 URL은 하나로 합치되 어떤 검색
갈래에서 발견됐는지 보존한다. panels는 이 목록을 읽지 않으며 외부 근거로
status나 controversy를 자동 판정하지 않는다.

Critic의 결정론적 검사에서 fatal violation이 하나라도 나오면 verdict는
`UNSAFE_TO_VISUALIZE`다. 파이프라인은 panels를 재실행하지 않고 render까지
계속 진행해, 선택 claim의 원문·외부 근거와 가정만 담은 읽기 전용 artifact를
만든다. 원문 map은 claim 근거 span과 가정 귀속 span의 순서 보존 합집합이다.
이 판정은 `quantitative / qualitative` mode를 바꾸지 않는다.

가정이 하나도 채굴되지 않은 것은 스위치보드 패널일 때만 fatal이다. 거기서는
가정이 곧 컨트롤이라 0개면 죽은 화면이 된다. 다른 패널이 인터랙션을 제공하는
route에서는 비판 지점 문단이 얇아질 뿐 안전 문제가 아니다.

## claim status

스위치보드 패널 안에서만 표시된다. 다른 패널에는 status 배지가 없다.

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
  스위치보드 패널이 이 mode의 인터랙션을 담당한다)
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

## 현재 구현 범위

계보 graph, frontier 선택, root→frontier path analysis, 네 갈래 external 나열,
병목 선택과 패널 구성, safe map은 현재 구현 범위다.

실제 `LinerSearch`, OpenAI Agents structured output, DemoPayload builder,
FastAPI/SSE bridge, 브라우저 입력 UI, critic soft check가 구현되어 있다. 기본은
offline mock이며 `--live`에서만 API를 호출한다. 이후 변경도 `PaperState` 필드,
stage `reads`/`writes`, `EventBus` 시그니처를 유지해야 한다.

**아직 안 된 것 (다음 작업 순서대로).**

1. 병목이 1개로 고정되어 있다. `example/kimi-k3-explainer.html`처럼 2~4개
   섹션이 되려면 `state.bottlenecks: list`가 필요하다.
2. `PanelComposer`의 calibration 패널·glossary가 Guo 논문 전용 하드코딩이다.
   다른 논문에서는 이 route를 타도 문구가 맞지 않는다.
3. `thesis`가 초록 원문 그대로다. "큰 게 아니라 안 막히게 만든 것" 같은
   편집적 재정의가 claim graph의 첫 번째 용도인데 아직 쓰이지 않고 있다.
4. 프론트엔드에 패널 primitive별 렌더러가 `assumption_switchboard` 하나뿐이다.
   나머지는 질문·설명·출처만 있는 정적 카드로 표시된다.

## 도메인 — 없음

분야 팩은 없다. 어떤 패널이 나오는지는 분야가 아니라 **이 논문에서 어떤
슬롯이 채워지는가**가 결정한다. 도메인 인자, `PACKS`, `--domain` 플래그는
삭제됐고 다시 넣지 않는다.

서로 성격이 다른 논문 두 계열은 **일반성 검증 fixture**로만 쓴다:
`fixtures/guo17a.pdf`(ML, 정량 claim 후보 88.6%)와
`fixtures/sample.pdf`·glioblastoma PDF(의학 계열). 한쪽에서만 통과하는 변경은
하드코딩이 새로 생겼다는 신호다.

수치가 figure 안에만 있는 논문은 number_pool이 비어 `qualitative`로 강등된다.
논문을 고정하기 전에 `scripts/audit_pool.py`로 claim 후보 중 수치가 묶인
비율을 먼저 재고, 그 숫자를 보고 논문을 고른다. 강등 자체는 여전히 기능이다.

## 하지 않을 것

- 논문 저자가 틀렸다는 판정 (`broken` status 없음)
- 토글마다 LLM 호출
- 선택되지 않은 claim까지 미리 분해
- 모든 figure 자동 vectorization
- 범용 코드 실행
- 논문 전체를 웹사이트로 변환
- multi-user, 음성, 논문 간 메타분석
