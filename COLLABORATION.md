# AGENT:24 협업 계약

이 문서는 2026-08-01 18:33의 `main`(`5490e75`) 다음 작업부터 적용한다.
기존 main 이력은 고치지 않고, 이후 변경은 작업 브랜치와 PR로만 합친다.

## 제품 실행 계약

심사 경로는 입력 한 번으로 끝난다.

```text
PDF 입력 → parse → claims → score → select → assumptions → external
         → design → critic → render → artifact
```

- `select`는 interaction score 최고 claim을 자동 선택한다. 동점이면 원문 후보
  순서를 따른다.
- 실행 도중 사용자 승인, claim 선택, profile 변경을 요청하지 않는다.
- 근거 있는 claim 자체가 없으면 추가 입력을 받지 않고 `refused`로 끝낸다.
- Critic이 산출물의 잘못된 참조를 발견하면 추가 입력이나 재설계 없이
  `UNSAFE_TO_VISUALIZE`로 판정하고 읽기 전용 evidence/assumption map을 낸다.
- 렌더가 끝난 뒤 assumption 토글은 브라우저 안에서 규칙만 평가한다. 토글은
  API, LLM, 검색, 파이프라인 재실행을 호출하지 않는다.

## 브랜치와 PR

1. 최신 main에서 `feat/<이름>-<작업>` 또는 `fix/<작업>` 브랜치를 만든다.
2. 시작 전에 담당 파일, 목표, 완료 조건을 팀 채팅에 남긴다.
3. 작은 단위로 커밋하고 자기 브랜치에 push한다.
4. PR에 변경 목적, 정상/실패 smoke 결과, 계약 변경 여부를 적는다.
5. 팀장 리뷰 뒤 main에 합친다. main에는 직접 push하거나 force push하지 않는다.

계약 파일인 `CLAUDE.md`, `PaperState` 필드, stage `reads/writes`, `EventBus`
시그니처를 바꾸려면 PR 전에 팀장에게 먼저 알린다.

## 작업 경계

| 담당 | 기본 소유 영역 | 완료 조건 |
|---|---|---|
| 팀장/코어 | `state.py`, `pipeline.py`, `events.py`, `CLAUDE.md` | 단일 입력 DAG와 불변식 유지 |
| API | `clients.py`, Liner/OpenAI 연결, 환경 설정 | 원본 출처 보존, 빈 결과·부분 실패 이벤트 |
| 화면/세컨드 모니터 | `frontend/`, payload adapter, raw stream | 아래 payload만 소비, 토글은 완전 로컬 |

둘이 같은 파일을 고쳐야 하면 먼저 한 PR을 합친 뒤 다음 브랜치를 최신 main에서
만든다. 긴급히 병렬 작업할 때도 계약 타입을 임의로 복제하지 않는다.

## DemoPayloadV1

백엔드와 프론트는 다음 snake_case envelope를 계약으로 사용한다. 현재 정적
`frontend/data.js`는 이 계약의 offline fixture다. 실제 HTTP/SSE transport는
아직 구현하지 않았으며, 나중에 transport만 교체하고 renderer는 유지한다.

```json
{
  "schema_version": "1.0",
  "run_id": "offline-demo",
  "mode": "quantitative",
  "selected_claim_id": "c1",
  "claims": [
    {
      "id": "c1",
      "text": "...",
      "score": 0.69,
      "evidence_span_ids": ["p1_b1"]
    }
  ],
  "spans": {
    "p1_b1": {"page": 1, "kind": "paragraph", "text": "..."}
  },
  "artifact": {
    "primitive": "assumption_switchboard",
    "title": "...",
    "controls": [],
    "explanation": "...",
    "warning": null,
    "base_status": "strong",
    "status_rules": [
      {
        "assumption_id": "a1",
        "status": "conditional",
        "because": "...",
        "attribution": {
          "kind": "paper",
          "span_id": "p1_b1",
          "evidence_id": null
        }
      }
    ],
    "assumptions": [
      {
        "id": "a1",
        "claim_id": "c1",
        "text": "...",
        "kind": "measurement",
        "source": "paper_explicit",
        "weakens_how": "...",
        "span_id": "p1_b1"
      }
    ],
    "sources": {"paper": "c1", "external": 0}
  },
  "external": [
    {
      "id": "ev_c1_0",
      "claim_id": "c1",
      "title": "...",
      "url": "...",
      "snippet": "...",
      "stance": "unclear",
      "facets": ["boundary", "methodology"]
    }
  ],
  "raw_events": [
    {"id": "...", "ts": 0.0, "type": "tool_call", "name": "..."}
  ]
}
```

`artifact`는 다음 두 variant 중 하나다. 위 예시는 정상
`assumption_switchboard`이고, Critic에서 fatal violation이 생긴 경우에는 다음
안전 variant를 사용한다. `evidence_map.paper`는 선택 claim의 근거 span과 해당
가정이 귀속된 span을 최초 등장 순서로 합치며 중복과 실재하지 않는 span은 뺀다.

```json
{
  "primitive": "evidence_assumption_map",
  "mode": "quantitative",
  "title": "...",
  "evidence_map": {
    "claim_id": "c1",
    "paper": [
      {
        "span_id": "p1_b1",
        "page": 1,
        "kind": "paragraph",
        "text": "..."
      }
    ],
    "external": [
      {
        "id": "ev_c1_0",
        "claim_id": "c1",
        "title": "...",
        "url": "...",
        "snippet": "...",
        "stance": "unclear",
        "facets": ["boundary"]
      }
    ]
  },
  "assumption_map": [
    {
      "id": "a1",
      "claim_id": "c1",
      "text": "...",
      "kind": "measurement",
      "source": "paper_explicit",
      "weakens_how": "...",
      "span_id": "p1_b1"
    }
  ]
}
```

계약 세부사항:

- `schema_version`이 달라지면 renderer는 조용히 추측하지 말고 명시적으로
  지원 불가를 표시한다.
- `raw_events`는 raw 채널의 `Event.to_json()` 객체를 순서대로 append한 배열이다.
  필드 이름을 바꾸거나 요약하지 않고 status 채널 이벤트와 섞지 않는다.
- facet은 검색한 관점이지 출처가 실제로 주장을 지지·반박한다는 판정이 아니다.
- `evidence_assumption_map`에는 `controls`, `base_status`, `status_rules`를 넣지
  않는다. renderer는 토글을 만들지 않고 두 map만 읽기 전용으로 표시한다.
- 누락 가능한 값은 `null`로 보내고, 필드 자체를 임의로 다른 이름으로 바꾸지
  않는다.

## 최소 검증

기본 검증은 테스트 코드를 새로 쓰는 대신 다음 smoke로 한다.

```bash
python -m playground.run
python -m playground.run --pdf fixtures/does-not-exist.pdf
python -m pytest -q
node --check frontend/data.js
node --check frontend/app.js
```

새 테스트 파일은 핵심 불변식 회귀가 실제로 발견됐을 때만 팀장과 합의해 최소로
추가한다. PR에는 실행한 명령과 결과만 남긴다.

## 현재 보류 항목

- Liner API: 키와 실제 응답 사양을 받은 뒤 구현한다. 지금은 `MockSearch`가
  offline 기본값이다.
- live bridge: DemoPayloadV1 transport는 화면 담당 브랜치에서 구현한다.
- fixture/domain: `ml`이 기본이지만 `med`도 유지한다. 최종 집중 분야를 고른 뒤
  그 분야의 fixture 하나만 검증해 교체한다.
- GitHub branch protection, Collaborator, Daker 등록과 제출물은 저장소 밖에서
  팀장이 확인한다.

## 작업 공유 형식

```text
[작업 시작]
담당자:
브랜치:
목표:
수정 파일:
완료 조건:

[PR 요청]
PR:
정상 smoke:
실패 smoke:
계약 변경:
특히 볼 부분:
```
