# Renderer fixtures

브라우저에서 `?fixture=<name>`으로 불러 API 키 없이 4가지 artifact 상태를
확인한다. 백엔드 acceptance 기록이 아니라 **렌더러 개발용**이다.

| 파일 | primitive | 출처 |
|---|---|---|
| `complete.json` | `defense_report` | **실제 live 실행 산출물** (critic 통과) |
| `partial.json` | `partial_defense_report` | **실제 live 실행 산출물** (critic 강등) |
| `complete_necessary.json` | `defense_report` | partial에서 파생 |
| `partial_deadline.json` | `partial_defense_report` | partial에서 파생 |
| `refusal.json` | `refusal` | 손으로 작성 |

## 실물 둘

둘 다 Guo et al., *On Calibration of Modern Neural Networks* 대상
`--live-fast` 73~74초 실행 결과다. 같은 입력에서 critic 판정이 갈렸다.

```bash
python -m playground.server --live-fast          # complete.json
python -m playground.run --live-fast --pdf fixtures/guo17a.pdf \
  --artifact-out frontend/fixtures/partial.json  # partial.json
```

`partial.json`은 critic이 `evidence_source_mismatch`로 fail-closed 판정해
방어 범위를 숨긴, 정상적인 강등 경로의 산출물이다.

## 파생 fixture

파생된 셋은 `run.fixture_note`에 합성 표시가 들어 있다. 렌더링 검증 외의
용도로 인용하면 안 된다.

`complete_necessary.json`이 따로 있는 이유는 하나다. 실물 두 건 모두 가정이
전부 `independent`라 `status_if_off`가 `narrows`뿐이고, `unsupported` 분기를
렌더링해볼 수 없다. 이 파일은 가정 `a1`을 `necessary`로 승격해 그 분기 하나만
확보한다.

## 재생성

실물을 새로 캡처했으면 파생본도 다시 만들어야 한다. 파생 규칙은 이 파일의 표와
위 설명이 전부이며, 별도 스크립트를 저장소에 두지 않는다.
