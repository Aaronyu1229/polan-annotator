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
    loadPendingAnnotators(),  // Phase 8 — 待校準 widget(admin only)
    loadStatusCards(),        // Phase 10 — 資料品質狀態分布
  ])
  // progress 依 annotator 個別查 — 用 ICC endpoint 回的 annotators
  // 在 loadIcc 裡 trigger
}

// Phase 8.5：admin 才能看到「維度定義 Review」連結。/api/me 取 is_admin。
async function showAdminLinks() {
  try {
    const res = await fetch('/api/me')
    if (!res.ok) return
    const me = await res.json()
    if (me.is_admin) {
      const link = $('review-dims-link')
      if (link) link.classList.remove('hidden')
    }
  } catch {
    // 靜默 — 非 admin / 未登入都不顯示
  }
}
showAdminLinks()

// Phase 10：5 張資料狀態卡(全 logged-in user 可看)
async function loadStatusCards() {
  const wrap = $('status-cards')
  try {
    const res = await fetch('/api/admin/audio_status_summary')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const total = data.total || 0
    $('status-total').textContent = `共 ${total} 筆`
    // Tailwind CDN JIT 需要看到完整 class 字串才會 generate;不能用 ${color}-50 動態組
    const cards = [
      {
        key: 'untouched', label: '未標',
        cls: 'bg-slate-50 dark:bg-slate-950/30 border-slate-200 dark:border-slate-800',
        labelCls: 'text-slate-700 dark:text-slate-300',
        numCls: 'text-slate-900 dark:text-slate-200',
      },
      {
        key: 'draft', label: '初標',
        cls: 'bg-sky-50 dark:bg-sky-950/30 border-sky-200 dark:border-sky-900',
        labelCls: 'text-sky-700 dark:text-sky-300',
        numCls: 'text-sky-900 dark:text-sky-200',
      },
      {
        key: 'cross_annotated', label: '多人交叉',
        cls: 'bg-indigo-50 dark:bg-indigo-950/30 border-indigo-200 dark:border-indigo-900',
        labelCls: 'text-indigo-700 dark:text-indigo-300',
        numCls: 'text-indigo-900 dark:text-indigo-200',
      },
      {
        key: 'lockable', label: '可鎖未鎖',
        cls: 'bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-900',
        labelCls: 'text-amber-700 dark:text-amber-300',
        numCls: 'text-amber-900 dark:text-amber-200',
      },
      {
        key: 'gold', label: '🏆 Gold',
        cls: 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-900',
        labelCls: 'text-emerald-700 dark:text-emerald-300',
        numCls: 'text-emerald-900 dark:text-emerald-200',
      },
    ]
    wrap.innerHTML = cards.map(c => {
      const n = data[c.key] || 0
      const pct = total > 0 ? Math.round((n / total) * 100) : 0
      // Phase 11:cross_annotated 卡片若 > 0 可點到仲裁列表(後端會 403 擋非 admin)
      const isClickable = c.key === 'cross_annotated' && n > 0
      const inner = `
        <div class="text-xs mb-1 ${c.labelCls}">${c.label}${isClickable ? ' →' : ''}</div>
        <div class="text-2xl font-semibold font-mono ${c.numCls}">${n}</div>
        <div class="text-xs ${c.labelCls}">${pct}%</div>
      `
      if (isClickable) {
        return `<a href="/admin/reconcile" title="點開待仲裁清單" class="block p-3 rounded border cursor-pointer hover:shadow ${c.cls}">${inner}</a>`
      }
      return `<div class="p-3 rounded border ${c.cls}">${inner}</div>`
    }).join('')
  } catch (err) {
    wrap.innerHTML = `<div class="text-sm text-red-600 col-span-5">載入狀態統計失敗:${escapeHtml(err.message)}</div>`
  }
}

// Phase 8：列出 pending_calibration 的人 + 認可按鈕。403 = 非 admin,靜默隱藏 section。
async function loadPendingAnnotators() {
  const section = $('pending-section')
  const list = $('pending-list')
  try {
    const res = await fetch('/api/admin/annotators/pending')
    if (res.status === 403) {
      section.classList.add('hidden')
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const items = await res.json()
    if (!items.length) {
      section.classList.add('hidden')
      return
    }
    section.classList.remove('hidden')
    list.innerHTML = items.map(it => {
      const prog = it.calibration_progress || { completed: 0, calibration_set_size: 0 }
      const pct = prog.calibration_set_size > 0
        ? Math.round((prog.completed / prog.calibration_set_size) * 100)
        : 0
      const profileLabel = it.annotator_profile === 'TBD_pending_amber_confirm'
        ? '<span class="text-xs px-2 py-0.5 bg-yellow-200 dark:bg-yellow-900 rounded">待 Amber 分類</span>'
        : `<span class="text-xs px-2 py-0.5 bg-slate-200 dark:bg-slate-700 rounded">${escapeHtml(it.annotator_profile)}</span>`
      const reportLink = prog.completed > 0
        ? `<a href="/calibration/report?annotator=${encodeURIComponent(it.id)}" target="_blank"
              class="text-xs text-amber-600 dark:text-amber-400 hover:underline" title="開啟對 amber 的校準對齊度報告">
            📊 查看報告 ↗
          </a>`
        : ''
      return `
        <div class="flex items-center gap-3 p-2 rounded bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-900" data-annotator="${escapeAttr(it.id)}">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-medium">${escapeHtml(it.name || it.id)}</span>
              <span class="text-xs text-slate-500">(${escapeHtml(it.id)})</span>
              ${profileLabel}
            </div>
            <div class="text-xs text-slate-600 dark:text-slate-400 mt-1 flex items-center gap-3">
              <span>校準進度:${prog.completed} / ${prog.calibration_set_size}(${pct}%)</span>
              ${reportLink}
            </div>
          </div>
          <button
            type="button"
            data-approve="${escapeAttr(it.id)}"
            class="px-3 py-1.5 text-sm font-medium rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
            ${prog.completed === 0 ? 'disabled title="尚未標任何校準音檔"' : ''}
          >✓ 認可校準通過</button>
        </div>
      `
    }).join('')
    list.querySelectorAll('button[data-approve]').forEach(btn => {
      btn.addEventListener('click', () => approveAnnotator(btn.dataset.approve, btn))
    })
  } catch (err) {
    section.classList.remove('hidden')
    list.innerHTML = `<div class="text-sm text-red-600">載入待校準清單失敗:${escapeHtml(err.message)}</div>`
  }
}

async function approveAnnotator(annotatorId, btn) {
  if (!confirm(`確定認可 ${annotatorId} 校準通過?完成後該標註員將可標全部音檔。`)) return
  btn.disabled = true
  btn.textContent = '處理中…'
  try {
    const res = await fetch(`/api/admin/annotators/${encodeURIComponent(annotatorId)}/approve`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    await loadPendingAnnotators()  // 刷新清單 — 通過後該人會從 list 消失
    await loadAll()  // 同步更新進度區塊
  } catch (err) {
    alert(`認可失敗:${err.message}`)
    btn.disabled = false
    btn.textContent = '✓ 認可校準通過'
  }
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
