// Phase 8.5 — 維度定義 review 頁邏輯。
// fetch /api/admin/dimension-review → 渲染 4 個 amber_confirmed:false 維度區塊。

const $ = id => document.getElementById(id)
const status = $('status')
const content = $('content')

loadReview()

async function loadReview() {
  try {
    const res = await fetch('/api/admin/dimension-review')
    if (res.status === 403) {
      status.textContent = '需要 admin 權限。'
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    renderReview(data)
  } catch (err) {
    status.textContent = `載入失敗:${err.message}`
  }
}

function renderReview(data) {
  status.textContent = `Reference annotator: ${data.reference_annotator}(已 is_complete ${data.total_amber_annotations} 筆) — 下方 ${data.dimensions.length} 個維度待 Amber confirm`
  content.innerHTML = data.dimensions.map(renderDimension).join('')
  // 接 audio play 按鈕
  document.querySelectorAll('button[data-play]').forEach(btn => {
    btn.addEventListener('click', () => togglePlay(btn))
  })
}

function renderDimension(dim) {
  const itemsHtml = dim.items.length === 0
    ? '<div class="text-sm text-slate-500 italic p-3">尚無此維度的標註資料</div>'
    : dim.items.map((it, idx) => `
        <tr class="border-t border-slate-200 dark:border-slate-700">
          <td class="px-3 py-2 text-xs font-mono text-slate-500">${idx + 1}</td>
          <td class="px-3 py-2 text-sm font-mono ${valueColor(it.value)}">${it.value.toFixed(2)}</td>
          <td class="px-3 py-2 text-sm">${escapeHtml(it.game_name)}</td>
          <td class="px-3 py-2 text-sm text-slate-600 dark:text-slate-400">${escapeHtml(it.game_stage)}</td>
          <td class="px-3 py-2">
            <button type="button" data-play="${escapeAttr(it.audio_id)}"
              class="text-xs px-2 py-1 rounded bg-slate-200 dark:bg-slate-700 hover:bg-amber-200 dark:hover:bg-amber-900">
              ▶ 播放
            </button>
            <a href="/annotate/${encodeURIComponent(it.audio_id)}?annotator=amber"
              class="ml-2 text-xs text-slate-500 hover:text-amber-500" title="開啟標註頁重新編輯這筆">
              ✎ 改標
            </a>
          </td>
        </tr>
      `).join('')

  const todoHtml = dim.todo_amber
    ? `<div class="mt-2 p-2 bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-300 dark:border-yellow-800 rounded text-xs text-yellow-900 dark:text-yellow-200">
         📌 TODO 給 Amber: ${escapeHtml(dim.todo_amber)}
       </div>`
    : ''

  return `
    <section class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 overflow-hidden">
      <header class="p-4 border-b border-slate-200 dark:border-slate-700">
        <div class="flex items-baseline justify-between mb-2">
          <h2 class="text-lg font-semibold">
            ⚠️ ${escapeHtml(dim.label_zh)}
            <span class="text-xs text-slate-500 font-mono ml-2">(${escapeHtml(dim.dim_id)})</span>
          </h2>
          <span class="text-xs px-2 py-0.5 bg-slate-200 dark:bg-slate-700 rounded">${escapeHtml(dim.category)}</span>
        </div>
        <p class="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">${escapeHtml(dim.definition)}</p>
        <div class="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          <div class="p-2 bg-slate-100 dark:bg-slate-900 rounded">
            <span class="font-medium text-slate-500">低錨 (low_anchor):</span>
            <span class="text-slate-700 dark:text-slate-300">${escapeHtml(dim.low_anchor)}</span>
          </div>
          <div class="p-2 bg-slate-100 dark:bg-slate-900 rounded">
            <span class="font-medium text-slate-500">高錨 (high_anchor):</span>
            <span class="text-slate-700 dark:text-slate-300">${escapeHtml(dim.high_anchor)}</span>
          </div>
        </div>
        ${todoHtml}
      </header>
      <table class="w-full text-sm">
        <thead class="bg-slate-50 dark:bg-slate-900 text-xs text-slate-500">
          <tr>
            <th class="px-3 py-2 text-left w-10">#</th>
            <th class="px-3 py-2 text-left w-16">值</th>
            <th class="px-3 py-2 text-left">遊戲</th>
            <th class="px-3 py-2 text-left">階段</th>
            <th class="px-3 py-2 text-left w-40">操作</th>
          </tr>
        </thead>
        <tbody>${itemsHtml}</tbody>
      </table>
    </section>
  `
}

function valueColor(v) {
  // 三段顏色幫 Amber 視覺掃描:極低 / 中段 / 極高
  if (v <= 0.25) return 'text-blue-600 dark:text-blue-400'
  if (v >= 0.75) return 'text-rose-600 dark:text-rose-400'
  return 'text-slate-700 dark:text-slate-300'
}

// 簡單一次只播一首
let currentAudio = null
let currentBtn = null

function togglePlay(btn) {
  const audioId = btn.dataset.play
  if (currentBtn === btn && currentAudio && !currentAudio.paused) {
    currentAudio.pause()
    btn.textContent = '▶ 播放'
    return
  }
  if (currentAudio) {
    currentAudio.pause()
    if (currentBtn) currentBtn.textContent = '▶ 播放'
  }
  currentAudio = new Audio(`/api/audio/${encodeURIComponent(audioId)}/stream`)
  currentBtn = btn
  btn.textContent = '⏸ 暫停'
  currentAudio.play().catch(err => {
    btn.textContent = `✗ ${err.message}`
  })
  currentAudio.addEventListener('ended', () => {
    btn.textContent = '▶ 播放'
  })
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}

function escapeAttr(s) {
  return escapeHtml(s)
}
