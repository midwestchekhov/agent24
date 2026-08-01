# Paper Defense Simulator 열린 항목

## 현재 결정

- 제품은 교육용 explainer가 아니라 제출 전 defense simulator다.
- claim graph는 내부용이다.
- frontier는 하나만 선택한다.
- 가정 OFF는 단일 가정 영향표만 제공한다.
- 입력은 PDF 또는 원문 text다. claim-only는 거부한다.
- 실제 OpenAI·Liner API가 품질 acceptance 경로다.

## 구현 중

### 1. DefensePayloadV1

`defense/1.0` envelope와 `defense_report`/`partial_defense_report` renderer 계약을
backend에서 먼저 확정해야 한다.

### 2. Evidence 품질

Liner Scholar 결과는 검색 snippet/chunk 범위다. full-text verification이 없는
관계는 unresolved로 남기며, 검색 결과를 법적·학술적 확정 사실로 표시하지 않는다.

### 3. 점수 최적화

초기 frontier 점수는 고정 가중치로 시작한다. gold fixture와 live 실행 로그가
쌓인 뒤 component weight를 별도 최적화한다.

### 4. Gold rubric

Guo, sample clinical, GBM 세 편에 대해 허용 frontier·공격 유형·금지 과장·최소
근거를 의미 단위로 고정해야 한다.

## 의도적 보류

- 브라우저 renderer와 UX
- Liner Visualization API
- 특허 DB와 법률 판정
- full-text ingestion, URL ingestion, 영속 저장
- 여러 가정의 조합 counterfactual
