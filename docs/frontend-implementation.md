# 프런트엔드 구현 노트

작성 2026-08-02 · 브랜치 `feat/defense-frontend-ui`

설계 원칙은 `frontend/FRONTEND_DESIGN.md`가 계약이다. 이 문서는 **그 화면을 실제
백엔드에 붙이면서 무엇을 확인했고 무엇을 고쳤는지**의 기록이다. 원칙과 이 문서가
어긋나면 원칙이 우선이다.

## 산출물

```
frontend/defense-report.html    접수 / 진행 / 보고서 세 화면
frontend/pipeline-stream.html   심사위원용 세컨드 스크린
frontend/FRONTEND_DESIGN.md     설계 원칙 (계약)
```

정적 HTML 두 개뿐이다. 프레임워크·빌드·라우터·저장소·CDN 없음.

`frontend/index.html` + `app.js` + `report.js`는 이전 렌더러로, 손대지 않았다.
`playground/server.py`가 `frontend/`를 `/`에 정적 마운트하므로 `/`는 여전히
예전 화면이 뜬다. 데모 URL은 `/defense-report.html`이다.

## 확정된 백엔드 계약

`playground/server.py`와 실행 결과로 직접 확인한 값이다. 추정 아님.

### 실행

```
POST /api/runs                  multipart: pdf | source_text, source_title
  → 202 { run_id, status, events_url, payload_url }
GET  /api/runs/{id}             { status, mode, ... }   재접속용
GET  /api/runs/{id}/events      SSE
GET  /api/runs/{id}/payload     완료 후 defense/1.0
GET  /api/health
```

- **비동기다.** POST는 보고서를 돌려주지 않는다
- 필드명은 `pdf` (`file` 아님), `source_text`, `source_title`
- `claim_text`만 보내면 422로 거절된다
- 한 번에 run 하나. 중복 제출은 409
- 업로드 상한 25 MiB

### SSE

**이름 붙은 이벤트만 온다.** `onmessage`로는 아무것도 오지 않는다.

```
event: raw       stage_start / stage_end / tool_call / tool_result / decision / stage_error
event: status    사람이 읽는 진행 문구
event: complete  종료
event: error     실패
```

재접속하면 서버가 **기록을 처음부터 다시 보낸다.** 이벤트 `id`로 중복을 거른다.

### 단계 이름 (8개, 7개 아님)

```
parse → defense_context → defense_frontier → defense_probe
     → defense_evidence → defense_synthesizer → defense_critic → defense_render
```

`raw` 이벤트의 `stage_start` / `stage_end`가 `stage` 필드로 실어 나른다.
진행 화면 목록과 `stageClass()`가 이 여덟 개에 1:1로 맞춰져 있다.

### payload (`defense/1.0`)

- 본문 `payload.artifact`, 제목 `payload.run.source_title`
- 주장 `artifact.target_claim.text`, 부연 `artifact.weak_point`
- 질문 `artifact.attack_questions[]` — `question`, `why_likely`, `assumption_ids`
- 전제는 별도 배열 `artifact.assumptions[]`
- 근거 `artifact.external_evidence`는 **관계별로 묶인 객체** (배열 아님)
- 범위 `artifact.defensible_scope`는 **객체** — `statement`, `conditions`, `excluded_scope`

## 핵심 설계 결정

### adapt() 하나만 백엔드 키를 읽는다

렌더 함수는 백엔드 키를 직접 읽지 않는다. 스키마가 바뀌면 `adapt()` 안 후보
배열에 키를 하나 더 넣는 것으로 끝난다. 각 배열의 맨 앞이 현재 계약이다.

### 근거를 질문에 붙이는 join

`external_evidence`에는 **어느 질문의 근거인지가 없다.** 연결은 감사 기록에 있다.

```
analysis.evidence_ledger.records[].id             == external_evidence[…].evidence_id
analysis.evidence_ledger.records[].obligation_ids ⊇ attack_questions[].id
```

이 join이 없으면 3열 질문 카드가 전부 "확인 못 함"이 된다.

라이브에서 `obligation_ids`가 `["c1", "q2"]`처럼 **질문 id가 아닌 claim id를
섞어서** 내보내는 것을 확인했다. 질문 id로만 필터하므로 `c1`은 무시된다.
fixture만 봤으면 못 잡았을 케이스다.

`relation: unresolved`는 근거로 세지 않는다. 원문을 확보하지 못했다는 뜻이므로
"확인 못 함" 쪽으로 보내고, 몇 건이 그렇게 남았는지 문장으로 말한다.

### 백엔드가 죽는 것과 거절하는 것을 구분한다

- 어떤 엔드포인트도 응답 없음 = 죽음 → **MOCK으로 보고서를 렌더**하고 하단에
  `· 저장된 예시 보고서`만 작게 표시
- 4xx로 이유를 대고 거절 → MOCK으로 덮지 않고 **접수 화면에 이유를 표시**
- 실행까지 갔다가 `refusal` → 마찬가지로 접수 화면. `reason_code`를 우리 말로
  옮기고 서버 원문도 아래에 남긴다

`MOCK`은 실제와 같은 `defense/1.0` 모양이다. 그래서 예시가 렌더되면 live와
동일한 `adapt()` 경로가 그대로 검증된다.

### 세컨드 스크린은 미리 띄운다

시연 전에 창을 열어 두면 예시가 재생되고 있다가, run이 시작되면 창 참조로
`?run=<id>`를 넘겨 **자동으로 실제 실행으로 전환**된다. 저장소도, 백엔드 추가
엔드포인트도 필요 없다.

## 거절 경로 (실측)

`tests/inputs/` 전부 실제 서버에 넣어 확인한 응답이다.

| 입력 | 응답 | 화면 |
|---|---|---|
| `08_encrypted` | 422 `could not be opened as a PDF` | 암호/손상 안내 |
| `11_truncated` | 422 `could not be opened as a PDF` | 위와 같음 |
| `09_empty` | 422 `is not a PDF` | PDF 아님 안내 |
| `10_not_a_pdf` | 422 `is not a PDF` | 위와 같음 |
| `07_scanned_no_text_layer` | **202 후 refusal** `INPUT_UNREADABLE` | 스캔본 안내 |
| `12_blank_page` | 202 후 refusal | 실행 후 사유 표시 |
| 입력 없음 | 422 `source_text or pdf is required` | 원고 없음 |
| 중복 제출 | 409 | 이미 처리 중 |

스캔본은 **업로드 단계에서 걸리지 않는다.** 열리기는 하므로 접수는 통과하고,
`parse`에서 실패해 `reason_code: INPUT_UNREADABLE`로 돌아온다. 프런트에서 사유를
말할 수 있으므로 백엔드 변경은 필요 없었다.

## 고친 결함

주어진 HTML에 있던 것과 라이브에서 드러난 것.

1. **`display:flex`가 `[hidden]`을 이겼다.** `#view-input`의 author 규칙이 UA
   기본값을 눌러서 세 화면이 한 페이지에 전부 쌓였다. 보고서는 접수 화면 아래에
   계속 렌더되고 있었다. `[hidden] { display: none !important }`로 해결
2. **동기 응답을 기다렸다.** 실행 경로가 POST 응답에서 보고서를 꺼내려 해서,
   성공한 run조차 MOCK으로 빠졌다. POST → SSE → GET payload로 교체
3. **`onmessage`로는 SSE가 안 온다.** 이름 붙은 이벤트라 단계 표시가 실제
   데이터에서 한 번도 움직이지 않았다. `addEventListener("raw")`로 교체
4. **FormData 필드명이 `file`이었다.** 실제는 `pdf`
5. **3열 격자가 질문 4건 이상에서 깨졌다.** `:last-child`만으로는 줄바꿈
   지점의 괘선·여백이 처리되지 않는다. `:nth-child(3n)` / `(3n+1)` 규칙 추가
6. **`scrollIntoView`가 창 전체를 스크롤했다.** 세컨드 스크린 상단의
   "확인 못 함" 카운터가 화면 밖으로 나갔다. 로그 자체 스크롤로 교체
7. **replay 시각이 전부 `0.0s`였다.** 도는 중인 run에 붙으면 서버가 기록을
   한꺼번에 보내므로 벽시계로는 잴 수 없다. 이벤트 `ts` 기준으로 환산
8. **모델 원본 응답이 화면에 덤프됐다.** `{"weak_point":…}`,
   `UNGROUNDED_INFERENCE` 같은 내부 필드가 노출됐다. 요약 문구로 교체하고
   backend decision 문구(`DefensePayloadV1`, `frontier`, `evidence ledger`)도
   우리 말로 옮김
9. **partial에서 빈 칸이 남았다.** 방어 범위와 제외 범위 모두 비면 상자만
   그려졌다. 왜 비었는지 문장으로 말하도록 수정
10. **반박 0건일 때 카드 영역이 빈칸처럼 보였다.** 접힌 링크 하나만 남아서,
    못 찾았다는 사실을 문장으로 표시하도록 수정

## 검증

- 정적 fixture 5종(`complete`, `complete_necessary`, `partial`,
  `partial_deadline`, `refusal`) 전부 `adapt()` 통과 확인
- 1920 / 1440 / 1180px, 질문 1·2·3·5건, 제목 100자 초과 브라우저 확인
- **라이브 실행 3회** (`--live-fast`, 실제 OpenAI·Liner 호출)
  - `guo17a.pdf` 69.6s → `partial_defense_report`
  - `sample.pdf` → 검색 action 없이 종료
  - `guo17a.pdf` 84.0s → 8단계 전부 통과, 세컨드 스크린 실시간 부착 확인
- 렌더된 43줄 전수 검사로 세컨드 스크린 내부 용어 0건 확인

## 열린 항목

- **반박(challenges) 근거가 라이브에서 안 나온다.** 확인한 두 번 모두
  `challenges: 0`, `unresolved: 2`였다. 화면은 정직하게 그리고 있지만 데모의
  핵심 장면인 빨간 반박 카드가 뜨지 않는다. 검색·해석 쪽 문제이고 프런트에서
  만들어낼 수 없다. 시연 백업은 `?src=fixtures/complete.json`
- `defense_critic` 단독 24.6s. 120초 예산에서 긴 논문은 여유가 크지 않다
- 오프라인 프로필(`python -m playground.server`)은 옛 explainer 경로라
  `schema 1.1/2.0`을 내보낸다. `adapt()`가 `artifact`를 읽으므로 깨지지는
  않지만 단계 이름이 달라 진행 표시가 일부만 맞는다. 데모 경로는 `--live-fast`
- `/`는 여전히 예전 `index.html`이다. `defense-report.html`을 루트로 삼으려면
  파일명 교체가 필요한데, 이미 `main`에 병합된 파일을 덮는 일이라 하지 않았다

## 실행

```bash
python -m playground.server --live-fast
# http://127.0.0.1:8000/defense-report.html
```

`file://`로 열면 POST가 막힌다. **가장 흔한 실패다.** 이때도 화면은 MOCK으로
끝까지 가지만 실제 실행은 되지 않는다.
