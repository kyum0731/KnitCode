"""CPython monitoring collector. Stores locations and types, never runtime values."""

import ast
import hashlib
import inspect
import io
import json
from pathlib import Path
import sys
import threading
import time
import tokenize
import types


class Collector:
    def __init__(self, analysis, stream, max_events):
        self.analysis, self.stream, self.max_events = analysis, stream, max_events
        self.root = Path(analysis["metadata"]["project_root"])
        self.thread = threading.get_ident()
        self.lock = threading.RLock()
        self.seq = 0
        self.active = False
        self.reasons = set()
        self.expected, self.cache, self.positions = {}, {}, {}
        self.tool_id = None
        self.started_ns = 0
        self.invocations = {}
        self.nodes = {n["id"]: n for n in analysis["nodes"]}
        self.files = {str((self.root / f["path"]).resolve()): f for f in analysis["files"]}
        self.internal_files = {str(Path(__file__).resolve()), str(Path(__file__).with_name("_runtime_worker.py").resolve())}
        self.prepare()

    def prepare(self):
        """Compile but never execute snapshot-matching source to identify code objects."""
        for filename, file in self.files.items():
            if file["parse_status"] != "ok":
                continue
            path = Path(filename)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != file["sha256"]:
                raise ValueError("실행 전 원문이 변경되었습니다: " + file["path"])
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
            tree = ast.parse(source, filename=filename)
            starts = {n.lineno: min([n.lineno] + [d.lineno for d in n.decorator_list])
                      for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            definitions = [n for n in self.analysis["nodes"] if n["file"] == file["path"] and n["range"] and n["type"] != "variable"]
            compiled = compile(source, filename, "exec", dont_inherit=True)
            pending, expected = [compiled], []
            while pending:
                code = pending.pop()
                pending.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
                qualname = code.co_qualname.replace(".<locals>.", ".")
                matches = [n for n in definitions if
                           (code.co_name == "<module>" and n["type"] == "file") or
                           (n["type"] != "file" and n["qualified_name"] == qualname and
                            starts.get(n["range"]["start_line"], n["range"]["start_line"]) <= code.co_firstlineno <= n["range"]["start_line"])]
                if len(matches) == 1:
                    expected.append((code, matches[0]["id"]))
            self.expected[filename] = expected

    def identify(self, code):
        key = (code.co_filename, code)
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= 20000:
            self.notice("code_cache_limit")
            self.stop_events()
            return None
        try:
            filename = str(Path(code.co_filename).resolve())
        except (OSError, ValueError):
            filename = ""
        found = None
        if filename in self.files and filename not in self.internal_files:
            if code.co_flags & (inspect.CO_COROUTINE | inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR):
                self.notice("async_or_generator_not_traced")
            else:
                # Code equality compares structure/constants/positions, not just a name.
                matches = [nid for expected, nid in self.expected.get(filename, []) if code == expected]
                if len(matches) == 1:
                    found = matches[0]
                else:
                    self.notice("unmapped_project_code")
        self.cache[key] = found
        return found

    def location(self, frame, node_id, offset=None):
        code = frame.f_code
        key = (code.co_filename, code)
        if key not in self.positions:
            self.positions[key] = tuple(code.co_positions())
        offset = frame.f_lasti if offset is None else offset
        index = max(0, offset // 2)  # CPython 3.13 wordcode includes cache entries.
        positions = self.positions[key]
        pos = positions[index] if index < len(positions) else (None,) * 4
        r = None
        if all(p is not None for p in pos) and pos[0] > 0:
            r = dict(start_line=pos[0], end_line=pos[1], start_col=pos[2], end_col=pos[3])
        return {"file": self.nodes[node_id]["file"], "range": r}

    def write(self, event):
        if self.seq >= self.max_events:
            if "event_limit" not in self.reasons:
                self.reasons.add("event_limit")
                self.seq += 1
                self.stream.write(json.dumps({"seq": self.seq, "event": "limitation", "reason": "event_limit"}) + "\n")
                self.stream.flush()
            self.stop_events()
            return
        self.seq += 1
        event["seq"] = self.seq
        self.stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.stream.flush()

    def notice(self, reason):
        with self.lock:
            if reason not in self.reasons:
                self.reasons.add(reason)
                self.write({"event": "limitation", "reason": reason})

    def handle(self, event, code, offset, frame, exception=None):
        with self.lock:
            self._handle(event, code, offset, frame, exception)

    def _handle(self, event, code, offset, frame, exception=None):
        if not self.active:
            return
        timestamp_ns = time.perf_counter_ns() - self.started_ns
        try:
            node_id = self.identify(code)
            if not node_id or not self.active:
                return
            if threading.get_ident() != self.thread:
                self.notice("other_thread_not_traced")
                return
            if frame.f_code is not code:
                self.notice("frame_unavailable")
                return
            item = {"event": event, "node_id": node_id, "location": self.location(frame, node_id, offset),
                    "timestamp_ns": timestamp_ns}
            if event == "start":
                # IDs belong to invocations, so recursive calls remain distinct.
                ancestor = frame.f_back
                while ancestor is not None and id(ancestor) not in self.invocations:
                    ancestor = ancestor.f_back
                parent = self.invocations.get(id(ancestor)) if ancestor is not None else None
                invocation = {"invocation_id": self.seq + 1,
                              "parent_invocation_id": parent["invocation_id"] if parent else None,
                              "depth": parent["depth"] + 1 if parent else 0}
                self.invocations[id(frame)] = invocation
                item.update(invocation)
                caller = frame.f_back
                caller_id = self.identify(caller.f_code) if caller else None
                item["caller_id"] = caller_id
                item["caller_relation"] = "python_frame_parent"
                item["caller_location"] = self.location(caller, caller_id) if caller_id else None
                item["entry_kind"] = self.nodes[node_id]["type"]
            else:
                item.update(self.invocations.get(id(frame), {}))
                if event in {"return", "unwind"}:
                    self.invocations.pop(id(frame), None)
            if exception is not None:
                cls = type(exception)
                item["exception_type"] = type.__getattribute__(cls, "__module__") + "." + type.__getattribute__(cls, "__qualname__")
            if self.active:
                self.write(item)
        except BaseException:
            self.reasons.add("collector_error")
            self.stop_events()

    def on_start(self, code, offset):
        self.handle("start", code, offset, sys._getframe(1))

    def on_return(self, code, offset, value):
        self.handle("return", code, offset, sys._getframe(1))

    def on_raise(self, code, offset, exception):
        self.handle("raise", code, offset, sys._getframe(1), exception)

    def on_unwind(self, code, offset, exception):
        self.handle("unwind", code, offset, sys._getframe(1), exception)

    def on_resume(self, code, offset):
        self.handle("resume", code, offset, sys._getframe(1))

    def audit(self, event, args):
        if self.active and event in {"subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.spawn"}:
            try:
                self.notice("child_process_not_traced")
            except BaseException:
                self.reasons.add("collector_error")
                self.stop_events()

    def start(self):
        mon = sys.monitoring
        for tool_id in range(6):
            if mon.get_tool(tool_id) is None:
                mon.use_tool_id(tool_id, "knitcode-runtime")
                self.tool_id = tool_id
                break
        if self.tool_id is None:
            raise ValueError("사용 가능한 sys.monitoring 도구 ID가 없습니다.")
        events = {"PY_START": self.on_start, "PY_RETURN": self.on_return, "RAISE": self.on_raise,
                  "PY_UNWIND": self.on_unwind, "PY_RESUME": self.on_resume}
        mask = 0
        for name, callback in events.items():
            event = getattr(mon.events, name)
            mon.register_callback(self.tool_id, event, callback)
            mask |= event
        sys.addaudithook(self.audit)
        self.active = True
        self.started_ns = time.perf_counter_ns()
        mon.set_events(self.tool_id, mask)

    def stop_events(self):
        self.active = False
        if self.tool_id is not None:
            sys.monitoring.set_events(self.tool_id, 0)

    def close(self):
        with self.lock:
            self.stop_events()
            if self.tool_id is not None:
                for name in ("PY_START", "PY_RETURN", "RAISE", "PY_UNWIND", "PY_RESUME"):
                    sys.monitoring.register_callback(self.tool_id, getattr(sys.monitoring.events, name), None)
                sys.monitoring.free_tool_id(self.tool_id)


def summarize(analysis, events):
    """Only observed function/method entries create runtime call relationships."""
    from .models import digest
    nodes = {n["id"]: n for n in analysis["nodes"]}
    calls, entries, exceptions, reasons = {}, {}, [], set()
    for event in events:
        if event["event"] == "limitation":
            reasons.add(event["reason"])
            continue
        nid = event.get("node_id")
        if nid not in nodes:
            reasons.add("invalid_event_node")
            continue
        if event["event"] == "start":
            entries[nid] = entries.get(nid, 0) + 1
            source, location = event.get("caller_id"), event.get("caller_location")
            if source in nodes and location and nodes[nid]["type"] in {"function", "method"}:
                key = digest([source, nid, location])
                if key not in calls:
                    calls[key] = {"id": "runtime:" + key, "source": source, "target": nid,
                                  "type": "observed_calls", "count": 0, "evidence": location,
                                  "caller_relation": "python_frame_parent",
                                  "event_sequences": []}
                calls[key]["count"] += 1
                calls[key]["event_sequences"].append(event["seq"])
        if event["event"] in {"raise", "unwind"}:
            exceptions.append(event)
    spans, timings = summarize_timing(nodes, events)
    functions = [n["id"] for n in nodes.values() if n["type"] in {"function", "method"}]
    observed = [nid for nid in functions if nid in entries]
    return {"runtime_schema_version": "1.1", "snapshot_id": analysis["metadata"]["snapshot_id"],
            "observed_calls": list(calls.values()), "node_entries": entries,
            "exceptions": exceptions, "event_count": len(events), "limitations": sorted(reasons),
            "invocations": spans, "node_timings": timings,
            "function_coverage": {"total": len(functions), "observed": len(observed),
                                  "percent": round(100 * len(observed) / len(functions), 2) if functions else None,
                                  "unobserved_node_ids": [nid for nid in functions if nid not in entries]}}


def summarize_timing(nodes, events):
    """Pair explicit invocation IDs; truncated spans have no invented end time.

    Self time excludes nearest *observed* child spans, including callbacks across
    external frames. External work and collector overhead remain in wall time.
    Legacy 1.0 events have no timing information and produce no spans.
    """
    spans = {}
    for event in events:
        iid, timestamp = event.get("invocation_id"), event.get("timestamp_ns")
        if iid is None or not isinstance(timestamp, int) or timestamp < 0:
            continue
        if event["event"] == "start" and event.get("node_id") in nodes:
            spans[iid] = {"invocation_id": iid, "node_id": event["node_id"],
                          "parent_invocation_id": event.get("parent_invocation_id"),
                          "depth": event.get("depth", 0), "start_seq": event["seq"],
                          "end_seq": None, "start_ns": timestamp, "end_ns": None,
                          "duration_ns": None, "self_ns": None, "outcome": "incomplete"}
        elif event["event"] in {"return", "unwind"} and iid in spans:
            span = spans[iid]
            if span["node_id"] == event.get("node_id") and timestamp >= span["start_ns"] and span["end_seq"] is None:
                span.update(end_seq=event["seq"], end_ns=timestamp, duration_ns=timestamp - span["start_ns"],
                            outcome=event["event"])
    children = {}
    for span in spans.values():
        children.setdefault(span["parent_invocation_id"], []).append(span)
    timings = {}
    for span in spans.values():
        duration = span["duration_ns"]
        nested = children.get(span["invocation_id"], [])
        if duration is not None and all(c["duration_ns"] is not None and
                                        span["start_ns"] <= c["start_ns"] <= c["end_ns"] <= span["end_ns"] for c in nested):
            span["self_ns"] = max(0, duration - sum(c["duration_ns"] for c in nested))
        nid = span["node_id"]
        if nodes[nid]["type"] not in {"function", "method"}:
            continue
        metric = timings.setdefault(nid, {"node_id": nid, "count": 0, "completed_count": 0,
                                         "incomplete_count": 0, "inclusive_ns": 0, "self_ns": 0,
                                         "self_sample_count": 0, "max_ns": None, "average_ns": None})
        metric["count"] += 1
        if duration is None:
            metric["incomplete_count"] += 1
        else:
            metric["completed_count"] += 1
            metric["inclusive_ns"] += duration
            metric["max_ns"] = max(metric["max_ns"] or 0, duration)
            metric["average_ns"] = metric["inclusive_ns"] // metric["completed_count"]
            if span["self_ns"] is not None:
                metric["self_ns"] += span["self_ns"]
                metric["self_sample_count"] += 1
    return list(spans.values()), sorted(timings.values(), key=lambda m: (-m["inclusive_ns"], m["node_id"]))
