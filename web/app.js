const $ = id => document.getElementById(id);
let csrf = '', active = null, timer = null, lastTask = null;
const money = minor => new Intl.NumberFormat('en-IN', {style:'currency', currency:'INR'}).format(minor / 100);
function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if(cls) n.className=cls; return n; }
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Worker-CSRF':csrf}, body:JSON.stringify(body)});
  const value = await r.json(); if(!r.ok) throw new Error(value.error || 'Request failed'); return value;
}
function invoiceCard(i, verified=false) {
  const card=el('div',undefined,'invoice-card'); card.append(el('strong',`${verified?'✓ ':''}${i.invoice_number} · ${money(i.amount_minor)}`));
  card.append(el('div',`Due ${i.due} · Source ${i.source_id}`));
  if(verified) card.append(el('div',`SHA-256 ${i.source_hash.slice(0,20)}… · All required fields matched`)); return card;
}
function render(task) {
  lastTask=task; $('status').textContent=task.status.replaceAll('_',' ').toUpperCase(); $('status').className='tag '+task.status;
  $('summary').textContent=task.state.summary;
  const timeline=$('timeline'), bottom=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight < 80;
  timeline.replaceChildren();
  for(const event of task.events) {
    const row=el('div',undefined,'event '+event.kind);
    const icon={plan:'↗',action:'→',observation:'✓',retry:'↻',approval:'!',human:'◉',complete:'✓',blocked:'!',clarification:'?',error:'!'}[event.kind]||'·';
    row.append(el('div',icon,'event-dot')); const content=el('div'); content.append(el('div',event.message,'event-line'));
    content.append(el('div',`${event.kind.toUpperCase()} · ${new Date(event.at).toLocaleTimeString()}`,'event-time'));
    if(Object.keys(event.detail).length) {const details=el('details'); details.append(el('summary','Inspect observation')); details.append(el('pre',JSON.stringify(event.detail,null,2))); content.append(details);}
    row.append(content);timeline.append(row);
  }
  if(bottom) timeline.scrollTop=timeline.scrollHeight;
  $('approval').hidden=task.status!=='awaiting_approval'; $('preview').replaceChildren(...Object.values(task.state.invoices).map(i=>invoiceCard(i)));
  const evidence=Object.values(task.state.evidence); $('evidence').hidden=!evidence.length; $('evidence-items').replaceChildren(...evidence.map(i=>invoiceCard(i,true)));
  $('run').disabled=['running','queued'].includes(task.status);
}
async function refreshLedger() {
  const rows=await api('/api/ledger'); $('ledger-count').textContent=`${rows.length} RECORD${rows.length===1?'':'S'}`; const body=$('ledger');body.replaceChildren();
  if(!rows.length) {const row=el('tr'),cell=el('td','No invoices recorded yet. Run a task to populate the ledger.','table-empty');cell.colSpan=5;row.append(cell);body.append(row);}
  for(const i of rows) { const row=el('tr'),first=el('td',i.invoice_number);first.append(el('small',i.vendor));row.append(first,el('td',money(i.amount_minor)),el('td',i.due),el('td',i.source_id),el('td',new Date(i.recorded_at).toLocaleString()));body.append(row); }
}
async function refreshHistory() { const tasks=await api('/api/tasks');$('history').replaceChildren(...tasks.map(t=>{const b=el('button',t.request);b.append(el('small',t.status.replaceAll('_',' ')));b.onclick=()=>watch(t.id);return b;})); }
async function poll() {
  const polledId=active;
  try { const task=await api('/api/tasks/'+polledId);if(polledId!==active)return;render(task);await refreshLedger();
    if(!['running','queued'].includes(task.status)){clearInterval(timer);timer=null;await refreshHistory();}
  } catch(e) {clearInterval(timer);timer=null;$('summary').textContent=e.message;$('run').disabled=false;}
}
async function watch(id) {clearInterval(timer);active=id;await poll();if(lastTask&&['running','queued'].includes(lastTask.status))timer=setInterval(poll,700);}
$('run').onclick=async()=>{ $('run').disabled=true;try {const t=await api('/api/tasks',{request:$('request').value,mode:$('mode').value,fault:$('fault').value});await watch(t.id);await refreshHistory();}catch(e){$('summary').textContent=e.message;$('run').disabled=false;} };
for(const b of document.querySelectorAll('[data-task]'))b.onclick=()=>{$('request').value=b.dataset.task;$('request').focus();};
async function decide(approve) { $('approve').disabled=$('decline').disabled=true;try{await api('/api/tasks/'+active+'/approval',{approve});await watch(active);}catch(e){$('summary').textContent=e.message;}finally{$('approve').disabled=$('decline').disabled=false;} }
$('approve').onclick=()=>decide(true);$('decline').onclick=()=>decide(false);
$('download').onclick=async()=>{try{const evidence=await api('/api/tasks/'+active+'/evidence');const url=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='task-'+active+'-evidence.json';a.click();URL.revokeObjectURL(url);}catch(e){$('summary').textContent=e.message;}};
async function init(){try{const session=await api('/api/session');csrf=session.csrf;if(session.llm_configured){$('mode').value='llm';$('mode').querySelector('option[value="llm"]').textContent='LLM · '+session.model;}const docs=await api('/api/documents');for(const d of docs){const row=el('div',undefined,'doc');row.append(el('span','▤','doc-icon'),el('span',d.vendor+' · '+d.id,'doc-name'),el('span',d.issued,'doc-date'),el('span',d.unpaid?'unpaid':'paid','doc-state'));$('documents').append(row);}await refreshLedger();await refreshHistory();}catch(e){$('summary').textContent='Cannot reach the local server: '+e.message;}}
init();
