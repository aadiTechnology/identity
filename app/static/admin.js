const csrf=document.cookie.split('; ').find(c=>c.startsWith('id_csrf='))?.split('=')[1]||'';
const api=(p,b)=>fetch('/admin/api'+p,{method:b?'POST':'GET',headers:{'content-type':'application/json','x-csrf-token':csrf},body:b?JSON.stringify(b):undefined}).then(r=>r.json());
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D;const picked=new Set();
async function load(){D=await api('/overview');
 document.getElementById('stats').textContent=D.tenants.length+' companies · '+D.users+' people · '+D.products.length+' products';
 document.getElementById('tenants').innerHTML=D.tenants.map(t=>`<div class="row"><div><b>${esc(t.name)}</b> <span class="mini">${t.members} people · ${t.status}</span></div><div>${D.products.map(p=>{const s=t.subscriptions.find(x=>x.product===p.key);const on=s&&(s.status==='active'||s.status==='trial');return `<button class="chip ${on?'on':''}" data-t="${t.id}" data-p="${p.key}" data-on="${on?1:0}" aria-pressed="${!!on}">${esc(p.name)}</button>`}).join('')}</div></div>`).join('');
 document.getElementById('prodpick').innerHTML=D.products.map(p=>`<button type="button" class="chip pick ${picked.has(p.key)?'on':''}" data-k="${p.key}" aria-pressed="${picked.has(p.key)}">${esc(p.name)}</button>`).join('');
 document.getElementById('audit').innerHTML=D.audit.map(a=>`<div>${new Date(a.at).toLocaleString()} · ${esc(a.action)}</div>`).join('')||'Nothing yet.';}
document.addEventListener('click',async e=>{const b=e.target.closest('.chip');if(!b)return;
 if(b.classList.contains('pick')){picked.has(b.dataset.k)?picked.delete(b.dataset.k):picked.add(b.dataset.k);return load()}
 await api('/tenants/'+b.dataset.t+'/subscriptions',{product:b.dataset.p,status:b.dataset.on==='1'?'cancelled':'active'});load()});
document.getElementById('newform').addEventListener('submit',async e=>{e.preventDefault();
 const r=await api('/tenants',{name:tn.value,owner_email:oe.value,owner_name:on.value,products:[...picked]});
 if(r.id){e.target.reset();picked.clear();load()}else alert(r.detail||'Could not create');});
load();
