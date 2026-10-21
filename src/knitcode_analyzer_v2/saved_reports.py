"""Read verified saved artifacts without importing or reading project source."""

import hashlib
import json
from pathlib import Path

from .analyzer import validate_result
from .models import digest


STATIC_FILES = {"analysis.json", "report.html", "report.md"}
RUN_FILES = STATIC_FILES | {"manifest.json", "run.json", "summary.json", "events.jsonl", "stdout.log", "stderr.log"}


def report_directory(value):
    path = Path(value).expanduser()
    if path.is_file() and path.name in {"report.html", "analysis.json", "run.json"}:
        path = path.parent
    if not path.is_dir():
        raise ValueError("보고서 폴더 또는 report.html 경로가 필요합니다: " + str(value))
    return path.resolve()


def load_bundle(directory, manifest_name, producer, required, optional=()):
    """Hash the very bytes subsequently consumed; manifest names cannot select paths."""
    manifest_path = directory / manifest_name
    if manifest_path.is_symlink() or manifest_path.is_junction():
        raise ValueError("보고서 manifest에 링크를 사용할 수 없습니다.")
    manifest = json.loads(manifest_path.read_bytes())
    names = set(manifest["artifacts"])
    if manifest.get("producer") != producer or not required <= names or not names <= required | set(optional):
        raise ValueError("보고서 manifest 형식 또는 산출물 목록이 다릅니다.")
    contents = {}
    for name in sorted(names):
        path = directory / name
        if path.is_symlink() or path.is_junction():
            raise ValueError("보고서 산출물에 링크를 사용할 수 없습니다: " + name)
        contents[name] = path.read_bytes()
        if hashlib.sha256(contents[name]).hexdigest() != manifest["artifacts"][name]:
            raise ValueError("보고서 무결성 검사 실패: " + name)
    return manifest, contents


def validate_snapshot(analysis):
    validate_result(analysis)
    m = analysis["metadata"]
    expected = digest([m["analyzer_version"], analysis["schema_version"], m["python_version"],
                       m["options"], analysis["files"], m["analysis_status"], analysis["nodes"], analysis["edges"]])
    if expected != m["snapshot_id"]:
        raise ValueError("정적 분석 스냅샷 ID가 내용과 다릅니다.")


def load_static_report(value):
    directory = report_directory(value)
    manifest, contents = load_bundle(directory, "manifest.json", "knitcode_analyzer_v2", STATIC_FILES)
    analysis = json.loads(contents["analysis.json"])
    validate_snapshot(analysis)
    if (manifest["snapshot_id"] != analysis["metadata"]["snapshot_id"] or
            manifest["project_root"] != analysis["metadata"]["project_root"]):
        raise ValueError("정적 보고서의 프로젝트 또는 스냅샷 정보가 다릅니다.")
    return directory, analysis, manifest


def require_matching_snapshots(static, dynamic):
    a, b = static["metadata"], dynamic["metadata"]
    if a["project_root"] != b["project_root"]:
        raise ValueError("정적·동적 보고서의 대상 프로젝트가 다릅니다.")
    if a["snapshot_id"] != b["snapshot_id"]:
        raise ValueError("정적·동적 스냅샷이 다릅니다. 같은 소스와 분석 옵션으로 생성한 보고서를 지정하세요.")
