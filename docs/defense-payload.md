# DefensePayloadV1

`schema_version`은 `defense/1.0`이다. 내부 분석과 사용자용 report를 분리한다.

```json
{
  "schema_version": "defense/1.0",
  "run": {},
  "mode": "complete",
  "artifact": {
    "primitive": "defense_report",
    "target_claim": {},
    "selection_reason": {},
    "assumptions": [],
    "attack_questions": [],
    "external_evidence": {
      "supports": [],
      "qualifies": [],
      "challenges": [],
      "unresolved": []
    },
    "defensible_scope": {},
    "assumption_impacts": [],
    "limitations": []
  },
  "spans": {},
  "analysis": {
    "claim_graph": {},
    "candidate_scores": [],
    "evidence_ledger": {}
  },
  "raw_events": []
}
```

`partial_defense_report`는 검증된 target claim·assumption·evidence만 제공하고
critic이 승인하지 않은 `defensible_scope`를 생략한다. `refusal`은 claim/span을
검증할 수 없을 때만 사용한다.

## 원문 span

`spans`는 artifact가 실제로 인용한 span만 담는다. 수집 대상은
`target_claim.source_refs`, `assumptions[].source_span_ids`,
`assumption_impacts[].source_refs`, `defensible_scope.source_refs`다.

```json
{ "p2_b4": { "page": 2, "kind": "paragraph", "section": "results", "text": "..." } }
```

`analysis`를 열지 않고도 방어 문장을 원문과 대조할 수 있어야 하므로 artifact와
같은 층에 둔다. 문서 전체를 싣지는 않는다.

## 외부 근거

`external_evidence`의 각 항목은 `chunk_ids`와 `chunks`를 함께 갖는다. chunk만
사실 근거이므로 본문이 artifact 안에 있어야 한다.

```json
{
  "evidence_id": "ev_0",
  "relation": "qualifies",
  "chunk_ids": ["ch_0_3"],
  "chunks": [{ "id": "ch_0_3", "num": 3, "content": "..." }]
}
```

`chunk_ids`는 critic precheck가 사용하므로 제거하지 않는다. `relation`이
`unresolved`가 아니면 `chunks`는 비어 있을 수 없다.

## 가정 영향

각 assumption마다 하나의 행만 둔다.

```json
{
  "assumption_id": "a1",
  "status_if_off": "narrows",
  "surviving_scope": "...",
  "because": "...",
  "source_refs": ["p2_b4"],
  "evidence_ids": []
}
```

프론트엔드는 이 표를 로컬로 평가한다. toggle로 API, LLM, Liner, pipeline을 다시
호출하지 않는다.
