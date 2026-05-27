// Phase 5 — 資料品質信號頁。

const $ = id => document.getElementById(id)

load()

async function load() {
  try {
    const res = await fetch('/api/admin/quality')
    if (res.status === 403) {
      document.querySelectorAll('[id$="-block"]').forEach(el => { el.textContent = '需要 admin 權限。' })
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    render(await res.json())
  } catch (err) {
    $('industry-block').textContent = `載入失敗:${err.message}`
  }
}

function render(d) {
  renderIndustry(d)
  renderProduct(d)
  renderAudience(d)
}

function renderIndustry(d) {
  const recs = new Set(d.recalibration_recommended_dims || [])
  const rows = Object.entries(d.industry_divergence_by_dim || {})
    .map(([dim, info]) => {
      const flagged = recs.has(dim)
      const cls = flagged ? 'text-rose-600 dark:text-rose-400 font-medium' : 'text-slate-600 dark:text-slate-300'
      const note = flagged ? `　建議重新校準（≥${d.recal_min_files} 筆）` : ''
      return `<div class="flex justify-between py-1 border-b border-slate-100 dark:border-slate-700/50 ${cls}">
        <span>${escapeHtml(dim)}</span><span class="font-mono">${info.count}${note}</span></div>`
    }).join('')
  $('industry-block').innerHTML = rows || '無資料'
}

function renderProduct(d) {
  const files = d.product_divergence_files || []
  if (!files.length) {
    $('product-block').textContent = '尚無 industry-audience gap > 0.40 的檔。'
    return
  }
  const q = d.audience_quality || {}
  const warn = q.suspect
    ? '<div class="text-xs text-rose-600 mb-2">⚠️ audience 資料品質待驗證（straight-lining 疑慮），下列商品證據暫不可盡信。</div>'
    : ''
  $('product-block').innerHTML = warn + files.map(f =>
    `<div class="py-1 border-b border-slate-100 dark:border-slate-700/50">
       <span class="font-medium">${escapeHtml(f.filename)}</span>
       <span class="text-xs text-slate-500">— ${f.dims.map(escapeHtml).join(', ')}</span>
     </div>`).join('')
}

function renderAudience(d) {
  const q = d.audience_quality || {}
  if (q.insufficient) {
    $('audience-block').textContent = `資料不足（n=${q.n_complete}），暫不判定。`
    return
  }
  const badge = q.suspect
    ? '<span class="text-rose-600 font-medium">⚠️ 疑似 straight-lining</span>'
    : '<span class="text-emerald-600">看起來正常</span>'
  const dims = (q.low_variance_dims || []).length
    ? `　低變異維度: ${q.low_variance_dims.map(escapeHtml).join(', ')}`
    : ''
  $('audience-block').innerHTML = `${badge}（n=${q.n_complete}）${dims}`
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
