// Plans: packages of a product (features and limits) and giving a company one. Loaded after admin.js (uses its api, esc, D). Buttons here are not .chip,
// because admin.js treats every .chip as a subscription toggle.
const $p = id => document.getElementById(id)
let PLANS = []
async function loadPlans() {
  if (!D) { setTimeout(loadPlans, 300); return }
  PLANS = (await api('/plans')).plans
  $p('plans').innerHTML = PLANS.map(p => `<div class="row"><div><b>${esc(p.name)}</b> <span class="mono">${esc(p.key)}</span> <span class="mini">${esc(p.product)} · ${p.active ? 'active' : 'inactive'}<br>features: ${esc(p.features.join(', ') || 'none')}<br>limits: ${esc(Object.entries(p.limits).map(([k, v]) => k + '=' + (v === null ? 'unlimited' : v)).join(', ') || 'none')}</span></div></div>`).join('') || '<span class="mini">No plans yet.</span>'
  const prods = D.products.filter(p => p.kind !== 'service')
  $p('pl-prod').innerHTML = prods.map(p => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('')
  $p('as-t').innerHTML = D.tenants.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')
  $p('as-p').innerHTML = prods.map(p => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('')
  fillPlanChoices()
}
function fillPlanChoices() {
  const prod = $p('as-p').value
  $p('as-plan').innerHTML = '<option value="">(no plan)</option>' + PLANS.filter(p => p.product === prod && p.active).map(p => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('')
  const t = D.tenants.find(x => x.id === $p('as-t').value), s = t && t.subscriptions.find(x => x.product === prod)
  if (s && s.plan) $p('as-plan').value = s.plan
}
loadPlans()
$p('as-p').addEventListener('change', fillPlanChoices)
$p('as-t').addEventListener('change', fillPlanChoices)
$p('planform').addEventListener('submit', async e => {
  e.preventDefault()
  const limits = {}
  for (const part of $p('pl-lim').value.split(/\s+/).filter(Boolean)) { const [k, v] = part.split('='); limits[k] = v === undefined || v === '' ? null : Number(v) }
  const r = await api('/plans', { key: $p('pl-key').value, name: $p('pl-name').value, product_key: $p('pl-prod').value, features: $p('pl-feat').value.split(/\s+/).filter(Boolean), limits })
  $p('plan-msg').textContent = r.key ? `Plan ${r.name} saved. Companies on it are told within seconds.` : (typeof r.detail === 'string' ? r.detail : 'Could not save the plan')
  if (r.key) { e.target.reset(); await loadPlans() }
})
$p('assignform').addEventListener('submit', async e => {
  e.preventDefault()
  const r = await api('/tenants/' + $p('as-t').value + '/plan', { product: $p('as-p').value, plan: $p('as-plan').value || null })
  $p('plan-msg').textContent = r.id ? 'Plan assigned. The product hears about it within seconds.' : (typeof r.detail === 'string' ? r.detail : 'Could not assign the plan')
  if (r.id) { await load(); loadPlans() }
})
