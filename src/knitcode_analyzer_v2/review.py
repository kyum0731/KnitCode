"""AI post-analysis of saved static and dynamic reports; never executes the target."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import uuid

from .exporter import to_json, validate_output_directory, write_report
from .project_summary import explain_project
from .runtime import decode_events
from .runtime_trace import summarize
from .saved_reports import (RUN_FILES, load_bundle, load_static_report, report_directory,
                            require_matching_snapshots, validate_snapshot)


REVIEW_FILES = {"analysis.json", "runtime-analysis.json", "report.html", "report.md", "manifest.json",
                "run.json", "summary.json", "events.jsonl", "ai-explanation.json", "review.json"}


def load_runtime_report(value):
    directory = report_directory(value)
    manifest, contents = load_bundle(directory, "run-manifest.json", "knitcode_runtime",
                                     RUN_FILES, {"ai-explanation.json"})
    analysis = json.loads(contents["analysis.json"])
    run = json.loads(contents["run.json"])
    summary = json.loads(contents["summary.json"])
    validate_snapshot(analysis)
    if run["runtime_schema_version"] != "1.1" or summary["runtime_schema_version"] != "1.1":
        raise ValueError("AI 후처리는 동적 결과 스키마 1.1을 지원합니다.")
    if (not manifest["snapshot_id"] == run["snapshot_id"] == summary["snapshot_id"] == analysis["metadata"]["snapshot_id"] or
            not manifest["run_id"] == run["run_id"] == summary["run_id"] or
            run["project_root"] != analysis["metadata"]["project_root"]):
        raise ValueError("동적 결과의 실행 ID·프로젝트·스냅샷 연결이 다릅니다.")
    if ("ai_status" in run) != ("ai-explanation.json" in contents):
        raise ValueError("동적 결과의 AI 산출물 목록이 다릅니다.")
    if not isinstance(run["max_events"], int) or not 1 <= run["max_events"] <= 500000:
        raise ValueError("동적 결과의 이벤트 한도가 잘못되었습니다.")
    events, invalid = decode_events(io.BytesIO(contents["events.jsonl"]), run["max_events"])
    if invalid and "incomplete_event_stream" not in run["limitations"]:
        raise ValueError("불완전한 이벤트 스트림이 실행 상태에 기록되지 않았습니다.")
    # Recompute aggregates from the saved observations, never from live source.
    expected = summarize(analysis, events)
    if any(summary.get(key) != value for key, value in expected.items() if key != "limitations"):
        raise ValueError("저장된 동적 요약과 이벤트 기록이 일치하지 않습니다.")
    if summary["limitations"] != run["limitations"]:
        raise ValueError("동적 요약과 실행의 제한 정보가 다릅니다.")
    return directory, analysis, {"run": run, "summary": summary, "events": events}, manifest, contents


def review_reports(static_report, runtime_report, *, ai_options, report_dir=None):
    static_dir, analysis, static_manifest = load_static_report(static_report)
    runtime_dir, runtime_analysis, runtime, run_manifest, contents = load_runtime_report(runtime_report)
    require_matching_snapshots(analysis, runtime_analysis)
    root = Path(analysis["metadata"]["project_root"])
    base = validate_output_directory(report_dir or Path.cwd() / "knitcode-reports", root,
                                     root / analysis["metadata"]["source_root"])
    if (base / "ai").is_symlink() or (base / "ai").is_junction():
        raise ValueError("AI 출력 디렉터리에 링크를 사용할 수 없습니다.")
    review_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = base / "ai" / review_id
    for source_dir in (static_dir, runtime_dir):
        if directory.is_relative_to(source_dir):
            raise ValueError("AI 결과는 입력 보고서 폴더 밖에 저장하세요.")
    provenance = {"review_schema_version": "1.0", "review_id": review_id,
                  "created_at": datetime.now(timezone.utc).isoformat(), "target_reexecuted": False,
                  "snapshot_id": analysis["metadata"]["snapshot_id"], "run_id": runtime["run"]["run_id"],
                  "static_report": {"directory": str(static_dir), "artifacts": static_manifest["artifacts"]},
                  "runtime_report": {"directory": str(runtime_dir), "artifacts": run_manifest["artifacts"]}}
    # Check destination writability before sending any request. Inputs are never changed.
    directory.mkdir(parents=True, exist_ok=False)
    runtime["ai"] = explain_project(analysis, runtime=runtime, options=ai_options)
    provenance["ai_status"] = runtime["ai"]["status"]
    runtime["review"] = provenance
    for name in ("run.json", "summary.json", "events.jsonl"):
        (directory / name).write_bytes(contents[name])
    (directory / "runtime-analysis.json").write_bytes(contents["analysis.json"])
    (directory / "ai-explanation.json").write_text(to_json(runtime["ai"]), encoding="utf-8")
    (directory / "review.json").write_text(to_json(provenance), encoding="utf-8")
    write_report(analysis, directory, runtime=runtime)
    manifest = {"producer": "knitcode_review", "review_id": review_id, "snapshot_id": provenance["snapshot_id"],
                "run_id": provenance["run_id"],
                "artifacts": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in sorted(REVIEW_FILES)}}
    (directory / "review-manifest.json").write_text(to_json(manifest), encoding="utf-8")
    return directory, provenance


def verify_review(directory):
    try:
        manifest, contents = load_bundle(Path(directory), "review-manifest.json", "knitcode_review", REVIEW_FILES)
        review = json.loads(contents["review.json"])
        return all(manifest[key] == review[key] for key in ("review_id", "snapshot_id", "run_id"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="저장된 정적·동적 결과를 이용한 AI 후처리. 대상 프로그램을 재실행하지 않습니다.")
    parser.add_argument("--static-report", required=True, help="정적 보고서 폴더 또는 report.html")
    parser.add_argument("--runtime-report", required=True, help="동적 보고서 폴더 또는 report.html")
    parser.add_argument("--ai", required=True, choices=("api", "ollama"))
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--ai-timeout", type=int, default=60)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        from .ai import endpoint_url
        local = args.ai == "ollama"
        base_url = args.base_url or os.environ.get("KNITCODE_OLLAMA_BASE_URL" if local else "KNITCODE_AI_BASE_URL") or ("http://127.0.0.1:11434" if local else None)
        model = args.model or os.environ.get("KNITCODE_OLLAMA_MODEL" if local else "KNITCODE_AI_MODEL")
        if not base_url or not model:
            raise ValueError("AI 서버 주소와 모델을 옵션 또는 환경 변수로 지정하세요.")
        endpoint, _ = endpoint_url(base_url, args.ai)
        options = {"provider": args.ai, "base_url": base_url, "model": model,
                   "api_key": "" if local else os.environ.get("KNITCODE_AI_API_KEY", ""),
                   "timeout": args.ai_timeout, "num_ctx": args.num_ctx}
        print(f"AI 전송 대상: {endpoint} · 모델: {model}. 저장된 정적·동적 요약만 전송하며 대상은 재실행하지 않습니다.", file=sys.stderr)
        directory, result = review_reports(args.static_report, args.runtime_report, ai_options=options, report_dir=args.report_dir)
        print(f"AI 보완 보고서: {directory / 'report.html'}\nAI 상태: {result['ai_status']} · 대상 재실행 없음", file=sys.stderr)
        return 0 if result["ai_status"] == "complete" else 3
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"knitcode review: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
