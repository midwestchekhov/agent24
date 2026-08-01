# Defense backend 테스트 코퍼스

`tests/inputs/`는 임의 PDF 경계와 prompt-injection, 빈 문서, 암호화 문서를
검증하는 합성 fixture다. 실제 품질 fixture는 `fixtures/guo17a.pdf`,
`fixtures/sample.pdf`, GBM Nature PDF이며 semantic rubric은
`tests/defense_gold.json`에 있다.

## 결정론적 테스트

```bash
python -m pytest -q
```

provider를 호출하지 않는 테스트는 다음을 고정한다.

- PDF/text parsing과 references/methods claim 제외
- source span과 claim token grounding
- frontier score·단일 선택·LOW_SCORE_VARIANCE
- assumption provenance·single-off impact
- chunk 없는 external relation의 unresolved 강등
- URL dedupe와 fast action/round cap
- critic fatal partial report
- raw/status SSE와 API 입력 계약

## 실제 API gold acceptance

과금 호출은 명시적 `--live`가 있을 때만 한다.

```bash
python -m playground.defense_eval --live --out-dir /tmp/paper-defense-gold
```

실행 결과에는 fixture별 payload 경로, elapsed, deterministic score가 남는다.
실패 시 API key나 provider 원문은 출력하지 않는다.

## 공통 실패 기준

- terminal payload 없이 traceback으로 종료
- source span 없는 claim·assumption·evidence attribution
- unresolved 검색 결과를 긍정 근거로 해석
- 방어 범위가 원 주장보다 넓음
- API key가 event, payload, exception에 노출
- raw channel에 status event 혼입
