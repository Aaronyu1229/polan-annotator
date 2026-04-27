// Dashboard 主邏輯：fetch /api/stats/icc + /api/stats/overlap + 各 annotator 的 progress。
// include_fixture toggle 重新 fetch 全部 endpoint。

const $ = id => document.getElementById(id)
const includeFixtureBox = $('include-fixture')

const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})()

includeFixtureBox.addEventListener('change', loadAll)
loadAll()

async function loadAll() {
  const includeFixture = includeFixtureBox.checked ? 'true' : 'false'
  await Promise.all([
    loadIcc(includeFixture),
    loadOverlap(includeFixture),
  ])
  // progress 依 annotator 個別查 — 用 ICC endpoint 回的 annotators
  // 在 loadIcc 裡 trigger
}

async function loadIcc(includeFixture) {
  try {
    const res = await fetch(`/api/stats/icc?include_fixture=${includeFixture}`)
    const data = await res.json()
    renderIccTable(data)
    loadProgressForAll(data.annotators)
  } catch (err) {
    $('icc-meta').textContent = `載入 ICC 失敗：${err.message}`
  }
}

async function loadOverlap(includeFixture) {
  try {
    const res = await fetch(`/api/stats/overlap?include_fixture=${includeFixture}`)
    const items = await res.json()
    renderOverlapTable(items)
  } catch (err) {
    $('overlap-meta').textContent = `載入 overlap 失敗：${err.message}`
  }
}

async function loadProgressForAll(annotators) {
  const list = $('progress-list')
  if (!annotators.length) {
    list.innerHTML = '<div class="text-sm text-slate-500 dark:text-slate-400">尚無標註員資料</div>'
    return
  }
  list.innerHTML = annotators.map(a => `
    <div class="flex items-center gap-3" data-annotator="${escapeAttr(a)}">
      <div class="w-32 text-sm font-medium truncate">${escapeHtml(a)}</div>
      <div class="flex-1 h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full bg-amber-500" data-bar style="width: 0%"></div>
      </div>
      <div class="w-28 text-right text-sm text-slate-500 dark:text-slate-400 font-mono" data-text>—</div>
    </div>
  `).join('')

  await Promise.all(annotators.map(async a => {
    try {
      const res = await fetch(`/api/stats/progress?annotator=${encodeURIComponent(a)}&tz=${encodeURIComponent(TZ)}`)
      const d = await res.json()
      const row = list.querySelector(`[data-annotator="${cssEscape(a)}"]`)
      if (!row) return
      const pct = d.total_audio_files > 0
        ? Math.round((d.completed_count / d.total_audio_files) * 100)
        : 0
      row.querySelector('[data-bar]').style.width = `${pct}%`
      row.querySelector('[data-text]').textContent = `${d.completed_count} / ${d.total_audio_files}（${pct}%）`
    } catch (err) {
      console.warn(`progress for ${a} 失敗`, err)
    }
  }))
}

function renderIccTable(data) {
  const meta = data.sample_size > 0
    ? `基於 N=${data.sample_size} 筆共同標註的檔案，K=${data.annotators.length} 位標註員：${data.annotators.join(', ')}`
    : '尚無跨標註員資料（需 ≥ 2 位標註員各自完整標記 ≥ 2 個共同檔案）'
  $('icc-meta').textContent = meta

  const dims = data.dimensions || {}
  const dimKeys = Object.keys(dims)
  if (!dimKeys.length) {
    $('icc-table').innerHTML = '<tr><td colspan="5" class="p-3 text-sm text-slate-500 dark:text-slate-400">無資料</td></tr>'
    return
  }
  $('icc-table').innerHTML = dimKeys.map(k => {
    const d = dims[k]
    let statusCell = ''
    if (d.icc == null) {
      statusCell = `<span class="text-slate-400" title="${escapeAttr(d.note || '')}">—</span>`
    } else if (d.pass) {
      statusCell = '<span class="text-emerald-600 dark:text-emerald-400">🟢</span>'
    } else {
      statusCell = '<span class="text-red-600 dark:text-red-400">🔴</span>'
    }
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700/60">
        <td class="p-3 font-medium">${escapeHtml(k)}</td>
        <td class="p-3 text-slate-500 dark:text-slate-400 text-xs">${escapeHtml(d.category)}</td>
        <td class="p-3 text-right font-mono ${d.icc == null ? 'text-slate-400' : ''}">${d.icc == null ? '—' : d.icc.toFixed(3)}</td>
        <td class="p-3 text-right font-mono text-slate-500 dark:text-slate-400 text-xs">${d.threshold.toFixed(2)}</td>
        <td class="p-3 text-center">${statusCell}</td>
      </tr>
    `
  }).join('')

  // skipped dims 區塊
  const skipped = data.skipped_dimensions || []
  if (skipped.length) {
    $('icc-skipped').innerHTML = '⚠️ 略過維度：' + skipped.map(s =>
      `<code class="px-1 bg-slate-100 dark:bg-slate-700 rounded mx-1">${escapeHtml(s.key)}</code>(${escapeHtml(s.reason)})`
    ).join(' ')
  } else {
    $('icc-skipped').textContent = ''
  }
}

function renderOverlapTable(items) {
  if (!items.length) {
    $('overlap-meta').textContent = '尚無被多人標過的檔案'
    $('overlap-table').innerHTML = ''
    return
  }
  $('overlap-meta').textContent = `共 ${items.length} 個檔案被 ≥ 2 位標註者 is_complete 標過`
  $('overlap-table').innerHTML = items.map(it => `
    <tr class="border-t border-slate-200 dark:border-slate-700/60">
      <td class="p-3">
        <div class="font-medium">${escapeHtml(it.game_name)}</div>
        <div class="text-xs text-slate-500 dark:text-slate-400">${escapeHtml(it.game_stage)}</div>
      </td>
      <td class="p-3 text-xs text-slate-600 dark:text-slate-400">${it.annotators.map(escapeHtml).join(', ')}</td>
    </tr>
  `).join('')
}

// ─── helpers ──────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, '&#10;') }
function cssEscape(s) {
  // 簡易 CSS attribute selector escape — annotator id 不會含 quote
  return String(s).replace(/(["\\])/g, '\\$1')
}
