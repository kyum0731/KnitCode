# 순서대로 실행하기

Windows PowerShell 기준입니다. **정적 분석 → AI와 동적 실행의 상호 보완 → 프로젝트 이해 보고서** 순서로 진행합니다. 처음에는 추가 도구 없이 기본 흐름을 실행하세요.

## 1. 준비

README.md가 있는 `prototype_3` 폴더에서 PowerShell을 열고 실행합니다. CPython 3.13 이상이 필요합니다.

```powershell
python --version
$env:PYTHONPATH = "$PWD/src;$env:PYTHONPATH"
```

대상 프로젝트에 필요한 라이브러리는 실행할 Python에 설치되어 있어야 합니다. 아래 입력란에는 실제 프로젝트 폴더의 절대 경로를 따옴표 없이 입력하세요.

```powershell
$projectPath = Read-Host "분석할 프로젝트 디렉터리의 절대 경로"
$reportPath = "$PWD/reports/my-project"
```

## 2. 정적 분석과 구조 확인

```powershell
python -m knitcode_analyzer_v2.runtime "$projectPath" --discover --report-dir "$reportPath"
```

대상은 실행하지 않습니다. 터미널의 `후보·구조 보고서:` 뒤에 출력된 `report.html` 전체 경로만 다음 입력란에 붙여 넣으세요.

```powershell
$staticReport = Read-Host "정적 report.html의 전체 경로"
Start-Process -FilePath "$staticReport"
```

프로젝트 구조와 실행 후보를 확인합니다. 다음 자동 분석에서는 후보나 입력값을 직접 지정할 필요가 없습니다. 이미 기본 정적 분석 명령으로 만든 보고서가 있다면 해당 경로를 `$staticReport`에 지정해도 됩니다.

## 3. AI 연결 설정

아래 **한 가지 방식만** 선택합니다.

### 로컬 Ollama

Ollama 서버가 실행 중이고 모델이 설치되어 있어야 합니다. 설치된 모델명을 입력합니다.

```powershell
$aiModel = Read-Host "Ollama에 설치한 모델명"
$aiOptions = @("--ai", "ollama", "--model", "$aiModel")
```

기본 서버는 `http://127.0.0.1:11434`입니다.

### 외부 호환 API

예시 값을 실제 서비스 설정으로 바꾸세요. 실제 키를 파일에 저장하지 마세요.

```powershell
$env:KNITCODE_AI_BASE_URL = "https://your-api-server.example/v1"
$env:KNITCODE_AI_MODEL = "your-model-name"
$env:KNITCODE_AI_API_KEY = "your-api-key"
$aiOptions = @("--ai", "api")
```

## 4. AI·동적 통합 분석

```powershell
python -m knitcode_analyzer_v2.study "$projectPath" --static-report "$staticReport" --report-dir "$reportPath" --max-attempts 3 --timeout 30 @aiOptions
```

다음 작업이 자동으로 진행됩니다.

1. 정적 보고서와 현재 소스의 일치를 확인하고 정적 결과를 저장합니다.
2. AI가 실행 후보와 작은 합성 입력을 선택합니다.
3. 새 프로젝트 복사본에서 CLI 인자·함수 인자·표준입력을 전달하고 실행합니다.
4. 실패나 관측 결과를 AI에 돌려주어 다음 입력을 조정합니다. 최대 3회이며 같은 계획은 반복 실행하지 않습니다.
5. 프로젝트 목적, 디렉터리·파일 역할과 연결, 기능 흐름, 읽기 순서를 설명하는 보고서를 생성합니다.

`input()`에는 AI가 미리 생성한 텍스트를 공급합니다. 실시간 대화는 아니며 부족한 입력은 다음 시도에서 보완합니다. 필요한 JSON·CSV·TXT 예제는 복사본 안에 생성합니다. 실제 인증정보·서비스·복잡한 객체가 필요하면 자동 해결하지 못할 수 있으며 이유를 보고서에 남깁니다.

복사본에서 실행해도 대상의 네트워크 요청·절대 경로 접근 등은 실제 발생할 수 있습니다. 테스트용 프로젝트·환경에서 실행하세요. AI에는 구조·입력 구문·README 일부·생성한 입력·실행 집계가 전달됩니다.

## 5. 통합 보고서 열기

터미널의 `프로젝트 이해 보고서:`에 출력된 경로를 입력하세요.

```powershell
$studyReport = Read-Host "통합 report.html의 전체 경로"
Start-Process -FilePath "$studyReport"
```

결과는 `reports/my-project/studies/<ID>/`에 저장됩니다. 다음 순서로 읽으세요.

1. **어떤 프로젝트인가**: 목적과 추정 근거.
2. **디렉터리별 역할과 연결**: 어떤 폴더가 무엇을 담당하는지.
3. **핵심 파일·기능 흐름·읽기 순서**: 어디서 시작해 무엇을 읽을지.
4. **AI 입력 계획과 실제 실행**: 시도별 입력·실패·관측 결과와 개별 동적 HTML.
5. **전체 구조 그래프**: 디렉터리·파일·함수 수준에서 관계 탐색.

최상위 그래프의 동적 관계는 마지막 기본 실행입니다. 다른 시도의 흐름은 해당 동적 보고서 링크로 확인합니다. `partial`이라도 생성된 보고서는 읽을 수 있습니다. 미관측 코드를 미사용 코드로 해석하지 마세요.

## 6. 필요할 때 추가 도구 사용

DynaPyt는 세부 함수 진입·분기, Scalene은 성능 측정을 보완합니다. 설치와 추가 실행을 원할 때만 진행합니다.

```powershell
python -m pip install -e ".[dynapyt,scalene]"
python -m knitcode_analyzer_v2.study "$projectPath" --static-report "$staticReport" --report-dir "$reportPath" --max-attempts 3 --timeout 30 --supplement dynapyt --supplement scalene @aiOptions
```

이 명령은 **새 통합 분석을 시작**하므로 기본 동적 실행도 다시 수행합니다. 각 추가 도구도 별도 복사본에서 대상을 실행합니다. Scalene 메모리 시작 실패 시 새 복사본에서 CPU 전용으로 한 번 더 실행하고 부분 성공으로 표시합니다. 결과는 새 통합 보고서의 추가 도구 항목과 `supplements/`에 남습니다. 도구가 설치되지 않았거나 실패해도 기본 결과를 유지합니다.

## 상황별 처리

| 상황 | 처리 |
| --- | --- |
| 정적 스냅샷 불일치 | 소스·분석 옵션이 바뀌었는지 확인하고 2단계부터 다시 진행 |
| `src`만 분석 | 2·4·6단계에 모두 `--source-root src` 추가 |
| 다른 Python에 대상 의존성이 있음 | 4·6단계에 `--python "C:/path/to/python.exe"` 추가 |
| 너무 빨리 시간 초과 | 4·6단계의 `--timeout`을 증가. 자동 실행 최대 300초 |
| AI 연결 실패 | 주소·모델·키를 확인. 정적 결과는 저장되어 있으며 생성된 보고서에서 실패 이유 확인 |
| 합성 입력으로 실행 불가 | 시도별 계획·예외·환경 요구를 확인하고 아래 수동 실행 사용 |
| AI 설명만 다시 만들고 싶음 | 아래 `review` 사용. `study`를 다시 실행하면 동적 실행도 다시 수행됨 |

## 수동 입력 또는 실행 없이 재해석

수동 함수 입력 예시에서 파일·함수명·인자는 실제 값으로 바꿉니다.

```powershell
python -m knitcode_analyzer_v2.runtime "$projectPath" --entry "실제파일.py::실제함수" --call-args '[3]' --report-dir "$reportPath"
```

키보드 입력을 직접 정하려면 UTF-8 텍스트 파일에 입력할 내용을 줄 순서대로 저장하고 지정합니다.

```powershell
python -m knitcode_analyzer_v2.runtime "$projectPath" --script "실제파일.py" --stdin-file "./inputs.txt" --report-dir "$reportPath"
```

통합 결과의 **개별 동적 보고서 폴더**로 AI 설명만 다시 만들 수 있습니다. 예: `studies/<ID>/dynamic/runs/<실행ID>`.

```powershell
$runFolder = Read-Host "재해석할 개별 동적 보고서 폴더의 전체 경로"
python -m knitcode_analyzer_v2.review --static-report "$runFolder" --runtime-report "$runFolder" --report-dir "$reportPath" @aiOptions
```

이 명령은 해당 실행의 정적 스냅샷과 동적 결과를 함께 사용하며 대상을 재실행하지 않습니다. 결과는 `reports/my-project/ai/<ID>/report.html`에 생성됩니다.

