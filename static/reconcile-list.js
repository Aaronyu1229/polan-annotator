// Phase 11 — 仲裁清單頁。
// fetch /api/admin/reconcile/list 後渲染表格,按 Amber–yyslin gap desc。

const $ = id => document.getElementById(id)

load()

async function load() {
  try {
    const res = await fetch('/api/admin/reconcile/list')
    if (res.status === 403) {
      $('meta').textContent = '需要 admin 權限。'
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const items = await res.json()
    render(items)
  } catch (err) {
    $('meta').textContent = `載入失敗:${err.message}`
  }
}

function render(items) {
  $('meta').textContent = `共 ${items.length} 筆待仲裁`
  const tbody = $('tbody')
  if (!items.length) {
    tbody.innerHTML = `
      <tr><td colspan="5" class="p-4 text-sm text-slate-500 text-center">
        🎉 沒有待仲裁的音檔。對齊的檔(Amber–yyslin gap ≤ 0.20)都已自動晉升 Creator Ready。
      </td></tr>`
    return
  }
  tbody.innerHTML = items.map(it => {
    const annotators = it.annotators.map(a => {
      const isAmber = a === 'amber'
      return `<span class="text-xs px-1.5 py-0.5 rounded ${isAmber ? 'bg-amber-200 dark:bg-amber-900 font-medium' : 'bg-slate-200 dark:bg-slate-700'}">${escapeHtml(a)}</span>`
    }).join(' ')
    const gap = it.max_gap_value != null ? it.max_gap_value.toFixed(2) : '—'
    const gapCls = it.max_gap_value > 0.5
      ? 'text-rose-600 dark:text-rose-400 font-medium'
      : it.max_gap_value > 0.3
      ? 'text-amber-600 dark:text-amber-400'
      : 'text-slate-700 dark:text-slate-300'
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700">
        <td class="p-3">
          <div class="font-medium">${escapeHtml(it.game_name)}</div>
          <div class="text-xs text-slate-500">${escapeHtml(it.game_stage)}</div>
        </td>
        <td class="p-3">${annotators}</td>
        <td class="p-3 text-right font-mono ${gapCls}">${gap}</td>
        <td class="p-3 text-xs text-slate-500">${escapeHtml(it.max_gap_dim || '—')}</td>
        <td class="p-3 text-right">
          <a href="/admin/reconcile/${encodeURIComponent(it.audio_id)}"
             class="inline-block px-3 py-1.5 text-sm rounded bg-emerald-600 text-white hover:bg-emerald-700">
            ⚖️ 仲裁 →
          </a>
        </td>
      </tr>
    `
  }).join('')
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
