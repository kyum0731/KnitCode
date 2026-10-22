import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import jsonschema

from knitcode_analyzer_v2 import analyze_project
from knitcode_analyzer_v2.ai import AIError, explain
from knitcode_analyzer_v2.study import study_project, verify_study
from knitcode_analyzer_v2.study_context import build_study_context
from .helpers import test_directory, write
from .test_study import plan, guide


class StudyRequestTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(test_directory())
        self.project = self.root / "project"
        self.project.mkdir()
        write(self.project, "main.py", "def double(n: int): return n * 2\nprint(double(3))")
        self.context = build_study_context(analyze_project(self.project))
        self.options = {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                        "model": "test", "num_ctx": 8192}

    def call(self, purpose, responder):
        captured = {}
        def reply(request, **kwargs):
            payload = json.loads(request.data)
            packet = json.loads(payload["messages"][1]["content"])
            captured.update(payload=payload, packet=packet)
            answer = responder(packet, payload)
            return io.BytesIO(json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 100,
                "eval_count": 50, "message": {"content": json.dumps(answer)}}).encode())
        with patch("knitcode_analyzer_v2.ai.build_opener") as mock:
            mock.return_value.open.side_effect = reply
            result = explain(self.context, purpose=purpose, **self.options)
        return result, captured

    def test_ollama_enforces_both_plan_branches_and_rejects_unknown_values(self):
        result, captured = self.call("plan", lambda packet, _: plan(packet))
        schema = captured["payload"]["format"]
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(result["answer"], schema)
        jsonschema.validate({"action": "stop", "reason": "불충분", "evidence_ids": []}, schema)
        for change in ({"action": "run"}, {"entry": "unknown.py"}, {"evidence_ids": ["E999999"]}, {"command": "echo"},
                       {"call_args": ["--help"]}, {"call_kwargs": {"domain": "example.com"}}):
            invalid = {**result["answer"], **change}
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid, schema)
        self.assertEqual(captured["payload"]["options"]["temperature"], 0)
        self.assertEqual(captured["payload"]["options"]["num_predict"], 1024)

    def test_large_context_is_bounded_immutable_and_evidence_is_validated_against_sent_subset(self):
        for index in range(250):
            item = copy.deepcopy(self.context["evidence"][0])
            item.update(id=f"L{index}", kind="node_facts", facts={"name": f"f{index}", "author_docstring_excerpt": "text " * 200})
            self.context["evidence"].append(item)
        before = copy.deepcopy(self.context)
        result, captured = self.call("plan", lambda packet, _: plan(packet))
        self.assertEqual(self.context, before)
        self.assertGreater(result["diagnostics"]["omissions"]["evidence"], 200)
        payload = captured["payload"]
        serialized = payload["messages"][0]["content"] + payload["messages"][1]["content"] + json.dumps(payload["format"], ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(serialized.encode()), result["diagnostics"]["input_budget_bytes"])
        self.assertLess(result["diagnostics"]["context_bytes"], result["diagnostics"]["original_context_bytes"] / 5)
        omitted = next(e["id"] for e in self.context["evidence"] if e["id"] not in {x["id"] for x in captured["packet"]["evidence"]})
        with self.assertRaises(AIError):
            self.call("plan", lambda packet, _: plan(packet, evidence_ids=[omitted]))

    def test_guide_schema_limits_paths_and_keeps_runtime_evidence(self):
        self.context["evidence"].append({**self.context["evidence"][0], "id": "E900", "kind": "supplement_observation",
                                         "facts": {"backend": "scalene", "count": 1}})
        result, captured = self.call("guide", lambda packet, _: guide(packet))
        self.assertEqual(captured["packet"]["allowed_candidates"], [])
        self.assertTrue(any(e["id"] == "E900" for e in captured["packet"]["evidence"]))
        jsonschema.validate(result["answer"], captured["payload"]["format"])
        invalid = copy.deepcopy(result["answer"])
        invalid["files"][0]["path"] = "not-provided.py"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, captured["payload"]["format"])

    def test_invalid_response_is_saved_bounded_and_manifest_sealed(self):
        response = {"action": "run", "unexpected": "x" * 8000}
        def reply(*args, **kwargs):
            return io.BytesIO(json.dumps({"done": True, "done_reason": "stop", "message": {"content": json.dumps(response)}}).encode())
        with patch("knitcode_analyzer_v2.ai.build_opener") as mock:
            mock.return_value.open.side_effect = reply
            directory, result = study_project(self.project, ai_options=self.options, report_dir=self.root / "reports", max_attempts=1)
        diagnostic = json.loads((directory / "plan-1-failed.json").read_text(encoding="utf-8"))["diagnostics"]
        self.assertEqual(len(diagnostic["response_excerpt"]), 4096)
        self.assertTrue(diagnostic["response_excerpt_truncated"])
        self.assertEqual(diagnostic["error_category"], "response_validation")
        self.assertEqual(result["ai_settings"]["num_ctx"], 8192)
        self.assertTrue(verify_study(directory))
        (directory / "plan-1-failed.json").write_text("tampered", encoding="utf-8")
        self.assertFalse(verify_study(directory))

    def test_timeout_and_http_error_have_diagnostics_without_error_body(self):
        for error, category in ((TimeoutError(), "timeout"),
                                (HTTPError("http://localhost", 500, "secret-body", {}, io.BytesIO(b"secret-body")), "http")):
            with patch("knitcode_analyzer_v2.ai.build_opener") as mock:
                mock.return_value.open.side_effect = error
                with self.assertRaises(AIError) as caught:
                    explain(self.context, purpose="plan", **self.options)
            self.assertEqual(caught.exception.diagnostics["error_category"], category)
            self.assertNotIn("secret-body", str(caught.exception) + json.dumps(caught.exception.diagnostics))

    def test_tiny_budget_rejected_before_request(self):
        with patch("knitcode_analyzer_v2.ai.build_opener") as mock:
            with self.assertRaises(AIError):
                explain(self.context, purpose="plan", **{**self.options, "num_ctx": 1024})
            mock.assert_not_called()

    def test_many_cli_options_keep_whole_input_hints_with_omission_count(self):
        write(self.project, "main.py", "import argparse\np=argparse.ArgumentParser()\n" +
              "\n".join(f"p.add_argument('--option{i}', required=True, help='sample input')" for i in range(30)))
        self.context = build_study_context(analyze_project(self.project))
        _, captured = self.call("plan", lambda packet, _: plan(packet))
        hints = next(e for e in captured["packet"]["evidence"] if e["kind"] == "input_sites")
        self.assertTrue(hints["facts"])
        self.assertIn("--option0", hints["facts"][0]["expression"])
        self.assertEqual(len(hints["facts"]) + hints["omitted_sites"], 30)

    def test_function_branch_requires_function_inputs(self):
        write(self.project, "main.py", "def double(n: int): return n * 2")
        self.context = build_study_context(analyze_project(self.project))
        result, captured = self.call("plan", lambda packet, _: plan(packet, "main.py::double", call_args=[3]))
        jsonschema.validate(result["answer"], captured["payload"]["format"])
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({**result["answer"], "args": ["--help"]}, captured["payload"]["format"])


if __name__ == "__main__":
    unittest.main()
