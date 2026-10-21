"""Launched by absolute path in the selected Python; target runs only here."""

import json
import inspect
import os
from pathlib import Path
import platform
import runpy
import sys
import threading
import traceback


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    directory = Path(config["directory"])
    status = {"worker_completed": False, "python": platform.python_version(), "executable": sys.executable,
              "limitations": [], "exit_code": 2}
    collector = None
    try:
        if sys.implementation.name != "cpython" or sys.version_info < (3, 13):
            raise ValueError("동적 실행은 CPython 3.13 이상이 필요합니다.")
        package_root = str(Path(__file__).resolve().parents[1])
        sys.path.insert(0, package_root)
        from knitcode_analyzer_v2.runtime_trace import Collector
        sys.path.remove(package_root)
        with (directory / "events.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            collector = Collector(config["analysis"], stream, config["max_events"])
            os.chdir(config["cwd"])
            target = config["script"] or config["module"]
            sys.argv = [target, *config["args"]]
            sys.path[0] = str(Path(target).parent) if config["script"] else config.get("import_root") or config["cwd"]
            collector.start()
            code = 0
            try:
                if config.get("callable"):
                    # Loading a function's module still executes its top-level code.
                    namespace = (runpy.run_path(config["script"], run_name="__knitcode_entry__") if config["script"] else
                                 runpy.run_module(config["module"], run_name="__knitcode_entry__", alter_sys=True))
                    function = namespace.get(config["callable"])
                    if (not inspect.isfunction(function) or inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function)
                            or inspect.isasyncgenfunction(function) or collector.identify(function.__code__) != config["callable_node_id"]):
                        raise ValueError("선택한 정적 함수와 실행 객체가 일치하지 않거나 지원 범위 밖입니다 (래퍼·재정의·비동기·제너레이터).")
                    inspect.signature(function).bind(*config["call_args"], **config["call_kwargs"])
                    function(*config["call_args"], **config["call_kwargs"])
                elif config["script"]:
                    runpy.run_path(config["script"], run_name="__main__")
                else:
                    runpy.run_module(config["module"], run_name="__main__", alter_sys=True)
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
                if isinstance(error.code, str):
                    print(error.code, file=sys.stderr)
            except BaseException:
                code = 1
                traceback.print_exc()
            finally:
                if any(t.ident != collector.thread and t.is_alive() for t in threading.enumerate()):
                    collector.notice("other_thread_not_traced")
                collector.close()
            status.update(worker_completed=True, exit_code=code, limitations=sorted(collector.reasons))
    except BaseException:
        if collector:
            collector.close()
        traceback.print_exc()
        status["limitations"].append("worker_setup_failed")
    finally:
        (directory / "worker-status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return status["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
