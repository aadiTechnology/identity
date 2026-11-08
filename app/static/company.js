const csrf = document.cookie.split('; ').find(c => c.startsWith('id_csrf='))?.split('=')[1] || ''
const tid = document.getElementById('tid').value, me = document.getElementById('me').value
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const err = document.getElementById('err')
async function call(path, body) {
  const r = await fetch(`/company/api/${tid}${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) { err.hidden = false; err.textContent = d.detail || 'Something went wrong'; throw new Error(d.detail) }
  err.hidden = true
  return d
}
async function load() {
  const d = await call('/members')
  document.getElementById('subs').textContent = 'Products: ' + (d.subscriptions.map(s => s.product + ' (' + s.status + ')').join(', ') || 'none')
  document.getElementById('members').innerHTML = d.members.map(m => `<div class="row" data-u="${m.user_id}">
    <div class="who"><b>${esc(m.name)}</b>${m.is_admin ? '<span class="tag">Administrator</span>' : ''}${m.status === 'suspended' ? '<span class="tag">Suspended</span>' : ''}${m.user_status === 'invited' ? '<span class="tag">Invited</span>' : ''}<br><span class="mini">${esc(m.email)}</span></div>
    <div class="acts">${m.user_id === me ? '<span class="mini">You</span>' : `
      <button data-act="${m.status === 'suspended' ? 'reactivate' : 'suspend'}">${m.status === 'suspended' ? 'Reactivate' : 'Suspend'}</button>
      <button data-act="admin">${m.is_admin ? 'Remove admin' : 'Make admin'}</button>
      <button data-act="remove">Remove</button>`}</div></div>`).join('') || '<p class="muted">No people yet.</p>'
}
document.addEventListener('click', async e => {
  const b = e.target.closest('button[data-act]'); if (!b) return
  const uid = b.closest('.row').dataset.u, act = b.dataset.act
  try {
    if (act === 'suspend') await call(`/members/${uid}/status`, { status: 'suspended' })
    if (act === 'reactivate') await call(`/members/${uid}/status`, { status: 'active' })
    if (act === 'admin') { const isAdmin = b.textContent.trim() === 'Make admin'; await call(`/members/${uid}/admin`, { is_admin: isAdmin }) }
    if (act === 'remove') await call(`/members/${uid}/remove`, {})
    load()
  } catch (_) { /* message is shown */ }
})
document.getElementById('inviteform').addEventListener('submit', async e => {
  e.preventDefault()
  try { await call('/members', { email: document.getElementById('ie').value, name: document.getElementById('in').value }); e.target.reset(); load() } catch (_) {}
})
const tsel = document.getElementById('tsel')
if (tsel) tsel.addEventListener('change', () => { location.href = '/company?tenant=' + tsel.value })
load()
