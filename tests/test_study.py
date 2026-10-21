import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
import unittest
from unittest.mock import patch

from knitcode_analyzer_v2 import analyze_project
from knitcode_analyzer_v2.ai import AIError
from knitcode_analyzer_v2.exporter import verify_manifest, write_report
from knitcode_analyzer_v2.runtime import run_project, verify_run
from knitcode_analyzer_v2.study import study_project, verify_study
from knitcode_analyzer_v2.study_context import build_study_context, validate_plan, validate_guide
from knitcode_analyzer_v2.supplements import normalize
from .helpers import test_directory, write


def plan(context, entry=None, **kwargs):
    value = {"action": "execute", "entry": entry or context["allowed_candidates"][0]["selector"],
             "args": [], "call_args": [], "call_kwargs": {}, "stdin": "", "fixtures": [],
             "reason": "작은 합성 입력으로 호출을 확인합니다.", "evidence_ids": ["E1"]}
    value.update(kwargs)
    return value


def guide(context):
    claim = {"text": "입력을 처리하는 예제 프로젝트입니다.", "kind": "inference", "evidence_ids": ["E1"]}
    return {"purpose": dict(claim), "directories": [{"path": context["allowed_directories"][0], **claim}],
            "files": [{"path": context["allowed_files"][0], **claim}], "flow": [dict(claim)],
            "reading_order": [{"path": context["allowed_files"][0], **claim}], "limitations": ["실제 업무 입력은 확인하지 않았습니다."]}


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(test_directory())
        self.project = self.root / "project"
        self.project.mkdir()
        self.output = self.root / "reports"
        self.options = {"base_url": "http://localhost:1234/v1", "model": "test"}
        self.contexts = []

    def mock_ai(self, planner):
        def request(req, **kwargs):
            payload = json.loads(req.data)
            context = json.loads(payload["messages"][1]["content"])
            self.contexts.append(context)
            answer = planner(context) if "Plan ONE" in payload["messages"][0]["content"] else guide(context)
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}).encode())
        mock = self.enterContext(patch("knitcode_analyzer_v2.ai.build_opener"))
        mock.return_value.open.side_effect = request
        return mock

    def run_study(self, **kwargs):
        return study_project(self.project, ai_options=self.options, report_dir=self.output, timeout=3, **kwargs)

    def test_ai_stdin_retry_and_original_preserved(self):
        source = write(self.project, "odd.py", "from pathlib import Path\ndef execute():\n    n = int(input('횟수: '))\n    Path('marker').write_text(str(n))\nexecute()")
        before = source.read_bytes()
        self.mock_ai(lambda c: plan(c, "odd.py", stdin="wrong\n" if not c["attempts"] else "3\n"))
        directory, result = self.run_study(max_attempts=2)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.project / "marker").exists())
        self.assertEqual(result["attempts"][0]["feedback"]["execution_status"], "failed")
        self.assertEqual(result["attempts"][1]["feedback"]["execution_status"], "finished")
        self.assertIn("builtins.ValueError", self.contexts[1]["attempts"][0]["exception_types"])
        self.assertTrue(verify_manifest(directory))
        for attempt in result["attempts"]:
            self.assertTrue(verify_run((directory / attempt["report"]).parent))
        page = (directory / "report.html").read_text(encoding="utf-8")
        self.assertIn("디렉터리별 역할과 연결", page)
        self.assertIn("처음 읽을 때의 순서", page)
        manifest = json.loads((directory / "study-manifest.json").read_bytes())
        self.assertTrue(all(hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected for name, expected in manifest["artifacts"].items()))
        self.assertTrue(verify_study(directory))
        input_sites = [e for e in self.contexts[0]["evidence"] if e["kind"] == "input_sites"]
        self.assertIn("int(input(", json.dumps(input_sites))
        run_dir = (directory / result["attempts"][1]["report"]).parent
        (run_dir / "stdout.log").write_text("tampered", encoding="utf-8")
        self.assertFalse(verify_study(directory))

    def test_ai_function_arguments_and_keyword_arguments(self):
        write(self.project, "maths.py", "def process(n: int, *, scale: int):\n    print(n * scale)")
        self.mock_ai(lambda c: plan(c, "maths.py::process", call_args=[3], call_kwargs={"scale": 4}))
        directory, result = self.run_study(max_attempts=1)
        self.assertEqual(result["status"], "complete")
        run_dir = (directory / result["attempts"][0]["report"]).parent
        self.assertEqual((run_dir / "stdout.log").read_text().strip(), "12")

    def test_saved_static_src_package_and_directory_connections(self):
        write(self.project, "src/strange_package/__init__.py", "")
        write(self.project, "src/strange_package/services/__init__.py", "")
        write(self.project, "src/strange_package/services/task.py", "def calculate(n): return n * 2")
        write(self.project, "src/strange_package/__main__.py", "from .services.task import calculate\nprint(calculate(int(input())))")
        analysis = analyze_project(self.project, source_root="src")
        static_dir = self.root / "static"
        write_report(analysis, static_dir)
        self.mock_ai(lambda c: plan(c, "src/strange_package/__main__.py", stdin="4\n"))
        directory, result = self.run_study(max_attempts=1, source_root="src", static_report=static_dir)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(verify_study(directory))
        run_dir = (directory / result["attempts"][0]["report"]).parent
        self.assertEqual((run_dir / "stdout.log").read_text().strip(), "8")
        facts = [e["facts"] for e in self.contexts[0]["evidence"] if e["kind"] == "directory_structure"]
        self.assertTrue(any("src/strange_package/services" in f["outgoing_directories"] for f in facts))

    def test_ai_cli_arguments_and_fixture(self):
        write(self.project, "load.py", "import argparse, json\np = argparse.ArgumentParser()\np.add_argument('--data', required=True)\na = p.parse_args()\nprint(json.load(open(a.data))['count'])")
        self.mock_ai(lambda c: plan(c, "load.py", args=["--data", ".knitcode-inputs/test.json"],
                                   fixtures=[{"path": ".knitcode-inputs/test.json", "content": '{"count":7}'}]))
        directory, result = self.run_study(max_attempts=1)
        self.assertEqual(result["status"], "complete")
        run_dir = (directory / result["attempts"][0]["report"]).parent
        self.assertEqual((run_dir / "stdout.log").read_text().strip(), "7")
        self.assertFalse((self.project / ".knitcode-inputs").exists())
        self.assertTrue(any(e["kind"] == "input_sites" for e in self.contexts[0]["evidence"]))

    def test_stop_for_missing_environment_does_not_execute(self):
        write(self.project, "remote.py", "print('requires credentials')")
        self.mock_ai(lambda c: {"action": "stop", "reason": "서버 자격증명이 필요합니다.", "evidence_ids": []})
        with patch("knitcode_analyzer_v2.study.run_project") as run:
            directory, result = self.run_study(max_attempts=3)
        run.assert_not_called()
        self.assertEqual(result["status"], "partial")
        self.assertIn("자격증명", result["stop_reason"])
        self.assertTrue((directory / "report.html").exists())

    def test_repeated_plan_is_not_executed_again(self):
        write(self.project, "simple.py", "print(1)")
        self.mock_ai(lambda c: plan(c, "simple.py"))
        _, result = self.run_study(max_attempts=2)
        self.assertEqual([a["status"] for a in result["attempts"]], ["executed", "failed"])
        self.assertIn("반복", result["attempts"][1]["error"])

    def test_ai_failure_retains_static_and_final_report(self):
        write(self.project, "simple.py", "print(1)")
        with patch("knitcode_analyzer_v2.ai.build_opener") as mock:
            mock.return_value.open.side_effect = TimeoutError()
            directory, result = self.run_study(max_attempts=1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(verify_manifest(directory / "static"))
        self.assertTrue(verify_manifest(directory))

    def test_plan_validation_rejects_invented_entry_extra_commands_and_path_escape(self):
        write(self.project, "simple.py", "print(1)")
        context = build_study_context(analyze_project(self.project))
        variants = [{"entry": "elsewhere.py"}, {"command": "delete"}, {"args": "not-array"},
                    {"fixtures": [{"path": ".knitcode-inputs/../x.py", "content": "print(1)"}]},
                    {"fixtures": [{"path": ".knitcode-inputs/x.py", "content": "print(1)"}]},
                    {"evidence_ids": ["E999999"]}, {"call_args": [float("nan")]}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(AIError):
                validate_plan(plan(context, **changes), context)
        wrong = guide(context)
        wrong["directories"][0]["path"] = "nonexistent"
        with self.assertRaises(AIError):
            validate_guide(wrong, context)

    def test_stdin_runtime_and_ai_key_not_inherited(self):
        write(self.project, "read.py", "import os\nprint(input())\nprint(os.environ.get('KNITCODE_AI_API_KEY', 'absent'))")
        with patch.dict(os.environ, {"KNITCODE_AI_API_KEY": "do-not-pass"}):
            directory, result = run_project(self.project, script="read.py", stdin_text="안녕\n", report_dir=self.output)
        self.assertEqual(result["run"]["execution_status"], "finished")
        self.assertEqual((directory / "stdout.log").read_text(encoding="utf-8").splitlines(), ["안녕", "absent"])
        self.assertTrue(verify_run(directory))

    def test_external_location_mapping_does_not_fabricate_edges(self):
        write(self.project, "example.py", "def f():\n    return 1\nf()")
        analysis = analyze_project(self.project)
        payload = {"events": [{"event": "if", "file": "example.py", "line": 2, "outcome": True, "count": 4}]}
        rows = normalize("dynapyt", payload, analysis, self.project)
        node = next(n for n in analysis["nodes"] if n["qualified_name"] == "f")
        self.assertEqual(rows[0]["node_id"], node["id"])
        self.assertEqual(rows[0]["count"], 4)
        self.assertNotIn("edges", rows[0])

    def test_readme_evidence_is_bounded_and_marked_unverified(self):
        write(self.project, "library.py", "def f(n: int): return n")
        write(self.project, "README.md", "Project purpose\n" + "X" * 10000)
        context = build_study_context(analyze_project(self.project))
        doc = next(e for e in context["evidence"] if e["kind"] == "author_documentation")
        self.assertFalse(doc["facts"]["verified"])
        self.assertEqual(len(doc["facts"]["excerpt"]), 4000)

    def test_supplement_evidence_reaches_guide_and_report_is_escaped(self):
        write(self.project, "simple.py", "def f(): return 1\nf()")
        self.mock_ai(lambda c: plan(c, "simple.py", reason="<script>alert(1)</script>"))
        def supplement(backend, root, analysis, *args, **kwargs):
            node = next(n for n in analysis["nodes"] if n["qualified_name"] == "f")
            return {"backend": backend, "status": "partial", "artifacts": [],
                    "observations": [{"file": "simple.py", "line": 1, "node_id": node["id"], "event": "function", "count": 1}]}
        with patch("knitcode_analyzer_v2.supplements.run_supplement", side_effect=supplement):
            directory, result = self.run_study(max_attempts=1, supplements=["dynapyt"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any(e["kind"] == "supplement_observation" for e in self.contexts[-1]["evidence"]))
        page = (directory / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("추가 도구: dynapyt", page)
        self.assertNotIn("Local Python Analysis / No AI", page)
        self.assertNotIn("AI 미사용", (directory / "report.md").read_text(encoding="utf-8"))
        ids = re.findall(r'\bid="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(re.findall(r'href="#([^"]+)"', page)) <= set(ids))
        self.assertTrue(verify_study(directory))


if __name__ == "__main__":
    unittest.main()
