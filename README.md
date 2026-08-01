# Paper Playground

직접 입력한 claim 또는 PDF에서 얻은 중심 thesis와 하위 claim graph를 만들고,
root→pedagogic frontier 경로를 검증·설명한 뒤 frontier에 assumption switchboard를
만드는 단일 입력 데모다.
Critic이 잘못된 참조를 발견하면 인터랙션 대신 읽기 전용 evidence/assumption map을 낸다.

## Offline 실행

Python 3.10 기준이다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playground.run
```

Windows PowerShell에서는 활성화 명령만 다음과 같이 바꾼다.

```powershell
.\.venv\Scripts\Activate.ps1
```

다른 PDF는 첫 입력에서 지정한다.

```bash
python -m playground.run --pdf path/to/paper.pdf
```

PDF 없이 claim을 직접 입력할 수도 있다. 이 경우 `input_claim` span에만 묶이며
paper 근거로 가장하지 않고 외부 검증·교육적 가정 경로로 처리한다.

```bash
python -m playground.run --claim "The proposed method improves calibration under distribution shift."
```

기존 최소 회귀 검사를 실행할 때만 개발 의존성을 설치한다.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

기본 실행은 `MockLLM`과 `MockSearch`를 사용한다. 실제 API는 명시적으로 `--live`를
붙인 경우에만 호출한다. `.env`에 `OPENAI_API_KEY`, `LINER_API_KEY`를 넣고 키를
코드나 저장소에 commit하지 않는다. 기본 ML fixture는
`On Calibration of Modern Neural Networks` (`fixtures/guo17a.pdf`)다.

```bash
python -m playground.run --live --pdf fixtures/guo17a.pdf
python -m playground.run --live --claim "Temperature scaling improves calibration."
```

로컬 브라우저 E2E는 FastAPI 서버로 실행한다.

```bash
python -m playground.server
# http://127.0.0.1:8000 에서 claim/PDF 제출
```

renderer는 schema 1.0 fixture와 1.1 live payload, 정상 switchboard,
`UNSAFE_TO_VISUALIZE` 안전 map, refusal artifact를 모두 소비한다.

협업·브랜치·프론트 계약은 [COLLABORATION.md](COLLABORATION.md), 코어 불변식은
[CLAUDE.md](CLAUDE.md), 남은 리스크는 [OPEN_ISSUES.md](OPEN_ISSUES.md)를 따른다.
