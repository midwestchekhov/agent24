# Paper Defense Simulator

논문 제출이나 발표 전에 심사자가 가장 먼저 공격할 만한 주장 하나를 찾고,
그 주장이 기대는 조건과 외부 문헌을 대조해 조건부 방어 범위를 만드는 backend다.

이 도구는 논문을 요약하거나 특허성을 판정하지 않는다. 결과는 다음 질문에
답한다.

> “이 주장을 그대로 말하면 어디를 공격받는가? 어떤 조건까지는 근거가 있고,
> 어디부터는 추가 실험이나 문헌이 필요한가?”

## 실행

실제 API를 사용하는 fast profile:

```bash
python -m playground.run \
  --live-fast \
  --pdf fixtures/guo17a.pdf \
  --artifact-out /tmp/guo-defense.json
```

Markdown/plain text 원문:

```bash
python -m playground.run \
  --live-fast \
  --source-text notes.md \
  --source-title "Draft paper"
```

로컬 API/SSE bridge:

```bash
python -m playground.server --live-fast
```

검색 재현율과 판정 품질을 우선하는 녹화·리뷰용 profile:

```bash
python -m playground.run --live-demo --pdf fixtures/guo17a.pdf \
  --artifact-out /tmp/guo-defense-demo.json
python -m playground.server --live-demo
```

`.env`에는 `OPENAI_API_KEY`, `LINER_API_KEY`를 두지만 키 값은 코드·로그·payload에
절대 포함하지 않는다. `--live-fast`는 120초·검색 1 round, `--live-demo`는
180초·검색 최대 2 rounds다. 두 profile 모두 frontier 하나만 분석한다.

## 처리 흐름

```text
PDF/text
 → section-aware parse
 → 핵심 주장과 공격 표면 구조화
 → 중요도·취약성 frontier 선택
 → 가정·예상 질문 생성
 → OpenAI가 검색 질문을 선택
 → Liner Scholar 문헌 검색
 → OpenAI가 chunk 근거를 supports/qualifies/challenges로 해석
 → 조건부 방어 범위와 단일 가정 영향 생성
 → deterministic + structured critic
```

최종 payload는 `schema_version=defense/1.0`이며 `defense_report` 또는 검증 범위가
줄어든 `partial_defense_report`다. 검색 결과 없음은 신규성이나 방어 가능성의
증거로 해석하지 않는다.

## 결과 예시의 의미

임상 예측모델의 성능 향상 주장을 선택했다면 결과는 다음을 분리한다.

- 공격 지점: 성능 향상이 모델 구조 때문인지, 튜닝·분할·leakage 때문인지
- 가정: 비교군 공정성, 데이터 무누출, AUROC와 임상 유용성의 연결, 대표성
- 근거: 유사 환경의 지지 문헌, 외부 검증에서 효과가 줄어드는 문헌, 방법론적 경고
- 방어 범위: “동일 기관 held-out cohort에서 비교군보다 높은 판별 성능”까지
- 제외 범위: 모든 임상 환경에서의 우월성

## 범위 제외

- 특허 신규성·진보성·침해·FTO 법률 판단
- 원문 figure pixel/OCR 분석
- 자동 peer-review 판정
- 다중 가정 조합 시뮬레이션
- 영속 저장·multi-user·배포

## 테스트

결정론적 경계 테스트:

```bash
python -m pytest -q
```

실제 API gold acceptance는 `fixtures/sample.pdf`, `fixtures/guo17a.pdf`,
`fixtures/Nature_2018_Lee_et_al._Human_glioblastoma_arises_from_subventricular_zone_cells.pdf`
를 대상으로 실행하며, 각 결과 JSON의 frontier·근거 chunk·방어 범위를 직접 검토한다.

## 방향 전환 기록

특허 청구항을 학술 선행문헌과 매핑하는 spike는 `feat/prior-art-liner-spike`에
보존되어 있다. Questel Qthena처럼 발명신고·검색·초안·FTO·office action까지
포괄하는 IP workflow가 이미 존재하므로, 현재 제품은 법률 workflow 경쟁이 아닌
논문 작성자·연구자의 제출 전 방어 리허설에 집중한다.
