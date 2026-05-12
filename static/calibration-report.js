// Phase 9 — 校準完成報告渲染。
// fetch /api/calibration/report?annotator=X 後渲染 per-dim 表 + 完成度。

const $ = id => document.getElementById(id)
const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || ''

// 嘗試讀 dimensions_config 以取得 amber_confirmed 旗標(給 ⚠️ icon 用)
let DIMENSIONS_META = {}

loadAll()

async function loadAll() {
  if (!annotator) {
    $('header-meta').textContent = '請從 URL 帶 ?annotator=xxx'
    return
  }
  $('back-link').href = `/?annotator=${encodeURIComponent(annotator)}`

  try {
    const [reportRes, dimsRes] = await Promise.all([
      fetch(`/api/calibration/report?annotator=${encodeURIComponent(annotator)}`),
      fetch('/api/dimensions'),
    ])
    if (!reportRes.ok) throw new Error(`報告 HTTP ${reportRes.status}`)
    const report = await reportRes.json()
    if (dimsRes.ok) DIMENSIONS_META = await dimsRes.json()
    render(report)
  } catch (err) {
    $('header-meta').textContent = `載入失敗:${err.message}`
  }
}

function render(report) {
  $('header-meta').textContent = `標註員:${report.annotator_id}`

  if (report.is_reference) {
    $('progress-panel').innerHTML = `
      <p class="text-sm text-slate-600 dark:text-slate-400">
        ${escapeHtml(report.annotator_id)} 是 reference annotator,不需要校準報告。
      </p>
    `
    $('dims-tbody').innerHTML = ''
    return
  }

  const refTotal = report.reference_total ?? 0
  const overlap = report.total_overlap ?? 0
  const pct = refTotal > 0 ? Math.round((overlap / refTotal) * 100) : 0

  $('progress-text').textContent = `${overlap} / ${refTotal}`
  $('progress-pct').textContent = `(${pct}%)`
  $('progress-bar').style.width = `${pct}%`

  const nextAction = $('next-action')
  if (report.completed_calibration) {
    nextAction.innerHTML = `
      <div class="p-3 rounded bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900">
        <strong class="text-emerald-700 dark:text-emerald-300">✓ 校準音檔全部標完。</strong>
        <span class="text-emerald-700 dark:text-emerald-400">等 Amber 看報告後在 Dashboard 認可,你就解鎖。</span>
      </div>
    `
  } else if (overlap === 0) {
    nextAction.innerHTML = `
      <div class="p-3 rounded bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 text-amber-800 dark:text-amber-200">
        尚未完成任何校準音檔。<a href="/calibration?annotator=${encodeURIComponent(annotator)}" class="underline">前往校準頁 →</a>
      </div>
    `
  } else {
    nextAction.innerHTML = `
      <div class="p-3 rounded bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300">
        還有 ${refTotal - overlap} 筆校準音檔未完成。<a href="/calibration?annotator=${encodeURIComponent(annotator)}" class="text-amber-600 hover:underline">繼續校準 →</a>
      </div>
    `
  }

  renderDimensionsTable(report.dimensions || {})
}

function renderDimensionsTable(dims) {
  const tbody = $('dims-tbody')
  const dimOrder = [
    'valence', 'arousal', 'emotional_warmth', 'tension_direction',
    'temporal_position', 'event_significance', 'world_immersion',
  ]
  tbody.innerHTML = dimOrder.map(k => {
    const d = dims[k]
    if (!d) return ''
    const meta = DIMENSIONS_META[k] || {}
    const labelZh = meta.label_zh || k
    const unconfirmedIcon = meta.amber_confirmed === false
      ? ' <span title="此維度定義由 Amber refining 中,feedback 僅供參考">⚠️</span>'
      : ''
    if (d.sample_size === 0) {
      return `
        <tr class="border-t border-slate-200 dark:border-slate-700">
          <td class="p-3">${escapeHtml(labelZh)}${unconfirmedIcon}</td>
          <td class="p-3 text-right font-mono text-slate-400">0</td>
          <td class="p-3 text-right font-mono text-slate-400">—</td>
          <td class="p-3 text-right font-mono text-slate-400">—</td>
          <td class="p-3 text-right font-mono text-slate-400">—</td>
          <td class="p-3 text-center text-slate-400">—</td>
        </tr>
      `
    }
    const verdictBadge = {
      green:  '<span class="text-emerald-600 dark:text-emerald-400 text-lg">🟢</span>',
      yellow: '<span class="text-yellow-600 dark:text-yellow-400 text-lg">🟡</span>',
      red:    '<span class="text-rose-600 dark:text-rose-400 text-lg">🔴</span>',
    }[d.verdict] || '—'

    const offsetLabel = formatSignedOffset(d.mean_signed_offset)
    const pearsonLabel = d.pearson_r == null ? '—' : d.pearson_r.toFixed(2)

    return `
      <tr class="border-t border-slate-200 dark:border-slate-700">
        <td class="p-3 font-medium">${escapeHtml(labelZh)}${unconfirmedIcon}</td>
        <td class="p-3 text-right font-mono text-slate-500">${d.sample_size}</td>
        <td class="p-3 text-right font-mono">${d.mae.toFixed(3)}</td>
        <td class="p-3 text-right font-mono ${offsetColor(d.mean_signed_offset)}">${offsetLabel}</td>
        <td class="p-3 text-right font-mono text-slate-500">${pearsonLabel}</td>
        <td class="p-3 text-center">${verdictBadge}</td>
      </tr>
    `
  }).join('')
}

function formatSignedOffset(v) {
  if (v == null) return '—'
  if (Math.abs(v) < 0.05) return `≈0`
  if (v > 0) return `偏高 +${v.toFixed(2)}`
  return `偏低 ${v.toFixed(2)}`  // 已有負號
}

function offsetColor(v) {
  if (v == null || Math.abs(v) < 0.10) return 'text-slate-500'
  return Math.abs(v) > 0.25 ? 'text-rose-600 dark:text-rose-400' : 'text-yellow-600 dark:text-yellow-400'
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
