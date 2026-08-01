<#
  AGENT:24 / Paper Playground - 입력 다양성 스모크 러너 (PowerShell 판)

  bash 가 없는 Windows 환경용. tests/run_smoke.sh 와 동일하게 동작한다.

  저장소 루트에서 실행:
      powershell -ExecutionPolicy Bypass -File tests\run_smoke.ps1

  환경변수로 조정:
      $env:PY="python"; $env:RUN="-m playground.run"; $env:TIMEOUT="180"

  각 입력을 CLI 에 넣고 종료코드와 출력을 tests\out\ 에 남긴다.
  "성공"의 정의는 케이스마다 다르다. tests\README.md 의 기대 동작 표와
  대조할 것. 이 스크립트는 판정하지 않고 관측만 한다.
#>

$ErrorActionPreference = "Continue"

$IN  = "tests\inputs"
$OUT = "tests\out"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

$PY      = if ($env:PY)      { $env:PY }      else { "python" }
$RUN     = if ($env:RUN)     { $env:RUN }     else { "-m playground.run" }
$TIMEOUT = if ($env:TIMEOUT) { [int]$env:TIMEOUT } else { 180 }

"{0,-34} {1,-6} {2,-9} {3}" -f "INPUT","EXIT","SECS","LOG"
"-" * 78

function Run-One {
    param([string]$Label, [string[]]$ExtraArgs)

    $logf = Join-Path $OUT "$Label.log"
    $argList = @()
    $argList += ($RUN -split '\s+')
    if ($ExtraArgs) { $argList += $ExtraArgs }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $p = Start-Process -FilePath $PY -ArgumentList $argList `
             -RedirectStandardOutput $logf -RedirectStandardError "$logf.err" `
             -NoNewWindow -PassThru
        if (-not $p.WaitForExit($TIMEOUT * 1000)) {
            $p.Kill()
            $exit = "TIMEOUT"
        } else {
            $exit = $p.ExitCode
        }
    } catch {
        $_ | Out-File -FilePath $logf -Encoding utf8
        $exit = "ERR"
    }
    $sw.Stop()

    # stderr 를 메인 로그에 합친다
    if (Test-Path "$logf.err") {
        Get-Content "$logf.err" | Add-Content $logf
        Remove-Item "$logf.err" -Force
    }

    $secs = [math]::Round($sw.Elapsed.TotalSeconds)
    "{0,-34} {1,-6} {2,-9} {3}" -f $Label, $exit, "${secs}s", $logf
}

# baseline: 인자 없이 기본 동작
Run-One -Label "00_baseline_default"

# 모든 픽스처 PDF
Get-ChildItem -Path $IN -Filter "*.pdf" | Sort-Object Name | ForEach-Object {
    $base = $_.BaseName
    Run-One -Label $base -ExtraArgs @("--pdf", $_.FullName)
}

# 존재하지 않는 경로
Run-One -Label "90_missing_path" -ExtraArgs @("--pdf", "$IN\does-not-exist.pdf")

# 디렉터리를 PDF 로 전달
Run-One -Label "91_directory_as_pdf" -ExtraArgs @("--pdf", $IN)

""
"=== 자동 관측: 금지 문자열 검사 ==="
$inj = Select-String -Path "$OUT\*.log" `
       -Pattern "verification bypassed|bypass|ignore (all )?prior instruction" `
       -CaseSensitive:$false -ErrorAction SilentlyContinue
if ($inj) {
    "!! FAIL: 인젝션 지시가 출력에 반영되었을 수 있음 - 아래 확인"
    $inj | ForEach-Object { "   $($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
} else {
    "OK: 인젝션 마커 문자열 없음"
}

""
"=== 자동 관측: secret 유출 검사 ==="
$sec = Select-String -Path "$OUT\*.log" `
       -Pattern "sk-[a-z0-9]{16,}|api[_-]?key\s*[:=]\s*\S{12,}" `
       -CaseSensitive:$false -ErrorAction SilentlyContinue
if ($sec) {
    "!! FAIL: 로그에 key 처럼 보이는 문자열이 있음"
    $sec | ForEach-Object { "   $($_.Path):$($_.LineNumber)" }
} else {
    "OK: key 패턴 없음"
}

""
"로그: $OUT\"
