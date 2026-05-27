// Phase 4 — 待快速確認清單 + 批次 fast-confirm。

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
    render(await res.json())
  } catch (err) {
    $('meta').textContent = `載入失敗:${err.message}`
  }
}

function render(items) {
  $('meta').textContent = `共 ${items.length} 筆待快速確認`
  const tbody = $('tbody')
  if (!items.length) {
    tbody.innerHTML = `
      <tr><td colspan="5" class="p-4 text-sm text-slate-500 text-center">
        目前無待快速確認的音檔（creator 與 industry 已對齊者）。
        gap 太寬或盲審抽中的去
        <a class="text-amber-600 hover:underline" href="/admin/reconcile">仲裁頁</a> 處理。
      </td></tr>`
    refreshConfirmBtn()
    return
  }
  tbody.innerHTML = items.map(it => {
    const annotators = it.annotators.map(a => {
      const isAmber = a === 'amber'
      return `<span class="text-xs px-1.5 py-0.5 rounded ${isAmber ? 'bg-amber-200 dark:bg-amber-900 font-medium' : 'bg-slate-200 dark:bg-slate-700'}">${escapeHtml(a)}</span>`
    }).join(' ')
    const gap = it.max_gap_value != null ? it.max_gap_value.toFixed(2) : '—'
    // 盲審抽中：不可快速確認，導到仲裁頁
    const pick = it.blind_audit
      ? `<a href="/admin/reconcile/${encodeURIComponent(it.audio_id)}" class="text-xs text-amber-600 hover:underline">🔍 需完整仲裁</a>`
      : `<input type="checkbox" class="row-check" data-audio="${escapeAttr(it.audio_id)}">`
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700" data-audio="${escapeAttr(it.audio_id)}">
        <td class="p-3 text-center">${pick}</td>
        <td class="p-3">
          <div class="font-medium">${escapeHtml(it.game_name)}</div>
          <div class="text-xs text-slate-500">${escapeHtml(it.game_stage)}</div>
        </td>
        <td class="p-3">${annotators}</td>
        <td class="p-3 text-right font-mono text-emerald-600">${gap}</td>
        <td class="p-3 text-xs text-slate-500">${escapeHtml(it.max_gap_dim || '—')}</td>
      </tr>
    `
  }).join('')

  tbody.querySelectorAll('.row-check').forEach(cb => {
    cb.addEventListener('change', refreshConfirmBtn)
  })
  refreshConfirmBtn()
}

function checkedIds() {
  return [...document.querySelectorAll('.row-check:checked')].map(cb => cb.dataset.audio)
}

function refreshConfirmBtn() {
  const n = checkedIds().length
  const btn = $('confirm-btn')
  btn.disabled = n === 0
  btn.textContent = n > 0 ? `✓ 確認選取 (${n})` : '✓ 確認選取'
}

$('select-all').addEventListener('change', (e) => {
  document.querySelectorAll('.row-check').forEach(cb => { cb.checked = e.target.checked })
  refreshConfirmBtn()
})

$('confirm-btn').addEventListener('click', async () => {
  const ids = checkedIds()
  if (!ids.length) return
  if (!confirm(`確定快速確認 ${ids.length} 筆?\n會以 creator 初標值寫入仲裁紀錄，狀態變 Creator Ready。`)) return
  const btn = $('confirm-btn')
  btn.disabled = true
  btn.textContent = '確認中…'
  try {
    const res = await fetch('/api/admin/arbitrate/fast-confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_ids: ids, notes: null }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(JSON.stringify(err.detail || err))
    }
    const data = await res.json()
    // 已確認的列移除
    data.confirmed.forEach(aid => {
      const row = document.querySelector(`tr[data-audio="${cssEscape(aid)}"]`)
      if (row) row.remove()
    })
    if (data.skipped.length) {
      alert(`已確認 ${data.confirmed.length} 筆；跳過 ${data.skipped.length} 筆:\n` +
        data.skipped.map(s => `${s.audio_id.slice(0, 8)}… (${s.reason})`).join('\n'))
    }
    const remaining = document.querySelectorAll('#tbody tr[data-audio]').length
    $('meta').textContent = `共 ${remaining} 筆待快速確認`
    $('select-all').checked = false
    if (remaining === 0) load()
  } catch (err) {
    alert(`快速確認失敗:${err.message}`)
  } finally {
    refreshConfirmBtn()
  }
})

function cssEscape(s) { return String(s).replace(/"/g, '\\"') }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s) }
