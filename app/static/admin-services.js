// Service clients: how a product's server gets tokens for a shared service. Loaded after admin.js (uses its csrf, api, esc, D).
let svcArm = null
const $ = id => document.getElementById(id)
async function loadClients() {
  if (!D) { setTimeout(loadClients, 300); return }
  const r = await api('/service-clients')
  $('svc-clients').innerHTML = r.clients.map(c => `<div class="row"><div><b>${esc(c.name)}</b> <span class="mono">${esc(c.client_id)}</span> <span class="mini">${esc(c.product)} → ${esc(c.audiences.join(', '))} · ${esc(c.scopes.join(' '))} · secret from ${new Date(c.secret_rotated_at).toLocaleDateString()} · ${c.active ? 'active' : 'disabled'}</span></div><div><button class="chip" data-svc="rotate" data-id="${esc(c.client_id)}">Rotate secret</button><button class="chip" data-svc="toggle" data-id="${esc(c.client_id)}" data-on="${c.active ? 1 : 0}">${c.active ? 'Disable' : 'Enable'}</button></div></div>`).join('') || '<span class="mini">No service clients yet.</span>'
  $('sc-prod').innerHTML = D.products.filter(p => p.kind !== 'service').map(p => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('')
  $('sc-aud').innerHTML = r.services.map(p => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('')
}
loadClients()
$('svcform').addEventListener('submit', async e => {
  e.preventDefault()
  const r = await api('/service-clients', { client_id: $('sc-id').value, name: $('sc-name').value, product_key: $('sc-prod').value, audiences: [$('sc-aud').value], scopes: $('sc-scope').value.split(/\s+/).filter(Boolean) })
  const out = $('svc-secret')
  if (r.secret) {
    out.innerHTML = `<b>Client ${esc(r.client_id)} created.</b> Secret (shown once, copy it now): <span class="mono">${esc(r.secret)}</span>`
    e.target.reset(); $('sc-scope').value = 'video:create video:read video:cancel'; loadClients()
  } else out.textContent = typeof r.detail === 'string' ? r.detail : 'Could not create'
})
$('svc-clients').addEventListener('click', async e => {
  const b = e.target.closest('[data-svc]'); if (!b) return
  const key = b.dataset.svc + b.dataset.id, out = $('svc-secret')
  if (svcArm !== key) { svcArm = key; const was = b.textContent; b.textContent = 'Click again'; setTimeout(() => { if (svcArm === key) { svcArm = null; b.textContent = was } }, 5000); return }
  svcArm = null
  if (b.dataset.svc === 'rotate') {
    const r = await api('/service-clients/' + b.dataset.id + '/rotate', {})
    out.innerHTML = `<b>New secret for ${esc(r.client_id)}</b> (shown once; the old one stops working now): <span class="mono">${esc(r.secret)}</span>`
  } else await api('/service-clients/' + b.dataset.id + '/status', { active: b.dataset.on !== '1' })
  loadClients()
})
