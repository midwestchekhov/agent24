# Renderer fixtures

브라우저에서 `?fixture=<name>`으로 불러 API 키 없이 4가지 artifact 상태를
확인한다. 백엔드 acceptance 기록이 아니라 **렌더러 개발용**이다.

| 파일 | primitive | 출처 |
|---|---|---|
| `partial.json` | `partial_defense_report` | **실제 live 실행 산출물** |
| `complete.json` | `defense_report` | partial에서 파생 (손으로 보강) |
| `partial_deadline.json` | `partial_defense_report` | partial에서 파생 |
| `refusal.json` | `refusal` | 손으로 작성 |

## partial.json — 유일한 실물

```bash
python -m playground.run --live-fast --pdf fixtures/guo17a.pdf \
  --artifact-out frontend/fixtures/partial.json
```

Guo et al., *On Calibration of Modern Neural Networks* 대상 74초 실행 결과다.
critic이 `evidence_source_mismatch`로 fail-closed 판정해 방어 범위를 숨긴,
정상적인 강등 경로의 산출물이다.

## 파생 fixture

파생된 셋은 `run.fixture_note`에 합성 표시가 들어 있다. 렌더링 검증 외의
용도로 인용하면 안 된다.

`complete.json`은 critic이 보류한 `defensible_scope`와 `assumption_impacts`를
채운 것이다. 가정 `a1`은 의도적으로 `necessary`로 승격했다 — 그러지 않으면
`status_if_off`가 전부 `narrows`가 되어 `unsupported` 분기를 렌더링해볼 수 없다.

## 재생성

`partial.json`을 새로 캡처했으면 나머지 셋도 다시 파생해야 한다. 파생 규칙은
이 파일의 표와 위 설명이 전부이며, 별도 스크립트를 저장소에 두지 않는다.
