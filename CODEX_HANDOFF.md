# AGENT:24 Codex 인계 문서

작성 목적: `midwestchekhov/agent24`에서 이후 작업을 Codex로 이어가기 위한 현재 상태·판단·실행 순서·완료 기준의 단일 인계 문서.

`AGENTS.md`는 매 작업의 강제 계약이고, 이 파일은 현재 세션의 작업 순서와 배경이다. 둘이 충돌하면 `AGENTS.md`의 불변 규칙을 따른다.

## 1. 프로젝트 목표

Paper Playground는 논문을 요약하는 서비스가 아니다. PDF를 한 번 입력하면 에이전트가 검증 가능한 claim 후보를 만들고, interaction score가 가장 높은 claim 하나를 자동 선택한 뒤, 다음을 수행한다.

```text
claim
 ├─ 원문 근거(span)
 ├─ 성립에 필요한 assumption
 └─ 외부 evidence: support / contradict / boundary / methodology
       ↓
interaction artifact + claim_status_logic
       ↓
브라우저에서 assumption을 끄며 strong / conditional / weak 확인
```

심사위원이 임의의 논문이나 예상하지 못한 입력을 줄 수 있으므로 특정 PDF·claim·검색 결과에 맞춘 데모로 만들면 안 된다. fixture는 offline smoke와 발표 리허설용이다.

## 2. 대회에서 반드시 보여야 하는 장면

1. PDF를 한 번만 입력한다.
2. agent가 tool call, 검색, 판단, 출력까지 자동 수행한다.
3. 메인 화면에는 선택된 claim, 원문 span, assumptions, artifact가 보인다.
4. 세컨드 화면에는 실제 실행 중 발생한 raw `tool_call`/`tool_result`가 가공 없이 실시간으로 보인다.
5. 사용자가 assumption을 끄면 API 재호출 없이 status가 즉시 바뀐다.
6. 잘못된 PDF나 분석 불가능한 입력은 억지 결과 대신 `refused` 또는 명시적 실패로 끝난다.
7. 심사위원이 다른 PDF를 넣어도 같은 단일 입력 계약으로 동작한다.

## 3. 현재 검토된 변경사항

팀원은 기존의 사용자 claim 선택형 방향을 다음처럼 바꾸었다.

- 사용자 claim 선택 제거
- interaction score 최고 claim 자동 선택
- PDF 파싱과 원문 `span_id` 연결
- assumption 추출과 switchboard 추가
- assumption toggle 시 브라우저 로컬 규칙 평가
- 외부 검색을 네 facet으로 분리
- OpenAI Agents 연결을 위한 adapter 구조 추가
- 협업 계약, `DemoPayloadV1`, raw event fixture 추가
- 정적 frontend 추가

이 변경은 “입력 1회 → 자율 실행”이라는 대회 규칙과 잘 맞는다. 다만 다음 기능은 현재 완성된 것으로 간주하면 안 된다.

- `MockLLM`/`MockSearch` 중심의 offline 실행
- 실제 Liner API 연결 미완성
- OpenAI Agents SDK가 실제 pipeline에서 호출되는지 미검증
- 실제 HTTP/SSE/NDJSON live bridge 부재
- 정적 fixture와 실제 raw stream 사이의 연결 부재
- Critic의 span/evidence/status attribution 끝단 검증 미흡

## 4. 작업 역할과 branch

**팀원 B 전용 branch는 `second-monitor`이며 origin에 이미 존재한다.**
(과거 `feat/minki-second-monitor`에서 이름이 변경되었다.) 새 branch를 만들지
말고 이 branch에서 이어서 작업한다. branch 절차의 기준 문서는 `AGENTS.md`
5절이다.

```text
second-monitor
```

이 branch의 1차 책임은 팀원 B 역할인 세컨드 모니터 및 live payload pipeline이다. 다만 세컨드 모니터가 실제 tool event를 받으려면 최소한의 backend adapter가 필요할 수 있다. `EventBus` 핵심 계약을 바꾸지 않는 선에서 구현하고, 변경이 필요하면 먼저 보고한다.

## 5. Codex가 수행할 일 — 순서 고정

### 0단계. 작업 전 확인

- [ ] 현재 저장소가 실제로 `midwestchekhov/agent24`인지 확인한다.
- [ ] `git status`, 현재 branch, remote를 확인한다.
- [ ] 작업 중인 사용자 변경이 있으면 덮어쓰지 않는다.
- [ ] 저장소 루트의 `AGENTS.md`, `CLAUDE.md`, `COLLABORATION.md`, `OPEN_ISSUES.md`, `README.md`를 읽는다.
- [ ] `.env`, API key, token을 출력하거나 commit하지 않는다.

### 1단계. 기존 작업 branch로 전환

아래 명령은 저장소 루트에서 실행한다. `AGENTS.md` 5절과 동일한 절차다.

```bash
git fetch origin
git switch second-monitor
git status --short --branch
```

주의:

- `main`에 직접 작업하지 않는다.
- `git reset --hard`, force push를 사용하지 않는다.
- branch가 origin보다 뒤처져 있으면 `git pull --ff-only`만 사용한다.
- 작업 시작 로그에 branch명과 시작 시각을 남긴다.

작업 시작 보고:

```text
[작업 시작]
담당자: Minki / Codex
브랜치: second-monitor
목표: 실제 raw event를 세컨드 모니터에 실시간 표시하고 DemoPayloadV1을 main UI에 연결
수정 예정: frontend/, payload/ 또는 bridge adapter의 최소 파일
보호 계약: PaperState, stage reads/writes, EventBus event shape, DemoPayloadV1 field names
완료 조건: 아래 8단계 검증 통과
```

### 2단계. baseline 실행 및 기록

기능을 수정하기 전에 현재 상태를 측정한다. 먼저 가상환경을 활성화한다
(PowerShell: `.venv\Scripts\Activate.ps1`, bash: `source .venv/Scripts/activate`).

```bash
python -m playground.run
python -m playground.run --pdf fixtures/does-not-exist.pdf
python -m pytest -q
node --check frontend/data.js
node --check frontend/app.js
```

각 명령의 성공·실패·환경 문제를 기록한다. baseline에서 실패하던 항목과 새로 만든 branch에서 발생한 실패를 구분한다.

### 3단계. 계약과 경계 확인

- [ ] raw channel과 status channel을 분리한다.
- [ ] bridge는 `Event.to_json()` 원본 JSON 문자열을 그대로 전달하고,
      브라우저는 그 문자열을 그대로 렌더링한다(파싱 결과는 라벨 생성에만 사용).
- [ ] `DemoPayloadV1`의 snake_case와 `schema_version`을 유지한다.
- [ ] main UI는 payload를 소비하고, 세컨드 화면은 raw stream을 소비하도록 역할을 분리한다.
- [ ] assumption toggle은 브라우저 로컬 evaluator만 호출한다.
- [ ] transport가 실패하면 “live”라고 표시하지 않고 오류 상태를 표시한다.

### 4단계. 세컨드 모니터용 transport 설계·구현

최소 구조:

```text
Pipeline / EventBus
      ↓ raw Event.to_json() (원본 JSON 문자열)
bridge adapter (stdlib http.server.ThreadingHTTPServer)
      ↓ SSE, 127.0.0.1:PLAYGROUND_BRIDGE_PORT(기본 8765)
      ↓ 링 버퍼 500개 + Last-Event-ID 재전송
second monitor browser
```

transport는 위로 확정한다. NDJSON 옵션은 폐기한다. 새 dependency
(FastAPI/uvicorn/aiohttp/flask 등)를 도입하지 않는다.

권장 구현 순서:

1. offline event replay로 브라우저가 raw event를 순서대로 렌더링하는지 먼저 확인한다.
2. 실제 `EventBus` subscriber가 발생한 이벤트를 bridge로 전달하도록 연결한다.
3. 실행 종료·stage error·client failure를 포함해 stream을 닫는다.
4. 브라우저에서 연결 끊김과 schema 오류를 명시적으로 표시한다.
5. raw event의 원본 JSON을 화면에 함께 표시하고, 사람이 읽는 label은 부가 정보로만 둔다.

세컨드 화면 완료 조건:

- [ ] 실제 실행 중 `tool_call`과 대응하는 `tool_result`가 나타난다.
- [ ] event 순서가 pipeline 실행 순서와 같다.
- [ ] `call_id`, `name`, `arguments`, `result`, `error` 등 원본 필드가 보존된다.
- [ ] `decision`, `stage_error`도 raw event로 표시된다.
- [ ] status channel 이벤트가 raw stream에 섞이지 않는다.
- [ ] 종료된 실행을 “연결 중”으로 남기지 않는다.
- [ ] 잘못된 PDF의 거절/오류 흐름도 화면에서 확인된다.

### 5단계. DemoPayloadV1 adapter 연결

메인 UI에는 backend state를 직접 노출하지 말고 `DemoPayloadV1`로 변환한다.

- [ ] `selected_claim_id`는 실제 자동 선택 결과와 같다.
- [ ] claims의 evidence span이 실제 `spans`에 존재한다.
- [ ] assumptions의 attribution이 실제 span/evidence/pedagogical 규칙과 일치한다.
- [ ] `artifact.status_rules`가 브라우저 evaluator가 이해할 수 있는 형태다.
- [ ] `raw_events`는 raw channel만 순서대로 포함한다.
- [ ] payload 버전이 다르면 명시적 unsupported 상태를 표시한다.

### 6단계. 로컬 toggle 검증

브라우저 개발자 도구 또는 테스트 로그로 toggle 전후 API/LLM 호출이 0회인지 확인한다.

```text
base: strong 또는 conditional
assumption 하나 OFF: conditional 또는 weak
전체 OFF: 가장 약한 규칙을 적용한 weak
```

상태와 함께 항상 `because` 및 attribution을 표시한다. `weak`만 단독으로 표시하지 않는다. `broken`은 사용하지 않는다.

### 7단계. live agent/API 연결 상태 확인

세컨드 모니터만 연결하고 실제 agent/API가 여전히 mock이면, 제출 상태를 live라고 표현하지 않는다.

다음 순서로 확인한다.

- [ ] OpenAI API 또는 Agents SDK 호출이 실제 pipeline에서 실행된다.
- [ ] 호출·응답이 raw `tool_call`/`tool_result`로 기록된다.
- [ ] Liner API 검색 client가 실제 응답을 `evidence_id`, URL, title, snippet, 발견 facet과 함께 보존한다.
- [ ] 검색 0건·부분 실패·rate limit·malformed response가 명시적 event가 된다.
- [ ] key가 없거나 live 연결이 실패하면 안전한 offline fallback 또는 refused를 사용한다.
- [ ] mock 결과를 live 결과처럼 표시하지 않는다.

이 단계에서 `clients.py`, `pipeline.py`, `state.py` 등 core 파일을 수정해야 하면 변경 필요성·영향 범위·계약 유지 여부를 먼저 보고한다. 특히 Liner 응답 사양을 추측하지 않는다.

### 8단계. Critic과 입력 다양성 검증

세컨드 화면 PR의 직접 범위를 넘어도, 최종 제출 전 다음을 확인한다.

- [ ] 존재하지 않는 `span_id`가 fatal로 거절된다.
- [ ] 존재하지 않는 `evidence_id`가 fatal로 거절된다.
- [ ] `paper`/`external`/`pedagogical` attribution이 규칙대로 검증된다.
- [ ] 숫자 출처가 없는 경우 quantitative 결과를 꾸며내지 않는다.
- [ ] 숫자 복원이 어려우면 qualitative로 강등된다.
- [ ] 근거 claim이 없으면 refused로 끝난다.
- [ ] 선택 claim은 score 최고점이며 동점일 때 원문 순서다.
- [ ] 선택되지 않은 claim의 assumption을 미리 분해하지 않는다.

### 9단계. 전체 smoke 및 발표 리허설

정상·실패·외부 검색 실패·임의 PDF를 각각 실행한다.

```text
입력 1회
→ 실제 또는 명시된 offline tool call
→ raw second screen
→ artifact
→ local toggle
→ 추가 API 호출 0회
```

발표 시나리오:

- 2분: 문제, claim 단위, 왜 기존 요약과 다른지, 에이전트 개요
- 3분: PDF 1회 입력부터 artifact와 raw second screen까지
- 2분: 심사위원의 새로운 입력을 받아 refusal/degrade 또는 새 artifact를 보여주기

슬라이드는 최대 5장이고, 제품 설명은 라이브 데모에서 증명한다.

### 10단계. commit·push·PR

```bash
git status
git diff --check
git diff --stat
git add <의도한 파일만>
git commit -m "feat: connect live second monitor stream"
git push -u origin second-monitor
```

PR 본문에 다음을 포함한다.

```text
## 변경 목적
실제 pipeline raw event를 second monitor에 실시간 표시하고 DemoPayloadV1을 연결했다.

## 변경 파일
- ...

## 검증
- 정상 CLI: PASS/FAIL
- 잘못된 PDF: PASS/FAIL
- pytest: PASS/FAIL 또는 환경상 미실행 사유
- node syntax: PASS/FAIL
- live raw stream: PASS/FAIL
- toggle 추가 호출 0회: PASS/FAIL

## 계약 변경
없음 / 변경 내용과 승인 여부

## 남은 이슈
- 실제 Liner key/API 사양 대기
- OpenAI live adapter 미완성
```

main merge는 리뷰 후에만 한다. merge 전에 main 최신 상태와 충돌 여부를 확인한다.

## 6. 파일별 작업 경계

| 영역 | 기본 담당 | Codex 작업 시 주의 |
|---|---|---|
| `playground/state.py` | core | 임의 수정 금지 |
| `playground/pipeline.py` | core | live bridge 때문에 바꾸기 전 보고 |
| `playground/events.py` | core 계약 | subscriber 사용 우선, signature 변경 금지 |
| `playground/clients.py` | API | Liner/OpenAI 실제 연결 시 별도 검토 |
| `frontend/` | 화면/B 역할 | 세컨드 모니터와 main UI 구현의 1차 범위 |
| payload adapter/bridge | 화면/B 역할 | `DemoPayloadV1`와 raw event 계약 보존 |
| `CLAUDE.md`, `AGENTS.md` | 계약 | 합의 없는 수정 금지 |
| `OPEN_ISSUES.md` | 기록 | 해결한 항목만 근거와 함께 갱신 |

## 7. 최종 완료 기준

완료 기준은 두 층으로 나뉜다. Codex(팀원 B)는 7-1만 판정한다. 7-2는 팀
차원의 제출 조건으로, 사람이 판정하며 B 범위 밖 작업(팀원 A 담당)을 포함한다.

### 7-1. B 브랜치 머지 조건 (Codex 판정)

다음 중 하나라도 미충족이면 B 작업을 "머지 가능"으로 결론내리지 않는다.

- [ ] `second-monitor` branch에서 작업했다.
- [ ] 14:00 이후 커밋만 작업으로 남아 있다.
- [ ] second monitor가 실제 EventBus raw stream을 실시간 표시한다
      (정적 fixture replay만으로는 미충족).
- [ ] main UI가 `DemoPayloadV1`을 소비한다.
- [ ] assumption toggle이 추가 API/LLM 호출 없이 동작한다.
- [ ] 정상 PDF, 잘못된 PDF의 오류·거절 흐름이 화면에 표시된다.
- [ ] PR에 변경 파일과 검사 결과가 남아 있다.
- [ ] API secret이 저장소·로그·스크린샷에 노출되지 않았다.

### 7-2. 팀 제출 조건 (사람 판정, B 범위 밖 포함)

B의 머지 여부와 무관하게, 제출 전 사람이 확인한다. 아래가 미충족이어도
Codex는 코드를 수정하지 않고 blocker로만 보고한다.

- [ ] 단일 PDF 입력으로 사람이 승인하지 않아도 terminal state에 도달한다.
- [ ] 실제 OpenAI API/SDK 사용이 코드 경로와 실행 로그에서 확인된다. (A 담당)
- [ ] Liner API가 실제 외부 evidence를 반환하거나, 실패를 정직하게 표시한다. (A 담당)
- [ ] 원문 span/evidence attribution이 Critic에서 모두 검증된다. (A 담당)
- [ ] 정상 PDF, 잘못된 PDF, 숫자 부족 PDF, 검색 0건/실패를 각각 확인했다.

## 8. Codex에 첫 메시지로 전달할 작업 지시

```text
이 저장소에서 AGENTS.md와 CODEX_HANDOFF.md를 먼저 읽어라.
현재 원격 상태를 read-only로 확인한 뒤, 기존 second-monitor branch로
전환하라. 새 branch를 만들지 말고 main에는 직접 수정하지 마라.
먼저 baseline smoke를 실행하고 결과를 보고하라.

이번 1차 목표는 팀원 B 역할의 세컨드 모니터다.
실제 EventBus raw event를 원본 JSON 문자열 그대로, AGENTS.md 1절에 확정된
SSE transport로 브라우저에 전달하고, frontend second monitor에 실시간 출력하라.
DemoPayloadV1과 raw/status 채널 계약을 바꾸지 마라.
assumption toggle은 브라우저 로컬 규칙 평가만 사용해야 한다.

새 dependency, EventBus signature, PaperState, stage reads/writes, core 계약을
변경해야 하면 구현 전에 이유·대안·영향 범위를 보고하고 멈춰라.

작은 단위로 구현하고 각 단계마다 정상/실패 smoke와 live raw stream을 검증하라.
완료 후 PR에 변경 목적, 파일 목록, 테스트 결과, 계약 변경 여부, 남은 Liner/OpenAI
live 이슈를 기록하라.
```

