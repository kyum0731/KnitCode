"""Bounded AI planning -> real observations -> revised planning -> project guide."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from .ai import AIError, endpoint_url, explain
from .analyzer import analyze_project
from .exporter import to_json, validate_output_directory, write_report
from .models import digest
from .project_summary import build_project_context
from .runtime import run_project, validate_call_arguments
from .saved_reports import load_static_report, require_matching_snapshots
from .scanner import EXCLUDED
from .study_context import build_study_context


def copy_project(root, destination, analysis, excluded):
    """Bounded project copy. No links, environment directories or secret config files."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root.is_relative_to(destination):
        raise ValueError("실행 복사본 경로는 원본과 분리되어야 합니다.")
    exclusions = [Path(p).resolve() for p in excluded] + [destination]
    selected = {f["path"] for f in analysis["files"]}
    pending, total = [], 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not d.startswith(".") and
                   not (parent / d).is_symlink() and not (parent / d).is_junction() and
                   not any((parent / d).resolve().is_relative_to(p) for p in exclusions)]
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if (path.is_symlink() or path.is_junction() or name.startswith(".") or
                    path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".pyc"} or
                    any(path.resolve().is_relative_to(p) for p in exclusions)):
                continue
            if path.suffix == ".py" and relative not in selected:
                continue
            total += path.stat().st_size
            pending.append((path, relative))
            if total > 50 * 1024 * 1024 or len(pending) > 5000:
                raise ValueError("자동 실행 복사본 한도(50MiB/5000파일)를 초과했습니다. 분석 범위를 줄이세요.")
    destination.mkdir(parents=True, exist_ok=False)
    for path, relative in pending:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for file in analysis["files"]:
        path = destination / file["path"]
        if file["sha256"] and (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != file["sha256"]):
            raise ValueError("원본과 실행 복사본의 분석 대상 파일이 일치하지 않습니다: " + file["path"])
    return destination


def append_observation(context, analysis, runtime, attempt):
    packet = build_project_context(analysis, runtime=runtime)
    observations = [e for e in packet["evidence"] if e["kind"] in {"runtime_observation", "project_summary"}]
    for evidence in observations:
        evidence["id"] = "E" + str(len(context["evidence"]) + 1)
        evidence["attempt"] = attempt["number"]
        context["evidence"].append(evidence)
    context["execution_observed"] = True
    run, summary = runtime["run"], runtime["summary"]
    feedback = {"number": attempt["number"], "plan": attempt["plan"], "execution_status": run["execution_status"],
                "trace_status": run["trace_status"], "target_exit_code": run["target_exit_code"],
                "limitations": run["limitations"], "coverage": summary["function_coverage"],
                "exception_types": sorted({e["exception_type"] for e in summary["exceptions"]})[:20]}
    # Keep node IDs useful for planning, but bound large coverage lists.
    feedback["coverage"] = dict(feedback["coverage"])
    feedback["coverage"]["unobserved_node_ids"] = feedback["coverage"]["unobserved_node_ids"][:80]
    context["attempts"].append(feedback)
    return feedback


def study_project(project, *, ai_options, report_dir=None, static_report=None, source_root=None, excludes=(),
                  max_attempts=3, timeout=30, max_events=10000, python=None, supplements=()):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5 or not 0.1 <= timeout <= 300 or not 1 <= max_events <= 500000:
        raise ValueError("자동 실행은 최대 1~5회, 회당 0.1~300초, 이벤트 1~500000개 범위입니다.")
    if not set(supplements) <= {"dynapyt", "scalene"}:
        raise ValueError("지원하지 않는 추가 분석 도구입니다.")
    root = Path(project).resolve()
    base = validate_output_directory(report_dir or Path.cwd() / "knitcode-reports", root, root / (source_root or "."))
    if (base / "studies").is_symlink() or (base / "studies").is_junction():
        raise ValueError("studies 결과 디렉터리에 링크를 사용할 수 없습니다.")
    analysis = analyze_project(root, source_root=source_root, excludes=excludes, excluded_paths=(base,))
    if static_report:
        static_dir, previous, _ = load_static_report(static_report)
        require_matching_snapshots(previous, analysis)
    else:
        static_dir = None
    study_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = base / "studies" / study_id
    directory.mkdir(parents=True, exist_ok=False)
    # Always persist deterministic results before connecting to AI.
    write_report(analysis, directory / "static")
    record = {"study_schema_version": "1.0", "study_id": study_id, "snapshot_id": analysis["metadata"]["snapshot_id"],
              "created_at": datetime.now(timezone.utc).isoformat(), "project_root": str(root),
              "static_input": str(static_dir) if static_dir else None, "status": "running", "attempts": [],
              "max_attempts": max_attempts, "timeout_per_run": timeout, "supplements": [],
              "limitations": ["복사본 실행은 보안 샌드박스가 아닙니다. 외부 서비스·절대 경로·자식 프로세스 부작용을 차단하지 않습니다.",
                              "AI 입력은 합성 예시입니다. 자격증명·서비스·복잡한 객체·실시간 대화는 자동 해결하지 않습니다.",
                              "각 시도는 새 복사본에서 실행하며 호출 순서·시간은 시도별로 구분합니다."]}
    context, latest, latest_plan, observed, fingerprints = None, None, None, set(), set()
    try:
        context = build_study_context(analysis)
        for index in range(max_attempts):
            attempt = {"number": index + 1, "status": "planning"}
            record["attempts"].append(attempt)
            context["input_hash"] = digest({k: v for k, v in context.items() if k != "input_hash"})
            try:
                decision = explain(context, purpose="plan", **ai_options)
                (directory / f"plan-{index + 1}.json").write_text(to_json(decision), encoding="utf-8")
                plan = decision["answer"]
                attempt["plan"] = plan
                if plan["action"] == "stop":
                    attempt["status"] = "stopped"
                    record["stop_reason"] = plan["reason"]
                    break
                fingerprint = digest({k: v for k, v in plan.items() if k not in {"reason", "evidence_ids"}})
                if fingerprint in fingerprints:
                    raise ValueError("동일한 실행 계획이 반복되어 재실행하지 않았습니다.")
                fingerprints.add(fingerprint)
                candidate = next(c for c in context["allowed_candidates"] if c["selector"] == plan["entry"])
                if candidate["callable"]:
                    validate_call_arguments(analysis, candidate["node_id"], plan["call_args"], plan["call_kwargs"])
                work = copy_project(root, directory / "workspaces" / str(index + 1), analysis, [base])
                for fixture in plan["fixtures"]:
                    target = work / fixture["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("x", encoding="utf-8") as stream:
                        stream.write(fixture["content"])
                run_dir, runtime = run_project(work, entry_point=plan["entry"], args=plan["args"],
                    call_args=plan["call_args"] if candidate["callable"] else None,
                    call_kwargs=plan["call_kwargs"] if candidate["callable"] else None,
                    stdin_text=plan["stdin"], python=python, source_root=source_root, excludes=excludes,
                    report_dir=directory / "dynamic", timeout=timeout, max_events=max_events)
                if runtime["run"]["snapshot_id"] != analysis["metadata"]["snapshot_id"]:
                    raise ValueError("실행 복사본의 정적 스냅샷이 달라 통합하지 않았습니다.")
                attempt.update(status="executed", report=run_dir.relative_to(directory).as_posix() + "/report.html",
                               run_id=runtime["run"]["run_id"])
                attempt["feedback"] = append_observation(context, analysis, runtime, attempt)
                observed.update(runtime["summary"]["node_entries"])
                latest, latest_plan = runtime, plan
                if runtime["run"]["execution_status"] == "cancelled":
                    record["status"] = "cancelled"
                    break
            except (AIError, ValueError, OSError) as error:
                attempt.update(status="failed", error=str(error))
                context["attempts"].append({"number": index + 1, "error": str(error)})
            (directory / "study.json").write_text(to_json(record), encoding="utf-8")
        if latest_plan and supplements and record["status"] != "cancelled":
            from .supplements import run_supplement
            for backend in dict.fromkeys(supplements):
                result = run_supplement(backend, root, analysis, latest_plan, directory, timeout=timeout, python=python,
                                        source_root=source_root, excludes=excludes)
                record["supplements"].append(result)
                context["supplements"] = [{k: v for k, v in r.items() if k != "observations"} for r in record["supplements"]]
                nodes = {n["id"]: n for n in analysis["nodes"]}
                for row in result.get("observations", [])[:40]:
                    node = nodes[row["node_id"]]
                    context["evidence"].append({"id": "E" + str(len(context["evidence"]) + 1),
                        "node_id": node["id"], "file": node["file"], "range": node["range"],
                        "kind": "supplement_observation", "facts": {"backend": backend, "separate_execution": True, **row}})
        if record["status"] != "cancelled":
            context["input_hash"] = digest({k: v for k, v in context.items() if k != "input_hash"})
            try:
                guide = explain(context, purpose="guide", **ai_options)
            except (AIError, ValueError, OSError) as error:
                guide = {"status": "failed", "error": str(error), "context": context}
            (directory / "guide.json").write_text(to_json(guide), encoding="utf-8")
            record["guide"] = guide
            failures = any(a["status"] == "failed" or a.get("feedback", {}).get("execution_status") != "finished" or
                           a.get("feedback", {}).get("trace_status") != "complete_within_scope"
                           for a in record["attempts"] if a["status"] != "stopped")
            failures = failures or any(r["status"] != "complete" for r in record["supplements"])
            record["status"] = "complete" if latest and not failures and guide["status"] == "complete" else "partial"
    except (AIError, ValueError, OSError) as error:
        record.update(status="partial", error=str(error))
    except KeyboardInterrupt:
        record["status"] = "cancelled"
    functions = {n["id"] for n in analysis["nodes"] if n["type"] in {"function", "method"}}
    record["observed_functions"] = len(observed & functions)
    record["total_functions"] = len(functions)
    record["latest_graph_run"] = latest["run"]["run_id"] if latest else None
    (directory / "study.json").write_text(to_json(record), encoding="utf-8")
    write_report(analysis, directory, runtime=latest, study=record)
    # The top-level manifest seals nested result manifests, not mutable workspace copies.
    names = ["analysis.json", "report.html", "report.md", "manifest.json", "study.json", "static/manifest.json"]
    names += [p.name for p in sorted(directory.glob("plan-*.json"))]
    if (directory / "guide.json").exists():
        names.append("guide.json")
    names += [str(Path(a["report"]).parent / "run-manifest.json").replace("\\", "/")
              for a in record["attempts"] if a.get("report")]
    for extra in record["supplements"]:
        names += extra.get("artifacts", [])
    manifest = {"producer": "knitcode_study", "study_id": study_id,
                "artifacts": {n: hashlib.sha256((directory / n).read_bytes()).hexdigest() for n in names}}
    (directory / "study-manifest.json").write_text(to_json(manifest), encoding="utf-8")
    return directory, record


def verify_study(directory):
    from .exporter import verify_manifest
    from .runtime import verify_run
    directory = Path(directory).resolve()
    try:
        manifest = json.loads((directory / "study-manifest.json").read_bytes())
        if manifest["producer"] != "knitcode_study" or not verify_manifest(directory) or not verify_manifest(directory / "static"):
            return False
        record = json.loads((directory / "study.json").read_bytes())
        if manifest["study_id"] != record["study_id"]:
            return False
        for name, expected in manifest["artifacts"].items():
            path = (directory / name).resolve()
            if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        for attempt in record["attempts"]:
            if attempt.get("report"):
                path = (directory / attempt["report"]).resolve().parent
                if not path.is_relative_to(directory) or not verify_run(path):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="정적 분석 → AI 입력 계획 ↔ 동적 실행 → 프로젝트 이해 보고서")
    parser.add_argument("project")
    parser.add_argument("--ai", required=True, choices=("api", "ollama"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--ai-timeout", type=int, default=60)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--report-dir")
    parser.add_argument("--static-report")
    parser.add_argument("--source-root")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-events", type=int, default=10000)
    parser.add_argument("--python")
    parser.add_argument("--supplement", choices=("dynapyt", "scalene"), action="append", default=[])
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        local = args.ai == "ollama"
        url = args.base_url or os.environ.get("KNITCODE_OLLAMA_BASE_URL" if local else "KNITCODE_AI_BASE_URL") or ("http://127.0.0.1:11434" if local else None)
        model = args.model or os.environ.get("KNITCODE_OLLAMA_MODEL" if local else "KNITCODE_AI_MODEL")
        if not url or not model:
            raise ValueError("AI 서버 주소와 모델을 지정하세요.")
        endpoint, _ = endpoint_url(url, args.ai)
        options = {"provider": args.ai, "base_url": url, "model": model, "timeout": args.ai_timeout,
                   "num_ctx": args.num_ctx, "api_key": "" if local else os.environ.get("KNITCODE_AI_API_KEY", "")}
        print(f"AI 서버: {endpoint} · 모델: {model}. 구조·입력 구문·README 일부·실행 집계를 전송합니다.\nAI가 정한 합성 입력으로 복사본을 실제 실행합니다.", file=sys.stderr)
        directory, result = study_project(args.project, ai_options=options, report_dir=args.report_dir,
            static_report=args.static_report, source_root=args.source_root, excludes=args.exclude,
            max_attempts=args.max_attempts, timeout=args.timeout, max_events=args.max_events, python=args.python,
            supplements=args.supplement)
        print(f"프로젝트 이해 보고서: {directory / 'report.html'}\n상태: {result['status']}", file=sys.stderr)
        return 0 if result["status"] == "complete" else 130 if result["status"] == "cancelled" else 1
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"knitcode study: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
