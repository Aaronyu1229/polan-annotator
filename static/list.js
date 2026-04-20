// Phase 2 音檔清單頁：fetch /api/audio 後渲染表格，顯示進度、✓/○ 狀態、duration。
// Phase 5 #2 加：dashboard card + sessionStorage-based session counter。

const STATUS = document.getElementById('status')
const TBODY = document.getElementById('audio-list')
const ANNOTATOR_SELECT = document.getElementById('annotator-select')
const PROGRESS_TEXT = document.getElementById('progress-text')
const PROGRESS_PERCENT = document.getElementById('progress-percent')
const PROGRESS_BAR = document.getElementById('progress-bar')
const DASHBOARD = document.getElementById('dashboard-card')

const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || 'guest'

const NEW_ANNOTATOR_OPTION = '__new__'

// sessionStorage key 以 annotator 隔離，避免切人後舊 session 干擾
const SESSION_START_KEY = `session_started_at:${annotator}`
const SESSION_COUNT_KEY = `session_completed_count:${annotator}`

function ensureSessionInitialized() {
  if (!sessionStorage.getItem(SESSION_START_KEY)) {
    sessionStorage.setItem(SESSION_START_KEY, new Date().toISOString())
    sessionStorage.setItem(SESSION_COUNT_KEY, '0')
  }
}

function consumeJustSavedFlag() {
  // 若 URL 帶 ?just_saved=1 則 session count +1，並立刻從 URL 抹除避免 F5/back/forward 重算 (R5)
  const url = new URL(window.location.href)
  if (url.searchParams.get('just_saved') === '1') {
    const prev = parseInt(sessionStorage.getItem(SESSION_COUNT_KEY) || '0', 10)
    sessionStorage.setItem(SESSION_COUNT_KEY, String(prev + 1))
    url.searchParams.delete('just_saved')
    const newUrl = url.pathname + (url.search ? url.search : '')
    window.history.replaceState({}, '', newUrl)
  }
}

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

function formatDurationHuman(sec) {
  // dashboard 用的人類友善時間：< 60s / < 60min / ≥ 60min 三段
  if (sec == null) return '—'
  if (sec < 60) return `${Math.round(sec)} 秒`
  if (sec < 3600) {
    const m = Math.floor(sec / 60)
    const s = Math.round(sec % 60)
    return s > 0 ? `${m} 分 ${s} 秒` : `${m} 分`
  }
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return m > 0 ? `${h} 小時 ${m} 分` : `${h} 小時`
}

function formatSessionStartTime() {
  const iso = sessionStorage.getItem(SESSION_START_KEY)
  if (!iso) return null
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

function renderDashboard(stats) {
  const sessionCount = parseInt(sessionStorage.getItem(SESSION_COUNT_KEY) || '0', 10)
  const sessionStart = formatSessionStartTime()

  if (!stats.has_data) {
    DASHBOARD.innerHTML = `
      <h2 class="text-base font-semibold mb-1">📊 你的標註進度</h2>
      <p class="text-sm text-slate-600 dark:text-slate-300">
        歡迎開始！還有 <strong>${stats.total_audio_files}</strong> 個音檔等你。
      </p>
    `
    return
  }

  const pct = Math.round(stats.completion_rate * 100)
  const sessionRight = sessionCount === 0
    ? `準備開始標第一個${sessionStart ? `（開工於 ${sessionStart}）` : ''}`
    : `${sessionCount} 個${sessionStart ? `（開工於 ${sessionStart}）` : ''}`

  const streak = stats.current_streak_days
  const streakRight = (streak == null || streak < 1)
    ? '—'
    : (streak >= 2 ? `已 ${streak} 天 🔥` : `已 ${streak} 天`)

  DASHBOARD.innerHTML = `
    <h2 class="text-base font-semibold mb-3">📊 你的標註進度</h2>
    <div class="space-y-1.5 text-sm">
      <div class="flex items-baseline justify-between">
        <span class="text-slate-600 dark:text-slate-300">已標註</span>
        <span><strong>${stats.completed_count} / ${stats.total_audio_files}</strong>
          <span class="ml-2 text-slate-500 dark:text-slate-400">${pct}%</span></span>
      </div>
      <div class="flex items-baseline justify-between">
        <span class="text-slate-600 dark:text-slate-300">本次 session</span>
        <span>${sessionRight}</span>
      </div>
      <div class="flex items-baseline justify-between">
        <span class="text-slate-600 dark:text-slate-300">平均耗時</span>
        <span>每個 ${formatDurationHuman(stats.avg_duration_sec)}</span>
      </div>
      <div class="flex items-baseline justify-between">
        <span class="text-slate-600 dark:text-slate-300">預計完成</span>
        <span>還需 ${formatDurationHuman(stats.estimated_remaining_sec)}</span>
      </div>
      <div class="flex items-baseline justify-between">
        <span class="text-slate-600 dark:text-slate-300">連續標註</span>
        <span>${streakRight}</span>
      </div>
    </div>
  `
}

async function fetchAndRenderDashboard() {
  // 用瀏覽器偵測到的 IANA TZ 傳給後端，讓 streak 用本地時區計算日界（R4）
  let tz = 'UTC'
  try {
    tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    // 極罕見環境無 Intl；fallback UTC
  }
  try {
    const url = `/api/stats/progress?annotator=${encodeURIComponent(annotator)}&tz=${encodeURIComponent(tz)}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const stats = await res.json()
    renderDashboard(stats)
  } catch (err) {
    console.warn('dashboard stats 載入失敗', err)
    DASHBOARD.innerHTML = `
      <p class="text-sm text-slate-500 dark:text-slate-400">
        統計暫時無法載入（${err.message}）。音檔清單仍可使用。
      </p>
    `
  }
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

// 順序：session init → consume ?just_saved= flag → dashboard + 列表並行載入
ensureSessionInitialized()
consumeJustSavedFlag()
populateAnnotatorSelect()
fetchAndRenderDashboard()
load()
