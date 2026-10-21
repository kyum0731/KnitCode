"""Evidence-based launch recommendations. Pure projection; never imports targets."""

from collections import Counter, defaultdict
from pathlib import Path


def launch_spec(analysis, relative):
    root = Path(analysis["metadata"]["project_root"])
    source = root / analysis["metadata"]["source_root"]
    path, import_root = root / relative, source
    parts = path.relative_to(source).parts
    if source == root and len(parts) > 2 and parts[0] == "src":
        import_root, parts = root / "src", parts[1:]
    parsed = {f["path"] for f in analysis["files"] if f["parse_status"] == "ok"}
    module = None
    if len(parts) > 1 and all(p.isidentifier() for p in (*parts[:-1], path.stem)):
        package = path.name == "__main__.py" or all(
            (import_root.joinpath(*parts[:i]) / "__init__.py").relative_to(root).as_posix() in parsed
            for i in range(1, len(parts)))
        if package:
            module = ".".join((*parts[:-1], path.stem))
    return {"file": relative, "script": None if module else relative, "module": module,
            "import_root": import_root.relative_to(root).as_posix() if module else None}


def discover_entry_points(analysis):
    nodes = {n["id"]: n for n in analysis["nodes"]}
    files = {f["path"]: f for f in analysis["files"] if f["parse_status"] == "ok"}
    rules = {e["evidence"]["file"]: e["rule"] for e in analysis["insights"]["entry_points"]}
    incoming, outgoing, imports_in = defaultdict(set), defaultdict(list), defaultdict(set)
    adjacency = defaultdict(set)
    for edge in analysis["edges"]:
        if edge["type"] == "calls" and edge.get("context") not in {"annotation", "definition_expression"}:
            outgoing[edge["source"]].append(edge)
            if edge["target"]:
                adjacency[edge["source"]].add(edge["target"])
                if edge["source"] != edge["target"]:
                    incoming[edge["target"]].add(edge["source"])
        if edge["type"] == "imports" and edge["target"]:
            a, b = nodes[edge["source"]]["file"], nodes[edge["target"]]["file"]
            if a != b:
                imports_in[b].add(a)
    def connections(nid):
        visited, pending = {nid}, [nid]
        # Ranking is bounded even for very large repositories; this is not coverage.
        while pending and len(visited) < 201:
            current = pending.pop()
            for target in sorted(adjacency[current]):
                if target not in visited:
                    visited.add(target)
                    pending.append(target)
                    if len(visited) >= 201:
                        break
        return len(visited)-1, len(visited) >= 201
    candidates = []
    source = Path(analysis["metadata"]["source_root"])
    for relative, file in sorted(files.items()):
        nid, path = file["node_id"], Path(relative)
        calls = [e for e in outgoing[nid] if e.get("context") == "module_body"]
        internal = [e for e in calls if e["target"]]
        rule = rules.get(relative)
        conventional = path.parent == source and path.name in {"main.py", "app.py"}
        if not (rule or calls or conventional):
            continue
        reasons, score = [], 0
        if rule:
            reasons.append("직접 실행 가드가 있음" if rule == "main_guard" else "Python 모듈 실행 진입점")
            score += 100
        if calls:
            reasons.append(f"모듈 최상위 호출 {len(calls)}곳 · 내부 대상 {len(internal)}곳")
            score += 20 + min(20, len(internal)*5)
        if not imports_in[relative]:
            reasons.append("분석된 다른 파일의 import 유입 없음 (외부 호출은 알 수 없음)")
            score += 10
        else:
            reasons.append(f"다른 파일 {len(imports_in[relative])}개에서 import됨")
            score -= min(15, len(imports_in[relative])*3)
        if conventional:
            reasons.append("관례적 파일명은 보조 근거로만 사용")
            score += 5
        reachable, capped = connections(nid)
        score += min(15, reachable)
        structural = bool(internal and not imports_in[relative] and path.name != "__init__.py")
        candidates.append({**launch_spec(analysis, relative), "selector": relative, "node_id": nid,
                           "kind": "file", "rule": rule or ("module_calls" if calls else "conventional_filename"),
                           "score": score, "confidence": "strong" if rule else "structural" if structural else "tentative",
                           "auto_eligible": bool(rule or structural or conventional), "callable": None,
                           "reasons": reasons, "evidence_edge_ids": [e["id"] for e in calls],
                           "reachable_nodes": reachable, "reachability_capped": capped,
                           "required_parameters": [], "supported": True})
    duplicates = Counter((n["file"], n["name"]) for n in nodes.values()
                         if n["type"] in {"function", "class"} and nodes[n["parent"]]["type"] == "file")
    for n in nodes.values():
        if n["type"] != "function" or n["file"] not in files or nodes[n["parent"]]["type"] != "file":
            continue
        calls = outgoing[n["id"]]
        # Functions already called by project code are downstream, not launch roots.
        if incoming[n["id"]]:
            continue
        reachable, capped = connections(n["id"])
        required = [p["name"] for p in n["parameters"] if p["kind"] not in {"var_positional", "var_keyword"} and p["default"] is None]
        supported = not n["is_async"] and duplicates[n["file"], n["name"]] == 1
        reasons = ["분석된 내부 호출자 없는 최상위 함수 (미사용 판정 아님)",
                   f"내부 호출 연결로 도달하는 요소 {reachable}개" + (" 이상" if capped else "")]
        if required:
            reasons.append("호출 인자 필요: " + ", ".join(required))
        if not supported:
            reasons.append("비동기 또는 중복 정의로 직접 호출 실행 미지원")
        candidates.append({**launch_spec(analysis, n["file"]), "selector": n["file"] + "::" + n["name"],
                           "node_id": n["id"], "kind": "function", "rule": "root_function",
                           "score": 15 + min(30, reachable*3), "confidence": "tentative",
                           "auto_eligible": False, "callable": n["name"], "supported": supported,
                           "reasons": reasons, "required_parameters": required,
                           "evidence_edge_ids": [e["id"] for e in calls],
                           "reachable_nodes": reachable, "reachability_capped": capped})
    return sorted(candidates, key=lambda c: (-c["score"], c["selector"]))


def select_entry_point(analysis, candidates=None):
    candidates = discover_entry_points(analysis) if candidates is None else candidates
    eligible = [c for c in candidates if c["auto_eligible"]]
    if len(eligible) == 1:
        return eligible[0]
    choices = "\n".join(f"  - {c['selector']} · 점수 {c['score']} · {'; '.join(c['reasons'])}" for c in candidates[:30])
    message = "실행 진입점 후보가 여러 개여서 실행하지 않았습니다" if len(eligible)>1 else "자동 실행할 만큼 근거가 충분한 진입점을 찾지 못했습니다"
    raise ValueError(message + (":\n" + choices if choices else ".") +
                     "\n--discover로 후보·근거 HTML을 확인하고 --entry 후보를 선택하세요. 일반 실행은 --script PATH / --module NAME도 가능합니다.")
