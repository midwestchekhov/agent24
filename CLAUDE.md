# Paper Defense Simulator 기술 계약

## 목적

단일 PDF/text 입력에서 논문 내부의 핵심 claim graph를 만들고, 가장 중요하면서
공격 가능한 frontier 하나를 선택한다. 선택된 frontier에 대해서만 가정·예상 질문·
외부 문헌·조건부 방어 범위를 생성한다.

Claim graph는 내부 분석이다. 최종 산출물은 graph를 시각화하는 explainer가 아니라
defense report다.

## 활성 pipeline

```text
Parse
ContextAnalyst
BuildClaims
DefenseScore
SelectFrontier
AssumptionProbe
EvidenceController
DefenseSynthesizer
DefenseCritic
RenderDefense
```

`BottleneckMiner`, `PanelComposer`, `KoreanEditorial`, `VisualizationAdapter`,
`DesignInteraction`, switchboard primitive는 활성 경로에서 호출하지 않는다.

## 선택 규칙

각 claim은 다음 값을 갖는다.

- `importance`
- `vulnerability`
- `scope_gap`
- `source_grounding`
- attack dimensions와 rationale

점수는 다음과 같다.

```text
0.35 * importance
+ 0.35 * vulnerability
+ 0.20 * scope_gap
+ 0.10 * source_grounding
```

grounding 하한 미만 claim은 선택에서 제외한다. root를 자동 제외하지 않는다.
최종 선택은 정확히 하나이며, 모든 구성요소와 결과값을 raw decision event에 남긴다.

## Evidence 규칙

EvidenceController는 선택된 frontier의 공격 질문과 가정을 검색 obligation으로
변환한다. Liner reference는 후보이고, reference chunk만 사실 근거다.

- chunk가 없으면 relation은 `unresolved`
- `supports`: 유사 조건에서 주장을 지지하는 근거
- `qualifies`: 범위·효과 크기·조건을 제한하는 근거
- `challenges`: 결과나 방법론을 직접 문제 삼는 근거
- 0건·검색 실패는 긍정 증거로 승격하지 않음
- 최대 3 actions, 최대 2 rounds, source당 3 chunks, 전체 12 chunks

## 방어 범위

DefenseSynthesizer는 원문 claim, assumption, 실제 chunk만 입력으로 받는다.
방어 문장은 원 주장보다 넓을 수 없다. 외삽은 `basis_kind=analyst_inference`,
`conditions`, `limitations`를 함께 요구한다.

가정 영향은 한 가정씩 계산한다.

- 모든 가정 ON: `holds`
- independent OFF: `narrows`
- necessary OFF: `unsupported`

여러 가정을 동시에 끈 조합은 계산하지 않는다.

## Critic

Critic은 deterministic precheck를 먼저 수행한다.

- span, evidence id, chunk 번호 존재
- relation의 chunk grounding
- source section과 claim 연결
- 방어 범위의 source/evidence attribution
- 정의형 assumption 및 공허한 failure effect

통과 후 structured critic이 방어 문장의 과장, analyst inference 미표시,
원 주장보다 넓은 scope를 검사한다. Critic은 수정하거나 재실행하지 않는다.
치명적 위반은 `partial_defense_report`로 강등한다.

## Payload

공개 계약은 `defense/1.0`이다. 내부 `analysis`와 사용자용 `artifact`를 분리한다.
사용자용 artifact에는 claim graph id나 점수 목록을 노출하지 않아도 되지만, audit
payload에는 원문 span, query, evidence id, raw events를 보존한다.
