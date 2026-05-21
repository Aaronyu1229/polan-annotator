// 校準完成報告渲染（detailed）。
// fetch /api/calibration/report?annotator=X → 渲染 overall / 維度表 /
// scatter（admin）/ top-10（admin）。response shape 見
// docs/superpowers/specs/2026-05-19-calibration-report-detail-design.md

const $ = id => document.getElementById(id)
const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || ''

loadAll()

async function loadAll() {
  if (!annotator) {
    $('header-meta').textContent = '請從 URL 帶 ?annotator=xxx'
    return
  }
  $('back-link').href = `/?annotator=${encodeURIComponent(annotator)}`
  try {
    const res = await fetch(
      `/api/calibration/report?annotator=${encodeURIComponent(annotator)}`,
    )
    if (!res.ok) throw new Error(`報告 HTTP ${res.status}`)
    render(await res.json())
  } catch (err) {
    $('header-meta').textContent = `載入失敗：${err.message}`
  }
}

function render(r) {
  $('header-meta').textContent =
    `標註員：${r.annotator_name || r.annotator}` +
    (r.role ? `（${r.role}）` : '') +
    ` · 進度 ${r.calibration_progress}`

  if (r.is_reference) {
    $('overall-panel').innerHTML =
      `<p class="text-sm text-slate-600 dark:text-slate-400">` +
      `${escapeHtml(r.annotator_name || r.annotator)} 是 reference annotator，` +
      `不需要校準報告。</p>`
    $('dims-tbody').innerHTML = ''
    return
  }
  if (!r.overall) {
    $('overall-panel').innerHTML =
      `<p class="text-sm text-amber-700 dark:text-amber-300">尚未開始校準` +
      `（進度 ${escapeHtml(r.calibration_progress)}）。` +
      `<a class="underline" href="/calibration?annotator=` +
      `${encodeURIComponent(annotator)}">前往校準頁 →</a></p>`
    $('dims-tbody').innerHTML = ''
    return
  }

  renderOverall(r.overall, r.recommendations)
  renderDimensions(r.dimensions || [])
  if (r.scatter_data) renderScatter(r.scatter_data)
  if (r.top_deviations) renderTop(r.top_deviations)
}

function renderOverall(o, rec) {
  const recMap = {
    approved: ['🟢 建議認可', 'text-emerald-700 dark:text-emerald-300'],
    needs_training: ['🟡 需再訓練', 'text-yellow-700 dark:text-yellow-300'],
    not_recommended: ['🔴 不建議通過', 'text-rose-700 dark:text-rose-300'],
  }
  const [recLabel, recColor] = recMap[o.recommendation] || ['—', '']
  const retrain = (rec && rec.dims_to_retrain) || []
  $('overall-panel').innerHTML = `
    <div class="flex items-baseline justify-between mb-2">
      <div class="text-sm text-slate-500 dark:text-slate-400">整體 MAE</div>
      <div class="text-sm font-semibold ${recColor}">${recLabel}</div>
    </div>
    <div class="flex items-baseline gap-2">
      <span class="text-3xl font-semibold font-mono">${o.mae == null ? '—' : o.mae.toFixed(3)}</span>
      <span class="text-sm text-slate-500 dark:text-slate-400">門檻 ${o.threshold}</span>
    </div>
    <div class="text-sm text-slate-600 dark:text-slate-400 mt-2">
      警示維度 ${o.warning_dims_count} / ${o.warning_dims_threshold}
      ${retrain.length ? `· 需重訓：${retrain.map(escapeHtml).join('、')}` : ''}
    </div>
  `
}

function renderDimensions(dims) {
  const statusBadge = {
    ok: '<span class="text-emerald-600 dark:text-emerald-400 text-lg">🟢</span>',
    warning: '<span class="text-yellow-600 dark:text-yellow-400 text-lg">🟡</span>',
    no_data: '<span class="text-slate-400 text-lg">⚪</span>',
  }
  $('dims-tbody').innerHTML = dims.map(d => `
    <tr class="border-t border-slate-200 dark:border-slate-700${d.status === 'no_data' ? ' text-slate-400' : ''}">
      <td class="p-3 font-medium">${escapeHtml(d.display_name_zh)}</td>
      <td class="p-3">${d.category === 'subjective' ? '主觀' : '客觀'}</td>
      <td class="p-3 text-right font-mono">${d.overlap_count}</td>
      <td class="p-3 text-right font-mono">${d.mae == null ? '—' : d.mae.toFixed(3)}</td>
      <td class="p-3 text-center">${statusBadge[d.status] || '—'}</td>
    </tr>
  `).join('')
}

function renderScatter(scatter) {
  const grid = $('scatter-grid')
  const entries = Object.entries(scatter).filter(([, pts]) => pts.length)
  if (!entries.length) return
  $('scatter-panel').classList.remove('hidden')
  grid.innerHTML = entries.map(([dim, pts]) => `
    <figure class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 p-2">
      <figcaption class="text-xs text-slate-600 dark:text-slate-400 mb-1">${escapeHtml(dim)}</figcaption>
      ${scatterSvg(pts)}
    </figure>
  `).join('')
}

function scatterSvg(points) {
  const S = 140, P = 10, span = S - 2 * P
  const x = v => P + v * span
  const y = v => S - P - v * span  // SVG y 反向
  const dots = points.map(p =>
    `<circle cx="${x(p.amber).toFixed(1)}" cy="${y(p.annotator).toFixed(1)}" r="3" fill="#f59e0b" fill-opacity="0.6"/>`,
  ).join('')
  return `<svg viewBox="0 0 ${S} ${S}" class="w-full h-auto" role="img" aria-label="散點圖">
    <rect x="${P}" y="${P}" width="${span}" height="${span}" fill="none" stroke="#cbd5e1"/>
    <line x1="${P}" y1="${S - P}" x2="${S - P}" y2="${P}" stroke="#94a3b8" stroke-dasharray="3 3"/>
    ${dots}
  </svg>`
}

function renderTop(items) {
  if (!items.length) return
  $('top-panel').classList.remove('hidden')
  $('top-list').innerHTML = items.map(it => `
    <details class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 p-3 text-sm">
      <summary class="cursor-pointer flex items-center justify-between gap-3">
        <span class="font-medium">${escapeHtml(it.game)} · ${escapeHtml(it.section)}</span>
        <span class="font-mono text-rose-600 dark:text-rose-400">Δ${it.diff.toFixed(3)} ${escapeHtml(it.worst_dim_display)}</span>
      </summary>
      <audio controls preload="none" class="w-full mt-2" src="${escapeHtml(it.audio_url)}"></audio>
      <table class="w-full mt-2 text-xs">
        <tbody>
          ${Object.entries(it.all_dims).map(([dim, v]) => `
            <tr class="border-t border-slate-100 dark:border-slate-700/50">
              <td class="py-1">${escapeHtml(dim)}</td>
              <td class="py-1 text-right font-mono">Amber ${v.amber.toFixed(2)}</td>
              <td class="py-1 text-right font-mono">你 ${v.annotator.toFixed(2)}</td>
              <td class="py-1 text-right font-mono ${v.diff > 0.30 ? 'text-rose-600 dark:text-rose-400' : v.diff > 0.15 ? 'text-yellow-600 dark:text-yellow-400' : 'text-slate-500'}">Δ${v.diff.toFixed(2)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </details>
  `).join('')
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
