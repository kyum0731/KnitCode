"""Optional tool runs on independent copies; no mixing of profiling and tracing clocks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid

from .exporter import to_json
from .runtime import drain
from .runtime_candidates import launch_spec


def execute(command, directory, cwd, env, stdin, timeout, label):
    controls, threads, logs = [], [], {}
    with tempfile.TemporaryFile(mode="w+b") as input_stream:
        input_stream.write(stdin.encode("utf-8"))
        input_stream.seek(0)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=input_stream,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            control = {"lock": threading.Lock(), "stopped": False, "stream": None}
            controls.append(control)
            logs[name] = {}
            thread = threading.Thread(target=drain, args=(pipe, directory / f"{label}-{name}.log", 1048576, logs[name], control), daemon=True)
            thread.start()
            threads.append(thread)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=1)
            for control in controls:
                with control["lock"]:
                    control["stopped"] = True
                    if control["stream"] is not None and not control["stream"].closed:
                        control["stream"].close()
    return {"exit_code": process.returncode, "timeout": timed_out, "logs": logs}


def normalize(backend, payload, analysis, work):
    """Map external locations to original static node IDs, without fabricating calls."""
    rows = []
    def node_at(file, line):
        matches = [n for n in analysis["nodes"] if n["file"] == file and n["range"] and
                   n["range"]["start_line"] <= line <= n["range"]["end_line"]]
        return min(matches, key=lambda n: n["range"]["end_line"] - n["range"]["start_line"])["id"] if matches else None
    if backend == "dynapyt":
        for row in payload["events"][:200]:
            nid = node_at(row["file"], row["line"])
            if nid:
                rows.append({**row, "node_id": nid})
    else:
        for filename, data in payload.get("files", {}).items():
            path = Path(filename)
            try:
                relative = (path if path.is_absolute() else work / path).resolve().relative_to(work).as_posix()
            except ValueError:
                continue
            for line in data.get("lines", []):
                number = line.get("lineno")
                if not isinstance(number, int):
                    continue
                nid = node_at(relative, number)
                metrics = {k: v for k, v in line.items() if (k.startswith("n_cpu") or k.startswith("n_memory"))
                           and isinstance(v, (int, float))}
                if nid and any(metrics.values()):
                    rows.append({"node_id": nid, "file": relative, "line": number, "metrics": metrics})
                if len(rows) >= 200:
                    break
            if len(rows) >= 200:
                break
    return rows


def run_supplement(backend, project, analysis, plan, directory, *, timeout, python=None, source_root=None, excludes=()):
    from .study import copy_project
    output = directory / "supplements" / backend
    output.mkdir(parents=True, exist_ok=False)
    result = {"backend": backend, "status": "failed", "artifacts": [],
              "description": "별도 복사본에서 같은 입력을 다시 실행한 추가 관측입니다. 기본 추적과 같은 실행이 아닙니다."}
    executable = python or sys.executable
    env = os.environ.copy()
    env.pop("KNITCODE_AI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        check = execute([executable, "-c", "import importlib.util,importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1])); sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 2)", backend],
                        output, output, env, "", min(timeout, 15), "availability")
        if check["exit_code"] != 0:
            result.update(status="unavailable", error=f"선택한 Python에 {backend}가 없거나 실행할 수 없습니다. 선택 의존성을 설치하세요.")
        else:
            result["version"] = (output / "availability-stdout.log").read_text(encoding="utf-8").strip()[:100]
            work = copy_project(project, output / "workspace", analysis, [directory.parents[1]])
            for fixture in plan["fixtures"]:
                target = work / fixture["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(fixture["content"], encoding="utf-8")
            spec = launch_spec(analysis, plan["entry"].split("::", 1)[0])
            config = output / "launch.json"
            config.write_text(to_json({"root": str(work), "spec": spec, "plan": plan}), encoding="utf-8")
            worker = str(Path(__file__).with_name("_supplement_worker.py"))
            if backend == "dynapyt":
                env["KNITCODE_DYNAPYT_ROOT"] = str(work)
                raw = output / "dynapyt-events.json"
                env["KNITCODE_DYNAPYT_OUTPUT"] = str(raw)
                session = uuid.uuid4().hex
                env.update(DYNAPYT_SESSION_ID=session, TMP=str(output), TEMP=str(output), TMPDIR=str(output))
                (output / f"dynapyt_analyses-{session}.txt").write_text("knitcode_analyzer_v2.dynapyt_hooks.FlowAnalysis\n", encoding="utf-8")
                setup = execute([executable, "-m", "dynapyt.run_instrumentation", "--directory", str(work),
                                 "--analysis", "knitcode_analyzer_v2.dynapyt_hooks.FlowAnalysis"], output, output, env, "", timeout, "instrumentation")
                if setup["exit_code"] != 0:
                    raise ValueError("DynaPyt 계측 실패. instrumentation-stderr.log를 확인하세요.")
                command = [executable, "-u", worker, str(config)]
            else:
                raw = output / "scalene-profile.json"
                command = [executable, "-m", "scalene", "run", "--memory", "--profile-all", "--profile-only", str(work), "-o", str(raw), worker, str(config)]
            status = execute(command, output, work, env, plan["stdin"], timeout, "execution")
            if backend == "scalene" and status["exit_code"] != 0 and not raw.exists() and not status["timeout"]:
                result["memory_attempt"] = status
                # Windows allocators can fail before profiling starts. Keep that failure
                # visible and do one separately identified CPU-only run on a fresh copy.
                work = copy_project(project, output / "cpu-workspace", analysis, [directory.parents[1]])
                for fixture in plan["fixtures"]:
                    target = work / fixture["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(fixture["content"], encoding="utf-8")
                config.write_text(to_json({"root": str(work), "spec": spec, "plan": plan}), encoding="utf-8")
                raw = output / "scalene-cpu-profile.json"
                command = [executable, "-m", "scalene", "run", "--cpu-only", "--profile-all", "--profile-only", str(work), "-o", str(raw), worker, str(config)]
                status = execute(command, output, work, env, plan["stdin"], timeout, "cpu-execution")
                result["description"] += " 메모리 분석 실패 후 새 복사본에서 CPU만 한 차례 추가 실행했습니다."
            result["execution"] = status
            if raw.is_file() and raw.stat().st_size < 20 * 1024 * 1024:
                payload = json.loads(raw.read_text(encoding="utf-8"))
                rows = normalize(backend, payload, analysis, work)
                complete = status["exit_code"] == 0 and not status["timeout"] and not payload.get("truncated", False) and bool(rows) and "memory_attempt" not in result
                result.update(status="complete" if complete else "partial", observations=rows,
                              observation_limit=200, event_limit=10000 if backend == "dynapyt" else None)
            else:
                result.update(status="failed", error="추가 도구 결과가 없거나 20MiB 한도를 초과했습니다. 실행 로그를 확인하세요.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status="failed", error=str(error))
    result["artifacts"] = [p.relative_to(directory).as_posix() for p in sorted(output.iterdir())
                           if p.is_file() and p.name not in {"launch.json"} and not p.name.startswith("dynapyt_analyses-")]
    summary = output / "summary.json"
    result["artifacts"].append(summary.relative_to(directory).as_posix())
    summary.write_text(to_json(result), encoding="utf-8")
    return result
