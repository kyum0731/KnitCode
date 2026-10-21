// Optional Node smoke check using a minimal DOM. This is not browser visual QA.
import assert from 'node:assert/strict';

class Element {
  constructor(tag) {this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.style={};this.value='';this.textContent='';}
  append(el) {this.children.push(el);el.parentElement=this;}
  replaceChildren(...els) {this.children=[];els.forEach(el=>this.append(el));}
  setAttribute(key,value) {this.attrs[key]=value;}
  getAttribute(key) {return this.attrs[key];}
  addEventListener(event,handler) {this['on'+event]=handler;}
  scrollIntoView() {this.scrolled=true;}
  querySelectorAll(selector) {return this.children.flatMap(el=>[(el.attrs.class===selector.slice(1)?el:null),...el.querySelectorAll(selector)]).filter(Boolean);}
}
const ids = new Map(['graph-data','graph-search','graph-level','graph-node','graph-node-type','graph-direction','graph-origin','graph-svg','graph-status','graph-detail','graph-reset','graph-expand','graph-zoom-in','graph-zoom-out'].map(id=>[id,new Element('div')]));
const kinds=['calls','imports','contains','reads','writes','deletes','references','inherits','depends_on'].map(value=>Object.assign(new Element('input'),{value,checked:true}));
ids.get('graph-node-type').value='all';
ids.get('graph-level').value='file';ids.get('graph-direction').value='both';
ids.get('graph-origin').value='all';
const range={start_line:1,start_col:0,end_line:1,end_col:1};
const nodes=[{id:'a',type:'file',file:'a.py',name:'a.py',qualified_name:''},{id:'b',type:'file',file:'b.py',name:'b.py',qualified_name:''},{id:'c',type:'function',file:'b.py',name:'run',qualified_name:'run'}];
const edges=[{id:'e1',source:'a',target:'c',type:'calls',expression:'run()',resolution_status:'resolved',evidence:{file:'a.py',range}},{id:'e2',source:'a',target:null,type:'calls',expression:'unknown()',resolution_status:'unresolved',evidence:{file:'a.py',range}},{id:'e3',source:'b',target:'c',type:'contains',evidence:{file:'b.py',range}}];
edges.push({id:'e4',source:'a',target:'c',type:'observed_calls',count:2,expression:'실행 관측',resolution_status:'observed',evidence:{file:'a.py',range}});
ids.get('graph-data').textContent=JSON.stringify({nodes,edges,file_edges:[{source:'a',target:'b',type:'calls',edge_ids:['e1']},{source:'a',target:'b',type:'observed_calls',count:2,edge_ids:['e4']}],symbol_edges:edges.filter(e=>e.target).map(e=>({...e,edge_ids:[e.id]}))});
const candidateButtons=[];
globalThis.document={getElementById:id=>ids.get(id),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:selector=>selector==='[data-graph-focus]'?candidateButtons:selector.endsWith(':checked')?kinds.filter(k=>k.checked):kinds};
await import('../build/graph-script.mjs');
const status=()=>ids.get('graph-status').textContent;
assert.match(status(),/노드 2\/2/);
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,2);
ids.get('graph-origin').value='runtime';ids.get('graph-origin').onchange();
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,1);
assert.match(ids.get('graph-svg').querySelectorAll('.graph-edge')[0].children[1].textContent,/실행 관측 ×2/);
ids.get('graph-origin').value='static';ids.get('graph-origin').onchange();
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,1);
ids.get('graph-origin').value='all';ids.get('graph-origin').onchange();
ids.get('graph-svg').querySelectorAll('.graph-edge')[0].onclick();
assert.equal(ids.get('graph-detail').children[0].textContent,'관계 근거 1개');
ids.get('graph-direction').value='in';ids.get('graph-direction').onchange();
assert.match(status(),/노드 1\/1/);
ids.get('graph-direction').value='both';ids.get('graph-direction').onchange();
kinds[0].checked=false;kinds[0].onchange();assert.match(status(),/관계 묶음 0\/0/);
kinds[0].checked=true;kinds[0].onchange();
ids.get('graph-level').value='symbol';ids.get('graph-level').onchange();
ids.get('graph-expand').onclick();assert.match(status(),/노드 3\/3/);
ids.get('graph-reset').onclick();assert.match(status(),/노드 2\/2/);
ids.get('graph-search').value='no-match';ids.get('graph-search').oninput();assert.match(status(),/없습니다/);
ids.get('graph-search').value='run';ids.get('graph-search').oninput();assert.match(status(),/노드 3\/3/);
ids.get('graph-zoom-in').onclick();assert.equal(ids.get('graph-svg').style.width,'125%');
ids.get('graph-level').value='file';ids.get('graph-direction').value='both';ids.get('graph-search').value='';
const denseNodes=Array.from({length:46},(_,i)=>({id:'n'+i,type:'file',file:i+'.py',qualified_name:'',name:i+'.py'}));
const denseEdges=denseNodes.slice(1).flatMap(n=>['calls','imports','contains'].map(type=>({id:n.id+type,source:'n0',target:n.id,type,evidence:{file:'0.py',range},edge_ids:[n.id+type]})));
ids.get('graph-data').textContent=JSON.stringify({nodes:denseNodes,edges:denseEdges,file_edges:denseEdges,symbol_edges:denseEdges});
await import('../build/graph-script-dense.mjs');
assert.match(status(),/노드 40\/46/);assert.match(status(),/관계 묶음 100\/135/);
const symbolNodes=[
  {id:'f',type:'function',name:'run',qualified_name:'run',file:'app.py'},
  {id:'v',type:'variable',name:'value',qualified_name:'run.value',file:'app.py'},
  {id:'child',type:'class',name:'Child',qualified_name:'Child',file:'app.py'},
  {id:'base',type:'class',name:'Base',qualified_name:'Base',file:'base.py'}
];
const symbolEdges=[['reads','f','v'],['writes','f','v'],['inherits','child','base'],['references','f','child']].map(([type,source,target],i)=>({id:'s'+i,type,source,target,expression:type,evidence:{file:'app.py',range},resolution_status:'resolved',edge_ids:['s'+i]}));
ids.get('graph-data').textContent=JSON.stringify({nodes:symbolNodes,edges:symbolEdges,file_edges:[],symbol_edges:symbolEdges});
ids.get('graph-level').value='symbol';
await import('../build/graph-script-symbols.mjs');
assert.ok(status().includes('노드 3/3'));
ids.get('graph-node-type').value='variable';ids.get('graph-node-type').onchange();
assert.equal(ids.get('graph-node').children.length,1);
assert.ok(status().includes('노드 2/2'));
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,2);
kinds.find(k=>k.value==='reads').checked=false;kinds.find(k=>k.value==='reads').onchange();
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,1);
ids.get('graph-svg').querySelectorAll('.graph-edge')[0].onclick();
assert.match(ids.get('graph-detail').children[1].children[0].children[0].textContent,/writes/);
ids.get('graph-node-type').value='class';ids.get('graph-node-type').onchange();
assert.equal(ids.get('graph-node').children.length,2);
assert.ok(ids.get('graph-svg').querySelectorAll('.graph-edge').some(e=>e.attrs['aria-label'].startsWith('inherits')));
// Whole-project overview includes disconnected files; flow reaches beyond one hop
// and terminates on recursion without treating imports or annotations as calls.
ids.set('graph-view',new Element('select'));ids.get('graph-view').value='overview';
ids.get('graph-node-type').value='all';ids.get('graph-level').value='file';
const overviewNodes=[...nodes,{id:'d',type:'function',file:'b.py',qualified_name:'finish',name:'finish'},
  {id:'z',type:'file',file:'isolated.py',qualified_name:'',name:'isolated.py'}];
const flowEdges=[...edges,
  {id:'e5',source:'c',target:'d',type:'calls',evidence:{file:'b.py',range}},
  {id:'e6',source:'d',target:'c',type:'calls',evidence:{file:'b.py',range}},
  {id:'e7',source:'c',target:'z',type:'calls',context:'annotation',evidence:{file:'b.py',range}},
  {id:'e8',source:'c',target:'b',type:'imports',evidence:{file:'b.py',range}}];
ids.get('graph-data').textContent=JSON.stringify({nodes:overviewNodes,edges:flowEdges,
  file_edges:[{source:'a',target:'b',type:'calls',edge_ids:['e1']}],
  symbol_edges:flowEdges.filter(e=>e.target).map(e=>({...e,edge_ids:[e.id]})),
  directories:[{id:'p',type:'directory',name:'.',file:'.',file_ids:['a','b','z']}],directory_edges:[],
  entry_points:[{node_id:'a',rule:'main_guard'}],entry_candidates:[{node_id:'c',rule:'root_function',score:20}]});
const candidateButton=new Element('button');candidateButton.setAttribute('data-graph-focus','c');candidateButtons.push(candidateButton);
await import('../build/graph-script-overview.mjs');
assert.match(status(),/전체 구조/);assert.match(status(),/노드 3\/3/);
assert.equal(ids.get('graph-node').value,'a');
assert.equal(ids.get('graph-direction').disabled,true);
candidateButton.onclick();
assert.equal(ids.get('graph-node').value,'c');assert.match(status(),/노드 2\/2/);
assert.equal(ids.get('graph-svg').scrolled,true);
assert.match(ids.get('graph-detail').children[2].textContent,/root_function/);
ids.get('graph-view').value='overview';ids.get('graph-view').onchange();
ids.get('graph-level').value='directory';ids.get('graph-level').onchange();
assert.match(status(),/노드 1\/1/);
assert.match(ids.get('graph-detail').children[2].textContent,/직접 포함된 파일 3개/);
ids.get('graph-level').value='file';ids.get('graph-level').onchange();
ids.get('graph-view').value='flow';ids.get('graph-view').onchange();
assert.match(status(),/호출 단계/);assert.match(status(),/노드 3\/3/);
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,4);
assert.ok(!ids.get('graph-svg').querySelectorAll('.graph-node').some(e=>e.attrs['data-node-id']==='z'));
ids.get('graph-origin').value='runtime';ids.get('graph-origin').onchange();
assert.match(status(),/노드 2\/2/);
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,1);
ids.get('graph-origin').value='static';ids.get('graph-origin').onchange();
assert.equal(ids.get('graph-svg').querySelectorAll('.graph-edge').length,3);
ids.get('graph-search').value='missing';ids.get('graph-search').oninput();assert.match(status(),/없습니다/);
delete globalThis.document;
console.log('Graph DOM smoke: overview, directories, isolated files, entry points, transitive flow, cycles, annotation exclusion, variables, inheritance, origins, counts, filters, expansion, search, evidence, zoom and caps OK');
