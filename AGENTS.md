# AGENT:24 — Paper Defense Simulator 계약

이 저장소의 현재 제품은 논문 제출·발표 전 디펜스 시뮬레이터다. PDF 또는 원문
텍스트를 한 번 입력하면, 에이전트가 가장 중요하고 공격받기 쉬운 주장 하나를
고르고 예상 질문·숨은 가정·외부 문헌·조건부 방어 범위를 반환한다.

## 제품 경계

- 법률 자문, 특허성·신규성·진보성 판정, peer review 대행이 아니다.
- “검색 결과가 없다”는 신규성·타당성·방어 가능성의 근거가 아니다.
- 원문에 묶인 주장과 Liner가 반환한 chunk만 사실 근거로 취급한다.
- 분석자의 조건부 추론은 `basis_kind=analyst_inference`로 표시한다.
- claim graph는 내부 분석이며 최종 보고서의 대상은 선택된 frontier 하나다.
- 프론트엔드와 UX는 별도 담당이다. 이 브랜치에서는 backend, payload, prompts,
  tests, 실행 문서만 수정한다.

## 현재 브랜치

새 기능 브랜치는 `feat/paper-defense-backend`다. 이전 실험은 다음 위치에 보존한다.

- 특허 선행문헌 spike: `feat/prior-art-liner-spike`
- 프론트 artifact renderer WIP: `wip/frontend-artifact-renderers`

## 실행 계약

```text
PDF/text
 → Parse
 → Defense Context Analyst
 → Claim Graph
 → Attack Frontier Scorer
 → Assumption & Attack Probe
 → OpenAI-controlled Evidence Loop
 → Defense Synthesizer
 → Defense Critic
 → DefensePayloadV1
```

입력은 PDF 또는 `source_text`/Markdown이다. claim 단독 입력은 제품 API에서 받지
않는다. 실행은 명시적으로 live profile을 선택해야 한다.

```bash
python -m playground.run --live-fast --pdf fixtures/guo17a.pdf \
  --artifact-out /tmp/guo-defense.json
python -m playground.server --live-fast
```

전체 실행 deadline은 120초다. deadline에 도달하면 검증된 정보만 담은
`partial_defense_report`를 반환한다.

## 데이터 불변식

- claim과 assumption은 실제 `doc.spans`에 연결되어야 한다.
- References, acknowledgments, methods의 문단은 claim 후보에서 제외한다.
- 모든 외부 relation은 실제 Liner chunk가 있어야 `supports`, `qualifies`,
  `challenges`로 공개할 수 있다. chunk가 없으면 `unresolved`다.
- `supports`와 `qualifies`는 신규성 또는 법적 결론이 아니다.
- 가정 OFF는 한 번에 하나만 계산한다. API 재호출·검색 재실행이 없어야 한다.
- `necessary` 가정은 드물게 허용하며 전체 가정이 necessary이면 재정규화한다.
- 방어 범위는 원 주장보다 넓어질 수 없다.
- raw event와 status event는 섞지 않는다. API key와 provider raw credential은
  event, payload, exception에 절대 넣지 않는다.

## 결과 계약

payload의 `schema_version`은 `defense/1.0`이다. `artifact.primitive`는
`defense_report`, `partial_defense_report`, `refusal` 중 하나다.

보고서는 다음을 포함한다.

- target claim과 frontier 선택 이유
- 숨은 가정 3~5개
- 예상 공격 질문 최대 3개
- `supports`/`qualifies`/`challenges`/`unresolved` 외부 근거
- 조건부 방어 범위와 제외 범위
- 가정별 단일 OFF 영향
- 한계와 미검증 항목

Critic fatal이면 방어 문장과 영향 문장을 숨기고 부분 보고서를 반환한다. 검증
가능한 claim 자체가 없을 때만 refusal이다.

## 구현 원칙

- 기존 Parse, provenance, evidence ledger, Liner/OpenAI adapter, deadline을
  최대한 재사용한다.
- 교육용 `BottleneckMiner`, `PanelComposer`, `KoreanEditorial`,
  `VisualizationAdapter`, primitive switchboard는 활성 pipeline에서 제거한다.
- 점수는 모델이 상수를 반환해도 변별되도록 구성요소와 최종값을 event에 기록한다.
- 모델은 구조화된 출력만 사용한다. 자유 JSON 파싱은 호환 fallback일 뿐이다.
- 기본 품질 acceptance는 실제 OpenAI·Liner API로 수행한다. Mock provider는
  provider transport 경계 테스트에서만 사용한다.

## 테스트 완료 기준

- Guo, sample clinical, GBM 세 gold fixture가 120초 이내 terminal payload를 만든다.
- 세 fixture 중 두 개 이상은 grounded external evidence를 가진 complete report다.
- 모든 relation에 실제 chunk가 연결된다.
- frontier는 정확히 하나다.
- 방어 범위가 원 주장보다 넓지 않다.
- malformed/encrypted/scanned PDF, prompt injection, missing provenance,
  claim-only 422, SSE raw/status 분리를 회귀 테스트한다.

## Git·보안

- 작업은 feature branch에서만 한다. main에 직접 push하지 않는다.
- 기존 사용자 변경과 프론트 파일을 덮어쓰지 않는다.
- `.env`, API key, token을 출력·커밋·fixture에 넣지 않는다.
- 커밋은 문서 계약, core/payload, pipeline, tests 단위로 나눈다.
