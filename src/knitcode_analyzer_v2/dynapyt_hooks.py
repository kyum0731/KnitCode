"""Optional DynaPyt hook adapter. Observe locations/counts, never replace values."""

from collections import Counter
import json
import os
from pathlib import Path

from dynapyt.analyses.BaseAnalysis import BaseAnalysis


class FlowAnalysis(BaseAnalysis):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.counts = Counter()
        self.root = Path(os.environ["KNITCODE_DYNAPYT_ROOT"]).resolve()
        self.output = Path(os.environ["KNITCODE_DYNAPYT_OUTPUT"])
        self.events = 0
        self.truncated = False

    def record(self, event, dyn_ast, iid, outcome=None):
        if self.events >= 10000:
            self.truncated = True
            return
        location = self.iid_to_location(dyn_ast, iid)
        path = Path(location.file)
        if str(path).endswith(".py.orig"):
            path = Path(str(path)[:-5])
        try:
            relative = path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return
        self.events += 1
        self.counts[(event, relative, location.start_line, outcome)] += 1

    def function_enter(self, dyn_ast, iid, args, name, is_lambda):
        self.record("function_enter", dyn_ast, iid)

    def enter_if(self, dyn_ast, iid, cond_value):
        self.record("if", dyn_ast, iid, cond_value if type(cond_value) is bool else None)

    def end_execution(self):
        records = [{"event": event, "file": file, "line": line, "outcome": outcome, "count": count}
                   for (event, file, line, outcome), count in self.counts.items()]
        self.output.write_text(json.dumps({"backend": "dynapyt", "events": records,
            "event_count": self.events, "truncated": self.truncated}, ensure_ascii=False, indent=2), encoding="utf-8")
