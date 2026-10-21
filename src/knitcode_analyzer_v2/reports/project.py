"""Project brief, candidate evidence and optional AI explanation."""

import html

from ..project_summary import project_overview
from ..runtime_candidates import discover_entry_points


def esc(value):
    return html.escape(str(value), quote=True)


def render_ai_html(explanation):
    if explanation is None:
        return '<p class="muted">AI 설명은 선택 기능입니다. 현재 요약은 수집된 구조·실행 사실로 작성했습니다.</p>'
    if explanation["status"] != "complete":
        return '<h3>AI 설명을 생성하지 못했습니다</h3><p>' + esc(explanation["error"]) + '</p><p>위 구조 요약과 분석 결과는 계속 사용할 수 있습니다.</p>'
    evidence = {e["id"]: e for e in explanation["context"]["evidence"]}
    blocks = ['<h3>AI 보완 설명</h3><p class="muted">모델: ' + esc(explanation["model"]) + ' · 근거 참조의 유효성만 검사했습니다. 설명의 정확성을 보증하지 않으며 실행 후보 선택·그래프에는 반영하지 않습니다.</p>']
    for claim in explanation["answer"]["claims"]:
        blocks.append('<p><span class="badge">' + ('AI 관찰 설명' if claim["kind"] == "observation" else 'AI 추론') + '</span> ' + esc(claim["text"]) + '<br>')
        for eid in dict.fromkeys(claim["evidence_ids"]):
            e = evidence[eid]
            blocks.append(f'<a href="#{esc(e["node_id"])}">{esc(eid)} · {esc(e["file"])}</a> ')
        blocks.append('</p>')
    blocks.append('<ul>' + ''.join('<li>'+esc(s)+'</li>' for s in explanation["answer"]["limitations"]) + '</ul>')
    if explanation["answer"]["suggested_relations"]:
        blocks.append('<p class="muted">AI의 미검증 관계 제안은 ai-explanation.json에 별도 보존했습니다.</p>')
    return ''.join(blocks)


def render_project_html(result, *, runtime=None, explanation=None):
    candidates = runtime["summary"].get("entry_candidates") if runtime else None
    candidates = discover_entry_points(result) if candidates is None else candidates
    overview = project_overview(result, runtime=runtime, candidates=candidates)
    blocks = [render_review_html(runtime.get("review") if runtime else None),
              '<section id="project-brief"><h2>프로젝트 구조와 실행 흐름 요약</h2>',
              ''.join('<p>'+esc(p)+'</p>' for p in overview["paragraphs"]),
              '<p><a href="#graph">전체 구조 그래프 보기</a> · <a href="#entry-candidates">실행 후보와 근거 보기</a>' +
              (' · <a href="#runtime">관측된 호출·시간 보기</a>' if runtime else '') + '</p>',
              '<details><summary>파일별 역할을 읽기 위한 구조 지표</summary><div class="table-wrap"><table><tr><th>파일</th><th>정의</th><th>유입 파일 / 참조 파일</th><th>이번 실행 진입</th></tr>']
    for m in overview["modules"][:20]:
        blocks.append(f'<tr><td><a href="#{m["node_id"]}">{esc(m["file"])}</a></td><td>{m["definitions"]}</td><td>{m["incoming_files"]} / {m["outgoing_files"]}</td><td>{m["observed_entries"] if runtime else "미실행"}</td></tr>')
    blocks.append(f'</table></div><p class="meta">표시 {min(20,len(overview["modules"]))}/{len(overview["modules"])}개 · 호출/import 연결 기준이며 업무 역할을 단정하지 않습니다.</p></details>')
    blocks.append(render_ai_html(runtime.get("ai") if runtime else explanation))
    blocks.append('</section><section id="entry-candidates"><h2>정적 근거 기반 실행 후보</h2><p>파일명과 관계없이 최상위 실행 구문·내부 호출 연결·import 유입·호출자 없는 함수를 살펴봅니다. 점수는 상대 추천 순위입니다. 함수는 명시적으로 선택해야 하며 필요한 값을 자동 생성하지 않습니다. 함수가 속한 모듈의 최상위 코드도 로딩할 때 실행됩니다.</p>')
    if runtime and runtime["run"].get("entry_selection", {}).get("selector"):
        blocks.append('<p>이번 실행 선택: <strong>'+esc(runtime["run"]["entry_selection"]["selector"])+'</strong></p>')
    blocks.append(f'<details><summary>후보 {len(candidates)}개 · 추천 근거와 실행 입력 확인</summary>')
    for c in candidates[:40]:
        status = "직접 실행 미지원" if not c["supported"] else "명시 선택 필요" if not c["auto_eligible"] else "단독 자동 실행 가능"
        blocks.append(f'<article><h3><a href="#{c["node_id"]}">{esc(c["selector"])}</a> <span class="badge">{c["score"]}점 · {status}</span></h3>')
        blocks.append('<ul>'+''.join('<li>'+esc(reason)+'</li>' for reason in c["reasons"])+ '</ul>')
        blocks.append(f'<p>선택 문자열: <code>{esc(c["selector"])}</code></p><button data-graph-focus="{c["node_id"]}">이 후보의 호출 흐름 보기</button>')
        if c["evidence_edge_ids"]:
            blocks.append('<p>호출 근거: '+ ' · '.join(f'<a href="#{eid}">{index+1}</a>' for index,eid in enumerate(c["evidence_edge_ids"][:12])) + '</p>')
        blocks.append('</article>')
    blocks.append(f'<p class="meta">표시 {min(40,len(candidates))}/{len(candidates)}개. 전체 후보는 --discover의 candidates.json 또는 동적 summary.json에 있습니다. 입력이 필요한 함수에는 --call-args / --call-kwargs를 지정하세요.</p></details></section>')
    return ''.join(blocks)


def render_review_html(review):
    if not review:
        return ""
    return ('<section id="ai-review"><h2>정적·동적 결과 기반 AI 보완 보고서</h2>'
            '<p>저장된 두 분석 결과의 동일 스냅샷과 무결성을 확인한 뒤 작성했습니다. '
            '대상 프로그램을 재실행하지 않았으며 원본 보고서는 보존했습니다.</p>'
            f'<p>AI 상태: {esc(review["ai_status"])} · 생성 시각(UTC): {esc(review["created_at"])}</p>'
            f'<p>정적 보고서: <code>{esc(review["static_report"]["directory"])}</code><br>'
            f'동적 보고서: <code>{esc(review["runtime_report"]["directory"])}</code></p>'
            f'<p class="meta">snapshot: {esc(review["snapshot_id"])}<br>run: {esc(review["run_id"])}</p>'
            '<p>아래 구조·관측 결과와 AI 설명을 함께 확인하세요. 원본 로그는 동적 보고서 폴더에 있습니다. '
            '입력 출처: review.json · 이 보고서 무결성: review-manifest.json</p></section>')


def render_review_markdown(review):
    if not review:
        return ""
    from .markdown import esc as safe
    return '\n'.join(["", "## 정적·동적 결과 기반 AI 보완 보고서", "",
                      "저장된 두 분석 결과를 사용했습니다. 대상 재실행 없이 원본 보고서를 보존했습니다.",
                      "", "AI 상태: " + safe(review["ai_status"]),
                      "", "정적 보고서: " + safe(review["static_report"]["directory"]),
                      "", "동적 보고서: " + safe(review["runtime_report"]["directory"]),
                      "", "snapshot: " + safe(review["snapshot_id"]),
                      "", "run: " + safe(review["run_id"]),
                      "", "입력 출처: review.json · 저장 무결성: review-manifest.json", ""])


def render_project_markdown(result, *, runtime=None):
    from .markdown import esc as safe
    candidates = discover_entry_points(result)
    overview = project_overview(result, runtime=runtime, candidates=candidates)
    lines = ["## 프로젝트 구조와 실행 흐름 요약", ""] + [safe(p)+"\n" for p in overview["paragraphs"]]
    if not runtime:
        lines += ["### 정적 근거 기반 실행 후보", "", "점수는 추천 순위이며 실행 성공 확률이 아닙니다. 함수 후보는 명시적으로 선택하고 필요한 입력을 제공해야 합니다.", ""]
        lines += [f"- {safe(c['selector'])} · {c['score']}점 · {safe('; '.join(c['reasons']))}" for c in candidates[:40]]
        lines += ["", f"후보 {min(40,len(candidates))}/{len(candidates)}개 표시. 전체 후보는 --discover 결과의 candidates.json에서 확인하세요.", ""]
    return '\n'.join(lines)


def render_project_ai_markdown(explanation):
    from .markdown import esc as safe
    if explanation is None:
        return ""
    lines = ["", "## AI 보완 설명", "", "AI 추론은 정적·동적 사실과 구분하며 실행 후보나 그래프를 자동 변경하지 않습니다.", ""]
    if explanation["status"] != "complete":
        return '\n'.join(lines + ["AI 설명 생성 실패: " + safe(explanation["error"]), ""])
    lines += [f"모델: {safe(explanation['model'])}", ""]
    for c in explanation["answer"]["claims"]:
        lines.append(f"- {'AI 관찰 설명' if c['kind']=='observation' else 'AI 추론'}: {safe(c['text'])} (근거 {', '.join(c['evidence_ids'])})")
    lines += ["", "설명 내용의 정확성은 자동 검증하지 않습니다. 제공 근거와 응답은 ai-explanation.json에 보존합니다.", ""]
    return '\n'.join(lines)
