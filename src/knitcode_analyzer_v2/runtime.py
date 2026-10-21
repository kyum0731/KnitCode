"""Explicit execution command: analysis stays read-only; the worker executes code."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import tempfile
import time
import uuid

from .analyzer import analyze_project
from .exporter import to_json, validate_output_directory, write_report
from .runtime_trace import summarize
from .runtime_candidates import discover_entry_points, select_entry_point


def inside(root, value, *, directory=False):
    candidate = Path(value).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    path = candidate.resolve()
    if not path.is_relative_to(root) or (not path.is_dir() if directory else not path.is_file()):
        raise ValueError("프로젝트 내부의 존재하는 경로가 필요합니다: " + str(value))
    for part in (candidate.absolute(), *candidate.absolute().parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("실행 경로에 링크를 사용할 수 없습니다.")
    return path


def drain(pipe, destination, limit, stats, control):
    """Continue draining past the cap so a chatty target cannot block on its pipe."""
    total = 0
    try:
        with destination.open("xb") as stream:
            with control["lock"]:
                control["stream"] = stream
            while chunk := pipe.read(8192):
                with control["lock"]:
                    if control["stopped"]:
                        break
                    remaining = max(0, limit - total)
                    if remaining:
                        stream.write(chunk[:remaining])
                    total += len(chunk)
                    stats.update(bytes_seen=total, bytes_saved=min(limit, total), truncated=total > limit)
    except (OSError, ValueError):
        with control["lock"]:
            if not control["stopped"]:
                stats["read_error"] = True
    finally:
        with control["lock"]:
            if not control["stopped"]:
                stats.update(bytes_seen=total, bytes_saved=min(limit, total), truncated=total > limit)
        pipe.close()


def read_events(path, max_events):
    if not path.exists():
        return [], True
    with path.open("rb") as stream:
        return decode_events(stream, max_events)


def decode_events(stream, max_events):
    events, invalid = [], False
    for index in range(max_events + 2):
        raw = stream.readline(262145)
        if not raw:
            break
        if index > max_events or len(raw) > 262144 or not raw.endswith(b"\n"):
            invalid = True
            break
        try:
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get("seq") != index + 1 or event.get("event") not in {"start", "return", "raise", "unwind", "limitation"}:
                raise ValueError("Invalid runtime event")
            events.append(event)
        except (ValueError, UnicodeError):
            invalid = True
            break
    return events, invalid


def verify_run(directory):
    directory = Path(directory)
    try:
        manifest = json.loads((directory / "run-manifest.json").read_text(encoding="utf-8"))
        names = {"analysis.json", "report.html", "report.md", "manifest.json", "run.json", "summary.json", "events.jsonl", "stdout.log", "stderr.log"}
        run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        if "ai_status" in run:
            names.add("ai-explanation.json")
        return (manifest["producer"] == "knitcode_runtime" and set(manifest["artifacts"]) == names
                and all(hashlib.sha256((directory / name).read_bytes()).hexdigest() == value for name, value in manifest["artifacts"].items())
                and run["run_id"] == manifest["run_id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def run_project(project, *, script=None, module=None, entry_point=None, args=(), cwd=".", python=None, source_root=None,
                excludes=(), report_dir=None, timeout=60, max_events=50000, max_log_bytes=1048576,
                call_args=None, call_kwargs=None, ai_options=None, static_report=None, stdin_text=None):
    if stdin_text is not None and (not isinstance(stdin_text, str) or len(stdin_text.encode("utf-8")) > 65536):
        raise ValueError("표준입력은 UTF-8 64KiB 이하 문자열이어야 합니다.")
    if sum(value is not None for value in (script, module, entry_point)) > 1:
        raise ValueError("--script, --module, --entry는 동시에 지정할 수 없습니다.")
    if any(value == "" for value in (script, module, entry_point)):
        raise ValueError("실행 대상은 빈 문자열일 수 없습니다.")
    if not 0.1 <= timeout <= 3600 or not 1 <= max_events <= 500000 or not 0 <= max_log_bytes <= 10485760:
        raise ValueError("timeout 0.1~3600초, max-events 1~500000, max-log-bytes 0~10485760 범위가 필요합니다.")
    root = Path(project).expanduser().resolve()
    working = inside(root, cwd, directory=True)
    entry = inside(root, script) if script else None
    if entry and entry.suffix != ".py":
        raise ValueError("스크립트는 .py 파일이어야 합니다.")
    if module and not re.fullmatch(r"[A-Za-z_\w]+(?:\.[A-Za-z_\w]+)*", module):
        raise ValueError("유효한 Python 모듈 이름이 필요합니다.")
    root_hash = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    base = Path(report_dir) if report_dir else Path.cwd() / "knitcode-reports" / f"{root.name}-{root_hash}"
    base = validate_output_directory(base, root, root / (source_root or "."))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = base / "runs" / run_id
    if (base / "runs").is_symlink() or (base / "runs").is_junction():
        raise ValueError("runs 디렉터리로 링크를 사용할 수 없습니다.")
    analysis = analyze_project(root, source_root=source_root, excludes=excludes, excluded_paths=(base,))
    static_input = None
    if static_report is not None:
        from .saved_reports import load_static_report, require_matching_snapshots
        static_directory, static_analysis, static_manifest = load_static_report(static_report)
        require_matching_snapshots(static_analysis, analysis)
        static_input = {"directory": str(static_directory), "snapshot_id": static_manifest["snapshot_id"],
                        "analysis_sha256": static_manifest["artifacts"]["analysis.json"]}
    candidates = discover_entry_points(analysis)
    selection, import_root, callable_name = {"mode": "explicit"}, None, None
    if not script and not module:
        if entry_point is not None:
            candidate_file, separator, function = entry_point.partition("::")
            selected_path = inside(root, candidate_file).relative_to(root).as_posix() + ("::" + function if separator else "")
            selected = next((c for c in candidates if c["selector"] == selected_path), None)
            if selected is None:
                raise ValueError("--entry는 분석 범위 내 진입점 후보의 상대 경로여야 합니다. 일반 스크립트는 --script로 지정하세요.")
        else:
            selected = select_entry_point(analysis, candidates)
        if not selected["supported"]:
            raise ValueError("이 후보는 직접 실행을 지원하지 않습니다: " + "; ".join(selected["reasons"]))
        script, module = selected["script"], selected["module"]
        callable_name = selected["callable"]
        entry = inside(root, script) if script else None
        import_root = inside(root, selected["import_root"], directory=True) if module else None
        # Validate the selected module's source path too; never follow a link.
        inside(root, selected["file"])
        selection = {"mode": "selected" if entry_point is not None else "auto", **selected}
    if (call_args is not None or call_kwargs is not None) and not callable_name:
        raise ValueError("--call-args / --call-kwargs는 --entry 파일.py::함수 후보에만 사용하세요.")
    if call_args is not None and not isinstance(call_args, list) or call_kwargs is not None and (not isinstance(call_kwargs, dict) or not all(isinstance(k, str) for k in call_kwargs)):
        raise ValueError("함수 인자는 JSON 배열, 키워드 인자는 문자열 키의 JSON 객체여야 합니다.")
    if callable_name:
        validate_call_arguments(analysis, selection["node_id"], call_args or [], call_kwargs or {})
    if entry and not any(f["path"] == entry.relative_to(root).as_posix() and f["parse_status"] == "ok" for f in analysis["files"]):
        raise ValueError("실행 스크립트는 정적 분석 범위에 포함되고 파싱에 성공해야 합니다.")
    directory.mkdir(parents=True, exist_ok=False)
    executable = str(Path(python).expanduser().resolve()) if python and ("/" in python or "\\" in python) else python or sys.executable
    config = {"analysis": analysis, "directory": str(directory), "cwd": str(working), "script": str(entry) if entry else None,
              "module": module, "import_root": str(import_root) if import_root else None,
              "args": list(args), "max_events": max_events, "callable": callable_name,
              "callable_node_id": selection.get("node_id") if callable_name else None,
              "call_args": call_args or [], "call_kwargs": call_kwargs or {}}
    config_path = directory / "worker-input.json"
    config_path.write_text(to_json(config), encoding="utf-8")
    started = time.perf_counter()
    run = {"runtime_schema_version": "1.1", "run_id": run_id, "snapshot_id": analysis["metadata"]["snapshot_id"],
           "started_at": datetime.now(timezone.utc).isoformat(), "project_root": root.as_posix(), "cwd": working.as_posix(),
           "script": entry.relative_to(root).as_posix() if entry else None, "module": module, "args": list(args),
           "entry_selection": selection,
           "callable": callable_name,
           "requested_python": executable, "timeout_seconds": timeout, "max_events": max_events,
           "max_log_bytes_per_stream": max_log_bytes, "execution_status": "finished", "trace_status": "partial",
           "target_exit_code": None, "logs": {"stdout": {}, "stderr": {}},
           "scope": "single initial thread; synchronous Python code; no values; child processes not instrumented",
           "limitations": []}
    process, threads, controls = None, [], []
    input_stream = None
    if static_input is not None:
        run["static_report"] = static_input
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("KNITCODE_AI_API_KEY", None)
        if stdin_text is not None:
            input_stream = tempfile.TemporaryFile(mode="w+b")
            raw_input = stdin_text.encode("utf-8")
            input_stream.write(raw_input)
            input_stream.seek(0)
            run["stdin"] = {"bytes": len(raw_input), "sha256": hashlib.sha256(raw_input).hexdigest()}
        process = subprocess.Popen([executable, "-u", "-B", str(Path(__file__).with_name("_runtime_worker.py")), str(config_path)],
                                   cwd=working, env=env, stdin=input_stream if input_stream is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            control = {"lock": threading.Lock(), "stopped": False, "stream": None}
            controls.append(control)
            thread = threading.Thread(target=drain, args=(pipe, directory / (name + ".log"), max_log_bytes, run["logs"][name], control), daemon=True)
            thread.start()
            threads.append(thread)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            run["execution_status"] = "timeout"
            process.kill()
            process.wait(timeout=5)
        except KeyboardInterrupt:
            run["execution_status"] = "cancelled"
            process.kill()
            process.wait(timeout=5)
        run["target_exit_code"] = process.returncode
    except OSError as error:
        run["execution_status"] = "launch_failed"
        run["launch_error"] = str(error)
    finally:
        if input_stream is not None:
            input_stream.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            run["limitations"].append("log_pipe_still_open_possible_child_process")
        # Freeze output before hashing. Descendants may retain inherited pipe handles;
        # late output must never change a published run's log files or metadata.
        for control in controls:
            with control["lock"]:
                control["stopped"] = True
                if control["stream"] is not None and not control["stream"].closed:
                    control["stream"].flush()
                    control["stream"].close()
        run["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        config_path.unlink(missing_ok=True)
    worker_path = directory / "worker-status.json"
    try:
        worker = json.loads(worker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        worker = {"worker_completed": False, "limitations": ["worker_status_missing"]}
    worker_path.unlink(missing_ok=True)
    run["worker"] = worker
    for name in ("events.jsonl", "stdout.log", "stderr.log"):
        if not (directory / name).exists():
            (directory / name).touch()
    events, invalid = read_events(directory / "events.jsonl", max_events)
    summary = summarize(analysis, events)
    changed = []
    for file in analysis["files"]:
        if file["sha256"]:
            try:
                actual = hashlib.sha256((root / file["path"]).read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual != file["sha256"]:
                changed.append(file["path"])
    run["changed_files"] = changed
    reasons = set(run["limitations"] + summary["limitations"] + worker.get("limitations", []))
    if changed:
        reasons.add("source_changed")
    if invalid:
        reasons.add("incomplete_event_stream")
    if analysis["metadata"]["analysis_status"] != "complete":
        reasons.add("static_analysis_" + analysis["metadata"]["analysis_status"])
    if run["execution_status"] != "finished":
        reasons.add(run["execution_status"])
    run["limitations"] = sorted(reasons)
    run["trace_status"] = "complete_within_scope" if not reasons and worker.get("worker_completed") else "partial"
    if run["execution_status"] == "finished" and run["target_exit_code"] != 0:
        run["execution_status"] = "failed"
    summary["limitations"] = run["limitations"]
    summary["run_id"] = run_id
    summary["entry_candidates"] = candidates
    runtime = {"run": run, "summary": summary, "events": events}
    from .project_summary import project_overview, explain_project
    summary["overview"] = project_overview(analysis, runtime=runtime)
    if ai_options is not None:
        runtime["ai"] = explain_project(analysis, runtime=runtime, options=ai_options)
        run["ai_status"] = runtime["ai"]["status"]
        (directory / "ai-explanation.json").write_text(to_json(runtime["ai"]), encoding="utf-8")
    (directory / "run.json").write_text(to_json(run), encoding="utf-8")
    (directory / "summary.json").write_text(to_json(summary), encoding="utf-8")
    write_report(analysis, directory, runtime=runtime)
    names = ("analysis.json", "report.html", "report.md", "manifest.json", "run.json", "summary.json", "events.jsonl", "stdout.log", "stderr.log")
    if ai_options is not None:
        names += ("ai-explanation.json",)
    manifest = {"producer": "knitcode_runtime", "run_id": run_id, "snapshot_id": analysis["metadata"]["snapshot_id"],
                "artifacts": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}}
    (directory / "run-manifest.json").write_text(to_json(manifest), encoding="utf-8")
    return directory, runtime


def validate_call_arguments(analysis, node_id, args, kwargs):
    """Check the declared signature before launching, without evaluating defaults."""
    import inspect
    node = next(n for n in analysis["nodes"] if n["id"] == node_id)
    kinds = {"positional_only": inspect.Parameter.POSITIONAL_ONLY, "positional_or_keyword": inspect.Parameter.POSITIONAL_OR_KEYWORD,
             "var_positional": inspect.Parameter.VAR_POSITIONAL, "keyword_only": inspect.Parameter.KEYWORD_ONLY,
             "var_keyword": inspect.Parameter.VAR_KEYWORD}
    parameters = [inspect.Parameter(p["name"], kinds[p["kind"]], default=None if p["default"] is not None else inspect.Parameter.empty)
                  for p in node["parameters"]]
    try:
        inspect.Signature(parameters).bind(*args, **kwargs)
    except TypeError as error:
        raise ValueError(f"함수 호출 인자를 확인하세요 (--call-args / --call-kwargs): {error}") from None
    # Also reject non-JSON Python API inputs before creating a worker.
    to_json({"args": args, "kwargs": kwargs})


def plan_project(project, *, source_root=None, excludes=(), report_dir=None, ai_options=None):
    root = Path(project).expanduser().resolve()
    root_hash = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    base = validate_output_directory(report_dir or Path.cwd() / "knitcode-reports" / f"{root.name}-{root_hash}", root, root / (source_root or "."))
    if (base / "plans").is_symlink() or (base / "plans").is_junction():
        raise ValueError("plans 디렉터리로 링크를 사용할 수 없습니다.")
    analysis = analyze_project(root, source_root=source_root, excludes=excludes, excluded_paths=(base,))
    plan = {"snapshot_id": analysis["metadata"]["snapshot_id"], "execution_status": "not_executed",
            "candidates": discover_entry_points(analysis)}
    from .project_summary import explain_project
    explanation = explain_project(analysis, options=ai_options) if ai_options is not None else None
    if explanation is not None:
        plan["ai_status"] = explanation["status"]
    directory = base / "plans" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
    write_report(analysis, directory, explanation=explanation)
    (directory / "candidates.json").write_text(to_json(plan), encoding="utf-8")
    names = ["analysis.json", "report.html", "report.md", "manifest.json", "candidates.json"]
    if explanation is not None:
        (directory / "ai-explanation.json").write_text(to_json(explanation), encoding="utf-8")
        names.append("ai-explanation.json")
    (directory / "plan-manifest.json").write_text(to_json({"snapshot_id": plan["snapshot_id"],
        "artifacts": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}}), encoding="utf-8")
    return directory, plan


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    target_args = []
    if "--" in argv:
        index = argv.index("--")
        argv, target_args = argv[:index], argv[index + 1:]
    parser = argparse.ArgumentParser(description="대상 Python 코드를 실제 실행해 동기 호출을 관측합니다. 파일 변경·네트워크 요청이 발생할 수 있으며 샌드박스가 아닙니다.")
    parser.add_argument("project", help="분석할 프로젝트 디렉터리. 실행 옵션 생략 시 유일한 진입점 후보를 자동 선택")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--script", help="프로젝트 내부 .py 스크립트")
    target.add_argument("--module", help="실행할 Python 모듈 (예: unittest)")
    target.add_argument("--entry", help="정적 근거 후보 선택: 파일.py 또는 파일.py::함수 (미지정 시 충분한 근거의 유일한 파일 후보)")
    parser.add_argument("--discover", action="store_true", help="대상을 실행하지 않고 후보·근거·전체 구조 HTML 생성")
    parser.add_argument("--call-args", help="함수 후보에 전달할 JSON 배열 (예: '[3]')")
    parser.add_argument("--call-kwargs", help="함수 후보에 전달할 JSON 객체")
    parser.add_argument("--ai", choices=("api", "ollama"), help="선택 AI 설명. 구조·후보·실행 요약을 설정 서버로 전송 (기본 미사용)")
    parser.add_argument("--base-url", help="AI 서버 기본 URL (provider별 환경 변수도 지원)")
    parser.add_argument("--model", help="AI 모델명 (provider별 환경 변수도 지원)")
    parser.add_argument("--ai-timeout", type=int, default=60)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--python", help="대상 의존성이 설치된 CPython 3.13+ 실행 파일")
    parser.add_argument("--cwd", default=".", help="프로젝트 내부 작업 디렉터리 (기본 프로젝트 루트)")
    parser.add_argument("--source-root")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--report-dir")
    parser.add_argument("--static-report", help="기존 정적 보고서 폴더 또는 report.html. 실행 전에 현재 소스 스냅샷과 일치하는지 검사")
    parser.add_argument("--stdin-file", help="input()/sys.stdin에 공급할 UTF-8 텍스트 파일")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-events", type=int, default=50000)
    parser.add_argument("--max-log-bytes", type=int, default=1048576)
    options = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        ai_options = None
        if options.ai:
            from .ai import endpoint_url
            local = options.ai == "ollama"
            base_url = options.base_url or os.environ.get("KNITCODE_OLLAMA_BASE_URL" if local else "KNITCODE_AI_BASE_URL") or ("http://127.0.0.1:11434" if local else None)
            model = options.model or os.environ.get("KNITCODE_OLLAMA_MODEL" if local else "KNITCODE_AI_MODEL")
            if not base_url or not model:
                raise ValueError("AI를 사용하려면 --model과 --base-url 또는 해당 provider 환경 변수를 설정하세요.")
            endpoint, _ = endpoint_url(base_url, options.ai)
            ai_options = {"provider": options.ai, "base_url": base_url, "model": model,
                          "api_key": "" if local else os.environ.get("KNITCODE_AI_API_KEY", ""),
                          "timeout": options.ai_timeout, "num_ctx": options.num_ctx}
            print(f"AI 전송 대상: {endpoint} · 모델: {model} · 상대 경로·이름·docstring 일부·정적 관계·후보·실행 집계 전송. 원문 전체·인자 값·로그는 제외합니다.", file=sys.stderr)
        elif options.base_url or options.model:
            raise ValueError("AI 연결 설정에는 --ai api 또는 --ai ollama를 함께 지정하세요.")
        if options.discover:
            if options.static_report or options.stdin_file:
                raise ValueError("--static-report는 동적 실행 단계에만 사용하세요.")
            if options.script or options.module or options.entry or options.call_args is not None or options.call_kwargs is not None or target_args:
                raise ValueError("--discover는 실행 대상이나 실행 인자와 함께 사용할 수 없습니다.")
            directory, plan = plan_project(options.project, source_root=options.source_root, excludes=options.exclude,
                                           report_dir=options.report_dir, ai_options=ai_options)
            print(f"후보 {len(plan['candidates'])}개 · 대상 미실행\n후보·구조 보고서: {directory / 'report.html'}", file=sys.stderr)
            if plan.get("ai_status"):
                print(f"AI 설명: {plan['ai_status']} (실패해도 기본 보고서 유지)", file=sys.stderr)
            return 0
        call_args = json.loads(options.call_args) if options.call_args is not None else None
        call_kwargs = json.loads(options.call_kwargs) if options.call_kwargs is not None else None
        if options.call_args is not None and not isinstance(call_args, list) or options.call_kwargs is not None and not isinstance(call_kwargs, dict):
            raise ValueError("--call-args는 JSON 배열, --call-kwargs는 JSON 객체여야 합니다.")
        print("프로젝트를 분석한 뒤 선택된 진입점을 실제 실행합니다. 입력은 비대화형이며 stdout/stderr는 결과 폴더에 저장합니다.", file=sys.stderr)
        directory, result = run_project(options.project, script=options.script, module=options.module, entry_point=options.entry, args=target_args,
                                        cwd=options.cwd, python=options.python, source_root=options.source_root,
                                        excludes=options.exclude, report_dir=options.report_dir, timeout=options.timeout,
                                        max_events=options.max_events, max_log_bytes=options.max_log_bytes,
                                        call_args=call_args, call_kwargs=call_kwargs, ai_options=ai_options,
                                        static_report=options.static_report,
                                        stdin_text=Path(options.stdin_file).read_text(encoding="utf-8") if options.stdin_file else None)
        run = result["run"]
        print(f"실행 진입점: {run['script'] or run['module']} · 선택: {run['entry_selection']['mode']}", file=sys.stderr)
        print(f"동적 보고서: {directory / 'report.html'}\n실행: {run['execution_status']} · 추적: {run['trace_status']} · 대상 종료 코드: {run['target_exit_code']}", file=sys.stderr)
        if run.get("ai_status"):
            print(f"AI 설명: {run['ai_status']} (실패해도 기본 보고서 유지)", file=sys.stderr)
        if run["execution_status"] == "cancelled":
            return 130
        if run["execution_status"] == "timeout":
            return 124
        return 0 if run["execution_status"] == "finished" and run["trace_status"] == "complete_within_scope" else 1
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(f"knitcode runtime: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
