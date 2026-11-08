const csrf=document.cookie.split('; ').find(c=>c.startsWith('id_csrf='))?.split('=')[1]||'';
const api=(p,b)=>fetch('/admin/api'+p,{method:b?'POST':'GET',headers:{'content-type':'application/json','x-csrf-token':csrf},body:b?JSON.stringify(b):undefined}).then(r=>r.json());
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D;const picked=new Set();
async function load(){D=await api('/overview');
 document.getElementById('stats').textContent=D.tenants.length+' companies · '+D.users+' people · '+D.products.length+' products';
 document.getElementById('tenants').innerHTML=D.tenants.map(t=>`<div class="row"><div><b>${esc(t.name)}</b> <span class="mini">${t.members} people · ${t.status}</span></div><div>${D.products.map(p=>{const s=t.subscriptions.find(x=>x.product===p.key);const on=s&&(s.status==='active'||s.status==='trial');return `<button class="chip ${on?'on':''}" data-t="${t.id}" data-p="${p.key}" data-on="${on?1:0}" aria-pressed="${!!on}">${esc(p.name)}</button>`}).join('')}</div></div>`).join('');
 document.getElementById('prodpick').innerHTML=D.products.map(p=>`<button type="button" class="chip pick ${picked.has(p.key)?'on':''}" data-k="${p.key}" aria-pressed="${picked.has(p.key)}">${esc(p.name)}</button>`).join('');
 const o=await api('/ops');const e=o.events;
 document.getElementById('health').innerHTML=`<div>Database: <b>${o.database}</b> · ${o.counts.users} people · ${o.counts.companies} companies · ${o.counts.live_sessions} live sign-ins</div><div>Events: <b>${e.pending}</b> waiting, <b>${e.failed}</b> failed${e.pending?` (oldest ${e.oldest_pending_seconds}s)`:''}${e.last_delivered?' · last delivered '+new Date(e.last_delivered).toLocaleString():''}</div><div>Signing key <span class="mono">${esc(o.signing_key.kid||'none')}</span>, ${o.signing_key.age_days} days old, ${o.signing_key.published} published · Webhooks: ${o.products.map(p=>esc(p.key)+(p.webhook?' on':' off')).join(', ')}</div>`;
 document.getElementById('audit').innerHTML=D.audit.map(a=>`<div>${new Date(a.at).toLocaleString()} · ${esc(a.action)}</div>`).join('')||'Nothing yet.';}
document.addEventListener('click',async e=>{const b=e.target.closest('.chip');if(!b)return;
 if(b.classList.contains('pick')){picked.has(b.dataset.k)?picked.delete(b.dataset.k):picked.add(b.dataset.k);return load()}
 await api('/tenants/'+b.dataset.t+'/subscriptions',{product:b.dataset.p,status:b.dataset.on==='1'?'cancelled':'active'});load()});
document.getElementById('newform').addEventListener('submit',async e=>{e.preventDefault();
 const r=await api('/tenants',{name:tn.value,owner_email:oe.value,owner_name:on.value,products:[...picked]});
 if(r.id){e.target.reset();picked.clear();load()}else alert(r.detail||'Could not create');});
load();

let armed=false
document.getElementById('rotate').addEventListener('click',async e=>{const b=e.currentTarget,msg=document.getElementById('rotmsg')
 if(!armed){armed=true;b.textContent='Click again to confirm';msg.textContent='Old keys stay valid until their tokens expire.';setTimeout(()=>{armed=false;b.textContent='Rotate signing key';msg.textContent=''},6000);return}
 armed=false;const r=await api('/keys/rotate',{});b.textContent='Rotate signing key';msg.textContent='New key '+r.kid;load()})
