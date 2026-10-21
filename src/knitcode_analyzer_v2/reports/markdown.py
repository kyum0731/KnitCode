import re
from .common import ANALYSIS_STATUS, CONTEXT, KIND, LIMITATION, STATUS, indexes, label, position, signature, target_text, tree
from ..insights import REASONS
from .symbols import render_symbol_markdown
from .graph import render_overview_markdown
from .project import render_project_markdown


def esc(value):
    text = str(value).replace("\r", "").replace("\n", " / ")
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~\-])", r"\\\1", text)


def render_markdown(result, *, runtime=None):
    m, s, insights = result["metadata"], result["stats"], result["insights"]
    nodes, file_nodes, incoming, outgoing, imports = indexes(result)
    lines = ["# KnitCode · 프로젝트 분석", "", f"**{ANALYSIS_STATUS[m['analysis_status']]}** · AI 미사용", "",
             f"대상: {esc(m['project_root'])} · 소스 루트: {esc(m['source_root'])}", "",
             f"분석 시각(UTC): {m['analyzed_at']} · snapshot: {m['snapshot_id']}", "",
             render_project_markdown(result, runtime=runtime), "", "## 프로젝트 한눈에 보기", "", esc(insights["overview"]["text"]), "",
             f"파싱 성공 {s['parsed_file_count']} / 실패 {s['parse_failed_file_count']} · 호출 위치 {s['call_count']}", "",
             " · ".join(f"{STATUS[k]} {v}" for k, v in s["calls_by_status"].items()), "",
             LIMITATION, "", "열은 0부터 시작하는 UTF-8 바이트 수이고, 끝 위치는 제외합니다.", "",
             "## 먼저 살펴볼 곳", ""]
    if insights["fallback_used"]:
        lines += ["진입점 후보를 찾지 못했습니다. 라이브러리에는 단일 진입점이 없을 수 있습니다. 아래는 구조에 따른 대안입니다.", ""]
    for entry in insights["entry_points"]:
        lines.append(f"- {esc(entry['text'])}: {esc(position(entry['evidence']))} · 규칙: {entry['rule']}")
    lines += ["", "### 읽기 안내 · 실행 순서가 아닌 정적 연결", ""]
    for item in insights["reading_order"]:
        lines.append(f"- 깊이 {item['depth']} · {esc(label(nodes[item['node_id']]))} — {esc(item['text'])}; 근거 {esc(position(item['evidence']))}" +
                     (f"; 호출 근거 ID {item['via_edge_id']}" if item["via_edge_id"] else ""))
    limit = insights["reading_limit"]
    lines += ["", f"최대 깊이 2 / 20개 항목 · 추가 대상 생략 {limit['omitted_node_count']}개 · 순환 관계 {len(limit['cycle_edge_ids'])}개", "",
              "## 파일 구조", ""]
    # Do not place untrusted filenames into a fenced block where they could close it.
    lines += ["    " + line for line in tree(result["files"]).splitlines()]
    lines += ["", "## 파일 사이 관계", ""]
    relations = insights["file_relations"]
    for rel in relations[:20]:
        lines.append(f"- {esc(rel['source_file'])} → {esc(rel['target_file'])} · {rel['type']} · {rel['count']}개 (근거 ID: {', '.join(rel['edge_ids'])})")
    if not relations:
        lines.append("분석된 내부 파일 간 연결이 없습니다.")
    if len(relations) > 20:
        lines.append(f"전체 {len(relations)}개 중 20개 표시. 나머지는 HTML 전체 관계와 analysis.json에서 확인하세요.")
    lines += ["", render_overview_markdown(result), "", render_symbol_markdown(result), "", "## 파일·코드 상세", ""]
    components = {c["file"]: c for c in insights["components"]}
    metrics = {v["node_id"]: v for v in insights["metrics"]}
    for file in result["files"]:
        lines += [f"### {esc(file['path'])}", "", f"파싱: {file['parse_status']} · {components[file['path']]['text']}", ""]
        for edge in imports[file["path"]]:
            lines.append(f"- import: {esc(edge['expression'])} → {esc(target_text(edge, nodes))}; 근거 {esc(position(edge['evidence']))}; ID {edge['id']}")
        for node in file_nodes[file["path"]]:
            lines += ["", f"#### {KIND[node['type']]} · {esc(signature(node))}", "", f"ID: {node['id']}", "",
                      f"정의 위치: {esc(position({'file': node['file'], 'range': node['range']}))}", ""]
            if node.get("docstring"):
                lines += [f"작성자 문서: {esc(node['docstring'])}", ""]
            if node["id"] in metrics:
                metric = metrics[node["id"]]
                lines += [f"분석된 고유 호출자 {metric['caller_count']} · 내부 호출 대상 {metric['callee_count']} · 호출 위치 {metric['call_site_count']}", ""]
            if node["type"] == "variable":
                lines += [f"변수 범위: {node['variable_kind']} · 사용 근거는 위 변수 관계 표에 표시합니다.", ""]
                continue
            lines.append("호출자:")
            lines.extend(f"- {esc(label(nodes[e['source']]))} · {esc(position(e['evidence']))}; ID {e['id']}" for e in incoming[node["id"]])
            if not incoming[node["id"]]:
                lines.append("- 분석된 내부 호출자 없음 (미사용을 뜻하지 않음)")
            lines += ["", "호출 대상:"]
            for edge in outgoing[node["id"]]:
                lines.append(f"- [{STATUS[edge['resolution_status']]}] {esc(edge['expression'])} → {esc(target_text(edge, nodes))}; {CONTEXT[edge['context']]}; 근거 {esc(position(edge['evidence']))}; ID {edge['id']}")
            if not outgoing[node["id"]]:
                lines.append("- 이 범위에 수집된 호출 없음")
        lines.append("")
    lines += ["## 분석 한계와 진단", "", "연결률은 정확도가 아닙니다. external은 분석 범위 밖의 import 이름이며 설치나 출처를 확인하지 않았습니다.", ""]
    for reason, count in s["unresolved_by_reason"].items():
        lines.append(f"- {reason}: {count}개 — {REASONS.get(reason, reason)}")
    for d in result["diagnostics"]:
        lines.append(f"- [{d['severity']}] {esc(d.get('file') or '프로젝트')} · {d['code']} · {esc(d['message'])}")
    lines += ["", "### 제외한 항목", "", "제외 디렉터리 내부의 파일 개수는 추정하지 않습니다.", ""]
    lines.extend(f"- {esc(i['path'])} · {i['kind']} · {i['reason']}" for i in m["coverage"]["skipped"])
    lines += ["", "원자료: analysis.json · 저장 무결성: manifest.json", ""]
    return "\n".join(lines)
