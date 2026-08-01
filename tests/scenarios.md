# Paper Defense Simulator 테스트 시나리오

현재 제품 입력 계약은 `PDF` 또는 `source_text`/Markdown이다. `claim_text` 단독
입력은 방어 시뮬레이터의 제품 경로가 아니며 API는 422를 반환한다. 결정론적
테스트는 provider를 호출하지 않고, 실제 품질 acceptance는 명시적으로
`python -m playground.defense_eval --live`를 사용한다.

공통 불변 조건:

- `schema_version=defense/1.0`
- target frontier는 정확히 하나이며 원문 span을 가리킨다.
- references/methods/acknowledgments span은 claim 후보에서 제외한다.
- 검색 관계가 `unresolved`가 아니면 실제 Liner chunk가 있어야 한다.
- assumption 하나당 OFF impact 하나만 생성한다.
- critic fatal이면 `partial_defense_report`이고 `defensible_scope`를 숨긴다.
- raw event와 status event를 섞지 않고 API key를 payload/event에 넣지 않는다.

## A. PDF·파싱 경계

| ID | 입력 | 기대 |
|---|---|---|
| A01 | `fixtures/guo17a.pdf` | 하나의 frontier, calibration 공격 질문, terminal payload |
| A02 | `fixtures/sample.pdf` | 비교 공정성·leakage·임상 범위 공격 후보 |
| A03 | GBM Nature fixture | 표본·인과·mouse-to-human 범위 공격 후보 |
| A04 | references가 긴 논문 | reference 문장이 claim이 되지 않음 |
| A05 | methods protocol이 긴 논문 | methods가 claim 후보·frontier가 되지 않음 |
| A06 | `tests/inputs/07_scanned_no_text_layer.pdf` | 명시적 거부, traceback 없음 |
| A07 | `tests/inputs/08_encrypted.pdf` | 명시적 거부, 오류 payload 생성 |
| A08 | `tests/inputs/10_not_a_pdf.pdf` | 서버 업로드 422 |
| A09 | 25MiB 초과 PDF | 서버 업로드 413 |
| A10 | prompt injection 문장이 본문에 포함된 PDF | 데이터로만 취급, 지시로 실행하지 않음 |

## B. source_text 입력

| ID | 입력 | 기대 |
|---|---|---|
| B01 | abstract + results 정상 텍스트 | defense report 또는 검증된 partial |
| B02 | 한국어 논문 텍스트 | UTF-8 span과 payload 직렬화 보존 |
| B03 | 수치 없는 서술형 논문 | 수치 발명 없이 report/partial |
| B04 | references heading 이후 문단 | claim 후보 제외 |
| B05 | methods heading 이후 문단 | claim 후보 제외 |
| B06 | 빈 문자열·공백 | API 422 |
| B07 | 서로 모순되는 두 결과 문단 | 한쪽을 조용히 합치지 않고 source span 유지 |
| B08 | 숫자만 나열된 텍스트 | 반증 가능한 claim으로 승격하지 않음 |
| B09 | unrelated claim을 기존 span에 붙인 model output | token grounding에서 폐기 |
| B10 | malformed source text | 오류를 감싸고 terminal 결과 반환 |

## C. 분석·근거 경계

| ID | 조건 | 기대 |
|---|---|---|
| C01 | frontier 점수 입력 차이가 있음 | 고정 가중식으로 점수 분산·단일 선택 |
| C02 | 모든 점수가 동일 | `LOW_SCORE_VARIANCE` decision 이벤트 |
| C03 | 정의 재진술 assumption | 폐기 |
| C04 | origin이 paper인데 source span 없음 | 폐기 또는 critic fatal |
| C05 | 모두 `necessary` assumption | 일부를 independent로 정규화 |
| C06 | 같은 URL에 supports·qualifies 여러 판정 | 하나의 deduped record로 chunk·관계 보존 |
| C07 | 검색 0건 | positive evidence로 사용하지 않음 |
| C08 | chunk 없는 supports/qualifies/challenges | unresolved 강등 |
| C09 | source span이 존재하지만 claim 토큰과 무관 | claim 후보 폐기 |
| C10 | 방어 범위에 “모든 환경” 추가 | critic precheck fatal, partial 전환 |
| C11 | critic 실패 | defensible scope와 impact 숨김 |

## D. 서버·실행 경계

| ID | 입력/상태 | 기대 |
|---|---|---|
| D01 | PDF/source_text 없이 POST | 422 |
| D02 | `claim_text` 단독 POST | 422 |
| D03 | 진행 중 두 번째 POST | 409 `RUN_IN_PROGRESS` |
| D04 | 진행 중 payload 요청 | 409 |
| D05 | 완료 payload 요청 | 200 `defense/1.0` |
| D06 | 없는 run id | events/payload 모두 404 |
| D07 | SSE | `raw → status/complete/error` 채널 분리 및 순서 보존 |
| D08 | fast profile | evidence action 1개, round 1개, visualization 0회 |
| D09 | deadline 도달 | `partial_defense_report`, scope 미노출 |
| D10 | live-fast | run metadata에 elapsed, stage elapsed, provider call counts |

## 실행

결정론적 전체 회귀:

```bash
python -m pytest -q
```

실제 API gold acceptance(명시적으로만 과금):

```bash
python -m playground.defense_eval --live --out-dir /tmp/paper-defense-gold
```

세부 rubric과 fixture별 허용 개념은 `tests/defense_gold.json`에 둔다.
