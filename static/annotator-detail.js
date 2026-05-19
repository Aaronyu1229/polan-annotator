// 標註員詳細頁 — fetch /api/stats/annotator/{id}/detail + /api/dimensions。
// admin 看任何人 / 本人看自己；權限由後端把關，403 顯示無權限。
// dimensions_config 是維度 label / amber_confirmed 的唯一來源（CLAUDE.md）。

const ANNOTATOR_ID = decodeURIComponent(window.location.pathname.split('/').pop())
const $ = id => document.getElementById(id)

const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})()

const CONTINUOUS_ORDER = [
  'valence', 'arousal', 'emotional_warmth', 'tension_direction',
  'temporal_position', 'event_significance', 'world_immersion',
  'tonal_noise_ratio', 'spectral_density',
]

const state = {
  files: [],
  dims: {},
  sortKey: 'time',
  expandedId: null,
}

load()

async function load() {
  try {
    const [detailRes, dimsRes] = await Promise.all([
      fetch(`/api/stats/annotator/${encodeURIComponent(ANNOTATOR_ID)}/detail?tz=${encodeURIComponent(TZ)}`),
      fetch('/api/dimensions'),
    ])
    if (detailRes.status === 403) {
      $('content').innerHTML = '<div class="p-6 text-sm text-slate-600 dark:text-slate-400">無權限檢視此標註員。</div>'
      return
    }
    if (!detailRes.ok) throw new Error(`HTTP ${detailRes.status}`)
    const data = await detailRes.json()
    state.dims = dimsRes.ok ? await dimsRes.json() : {}
    state.files = data.files || []
    $('title').textContent = `標註員明細 — ${data.annotator_name || data.annotator_id}`
    renderStats(data)
    bindSort()
    renderTable()
  } catch (err) {
    $('content').innerHTML = `<div class="p-6 text-sm text-red-600">載入失敗：${escapeHtml(err.message)}</div>`
  }
}

function fmtDuration(sec) {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

function statCard(label, value, sub) {
  return `
    <div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
      <div class="text-xs text-slate-500 dark:text-slate-400 mb-1">${escapeHtml(label)}</div>
      <div class="text-2xl font-semibold font-mono">${escapeHtml(value)}</div>
      <div class="text-xs text-slate-500 dark:text-slate-400">${escapeHtml(sub)}</div>
    </div>`
}

function renderStats(data) {
  const p = data.progress || {}
  const total = p.total_audio_files || 0
  const done = p.completed_count || 0
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  let html = statCard('完成筆數', `${done} / ${total}`, `${pct}%`)
  html += statCard('平均單筆耗時', fmtDuration(p.avg_duration_sec), '排除 ≥2h')
  html += statCard(
    '連續標註天數',
    p.current_streak_days == null ? '—' : String(p.current_streak_days),
    '',
  )
  if (data.calibration) {
    const c = data.calibration
    const worst = c.worst_dim ? (state.dims[c.worst_dim]?.label_zh || c.worst_dim) : '—'
    html += `
      <div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div class="text-xs text-slate-500 dark:text-slate-400 mb-1">vs Amber 校準</div>
        <div class="text-2xl font-semibold font-mono">${c.overall_mae == null ? '—' : Number(c.overall_mae).toFixed(3)}</div>
        <div class="text-xs text-slate-500 dark:text-slate-400">
          overall MAE · 最差：${escapeHtml(worst)} · 重疊 ${c.total_overlap} 筆 ·
          <a href="${escapeAttr(c.report_url)}" target="_blank" class="text-amber-600 dark:text-amber-400 hover:underline">看完整報告 ↗</a>
        </div>
      </div>`
  } else {
    const msg = data.annotator_id === 'amber'
      ? '此為 reference 標註員，無校準比對'
      : '與 Amber 無重疊檔案，無法比對'
    html += `<div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs text-slate-500 dark:text-slate-400 flex items-center">${msg}</div>`
  }
  $('stats').innerHTML = html
}

function sortedFiles() {
  const fs = state.files.slice()
  if (state.sortKey === 'filename') {
    fs.sort((a, b) => a.filename.localeCompare(b.filename))
  } else {
    fs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  }
  return fs
}

function dimLabel(key) {
  return state.dims[key]?.label_zh || key
}

function dimWarn(key) {
  return state.dims[key] && state.dims[key].amber_confirmed === false ? ' ⚠️' : ''
}

function chips(arr) {
  if (!arr || !arr.length) return '<span class="text-slate-400">—</span>'
  return arr.map(v =>
    `<span class="inline-block px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900 text-xs mr-1 mb-1">${escapeHtml(String(v))}</span>`,
  ).join('')
}

function renderDetail(f) {
  const dimRows = CONTINUOUS_ORDER.map(k => {
    const v = f[k]
    return `<div class="flex justify-between py-0.5">
      <span class="text-slate-600 dark:text-slate-400">${escapeHtml(dimLabel(k))}${dimWarn(k)}</span>
      <span class="font-mono">${v == null ? '—' : Number(v).toFixed(2)}</span>
    </div>`
  }).join('')
  const loop = (f.loop_capability || []).map(x => Number(x).toFixed(2)).join(', ') || '—'
  const dash = '<span class="text-slate-400">—</span>'
  return `
    <div class="bg-slate-50 dark:bg-slate-900/40 p-4 grid md:grid-cols-2 gap-4 text-sm">
      <div>
        <div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">維度</div>
        ${dimRows}
        <div class="flex justify-between py-0.5">
          <span class="text-slate-600 dark:text-slate-400">${escapeHtml(dimLabel('loop_capability'))}${dimWarn('loop_capability')}</span>
          <span class="font-mono">${escapeHtml(loop)}</span>
        </div>
      </div>
      <div class="space-y-2">
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">音源類型</div>${chips(f.source_type)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">功能角色</div>${chips(f.function_roles)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">Genre</div>${chips(f.genre_tag)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">Style</div>${chips(f.style_tag)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">世界觀</div>${f.worldview_tag ? escapeHtml(f.worldview_tag) : dash}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">備註</div>${f.notes ? escapeHtml(f.notes) : dash}</div>
      </div>
    </div>`
}

function renderTable() {
  const fs = sortedFiles()
  $('meta').textContent = `共 ${fs.length} 筆完成標註`
  if (!fs.length) {
    $('tbody').innerHTML = '<tr><td colspan="3" class="p-3 text-sm text-slate-500 dark:text-slate-400">尚無完成的標註</td></tr>'
    return
  }
  $('tbody').innerHTML = fs.map(f => {
    const expanded = state.expandedId === f.annotation_id
    const detail = expanded
      ? `<tr><td colspan="3" class="p-0">${renderDetail(f)}</td></tr>`
      : ''
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700/60 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/30"
          data-row="${escapeAttr(f.annotation_id)}">
        <td class="p-3"><div class="font-medium">${escapeHtml(f.filename)}</div></td>
        <td class="p-3 text-slate-600 dark:text-slate-400">${escapeHtml(f.game_name)} · ${escapeHtml(f.game_stage)}</td>
        <td class="p-3 text-right font-mono text-xs text-slate-500 dark:text-slate-400">${escapeHtml(fmtTime(f.updated_at))} ${expanded ? '▲' : '▼'}</td>
      </tr>
      ${detail}`
  }).join('')
  $('tbody').querySelectorAll('[data-row]').forEach(tr => {
    tr.addEventListener('click', () => {
      const id = tr.dataset.row
      state.expandedId = state.expandedId === id ? null : id
      renderTable()
    })
  })
}

function bindSort() {
  const sel = $('sort')
  if (!sel) return
  sel.addEventListener('change', e => {
    state.sortKey = e.target.value
    renderTable()
  })
}

// ─── helpers ──────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, '&#10;') }
