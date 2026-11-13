const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const view = $('#view');
const modal = $('#modal');
const modalForm = $('#modal-form');
let currentPage = 'overview';
let currentSemester = null;
let currentSubject = null;
let currentTask = null;
let sessionTick = null;
let checkinTimer = null;
let lastSessionId = null;
let modalAction = null;

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateLabel = value => value ? new Date(value.length === 10 ? `${value}T12:00:00` : value).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}) : '—';
const dateTimeLabel = value => value ? new Date(value).toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}) : '—';
const num = value => Number(value || 0);
const localDateISO = (value=new Date()) => { const d=new Date(value); d.setMinutes(d.getMinutes()-d.getTimezoneOffset()); return d.toISOString().slice(0,10); };
const token = () => localStorage.getItem('monday_api_key') || '';

async function api(path, options={}) {
  const headers = {'Content-Type':'application/json','X-API-Key':token(),...(options.headers||{})};
  const response = await fetch(path,{...options,headers});
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = {detail:raw}; }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}
function toast(message) {
  const el=$('#toast'); el.textContent=message; el.classList.add('show');
  clearTimeout(toast._timer); toast._timer=setTimeout(()=>el.classList.remove('show'),3200);
}
function setConnection(online, text='') {
  $('#connection-pill').textContent = online ? '● Connected' : '● Not connected';
  $('#connection-pill').style.color = online ? '#21845a' : '#687789';
  $('#device-status').textContent = online ? 'Website API connected' : (text || 'Not connected');
  $('.status-dot').classList.toggle('online',online);
}
function openModal(title, body, action, saveLabel='Save', hideSave=false) {
  $('#modal-title').textContent=title;
  $('#modal-body').innerHTML=body;
  $('#modal-save').textContent=saveLabel;
  $('#modal-save').style.display=hideSave?'none':'';
  $('#modal-actions').style.display=hideSave?'none':'flex';
  modalAction=action;
  modal.showModal();
}
function closeModal(){modal.close();modalAction=null;}
$('#modal-close').addEventListener('click',closeModal);
$('#modal-cancel').addEventListener('click',closeModal);
modalForm.addEventListener('submit',async e=>{
  e.preventDefault();
  if(!modalAction)return;
  const submit=$('#modal-save'); submit.disabled=true;
  try { await modalAction(new FormData(modalForm)); closeModal(); }
  catch(err){toast(err.message);}
  finally{submit.disabled=false;}
});

function connectDialog(){
  openModal('Connect to Monday',`<p class="muted small">Enter the existing MONDAY_API_TOKEN from <span class="mono">C:\\MondayServer\\.env</span>. It is stored in this browser only.</p><div class="field"><label>API TOKEN</label><input name="api_token" type="password" autocomplete="off" required value="${esc(token())}"></div>`,async fd=>{
    const value=String(fd.get('api_token')||'').trim();
    if(!value)throw new Error('Enter the API token.');
    localStorage.setItem('monday_api_key',value);
    try { await api('/api/ping'); setConnection(true);toast('Connected to Monday.');await render(); }
    catch(e){localStorage.removeItem('monday_api_key');setConnection(false);throw e;}
  });
}
$('#connect-button').addEventListener('click',connectDialog);

function nav(page){
  currentPage=page; currentSemester=null;currentSubject=null;currentTask=null;
  $$('.nav-button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  render();
}
$$('.nav-button[data-page]').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.page)));

function pageTitle(title){$('#page-title').textContent=title;}
function header(title,subtitle,actions=''){
  return `<div class="section-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div><div>${actions}</div></div>`;
}
function progressBar(value){const p=Math.max(0,Math.min(100,num(value)));return `<div class="progress-row"><div class="progress-track" style="flex:1"><div class="progress-fill" style="width:${p}%"></div></div><span>${p}%</span></div>`;}
function priorityBadge(p){return `<span class="badge ${p==='high'?'red':p==='low'?'':'amber'}">${esc((p||'normal').toUpperCase())}</span>`;}
function empty(title,detail){return `<div class="empty"><strong>${esc(title)}</strong>${esc(detail)}</div>`;}
function button(text,action,cls='button-outline',extra=''){return `<button class="button ${cls}" data-action="${action}" ${extra}>${text}</button>`;}

async function render(){
  if(!token()) { setConnection(false); pageTitle('Connect Monday'); view.innerHTML=`<div class="panel" style="max-width:560px;margin:45px auto"><h2>Connect to your Monday server</h2><p>Enter your existing API token to open the local control panel. Your database stays on this computer.</p><button class="button button-primary" id="connect-first">Connect</button></div>`;$('#connect-first').onclick=connectDialog;return; }
  try { await api('/api/ping');setConnection(true); }
  catch(e){setConnection(false);pageTitle('Connection required');view.innerHTML=`<div class="panel" style="max-width:600px"><h2>Cannot reach Monday's web API</h2><p>${esc(e.message)}</p><p class="muted small">Make sure the Monday Web API is running on this computer.</p><button class="button button-primary" id="retry-connect">Retry</button></div>`;$('#retry-connect').onclick=render;return;}
  try {
    if(currentTask){await renderTask(currentTask);return;}
    if(currentSubject){await renderSubject(currentSubject);return;}
    if(currentSemester){await renderSemester(currentSemester);return;}
    if(currentPage==='overview')await renderOverview();
    else if(currentPage==='semesters')await renderSemesters();
    else if(currentPage==='alarms')await renderAlarms();
    else if(currentPage==='reports')await renderReports();
    else if(currentPage==='settings')renderSettings();
    else await renderOverview();
  } catch(e){view.innerHTML=`<div class="panel">${empty('Could not load this section',e.message)}</div>`;}
}

async function renderOverview(){
  pageTitle('Overview'); const d=await api('/api/dashboard');
  view.innerHTML=`<div class="grid stats-grid">
    <div class="stat-card"><div class="stat-label">ACTIVE SEMESTERS</div><div class="stat-value">${d.active_semesters}</div><div class="stat-foot">Currently in progress</div></div>
    <div class="stat-card"><div class="stat-label">TOTAL TASKS</div><div class="stat-value">${d.total_tasks}</div><div class="stat-foot">All saved tasks in your workspace</div></div>
    <div class="stat-card"><div class="stat-label">COMPLETED TASKS</div><div class="stat-value">${d.completed_tasks}</div><div class="stat-foot">${d.completion_percent}% overall completion</div></div>
    <div class="stat-card"><div class="stat-label">ACTIVE ALARMS</div><div class="stat-value">${d.enabled_alarms}</div><div class="stat-foot">Configured alarms</div></div>
  </div>
  <div class="grid two-col">
    <div class="panel"><div class="panel-head"><h2>Upcoming deadlines</h2><button class="button button-outline button-small" data-action="go-semesters">View subjects</button></div>
      ${d.upcoming_tasks.length?`<div class="table-wrap"><table><thead><tr><th>TASK</th><th>SUBJECT</th><th>DUE DATE</th><th>PRIORITY</th></tr></thead><tbody>${d.upcoming_tasks.map(t=>`<tr><td><button class="table-link" data-task="${t.id}">${esc(t.title)}</button></td><td>${esc(t.subject_code||'—')}</td><td>${dateLabel(t.due_date)}</td><td>${priorityBadge(t.priority)}</td></tr>`).join('')}</tbody></table></div>`:empty('No upcoming deadlines','Create a semester, subject and tasks to get started.')}
    </div>
    <div class="panel"><div class="panel-head"><h2>Study session</h2></div>
      ${d.active_session?`<div class="session-box"><div class="muted small">CURRENTLY WORKING ON</div><strong>${esc(d.active_session.task_title)}</strong><div class="muted small">${esc(d.active_session.subject_code||'')} · Started ${dateTimeLabel(d.active_session.started_at)}</div><div class="session-time" id="session-countdown">--:--:--</div><div class="card-actions"><button class="button button-primary" data-session-end="${d.active_session.id}">End session</button><button class="button button-outline" data-session-early="${d.active_session.id}">End early</button></div></div>`:empty('No active session','Open a task and start a focused study session.')}
    </div>
  </div>
  <div class="panel"><div class="panel-head"><h2>Overall task completion</h2><span class="muted small">${d.completed_tasks} of ${d.total_tasks} completed</span></div>${progressBar(d.completion_percent)}</div>`;
  startSessionTicker(d.active_session);
}

async function renderSemesters(){
  pageTitle('Semesters & subjects'); const list=await api('/api/semesters');
  view.innerHTML=`${header('Your semesters','Keep each academic semester and its subject records together.',button('+ New semester','new-semester','button-primary'))}
  ${list.length?`<div class="grid cards-grid">${list.map(s=>`<div class="semester-card"><div class="panel-head"><span class="badge ${s.status==='active'?'green':''}">${esc(s.status.toUpperCase())}</span><span class="muted small">${s.subject_count} subjects</span></div><h3>${esc(s.name)}</h3><div class="card-meta">${dateLabel(s.start_date)} – ${dateLabel(s.end_date)}</div>${progressBar(s.task_count?Math.round(100*num(s.completed_count)/num(s.task_count)):0)}<div class="card-meta">${s.completed_count||0} / ${s.task_count} tasks completed</div><div class="card-actions"><button class="button button-primary button-small" data-semester="${s.id}">Open semester</button><button class="button button-outline button-small" data-semester-status="${s.id}" data-status="${s.status}">${s.status==='active'?'Mark completed':'Reopen'}</button></div></div>`).join('')}</div>`:empty('No semesters created yet','Create your first semester to start adding subjects and assignments.')}`;
}

async function renderSemester(id){
  const s=await api(`/api/semesters/${id}`);pageTitle(s.name);
  view.innerHTML=`<button class="crumb" data-action="back-semesters">← All semesters</button>
  <div class="detail-header"><div><h2>${esc(s.name)}</h2><p>${dateLabel(s.start_date)} – ${dateLabel(s.end_date)} · <span class="badge ${s.status==='active'?'green':''}">${esc(s.status)}</span></p></div><div class="card-actions"><button class="button button-outline" data-semester-status="${s.id}" data-status="${s.status}">${s.status==='active'?'Mark completed':'Reopen semester'}</button><button class="button button-primary" data-action="new-subject">+ Add subject</button></div></div>
  ${s.subjects.length?`<div class="grid cards-grid">${s.subjects.map(x=>`<div class="subject-card"><div class="subject-code">${esc(x.code)}</div><h3>${esc(x.name)}</h3><div class="card-meta">${x.task_count} tasks · ${x.completed_count||0} completed</div>${progressBar(x.task_count?Math.round(100*num(x.completed_count)/num(x.task_count)):0)}<div class="card-actions"><button class="button button-primary button-small" data-subject="${x.id}">Open subject</button><button class="button button-outline button-small" data-edit-subject="${x.id}">Edit</button></div></div>`).join('')}</div>`:empty('No subjects in this semester','Add units such as SIT220 or SIT112.')}`;
}

async function renderSubject(id){
  const s=await api(`/api/subjects/${id}`);pageTitle(s.code);
  view.innerHTML=`<button class="crumb" data-action="back-semester">← ${esc(currentSemester?.name||'Semester')}</button>
  <div class="detail-header"><div><div class="subject-code">${esc(s.code)}</div><h2>${esc(s.name)}</h2><p>${s.task_count} tasks · ${s.completed_count} completed · ${s.completion_percent}% complete</p></div><div class="card-actions"><button class="button button-outline" data-edit-subject="${s.id}">Edit subject</button><button class="button button-primary" data-action="new-task">+ Add task</button></div></div>
  <div class="panel"><div class="panel-head"><h2>Subject progress</h2><strong>${s.completion_percent}%</strong></div>${progressBar(s.completion_percent)}</div>
  <div class="panel"><div class="panel-head"><h2>Assignments and tasks</h2><span class="muted small">${s.task_count} total</span></div>
  ${s.tasks.length?`<div class="table-wrap"><table><thead><tr><th>TASK</th><th>START</th><th>DUE</th><th>PRIORITY</th><th>PROGRESS</th><th>STATUS</th></tr></thead><tbody>${s.tasks.map(t=>`<tr><td><button class="table-link" data-task="${t.id}">${esc(t.title)}</button></td><td>${dateLabel(t.start_date)}</td><td>${dateLabel(t.due_date)}</td><td>${priorityBadge(t.priority)}</td><td>${progressBar(t.progress)}</td><td><span class="badge ${t.completed?'green':''}">${t.completed?'COMPLETE':'IN PROGRESS'}</span></td></tr>`).join('')}</tbody></table></div>`:empty('No tasks yet','Use Add task to create the first assignment for this subject.')}</div>`;
}

async function renderTask(id){
  const t=await api(`/api/tasks/${id}`); currentTask=id;pageTitle(t.title);
  const sessions=t.sessions||[];const history=t.history||[];
  const active=sessions.find(x=>x.status==='active');
  if(!currentSubject && t.subject?.id) currentSubject=Number(t.subject.id);
  const backAction=currentSubject?'back-subject':'back-semesters';
  const backLabel=currentSubject?(t.subject?.code||'Subject'):'Subjects';
  view.innerHTML=`<button class="crumb" data-action="${backAction}">← ${esc(backLabel)}</button>
  <div class="detail-header"><div><div class="subject-code">${esc(t.subject?.code||'UNASSIGNED')}</div><h2>${esc(t.title)}</h2><p>Created ${dateLabel(t.created_at)} · ${priorityBadge(t.priority)}</p></div><div class="card-actions"><button class="button button-outline" data-action="edit-task">Edit task</button><button class="button button-danger" data-action="delete-task">Delete</button></div></div>
  <div class="task-layout"><div>
    <div class="panel"><div class="panel-head"><h2>Task details</h2><span class="badge ${t.completed?'green':''}">${t.completed?'COMPLETED':'IN PROGRESS'}</span></div>
      <div class="field-grid"><div><div class="field-help">START DATE</div><strong>${dateLabel(t.start_date)}</strong></div><div><div class="field-help">DUE DATE</div><strong>${dateLabel(t.due_date)}</strong></div></div>
      <div class="field-help" style="margin-top:15px">TASK NOTES</div><p>${t.description?esc(t.description):'<span class="muted">No task notes added.</span>'}</p>
      <div class="field-help">LATEST PROGRESS NOTE</div><p>${t.progress_note?esc(t.progress_note):'<span class="muted">No progress note added.</span>'}</p>
    </div>
    <div class="panel"><div class="panel-head"><h2>Progress history</h2><button class="button button-primary button-small" data-action="update-progress">Update progress</button></div>
      ${history.length?`<div class="history-list">${history.map(h=>`<div class="history-item"><div class="progress-row"><strong>${h.progress}% complete</strong><span>${dateTimeLabel(h.created_at)}</span></div>${h.note?`<p>${esc(h.note)}</p>`:''}</div>`).join('')}</div>`:empty('No progress updates','Progress changes will appear here.')}
    </div>
    <div class="panel"><div class="panel-head"><h2>Study sessions</h2><span class="muted small">${sessions.length} recorded</span></div>
      ${active?`<div class="session-box"><strong>Session active</strong><div class="muted small">Planned until ${dateTimeLabel(active.planned_end)}</div><div class="card-actions"><button class="button button-primary" data-session-end="${active.id}">Finish session</button><button class="button button-outline" data-session-early="${active.id}">End early</button></div></div>`:''}
      <div class="card-actions"><button class="button button-primary" data-action="start-session" ${active?'disabled':''}>Start study session</button></div>
      ${sessions.length?`<div class="history-list" style="margin-top:12px">${sessions.map(x=>`<div class="history-item"><strong>${dateTimeLabel(x.started_at)}</strong><p>${x.planned_minutes} min planned · ${x.status.replace('_',' ')}${x.ended_at?' · ended '+dateTimeLabel(x.ended_at):''}</p></div>`).join('')}</div>`:empty('No study sessions yet','Start a session when you begin working on this task.')}
    </div>
  </div>
  <aside><div class="panel"><h2>Completion</h2><div class="donut-wrap"><div class="donut" style="--pct:${num(t.progress)}%"><strong>${num(t.progress)}%</strong></div><div><div class="stat-label">TASK COMPLETE</div><div class="stat-value" style="font-size:20px">${100-num(t.progress)}%</div><div class="stat-foot">Remaining</div></div></div><div style="margin-top:15px">${progressBar(t.progress)}</div><button class="button button-primary" style="width:100%;margin-top:15px" data-action="update-progress">Update percentage</button></div>
  <div class="panel"><h2>Quick facts</h2><div class="settings-row"><span class="muted">Priority</span>${priorityBadge(t.priority)}</div><div class="settings-row"><span class="muted">Start</span><strong>${dateLabel(t.start_date)}</strong></div><div class="settings-row"><span class="muted">Due</span><strong>${dateLabel(t.due_date)}</strong></div></div></aside></div>`;
  startSessionTicker(active);
}

function renderSettings(){
  pageTitle('Settings');
  view.innerHTML=`<div class="panel" style="max-width:760px"><div class="panel-head"><h2>Connection</h2><button class="button button-outline button-small" id="settings-connect">Update token</button></div><div class="settings-row"><div><strong>Monday Web API</strong><div class="muted small">Local service on port 8002</div></div><span class="badge ${token()?'green':''}">${token()?'TOKEN SAVED':'NOT CONNECTED'}</span></div><div class="settings-row"><div><strong>Database</strong><div class="muted small">Shared SQLite database: monday.db</div></div><span class="badge">LOCAL</span></div><div class="settings-row"><div><strong>External services</strong><div class="muted small">No cloud database or paid API required</div></div><span class="badge green">NOT REQUIRED</span></div><div class="card-actions"><button class="button button-outline" id="forget-token">Disconnect this browser</button></div></div>
  <div class="panel" style="max-width:760px"><h2>Hardware integration status</h2><p>The website stores task, semester and alarm configuration. The Raspberry Pi checks the shared schedule, speaks alarms, accepts physical-button dismissal and provides timed study check-ins. This website does not pretend those hardware actions are already connected.</p><div class="settings-row"><span>Existing voice assistant</span><span class="badge">UNCHANGED</span></div><div class="settings-row"><span>Website task API</span><span class="badge green">AVAILABLE</span></div><div class="settings-row"><span>Pi alarm and focus service</span><span class="badge green">INCLUDED IN PI UPDATE</span></div></div>`;
  $('#settings-connect').onclick=connectDialog;
  $('#forget-token').onclick=()=>{localStorage.removeItem('monday_api_key');setConnection(false);render();};
}

async function renderAlarms(){
  pageTitle('Alarms');const alarms=await api('/api/alarms');const history=await api('/api/alarms/history');
  view.innerHTML=`${header('Alarm clock','Configure alarm times and the topic for the morning trivia.',button('+ New alarm','new-alarm','button-primary'))}
  <div class="panel"><div class="panel-head"><h2>Configured alarms</h2><span class="muted small">${alarms.filter(a=>a.enabled).length} enabled</span></div>
  ${alarms.length?alarms.map(a=>`<div class="alarm-row"><div><div class="alarm-time">${esc(a.alarm_time)}</div><div class="muted small">${a.alarm_date?dateLabel(a.alarm_date):'Repeating'}</div></div><div><div class="alarm-title">${esc(a.title)}</div><div class="alarm-detail">${a.repeat_days?.length?esc(a.repeat_days.join(', ')):'One-time'} · Trivia: ${esc(a.topic||'No topic set')}</div></div><div class="card-actions"><label class="small"><input type="checkbox" data-alarm-toggle="${a.id}" ${a.enabled?'checked':''}> On</label><button class="button button-outline button-small" data-edit-alarm="${a.id}">Edit</button><button class="button button-danger button-small" data-delete-alarm="${a.id}">Delete</button></div></div>`).join(''):empty('No alarms configured','Add a morning alarm and select a trivia topic.')}</div>
  <div class="panel"><div class="panel-head"><h2>Alarm history</h2><span class="muted small">Last 100 records</span></div>${history.length?`<div class="history-list">${history.map(h=>`<div class="history-item"><strong>${esc(h.title)} · ${dateTimeLabel(h.scheduled_for)}</strong><p>${esc(h.outcome)}${h.trivia_total?` · ${h.trivia_correct}/${h.trivia_total} trivia correct`:''}</p></div>`).join('')}</div>`:empty('No alarm history yet','The Raspberry Pi records each trigger and button dismissal here.')}</div>
  <div class="panel"><h2>Desk bot delivery</h2><p>Enabled alarms are delivered by the Raspberry Pi speaker. Press the physical desk button to dismiss an active alarm. The Pi must be powered on and connected to the Monday server at the scheduled time.</p></div>`;
}

async function renderReports(){
  pageTitle('Reports & history');const semesters=await api('/api/semesters');
  view.innerHTML=`${header('Semester records','Review what you completed and how much time you spent in each semester.')}
  ${semesters.length?`<div class="grid cards-grid">${semesters.map(s=>`<div class="semester-card"><span class="badge">${esc(s.status.toUpperCase())}</span><h3 style="margin-top:12px">${esc(s.name)}</h3><div class="card-meta">${s.subject_count} subjects · ${s.task_count} tasks</div><div class="card-actions"><button class="button button-primary button-small" data-report="${s.id}">View report</button><button class="button button-outline button-small" data-semester="${s.id}">Open records</button></div></div>`).join('')}</div>`:empty('No semester records','Create a semester to begin building your academic history.')}`;
}
async function showReport(id){
  const r=await api(`/api/reports/semesters/${id}`);const total=r.subjects.reduce((n,s)=>n+num(s.total),0),done=r.subjects.reduce((n,s)=>n+num(s.completed),0);
  pageTitle('Semester report');
  view.innerHTML=`<button class="crumb" data-action="back-reports">← Reports</button><div class="detail-header"><div><h2>${esc(r.semester.name)}</h2><p>${dateLabel(r.semester.start_date)} – ${dateLabel(r.semester.end_date)} · ${esc(r.semester.status)}</p></div></div>
  <div class="grid stats-grid"><div class="stat-card"><div class="stat-label">SUBJECTS</div><div class="stat-value">${r.subjects.length}</div></div><div class="stat-card"><div class="stat-label">TASKS</div><div class="stat-value">${total}</div></div><div class="stat-card"><div class="stat-label">COMPLETED</div><div class="stat-value">${done}</div></div><div class="stat-card"><div class="stat-label">STUDY TIME</div><div class="stat-value">${Math.floor(r.study_minutes/60)}h ${r.study_minutes%60}m</div></div></div>
  <div class="panel"><h2>Subject completion</h2><div class="table-wrap"><table><thead><tr><th>SUBJECT</th><th>TASKS</th><th>COMPLETED</th><th>AVERAGE PROGRESS</th></tr></thead><tbody>${r.subjects.map(s=>`<tr><td><strong>${esc(s.code)}</strong> · ${esc(s.name)}</td><td>${s.total}</td><td>${s.completed||0}</td><td>${progressBar(s.avg_progress)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

function startSessionTicker(session){
  clearInterval(sessionTick);clearTimeout(checkinTimer);
  if(!session||!session.id)return;
  const planned=new Date(session.planned_end).getTime();
  const tick=()=>{
    const el=$('#session-countdown');if(!el)return;
    const seconds=Math.max(0,Math.floor((planned-Date.now())/1000));
    const h=String(Math.floor(seconds/3600)).padStart(2,'0'),m=String(Math.floor(seconds%3600/60)).padStart(2,'0'),s=String(seconds%60).padStart(2,'0');
    el.textContent=`${h}:${m}:${s}`;
    if(seconds===0){clearInterval(sessionTick);toast('Your planned study time is over.');}
  };
  tick();sessionTick=setInterval(tick,1000);
}

function modalSemester(){openModal('Create semester',`<div class="field"><label>SEMESTER NAME</label><input name="name" placeholder="e.g. Semester 4 · 2027" required></div><div class="field-grid"><div class="field"><label>START DATE</label><input type="date" name="start_date"></div><div class="field"><label>END DATE</label><input type="date" name="end_date"></div></div>`,async fd=>{const s=await api('/api/semesters',{method:'POST',body:JSON.stringify(Object.fromEntries(fd))});currentSemester=s.id;currentPage='semesters';await render();toast('Semester created.');});}
function modalSubject(){openModal('Add subject',`<div class="field"><label>UNIT CODE</label><input name="code" placeholder="SIT220" required></div><div class="field"><label>SUBJECT NAME</label><input name="name" placeholder="Data Wrangling" required></div>`,async fd=>{await api(`/api/semesters/${currentSemester}/subjects`,{method:'POST',body:JSON.stringify(Object.fromEntries(fd))});await renderSemester(currentSemester);toast('Subject added.');});}
function modalEditSubject(id){api(`/api/subjects/${id}`).then(s=>openModal('Edit subject',`<div class="field"><label>UNIT CODE</label><input name="code" value="${esc(s.code)}" required></div><div class="field"><label>SUBJECT NAME</label><input name="name" value="${esc(s.name)}" required></div>`,async fd=>{await api(`/api/subjects/${id}`,{method:'PATCH',body:JSON.stringify(Object.fromEntries(fd))});await renderSemester(currentSemester);toast('Subject updated.');})).catch(e=>toast(e.message));}
function modalTask(){openModal('Add task',`<div class="field"><label>TASK NAME</label><input name="title" placeholder="CREDIT-1 / HIGH DISTINCTION 2" required></div><div class="field-grid"><div class="field"><label>START DATE</label><input type="date" name="start_date"></div><div class="field"><label>DUE DATE</label><input type="date" name="due_date"></div></div><div class="field"><label>PRIORITY</label><select name="priority"><option value="normal">Normal</option><option value="high">High</option><option value="low">Low</option></select></div><div class="field"><label>NOTES</label><textarea name="description" placeholder="A short description or requirements..."></textarea></div>`,async fd=>{await api('/api/tasks',{method:'POST',body:JSON.stringify({...Object.fromEntries(fd),subject_id:currentSubject})});await renderSubject(currentSubject);toast('Task added.');});}
async function modalEditTask(){const taskId=currentTask;const t=await api(`/api/tasks/${taskId}`);const returnToList=false;openModal('Edit task',`<div class="field"><label>TASK NAME</label><input name="title" maxlength="180" value="${esc(t.title)}" required></div><div class="field-grid"><div class="field"><label>START DATE</label><input type="date" name="start_date" value="${esc(t.start_date)}"></div><div class="field"><label>DUE DATE</label><input type="date" name="due_date" value="${esc(t.due_date)}"></div></div><div class="field"><label>PRIORITY</label><select name="priority"><option value="normal" ${t.priority==='normal'?'selected':''}>Normal</option><option value="high" ${t.priority==='high'?'selected':''}>High</option><option value="low" ${t.priority==='low'?'selected':''}>Low</option></select></div><div class="field"><label>TASK NOTES</label><textarea name="description">${esc(t.description)}</textarea></div>`,async fd=>{const payload=Object.fromEntries(fd);const start=String(payload.start_date||'');const due=String(payload.due_date||'');if(start&&due&&start>due)throw new Error('Start date cannot be later than the due date.');await api(`/api/tasks/${taskId}`,{method:'PATCH',body:JSON.stringify(payload)});if(returnToList){currentTask=null;nav('semesters');}else{await renderTask(taskId);}toast('Task updated and saved.');});}
function modalProgress(){openModal('Update task progress',`<div class="field"><label>COMPLETION (%)</label><input type="number" name="progress" min="0" max="100" value="${currentTask?0:0}" required></div><div class="field"><label>PROGRESS NOTE</label><textarea name="note" placeholder="e.g. Only the references section is left."></textarea></div>`,async fd=>{const p=Number(fd.get('progress'));await api(`/api/tasks/${currentTask}/progress`,{method:'POST',body:JSON.stringify({progress:p,note:fd.get('note')})});await renderTask(currentTask);toast('Progress saved.');});api(`/api/tasks/${currentTask}`).then(t=>{const inp=$('[name="progress"]',modal);if(inp)inp.value=t.progress;}).catch(()=>{});}
function modalStartSession(){openModal('Start study session',`<div class="field"><label>PLANNED DURATION (MINUTES)</label><input name="planned_minutes" type="number" min="1" max="720" value="180" required></div><p class="muted small">The session is recorded against this task. Monday will give a brief spoken focus check-in approximately every 20 minutes while this session is active.</p>`,async fd=>{const s=await api(`/api/tasks/${currentTask}/sessions`,{method:'POST',body:JSON.stringify({planned_minutes:Number(fd.get('planned_minutes'))})});lastSessionId=s.id;await renderTask(currentTask);toast('Study session started.');});}
function modalAlarm(existing=null){const a=existing||{title:'Morning alarm',alarm_time:'06:00',alarm_date:'',repeat_days:['Mon','Tue','Wed','Thu','Fri'],topic:'',enabled:true};const days=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];openModal(existing?'Edit alarm':'Create alarm',`<div class="field"><label>ALARM LABEL</label><input name="title" value="${esc(a.title)}" required></div><div class="field-grid"><div class="field"><label>TIME</label><input name="alarm_time" type="time" value="${esc(a.alarm_time)}" required></div><div class="field"><label>DATE (OPTIONAL)</label><input name="alarm_date" type="date" value="${esc(a.alarm_date)}"></div></div><div class="field"><label>REPEAT DAYS</label><div class="check-row">${days.map(d=>`<label><input type="checkbox" name="repeat_days" value="${d}" ${a.repeat_days.includes(d)?'checked':''}>${d}</label>`).join('')}</div><div class="field-help">Leave all days unchecked for a one-time alarm. If its date is blank, the next upcoming occurrence is used.</div></div><div class="field"><label>SPOKEN ALARM TOPIC</label><input name="topic" value="${esc(a.topic)}" placeholder="e.g. Linked lists"></div><div class="field"><label><input type="checkbox" name="enabled" ${a.enabled?'checked':''}> Enable this alarm</label></div><p class="muted small">The Raspberry Pi will speak this alarm at the scheduled time. Press the desk button to dismiss it.</p>`,async fd=>{const payload={title:fd.get('title'),alarm_time:fd.get('alarm_time'),alarm_date:fd.get('alarm_date'),repeat_days:fd.getAll('repeat_days'),topic:fd.get('topic'),enabled:$('[name="enabled"]',modal).checked};await api(existing?`/api/alarms/${existing.id}`:'/api/alarms',{method:existing?'PATCH':'POST',body:JSON.stringify(payload)});await renderAlarms();toast(existing?'Alarm updated.':'Alarm saved.');});}

async function endSession(id,early){if(!confirm(early?'End this session early?':'Finish this study session?'))return;await api(`/api/sessions/${id}/end`,{method:'POST',body:JSON.stringify({ended_early:early})});clearInterval(sessionTick);await render();toast(early?'Session ended early.':'Session completed.');}

view.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 try{
  if(b.dataset.action==='new-semester')modalSemester();
  else if(b.dataset.action==='new-subject')modalSubject();
  else if(b.dataset.action==='new-task')modalTask();
  else if(b.dataset.action==='edit-task')modalEditTask();
  else if(b.dataset.action==='update-progress')modalProgress();
  else if(b.dataset.action==='start-session')modalStartSession();
  else if(b.dataset.action==='delete-task'){if(confirm('Delete this task and its progress/session history?')){const deletedId=currentTask;await api(`/api/tasks/${deletedId}`,{method:'DELETE'});currentTask=null;if(currentSubject)await renderSubject(currentSubject);else nav('semesters');toast('Task deleted.');}}
  else if(b.dataset.action==='back-semesters'){currentSemester=null;nav('semesters');}
  else if(b.dataset.action==='back-semester'){currentSubject=null;await renderSemester(currentSemester);}
  else if(b.dataset.action==='back-subject'){currentTask=null;await renderSubject(currentSubject);}
  else if(b.dataset.action==='back-reports'){nav('reports');}
  else if(b.dataset.action==='go-semesters'){nav('semesters');}
  else if(b.dataset.semester){currentSemester=Number(b.dataset.semester);currentSubject=null;currentTask=null;await renderSemester(currentSemester);}
  else if(b.dataset.subject){currentSubject=Number(b.dataset.subject);currentTask=null;await renderSubject(currentSubject);}
  else if(b.dataset.task){currentTask=Number(b.dataset.task);await renderTask(currentTask);}
  else if(b.dataset.editSubject){modalEditSubject(Number(b.dataset.editSubject));}
  else if(b.dataset.semesterStatus){const id=Number(b.dataset.semesterStatus);const s=await api(`/api/semesters/${id}`);const status=s.status==='active'?'completed':'active';await api(`/api/semesters/${id}`,{method:'PATCH',body:JSON.stringify({status})});await render();toast(`Semester marked ${status}.`);}
  else if(b.dataset.report){await showReport(Number(b.dataset.report));}
  else if(b.dataset.sessionEnd){await endSession(Number(b.dataset.sessionEnd),false);}
  else if(b.dataset.sessionEarly){await endSession(Number(b.dataset.sessionEarly),true);}
  else if(b.dataset.editAlarm){const a=(await api('/api/alarms')).find(x=>x.id===Number(b.dataset.editAlarm));if(a)modalAlarm(a);}
  else if(b.dataset.deleteAlarm){if(confirm('Delete this alarm?')){await api(`/api/alarms/${b.dataset.deleteAlarm}`,{method:'DELETE'});await renderAlarms();toast('Alarm deleted.');}}
 }catch(err){toast(err.message);}
});

view.addEventListener('change',async e=>{
 const el=e.target;
 try{
  if(el.dataset.alarmToggle){await api(`/api/alarms/${el.dataset.alarmToggle}`,{method:'PATCH',body:JSON.stringify({enabled:el.checked})});toast(el.checked?'Alarm enabled.':'Alarm disabled.');}
 }catch(err){el.checked=!el.checked;toast(err.message);}
});

function updateClock(){const el=$('#footer-time');if(el)el.textContent=new Date().toLocaleString(undefined,{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'});}
setInterval(updateClock,15000);updateClock();render();
