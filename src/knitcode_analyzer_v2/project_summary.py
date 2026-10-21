"""Deterministic project narratives and bounded, opt-in AI fact packets."""

from collections import Counter, defaultdict

from .models import digest
from .runtime_candidates import discover_entry_points


def project_overview(analysis, *, runtime=None, candidates=None):
    nodes = {n["id"]: n for n in analysis["nodes"]}
    candidates = discover_entry_points(analysis) if candidates is None else candidates
    incoming, outgoing = defaultdict(set), defaultdict(set)
    definitions = Counter(n["file"] for n in nodes.values() if n["type"] in {"function", "method", "class"})
    for edge in analysis["edges"]:
        if edge["target"] and edge["type"] in {"calls", "imports"}:
            a, b = nodes[edge["source"]]["file"], nodes[edge["target"]]["file"]
            if a != b:
                incoming[b].add(a)
                outgoing[a].add(b)
    entries = runtime["summary"]["node_entries"] if runtime else {}
    observed_files = Counter()
    for nid, count in entries.items():
        observed_files[nodes[nid]["file"]] += count
    modules = [{"file": f["path"], "node_id": f["node_id"], "definitions": definitions[f["path"]],
                "incoming_files": len(incoming[f["path"]]), "outgoing_files": len(outgoing[f["path"]]),
                "observed_entries": observed_files[f["path"]]} for f in analysis["files"]]
    modules.sort(key=lambda m: (-(m["incoming_files"]+m["outgoing_files"]), m["file"]))
    stats = analysis["stats"]
    paragraphs = [f"분석 범위의 Python 파일 {stats['file_count']}개에 함수·메서드·클래스 정의 {stats['definition_count']}개가 있습니다. "
                  f"정적 호출 위치 {stats['call_count']}곳 중 내부 대상으로 연결한 곳은 {stats['calls_by_status']['resolved']}곳입니다.",
                  f"실행 진입점 후보는 {len(candidates)}개입니다. 추천 점수는 구문·호출·import 근거를 정리한 상대 순위이며 실행 성공 확률이 아닙니다."]
    if candidates:
        first = candidates[0]
        paragraphs.append(f"정적 근거의 상위 후보는 {first['selector']}입니다. " + '; '.join(first['reasons'][:2]) + ". 후보의 호출 흐름을 확인한 뒤 실행 입력을 정할 수 있습니다.")
    connected = [m for m in modules if m["incoming_files"]+m["outgoing_files"]]
    if connected:
        paragraphs.append("파일 간 호출·import 연결이 많은 곳은 " + ", ".join(m["file"] for m in connected[:3]) + "입니다. 연결 수는 업무 중요도나 위험도를 뜻하지 않습니다.")
    else:
        paragraphs.append("해석된 파일 간 호출·import 연결이 없습니다. 각 파일 내부 관계와 미해결 호출을 함께 확인하세요.")
    if runtime:
        run, summary = runtime["run"], runtime["summary"]
        target = run.get("entry_selection", {}).get("selector") or run["script"] or run["module"]
        paragraphs.append(f"이번 실행은 {target}에서 시작했으며 상태는 {run['execution_status']}, 추적 상태는 {run['trace_status']}입니다. "
                          f"프로젝트 파일 {len(observed_files)}개에서 코드 진입을 관측했습니다.")
        coverage = summary.get("function_coverage")
        if coverage:
            paragraphs.append(f"함수·메서드 {coverage['total']}개 중 {coverage['observed']}개에서 진입을 관측했습니다. "
                              "관측되지 않은 함수는 이번 입력에서 실행되지 않았거나 추적 범위 밖일 수 있습니다.")
        first_files = list(dict.fromkeys(nodes[e["node_id"]]["file"] for e in runtime["events"]
                                         if e["event"] == "start" and e.get("node_id") in nodes))
        if first_files:
            paragraphs.append("파일별 첫 진입 관측 순서: " + " → ".join(first_files[:8]) +
                              (f" · 이후 {len(first_files)-8}개 파일 생략" if len(first_files)>8 else "") + ". 반복·중첩 호출의 전체 순서는 타임라인에서 확인하세요.")
        pairs = Counter()
        for edge in summary["observed_calls"]:
            pairs[edge["source"], edge["target"]] += edge["count"]
        if pairs:
            def label(nid):
                n = nodes[nid]
                return n["file"] + "::" + (n["qualified_name"] or "<module>")
            paragraphs.append("자주 관측한 호출 연결: " + "; ".join(f"{label(a)} → {label(b)} ({count}회)" for (a,b),count in pairs.most_common(3)) + ". 호출 횟수 순서이며 시간 순서는 아닙니다.")
    else:
        paragraphs.append("대상 코드는 실행하지 않았습니다. 실제 실행 경로·횟수·시간은 후보 선택 후 동적 분석으로 확인할 수 있습니다.")
    return {"paragraphs": paragraphs, "modules": modules, "candidate_count": len(candidates),
            "observed_file_count": len(observed_files) if runtime else None}


def build_project_context(analysis, *, runtime=None):
    """Send facts, not raw source, program arguments, logs or runtime values."""
    nodes = {n["id"]: n for n in analysis["nodes"]}
    files = {f["path"]: f for f in analysis["files"]}
    candidates = discover_entry_points(analysis)
    overview = project_overview(analysis, runtime=runtime, candidates=candidates)
    valid = [n for n in nodes.values() if n["range"] is not None and n["type"] != "variable"]
    if not valid:
        raise ValueError("AI에 제공할 분석 근거가 없습니다.")
    observed = runtime["summary"]["node_entries"] if runtime else {}
    preferred = [c["node_id"] for c in candidates[:20]] + [m["node_id"] for m in overview["modules"][:30]]
    preferred += sorted(observed, key=lambda nid: (-observed[nid], nid))[:30]
    preferred += [n["id"] for n in valid]
    selected = list(dict.fromkeys(nid for nid in preferred if nodes[nid]["range"] is not None))[:80]
    included = set(selected)
    timings = {m["node_id"]: m for m in runtime["summary"].get("node_timings", [])} if runtime else {}
    by_candidate = {c["node_id"]: c for c in candidates}
    evidence = []
    def add(nid, kind, facts, range_=None):
        n = nodes[nid]
        evidence.append({"id": "E"+str(len(evidence)+1), "node_id": nid, "file": n["file"],
                         "range": range_ or n["range"], "file_sha256": files[n["file"]]["sha256"],
                         "kind": kind, "facts": facts})
    for nid in selected:
        n = nodes[nid]
        facts = {"name": n["qualified_name"] or n["name"], "type": n["type"],
                 "author_docstring_excerpt": (n.get("docstring") or "")[:600]}
        if nid in by_candidate:
            c = by_candidate[nid]
            facts["candidate"] = {k: c[k] for k in ("selector", "score", "confidence", "reasons", "auto_eligible", "required_parameters")}
        if runtime:
            facts["observed_entries"] = observed.get(nid, 0)
            if nid in timings:
                facts["timing"] = timings[nid]
        add(nid, "node_facts", facts)
    related = [e for e in analysis["edges"] if e["source"] in included and e["target"] in included and e["type"] in {"calls", "imports"}]
    for e in related[:80]:
        add(e["source"], "static_relation", {k: e[k] for k in ("id", "source", "target", "type", "resolution_status")}, e["evidence"]["range"])
    dynamic = [e for e in runtime["summary"]["observed_calls"] if e["source"] in included and e["target"] in included] if runtime else []
    for e in dynamic[:40]:
        add(e["source"], "runtime_observation", {"run_id": runtime["run"]["run_id"],
            **{k: e[k] for k in ("id", "source", "target", "count", "caller_relation")},
            "event_sequences": e["event_sequences"][:20], "omitted_event_sequences": max(0, len(e["event_sequences"])-20)}, e["evidence"]["range"])
    facts = {"file_count": len(files), "definition_count": analysis["stats"]["definition_count"],
             "candidate_count": len(candidates), "analysis_status": analysis["metadata"]["analysis_status"]}
    if runtime:
        facts["run"] = {k: runtime["run"][k] for k in ("run_id", "execution_status", "trace_status", "limitations")}
        facts["function_coverage"] = {k: runtime["summary"]["function_coverage"][k] for k in ("total", "observed", "percent")}
    add(selected[0], "project_summary", facts)
    context = {"context_version": "project-1.0", "prompt_version": "knitcode-project-2",
               "snapshot_id": analysis["metadata"]["snapshot_id"], "selected_node_id": selected[0],
               "query": "프로젝트 구조, 추천 진입점의 근거, 관측한 실행 흐름과 남은 분석 한계를 한국어로 간략히 설명하세요.",
               "evidence": evidence, "execution_observed": runtime is not None,
               "omitted": {"nodes": len(nodes)-len(selected),
                           "static_relations": sum(e["type"] in {"calls", "imports"} for e in analysis["edges"])-min(80,len(related)),
                           "runtime_relations": (len(runtime["summary"]["observed_calls"]) if runtime else 0)-min(40,len(dynamic))},
               "not_provided": ["전체 원문", "프로그램 입력 인자", "stdout/stderr", "변수·반환값", "절대 프로젝트 경로"],
               "limitations": "추천은 정적 추정입니다. 동적 근거는 특정 실행·지원 범위 안에서만 유효합니다. AI는 실행 대상을 선택하거나 그래프를 변경하지 않습니다."}
    context["input_hash"] = digest(context)
    return context


def explain_project(analysis, *, runtime=None, options):
    from .ai import AIError, explain
    context = None
    try:
        if not isinstance(options, dict) or set(options) - {"base_url", "model", "provider", "api_key", "timeout", "num_ctx"}:
            raise AIError("AI 설정은 지원하는 연결 옵션의 객체여야 합니다.")
        if not isinstance(options.get("base_url"), str) or not isinstance(options.get("model"), str):
            raise AIError("AI 서버 주소와 모델명을 설정하세요.")
        if (not isinstance(options.get("api_key", ""), str) or
                not isinstance(options.get("provider", "api"), str) or
                not isinstance(options.get("timeout", 60), (int, float)) or
                not isinstance(options.get("num_ctx", 16384), int)):
            raise AIError("AI 인증·provider·시간 제한·문맥 설정의 형식을 확인하세요.")
        context = build_project_context(analysis, runtime=runtime)
        return explain(context, purpose="project", **options)
    except (AIError, ValueError) as error:
        return {"status": "failed", "error": str(error), "context": context,
                "snapshot_id": analysis["metadata"]["snapshot_id"]}
