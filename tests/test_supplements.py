import os
import unittest

from knitcode_analyzer_v2 import analyze_project
from knitcode_analyzer_v2.supplements import run_supplement
from .helpers import test_directory, write


@unittest.skipUnless(os.environ.get("KNITCODE_TEST_OPTIONAL") == "1", "선택 도구 실제 실행은 KNITCODE_TEST_OPTIONAL=1로 활성화")
class OptionalToolTests(unittest.TestCase):
    def exercise(self, backend):
        with test_directory() as root:
            project = root / "project"
            project.mkdir()
            source = write(project, "runner.py", "import time\ndef calculate(n):\n    if n > 0:\n        for j in range(10):\n            values = [i*i for i in range(20000)]\n            time.sleep(0.05)\n        print(len(values))\ncalculate(int(input('n: ')))")
            original = source.read_bytes()
            analysis = analyze_project(project)
            directory = root / "reports" / "studies" / "test"
            directory.mkdir(parents=True)
            plan = {"entry": "runner.py", "args": [], "call_args": [], "call_kwargs": {}, "stdin": "3\n", "fixtures": []}
            result = run_supplement(backend, project, analysis, plan, directory, timeout=30)
            logs = '\n'.join(p.read_text(encoding="utf-8", errors="replace")[-6000:] for p in (directory / "supplements" / backend).glob("*-stderr.log"))
            self.assertIn(result["status"], {"complete", "partial"}, str(result) + '\n' + logs)
            self.assertTrue(result.get("observations"), str(result) + '\n' + logs)
            self.assertEqual(source.read_bytes(), original)

    def test_dynapyt_real_function_and_branch_hooks(self):
        self.exercise("dynapyt")

    def test_scalene_real_profile(self):
        self.exercise("scalene")


if __name__ == "__main__":
    unittest.main()
