# 입력 시나리오 카탈로그 (80건)

파이프라인 입력 계약(`POST /api/runs`: `claim_text` / `source_text` / `pdf`,
CLI: `python -m playground.run --claim/--pdf/--text`) 기준의 테스트 시나리오.
카테고리당 20건. **URL 직접 입력은 현재 파이프라인에 없다** — D군은 URL이
다른 입력 필드로 들어왔을 때의 우아한 처리를 검증한다.

공통 판정 어휘:
- **완주** = artifact 또는 refused까지 추가 입력 없이 도달, traceback 없음
- **강등** = quantitative → qualitative 또는 safe map, decision 이벤트 필수
- **거부** = refused 또는 4xx, 사유가 이벤트/응답에 명시
- 모든 케이스 공통: raw 스트림에 tool_call/tool_result 쌍·종료 이벤트,
  status 채널 미혼입, key/.env 미노출

실행: mock은 `tests/run_smoke.ps1` 또는 서버 offline, live는 `--live`
(과금 주의 — live 표시된 것만 키 사용).

## A. PDF 입력 (20)

| ID | 입력 | 기대 | 확인 포인트 |
|---|---|---|---|
| A01 | `tests/inputs/01_normal_quantitative.pdf` | 완주(quantitative) | 패널 슬롯이 number_pool id에 묶임 |
| A02 | `02_qualitative_no_numbers.pdf` | 강등 또는 거부 | 강등 decision 이벤트 존재 |
| A03 | `03_no_verifiable_claim.pdf` (학회 안내문) | 거부 | 날짜·시간 숫자로 claim 조작하지 않음 |
| A04 | `04_niche_zero_search_hits.pdf` | 완주, external 0건 | 갈래별 0건이 명시적 이벤트 |
| A05 | `05_prompt_injection.pdf` | 완주, 지시 무시 | README 05 판별 기준(인용=통과, 준수=실패) |
| A06 | `06_unicode_korean.pdf` | 완주 | span offset 한글 깨짐 없음 |
| A07 | `07_scanned_no_text_layer.pdf` | 거부 | "no text layer" stage_error |
| A08 | `08_encrypted.pdf` | 거부 | **현재 traceback 크래시 — A 영역 미해결** |
| A09 | `09_empty.pdf` (0바이트) | 거부 | stage_error로 감쌈 |
| A10 | `10_not_a_pdf.pdf` | 거부 | 서버 경로는 422 (magic bytes 검사) |
| A11 | `11_truncated.pdf` | 거부 | **현재 부분 파싱으로 진행 — A 영역 미해결** |
| A12 | `12_blank_page.pdf` | 거부 | 억지 claim 생성 없음 |
| A13 | `fixtures/guo17a.pdf` (실제 ML 논문) | 완주 | 심사위원 임의 PDF 대역. live 권장 |
| A14 | `fixtures/Nature_2018_Lee_et_al...pdf` (의학) | 완주 | 도메인 하드코딩 없는지 |
| A15 | 25MiB 초과 PDF | 거부 | 서버 413, 업로드 전체 읽지 않음 |
| A16 | 확장자 `.txt`인 유효 PDF | 완주 | magic bytes 우선, 확장자 무시 |
| A17 | 2단 조판 논문 | 완주 | span 순서가 읽기 순서 근사 |
| A18 | 수식 위주 논문 | 완주 또는 강등 | 수식을 숫자 근거로 오인하지 않음 |
| A19 | PDF + `source_title` 동시 | 완주 | title 필드가 payload run에 반영 |
| A20 | PDF + `claim_text` 동시 | 완주 | claim이 root 유지, PDF는 context만 |

## B. 텍스트 입력 — `source_text` (20)

| ID | 입력 | 기대 | 확인 포인트 |
|---|---|---|---|
| B01 | 정상 abstract (수치 포함, 영어) | 완주(quantitative) | input_kind="text" |
| B02 | 정상 abstract (한국어) | 완주 | 한글 span 정상 |
| B03 | 수치 없는 서술 문단 | 강등 | qualitative 경로 |
| B04 | 한 문장짜리 텍스트 | 완주 또는 거부 | 억지 graph 생성 없음 |
| B05 | 빈 문자열 `""` | 거부 | 서버 422 (strip 후 None) |
| B06 | 공백·개행만 | 거부 | B05와 동일 처리 |
| B07 | 100KB 초과 장문 | 완주 | 예산 내 종료 또는 명시적 초과 이벤트 |
| B08 | HTML 태그 섞인 텍스트 | 완주 | 태그를 본문으로 오인하지 않음 |
| B09 | 마크다운 표 포함 | 완주 | 표 수치가 number_pool에 등록 |
| B10 | 코드 블록 포함 | 완주 | 코드 내 숫자를 근거로 오인하지 않음 |
| B11 | 인젝션 문장 텍스트 직접 입력 | 완주, 지시 무시 | A05와 동일 판별 기준 |
| B12 | 이모지·특수문자 다수 | 완주 | JSON 직렬화 깨짐 없음 |
| B13 | 같은 문단 3회 반복 | 완주 | 중복 span/claim 정리 여부 |
| B14 | 서로 모순되는 두 문단 | 완주 | 한쪽을 조용히 버리지 않고 이벤트 |
| B15 | 뉴스 기사 (논문 아님) | 완주 또는 거부 | 검증 가능 claim 없으면 거부 |
| B16 | 소설 문단 | 거부 | 반증 불가 텍스트 필터 |
| B17 | 숫자만 나열 ("1 2 3 4 5") | 거부 | 숫자 밀집 ≠ claim |
| B18 | RTL 문자(아랍어) 포함 | 완주 | 인코딩 오류 없음 |
| B19 | `source_text` + `source_title` | 완주 | title 반영 |
| B20 | `source_text` + `pdf` 동시 | 완주 | input_kind="pdf+text", 충돌 없이 병합 |

## C. Claim 입력 — `claim_text` (20)

| ID | 입력 | 기대 | 확인 포인트 |
|---|---|---|---|
| C01 | 정량 claim ("X가 recall을 0.61→0.74로 올린다") | 완주 | claim이 c1 root, mapper 미호출 |
| C02 | 정성 claim ("X는 y보다 해석이 쉽다") | 강등 경로 완주 | 숫자 생성 금지 |
| C03 | 반증 불가 claim ("X는 아름답다") | 거부 | 거부 사유 이벤트 |
| C04 | 의견형 ("나는 X가 좋다") | 거부 | C03과 동일 |
| C05 | 질문형 ("X가 y를 개선하는가?") | 완주 또는 거부 | 질문→claim 변환 여부가 명시적 |
| C06 | 한국어 claim | 완주 | 검색 쿼리 언어 처리 |
| C07 | 널리 반박된 claim ("지구는 평평하다") | 완주 | external contradict 검출(live), status 강제 없음 |
| C08 | 니치 claim (검색 0건 유도) | 완주 | 갈래별 0건 명시 이벤트 |
| C09 | 두 주장이 합쳐진 복문 claim | 완주 | 분해 또는 단일 root 처리 명시 |
| C10 | 수식 포함 claim ("O(n²)→O(n log n)") | 완주 | 수식 파싱 오류 없음 |
| C11 | 매우 긴 claim (1000자+) | 완주 | 예산 내 처리 |
| C12 | 빈 claim | 거부 | 서버 422 |
| C13 | 인젝션 문장을 claim으로 | 완주, 지시 무시 | 검색 쿼리로의 지시문 유출 여부 기록(알려진 A 이슈) |
| C14 | 이모지만 ("🚀🔥") | 거부 | 우아한 거부 |
| C15 | claim + `source_text` 동시 | 완주 | claim이 root 유지 |
| C16 | claim + 무관한 PDF 동시 | 완주 | PDF가 root를 바꾸지 않음 |
| C17 | 자기지시 claim ("이 문장은 거짓이다") | 거부 또는 완주 | 무한 루프 없음 |
| C18 | 의학적 위험 claim ("백신이 자폐 유발") | 완주 | contradict evidence 표시, 단정 없음 |
| C19 | 숫자 틀린 유명 claim ("빛은 3km/s") | 완주(live) | external이 반박 근거 수집 |
| C20 | 동일 claim 대소문자만 다르게 2회 | 완주 ×2 | 결과 일관성(비결정성 허용 범위 기록) |

## D. URL 입력·재질문·재실행 (20)

URL 직접 입력 필드는 **현재 미구현**. D01~D08은 "URL이 들어와도 죽지 않고
정직하게 처리"를 검증한다.

| ID | 입력 | 기대 | 확인 포인트 |
|---|---|---|---|
| D01 | `claim_text`에 URL만 ("https://arxiv.org/abs/...") | 거부 | URL을 fetch하려 들지 않음, 사유 명시 |
| D02 | `source_text`에 URL만 | 거부 | D01과 동일 |
| D03 | claim 문장 안에 URL 포함 | 완주 | URL을 span 텍스트로만 취급 |
| D04 | DOI 문자열 입력 | 거부 또는 완주 | fetch 시도 없음 |
| D05 | `file://` 경로 URL | 거부 | 로컬 파일 접근 시도 없음(보안) |
| D06 | `javascript:` 스킴 | 거부 | 프론트에서 링크로 렌더링되지 않음 |
| D07 | external evidence의 URL 클릭 | 새 탭 열림 | rel=noreferrer 유지 |
| D08 | 조작된 evidence URL (Liner 응답 위조 가정) | 표시만 | 자동 fetch 없음 확인 |
| D09 | 같은 claim 연속 2회 실행 (앞 run 종료 후) | 완주 ×2 | 두 번째가 첫 결과를 오염시키지 않음 |
| D10 | run 진행 중 두 번째 POST | 409 RUN_IN_PROGRESS | 첫 run 무손상 |
| D11 | run 완료 후 payload 재요청 | 200 동일 payload | 멱등성 |
| D12 | 존재하지 않는 run_id로 events/payload | 404 | 명시적 오류 |
| D13 | run 진행 중 payload 요청 | 409 "still active" | 완료 전 부분 payload 미노출 |
| D14 | SSE 수신 중 브라우저 새로고침 | 이벤트 복원 | dedupe로 중복 없음 (사람 확인) |
| D15 | SSE 수신 중 서버 kill | "연결 끊김" 표시 | 화면 멈춤 없음 (사람 확인) |
| D16 | 결과 화면에서 claim 수정 후 재실행 (재질문) | 새 run 생성 | 이전 이벤트 스트림 초기화 확인 |
| D17 | refused 결과 후 더 구체적 claim으로 재질문 | 완주 | 거부→성공 전이가 데모 시나리오로 동작 |
| D18 | 21개 run 연속 실행 | 완주 | RunStore max_completed=20 초과 시 오래된 record 정리 |
| D19 | `--live` 키 없음 (.env 제거 상태) | 기동 실패 | "live mode requires API keys" 명시 |
| D20 | `--live` 정상 키 (과금, 1회만) | 완주 | OpenAI/Liner 실호출이 raw tool_call/result로 기록, 키 미노출 |

## 실행 우선순위

1. mock 전수: A01~A12, B01~B20, C01~C06, C08~C17, D09~D13, D16~D18
2. 사람 확인: D14, D15, D07 (+ toggle 0회 Network 탭)
3. live 최소셋 (과금): A13, C07, C19, D19, D20 — 각 1회만
