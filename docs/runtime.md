# J: 동적 분석 계약 1.1

정적 스키마 2.1은 유지한다. 수동 동적 실행은 `python -m knitcode_analyzer_v2.runtime`, AI 계획과 반복 실행은 `python -m knitcode_analyzer_v2.study`에서 수행한다. 기본 분석과 `assist`는 대상 코드를 실행하지 않는다. 동적 계약 1.1은 1.0의 호출 관계 의미를 유지하면서 호출별 시간·중첩·함수 실행 관측 비율을 추가한다. 아래 실행 계약은 별도 표시가 없으면 수동 `runtime` 기준이다.

## AI·동적 통합 실행

`study`는 정적 결과를 먼저 저장하고 정적 후보·매개변수·입력 구문·README 일부를 사용해 AI 실행 계획을 만든다. 제한된 JSON 계획을 검사하고 새 복사본에서 인자·표준입력·예제 파일을 공급한다. 실행 결과의 상태·예외 종류·관측 집계를 다시 AI에 전달한다. 계획은 기본 3회, 최대 5회이며 동일 계획은 재실행하지 않는다. 선택한 DynaPyt·Scalene은 마지막 수집 입력을 도구별 별도 복사본에서 실행한다.

`studies/<ID>/`에 정적 보고서, 시도별 동적 보고서, 계획과 최종 AI 설명, 디렉터리·파일 역할과 읽기 순서를 담은 통합 HTML·Markdown을 저장한다. `verify_study(directory)`는 최상위 산출물과 하위 실행의 manifest를 확인한다. 실행 복사본은 manifest에서 제외한다. 최상위 동적 그래프는 마지막 기본 실행이며 시간축은 합치지 않는다. 자세한 입력·산출물·한도는 [전체 흐름](../ANALYSIS_FLOW.md)과 [README](../README.md)를 따른다.

AI가 만든 인자·표준입력·예제 데이터는 `plan-N.json`과 `study.json`에 보존되며 다음 AI 요청에도 포함된다. 수동 실행의 인자 비전송 규칙과 구분한다. 실행 복사본은 보안 샌드박스가 아니다.

## 실행과 환경

`PROJECT` 디렉터리만 지정하거나 `PROJECT --script PATH`, `PROJECT --module NAME`, `PROJECT --entry PATH`를 지정한다. 세 선택 옵션은 상호 배타적이다. `--` 뒤의 인자는 대상 프로그램에 그대로 전달한다. 스크립트와 cwd는 프로젝트 내부 실제 경로로 제한하며 경로의 symlink/junction을 거부한다. 스크립트는 분석 범위에 포함되고 파싱되어야 한다. 기본 cwd는 프로젝트, 명시적 `--module` 실행의 sys.path 시작은 cwd, 스크립트 실행의 시작은 스크립트의 부모다. 명시적 실행에서 `--source-root`는 분석에만 영향을 주며 대상 import 환경을 대신 구성하지 않는다.

디렉터리만 지정하면 정적 분석 스냅샷에서 후보를 수집하며 대상 코드를 import하지 않는다. 이름과 무관하게 모듈 최상위 호출, 내부 호출 연결, 파일 간 import 유입, 최상위 함수의 내부 호출자 유무를 사용한다. `__main__` 가드/모듈과 관례적 파일명도 근거에 포함한다. 제외된 파일과 파싱 실패 파일은 후보가 아니다. 함수 후보는 `파일.py::함수`로 식별한다. 정적 edges/nodes를 변경하지 않는다.

자동 선택은 `auto_eligible`인 **파일** 후보가 하나일 때만 한다. 직접 실행 가드/모듈, 다른 파일의 import 유입 없이 내부 호출을 수행하는 최상위 파일, 소스 루트의 관례적 실행 파일이 해당한다. 자동 후보가 없거나 여러 개이면 실행·출력 폴더 생성 전에 오류와 후보를 안내한다. 점수만으로 동률이나 여러 후보를 임의 선택하지 않는다. 함수·약한 파일 후보는 `--entry`로 명시한다. 모든 Python 파일·함수를 순회 실행하지 않는다.

자동/후보 선택에서 중첩 `__main__.py` 또는 `__init__.py`가 확인되는 패키지 내부 가드 파일은 유효한 모듈 경로로 변환한다. 모듈 경로는 실제 선택 파일을 가리킨다(예: `pkg.__main__`, `pkg.cli`). 분석 소스 루트를 모듈 검색 시작으로 사용하며, 기본 소스 루트에서 발견한 `src/패키지/` 구조는 `src`를 사용한다. cwd는 변경하지 않는다. 패키지로 처리할 수 없는 후보는 파일로 실행한다. 사용자 지정 로더·콘솔 스크립트·프레임워크 실행법은 추정하지 않는다.

`run.json.entry_selection`의 mode는 `auto`, `selected`, `explicit`이다. auto/selected에는 file, rule, script, module, import_root(프로젝트 상대 경로 또는 null)를 기록한다. 기존 명시적 script/module 실행의 import 경로 동작은 유지한다. API는 `run_project(project)`로 자동 선택하며 `entry_point="pkg/__main__.py"`로 후보를 지정할 수 있다.

### 후보 추천·실행 전 보고서

`runtime_candidates.discover_entry_points(analysis)`는 selector, node_id, kind, rule, score, confidence, auto_eligible, supported, reasons, evidence_edge_ids, required_parameters, reachable_nodes, reachability_capped 및 실행 정보를 반환한다. 점수는 확률이 아니다. 파일 후보는 명시적 시작 근거 100점, 최상위 호출 20점과 내부 대상 수 최대 20점, import 유입 없음 10점(유입 시 최대 15점 감점), 관례적 이름 5점, 도달 수 최대 15점을 조합한다. 함수 후보는 외부 호출 가능성이 있는 내부 호출자 없는 최상위 함수로 15점에 도달 수 기여 최대 30점을 더한다. 동점은 selector로 정렬한다. 소속·annotation·definition_expression 호출은 도달 탐색에서 제외하고 순환은 방문 집합으로 종료한다. 후보별 도달 탐색은 200개 수준에서 제한하고 제한 여부를 명시한다.

`--discover` / `plan_project`는 실행 없이 `REPORT_DIR/plans/계획-ID/`에 정적 JSON·HTML·Markdown·candidates.json·plan-manifest.json을 만든다. 기존 보고서를 덮어쓰지 않는다. HTML은 간략 구조 설명, 추천 근거와 필요 입력, 전체 구조 그래프를 포함하고 후보 버튼에서 호출 흐름으로 이동할 수 있다. candidates.json은 snapshot_id와 execution_status=not_executed를 기록한다. 추가 AI 파일을 포함한 해시는 plan-manifest에 남긴다. 기본 manifest는 기존 analysis/report 파일만 검증한다.

`--entry file.py::function`은 최상위 동기 함수 후보를 호출한다. JSON 배열 `--call-args`, JSON 객체 `--call-kwargs`를 명시하고 부모에서 선언 매개변수에 bind하여 누락/불필요 입력을 검사한다. 기본값이나 타입 힌트를 평가해 인자를 만들지 않는다. worker는 모듈을 `__knitcode_entry__` 이름으로 로딩하여 __main__ 가드는 실행하지 않지만 일반 최상위 부작용은 발생한다. 실제 함수 코드 객체가 스냅샷의 선택 노드와 일치하는지와 실제 signature를 다시 검사한다. 래퍼·재정의·비동기·제너레이터는 호출을 거부하며 로딩 이후 오류는 실패 실행 보고서에 남긴다. 클래스 인스턴스나 중첩 함수는 직접 실행 후보가 아니다. 함수 인자 값은 worker 설정에 잠시 기록한 후 삭제하며 결과 JSON이나 AI 문맥에는 남기지 않는다. 로그에는 대상이 출력한 값이 있을 수 있다.

`--python`은 인터프리터 실행 파일 경로 또는 실행 이름이다. 기본은 현재 Python이다. CPython 3.13 이상이 필요하고 대상의 패키지·환경 설정은 사용자가 준비한다. 자식은 현재 프로세스 환경에서 `KNITCODE_AI_API_KEY`를 제거하고 표준 출력 인코딩을 UTF-8로 설정한다. `--stdin-file`로 UTF-8 64KiB 이하 입력을 임시 파일 핸들로 전달하며 미지정 시 stdin은 닫힌 상태다. API는 `stdin_text`를 받는다. 입력의 바이트 수와 해시를 기록하며 실시간 대화형 stdin은 제공하지 않는다. 실행기 자체는 분석기 설치 위치의 절대 경로로 호출하므로 선택한 Python에 분석기 패키지를 다시 설치할 필요가 없다. 이 방식은 분석기 소스 경로에 접근 가능해야 한다.

대상 실행에는 별도 프로세스와 인자 배열을 사용하고 shell은 사용하지 않는다. 대상 소스 자체에 추적 코드를 삽입하지 않는다. 실행기는 `runpy`를 사용하며 일반 실행과 완전히 동일한 시작 상태나 성능을 보장하지 않는다. 모듈 로딩·코드 컴파일·추적 준비 비용이 추가된다. 참고: [runpy](https://docs.python.org/3.13/library/runpy.html).

## 수집 범위

`sys.monitoring`의 PY_START/PY_RETURN/RAISE/PY_UNWIND/PY_RESUME를 관측한다. 시작 이벤트에 대응하는 현재 Python 프레임과 바로 위 Python 프레임을 확인한다. 실제 진입한 프로젝트 함수·메서드만 관측 호출 대상이 된다. 파일/클래스 본문 진입도 이벤트·노드 진입 횟수에 남기되 함수 호출로 집계하지 않는다. 참고: [Python monitoring](https://docs.python.org/3.13/library/sys.monitoring.html).

`caller_relation: python_frame_parent`는 바로 위 Python 프레임이라는 의미다. 중간 C 함수의 콜백, 클래스 생성 같은 암시적 실행이 있을 수 있으므로 Python 소스의 명시적 직접 호출과 같다고 단정하지 않는다. 위 프레임이 프로젝트 밖 또는 매칭 불가이면 caller_id는 null이고 프로젝트 내 호출자를 추측해 건너 연결하지 않는다. 출발점이 없는 함수 진입도 node_entries와 이벤트에는 남는다.

최초 실행 스레드의 일반 동기 Python 코드만 지원한다. 수집기와 내부 worker 자체의 실행은 기록에서 제외한다. 비동기·제너레이터는 code flags로 제외하고 추가 스레드의 프로젝트 진입을 만나면 생략 이유를 기록한다. 관측된 subprocess/os spawn audit 이벤트는 자식 프로세스 미추적 제한을 남긴다. C 확장 내부·임의 네이티브 프로세스 생성·범위 밖 코드의 모든 동작을 탐지한다고 보장하지 않는다. 스레드 및 자식 프로세스 자체를 생성하지 못하게 막지는 않는다.

예외 이벤트는 클래스의 모듈·종류와 위치만 담는다. 예외 메시지, 로컬 변수, 인자·반환값은 수집하지 않는다. raise에는 나중에 처리되는 예외도 포함되며 최종 실패와 구분한다. 대상 프로그램의 argv는 실행 조건으로 보존하고 stdout/stderr는 별도 로그로 수집하므로 거기에 출력된 값·예외 메시지는 로그에 남을 수 있다.

## 정적 스냅샷 연결

실행 준비 시 읽은 파일 바이트의 SHA-256이 정적 분석과 일치하는지 확인한다. 실행 Python에서 해당 소스를 실행 없이 컴파일하여 코드 객체들을 얻는다. AST 정의 위치·qualified name·데코레이터 시작 위치로 정적 노드와 대응하고, 실제 실행 코드 객체의 구조·상수·위치가 컴파일된 객체와 일치할 때 연결한다. 같은 이름 또는 같은 co_filename만으로 연결하지 않는다. 람다·일부 합성 코드·동적 생성 코드·특이한 로더는 매칭 불가로 남을 수 있다.

콜백 위치는 CPython 코드 위치와 명령 offset에서 얻는다. 위치가 없으면 range는 null이다. 정적 위치와 같이 행 1 기준·UTF-8 바이트 열 0 기준·끝 제외다. 호출 근거 범위는 상위 프레임의 실행 명령이며 AST의 ast.Call 전체 범위와 항상 같지는 않다.

실행 후에도 원본 파일 해시를 비교한다. 바뀌거나 없어진 파일은 changed_files와 source_changed로 기록한다. 저장된 관계는 실행 전 스냅샷에 연결되며 현재 편집된 코드에 자동으로 재부착하지 않는다. 파일 전체를 잠그는 원자적 스냅샷이 아니며, 다른 코드가 추적기를 고의로 변경하는 상황에 대한 보안 경계도 아니다.

## 저장 형식

매번 `REPORT_DIR/runs/UTC시각-임의ID/`에 새로 저장한다. 기존 정적 보고서와 다른 실행 폴더를 덮어쓰지 않는다. 출력 베이스는 프로젝트·소스 루트 자체나 프로젝트 상위 경로일 수 없다. 분석 중에는 출력 베이스 전체를 제외한다.

| 파일 | 계약 |
| --- | --- |
| analysis.json | 변경하지 않은 정적 스키마 2.1 결과 |
| run.json | run_id, snapshot_id, 실행 명령/인자/cwd/인터프리터, 한도, 실행·추적 상태, 원래 대상 종료 코드, 로그 집계, 파일 변경, worker 정보 |
| events.jsonl | 이벤트 순서 seq, event, node_id, location, timestamp_ns, invocation_id/parent_invocation_id/depth; start에는 caller_id/caller_location/entry_kind/caller_relation; 예외에는 exception_type. limitation에는 시간·호출 필드가 없을 수 있음 |
| summary.json | runtime_schema_version 1.1, run_id/snapshot_id, observed_calls, node_entries, exceptions, event_count, limitations, invocations, node_timings, function_coverage, entry_candidates, overview |
| ai-explanation.json (선택) | AI에 제공한 제한 문맥, 응답 또는 실패 이유. 실행의 run-manifest 또는 후보 보고서의 plan-manifest에 해시 포함 |
| stdout.log / stderr.log | 프로그램 출력 원본 바이트의 앞부분. 한도 때문에 UTF-8 마지막 문자가 잘릴 수 있음 |
| report.html / report.md | 기존 정적 내용과 별도로 실행 상태·관계·근거 표시 |
| manifest.json | 기존 정적 산출물 검증 계약 |
| run-manifest.json | 전체 9개 산출물 SHA-256과 run ID. 마지막 저장 |

observed_calls 항목은 출발/대상 노드·호출 위치별로 집계한다. id는 `runtime:` 접두어, type은 observed_calls, count는 진입 횟수, event_sequences는 전체 근거 start 이벤트 번호다. 동적 관계를 정적 edges에 삽입하거나 static unresolved를 resolved로 변경하지 않는다.

### 시간·호출별 기록·실행 관측 비율

- `timestamp_ns`는 추적 시작의 `perf_counter_ns()`를 기준으로 한 단조 시계 경과 시간이다. 호출 이벤트의 콜백 진입 시 측정하며 파일 기록·코드 식별 등 추적 비용이 실행 시간에 포함된다.
- `invocation_id`는 해당 start의 seq이며 재귀마다 별도 ID를 부여한다. `parent_invocation_id`는 가장 가까운 **관측된** 활성 상위 호출이다. 추적하지 않는 외부 프레임이 사이에 있을 수 있으므로 바로 위 Python 프레임인 `caller_id`와 다른 개념이다. `depth`는 이 관측 부모를 기준으로 한다. 프레임 자체와 로컬 값은 저장하지 않는다.
- `invocations`는 파일·클래스 본문을 포함한 진입별 목록이다. `start_seq/end_seq`, `start_ns/end_ns`, `duration_ns`, `self_ns`, `outcome(return/unwind/incomplete)`를 제공한다. `raise`는 종료로 취급하지 않고 같은 invocation의 return/unwind로 종료를 확인한다. 잘림·타임아웃 때 열린 호출은 종료·시간 필드가 null이다.
- `self_ns`는 완료된 호출 시간에서 바로 아래 관측 자식들의 전체 시간을 뺀다. 외부 코드와 대기 비용은 남는다. 자식 종료가 누락된 경우 self 시간도 null이다. 함수·메서드별 `node_timings`는 `count`, `completed_count`, `incomplete_count`, `inclusive_ns`, `self_ns`, `self_sample_count`, `average_ns`, `max_ns`를 제공한다. 완료 호출만 합산하며 재귀 누적 시간은 중복 합산되므로 실행 전체 시간과 같지 않다. 완료 표본이 없으면 평균·최대는 null이다.
- `function_coverage`는 `total`, `observed`, `percent`, `unobserved_node_ids`다. 분모는 정적으로 수집한 모든 함수·메서드이며 지원 범위 밖의 async/generator도 포함한다. 함수가 없으면 percent는 null이다. 행·분기 커버리지 또는 미사용 판정이 아니다.
- 시간 필드 없는 1.0 이벤트를 요약하면 기존 호출 집계는 유지하고 시간 목록은 빈 목록이 된다. 정밀 프로파일러나 일반 실행의 성능 측정을 대체하지 않는다.

`verify_run(directory)`로 전체 실행 파일의 무결성을 확인할 수 있다. 해시는 우발적 파일 변경·불완전 저장 검출용이며 실행 대상이 같은 권한으로 결과와 manifest를 고의로 변조하는 것을 막는 서명은 아니다. 여러 파일 전체의 원자적 저장이 아니며 manifest가 없거나 해시가 다르면 완성된 실행 결과로 소비하지 않는다. 중간 실행기 설정 파일은 종료 후 정리하고 필요한 worker 상태는 run.json에 포함한다.

## 한도와 상태

기본 시간 제한 60초(허용 0.1~3600), 데이터 이벤트 50,000개(허용 1~500,000), 스트림당 로그 1MiB(허용 0~10MiB)다. 이벤트 한도에 도달하면 제한 표식 하나를 추가해 최대 한도+1행을 쓰고 모니터링을 비활성화한다. 대상 실행 자체는 계속되며 실행 시간 제한은 유지한다. 코드 식별 캐시는 20,000개 한도이며 초과하면 추적을 중단한다.

로그는 한도 이후 버리면서 파이프를 계속 읽어 대상의 출력 대기를 막는다. worker 종료 뒤에도 자식이 파이프를 잡고 있으면 짧게 기다린 뒤 로그 쓰기를 동결하고 제한을 기록한다. 이후 도착한 출력이 발행한 로그·manifest를 바꾸지 않는다. 지원하지 않는 자식 프로세스는 여전히 실행 중일 수 있다.

시간 제한·취소 시 worker를 종료하고 남은 유효 JSONL 행을 집계한다. 잘린 마지막 행은 원본 이벤트 파일에는 남을 수 있지만 보고서 집계에서 제외하고 제한으로 표시한다. 부모가 강제 종료되거나 디스크 오류가 발생하면 보고서가 완성되지 않을 수 있다.

execution_status는 finished / failed / timeout / cancelled / launch_failed다. trace_status는 complete_within_scope 또는 partial이다. 예를 들어 예외로 프로그램이 실패해도 그 실패 과정 전체를 관측했다면 trace_status는 complete_within_scope일 수 있다. static partial/empty, 기록 한도, 변경된 파일, 지원 범위 이탈 등은 partial로 표시한다. 로그 잘림은 로그 정보로 따로 표시한다.

CLI 종료 코드는 성공·범위 내 완료 0, 대상 실패·부분 추적 1, 입력/출력 오류 2, timeout 124, 취소 130이다. 대상 종료 코드는 run.json에서 별도 확인한다.

## 저장된 두 결과의 AI 후처리

단계별 사용법은 [실행 순서 가이드](../RUN_GUIDE.md)를 따른다. 기본 정적 분석은 변경하지 않는다. 동적 실행에는 선택적으로 `--static-report DIR_OR_HTML`을 지정할 수 있다. 기존 정적 보고서와 현재 소스의 프로젝트·스냅샷이 일치하지 않으면 대상 실행 전에 중단한다. 연결한 정적 결과의 경로·스냅샷·analysis.json 해시는 `run.json.static_report`에 기록한다. 동적 결과는 기존처럼 별도 `runs/<ID>/`에 저장한다.

```powershell
python -m knitcode_analyzer_v2.review --static-report "정적 결과 폴더" --runtime-report "동적 결과 폴더" --ai ollama --model "설치한 모델명" --report-dir "./reports/my-project"
```

`review`의 두 입력은 결과 폴더 또는 그 안의 `report.html`이다. 정적 입력은 기본 정적 CLI와 `--discover` 결과 모두 지원한다. HTML 파일을 모델에 전달하지 않고, 해당 폴더에 함께 저장된 구조·관측 데이터를 사용한다. 원본 프로젝트 소스가 이동하거나 없어도 입력 보고서가 온전하면 처리할 수 있다. 프로젝트 재분석, import, 대상 재실행은 하지 않는다.

입력 manifest의 산출물 목록과 SHA-256, 정적 스냅샷 내용, 두 프로젝트·스냅샷의 일치, 동적 run/snapshot ID, 저장된 이벤트와 집계의 일치를 검사한다. 동적 스키마 1.1을 지원한다. 서로 다른 소스 버전·분석 옵션의 결과는 병합하지 않고 오류를 반환한다. 해시는 실수에 의한 변경·불완전 저장을 확인하는 용도이며 신뢰할 수 없는 작성자를 인증하는 서명이 아니다.

실행 실패·부분 추적·타임아웃도 완성된 보고서이면 입력으로 사용할 수 있다. 유효하게 수집된 이벤트와 기존 실행 상태·한계를 유지한다. AI는 동적 명령의 선택 AI와 같은 제한된 문맥을 받으며, 정적 구조와 관측 경로 비교·미관측 범위·시간 측정 한계·후속 분석 제안을 설명하도록 요청한다. AI 추론은 검증된 결함이나 자동 실행 명령으로 취급하지 않는다.

매번 `REPORT_DIR/ai/<UTC시각-ID>/`에 새 결과를 생성한다. 입력 폴더 안으로 출력하는 것은 거부한다. 정적·동적 원본 파일은 수정하지 않는다.

| 산출물 | 내용 |
| --- | --- |
| report.html / report.md | 두 입력 출처, 구조·실행 흐름, 원래 정적·동적 결과와 AI 보완 설명 또는 실패 이유 |
| analysis.json / runtime-analysis.json | 사용한 정적 보고서의 분석 데이터 / 동적 실행 당시 정적 데이터 |
| run.json / summary.json / events.jsonl | 사용한 실행 데이터의 사본. 원본 로그는 동적 입력 폴더에 유지 |
| ai-explanation.json | 제한된 요청 문맥, 응답·검증 결과 또는 실패 이유 |
| review.json | 생성 시각·review ID·snapshot/run ID·입력 폴더와 산출물 해시·AI 상태·target_reexecuted=false |
| manifest.json / review-manifest.json | 기본 보고서 해시 / 전체 AI 후처리 산출물 10개의 해시 |

`verify_review(directory)`로 AI 결과 무결성을 확인한다. 성공 0, 입력·출력 오류 2, AI 실패 3, 취소 130이다. AI 실패는 원본을 보존하고 실패 보고서를 생성하므로 연결 설정을 수정해 `review`만 다시 실행하면 된다. 외부 API는 `--ai api`와 기존 `KNITCODE_AI_*`, 로컬 Ollama는 `--ai ollama`와 기존 `KNITCODE_OLLAMA_*` 설정을 사용한다. `--base-url`, `--model`, `--ai-timeout`, `--num-ctx`도 지원한다.

기존 `runtime --ai`와 `--discover --ai` 일괄 방식은 호환성을 위해 유지한다. 동적 보고서를 먼저 검토하고 AI를 별도 실행하려면 `runtime`에 `--ai`를 넣지 않고 이후 `review`를 실행한다.

## UI와 후속 범위

HTML 앞부분은 프로젝트 전체 구조·파일 연결 지표·실행 후보와 근거·규칙 기반 간략 설명을 제공한다. 동적 보고서는 실제 시작점·실행/추적 상태·관측 파일 수·함수 비율·파일별 최초 진입 순서·빈번한 호출 연결도 설명한다. 파일별 최초 진입 순서나 빈도 순위를 전체 실행 순서로 단정하지 않으며 상세 타임라인을 함께 제공한다. 모든 코드 유래 문자열과 AI 응답은 HTML 이스케이프한다.

### 선택 AI 설명

`--ai api|ollama`를 명시한 경우에만 기존 AI 어댑터를 사용한다. `--discover`에는 정적 근거만, 실제 실행에는 정적 근거와 관측 집계를 제공한다. `--base-url/--model` 또는 기존 provider별 환경 변수, `--ai-timeout`, `--num-ctx`를 지원한다. 환경 변수만으로는 연결하지 않는다. API 인증 키는 결과와 worker 설정에 저장하지 않으며 Ollama에 전달하지 않는다.

project 문맥은 최대 노드 80개·정적 관계 80개·관측 관계 40개와 프로젝트 통계 1개, 각 노드 docstring 앞 600자를 제공한다. 상대 경로·이름·해시·후보 추천·실행 상태·횟수·시간·한도를 포함하고 원문 전체·프로그램 인자·로그·변수 값·절대 루트 경로는 제외한다. 생략량과 input_hash를 기록한다. 근거 ID·노드 참조·응답 계약을 검사하고 관찰/추론을 분리하되 내용의 사실성을 자동 검증하지 않는다. AI는 후보 순위나 실행 선택·그래프 edges를 수정하지 않는다. 실패 시 기본 보고서는 보존하고 HTML과 ai-explanation.json에 실패를 명시한다. CLI 실행 종료 코드는 AI 실패와 별개이며 ai_status를 확인한다. 기본 AI 결과물도 오프라인 HTML 안에 표시하고 페이지에서 AI 서버를 직접 호출하지 않는다.

그래프는 정적/실행 관측/함께 보기 필터를 제공한다. observed_calls는 보라색으로 표시하고 집계 숫자는 실행 횟수다. 정적 관계 숫자는 기존처럼 근거 위치 수다. 화면은 기존 노드 40·관계 묶음 100개 상한을 유지하며 모든 근거는 원자료에 남긴다. 실행 섹션은 이벤트 처음 300개, 관계 상세는 이벤트 번호 처음 50개와 생략 수를 보여준다.

전체 구조·폴더/파일 요약·호출 흐름에서도 같은 출처 필터를 사용한다. 호출 흐름의 열은 도달 가능한 최소 호출 단계이며 실행 순서가 아니다. 실제 순서는 호출 타임라인에서 진입 순서와 중첩 깊이, 상대 시간으로 읽는다. 타임라인은 처음 200개, 함수 시간은 누적 시간 상위 100개, 미관측 함수는 처음 100개를 표시하며 생략 수와 원자료 위치를 안내한다. 미완료 호출은 종료 위치를 추정하지 않고 시작 위치의 점선 표식으로 표시한다.

다중 스레드·비동기 task별 기본 추적, 자식 프로세스 추적/종료 보장, 실시간 대화형 입력, 정밀 성능 벤치마크, 추적 중 변수 값 수집은 후속 범위다. 기본 `assist`는 정적 분석만 사용한다. AI 통합 자동 실행은 `study`, 저장 결과 후처리는 `review`, 기존 일괄 설명은 `runtime --ai`로 생성한다. 별도 프로세스와 venv는 보안 샌드박스가 아니며 실행 대상의 파일·네트워크 부작용은 실제 발생한다.
