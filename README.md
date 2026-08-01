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

기본 실행은 `MockLLM`과 `MockSearch`를 사용한다. OpenAI Agents와 Liner의 live
실행은 아직 제공하지 않는다. 키를 코드에 넣거나 저장소에 commit하지 않는다.

정적 화면은 `frontend/index.html`을 직접 열 수 있다. 현재 화면 데이터는
DemoPayloadV1 offline fixture이며 live 파이프라인과 연결되어 있지 않다.
renderer는 정상 switchboard와 `UNSAFE_TO_VISUALIZE` 안전 map을 모두 소비한다.

협업·브랜치·프론트 계약은 [COLLABORATION.md](COLLABORATION.md), 코어 불변식은
[CLAUDE.md](CLAUDE.md), 남은 리스크는 [OPEN_ISSUES.md](OPEN_ISSUES.md)를 따른다.
