"""Runtime observations displayed separately from static facts."""

import html


REASONS = {
    "async_or_generator_not_traced": "비동기·제너레이터 실행은 추적 범위 밖",
    "other_thread_not_traced": "추가 스레드에서 실행된 프로젝트 코드 생략",
    "child_process_not_traced": "자식 프로세스 실행 감지: 자식 내부는 추적하지 않음",
    "unmapped_project_code": "프로젝트 경로의 실행 코드가 정적 정의와 매칭되지 않음",
    "event_limit": "이벤트 한도 도달: 이후 실행 기록 생략",
    "source_changed": "분석한 파일이 실행 전후에 변경됨: 저장된 스냅샷 기준 결과",
    "timeout": "시간 제한으로 추적 프로세스 종료",
    "cancelled": "사용자가 실행 취소",
    "collector_error": "추적기 오류로 수집 중단",
    "worker_status_missing": "추적 프로세스의 정상 완료 기록 없음",
    "worker_setup_failed": "실행 준비 실패",
    "incomplete_event_stream": "잘렸거나 유효하지 않은 이벤트 제외",
}


def esc(value):
    return html.escape(str(value), quote=True)


def place(ev):
    r = ev.get("range")
    return ev["file"] + (f":{r['start_line']}:{r['start_col']}–{r['end_line']}:{r['end_col']}" if r else " (정확한 위치 없음)")


def milliseconds(ns):
    return f"{ns / 1_000_000:.3f}" if ns is not None else "—"


def render_timing_html(summary, nodes, link):
    coverage = summary.get("function_coverage")
    if coverage is None:
        return ""
    percent = f'{coverage["percent"]}%' if coverage["percent"] is not None else "대상 함수 없음"
    blocks = ['<h3>함수 실행 관측 비율</h3>',
              f'<p><strong>{coverage["observed"]} / {coverage["total"]}개 · {percent}</strong></p>',
              '<p class="muted">정적으로 수집한 전체 함수·메서드 중 이번 실행에서 한 번 이상 진입한 비율입니다. 행·분기 커버리지가 아니며 비동기 등 추적 범위 밖의 함수도 분모에 포함합니다.</p>']
    missing = coverage["unobserved_node_ids"]
    blocks.append(f'<details><summary>이번 실행에서 관측되지 않은 함수 {len(missing)}개</summary><ul>')
    blocks.extend(f'<li>{link(nid)}</li>' for nid in missing[:100])
    blocks.append('</ul>' + (f'<p>나머지 {len(missing)-100}개는 summary.json에서 확인하세요.</p>' if len(missing)>100 else '') + '</details>')
    blocks.extend(['<h3>함수별 소요 시간</h3>',
                   '<p class="muted">완료된 호출의 벽시계 시간(ms)입니다. 누적 시간에는 하위 호출이 포함되고, 관측 자식 제외 시간은 관측된 중첩 호출만 뺍니다. 외부 코드·대기·추적기 비용이 포함되므로 정밀 벤치마크가 아닙니다. 재귀 호출의 누적 시간은 중복 합산됩니다. 미완료 호출의 시간은 합산하지 않습니다.</p>',
                   '<div class="table-wrap"><table><tr><th>함수 / 메서드</th><th>완료 / 미완료</th><th>누적 ms</th><th>관측 자식 제외 ms</th><th>평균 ms</th><th>최대 ms</th></tr>'])
    timings = summary.get("node_timings", [])
    for m in timings[:100]:
        blocks.append(f'<tr><td>{link(m["node_id"])}</td><td>{m["completed_count"]} / {m["incomplete_count"]}</td>'
                      f'<td>{milliseconds(m["inclusive_ns"]) if m["completed_count"] else "—"}</td>'
                      f'<td>{milliseconds(m["self_ns"]) if m["self_sample_count"] else "—"} ({m["self_sample_count"]}회)</td>'
                      f'<td>{milliseconds(m["average_ns"])}</td><td>{milliseconds(m["max_ns"])}</td></tr>')
    blocks.append('</table></div>')
    if not timings:
        blocks.append('<p>시간을 측정한 함수 호출이 없습니다.</p>')
    if len(timings)>100:
        blocks.append(f'<p>누적 시간 상위 100개 표시 · 나머지 {len(timings)-100}개는 summary.json에서 확인하세요.</p>')
    spans = summary.get("invocations", [])
    blocks.append('<h3>호출 타임라인</h3><p class="muted">진입 순서대로 처음 200개 호출을 표시합니다. 가로축은 추적 시작 이후 시간, 들여쓰기는 관측된 중첩 깊이입니다. 보라색은 복귀, 붉은색은 예외로 이탈, 점선은 미완료입니다. 중간 외부 프레임은 생략될 수 있습니다. 전체 기록은 summary.json의 invocations와 events.jsonl에 있습니다.</p>')
    if not spans:
        return "".join(blocks) + '<p>타임라인에 표시할 호출 기록이 없습니다.</p>'
    shown = spans[:200]
    latest = max((s["end_ns"] or s["start_ns"]) for s in shown)
    scale = max(latest, 1)
    blocks.append(f'<div class="graph-canvas"><svg viewBox="0 0 1120 {len(shown)*30+65}" style="min-width:1120px" role="img" aria-label="시간과 중첩 깊이에 따른 호출 타임라인">')
    for tick in range(5):
        x = 420 + tick*165
        blocks.append(f'<text x="{x}" y="20" font-size="11" fill="#536966">{milliseconds(scale*tick/4)} ms</text>'
                      f'<path d="M {x} 32 V {len(shown)*30+50}" stroke="#d5e4df"/>')
    for row, span in enumerate(shown):
        n = nodes[span["node_id"]]
        title = f'#{span["start_seq"]} {n["file"]}::{n["qualified_name"] or "<module>"} · 깊이 {span["depth"]} · ' + (milliseconds(span["duration_ns"]) + ' ms' if span["duration_ns"] is not None else '미완료 · 종료 시간 없음')
        name = n["qualified_name"] or n["file"]
        name = name if len(name)<=38 else name[:35]+'…'
        y = 45 + row*30
        x = 420 + 660*span["start_ns"]/scale
        duration = span["duration_ns"] or 0
        width = max(3,660*duration/scale)
        color = '#ac344b' if span["outcome"]=='unwind' else '#a444aa'
        blocks.append(f'<g><title>{esc(title)}</title><text x="{12+min(span["depth"],12)*12}" y="{y+14}" font-size="12" fill="#17312e">#{span["start_seq"]} {esc(name)}</text>'
                      f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="20" rx="3" fill="{color if span["duration_ns"] is not None else "none"}" stroke="{color}" stroke-dasharray="{"none" if span["duration_ns"] is not None else "3 2"}"/></g>')
    blocks.append(f'</svg></div><p class="meta">표시 {len(shown)} / {len(spans)}개 · 생략 {len(spans)-len(shown)}개</p>')
    return "".join(blocks)


def render_runtime_html(result, runtime):
    run, summary = runtime["run"], runtime["summary"]
    nodes = {n["id"]: n for n in result["nodes"]}
    def link(nid):
        n = nodes[nid]
        return f'<a href="#{nid}">{esc(n["file"] + "::" + (n["qualified_name"] or "<module>"))}</a>'
    blocks = [f'<section id="runtime"><h2>실행 관측 결과</h2><p>실행 {esc(run["execution_status"])} · 추적 {esc(run["trace_status"])} · 대상 종료 코드 {esc(run["target_exit_code"])}</p>',
              f'<p class="meta">run: {esc(run["run_id"])} · 전체 경과 {run["elapsed_seconds"]}초 · 기록 이벤트 {summary["event_count"]}개</p>',
              '<p class="notice">이 입력·이 실행에서 관측한 동기 Python 코드입니다. 관측되지 않은 코드는 미사용 코드가 아닙니다. 외부 코드·비동기·다른 스레드·자식 프로세스의 전체 흐름은 포함하지 않습니다. 값은 수집하지 않지만 프로그램 출력 로그에는 값이 포함될 수 있습니다.</p>',
              f'<p>실행 대상: {esc(run["script"] or run["module"])} · 작업 폴더: {esc(run["cwd"])}</p>',
              ('<p>원자료: run.json · summary.json · events.jsonl · AI 보고서 무결성: review-manifest.json. '
               'stdout.log / stderr.log 및 run-manifest.json은 원본 동적 보고서 폴더에서 확인하세요.</p>' if runtime.get("review") else
               '<p>원자료: run.json · summary.json · events.jsonl · stdout.log · stderr.log · 실행 무결성: run-manifest.json</p>')]
    if run.get("static_report"):
        blocks.append('<p>연결된 정적 보고서: <code>' + esc(run["static_report"]["directory"]) + '</code> · 실행 전 스냅샷 일치 확인</p>')
    if run["limitations"]:
        blocks.append('<ul>' + ''.join('<li>' + esc(REASONS.get(r, r)) + '</li>' for r in run["limitations"]) + '</ul>')
    for name, info in run["logs"].items():
        if info.get("truncated") or info.get("read_error"):
            blocks.append(f'<p class="notice">{name} 로그 제한 또는 읽기 오류: {esc(info)}</p>')
    blocks.append(render_timing_html(summary, nodes, link))
    blocks.append('<h3>실행된 코드 요소</h3><ul>')
    for nid, count in summary["node_entries"].items():
        blocks.append(f'<li>{link(nid)} · 진입 {count}회</li>')
    blocks.append('</ul><h3>실행에서 관측한 호출</h3><p class="muted">출발점은 호출 스택상 바로 위 Python 프레임입니다. 중간 C 함수의 콜백·암시적 호출이 있을 수 있습니다. 근거 위치는 상위 프레임의 실행 명령 범위이며 정적 호출 표현식과 항상 일대일로 일치하지 않습니다.</p>')
    for edge in summary["observed_calls"]:
        seqs = edge["event_sequences"]
        shown = ", ".join(map(str, seqs[:50]))
        blocks.append(f'<details id="{edge["id"]}"><summary>{link(edge["source"])} → {link(edge["target"])} · {edge["count"]}회</summary><p>{esc(place(edge["evidence"]))}</p><p>진입 이벤트 번호: {shown}' + (f' · 나머지 {len(seqs)-50}개는 summary.json 확인' if len(seqs)>50 else '') + '</p></details>')
    blocks.append('<h3>이벤트 순서</h3><p class="muted">처음 300개 표시. 전체 순서는 events.jsonl에 있습니다. 시간은 추적 시작 기준입니다. raise는 처리된 예외도 포함하며 실패 여부는 실행 상태로 확인합니다.</p><div class="table-wrap"><table><tr><th>순번</th><th>시간 ms / 호출 ID</th><th>이벤트</th><th>코드 / 위치</th></tr>')
    for event in runtime["events"][:300]:
        nid = event.get("node_id")
        content = (link(nid) + ' · ' + esc(place(event["location"]))) if nid in nodes else esc(event.get("reason", ""))
        blocks.append(f'<tr><td>{event["seq"]}</td><td>{milliseconds(event.get("timestamp_ns"))}<br>#{esc(event.get("invocation_id", "—"))}</td><td>{esc(event["event"])}<br>{esc(event.get("exception_type", ""))}</td><td>{content}</td></tr>')
    blocks.append('</table></div></section>')
    return "".join(blocks)


def render_runtime_markdown(result, runtime):
    from .ai_markdown import safe
    run, summary = runtime["run"], runtime["summary"]
    nodes = {n["id"]: n for n in result["nodes"]}
    lines = ["", "## 실행 관측 결과", "", f"- run: {run['run_id']}",
             f"- 실행 상태: {run['execution_status']} / 대상 종료 코드: {run['target_exit_code']}",
             f"- 추적 상태: {run['trace_status']} / 기록 이벤트: {summary['event_count']}",
             "", "이 실행에서 관측한 관계입니다. 관측되지 않은 코드는 미사용을 뜻하지 않습니다.", ""]
    for reason in run["limitations"]:
        lines.append("- " + safe(REASONS.get(reason, reason)))
    coverage = summary.get("function_coverage")
    if coverage:
        lines += ["", "### 함수 실행 관측 비율", "",
                  f"{coverage['observed']} / {coverage['total']}개 · " + (f"{coverage['percent']}%" if coverage['percent'] is not None else "대상 함수 없음"),
                  "", "전체 정적 함수·메서드 기준이며 행·분기 커버리지가 아닙니다. 범위 밖 함수도 분모에 포함합니다.",
                  "", "### 함수별 소요 시간", "", "완료 호출의 벽시계 시간이며 외부 코드·대기·추적기 비용을 포함합니다. 재귀 누적 시간은 중복 합산됩니다.", "",
                  "| 함수 | 완료 / 미완료 | 누적 ms | 관측 자식 제외 ms | 평균 ms | 최대 ms |", "| --- | --- | --- | --- | --- | --- |"]
        for m in summary.get("node_timings", [])[:100]:
            n = nodes[m["node_id"]]
            lines.append(f"| {safe(n['file']+'::'+n['qualified_name'])} | {m['completed_count']} / {m['incomplete_count']} | {milliseconds(m['inclusive_ns']) if m['completed_count'] else '—'} | {milliseconds(m['self_ns']) if m['self_sample_count'] else '—'} | {milliseconds(m['average_ns'])} | {milliseconds(m['max_ns'])} |")
        lines += ["", "시간 표는 상위 100개까지 표시합니다. 전체 시간·미관측 함수·호출 타임라인 원자료는 summary.json, 시각화는 report.html에서 확인하세요.", ""]
    lines += ["", "### 실행에서 관측한 호출", ""]
    for edge in summary["observed_calls"]:
        a, b = nodes[edge["source"]], nodes[edge["target"]]
        lines.append(f"- {safe(a['file']+'::'+a['qualified_name'])} → {safe(b['file']+'::'+b['qualified_name'])}: {edge['count']}회 · {safe(place(edge['evidence']))}")
    return "\n".join(lines) + "\n"
