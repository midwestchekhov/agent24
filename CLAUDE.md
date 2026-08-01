# Paper Playground — working contract

논문의 주장 하나를 골라 사용자가 직접 조작할 수 있는 미니 실험으로 변환하는 에이전트.
요약 도구가 아니다. 조작과 반증이 목적이다.

## 실행

```bash
python -m playground.run --domain med   # or ml
python -m pytest tests -q
```

목 클라이언트로 전체 DAG가 오프라인에서 돈다. 키가 생기면 `clients.py`의
`MockLLM` / `MockSearch`만 교체하면 되고 다른 파일은 건드리지 않는다.

## 불변 규칙

이 다섯 개는 리팩터링 중에도 깨지면 안 된다.

1. **모든 상태는 `PaperState` 하나에.** 스테이지가 자체 필드를 들고 있으면
   증분 재계산이 깨진다.
2. **모든 수치와 컨트롤은 `span_id`로 원문에 묶인다.** 묶이지 않은 수치는
   `provenance="illustrative"`여야 하고, 그 경우 `fidelity_warning`이 필수다.
3. **크리틱은 결정론적 검사가 먼저.** `critic_rules.precheck`가 잡을 수 있는
   것을 LLM에게 묻지 않는다.
4. **설계 스테이지는 HTML을 만들지 않는다.** `InteractionSpec` 스키마만 낸다.
   자유 코드 생성은 라이브 데모 최대 리스크.
5. **호출하지 않기로 한 판단도 이벤트로 남긴다.** `bus.decision(...)`.
   검색 남발보다 "이 주장은 검색 불필요"가 심사에서 강하다.

## 스테이지 계약

| 스테이지 | LLM | reads | writes | 예산 |
|---|---|---|---|---|
| parse | ✗ | — | doc, number_pool | 8s |
| claims | ✓ | doc | claims | 6s |
| score | 소형 | claims, number_pool | scores | 2s |
| external | 조건부 | claims | external | 5s |
| design | ✓ | claims, scores, external, profile, mode, selected_claim_id | spec | 6s |
| critic | ✗→✓ | spec, number_pool | verdict | 4s |
| render | ✗ | spec, verdict, mode | artifact | 1s |

`reads`/`writes`를 바꾸면 증분 재계산 범위가 자동으로 바뀐다. `INTERRUPTS`
테이블은 어떤 필드가 더러워지는지만 선언한다.

## 모드

`quantitative → qualitative → refused`. 강등은 실패가 아니라 기능이다.
데모에서 반드시 한 번 보여준다.

- 수치 복원 불가 → `qualitative` (정성적 인터랙션, 숫자 생성 금지)
- 근거 있는 주장이 하나도 없음 → `refused` (거절 화면도 제품의 일부)

## 지금 채워야 할 스텁

우선순위 순.

1. `stages/core.py::Parse.run` — pymupdf로 실제 추출. span 색인이
   나머지 전부의 전제라서 여기가 가장 먼저다.
2. `clients.py::OpenAIAgentsLLM` — Agents SDK. tracing을 `EventBus.emit_raw`로
   포워딩하면 세컨드 화면이 공짜로 채워진다.
3. `stages/core.py::BuildClaims` — 프롬프트는 요약 금지, 구조화만.
4. `clients.py::LinerSearch` — `VerifyExternal._trigger`가 참일 때만.
5. `stages/core.py::Critic` — precheck 통과 후 LLM 소프트 검사 추가.
6. 프론트 — primitive 렌더러 3종. 코어는 손대지 않는다.

## 도메인

`domains/__init__.py`의 `PACKS`가 med/ML 결정을 격리한다. 도메인 추가가
`pipeline.py`나 `stages/`를 건드리게 만들면 설계가 틀린 것이다.

med 쪽이 안전한 이유: threshold, hazard ratio, effect size가 표와 caption에
텍스트로 있어서 number_pool 매칭률이 높다. ML 논문은 figure가 예쁘지만
수치가 그림 안에만 있어 `qualitative`로 강등될 확률이 크다.

## 하지 않을 것

- 모든 figure 자동 vectorization
- 범용 코드 실행
- 논문 전체를 웹사이트로 변환
- multi-user, 음성, 논문 간 메타분석
