import copy
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from knitcode_analyzer_v2 import analyze_project
from knitcode_analyzer_v2.exporter import verify_manifest
from knitcode_analyzer_v2.project_summary import build_project_context
from knitcode_analyzer_v2.runtime import main, plan_project, run_project, verify_run
from knitcode_analyzer_v2.runtime_candidates import discover_entry_points
from .helpers import test_directory, write


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(test_directory())
        self.project = self.root / "project"
        self.project.mkdir()
        self.output = self.root / "reports"

    def test_arbitrary_filename_uses_static_calls_and_cross_file_evidence(self):
        write(self.project, "launch_everything.py", "from operations import calculate\ncalculate(3)")
        write(self.project, "operations.py", "def calculate(n): return n*2")
        analysis = analyze_project(self.project)
        original = copy.deepcopy(analysis)
        candidates = discover_entry_points(analysis)
        entry = next(c for c in candidates if c["selector"] == "launch_everything.py")
        self.assertEqual(entry["confidence"], "structural")
        self.assertEqual(entry["reachable_nodes"], 1)
        self.assertTrue(entry["evidence_edge_ids"])
        self.assertEqual(analysis, original)
        directory, runtime = run_project(self.project, report_dir=self.output)
        self.assertEqual(runtime["run"]["script"], "launch_everything.py")
        self.assertEqual(runtime["run"]["target_exit_code"], 0)
        page = (directory / "report.html").read_text(encoding="utf-8")
        self.assertIn("프로젝트 구조와 실행 흐름 요약", page)
        self.assertLess(page.index('id="graph"'), page.index('id="runtime"'))
        self.assertIn("정적 근거 기반 실행 후보", page)
        self.assertTrue(verify_run(directory))

    def test_discover_html_never_executes_even_top_level_side_effects(self):
        write(self.project, "unusual.py", "from pathlib import Path\nPath('marker').write_text('executed')\ndef controller(x): return helper(x)\ndef helper(x): return x")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.project), "--discover", "--report-dir", str(self.output)]), 0)
        directory = next((self.output / "plans").iterdir())
        plan = json.loads((directory / "candidates.json").read_text(encoding="utf-8"))
        selectors = {c["selector"] for c in plan["candidates"]}
        self.assertIn("unusual.py", selectors)
        self.assertIn("unusual.py::controller", selectors)
        self.assertNotIn("unusual.py::helper", selectors)
        self.assertFalse((self.project / "marker").exists())
        self.assertFalse((directory / "events.jsonl").exists())
        self.assertIn("실행하지 않았습니다", (directory / "report.html").read_text(encoding="utf-8"))
        self.assertTrue(verify_manifest(directory))

    def test_function_candidate_requires_inputs_then_traces_execution(self):
        write(self.project, "workflow.py", "def dispatch(n, *, scale=2):\n    print(helper(n)*scale)\ndef helper(n): return n+1")
        with self.assertRaisesRegex(ValueError, "인자"):
            run_project(self.project, entry_point="workflow.py::dispatch", report_dir=self.output)
        self.assertFalse(self.output.exists())
        directory, runtime = run_project(self.project, entry_point="workflow.py::dispatch", call_args=[3], call_kwargs={"scale": 4}, report_dir=self.output)
        self.assertEqual(runtime["run"]["target_exit_code"], 0, (directory / "stderr.log").read_text())
        self.assertEqual((directory / "stdout.log").read_text().strip(), "16")
        self.assertEqual(runtime["summary"]["function_coverage"]["observed"], 2)
        self.assertTrue(verify_run(directory))

    def test_function_cli_package_relative_import_and_json_inputs(self):
        write(self.project, "src/pkg/__init__.py", "")
        write(self.project, "src/pkg/work.py", "from .part import calculate\ndef execute(n): print(calculate(n))")
        write(self.project, "src/pkg/part.py", "def calculate(n): return n*2")
        with redirect_stderr(io.StringIO()):
            code = main([str(self.project), "--entry", "src/pkg/work.py::execute", "--call-args", "[5]", "--report-dir", str(self.output)])
        self.assertEqual(code, 0)
        directory = next((self.output / "runs").iterdir())
        self.assertEqual((directory / "stdout.log").read_text().strip(), "10")
        for invalid in ("null", "{}", "[NaN]"):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(self.project), "--entry", "src/pkg/work.py::execute", "--call-args", invalid, "--report-dir", str(self.output)]), 2)

    def test_async_and_replaced_function_not_invoked(self):
        write(self.project, "library.py", "async def task(): pass\ndef victim(): print('must not run')\nvictim = print")
        with self.assertRaisesRegex(ValueError, "지원하지"):
            run_project(self.project, entry_point="library.py::task", report_dir=self.output)
        directory, runtime = run_project(self.project, entry_point="library.py::victim", report_dir=self.output)
        self.assertEqual(runtime["run"]["execution_status"], "failed")
        self.assertNotIn("must not run", (directory / "stdout.log").read_text())

    def test_default_never_connects_ai(self):
        write(self.project, "flow.py", "def call(): pass\ncall()")
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            directory, runtime = run_project(self.project, report_dir=self.output)
            build.assert_not_called()
        self.assertNotIn("ai", runtime)
        self.assertFalse((directory / "ai-explanation.json").exists())

    def test_definition_expressions_are_not_file_launch_evidence_and_cycles_finish(self):
        write(self.project, "units.py", "def prepare(): return 1\ndef first(value=prepare()): return second()\ndef second(): return first()")
        candidates = discover_entry_points(analyze_project(self.project))
        self.assertFalse(any(c["kind"] == "file" for c in candidates))
        write(self.project, "invoke.py", "from units import first\nfirst()")
        candidates = discover_entry_points(analyze_project(self.project))
        selected = next(c for c in candidates if c["selector"] == "invoke.py")
        self.assertEqual(selected["reachable_nodes"], 2)
        self.assertFalse(selected["reachability_capped"])

    def test_discover_cli_ollama_opt_in_and_cloud_key_is_not_forwarded(self):
        write(self.project, "library.py", "def task(): return 1")
        answer = {"claims": [{"text": "최상위 함수 후보입니다.", "kind": "inference", "evidence_ids": ["E1"]}],
                  "limitations": ["실행하지 않았습니다."], "suggested_relations": []}
        reply = io.BytesIO(json.dumps({"done": True, "done_reason": "stop", "message": {"content": json.dumps(answer)}}).encode())
        with patch.dict("os.environ", {"KNITCODE_AI_API_KEY": "never-send"}), patch("knitcode_analyzer_v2.ai.build_opener") as build, redirect_stderr(io.StringIO()):
            build.return_value.open.return_value = reply
            code = main([str(self.project), "--discover", "--ai", "ollama", "--model", "local-test", "--report-dir", str(self.output)])
            request = build.return_value.open.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertIsNone(request.get_header("Authorization"))
        directory = next((self.output / "plans").iterdir())
        self.assertEqual(json.loads((directory / "candidates.json").read_text())["ai_status"], "complete")

    def response(self, text="관측한 흐름을 설명합니다.", evidence="E1"):
        answer = {"claims": [{"text": text, "kind": "observation", "evidence_ids": [evidence]}],
                  "limitations": ["한 번의 실행에 한정됩니다."], "suggested_relations": []}
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}).encode())

    def test_ai_runtime_facts_escaping_privacy_and_manifest(self):
        write(self.project, "launcher.py", "import sys\ndef run(): print(sys.argv[1])\nrun()")
        options = {"base_url": "https://example.invalid/v1", "model": "test-model", "api_key": "secret-key"}
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response('<script>unsafe</script>')
            directory, runtime = run_project(self.project, args=["private-input"], report_dir=self.output, ai_options=options)
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        context = runtime["ai"]["context"]
        self.assertTrue(context["execution_observed"])
        self.assertTrue(any(e["kind"] == "runtime_observation" for e in context["evidence"]))
        self.assertNotIn("private-input", json.dumps(context))
        self.assertNotIn("secret-key", (directory / "ai-explanation.json").read_text())
        self.assertNotIn(str(self.project), json.dumps(context))
        self.assertIn("Candidate scores", payload["messages"][0]["content"])
        page = (directory / "report.html").read_text(encoding="utf-8")
        self.assertIn("AI 보완 설명", page)
        self.assertNotIn("<script>unsafe</script>", page)
        self.assertIn("&lt;script&gt;unsafe", page)
        self.assertTrue(verify_run(directory))
        (directory / "ai-explanation.json").write_text("tampered")
        self.assertFalse(verify_run(directory))

    def test_ai_failure_preserves_dynamic_report(self):
        write(self.project, "launch.py", "def run(): pass\nrun()")
        for response in (TimeoutError(), self.response(evidence="E999")):
            with self.subTest(response=type(response)), patch("knitcode_analyzer_v2.ai.build_opener") as build:
                if isinstance(response, Exception):
                    build.return_value.open.side_effect = response
                else:
                    build.return_value.open.return_value = response
                directory, runtime = run_project(self.project, report_dir=self.output,
                    ai_options={"base_url": "http://localhost:1234/v1", "model": "test"})
            self.assertEqual(runtime["ai"]["status"], "failed")
            self.assertEqual(runtime["run"]["execution_status"], "finished")
            self.assertTrue(verify_run(directory))
            self.assertIn("AI 설명을 생성하지 못했습니다", (directory / "report.html").read_text(encoding="utf-8"))

    def test_static_plan_ai_and_context_are_bounded(self):
        write(self.project, "arbitrary.py", "\n".join(f"def f{i}(): return {i}" for i in range(100)))
        analysis = analyze_project(self.project)
        before = copy.deepcopy(analysis)
        context = build_project_context(analysis)
        self.assertFalse(context["execution_observed"])
        self.assertGreater(context["omitted"]["nodes"], 0)
        self.assertLessEqual(len(context["evidence"]), 201)
        self.assertEqual(analysis, before)
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            directory, plan = plan_project(self.project, report_dir=self.output,
                ai_options={"base_url": "http://localhost:1234/v1", "model": "test"})
        self.assertEqual(plan["execution_status"], "not_executed")
        self.assertTrue(verify_manifest(directory))
        self.assertIn("AI Supplement", (directory / "report.html").read_text(encoding="utf-8"))
