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
        目前無可鎖音檔(沒有達 gold prereq 的)。
        多人交叉但 spread 太寬的去
        <a class="text-amber-600 hover:underline" href="/admin/reconcile">仲裁頁</a> 處理。
      </td></tr>`
    return
  }
  tbody.innerHTML = items.map(it => {
    const annotators = it.annotators.map(a => {
      const isAmber = a === 'amber'
      return `<span class="text-xs px-1.5 py-0.5 rounded ${isAmber ? 'bg-amber-200 dark:bg-amber-900 font-medium' : 'bg-slate-200 dark:bg-slate-700'}">${escapeHtml(a)}</span>`
    }).join(' ')
    const spread = it.max_spread_value != null ? it.max_spread_value.toFixed(2) : '—'
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700" data-audio="${escapeAttr(it.audio_id)}">
        <td class="p-3">
          <div class="font-medium">${escapeHtml(it.game_name)}</div>
          <div class="text-xs text-slate-500">${escapeHtml(it.game_stage)}</div>
        </td>
        <td class="p-3">${annotators}</td>
        <td class="p-3 text-right font-mono text-emerald-600">${spread}</td>
        <td class="p-3 text-xs text-slate-500">${escapeHtml(it.max_spread_dim || '—')}</td>
        <td class="p-3 text-right">
          <button type="button" data-lock="${escapeAttr(it.audio_id)}"
            class="px-3 py-1.5 text-sm font-medium rounded bg-emerald-600 text-white hover:bg-emerald-700">
            🏆 鎖為 gold
          </button>
        </td>
      </tr>
    `
  }).join('')

  tbody.querySelectorAll('button[data-lock]').forEach(btn => {
    btn.addEventListener('click', () => lockAudio(btn.dataset.lock, btn))
  })
}

async function lockAudio(audioId, btn) {
  if (!confirm('確定把此檔鎖為 gold?\n鎖了會進 Sample Pack 商用集合。(仍可 unlock)')) return
  btn.disabled = true
  btn.textContent = '鎖定中…'
  try {
    const res = await fetch(`/api/admin/audio/${encodeURIComponent(audioId)}/lock_gold`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(JSON.stringify(err.detail || err))
    }
    // 該行從 list 移除(已鎖,不再算 lockable)
    const row = btn.closest('tr')
    if (row) row.remove()
    // 重撈刷新 meta
    const remaining = document.querySelectorAll('#tbody tr[data-audio]').length
    $('meta').textContent = `共 ${remaining} 筆可鎖 gold`
    if (remaining === 0) {
      load()  // 重新 render「無清單」訊息
    }
  } catch (err) {
    alert(`鎖定失敗:${err.message}`)
    btn.disabled = false
    btn.textContent = '🏆 鎖為 gold'
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s) }
