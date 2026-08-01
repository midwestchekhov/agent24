# Paper Defense Simulator 협업 계약

## 현재 소유권

- backend: claim analysis, frontier, assumptions, evidence loop, critic, payload,
  tests, runtime 최적화
- frontend: `frontend/**`, DefensePayloadV1 renderer, UX와 로컬 assumption toggle
- 공통: API/SSE raw/status transport 계약

backend 브랜치는 `feat/paper-defense-backend`다. main 직접 커밋·force push는 하지
않는다. 프론트 작업은 backend 커밋에 섞지 않는다.

## 인터페이스

backend는 `schema_version=defense/1.0` payload를 제공한다.

- `artifact.primitive`: `defense_report | partial_defense_report | refusal`
- raw event는 `raw_events`에 순서대로 보존
- status event는 `raw_events`에 넣지 않음
- external relation은 `supports | qualifies | challenges | unresolved`
- assumption impact는 정적 표이며 프론트에서만 토글 평가

## 변경 원칙

- state/stage 계약을 바꿀 때는 payload와 테스트를 함께 갱신한다.
- Liner/OpenAI 호출 수·prompt 크기·deadline 변경은 acceptance 결과와 함께 기록한다.
- API key, provider raw response의 credential, 사용자 원문 외 비밀값을 커밋하지 않는다.
- 기존 특허 spike와 프론트 WIP는 해당 브랜치에서만 유지한다.

## 검토 기준

PR에는 다음을 포함한다.

- 변경된 실행 흐름
- 정상·부분·거부 결과 JSON
- live gold fixture별 elapsed/evidence/critic 결과
- 기존 계약을 제거하거나 깨뜨린 경우의 migration note
- 미검증 범위와 다음 작업
