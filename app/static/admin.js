const csrf=document.cookie.split('; ').find(c=>c.startsWith('id_csrf='))?.split('=')[1]||'';
const api=(p,b)=>fetch('/admin/api'+p,{method:b?'POST':'GET',headers:{'content-type':'application/json','x-csrf-token':csrf},body:b?JSON.stringify(b):undefined}).then(r=>r.json());
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D;const picked=new Set();let armedToggle=null;
async function load(){D=await api('/overview');
 document.getElementById('stats').textContent=D.tenants.length+' companies · '+D.users+' people · '+D.products.length+' products';
 document.getElementById('tenants').innerHTML=D.tenants.map(t=>`<div class="row"><div><b>${esc(t.name)}</b> <span class="mini">${t.members} people · ${t.status}</span></div><div>${D.products.map(p=>{const s=t.subscriptions.find(x=>x.product===p.key);const on=s&&(s.status==='active'||s.status==='trial');return `<button class="chip ${on?'on':''}" data-t="${t.id}" data-p="${p.key}" data-on="${on?1:0}" aria-pressed="${!!on}">${esc(p.name)}</button>`}).join('')}</div></div>`).join('');
 document.getElementById('prodpick').innerHTML=D.products.map(p=>`<button type="button" class="chip pick ${picked.has(p.key)?'on':''}" data-k="${p.key}" aria-pressed="${picked.has(p.key)}">${esc(p.name)}</button>`).join('');
 const o=await api('/ops');const e=o.events;
 document.getElementById('health').innerHTML=`<div>Database: <b>${o.database}</b> · ${o.counts.users} people · ${o.counts.companies} companies · ${o.counts.live_sessions} live sign-ins</div><div>Events: <b>${e.pending}</b> waiting, <b>${e.failed}</b> failed${e.pending?` (oldest ${e.oldest_pending_seconds}s)`:''}${e.last_delivered?' · last delivered '+new Date(e.last_delivered).toLocaleString():''}</div><div>Signing key <span class="mono">${esc(o.signing_key.kid||'none')}</span>, ${o.signing_key.age_days} days old, ${o.signing_key.published} published · Webhooks: ${o.products.map(p=>esc(p.key)+(p.webhook?' on':' off')).join(', ')}</div>`;
 document.getElementById('audit').innerHTML=D.audit.map(a=>`<div>${new Date(a.at).toLocaleString()} · ${esc(a.action)}</div>`).join('')||'Nothing yet.';}
document.addEventListener('click',async e=>{const b=e.target.closest('.chip');if(!b)return;
 if(b.classList.contains('pick')){picked.has(b.dataset.k)?picked.delete(b.dataset.k):picked.add(b.dataset.k);return load()}
 // a subscription change reaches products within seconds (turning one off closes the company there), so it needs a second click
 const key=b.dataset.t+':'+b.dataset.p
 if(armedToggle!==key){armedToggle=key;const was=b.textContent;b.textContent=(b.dataset.on==='1'?'Turn off? ':'Turn on? ')+'Click again';setTimeout(()=>{if(armedToggle===key){armedToggle=null;b.textContent=was}},5000);return}
 armedToggle=null
 await api('/tenants/'+b.dataset.t+'/subscriptions',{product:b.dataset.p,status:b.dataset.on==='1'?'cancelled':'active'});load()});
document.getElementById('newform').addEventListener('submit',async e=>{e.preventDefault();
 const r=await api('/tenants',{name:tn.value,owner_email:oe.value,owner_name:on.value,products:[...picked]});
 if(r.id){e.target.reset();picked.clear();load()}else alert(r.detail||'Could not create');});
load();

let armed=false
document.getElementById('rotate').addEventListener('click',async e=>{const b=e.currentTarget,msg=document.getElementById('rotmsg')
 if(!armed){armed=true;b.textContent='Click again to confirm';msg.textContent='Old keys stay valid until their tokens expire.';setTimeout(()=>{armed=false;b.textContent='Rotate signing key';msg.textContent=''},6000);return}
 armed=false;const r=await api('/keys/rotate',{});b.textContent='Rotate signing key';msg.textContent='New key '+r.kid;load()})

const impOut = () => document.getElementById('imp-out')
const chosen = () => [...document.querySelectorAll('#imp-companies input:checked')].map(x => x.value)
function showCompanies(list) { document.getElementById('imp-companies').innerHTML = list.map(c => `<label style="display:flex;gap:8px;align-items:center;margin:3px 0"><input type="checkbox" value="${esc(c.slug)}"> ${esc(c.name)} <span class="mini">${c.people} people</span></label>`).join('') || '<span class="mini">No companies found.</span>' }
async function runImport(dry) {
  const out = impOut(); out.textContent = 'Working…'
  const sel = chosen()
  if (!dry && !sel.length) { out.textContent = 'Tick at least one company first.'; return }
  if (dry && !sel.length && document.querySelector('#imp-companies input')) { out.textContent = 'Tick at least one company to preview.'; return }
  const r = await api('/import/tracker', { dry_run: dry, companies: sel })
  if (r.detail) { out.textContent = typeof r.detail === 'string' ? r.detail : 'Could not import.'; return }
  if (!document.querySelector('#imp-companies input')) showCompanies(r.companies)
  if (!sel.length) { out.textContent = 'Companies listed. Tick the ones to bring across.'; return }
  out.innerHTML = (dry ? '<b>Preview, nothing written.</b> ' : '<b>Imported.</b> ') + `${r.users_created} new people, ${r.users_updated} already here · ${r.tenants_created} new companies, ${r.tenants_updated} already here · ${r.members_created} memberships added` + (r.skipped.length ? ` · ${r.skipped.length} skipped` : '')
  if (!dry) load()
}
document.getElementById('imp-list').addEventListener('click', () => { document.getElementById('imp-companies').innerHTML = ''; runImport(true) })
document.getElementById('imp-preview').addEventListener('click', () => runImport(true))
let impArmed = false
document.getElementById('imp-run').addEventListener('click', e => {
  const b = e.currentTarget
  if (!impArmed) { impArmed = true; b.textContent = 'Click again to confirm'; setTimeout(() => { impArmed = false; b.textContent = 'Import selected' }, 6000); return }
  impArmed = false; b.textContent = 'Import selected'; runImport(false)
})
