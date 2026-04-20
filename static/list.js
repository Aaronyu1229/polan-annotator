// Phase 2 音檔清單頁：fetch /api/audio 後渲染表格，顯示進度、✓/○ 狀態、duration。

const STATUS = document.getElementById('status')
const TBODY = document.getElementById('audio-list')
const ANNOTATOR_SELECT = document.getElementById('annotator-select')
const PROGRESS_TEXT = document.getElementById('progress-text')
const PROGRESS_PERCENT = document.getElementById('progress-percent')
const PROGRESS_BAR = document.getElementById('progress-bar')

const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || 'guest'

const NEW_ANNOTATOR_OPTION = '__new__'

async function populateAnnotatorSelect() {
  let known = []
  try {
    const res = await fetch('/api/annotations/annotators')
    if (res.ok) known = await res.json()
  } catch (err) {
    console.warn('載入 annotators 失敗', err)
  }
  // 確保當前 annotator 出現在選項裡（即使 DB 尚未紀錄過）
  const all = Array.from(new Set([annotator, ...known])).filter(Boolean).sort()
  const options = [
    ...all.map(a => `<option value="${escapeHtml(a)}"${a === annotator ? ' selected' : ''}>${escapeHtml(a)}</option>`),
    `<option value="${NEW_ANNOTATOR_OPTION}">+ 新增…</option>`,
  ]
  ANNOTATOR_SELECT.innerHTML = options.join('')
}

ANNOTATOR_SELECT.addEventListener('change', () => {
  let next = ANNOTATOR_SELECT.value
  if (next === NEW_ANNOTATOR_OPTION) {
    const name = window.prompt('新增標註員 id（例如 amber、aaron）：', '')
    if (!name || !name.trim()) {
      ANNOTATOR_SELECT.value = annotator
      return
    }
    next = name.trim()
  }
  if (next !== annotator) {
    const url = new URL(window.location.href)
    url.searchParams.set('annotator', next)
    window.location.href = url.toString()
  }
})

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[c])
}

function formatDuration(sec) {
  if (sec == null) return '—'
  return `${sec.toFixed(1)}s`
}

function renderRow(item) {
  const done = item.is_annotated_by_current_annotator
  const icon = done
    ? '<span class="status-dot text-emerald-600 dark:text-emerald-400">✓</span>'
    : '<span class="status-dot text-slate-300 dark:text-slate-600">○</span>'
  const tag = item.is_brand_theme
    ? '<span class="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200 rounded">品牌主題</span>'
    : '<span class="text-xs px-2 py-0.5 bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400 rounded">遊戲音樂</span>'
  const href = `/annotate/${encodeURIComponent(item.id)}?annotator=${encodeURIComponent(annotator)}`
  const rowTextClass = done
    ? 'text-slate-400 dark:text-slate-500'
    : 'text-slate-900 dark:text-slate-100'
  return `
    <tr class="border-t border-slate-100 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 cursor-pointer" data-href="${href}">
      <td class="p-3">${icon}</td>
      <td class="p-3 ${rowTextClass}">
        <div class="font-medium">${escapeHtml(item.game_name)}</div>
        <div class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">${escapeHtml(item.game_stage || '')}</div>
      </td>
      <td class="p-3">${tag}</td>
      <td class="p-3 text-right font-mono text-slate-500 dark:text-slate-400 text-xs">${formatDuration(item.duration_sec)}</td>
    </tr>
  `
}

function updateProgress(items) {
  const total = items.length
  const done = items.filter(i => i.is_annotated_by_current_annotator).length
  const pct = total === 0 ? 0 : Math.round((done / total) * 100)
  PROGRESS_TEXT.textContent = `${done} / ${total}`
  PROGRESS_PERCENT.textContent = `${pct}%`
  PROGRESS_BAR.style.width = `${pct}%`
}

async function load() {
  try {
    const res = await fetch(`/api/audio?annotator=${encodeURIComponent(annotator)}`)
    if (!res.ok) throw new Error(`API 回傳 HTTP ${res.status}`)
    const items = await res.json()
    TBODY.innerHTML = items.map(renderRow).join('')
    STATUS.textContent = items.length === 0
      ? '尚未掃描到任何音檔。請確認 data/audio/ 下有 .wav 檔案並重啟 server。'
      : `共 ${items.length} 個音檔`
    updateProgress(items)

    TBODY.querySelectorAll('tr[data-href]').forEach(row => {
      row.addEventListener('click', () => {
        window.location.href = row.getAttribute('data-href')
      })
    })
  } catch (err) {
    STATUS.textContent = `載入失敗：${err.message}`
    STATUS.className = 'text-red-600 text-sm mb-4'
  }
}

populateAnnotatorSelect()
load()
