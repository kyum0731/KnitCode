import hashlib
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from knitcode_analyzer_v2 import analyze_project
from knitcode_analyzer_v2.exporter import to_json, verify_manifest, write_report
from knitcode_analyzer_v2.review import main, review_reports, verify_review
from knitcode_analyzer_v2.runtime import plan_project, run_project, verify_run
from .helpers import test_directory, write


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(test_directory())
        self.project = self.root / "project"
        self.project.mkdir()
        write(self.project, "arbitrary.py", "from pathlib import Path\ndef operation():\n    Path('marker').write_text('one execution')\noperation()")
        self.static = self.root / "static"
        write_report(analyze_project(self.project), self.static)
        self.dynamic, self.runtime = run_project(self.project, static_report=self.static, report_dir=self.root / "dynamic")
        self.output = self.root / "review"
        self.options = {"base_url": "http://localhost:1234/v1", "model": "test"}

    def response(self):
        answer = {"claims": [{"text": "<script>Example</script> 관측 결과를 설명합니다.",
                              "kind": "observation", "evidence_ids": ["E1"]}],
                  "limitations": ["한 번의 실행입니다."], "suggested_relations": []}
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}).encode())

    def saved_bytes(self):
        return {str(p): p.read_bytes() for directory in (self.static, self.dynamic) for p in directory.iterdir() if p.is_file()}

    def test_offline_inputs_no_source_no_rerun_separate_report_and_provenance(self):
        before = self.saved_bytes()
        # Live source is no longer at its original path; the saved reports suffice.
        self.project.rename(self.root / "moved-project")
        with patch("knitcode_analyzer_v2.runtime.subprocess.Popen") as launch, patch("knitcode_analyzer_v2.runtime.analyze_project") as analyze, patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            directory, result = review_reports(self.static / "report.html", self.dynamic / "report.html",
                                               ai_options=self.options, report_dir=self.output)
            launch.assert_not_called()
            analyze.assert_not_called()
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertEqual(before, self.saved_bytes())
        self.assertEqual(result["ai_status"], "complete")
        self.assertFalse(result["target_reexecuted"])
        self.assertTrue(verify_review(directory))
        self.assertTrue(verify_manifest(directory))
        self.assertTrue(verify_run(self.dynamic))
        context = json.loads(payload["messages"][1]["content"])
        self.assertTrue(context["execution_observed"])
        self.assertTrue(any(e["kind"] == "static_relation" for e in context["evidence"]))
        self.assertTrue(any(e["kind"] == "runtime_observation" for e in context["evidence"]))
        page = (directory / "report.html").read_text(encoding="utf-8")
        self.assertIn("정적·동적 결과 기반 AI 보완 보고서", page)
        self.assertIn('id="graph"', page)
        self.assertIn('id="runtime"', page)
        self.assertIn("대상 프로그램을 재실행하지 않았으며", page)
        self.assertNotIn("<script>Example</script>", page)
        self.assertIn("&lt;script&gt;Example", page)
        self.assertNotIn(str(self.project), json.dumps(context))
        self.assertEqual(json.loads((directory / "run.json").read_bytes()), self.runtime["run"])
        (directory / "ai-explanation.json").write_text("changed")
        self.assertFalse(verify_review(directory))

    def test_discover_report_is_also_accepted(self):
        static, _ = plan_project(self.project, report_dir=self.root / "plans")
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            directory, _ = review_reports(static, self.dynamic, ai_options=self.options, report_dir=self.output)
        self.assertTrue(verify_review(directory))

    def test_ai_failure_keeps_inputs_and_writes_retryable_failure_report(self):
        before = self.saved_bytes()
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.side_effect = TimeoutError()
            directory, result = review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)
        self.assertEqual(result["ai_status"], "failed")
        self.assertTrue(verify_review(directory))
        self.assertEqual(before, self.saved_bytes())
        self.assertIn("AI 설명을 생성하지 못했습니다", (directory / "report.html").read_text(encoding="utf-8"))
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            retried, _ = review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)
        self.assertNotEqual(retried, directory)
        self.assertTrue(verify_review(directory))
        self.assertTrue(verify_review(retried))

    def test_changed_source_rejects_linked_execution_before_launch(self):
        write(self.project, "arbitrary.py", "print('changed')")
        with patch("knitcode_analyzer_v2.runtime.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "스냅샷"):
                run_project(self.project, script="arbitrary.py", static_report=self.static, report_dir=self.root / "new-run")
            launch.assert_not_called()

    def test_mismatched_saved_snapshots_rejected_before_ai(self):
        write(self.project, "arbitrary.py", "print('changed')")
        other = self.root / "new-static"
        write_report(analyze_project(self.project), other)
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            with self.assertRaisesRegex(ValueError, "스냅샷"):
                review_reports(other, self.dynamic, ai_options=self.options, report_dir=self.output)
            build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_different_project_rejected_even_if_source_matches(self):
        other_project = self.root / "other"
        other_project.mkdir()
        write(other_project, "arbitrary.py", (self.project / "arbitrary.py").read_text())
        other_static = self.root / "other-static"
        write_report(analyze_project(other_project), other_static)
        with self.assertRaisesRegex(ValueError, "프로젝트"):
            review_reports(other_static, self.dynamic, ai_options=self.options, report_dir=self.output)

    def test_tampered_missing_and_unsafe_artifacts_rejected_without_network(self):
        for directory, name in ((self.static, "analysis.json"), (self.dynamic, "summary.json"), (self.dynamic, "events.jsonl")):
            path = directory / name
            original = path.read_bytes()
            with self.subTest(name=name), patch("knitcode_analyzer_v2.ai.build_opener") as build:
                try:
                    path.write_bytes(original + b" ")
                    with self.assertRaisesRegex(ValueError, "무결성"):
                        review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)
                    path.unlink()
                    with self.assertRaises(OSError):
                        review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)
                    build.assert_not_called()
                finally:
                    path.write_bytes(original)
        manifest_path = self.dynamic / "run-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"]["../outside"] = "fake"
        manifest_path.write_text(to_json(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "목록"):
            review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)

    def test_rehashed_inconsistent_summary_is_rejected(self):
        summary_path = self.dynamic / "summary.json"
        summary = json.loads(summary_path.read_bytes())
        summary["event_count"] += 1
        summary_path.write_text(to_json(summary), encoding="utf-8")
        manifest_path = self.dynamic / "run-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"]["summary.json"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        manifest_path.write_text(to_json(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "이벤트 기록"):
            review_reports(self.static, self.dynamic, ai_options=self.options, report_dir=self.output)

    def test_partial_failed_execution_can_be_reviewed_without_hiding_status(self):
        write(self.project, "arbitrary.py", "def execute(): raise RuntimeError('private message')\nexecute()")
        static = self.root / "failed-static"
        write_report(analyze_project(self.project), static)
        dynamic, _ = run_project(self.project, script="arbitrary.py", report_dir=self.root / "failed-dynamic")
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            directory, _ = review_reports(static, dynamic, ai_options=self.options, report_dir=self.output)
        run = json.loads((directory / "run.json").read_bytes())
        self.assertEqual(run["execution_status"], "failed")
        self.assertNotIn("private message", (directory / "ai-explanation.json").read_text(encoding="utf-8"))

    def test_cli_success_failure_and_invalid_input_exit_codes(self):
        args = ["--static-report", str(self.static), "--runtime-report", str(self.dynamic),
                "--ai", "api", "--base-url", "http://localhost:1234/v1", "--model", "test", "--report-dir", str(self.output)]
        with redirect_stderr(io.StringIO()), patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            self.assertEqual(main(args), 0)
            build.return_value.open.side_effect = TimeoutError()
            self.assertEqual(main(args), 3)
            args[1] = str(self.root / "missing")
            self.assertEqual(main(args), 2)

    def test_timeout_partial_trace_retains_limitations_in_ai_context(self):
        write(self.project, "arbitrary.py", "import time\ndef execute(): time.sleep(30)\nexecute()")
        static = self.root / "timeout-static"
        write_report(analyze_project(self.project), static)
        dynamic, _ = run_project(self.project, script="arbitrary.py", timeout=0.5, report_dir=self.root / "timeout-dynamic")
        with patch("knitcode_analyzer_v2.ai.build_opener") as build:
            build.return_value.open.return_value = self.response()
            directory, _ = review_reports(static, dynamic, ai_options=self.options, report_dir=self.output)
        explanation = json.loads((directory / "ai-explanation.json").read_bytes())
        facts = next(e["facts"] for e in explanation["context"]["evidence"] if e["kind"] == "project_summary")
        self.assertEqual(facts["run"]["execution_status"], "timeout")
        self.assertEqual(facts["run"]["trace_status"], "partial")
        self.assertIn("timeout", facts["run"]["limitations"])
        self.assertTrue(verify_review(directory))


if __name__ == "__main__":
    unittest.main()
