'use strict';
let state, busy=false;
const $=id=>document.getElementById(id);
async function act(action='',text='') {
  if(busy) return;
  busy=true; $('save-status').textContent='○ Сохраняем…'; $('error').hidden=true;
  document.querySelectorAll('#options button,.send-button').forEach(b=>b.disabled=true);
  try {state=await api('/api/action',{action,text,revision:state.revision}); render();}
  catch(e) {if(e.status===409){state=await api('/api/state');render();} $('error').textContent=e.message; $('error').hidden=false;}
  finally {busy=false;$('save-status').textContent='● Сохранено';document.querySelectorAll('#options button,.send-button').forEach(b=>b.disabled=false);}
}
function render() {
  $('bot-name').textContent=state.brand;
  $('service').textContent=state.service;
  $('step-count').textContent=state.node?'ВОПРОС '+state.step+' / '+(state.multi?'МОЖНО НЕСКОЛЬКО':'ВАШ ПРОЕКТ'):'ВАША ИДЕЯ → ПОНЯТНЫЙ ПЛАН';
  const q=$('question');q.replaceChildren(el('h2',{class:'question-title'},state.title));
  if(state.text) q.append(el('p',{class:'question-text'},state.text));
  if(state.hint) q.append(el('p',{class:'question-hint'},state.hint));
  if(state.lead_id) q.append(el('div',{class:'lead-receipt'},[el('span',{},'Бриф сохранён'),el('strong',{},state.lead_id),el('a',{href:'/api/brief.txt',class:'text-link'},'Скачать мой бриф ↓')]));
  for(const row of state.summary||[]) {
    const card=el('div',{class:'summary-row'},[el('span',{},row.title),el('strong',{},row.value)]);
    if(row.editable)card.append(el('button',{class:'edit-button',onclick:()=>act('edit:'+row.key),'aria-label':'Изменить: '+row.title},'Изменить ↗'));
    q.append(card);
  }
  $('reply-form').hidden=!state.input||state.input==='file';$('upload-form').hidden=state.input!=='file';
  $('reply').value='';$('attachment').value='';
  $('reply').setAttribute('inputmode',state.input==='phone'?'tel':'text');
  $('reply').placeholder=state.input==='phone'?'+7 999 123-45-67':state.input==='url'?'https://example.ru — что вам нравится':'Расскажите своими словами…';
  const opts=$('options');opts.replaceChildren();
  opts.className='options '+(state.options.length>5?'many-options':'');
  for(const o of state.options){const toggles=state.multi&&(o.action.startsWith('opt:')||o.action.startsWith('section:'));opts.append(el('button',{class:'option '+(o.primary?'primary ':'')+(o.quiet?'quiet ':'')+(o.selected?'selected ':''),onclick:()=>act(o.action),'aria-pressed':toggles?String(!!o.selected):undefined},[el('span',{class:'option-label'},o.label),el('span',{class:'option-symbol','aria-hidden':true},o.selected?'✓':o.quiet?'←':o.primary?'↗':toggles?'+':'↗')]));}
  const links=$('external-links');links.replaceChildren();for(const item of state.links||[]) {
    const a=link(item.title+' ↗',item.url,'portfolio-link');
    if(item.image)a.prepend(el('img',{src:item.image,alt:item.title,loading:'lazy',referrerpolicy:'no-referrer'}));
    links.append(a);
  }
  const nav=$('navigation');nav.replaceChildren();
  [['menu','Обзор','01'],['portfolio','Портфолио','02'],['contact','Контакты','03']].forEach(([action,label,i])=>nav.append(el('button',{class:'nav-item '+(state.screen===action?'active':''),disabled:!state.accepted,onclick:()=>act(action)},[el('span',{},i),label,el('span',{class:'nav-arrow'},'↗')])));
  const preview=$('brief-preview');preview.replaceChildren();
  const rows=state.draft_summary||[];
  if(!rows.length) preview.append(el('div',{class:'empty-brief'},[el('div',{class:'empty-lines'},[el('i'),el('i'),el('i')]),el('p',{},'Пока чистый лист.\nНачнём с вашей задачи?')]));
  else {preview.append(el('div',{class:'brief-count'},[el('strong',{},String(rows.length).padStart(2,'0')),el('span',{},'ответов сохранено')]));rows.forEach(row=>preview.append(el('div',{class:'preview-row'},[el('small',{},row.title),el('p',{},row.value)])));}
  $('error').hidden=true;
}
$('reply-form').addEventListener('submit',e=>{e.preventDefault();act('', $('reply').value);});
$('attachment').addEventListener('change',async()=>{
  const file=$('attachment').files[0];if(!file||busy)return;
  if(file.size>10*1024*1024){$('error').textContent='Максимальный размер файла — 10 МБ';$('error').hidden=false;return;}
  busy=true;$('save-status').textContent='○ Загружаем…';
  try {const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});state=await api('/api/upload',{name:file.name,data,revision:state.revision});render();}
  catch(e){$('error').textContent=e.message;$('error').hidden=false;}
  finally{busy=false;$('save-status').textContent='● Сохранено';}
});
api('/api/state').then(s=>{state=s;render();if(location.pathname==='/privacy')act('privacy');}).catch(e=>{$('question').textContent='Не удалось загрузить бриф: '+e.message;});
