"""Offline SVG explorer with a hash-authorized script and inert escaped data."""

import base64
import hashlib
import json

from ..graph import graph_data

STYLE = """
.graph-controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end}.graph-controls label{display:grid;gap:5px;font-size:13px}.graph-controls input,.graph-controls select,.graph-controls button,.graph-actions button{font:inherit;padding:8px;border:1px solid #abc8bd;border-radius:6px;background:white;color:#17312e}.graph-controls select{max-width:340px}.graph-controls button,.graph-actions button{cursor:pointer}.graph-layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);gap:18px;margin-top:16px}.graph-canvas{overflow:auto;border:1px solid #d5e4df;background:#f8fbf9;border-radius:10px;max-height:650px}.graph-canvas svg{width:100%;min-width:650px;display:block}.graph-detail{max-height:650px;overflow:auto;overflow-wrap:anywhere}.graph-node{cursor:pointer}.graph-node:focus rect{stroke:#d58320;stroke-width:4}.graph-edge{cursor:pointer}.graph-edge:focus path{stroke-width:5}.graph-detail li{font-size:13px}.graph-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}@media(max-width:1000px){.graph-layout{grid-template-columns:1fr}}
"""

SCRIPT = r"""
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('graph-data').textContent);
  const $ = id => document.getElementById(id);
  const nodes = new Map([...data.nodes,...(data.directories||[])].map(n => [n.id,n]));
  const entryRules = new Map([...(data.entry_points||[]),...(data.entry_candidates||[])].map(e=>[e.node_id,e.rule]));
  const entries = new Set(entryRules.keys());
  const edges = new Map(data.edges.map(e => [e.id,e]));
  let focus = '', expanded = new Set(), scale = 1;
  const color = {calls:'#176f60',imports:'#4265ad',contains:'#957332',observed_calls:'#a444aa',reads:'#187c92',writes:'#b45424',deletes:'#ac344b',references:'#64758a',inherits:'#6545b1',depends_on:'#985725'};
  const kindsLabel = {directory:'폴더',file:'파일',class:'클래스',function:'함수',method:'메서드',variable:'변수'};
  const view = () => $('graph-view')?.value || 'neighborhood';
  function text(tag, value, parent) {const el=document.createElement(tag);el.textContent=value;parent.append(el);return el;}
  function svg(tag, attrs, parent) {
    const el=document.createElementNS('http:'+'//www.w3.org/2000/svg',tag);
    for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));
    parent.append(el);return el;
  }
  const label = n => ['file','directory'].includes(n.type)?n.file:n.qualified_name+' · '+n.file;
  function jump(id, title, parent) {
    const a=text('a',title,parent);a.href='#'+id;
    a.addEventListener('click',()=>{let el=$(id);while(el){if(el.tagName==='DETAILS')el.open=true;el=el.parentElement;}});
  }
  function evidence(ids) {
    const detail=$('graph-detail');detail.replaceChildren();
    text('h3','관계 근거 '+ids.length+'개',detail);
    const list=text('ul','',detail);
    for(const id of ids){const e=edges.get(id),r=e.evidence.range,li=text('li','',list);
      text('p',e.type+' · '+(e.expression||'소속')+' · '+(e.resolution_status||'구조'),li);
      jump(id,e.evidence.file+(r?':'+r.start_line+':'+r.start_col:' (정확한 위치 없음)'),li);
    }
  }
  function describe(id){
    const n=nodes.get(id),detail=$('graph-detail');detail.replaceChildren();
    text('h3',kindsLabel[n.type]+' · '+label(n),detail);
    if(n.type!=='directory')jump(id,'보고서 코드 상세 보기',detail);
    if(entries.has(id))text('p','진입점 후보 · '+entryRules.get(id),detail);
    if(data.runtime && n.type!=='directory'){
      text('p','이번 실행 진입: '+(data.runtime.node_entries[id]||0)+'회',detail);
      const metric=data.runtime.node_timings.find(m=>m.node_id===id);
      if(metric)text('p',`완료 ${metric.completed_count}회 / 미완료 ${metric.incomplete_count}회 · 누적 ${metric.completed_count?(metric.inclusive_ns/1e6).toFixed(3):'—'} ms · 관측 자식 제외 ${metric.self_sample_count?(metric.self_ns/1e6).toFixed(3):'—'} ms`,detail);
    }
    if(n.docstring)text('p',n.docstring,detail);
    const actions=text('div','',detail);actions.className='graph-actions';
    const select=text('button','이 노드를 중심으로',actions);select.onclick=()=>choose(id);
    const expand=text('button','이 노드 주변 확장',actions);expand.onclick=()=>{if($('graph-view'))$('graph-view').value='neighborhood';expanded.add(id);draw();};
    if(n.type==='directory'){
      text('p','직접 포함된 파일 '+n.file_ids.length+'개',detail);
      const list=text('ul','',detail);
      for(const fid of n.file_ids){const li=text('li','',list);const b=text('button',label(nodes.get(fid)),li);b.onclick=()=>{$('graph-level').value='file';$('graph-search').value='';populate(fid);};}
      return;
    }
    if(n.type==='file'){
      const b=text('button','파일 안의 정의 펼치기',actions);b.onclick=()=>{if($('graph-view'))$('graph-view').value='neighborhood';$('graph-level').value='symbol';$('graph-node-type').value='all';$('graph-search').value='';populate(id);};
    }
    const flow=text('button','여기서 호출 흐름 보기',actions);flow.onclick=()=>{if($('graph-view'))$('graph-view').value='flow';$('graph-level').value='symbol';$('graph-node-type').value='all';$('graph-search').value='';populate(id);};
    const pending=data.edges.filter(e=>!e.target && (e.source===id || (n.type==='file' && nodes.get(e.source).file===n.file)));
    text('p','대상이 없는 관계 '+pending.length+'개 (가상 노드를 만들지 않음)',detail);
    const list=text('ul','',detail);
    for(const e of pending){const li=text('li',e.resolution_status+' · '+e.expression+' · '+(e.reason||e.external_name||e.builtin_name||''),list);jump(e.id,' 근거',li);}
  }
  function choose(id){focus=id;expanded=new Set([id]);$('graph-node').value=id;draw();describe(id);}
  function populate(preferred){
    const query=$('graph-search').value.toLocaleLowerCase(),level=$('graph-level').value;
    const type=$('graph-node-type')?.value || 'all';
    const pool=level==='directory'?(data.directories||[]):data.nodes;
    const candidates=pool.filter(n=>(level==='symbol'||n.type===level) && (level!=='symbol'||type==='all'||n.type===type) && (view()!=='flow'||n.type!=='variable') && (label(n)+' '+(n.docstring||'')).toLocaleLowerCase().includes(query));
    const select=$('graph-node');select.replaceChildren();
    for(const n of candidates){const option=text('option',kindsLabel[n.type]+' · '+label(n),select);option.value=n.id;}
    const id=candidates.some(n=>n.id===preferred)?preferred:candidates.find(n=>entries.has(n.id))?.id || candidates[0]?.id;
    if(id)choose(id);else{focus='';$('graph-svg').replaceChildren();$('graph-detail').replaceChildren();$('graph-status').textContent='일치하는 코드 요소가 없습니다.';}
  }
  function draw(){
    if(!focus)return;
    const kinds=new Set(Array.from(document.querySelectorAll('[data-graph-kind]:checked')).map(e=>e.value));
    const level=$('graph-level').value,direction=$('graph-direction').value;
    const origin=$('graph-origin')?.value || 'all';
    const sourceRelations=level==='directory'?(data.directory_edges||[]):level==='file'?data.file_edges:data.symbol_edges;
    const relations=sourceRelations.filter(e=>kinds.has(e.type==='observed_calls'?'calls':e.type) && (origin==='all'||(origin==='runtime'?e.type==='observed_calls':e.type!=='observed_calls')));
    const wanted=new Set([focus]),eligible=[],depths=new Map([[focus,0]]);
    if(view()==='overview'){
      const query=$('graph-search').value.toLocaleLowerCase();
      const type=$('graph-node-type')?.value || 'all';
      for(const n of nodes.values())if((level==='symbol'?n.type!=='directory'&&(type==='all'||n.type===type):n.type===level) && (label(n)+' '+(n.docstring||'')).toLocaleLowerCase().includes(query))wanted.add(n.id);
      eligible.push(...relations.filter(e=>wanted.has(e.source)&&wanted.has(e.target)));
    }else if(view()==='flow'){
      // Reachability follows call arrows only, never lexical contains/import edges.
      const calls=relations.filter(e=>e.type==='calls'||e.type==='observed_calls').map(e=>{
        const ids=e.edge_ids.filter(id=>!['annotation','definition_expression'].includes(edges.get(id).context));
        return {...e,edge_ids:ids,count:ids.reduce((n,id)=>n+(edges.get(id).count||1),0)};
      }).filter(e=>e.edge_ids.length);
      const outgoing=new Map();for(const e of calls){if(!outgoing.has(e.source))outgoing.set(e.source,[]);outgoing.get(e.source).push(e);}
      const queue=[focus];for(let i=0;i<queue.length;i++)for(const e of outgoing.get(queue[i])||[]){
        eligible.push(e);if(!wanted.has(e.target)){wanted.add(e.target);depths.set(e.target,depths.get(e.source)+1);queue.push(e.target);}
      }
    }else{
      for(const e of relations){
        if((direction!=='in'&&expanded.has(e.source))||(direction!=='out'&&expanded.has(e.target))){eligible.push(e);wanted.add(e.source);wanted.add(e.target);}
      }
    }
    // Deterministic caps; all omitted elements are counted instead of silently dropped.
    const shown=Array.from(wanted).slice(0,40),visible=new Set(shown);
    const drawable=eligible.filter(e=>visible.has(e.source)&&visible.has(e.target)).slice(0,100);
    const modeLabel=view()==='overview'?'전체 구조 · 연결 없는 요소도 포함':view()==='flow'?'시작점에서 도달하는 호출 연결 · 열은 최소 호출 단계이며 실행 순서가 아닙니다':'주변 탐색 · 초기 1단계';
    $('graph-status').textContent=`${modeLabel} · 노드 ${shown.length}/${wanted.size} · 관계 묶음 ${drawable.length}/${eligible.length} · 생략 ${wanted.size-shown.length}개 노드 / ${eligible.length-drawable.length}개 관계 · 최대 노드 40 / 관계 100. 화살표는 원래 방향입니다.`;
    $('graph-direction').disabled=view()!=='neighborhood';
    $('graph-expand').disabled=view()!=='neighborhood';
    const canvas=$('graph-svg');canvas.replaceChildren();
    let height=Math.max(400,Math.ceil((shown.length-1)/2)*85+90),width=1000;
    const positions=new Map(),columns=new Map();
    if(view()!=='neighborhood'){
      for(const id of shown){const n=nodes.get(id),key=view()==='flow'?depths.get(id):level==='directory'?'폴더':n.file.includes('/')?n.file.slice(0,n.file.lastIndexOf('/')):'.';if(!columns.has(key))columns.set(key,[]);columns.get(key).push(id);}
      const keys=Array.from(columns.keys()).sort((a,b)=>view()==='flow'?a-b:String(a).localeCompare(String(b)));
      width=Math.max(1000,keys.length*320+40);height=Math.max(400,Math.max(...keys.map(k=>columns.get(k).length))*85+100);
      keys.forEach((key,col)=>columns.get(key).forEach((id,row)=>positions.set(id,{x:180+col*320,y:100+row*85})));
    }else{
      positions.set(focus,{x:500,y:height/2});
      shown.filter(id=>id!==focus).forEach((id,i)=>positions.set(id,{x:i%2?815:185,y:55+Math.floor(i/2)*85}));
    }
    canvas.setAttribute('viewBox',`0 0 ${width} ${height}`);canvas.style.width=(scale*100)+'%';
    canvas.style.minWidth=(width*scale)+'px';
    const defs=svg('defs',{},canvas);
    for(const kind of Object.keys(color)){const marker=svg('marker',{id:'graph-arrow-'+kind,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'},defs);svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color[kind]},marker);}
    for(const [key,ids] of columns){const p=positions.get(ids[0]);svg('text',{x:p.x,y:38,'text-anchor':'middle','font-size':14,fill:'#245448'},canvas).textContent=view()==='flow'?'호출 단계 '+key:key;}
    drawable.forEach((e,i)=>{
      const a=positions.get(e.source),b=positions.get(e.target),same=e.source===e.target;
      const path=same?`M ${a.x-35} ${a.y-22} C ${a.x-100} ${a.y-90},${a.x+100} ${a.y-90},${a.x+35} ${a.y-22}`:a.x===b.x?`M ${a.x+130} ${a.y} C ${a.x+160} ${a.y},${b.x+160} ${b.y},${b.x+130} ${b.y}`:
        `M ${a.x} ${a.y} Q ${(a.x+b.x)/2} ${(a.y+b.y)/2+24+(i%3)*12} ${b.x+(a.x>b.x?130:-130)} ${b.y}`;
      const g=svg('g',{class:'graph-edge',tabindex:0,role:'button','aria-label':e.type+' '+label(nodes.get(e.source))+' → '+label(nodes.get(e.target))},canvas);
      svg('path',{d:path,stroke:color[e.type],fill:'none','stroke-width':2,'stroke-dasharray':e.type==='contains'?'5 4':'none','marker-end':'url(#graph-arrow-'+e.type+')'},g);
      const t=svg('text',{x:same?a.x:a.x===b.x?a.x+140:(a.x+b.x)/2,y:same?a.y-62:(a.y+b.y)/2+12+(i%3)*12,fill:color[e.type],'font-size':12},g);t.textContent=(e.type==='observed_calls'?'실행 관측':e.type)+' ×'+(e.count??e.edge_ids.length);
      g.onclick=()=>evidence(e.edge_ids);g.onkeydown=event=>{if(event.key==='Enter')evidence(e.edge_ids);};
    });
    for(const id of shown){const n=nodes.get(id),p=positions.get(id),g=svg('g',{class:'graph-node',tabindex:0,role:'button','aria-label':label(n)},canvas);
      g.setAttribute('data-node-id',id);
      svg('rect',{x:p.x-130,y:p.y-23,width:260,height:46,rx:n.type==='variable'?20:8,fill:id===focus?'#d5eee3':n.type==='class'?'#eee9fb':n.type==='variable'?'#fff2df':'white',stroke:entries.has(id)?'#bc7a20':'#6b9f8d','stroke-width':entries.has(id)?3:1},g);
      const title=svg('title',{},g);title.textContent=label(n);
      const t=svg('text',{x:p.x,y:p.y-2,'text-anchor':'middle','font-size':12,fill:'#17312e'},g);const name=['file','directory'].includes(n.type)?n.file:n.qualified_name;t.textContent=name.length>32?name.slice(0,29)+'…':name;
      svg('text',{x:p.x,y:p.y+15,'text-anchor':'middle','font-size':10,fill:'#536966'},g).textContent=kindsLabel[n.type]+(entries.has(id)?' · 진입점 후보':'')+(n.type==='directory'?' · 파일 '+n.file_ids.length+'개':'')+(data.runtime?.node_entries[id]?' · 실행 '+data.runtime.node_entries[id]+'회':'');
      g.onclick=()=>describe(id);g.ondblclick=()=>choose(id);g.onkeydown=e=>{if(e.key==='Enter')describe(id);};
    }
  }
  $('graph-search').oninput=()=>populate(focus);
  $('graph-level').onchange=()=>{if(view()==='flow'&&$('graph-view'))$('graph-view').value='overview';populate();};
  if($('graph-view'))$('graph-view').onchange=()=>{if(view()==='flow'){$('graph-level').value='symbol';$('graph-node-type').value='all';}populate(focus);};
  if($('graph-node-type'))$('graph-node-type').onchange=()=>populate();
  $('graph-node').onchange=e=>choose(e.target.value);
  $('graph-direction').onchange=draw;
  if($('graph-origin'))$('graph-origin').onchange=draw;
  document.querySelectorAll('[data-graph-kind]').forEach(e=>e.onchange=draw);
  $('graph-reset').onclick=()=>{expanded=new Set([focus]);scale=1;draw();};
  $('graph-expand').onclick=()=>{for(const el of $('graph-svg').querySelectorAll('.graph-node'))expanded.add(el.getAttribute('data-node-id'));draw();};
  $('graph-zoom-in').onclick=()=>{scale=Math.min(3,scale+.25);draw();};
  $('graph-zoom-out').onclick=()=>{scale=Math.max(.75,scale-.25);draw();};
  document.querySelectorAll('[data-graph-focus]').forEach(button=>{
    const id=button.getAttribute('data-graph-focus');if(!nodes.has(id))return;
    button.onclick=()=>{$('graph-view').value='flow';$('graph-level').value='symbol';$('graph-node-type').value='all';$('graph-search').value='';populate(id);$('graph-svg').scrollIntoView({block:'center'});};
  });
  populate();
})();
"""


def script_hash():
    return base64.b64encode(hashlib.sha256(SCRIPT.encode("utf-8")).digest()).decode("ascii")


def render_overview_markdown(result):
    data = graph_data(result)
    files = [n for n in data["nodes"] if n["type"] == "file"]
    ids = {n["id"]: f"f{i}" for i, n in enumerate(files[:40])}
    relations = [e for e in data["file_edges"] if e["type"] in {"calls", "imports"} and e["source"] != e["target"]]
    shown = [e for e in relations if e["source"] in ids and e["target"] in ids][:80]
    lines = ["## 전체 구조 그래프", "", "폴더별 파일과 파일 사이 호출·import를 표시합니다. 연결 없는 파일도 포함하며 실제 실행 순서는 아닙니다.", "",
             f"파일 {len(ids)}/{len(files)}개 · 관계 묶음 {len(shown)}/{len(relations)}개. 생략된 요소는 HTML 검색·폴더 요약에서 탐색하세요.", ""]
    if not files:
        return "\n".join(lines + ["표시할 Python 파일이 없습니다."])
    # Keep code-derived text out of Mermaid syntax, including fence delimiters.
    def safe(value):
        return ''.join(ch if ch.isalnum() or ch in ' ._/' else ' ' for ch in value)[:100]
    names = {n["id"]: n["file"] for n in files}
    lines += ["```mermaid", "flowchart LR"]
    for i, directory in enumerate(data["directories"]):
        members = [nid for nid in directory["file_ids"] if nid in ids]
        if members:
            lines.append(f'  subgraph d{i}["{safe(directory["name"])}"]')
            lines.extend(f'    {ids[nid]}["{safe(names[nid])}"]' for nid in members)
            lines.append("  end")
    lines.extend(f'  {ids[e["source"]]} -->|{e["type"]} x{e["count"]}| {ids[e["target"]]}' for e in shown)
    for entry in data["entry_points"]:
        if entry["node_id"] in ids:
            lines.append(f'  style {ids[entry["node_id"]]} stroke:#bc7a20,stroke-width:3px')
    return "\n".join(lines + ["```", "", "금색 테두리는 정적 규칙에 따른 진입점 후보입니다. 근거와 함수 수준 호출 흐름은 report.html에서 확인하세요."])


def render_graph(result, *, runtime=None):
    payload = json.dumps(graph_data(result, runtime=runtime), ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return '''<section id="graph"><h2>관계 그래프</h2>
<p class="muted">전체 구조에서 폴더·파일 연결을 살펴보고, 요소를 선택해 호출 흐름이나 주변 관계로 이동하세요. 금색 테두리는 진입점 후보입니다. 호출 흐름은 도달 가능한 연결이며 분기 조건이나 실행 순서를 확정하지 않습니다. 실제 실행 순서와 시간은 동적 보고서의 호출 타임라인에서 확인하세요. 관계 선을 선택하면 근거 위치를 확인합니다.</p>
<noscript>그래프는 JavaScript가 필요합니다. 아래 파일 관계와 코드 상세는 JavaScript 없이도 읽을 수 있습니다.</noscript>
<div class="graph-controls">
<label>보기<select id="graph-view"><option value="overview">전체 구조</option><option value="flow">호출 흐름</option><option value="neighborhood">주변 관계 탐색</option></select></label>
<label>이름·경로·docstring 검색<input id="graph-search" type="search" placeholder="함수 또는 파일 이름"></label>
<label>표시 단위<select id="graph-level"><option value="file">파일 요약</option><option value="directory">폴더 요약</option><option value="symbol">함수·클래스·변수</option></select></label>
<label>중심 요소 종류<select id="graph-node-type"><option value="all">전체</option><option value="function">함수</option><option value="method">메서드</option><option value="class">클래스</option><option value="variable">변수</option><option value="file">파일</option></select></label>
<label>중심 요소<select id="graph-node" aria-label="중심 요소"></select></label>
<label>방향<select id="graph-direction"><option value="both">양방향</option><option value="out">나가는 관계</option><option value="in">들어오는 관계</option></select></label>
<label>관계 출처<select id="graph-origin"><option value="all">함께 보기</option><option value="static">정적 분석</option><option value="runtime">실행 관측 (보라색)</option></select></label>
<label><input type="checkbox" data-graph-kind value="calls" checked>calls · 호출</label>
<label><input type="checkbox" data-graph-kind value="imports" checked>imports · 가져오기</label>
<label><input type="checkbox" data-graph-kind value="contains" checked>contains · 소속</label>
<label><input type="checkbox" data-graph-kind value="reads" checked>reads · 읽기</label>
<label><input type="checkbox" data-graph-kind value="writes" checked>writes · 이름 쓰기</label>
<label><input type="checkbox" data-graph-kind value="deletes" checked>deletes · 이름 삭제</label>
<label><input type="checkbox" data-graph-kind value="references" checked>references · 참조</label>
<label><input type="checkbox" data-graph-kind value="inherits" checked>inherits · 상속</label>
<label><input type="checkbox" data-graph-kind value="depends_on" checked>depends_on · 대입식 참조</label>
</div><div class="graph-actions"><button id="graph-expand">표시된 노드 주변 확장</button><button id="graph-reset">확장·배율 초기화</button><button id="graph-zoom-in" aria-label="확대">확대 +</button><button id="graph-zoom-out" aria-label="축소">축소 −</button></div>
<p id="graph-status" class="meta" role="status"></p><div class="graph-layout"><div class="graph-canvas"><svg id="graph-svg" role="group" aria-label="프로젝트 구조 및 호출 관계 그래프"></svg></div><div id="graph-detail" class="graph-detail" aria-live="polite"></div></div>
</section>''' + '<script type="application/json" id="graph-data">' + payload + '</script>' + '<script>' + SCRIPT + '</script>'
