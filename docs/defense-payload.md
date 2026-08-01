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
