"""Fixed launcher for optional profilers; arguments are JSON data, never Python code."""

import inspect
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(config["root"])
    spec, plan = config["spec"], config["plan"]
    os.chdir(root)
    path = str(root / spec["file"])
    sys.path.insert(0, str(root / spec["import_root"]) if spec["module"] else str(Path(path).parent))
    sys.argv = [path, *plan["args"]]
    name = "__knitcode_entry__" if "::" in plan["entry"] else "__main__"
    namespace = runpy.run_module(spec["module"], run_name=name, alter_sys=True) if spec["module"] else runpy.run_path(path, run_name=name)
    if "::" in plan["entry"]:
        function = namespace[plan["entry"].split("::", 1)[1]]
        if not inspect.isfunction(function) or inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function):
            raise ValueError("추가 도구는 동기 함수만 직접 호출합니다.")
        inspect.signature(function).bind(*plan["call_args"], **plan["call_kwargs"])
        function(*plan["call_args"], **plan["call_kwargs"])


if __name__ == "__main__":
    main()
