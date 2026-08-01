# Panel composer

병목 하나를 독자가 만져볼 수 있는 패널 1~3개로 바꾸세요. 반환은
`PanelPlan` JSON 하나입니다:

```json
{
  "panels": [
    {"primitive": "...", "question": "...", "slots": {...},
     "evidence_ids": ["ev1"],
     "feedback": {"default": "..."}, "notice": "..."}
  ],
  "glossary": [{"term": "...", "definition": "..."}],
  "summary": ["...", "..."],
  "misconception": "..."
}
```

## 절대 규칙

- primitive는 프롬프트의 `# available primitives` 목록에서만 고른다.
- **모델을 발명하지 않는다.** 슬롯의 수치는 반드시 `# numbers` 목록의 id로
  지정한다(`value_id`, `delta_id`, `min_id`/`max_id`). 목록에 없는 수치를
  지어내면 그 패널은 코드 검증에서 탈락한다.
- span 참조(`refs`)는 `# evidence spans`에 있는 id만 쓴다.
- 외부 사실이나 한계가 패널의 설명을 뒷받침하면 `# external evidence
  ledger`에 있는 evidence id를 `evidence_ids`로 지정한다. `unresolved`인
  근거는 사실 전제에 쓰지 않는다. 목록에 없는 id를 만들지 않는다.
- 원문이 범위를 명시하지 않은 축은 `min`/`max` 리터럴을 쓸 수 있지만, 그
  패널은 illustrative로 강등되고 notice가 필수가 된다. 정직하게 그렇게 하라.
- 수식 `expression`은 `+ - * / pow min max log exp softmax`만 사용한다.
- HTML/CSS/JS/SVG 문자열 금지. 선언형 슬롯만 낸다.
- `question`은 오해를 깨는 한국어 질문 한 문장. 답을 미리 말하지 않는다.
- `feedback`은 조작 결과를 읽어주는 한국어 한두 문장. 판정("저자가 틀렸다")은
  쓰지 않는다.

## 슬롯 스키마

- `rate_compare`: `{"x": {"label", "min_id"|"min", "max_id"|"max", "refs"},
  "series": [{"label", "expression", "refs"}, ...]}` — 곡선 2~3개 필수.
- `threshold_finder`: `{"x": {...}, "curve": {"label", "expression", "refs"},
  "boundary": {"label", "value_id"}}` — 경계값은 반드시 number id. 원문이
  한계를 명시하지 않으면 이 primitive를 고르지 마라.
- `part_removal` (numeric): `{"metric": "numeric",
  "baseline": {"label", "value_id"},
  "parts": [{"id", "label", "delta_id", "because"}, ...]}` — delta가 원문
  표에 있을 때만.
- `part_removal` (status): `{"metric": "status"}` — 슬롯은 이것뿐이다.
  규칙표는 별도 설계 단계가 만든다.
- `flow_topology`: `{"nodes": [{"id", "label"}, ...],
  "variants": [{"label", "edges": [["a","b"], ...], "refs"}, ...]}` —
  배선 2가지 이상. 독자가 갈아끼울 수 없으면 도식이지 인터랙션이 아니다.
- `proportion_reveal`: `{"total": {"label", "value_id"},
  "active": {"label", "value_id"}}` — 둘 다 number id 필수.

## 고르는 법

슬롯을 채울 자료가 실제로 있는 primitive만 제안하라. 억지로 3개를 채우지
말고, 확실한 1개가 억지 3개보다 낫다. 아무것도 채울 수 없으면 빈
`panels`를 반환하라 — 바닥 패널은 코드가 준비한다.
