# Paper Playground

논문에서 검증 가능한 claim 하나를 자동 선택하고, 근거·가정·외부 근거로
분해한 뒤 assumption switchboard를 만드는 단일 입력 데모다.

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

기존 최소 회귀 검사를 실행할 때만 개발 의존성을 설치한다.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

기본 실행은 `MockLLM`과 `MockSearch`를 사용한다. OpenAI Agents와 Liner의 live
실행은 아직 제공하지 않는다. 키를 코드에 넣거나 저장소에 commit하지 않는다.

정적 화면은 `frontend/index.html`을 직접 열 수 있다. 현재 화면 데이터는
DemoPayloadV1 offline fixture이며 live 파이프라인과 연결되어 있지 않다.

협업·브랜치·프론트 계약은 [COLLABORATION.md](COLLABORATION.md), 코어 불변식은
[CLAUDE.md](CLAUDE.md), 남은 리스크는 [OPEN_ISSUES.md](OPEN_ISSUES.md)를 따른다.
