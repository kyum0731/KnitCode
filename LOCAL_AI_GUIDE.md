# 무료 로컬 AI로 AI·동적 분석 실행하기

확인일: 2026-10-22. 이 저장소의 `study`, `runtime`, `ai.py` 구현을 기준으로 작성했습니다. Windows PowerShell용입니다. Python 3.13.3, Ollama 0.40.8과 설치된 qwen2.5-coder:7b를 확인했습니다. Ollama 초기화 로그에서는 RAM 약 31.5GiB와 CPU 추론을 확인했습니다. 작은 예제의 실제 AI·동적 통합 실행은 complete였습니다. 이 결과가 모든 프로젝트의 성공을 보장하지는 않습니다.

## 1. 모델을 고르는 기준

이 도구에서는 AI가 입력을 계획하고 실행 결과를 설명하며, Python 실행기가 실제 호출·예외·시간을 관측합니다. 모델만 설치해서 동적 분석이 되는 것은 아닙니다. `study` 명령이 둘을 연결합니다.

선정 기준은 무료 로컬 이용, 코드 이해·입력 생성 용도, Ollama 네이티브 JSON API 연결, 메모리 비용, 현재 구현과의 호환성입니다. 수정된 `study`는 JSON Schema를 적용하고, 계획 최대 1024·최종 설명 최대 1536토큰을 사용합니다. thinking 설정은 별도로 보내지 않습니다. 최신 종합 순위가 아니라 현재 프로젝트에 연결하기 쉬운 후보를 선정했습니다.

| 모델 | 정확한 Ollama 이름 | 모델 다운로드 | 권장 시작 환경(추정) | 선택 이유 |
| --- | --- | --- | --- | --- |
| Qwen2.5-Coder 7B | `qwen2.5-coder:7b` | 약 4.7GB | RAM 16GB 이상, 지원 GPU VRAM 8GB 이상이면 유리 | 첫 설치·소규모 프로젝트용 기준 모델 |
| Qwen2.5-Coder 14B | `qwen2.5-coder:14b` | 약 9GB | RAM 32GB, 지원 GPU VRAM 16GB 정도 권장 | 같은 계열에서 모델 크기 증가의 효과 비교 |
| Qwen3-Coder 30B-A3B | `qwen3-coder:30b` | 약 19GB | RAM 32~64GB, 지원 GPU VRAM 24GB 이상 권장 | 코드 에이전트 작업에 특화된 상위 후보 |

용량과 Apache-2.0 라이선스는 [7B 모델 페이지](https://ollama.com/library/qwen2.5-coder:7b), [14B 모델 페이지](https://ollama.com/library/qwen2.5-coder:14b), [30B 모델 페이지](https://ollama.com/library/qwen3-coder:30b)에서 확인했습니다. 표의 RAM·VRAM은 아래 문맥 크기로 시작하기 위한 실용적 추정이며 최소 사양이나 속도 보장이 아닙니다. 다운로드 크기 외에 실행 버퍼·문맥 캐시·OS 메모리가 필요합니다. 세 모델을 동시에 설치하거나 실행할 필요는 없습니다.

Qwen2.5-Coder는 코드 생성·수정·추론 평가를 공개한 계열입니다. 32B의 공개 점수를 7B·14B의 점수로 사용하면 안 됩니다. 14B가 이 프로젝트에서 항상 더 좋다는 보장도 없습니다. [Qwen 공식 발표](https://qwenlm.github.io/blog/qwen2.5-coder-family/)

Qwen3-Coder 30B는 총 약 30B 중 토큰당 약 3.3B를 활성화하는 MoE 모델입니다. 따라서 3B 모델만큼의 메모리로 실행된다는 뜻은 아닙니다. 공식 모델 카드에서 non-thinking 모델임을 확인할 수 있습니다. [공식 모델 카드](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)

Ollama 본체는 MIT 라이선스이며 위 모델을 PC에서 실행할 때 구독·토큰 요금·API 키가 필요하지 않습니다. 전기·저장공간·인터넷 비용은 별도입니다. [Ollama 라이선스](https://github.com/ollama/ollama/blob/main/LICENSE)

사양을 모르므로 먼저 7B를 기준으로 안내합니다. `Ctrl+Shift+Esc` → 성능 → 메모리에서 RAM을 확인하세요. GPU 항목의 **전용 GPU 메모리**가 VRAM이며 공유 메모리와 다릅니다. RAM 8GB 이하라면 아래 7B 설정이 원활하게 동작한다고 보기 어렵습니다. GPU가 없어도 CPU로 시도할 수 있지만 느려서 AI 제한 시간에 걸릴 수 있습니다.

## 2. Ollama 설치

1. [Ollama Windows 다운로드](https://ollama.com/download/windows)에서 Windows 설치 파일을 내려받아 실행합니다. 페이지의 수동 다운로드를 이용해도 됩니다.
2. 설치 후 PowerShell 창을 새로 엽니다.
3. 다음 명령을 실행합니다.

```powershell
ollama --version
```

버전이 나오면 설치 확인 완료입니다. 명령을 찾지 못하면 터미널 앱 전체를 종료한 뒤 새로 여세요. Ollama는 Windows에서 직접 실행되므로 이 기본 절차에는 Docker·WSL·PyTorch 설치가 필요하지 않습니다. Windows 10 22H2 이상을 지원합니다. 설치 파일 외에 모델 저장공간도 확보하세요. [공식 Windows 안내](https://docs.ollama.com/windows)

## 3. 로컬 전용 서버 실행

명확한 설정 적용을 위해 이 안내에서는 서버용 PowerShell과 분석용 PowerShell을 나눕니다.

1. 작업 표시줄 오른쪽 숨겨진 아이콘에서 Ollama를 찾아 **Quit/종료**합니다. 창만 닫으면 서버가 남을 수 있습니다.
2. **서버용 PowerShell 창 A**를 열고 다음을 실행합니다.

```powershell
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_HOST = "127.0.0.1:11434"
ollama serve
```

창 A는 분석이 끝날 때까지 켜 두세요. 환경 변수는 이 창에서 시작하는 서버에 적용됩니다. 다음번에 서버를 시작할 때도 세 줄을 실행하세요. 클라우드 추론·웹 검색 비활성화 설정은 [Ollama 공식 소스](https://github.com/ollama/ollama/blob/main/envconfig/config.go)에 정의되어 있습니다. 모델 다운로드에는 인터넷을 사용합니다.

포트가 이미 사용 중이라는 오류가 나면 기존 Ollama 서버를 종료하고 다시 실행하세요. 이미 실행 중인 서버에는 위 환경 변수가 소급 적용되지 않습니다.

3. **분석용 PowerShell 창 B**를 새로 열고 확인합니다.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version"
```

버전이 표시되면 서버 연결 성공입니다. 이후 모든 명령은 특별한 안내가 없으면 창 B에서 실행합니다. API 주소에는 `/v1`을 붙이지 않습니다.

## 4. 분석기 준비

```powershell
Set-Location -LiteralPath "knitCode_Project 절대주소"
python --version
$env:PYTHONPATH = "$PWD/src;$env:PYTHONPATH"
python -m knitcode_analyzer_v2.study --help
```

Python 3.13 이상과 도움말이 출력되면 됩니다. 현재 기본 분석기는 표준 라이브러리만 사용하므로 별도 pip 설치 없이 가능합니다. 새 창 B를 열 때는 이 준비를 다시 실행하세요.

## 5. 사용할 모델 하나 선택

아래 A/B/C 중 **하나만** 실행한 뒤 6단계로 갑니다. 처음에는 A를 권장합니다. 모델을 바꿀 때는 기존 모델을 `ollama stop 기존모델명`으로 내리면 메모리를 확보할 수 있습니다.

### A. Qwen2.5-Coder 7B: 첫 실행

```powershell
ollama pull qwen2.5-coder:7b
$aiModel = "qwen2.5-coder:7b"
$ctx = 8192
$modelLabel = "qwen25-coder-7b"
```

작은 프로젝트로 연결을 확인합니다. 실제 프로젝트의 문맥이 길고 메모리 여유가 있으면 `$ctx = 16384`로 올리세요. 문맥을 작게 하면 메모리는 절약되지만 입력 근거가 잘릴 수 있습니다.

### B. Qwen2.5-Coder 14B: 크기에 따른 품질 비교

```powershell
ollama pull qwen2.5-coder:14b
$aiModel = "qwen2.5-coder:14b"
$ctx = 16384
$modelLabel = "qwen25-coder-14b"
```

7B보다 메모리·처리 비용이 높습니다. GPU에 전부 들어가지 않아 CPU와 나누어 실행하면 속도가 크게 느려질 수 있습니다.

### C. Qwen3-Coder 30B: 고사양 환경

```powershell
ollama pull qwen3-coder:30b
$aiModel = "qwen3-coder:30b"
$ctx = 16384
$modelLabel = "qwen3-coder-30b"
```

사양 확인 전 첫 모델로는 권하지 않습니다. 지원 최대 문맥이 크더라도 처음부터 256K로 실행하지 마세요. 현재 분석기의 `--num-ctx` 허용 상한도 131072입니다. 긴 문맥은 메모리를 더 사용합니다.

선택 후 공통 확인:

```powershell
ollama list
ollama show $aiModel
```

`list`에 선택한 모델이 보여야 합니다. `show`의 정보와 `list`의 모델 ID를 기록하면 비교 실험을 재현하는 데 도움이 됩니다.

## 6. JSON 응답 연결 시험

현재 분석기가 사용하는 것과 같은 `/api/chat`, JSON 출력 방식으로 간단한 요청을 보냅니다. 이 시험은 연결·JSON 출력 확인이며 분석기 전체 응답 계약의 통과를 보장하지 않습니다. API 필드는 [Ollama Chat API](https://docs.ollama.com/api/chat)를 따릅니다.

```powershell
$body = @{
    model = $aiModel
    messages = @(
        @{ role = "user"; content = 'Return only this JSON object: {"ok":true}' }
    )
    stream = $false
    format = "json"
    options = @{ num_ctx = $ctx; num_predict = 128; temperature = 0 }
} | ConvertTo-Json -Depth 6
$reply = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/chat" -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 600
$reply.message.content
$reply.message.content | ConvertFrom-Json
$reply.done_reason
ollama ps
```

`ok`가 `True`, 종료 이유가 `stop`이면 됩니다. `ollama ps`의 PROCESSOR에서 GPU/CPU 분배를 확인합니다. 처음에는 모델 적재 시간이 추가됩니다. 수정된 Ollama `study` 요청도 `temperature=0`을 사용합니다. [GPU 적재 확인 방법](https://docs.ollama.com/faq)

## 7. 작은 예제로 먼저 동적 분석 확인

다음 명령은 실행할 때마다 새로운 예제 폴더를 만들어 기존 파일을 덮어쓰지 않습니다. 외부 라이브러리 없이 정수를 입력받아 계산합니다.

```powershell
$demoName = "local-ai-demo-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + ([guid]::NewGuid().ToString("N").Substring(0, 6))
$projectPath = Join-Path $PWD "build/$demoName"
New-Item -ItemType Directory -Path $projectPath | Out-Null
@'
def classify(number: int) -> str:
    """Classify an integer as negative, zero, or positive."""
    if number < 0:
        return "negative"
    if number == 0:
        return "zero"
    return "positive"


def main():
    number = int(input("Enter an integer: "))
    print(classify(number))


if __name__ == "__main__":
    main()
'@ | Set-Content -LiteralPath (Join-Path $projectPath "main.py") -Encoding utf8
$reportPath = Join-Path $PWD "reports/local-ai-demo/$modelLabel"
```

실행 후보를 확인합니다. 이 단계에서는 대상을 실행하지 않습니다.

```powershell
python -m knitcode_analyzer_v2.runtime "$projectPath" --discover --report-dir "$reportPath"
```

이어서 AI와 동적 실행을 연결합니다.

```powershell
python -m knitcode_analyzer_v2.study "$projectPath" --ai ollama --base-url "http://127.0.0.1:11434" --model "$aiModel" --num-ctx $ctx --ai-timeout 600 --max-attempts 3 --timeout 30 --report-dir "$reportPath"
$studyExitCode = $LASTEXITCODE
Write-Output "study exit code: $studyExitCode"
```

`study`가 자체 정적 보고서를 만들므로 앞에서 생성한 보고서 경로를 복사할 필요는 없습니다. 기존 정적 결과를 재사용할 때만 `--static-report`를 지정하세요.

자동 처리 순서:

1. 정적 구조·실행 후보·입력 구문을 수집합니다.
2. AI가 후보와 함수 인자·CLI 인자·표준입력 등의 실행 계획을 만듭니다.
3. 새 복사본에서 실행하고 호출·예외·시간을 기록합니다.
4. 결과를 AI에 전달해 다음 계획을 요청합니다. 실행은 최대 3회이며 AI가 중단할 수 있습니다.
5. AI가 한국어 프로젝트 이해 보고서를 작성합니다.

AI는 정적으로 발견한 허용 후보 중에서 실행 대상을 선택합니다. 함수 후보가 제공되는 프로젝트에서는 함수를 직접 선택할 수도 있습니다. 세 번 시도해도 세 분기를 모두 관측한다고 보장하지 않습니다.

## 8. 결과 열기

터미널에 출력된 `프로젝트 이해 보고서:` 뒤의 전체 경로를 복사합니다. 다음 입력란에는 경로만, 따옴표 없이 붙여 넣으세요.

```powershell
$studyHtml = Read-Host "통합 report.html의 전체 경로"
Start-Process -FilePath "$studyHtml"
$studyDir = Split-Path -Parent $studyHtml
$study = Get-Content -LiteralPath (Join-Path $studyDir "study.json") -Raw -Encoding utf8 | ConvertFrom-Json
$study | Select-Object status, observed_functions, total_functions
$study.attempts | ConvertTo-Json -Depth 8
```

결과 위치는 `reports/.../studies/<ID>/report.html`입니다. 종료 코드 0은 완료, 1은 부분 완료, 2는 입력·설정 오류, 130은 취소입니다. 부분 완료에서도 보고서를 열어 실패 이유를 확인할 수 있습니다.

| 파일 | 확인할 내용 |
| --- | --- |
| `report.html` | 프로젝트 목적·파일 역할·읽기 순서·실행 링크 |
| `plan-N.json` | 성공적으로 받은 AI 입력 계획과 근거 |
| `study.json` | 계획 실패·중단 이유, 시도별 실행·추적 상태 |
| `dynamic/runs/<ID>/report.html` | 실제 호출·예외·관측 범위 |
| `guide.json` | 최종 AI 설명의 성공 여부와 내용 |

HTML이 존재하는 것만으로 AI나 동적 분석이 성공한 것은 아닙니다. `attempts`에 실제 실행이 있고, 실행 상태·추적 상태·AI 설명 상태가 적절한지 확인하세요. `observed_functions / total_functions`는 함수 관측 비율이며 분기 커버리지나 정확도는 아닙니다.

## 9. 실제 프로젝트 분석

분석 대상은 Python 프로젝트입니다. 이 분석기 폴더가 아니라 **이해하려는 프로젝트의 폴더**를 입력하세요.

```powershell
$projectPath = Read-Host "분석할 Python 프로젝트의 절대 경로(따옴표 제외)"
$projectPath = (Resolve-Path -LiteralPath $projectPath).Path
$reportPath = Join-Path $PWD "reports/local-ai-project/$modelLabel"
python -m knitcode_analyzer_v2.runtime "$projectPath" --discover --report-dir "$reportPath"
python -m knitcode_analyzer_v2.study "$projectPath" --ai ollama --base-url "http://127.0.0.1:11434" --model "$aiModel" --num-ctx $ctx --ai-timeout 600 --max-attempts 3 --timeout 30 --report-dir "$reportPath"
```

라이브러리가 필요한 대상은 먼저 그 프로젝트의 설치 안내에 따라 의존성을 준비하세요. 별도 Python 3.13+ 환경을 사용한다면 아래처럼 대상 실행 인터프리터를 지정합니다.

```powershell
$targetPython = Read-Host "대상 의존성이 설치된 Python 3.13+ python.exe의 전체 경로"
& $targetPython --version
python -m knitcode_analyzer_v2.study "$projectPath" --python "$targetPython" --ai ollama --model "$aiModel" --num-ctx $ctx --ai-timeout 600 --max-attempts 3 --timeout 30 --report-dir "$reportPath"
```

`--ai-timeout 600`은 AI 요청의 소켓 대기 제한이고 `--timeout 30`은 각 대상 실행 제한입니다. 전체 작업이 30초나 600초 안에 끝난다는 의미가 아닙니다. 계획 요청 최대 3회에 최종 설명 요청이 추가됩니다. CPU에서는 매우 오래 걸릴 수 있습니다.

이 도구의 복사본은 보안 샌드박스가 아닙니다. 대상 프로그램의 파일 변경·외부 서비스 호출은 실제로 발생할 수 있으므로 신뢰하는 코드와 테스트 데이터를 사용하세요. 현재 기본 추적은 최초 스레드의 동기 Python 중심이며 비동기·다른 스레드·자식 프로세스 내부 전체를 수집하지 않습니다. 이 결과만으로 취약점 부재나 모든 실행 경로를 증명할 수 없습니다.

## 10. 선택 사항: DynaPyt·Scalene

기본 동적 분석에는 두 도구가 필요하지 않습니다. 기본 실행에 성공한 다음 추가 관측이 필요할 때 사용하세요.

```powershell
python -m pip install -e ".[dynapyt,scalene]"
python -m knitcode_analyzer_v2.study "$projectPath" --ai ollama --model "$aiModel" --num-ctx $ctx --ai-timeout 600 --max-attempts 3 --timeout 60 --supplement dynapyt --supplement scalene --report-dir "$reportPath"
```

`--python`을 사용하는 경우 도구는 그 인터프리터 환경에 설치해야 합니다. 저장소 루트에서 `& $targetPython -m pip install -e ".[dynapyt,scalene]"`로 설치하고 분석 명령에도 `--python "$targetPython"`을 추가하세요.

DynaPyt는 함수 진입·if 조건 관측을, Scalene은 CPU·메모리 프로파일을 보충합니다. 두 도구는 별도 복사본에서 대상을 다시 실행합니다. 저장소의 [검증 기록](docs/validation.md)에 따르면 이 Windows 환경에서 Scalene 2.3.0의 메모리 실행은 실패했고 CPU 전용 보완 실행은 성공했습니다. 메모리 결과까지 항상 생성된다고 기대하면 안 됩니다.

## 11. 자주 만나는 문제

| 증상 | 대응 |
| --- | --- |
| `ollama` 명령 없음 | 설치 확인 후 터미널 전체를 다시 열기 |
| 11434 연결 거부 | 창 A의 서버 확인, `/api/version` 재확인 |
| 모델을 찾지 못함 / HTTP 404 | `ollama list`의 정확한 이름과 `--model` 일치 확인, 해당 모델 pull |
| 메모리 부족·극심한 지연 | 다른 모델 stop, 7B 사용, 작은 대상으로 시작, 문맥 16384→8192 축소 검토 |
| AI 응답 시간 오류 | `--ai-timeout 600`, `ollama ps`에서 CPU/GPU 배치 확인; 이미 600이면 작은 모델·대상 사용 |
| JSON 계약·근거 ID 오류 | `study.json`의 실패 확인, 작은 프로젝트로 재현, 모델 비교; 연결 성공과 계약 준수는 별개 |
| 출력 한도에서 중단 | study는 계획 1024·안내 1536토큰, 나머지 AI 요청은 4096토큰. timeout 증가는 해결책이 아님. diagnostics의 done_reason과 응답 발췌를 확인하고 범위·출력 예산 조정 |
| `ModuleNotFoundError` | 대상 실행 Python에 의존성 설치, 필요하면 `--python` 지정 |
| `No module named knitcode_analyzer_v2` | 저장소 루트에서 PYTHONPATH 준비 재실행 |
| 대상 실행 timeout | 서버형·대화형 프로그램인지 확인, 종료하는 작은 함수·테스트 사용; 필요한 경우 대상 timeout 조정 |
| `partial` 상태 | HTML과 study.json 확인; AI 실패, 실행 실패, 부분 추적, 선택 도구 실패 등을 구분 |

메모리 부족 때문에 문맥을 줄이면 모델이 필요한 근거를 잃을 수 있습니다. 같은 명령에 `--source-root src`를 추가하면 `src`만 분석하지만 대상 코드 구조에 따라 그 밖의 파일이 필요해 실행 실패가 생길 수도 있습니다.

## 12. 프로젝트에 맞는 최종 모델 선택

공개 코딩 벤치마크는 이 분석기의 입력 계획·한국어 설명 정확도를 직접 측정하지 않습니다. 같은 프로젝트·문맥 크기·실행 횟수·대상 timeout으로 비교하세요. 작은 대상에서 세 모델 모두 `$ctx = 8192`를 쓰면 비교 조건을 맞추기 쉽습니다. 모델을 바꾼 뒤에는 5단계의 모델 변수와 보고서 라벨도 변경하세요.

- JSON·근거 계약을 통과한 계획과 최종 설명의 비율
- 올바른 입력으로 실제 종료한 시도의 비율
- 관측된 함수·실행 경로가 실제로 유용했는지
- AI 설명의 사실성, 근거와 추론의 구분, 한국어 가독성
- 분석 소요 시간, 최대 RAM·VRAM 사용량

모델마다 가능하면 3회 이상 반복하고 최초 적재 시간과 이후 실행 시간을 구분하세요. Ollama study는 temperature를 0으로 지정하지만 완전한 재현성을 보장하지 않습니다. Ollama 버전·모델 ID·num_ctx·소스 버전·보고서 경로를 기록하세요.

최초 안내 작성 때는 AI 없는 예제만 검증했습니다. 이후 수정 검증에서 설치된 qwen2.5-coder:7b로 작은 예제의 계획·동적 실행·설명을 실제 수행해 complete와 산출물 무결성을 확인했습니다. Sublist3r도 사전에 확인한 오프라인 경로만 허용한 검증에서 complete, 함수 2/99 관측을 확인했습니다. 전체 네트워크 검색을 검증한 것은 아닙니다. 실패 시 study.json의 ai_settings, plan-N-failed.json 또는 guide.json의 diagnostics에서 오류 종류·요청 크기·모델 응답 발췌를 확인하세요. 상세 검증 결과는 docs/validation.md에 기록합니다.
