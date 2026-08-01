# 프런트엔드 설계 원칙

이 문서는 `defense-report.html`, `pipeline-stream.html` 두 파일이 왜 지금 모습인지를
적어 둔 것이다. 화면을 고칠 때는 이 원칙을 먼저 읽고, 원칙과 어긋나는 변경이면
원칙을 먼저 고쳐라.

레퍼런스 구현이 곧 스펙이다. 애매하면 두 HTML 파일의 실제 코드를 따른다.

---

## 0. 이 제품이 하는 일

원고를 넣으면 → 가장 먼저 반박당할 주장 하나를 찾고 → 나올 만한 질문으로 바꾼 뒤
→ Liner에서 관련 논문을 모아 → **버틸 수 있는 범위와 아닌 범위를 가른다.**

요약 도구가 아니다. 검색 결과 뷰어도 아니다.
화면이 표시해야 하는 것은 "많이 찾았다"가 아니라 **"어디까지가 안전한가"** 다.

---

## 1. 화면은 세 개, 파일은 두 개

`defense-report.html` 한 파일 안에 세 화면이 들어 있다. 라우터를 붙이지 마라.
데모 중에 URL을 잘못 여는 사고가 가장 흔하다.

```
#view-input   접수   → 원고를 받는다
#view-running 진행   → 기다리는 시간을 설명으로 채운다
#view-report  보고서 → 결과
```

`show("input" | "running" | "report")` 하나로만 전환한다. 세 섹션은 `hidden` 속성으로
제어하고, CSS `display:none`을 직접 건드리지 않는다.

`pipeline-stream.html`은 심사위원용 세컨드 스크린이다. 별도 창으로 띄운다.
메인 화면에 로그를 중복해서 넣지 않는다 — 진행 화면은 요약만, 상세는 저쪽.

---

## 2. 절대 빈 화면을 만들지 않는다

라이브 시연에서 가장 큰 리스크는 백엔드가 죽는 것이다. 그래서:

- 실행 엔드포인트를 여러 개 순서대로 시도한다 (`/api/runs`, `/api/run`, …)
- 전부 실패하면 **에러를 띄우지 않고** 저장된 예시(`MOCK`)로 보고서를 렌더한다
- 대신 판단을 속이지 않게 `· 저장된 예시 보고서`라고 표시해서 없어지지도 않는다
- SSE가 안 붙으면 조용히 포기하고, 진행 표시는 타이머로 계속 흐른다

**"에러 화면"보다 "정직한 예시 화면"이 낫다.** 이건 취향이 아니라 시연 전략이다.

단, **서버가 이유를 대며 거절한 경우는 예외다.** 암호화된 PDF, 텍스트 레이어가
없는 스캔본, PDF가 아닌 파일은 백엔드가 죽은 게 아니라 정상적으로 거절한 것이다.
이때는 MOCK으로 덮지 말고 접수 화면에 **서버가 말한 이유를 그대로** 보여준다.
"처리할 수 없습니다"가 아니라 "왜 안 되는지"를 말해야 한다.

---

## 3. 백엔드 필드 이름을 화면이 알지 못하게 한다

`adapt(payload)` 함수 하나가 백엔드 JSON을 화면 모델로 바꾼다.
**렌더링 코드는 백엔드 키를 절대 직접 읽지 않는다.**

```js
questions: arr(pick(report, [
  "attack_questions", "expected_questions", "questions", "reviewer_questions"
], []))
```

`pick()`은 후보 키를 순서대로 시도한다. 관계 표현은 `normRel()`이
`challenges | contradicts | refutes` 아무거나 받아 하나로 정규화한다.

백엔드 스키마가 바뀌면 **`adapt()` 안의 배열에 키를 하나 더 넣는 것으로 끝난다.**
이 경계를 무너뜨리지 마라. 렌더 함수 안에서 `payload.artifact.xxx`를 읽는
코드가 보이면 잘못된 것이다.

### 3.1 현재 백엔드가 실제로 주는 모양 (`defense/1.0`)

`adapt()`의 후보 배열 맨 앞은 항상 실제 계약이다.

- 사용자용 본문은 `payload.artifact`, 제목은 `payload.run.source_title`
- 반박당할 주장은 `artifact.target_claim.text`, 부연은 `artifact.weak_point`
- 질문은 `artifact.attack_questions[]` — `question`, `why_likely`, `assumption_ids`
- 전제는 별도 배열 `artifact.assumptions[]`. 질문의 `assumption_ids`로 이어 붙인다
- 근거는 `artifact.external_evidence`가 **관계별로 묶인 객체**다
  (`supports` / `qualifies` / `challenges` / `unresolved`). 배열이 아니다
- 범위는 `artifact.defensible_scope` **객체** — `statement`, `conditions`,
  `excluded_scope`

### 3.2 근거를 질문에 붙이는 법

`artifact.external_evidence`에는 **어느 질문의 근거인지가 없다.**
연결은 감사용 기록 쪽에 있다.

```
analysis.evidence_ledger.records[].id            == external_evidence[…].evidence_id
analysis.evidence_ledger.records[].obligation_ids == attack_questions[].id  ("q2", "q3")
```

`adapt()`가 이 둘을 join해서 질문마다 근거를 붙인다. 이 join이 없으면 3열 질문
카드가 전부 "확인 못 함"이 되어 버린다.

`relation`이 `unresolved`인 항목은 **근거로 세지 않는다.** 원문을 확보하지 못했다는
뜻이므로 "확인 못 함" 박스 쪽으로 보낸다. 이건 규칙이지 실수가 아니다.

---

## 4. 가로형 레이아웃

모니터에서 볼 화면이다. 세로로 긴 문서형 스크롤을 만들지 않는다.

- 컨테이너 최대 `1560px`, 좌우 여백 `48px` (`.shell`)
- 접수: 왼쪽 설명 / 오른쪽 입력 패널 2단
- 진행: 왼쪽 경과 시간 / 오른쪽 단계 목록 2단
- 보고서: **예상 질문을 3열로 나란히** 둔다. 한 화면에 다 보이는 게 핵심이다
- 질문이 1~2건이면 `.qgrid.n1` / `.n2`로 열 수가 자동으로 줄어든다
- 4건 이상이면 3열이 두 줄이 된다. 줄바꿈 지점의 세로선과 안쪽 여백은
  `:nth-child(3n)` / `:nth-child(3n+1)` 규칙이 처리한다. 이걸 지워서 격자가
  깨지게 만들지 마라
- `1180px` 아래에서만 1열로 접힌다

세로 스크롤이 길어지는 방향의 변경은 이 원칙에 걸린다.

---

## 5. 한글 조판

```css
body {
  word-break: keep-all;      /* 어절 중간에서 끊지 않는다 */
  overflow-wrap: break-word;
  line-break: strict;
}
```

이건 전역으로 걸려 있다. 각 컴포넌트에서 덮어쓰지 마라.

메인 제목은 `white-space: nowrap`으로 한 줄 고정이고, `1180px` 아래에서만 풀린다.
제목이 어중간하게 두 줄로 접히면 첫인상이 무너진다.

---

## 6. 말투

**쓰지 않는 말:** 하중, 병목, 공격 표면, 파싱, chunk, reference, 검증 의무,
frontier, primitive, payload, 관계 판정, 산출물

**쓰는 말:** 반박당할 주장, 약한 고리, 당연하게 여기는 것, 원고 읽기, 논문 원문,
버틸 수 있는 범위, 확인 못 함

내부 설계 어휘를 화면에 그대로 내보내지 않는다. 사용자는 대학원생이지
이 파이프라인의 개발자가 아니다.

문장은 필요한 존댓말로 짧게. 명사 나열로 끝내지 않는다.
- ✗ "방어 범위 분리 및 근거 판정 완료"
- ✓ "버틸 수 있는 범위와 아닌 범위를 가릅니다"

**제품 이름을 화면에 쓰지 않는다.** 로고, 브랜드 바, 태그라인 전부 없다.
화면은 첫 문장부터 바로 일을 시작한다.

---

## 7. 근거 없음을 숨기지 않는다

이 제품의 신뢰는 "못 찾았다"를 말하는 데서 나온다.

- 근거가 0건인 질문은 **비우지 않고** 점선 박스에 `확인 못 함`을 띄운다
- 문구는 반드시 "괜찮다는 뜻이 아니라, 아직 확인하지 못했다는 뜻입니다"를 포함한다
- 세컨드 스크린에도 `확인 못 함` 카운터가 상단에 있다. **0이 아닌 게 자랑거리다**
- 하단 각주: "판정은 논문 원문을 직접 확인한 경우에만 내립니다"

이 문구들을 "긍정적으로" 다듬지 마라. 덜 것이어야 신뢰가 생긴다.

---

## 8. 근거 카드의 우선순위

한 질문에 붙는 근거는 항상 이 순서다.

```
challenges (반박)   → 항상 펼쳐짐, 붉은 왼쪽 테두리
qualifies  (조건부) → 접힘, 노란 테두리
supports   (뒷받침) → 접힘, 초록 테두리
```

**반박이 먼저 보여야 한다.** 심사 대비 도구인데 지지 근거가 위에 오면 제품이
거짓말을 하는 것이다. `REL_ORDER`로 정렬이 강제돼 있다.

---

## 9. 색

| 토큰 | 값 | 쓰임 |
|---|---|---|
| `--paper` | `#f3f0e8` | 배경. 종이 |
| `--ink` | `#17212f` | 본문, 테두리 |
| `--pen` | `#b4232a` | 반박, 약한 지점. **빨간펜** |
| `--limit` | `#a4661c` | 조건부, 일부만 확인됨 |
| `--ok` | `#2d6a4f` | 뒷받침 |
| `--rule` | `#d7d1c2` | 괘선 |

빨강은 반박과 상태에만 쓴다. 강조용으로 쓰지 마라.
초록은 "뒷받침"과 "여기까지는 근거가 있다"에만.

---

## 10. 타이포

- 본문·제목: 세리프 (`Georgia` → 한글은 시스템 명조 폴백)
- 라벨·버튼·메타: 산세리프 (`.util` 클래스 또는 `ui-sans-serif`)
- 숫자·로그: `ui-monospace`

**웹폰트를 불러오지 않는다.** 오프라인이거나 네트워크가 느린 행사장에서 폰트가
안 떠서 레이아웃이 깨지는 걸 막기 위해서다. 시스템 폰트 스택만 쓴다.

---

## 11. 모션

거의 없다. 세 군데뿐이다.

1. 진행 화면의 무한 프로그레스 바
2. 단계 목록이 하나씩 진해지는 것 (`.track li.now`)
3. 로그 한 줄이 나타날 때 `translateY(-3px)` 페이드

`prefers-reduced-motion`에서 전부 꺼진다. 이 외에 넣지 마라.

---

## 12. 하지 말 것 / 확인할 것

- 키보드 포커스가 항상 보인다 (`:focus-visible` 외곽선)
- 드롭존은 `tabindex="0"` + Enter/Space로 열린다
- `aria-selected`로 탭 상태를 알린다
- 1920 / 1440 / 1180px에서 확인한다. 1180px에서 1열로 접힌다
- 질문이 1, 2, 5건일 때 격자가 깨지지 않는지 확인한다
- 브라우저 저장소(localStorage 등)를 쓰지 않는다
- 외부 요청은 백엔드 API와 SSE뿐. CDN 의존 없음
- 프레임워크·빌드 도구·라우터를 도입하지 않는다
- 파일을 세 개째 만들지 않는다

---

## 13. 백엔드에 붙이기

정적 파일은 백엔드와 **같은 오리진**에서 서빙해야 한다. `playground/server.py`가
`frontend/`를 `/`에 정적 마운트하므로 그냥 이렇게 열면 된다.

```bash
python -m playground.server --live-fast
# http://127.0.0.1:8000/defense-report.html
```

`file://`로 직접 열면 POST가 막힌다. **이게 가장 흔한 실패다.** 이때도 화면은
MOCK으로 끝까지 가지만, 실제 실행은 되지 않는다.

실행 흐름은 비동기다. 동기 응답을 기대하지 마라.

```
POST /api/runs            (multipart: pdf | source_text, source_title)
  → 202 { run_id, events_url, payload_url }
GET  /api/runs/{id}/events   SSE: status / raw / complete / error
GET  /api/runs/{id}/payload  완료 후 defense/1.0 payload
```

한 번에 한 run만 돈다. 이미 도는 중이면 `409`가 온다.

### 실제 단계 이름

진행 화면의 단계 목록과 `pipeline-stream.html`의 `stageClass()`는 이 여덟 개에
맞춰져 있다. 백엔드 `raw` 이벤트의 `stage_start` / `stage_end`가 실어 보낸다.

```
parse → defense_context → defense_frontier → defense_probe
     → defense_evidence → defense_synthesizer → defense_critic → defense_render
```
