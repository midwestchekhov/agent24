# Paper Defense Simulator 인계

현재 backend 작업 브랜치는 `feat/paper-defense-backend`다. 이전 특허 검색 spike와
프론트 WIP는 별도 branch에 보존되어 있다.

## 목표

논문 PDF/text 한 번 입력 → 핵심 claim graph → 공격 frontier 하나 → 가정과 예상
질문 → Liner 학술문헌 대조 → 조건부 방어 범위 → `DefensePayloadV1`.

## 먼저 읽을 문서

1. `AGENTS.md`
2. `CLAUDE.md`
3. `README.md`
4. `docs/product-direction.md`
5. `docs/defense-payload.md`
6. `docs/evaluation.md`

## 구현 순서

1. state에 defense score, attack question, defense report 타입을 추가한다.
2. context analyst와 claim builder를 defense vocabulary로 전환한다.
3. 교육용 stages를 pipeline에서 제거한다.
4. assumption probe를 frontier 전용으로 만든다.
5. evidence controller를 assumption/attack query 기반으로 재연결한다.
6. synthesizer와 critic을 추가하고 partial report를 구현한다.
7. server/CLI 입력을 PDF/text 전용으로 바꾼다.
8. 결정론적 경계 테스트와 세 gold live acceptance를 추가한다.

## 완료 기준

- 120초 이내 payload
- frontier 정확히 하나
- 실제 chunk 기반 relation
- 과장 없는 conditional defense scope
- critic fatal 시 방어 문장 미노출
- API key 미노출
