// Phase 12-A — Lockable 清單 + 一鍵 lock gold。

const $ = id => document.getElementById(id)

load()

async function load() {
  try {
    const res = await fetch('/api/admin/lockable/list')
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
  $('meta').textContent = `共 ${items.length} 筆可鎖 gold`
  const tbody = $('tbody')
  if (!items.length) {
    tbody.innerHTML = `
      <tr><td colspan="5" class="p-4 text-sm text-slate-500 text-center">
        目前無待快速確認的音檔（creator 與 industry 已對齊者）。
        creator-industry gap 太寬的去
        <a class="text-amber-600 hover:underline" href="/admin/reconcile">仲裁頁</a> 處理。
      </td></tr>`
    return
  }
  tbody.innerHTML = items.map(it => {
    const annotators = it.annotators.map(a => {
      const isAmber = a === 'amber'
      return `<span class="text-xs px-1.5 py-0.5 rounded ${isAmber ? 'bg-amber-200 dark:bg-amber-900 font-medium' : 'bg-slate-200 dark:bg-slate-700'}">${escapeHtml(a)}</span>`
    }).join(' ')
    const gap = it.max_gap_value != null ? it.max_gap_value.toFixed(2) : '—'
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700" data-audio="${escapeAttr(it.audio_id)}">
        <td class="p-3">
          <div class="font-medium">${escapeHtml(it.game_name)}</div>
          <div class="text-xs text-slate-500">${escapeHtml(it.game_stage)}</div>
        </td>
        <td class="p-3">${annotators}</td>
        <td class="p-3 text-right font-mono text-emerald-600">${gap}</td>
        <td class="p-3 text-xs text-slate-500">${escapeHtml(it.max_gap_dim || '—')}</td>
        <td class="p-3 text-right text-xs text-slate-400">批次快速確認（Phase 4）</td>
      </tr>
    `
  }).join('')
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s) }
