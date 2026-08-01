# 평가 계획

## Gold fixtures

### Guo 2017

핵심 후보는 현대 신경망의 calibration과 temperature scaling이다. 필수 공격
표면은 ECE binning, dataset/architecture 범위, calibration과 practical utility의
간극이다. 방어 범위는 논문이 실제 평가한 dataset·architecture 조건까지다.

### Synthetic clinical model

`fixtures/sample.pdf`의 AUC 0.87 주장을 사용한다. 필수 공격 표면은 comparison
fairness, leakage, AUROC와 clinical utility의 간극, representativeness다.
방어 범위는 동일 기관 held-out cohort의 discrimination 결과까지다.

### GBM origin paper

표본 오염·저수준 mutation 검출·인과 해석·mouse-to-human 외삽을 공격 표면으로
삼는다. 방어 범위는 관찰 cohort와 제시된 모델 실험까지다.

## 공통 pass 조건

- frontier 하나와 원문 span 연결
- 3~5개 meaningful assumption
- 질문 최대 3개
- 외부 relation마다 실제 chunk
- 방어 문장에 source/evidence/condition attribution
- 원 주장보다 넓은 scope 없음
- 검색 0건을 긍정 증거로 해석하지 않음
- 120초 이내 terminal payload

정확한 문장 일치 대신 semantic rubric을 사용한다. gold는 허용 개념, 필수 공격
유형, 금지 과장, 최소 근거, 기대 scope를 기록한다.

## 실제 API acceptance 실행

결정론적 pytest는 provider를 호출하지 않는다. 실제 과금 acceptance는 명시적으로
`--live`를 붙인 전용 harness에서만 실행한다.

```bash
python -m playground.defense_eval --live --out-dir /tmp/paper-defense-gold
```

특정 fixture만 반복할 때는 `--fixture`를 여러 번 지정한다.

```bash
python -m playground.defense_eval --live \
  --fixture fixtures/guo17a.pdf \
  --out-dir /tmp/guo-defense-gold
```

각 결과 JSON에는 payload 경로, score, contract checks, elapsed time이 남는다.
실패 시 provider 원문이나 예외 세부값을 출력하지 않고 예외 타입만 기록한다.

## 경계 테스트

- references/methods/acknowledgments claim 제외
- invalid/missing span 거부
- 정의형 assumption 거부
- chunk 없는 relation을 unresolved로 강등
- malformed/encrypted/scanned PDF
- prompt injection
- claim-only 입력 422
- raw/status SSE 분리
- API key redaction
