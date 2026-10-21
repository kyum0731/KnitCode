"""Readable onboarding guide and per-attempt provenance for the AI/dynamic loop."""

import html
import json


def render_study_html(study):
    def esc(value):
        return html.escape(str(value), quote=True)
    guide = study.get("guide", {})
    evidence = {e["id"]: e for e in guide.get("context", {}).get("evidence", [])}
    def claim(item):
        refs = ' · '.join(f'<a href="#{esc(evidence[r]["node_id"])}">{esc(r)}</a>' for r in item["evidence_ids"] if r in evidence)
        return ('<span class="badge">' + ('AI 관측 해석' if item["kind"] == "observation" else 'AI 추론') + '</span> ' +
                esc(item["text"]) + '<br><span class="meta">근거: ' + refs + '</span>')
    blocks = ['<section id="study"><h2>처음 보는 프로젝트 이해하기</h2>',
              '<p>정적 구조를 바탕으로 AI가 입력을 계획하고, 실제 실행 결과를 다시 검토한 보고서입니다. AI 설명과 합성 입력은 추정이며 업무 의미의 정확성을 보장하지 않습니다.</p>',
              f'<p>분석 상태: <strong>{esc(study["status"])}</strong> · 여러 시도에서 관측한 함수 {study["observed_functions"]}/{study["total_functions"]}개</p>',
              '<p><a href="static/report.html">정적 보고서</a> · <a href="#graph">전체 구조 그래프</a> · <a href="#attempts">자동 입력과 실행 기록</a></p>']
    if guide.get("status") == "complete":
        answer = guide["answer"]
        blocks += ['<h3>어떤 프로젝트인가</h3><p>' + claim(answer["purpose"]) + '</p>']
        for key, title in (("directories", "디렉터리별 역할과 연결"), ("files", "핵심 파일의 역할")):
            blocks.append('<h3>' + title + '</h3><div class="table-wrap"><table><tr><th>경로</th><th>역할·연결·근거</th></tr>')
            for item in answer[key]:
                blocks.append('<tr><td><code>' + esc(item["path"]) + '</code></td><td>' + claim(item) + '</td></tr>')
            blocks.append('</table></div>')
        for key, title in (("flow", "기능이 이어지는 흐름"), ("reading_order", "처음 읽을 때의 순서")):
            blocks.append('<h3>' + title + '</h3><ol>')
            blocks.extend('<li>' + ('<code>' + esc(item["path"]) + '</code> · ' if "path" in item else '') + claim(item) + '</li>' for item in answer[key])
            blocks.append('</ol>')
        blocks.append('<h3>아직 확인하지 못한 부분</h3><ul>' + ''.join('<li>' + esc(s) + '</li>' for s in answer["limitations"]) + '</ul>')
    else:
        blocks.append('<p>AI 역할 설명을 완성하지 못했습니다: ' + esc(guide.get("error", study.get("error", "분석 중단"))) + '</p><p>아래 정적 구조와 저장된 실행 보고서는 계속 확인할 수 있습니다.</p>')
    blocks += ['</section><section id="attempts"><h2>AI 입력 계획과 실제 실행</h2>',
               '<p>각 시도의 입력은 합성 예시입니다. 클릭해서 개별 보고서의 호출 순서·시간·실패 원인을 확인하세요.</p>']
    for attempt in study["attempts"]:
        blocks.append(f'<details><summary>시도 {attempt["number"]} · {esc(attempt["status"])}</summary>')
        plan = attempt.get("plan", {})
        blocks.append('<p>' + esc(plan.get("reason", attempt.get("error", ""))) + '</p>')
        if plan.get("action") == "execute":
            blocks.append('<pre class="tree">' + esc(json.dumps({k: plan[k] for k in ("entry", "args", "call_args", "call_kwargs", "stdin", "fixtures")}, ensure_ascii=False, indent=2)) + '</pre>')
        if attempt.get("report"):
            blocks.append('<p><a href="' + esc(attempt["report"]) + '">이 시도의 동적 보고서 열기</a></p>')
        feedback = attempt.get("feedback")
        if feedback:
            blocks.append('<p>실행: ' + esc(feedback["execution_status"]) + ' · 추적: ' + esc(feedback["trace_status"]) + ' · 종료 코드: ' + esc(feedback["target_exit_code"]) + '</p>')
        if attempt.get("error"):
            blocks.append('<p>' + esc(attempt["error"]) + '</p>')
        blocks.append('</details>')
    for extra in study.get("supplements", []):
        blocks.append('<h3>추가 도구: ' + esc(extra["backend"]) + '</h3><p>' + esc(extra["status"]) + ' · ' + esc(extra.get("error", extra.get("description", ""))) + '</p>')
        rows = extra.get("observations", [])
        if rows:
            blocks.append('<p>별도 실행의 위치별 관측입니다. 최대 50개를 표시하며 전체 수치는 연결된 JSON에서 확인할 수 있습니다.</p><div class="table-wrap"><table><tr><th>위치</th><th>관측·수치</th></tr>')
            for row in rows[:50]:
                detail = row.get("metrics", {k: row[k] for k in ("event", "outcome", "count") if k in row})
                blocks.append('<tr><td><a href="#' + esc(row["node_id"]) + '">' + esc(row["file"]) + ':' + esc(row["line"]) + '</a></td><td><code>' + esc(json.dumps(detail, ensure_ascii=False)) + '</code></td></tr>')
            blocks.append('</table></div>')
        for name in extra.get("artifacts", []):
            blocks.append('<p><a href="' + esc(name) + '">' + esc(name) + '</a></p>')
    blocks.append('<p>아래 그래프의 동적 관계와 타임라인은 마지막으로 저장한 실행에 해당합니다. 여러 시도의 시간을 하나의 실행처럼 합치지 않습니다.</p>')
    blocks.append('<ul>' + ''.join('<li>' + esc(s) + '</li>' for s in study["limitations"]) + '</ul></section>')
    return ''.join(blocks)


def render_study_markdown(study):
    from .markdown import esc
    lines = ["# AI·동적 통합 프로젝트 이해 보고서", "", "상태: " + study["status"], "",
             f"여러 시도에서 관측한 함수: {study['observed_functions']}/{study['total_functions']}", "",
             "[정적 보고서](static/report.html)", ""]
    guide = study.get("guide", {})
    if guide.get("status") == "complete":
        answer = guide["answer"]
        lines += ["## 프로젝트 목적", "", esc(answer["purpose"]["text"]), ""]
        for key, title in (("directories", "디렉터리 역할"), ("files", "핵심 파일"), ("flow", "기능 흐름"), ("reading_order", "읽기 순서")):
            lines += ["## " + title, ""]
            for item in answer[key]:
                lines.append("- " + esc(item.get("path", "")) + " · " + esc(item["text"]) + " (" + item["kind"] + "; " + ', '.join(item["evidence_ids"]) + ")")
            lines.append("")
        lines += ["## 미확인 범위", ""] + ["- " + esc(s) for s in answer["limitations"]]
    else:
        lines += ["AI 설명 생성 실패: " + esc(guide.get("error", study.get("error", "중단"))), ""]
    lines += ["", "## 실행 시도", ""]
    for attempt in study["attempts"]:
        lines.append(f"- 시도 {attempt['number']}: {attempt['status']} · " + esc(attempt.get("plan", {}).get("reason", attempt.get("error", ""))))
        if attempt.get("report"):
            lines.append(f"  [동적 보고서]({attempt['report']})")
    for extra in study.get("supplements", []):
        lines += ["", "## 추가 도구: " + esc(extra["backend"]), "", esc(extra["status"]) + " · " + esc(extra.get("error", extra.get("description", ""))), ""]
        for row in extra.get("observations", [])[:50]:
            detail = row.get("metrics", {k: row[k] for k in ("event", "outcome", "count") if k in row})
            lines.append("- " + esc(row["file"]) + ":" + str(row["line"]) + " · " + esc(json.dumps(detail, ensure_ascii=False)))
    lines += ["", "최상위 그래프·타임라인은 마지막 기본 실행입니다. 시도별 시간은 합산하지 않습니다.", ""]
    lines += ["- " + esc(s) for s in study["limitations"]]
    lines += ["", "입력·출처: study.json / plan-*.json / guide.json · 전체 저장 무결성: study-manifest.json", ""]
    return '\n'.join(lines)
