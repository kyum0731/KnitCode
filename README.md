# KnitCode Analyzer v2

로컬 Python 프로젝트의 구조를 정적으로 분석하고, AI가 만든 입력으로 실행 흐름을 관측하여 **처음 보는 사람을 위한 프로젝트 이해 보고서**를 만드는 도구입니다.

- 순서대로 실행하려면 [RUN_GUIDE.md](RUN_GUIDE.md)를 참고하세요.
- 분석 입력·처리·산출물의 전체 흐름은 [ANALYSIS_FLOW.md](ANALYSIS_FLOW.md)에 정리했습니다.
- 기본 정적 분석은 대상 실행이나 AI 연결 없이 동작합니다.

## 분석 흐름

```text
프로젝트 디렉터리
  → 정적 분석: 구조·호출·import·실행 후보 → 정적 HTML
  → AI 입력 계획: 후보 선택 + argv / 함수 인자 / 표준입력 / 예제 데이터
  → 복사본 동적 실행: 호출·예외·시간·관측 범위 → 시도별 HTML
  → AI가 실행 결과를 검토하여 입력 수정 또는 다른 경로 시도
  → 선택 도구의 별도 관측: DynaPyt / Scalene
  → 프로젝트 목적·디렉터리 역할·파일 관계·읽기 순서 → 통합 HTML
```

정적 분석과 실제 실행 사실은 유지하면서, AI를 실행 전 계획과 실행 후 해석에 모두 사용합니다. 기본 자동 실행 한도는 3회이며 각 시도는 새로운 프로젝트 복사본에서 진행합니다. AI가 정확한 입력을 알 수 없으면 이유를 남기고 중단할 수 있습니다.

## 실행 환경

- Windows PowerShell 기준으로 안내합니다. CPython **3.13 이상**이 필요합니다.
- 기본 분석기는 Python 표준 라이브러리를 사용합니다. AI API도 표준 라이브러리로 연결합니다.
- 실행 대상의 의존성은 실행에 사용할 Python 환경에 설치되어 있어야 합니다.
- AI 통합 분석에는 로컬 Ollama 또는 Chat Completions 호환 API가 필요합니다.
- DynaPyt·Scalene은 선택 의존성이며 지정하지 않으면 실행하지 않습니다.

`prototype_3`에서 다음 명령으로 설치 없이 실행할 수 있습니다.

```powershell
python --version
$env:PYTHONPATH = "$PWD/src;$env:PYTHONPATH"
python -m knitcode_analyzer_v2.study --help
```

새 PowerShell 창에서는 `PYTHONPATH` 설정을 다시 실행합니다. 설치형으로 사용하려면 같은 폴더에서 `python -m pip install -e .`를 실행하세요. 별도 가상환경 생성은 필수가 아닙니다.

## 기본 정적 분석

```powershell
python -m knitcode_analyzer_v2 "C:/work/my-project" --report-dir "./reports/my-project-static"
Start-Process "./reports/my-project-static/report.html"
```

Python AST로 정의·소속·호출·import 관계와 위치를 분석합니다. 파일 구조, 해석 가능한 관계, 미해결 호출, 읽기 시작점, 전체 구조 그래프를 HTML·Markdown·JSON으로 저장합니다. 기존 정적 분석의 계약은 유지됩니다.

실행 후보까지 검토하려면 다음 명령을 사용할 수 있습니다. 대상은 실행하지 않습니다.

```powershell
python -m knitcode_analyzer_v2.runtime "C:/work/my-project" --discover --report-dir "./reports/my-project"
```

`--source-root src`는 분석 범위를 `src`로 제한하며 그 밖의 테스트는 제외합니다. `--exclude "tests/"` 등은 반복 지정할 수 있습니다. 정적 보고서를 동적·AI 통합 단계에 연결할 때는 같은 소스와 분석 옵션을 사용하세요.

## AI·동적 통합 분석

로컬 Ollama 서버와 모델을 준비한 경우:

```powershell
python -m knitcode_analyzer_v2.study "C:/work/my-project" --ai ollama --model "설치한-모델명" --report-dir "./reports/my-project"
```

외부 호환 API를 사용하는 경우:

```powershell
$env:KNITCODE_AI_BASE_URL = "https://your-api-server.example/v1"
$env:KNITCODE_AI_MODEL = "your-model-name"
$env:KNITCODE_AI_API_KEY = "your-api-key"
python -m knitcode_analyzer_v2.study "C:/work/my-project" --ai api --report-dir "./reports/my-project"
```

URL·모델·키는 실제 서비스의 값으로 바꿉니다. 실제 키를 문서나 소스에 저장하지 마세요. Ollama 기본 주소는 `http://127.0.0.1:11434`이며 `KNITCODE_OLLAMA_BASE_URL`, `KNITCODE_OLLAMA_MODEL`도 지원합니다. `--base-url`, `--model`, `--ai-timeout`, `--num-ctx`로 설정을 바꿀 수 있습니다.

이미 생성한 정적 보고서를 연결하려면 명령에 `--static-report "정적 보고서 폴더 또는 report.html"`을 추가합니다. 연결하지 않아도 통합 실행이 정적 보고서를 먼저 저장합니다.

### 자동 입력과 반복 분석

AI는 정적 후보·함수 매개변수·`input()`과 CLI 옵션 구문·README 일부·이전 시도의 실행 상태와 예외 종류를 이용합니다.

| 입력 형태 | 자동 처리 |
| --- | --- |
| 명령행 옵션과 인자 | 선택한 파일의 `argv`로 전달 |
| 함수 위치·키워드 인자 | JSON 값으로 전달하고 호출 시그니처 검사 |
| `input()` / `sys.stdin` | 미리 생성한 텍스트를 표준입력으로 공급 |
| JSON·CSV·TXT 예제 파일 | 복사본의 `.knitcode-inputs/` 안에 생성 |
| 잘못된 입력으로 실패 | 상태·예외 종류·관측 범위를 AI에 전달하여 다음 입력 계획 |
| 같은 계획 반복·알 수 없는 실행 대상 | 재실행하지 않고 오류 기록 |
| 자격증명·서비스·복잡한 객체 등이 필요 | 자동 해결을 보장하지 않으며 미확인 이유를 보고서에 기록 |

실시간으로 터미널과 대화하는 에이전트는 아닙니다. 입력은 실행 전에 생성하고, 실행이 끝나면 다음 시도의 입력을 조정합니다. 실행기는 AI의 임의 셸 명령이나 Python 코드를 직접 실행하지 않고, 정적으로 발견한 후보와 제한된 JSON 실행 계획만 받아들입니다. 전달한 입력을 어떻게 처리하는지는 대상 프로그램의 동작에 따릅니다.

| 옵션 | 기본값과 의미 |
| --- | --- |
| `--max-attempts` | 3, 허용 1~5. 계획 요청 횟수이자 기본 동적 실행 횟수의 상한 |
| `--timeout` | 시도당 30초, 허용 0.1~300초 |
| `--max-events` | 시도당 10,000개, 최대 500,000개 |
| `--python` | 대상 의존성이 설치된 CPython 실행 파일 |
| `--source-root`, `--exclude` | 정적 단계와 동일한 분석 범위 |
| `--supplement` | `dynapyt` 또는 `scalene`. 반복 지정 가능 |

기본 시도의 계획 요청은 최대 `max-attempts`회, 최종 설명 요청은 최대 1회입니다. 추가 도구 실행은 별도이며 각 프로세스에 시간 제한을 적용합니다. 모델의 성공률이나 모든 코드 경로의 탐색을 보장하지 않습니다.

### 실행 복사본

각 시도는 보고서 아래 `workspaces/`에 만든 새 복사본에서 실행합니다. 원본 분석 파일의 해시를 대조하며 기본 제외 디렉터리, 숨김 항목, 링크, 일부 인증서·키 파일을 복사하지 않습니다. 복사 상한은 50MiB·5,000파일입니다. 분석 범위 밖 Python 파일과 제외된 환경 파일이 필요하면 실행이 실패할 수 있습니다.

**복사본은 보안 샌드박스가 아닙니다.** 대상이 사용하는 절대 경로·외부 서비스·자식 프로세스의 부작용을 차단하지 않습니다. 신뢰할 수 있는 대상과 테스트 환경에서 사용하세요. 분석기 API 키 `KNITCODE_AI_API_KEY`는 대상 프로세스에 전달하지 않습니다.

## 외부 동적 분석 도구

```powershell
python -m pip install -e ".[dynapyt,scalene]"
python -m knitcode_analyzer_v2.study "C:/work/my-project" --ai ollama --model "설치한-모델명" --supplement dynapyt --supplement scalene --report-dir "./reports/my-project"
```

선택한 Python에 도구가 없으면 해당 분석을 `unavailable`로 기록하며 기본 보고서를 유지합니다. 마지막으로 수집한 기본 실행의 입력을 사용해 도구별 새 복사본에서 별도로 실행합니다.

| 도구 | 현재 연동 범위 |
| --- | --- |
| DynaPyt | 복사본 계측, 함수 진입과 `if` 조건 관측의 위치·횟수 수집, 원본 정적 노드에 연결. 값 변경은 하지 않음. 최대 10,000개 이벤트와 200개 위치 집계 |
| Scalene | 별도 CPU·메모리 프로파일 실행, 위치별 수치를 최대 200개 정적 노드 위치에 연결. 메모리 시작 실패 시 새 복사본에서 CPU 전용 실행을 한 번 시도하며 부분 성공으로 표시 |

DynaPyt 계측 단계도 실행 단계와 별도 시간 제한을 사용합니다. Scalene의 CPU 전용 보완 실행은 대상이 추가로 한 번 실행되는 것입니다. 각 도구의 시간·표본·관측은 기본 추적과 동일한 실행으로 합치지 않습니다. 이 연동은 제한된 범위의 선택 기능이며 임의 프로젝트의 호환성을 보장하지 않습니다. 도구 출처: [DynaPyt](https://github.com/sola-st/DynaPyt), [Scalene](https://github.com/plasma-umass/scalene).

## 결과와 보고서 읽기

통합 결과는 `REPORT_DIR/studies/<ID>/`에 저장되며 터미널에 최상위 `report.html` 경로가 출력됩니다.

| 산출물 | 내용 |
| --- | --- |
| `report.html` / `report.md` | 프로젝트 목적, 디렉터리·핵심 파일 역할, 연결 흐름, 읽기 순서, AI 추론·한계, 실행별 링크, 전체 구조 그래프 |
| `static/` | 최초 정적 HTML·Markdown·JSON·manifest |
| `dynamic/runs/<ID>/` | 시도별 동적 HTML, 정적 스냅샷, 이벤트·집계·로그·manifest |
| `plan-N.json` | AI에 제공한 계획 근거, 후보·입력·예제 파일·실행 가정 |
| `guide.json` | 최종 프로젝트 이해 설명과 참조 근거 |
| `study.json` | 모든 시도의 상태·계획·관측 범위·추가 도구 결과 |
| `supplements/<도구>/` | 도구별 원자료·로그·위치 매핑 집계 |
| `workspaces/` | 기본 시도별 실행 복사본과 합성 입력 파일 |
| `study-manifest.json` | 통합 산출물과 개별 실행 manifest의 해시 |

보고서 앞부분에서 프로젝트 목적과 디렉터리 역할을 먼저 읽고, 핵심 파일·읽기 순서·실제 실행 기록을 확인하세요. 그래프는 디렉터리·파일·함수 수준과 정적/관측 관계 필터를 제공합니다. 최상위 그래프의 동적 관계·타임라인은 **마지막으로 저장한 기본 실행**이며, 다른 시도는 개별 보고서에서 확인합니다. 여러 시도의 함수 관측 수는 합집합으로 표시하지만 실행 순서와 시간을 합산하지 않습니다.

`complete`는 요청한 범위 안의 수집·설명이 완료되었다는 상태입니다. 실패한 시도, 부분 추적, 외부 도구 실패, AI 설명 실패, 미실행 상태는 `partial`로 남습니다. 결과가 만들어졌다면 부분 성공 보고서도 읽을 수 있습니다. `study` 종료 코드는 완료 0, 부분 완료 1, 입력·설정 오류 2, 취소 130입니다.

## 수동 실행과 저장 결과 재해석

입력을 직접 정해야 하는 대상은 수동 동적 실행을 사용할 수 있습니다.

```powershell
python -m knitcode_analyzer_v2.runtime "C:/work/my-project" --entry "실제파일.py::실제함수" --call-args '[3]' --report-dir "./reports/manual"
python -m knitcode_analyzer_v2.runtime "C:/work/my-project" --script "실제파일.py" --stdin-file "./inputs.txt" --report-dir "./reports/manual"
```

`--entry`는 실제 후보 문자열입니다. `--script`, `--module`도 지원하며 대상 CLI 인자는 마지막 `--` 뒤에 전달합니다. `--stdin-file`은 UTF-8 64KiB 이하 텍스트를 공급합니다. 수동 기본 실행은 최초 스레드의 동기 Python을 `sys.monitoring`으로 추적합니다. 비동기·제너레이터·다른 스레드·자식 프로세스 내부는 추적 범위 밖이며 시간에는 추적 비용과 대기가 포함됩니다.

대상을 재실행하지 않고 **저장된 정적·동적 보고서**로 AI 설명만 새로 만들려면 `review`를 사용합니다.

```powershell
python -m knitcode_analyzer_v2.review --static-report "정적 결과 폴더" --runtime-report "동적 결과 폴더" --ai ollama --model "설치한-모델명" --report-dir "./reports/review"
```

두 입력의 프로젝트·스냅샷이 같아야 합니다. 통합 실행의 개별 동적 결과를 다시 설명하려면 `--static-report`와 `--runtime-report`에 모두 **그 실행의 `dynamic/runs/<ID>` 폴더**를 지정하세요. 실행 복사본과 원본 프로젝트는 루트 경로가 달라 원본 정적 폴더와 직접 결합하지 않습니다. 새 AI 보고서는 `ai/<ID>/`에 생성되며 입력 보고서를 수정하지 않습니다.

`assist search/context/explain`은 선택한 코드에 대한 기존 검색·원문 설명 기능입니다. 통합 프로젝트 안내에는 `study`, 저장 결과 후처리에는 `review`를 사용합니다. 각 명령의 `--help`에서 전체 옵션을 확인할 수 있습니다.

## AI에 전달하는 정보

`study`는 상대 경로·이름·docstring 일부·정적 관계·후보와 매개변수·입력 구문·README 최대 4,000자·패키지 메타데이터·생성한 입력 계획·실행 상태·예외 종류·관측 집계·추가 도구 위치 수치를 전달합니다. 전체 소스, 환경 변수, 비밀 설정 파일, 실행 로그 원문, 추적 중 변수·반환값은 전달하지 않습니다. 문서와 입력 구문에 작성된 문자열도 전송 대상이므로 민감정보를 넣지 마세요. AI 요청과 응답은 결과 JSON에 저장됩니다.

AI 응답은 구조·후보·근거 ID·경로·크기 제약을 검사합니다. 이러한 검사는 설명의 의미나 입력의 업무상 적합성을 증명하지 않습니다. AI의 역할 설명과 읽기 안내는 추론으로 표시하며 원래 정적·동적 그래프를 변경하지 않습니다.

## 주요 코드와 검증

| 파일 | 역할 |
| --- | --- |
| `analyzer.py`, `scanner.py`, `symbols.py`, `resolver.py` | 정적 분석·파일 탐색·정의 수집·이름 해석 |
| `runtime_candidates.py` | 이름·호출·import 근거로 실행 후보 추출 |
| `runtime.py`, `_runtime_worker.py`, `runtime_trace.py` | 실행 관리·표준입력·실제 코드 추적·집계 |
| `study.py`, `study_context.py` | AI 계획·복사본·재시도·프로젝트 역할 설명 |
| `supplements.py`, `dynapyt_hooks.py`, `_supplement_worker.py` | 선택 도구 실행·위치 매핑 |
| `review.py`, `saved_reports.py` | 저장된 분석 결과 검증과 AI 후처리 |
| `ai.py`, `project_summary.py` | API 연결·응답 검증·구조화 문맥 |
| `reports/`, `exporter.py` | 그래프·HTML·Markdown·저장 무결성 |
| `tests/test_study.py`, `tests/test_supplements.py` | 자동 입력·피드백·보존·선택 도구 연동 검증 |

위 파일은 `src/knitcode_analyzer_v2/` 기준입니다. 테스트 실행:

```powershell
python -m pip install -e ".[test]"
python -m unittest discover -s tests -t .
```

선택 도구를 설치한 환경에서는 `$env:KNITCODE_TEST_OPTIONAL = "1"`을 설정한 뒤 `python -m unittest tests.test_supplements -v`로 실제 도구 연동을 확인합니다. AI 계획·설명 테스트는 모의 모델 응답을 사용하며 실제 모델의 입력 생성 성공률·설명 품질은 별도 검증이 필요합니다. 상세 제한과 검증 기록은 [docs/limitations.md](docs/limitations.md), [docs/validation.md](docs/validation.md)를 참고하세요.
