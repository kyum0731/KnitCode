"""Guarded report replacement with a final, hash-verified completion manifest."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .analyzer import validate_result
from .reports import render_html, render_markdown

ARTIFACTS = ("analysis.json", "report.md", "report.html")


def to_json(result):
    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def validate_output_directory(directory, project, source):
    path = Path(directory).expanduser()
    if path.is_symlink() or path.is_junction():
        raise ValueError("출력 디렉터리로 링크를 사용할 수 없습니다.")
    directory = path.resolve()
    if directory in {Path(project).resolve(), Path(source).resolve()} or Path(project).resolve().is_relative_to(directory):
        raise ValueError("출력 디렉터리를 프로젝트·소스 루트 또는 프로젝트의 상위 경로로 지정할 수 없습니다.")
    if directory.exists() and not directory.is_dir():
        raise ValueError("출력 경로가 디렉터리가 아닙니다.")
    manifest_path = directory / "manifest.json"
    for name in (*ARTIFACTS, "manifest.json", ".knitcode.lock"):
        target = directory / name
        if target.is_symlink() or target.is_junction() or (target.exists() and not target.is_file()):
            raise ValueError(f"출력 파일로 링크 또는 디렉터리를 덮어쓸 수 없습니다: {name}")
    existing = [name for name in ARTIFACTS if (directory / name).exists()]
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("producer") != "knitcode_analyzer_v2" or manifest.get("project_root") != Path(project).resolve().as_posix():
                raise ValueError("다른 프로젝트 또는 사용자 manifest를 덮어쓸 수 없습니다.")
            for name in existing:
                expected = manifest["artifacts"].get(name)
                if not expected or hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                    raise ValueError(f"수정되었거나 불완전한 기존 산출물입니다: {name}. 새 출력 디렉터리를 사용하세요.")
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
            raise ValueError("유효한 KnitCode manifest가 아닙니다.") from error
    elif existing:
        raise ValueError("출력 이름과 같은 사용자 파일이 있습니다. 비어 있는 결과 디렉터리를 선택하세요.")
    return directory


def write_report(result, directory, *, runtime=None, explanation=None, study=None):
    validate_result(result)
    project = Path(result["metadata"]["project_root"])
    source = project / result["metadata"]["source_root"]
    directory = validate_output_directory(directory, project, source)
    markdown = render_markdown(result, runtime=runtime)
    if runtime is not None:
        from .reports.project import render_review_markdown
        markdown += render_review_markdown(runtime.get("review"))
        from .reports.runtime import render_runtime_markdown
        markdown += render_runtime_markdown(result, runtime)
    from .reports.project import render_project_ai_markdown
    markdown += render_project_ai_markdown(runtime.get("ai") if runtime else explanation)
    if explanation is not None or runtime and runtime.get("ai"):
        markdown = markdown.replace("** · AI 미사용", "** · AI 보완 설명 별도 포함", 1)
    page = render_html(result, runtime=runtime, explanation=explanation)
    if study is not None:
        from .reports.study import render_study_html, render_study_markdown
        page = page.replace('</header>', '</header>' + render_study_html(study), 1)
        page = page.replace('<title>KnitCode · 프로젝트 분석</title>', '<title>KnitCode · AI·동적 프로젝트 이해</title>', 1)
        page = page.replace('Local Python Analysis / No AI', 'Local Python Analysis / AI Planning &amp; Dynamic Study', 1)
        page = page.replace('<nav>', '<nav><a href="#study">프로젝트 이해 안내</a><a href="#attempts">AI 입력과 실행 기록</a>', 1)
        page = page.replace('원자료: run.json · summary.json · events.jsonl · stdout.log · stderr.log · 실행 무결성: run-manifest.json',
                            '마지막 기본 실행의 원자료·로그·run-manifest.json은 위 실행 기록의 개별 동적 보고서 폴더에서 확인하세요.')
        page = page.replace('함수는 명시적으로 선택해야 하며 필요한 값을 자동 생성하지 않습니다.',
                            '이 통합 분석에서는 AI가 후보와 합성 입력을 계획했습니다. 실제 선택은 위 실행 기록에서 확인하세요.')
        markdown = render_study_markdown(study) + '\n' + markdown
        markdown = markdown.replace('** · AI 미사용', '** · AI 입력 계획·프로젝트 안내 포함', 1)
    texts = {"analysis.json": to_json(result), "report.md": markdown, "report.html": page}
    manifest = {"producer": "knitcode_analyzer_v2", "schema_version": result["schema_version"],
                "project_root": project.as_posix(), "snapshot_id": result["metadata"]["snapshot_id"],
                "artifacts": {name: hashlib.sha256(value.encode("utf-8")).hexdigest() for name, value in texts.items()}}
    texts["manifest.json"] = to_json(manifest)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".knitcode.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise ValueError("동일 출력 디렉터리에 다른 저장 작업이 있습니다. 중단된 작업이면 잠금 파일을 확인하세요.") from error
    staged, replaced, backups = {}, [], {}
    try:
        handle.write(str(os.getpid()))
        handle.close()
        validate_output_directory(directory, project, source)
        for name, value in texts.items():
            destination = directory / name
            backups[name] = destination.read_bytes() if destination.exists() else None
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=directory,
                                             prefix=".knitcode-", suffix=".tmp", delete=False) as stream:
                staged[name] = Path(stream.name)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        # The manifest is deliberately last. Consumers verify every artifact hash.
        for name in texts:
            os.replace(staged[name], directory / name)
            replaced.append(name)
    except BaseException:
        # Best effort rollback; an old manifest cannot validate mixed artifacts.
        for name in reversed(replaced):
            try:
                if backups[name] is None:
                    (directory / name).unlink(missing_ok=True)
                else:
                    (directory / name).write_bytes(backups[name])
            except OSError:
                pass
        raise
    finally:
        handle.close()
        for path in staged.values():
            path.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return {name: (directory / name).as_posix() for name in texts}


def verify_manifest(directory):
    directory = Path(directory)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        return (manifest["producer"] == "knitcode_analyzer_v2" and set(manifest["artifacts"]) == set(ARTIFACTS)
                and all(hashlib.sha256((directory / n).read_bytes()).hexdigest() == manifest["artifacts"][n] for n in ARTIFACTS)
                and json.loads((directory / "analysis.json").read_text(encoding="utf-8"))["metadata"]["snapshot_id"] == manifest["snapshot_id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
