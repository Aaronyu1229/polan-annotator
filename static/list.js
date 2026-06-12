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

// Phase 8：第一次登入觸發 welcome modal,pending_calibration 強制引導至校準頁。
maybeShowWelcomeModal(annotator)

async function maybeShowWelcomeModal(annotatorId) {
  const seenKey = `welcome_seen:${annotatorId}`
  let me
  try {
    const res = await fetch('/api/me')
    if (!res.ok) return  // 401/403:不渲染 modal,讓 auth.js 走 redirect
    me = await res.json()
  } catch {
    return
  }

  const isPending = me.status === 'pending_calibration'
  const alreadySeen = localStorage.getItem(seenKey) === '1'
  // pending 每次都顯示直到 Amber 認可(防止使用者 dismiss 後忘記校準)
  if (alreadySeen && !isPending) return

  const modal = document.getElementById('welcome-modal')
  const title = document.getElementById('welcome-title')
  const body = document.getElementById('welcome-body')
  const primary = document.getElementById('welcome-primary')
  const secondary = document.getElementById('welcome-secondary')
  if (!modal) return

  const displayName = me.display_name || me.name || annotatorId
  if (isPending) {
    title.textContent = `${displayName},歡迎加入珀瀾標註團隊 🎧`
    body.innerHTML = `
      <p>你目前的狀態是 <span class="px-1.5 py-0.5 bg-amber-200 dark:bg-amber-900 rounded text-xs font-medium">pending_calibration(待校準)</span>。</p>
      <p><strong>第一步:先到校準頁標完 Amber 已示範的音檔。</strong>校準是為了讓你跟其他標註員對齊維度理解,確保資料一致性。</p>
      <p>校準完成後,Amber 會在 Dashboard 認可你的標註,你就能標全部 1311 筆音檔。</p>
      <p>標註前請先<strong>聯絡 Amber 索取 Tension 維度規範</strong>(其他 6 個維度的定義在標註頁滑桿旁有解說)。</p>
      <p class="text-xs text-slate-500">有問題請 Line/Slack 找 Amber。</p>
    `
    primary.textContent = '前往校準頁 →'
    primary.onclick = () => {
      localStorage.setItem(seenKey, '1')
      window.location.href = `/calibration?annotator=${encodeURIComponent(annotatorId)}`
    }
    secondary.classList.remove('hidden')
    secondary.textContent = '稍後再說'
    secondary.onclick = () => modal.classList.add('hidden')
  } else {
    title.textContent = `${displayName},歡迎使用珀瀾標註工具 🎧`
    body.innerHTML = `
      <p>左側清單是所有 ${escapeHtml(String(1311))} 筆音檔,點開即可標註。</p>
      <p>右上角有 <strong>📊 Dashboard</strong> 看跨標註員 ICC 跟進度。</p>
      <p>每首標完按「儲存並下一個」會自動跳到下個未標檔案。</p>
    `
    primary.textContent = '開始標註'
    primary.onclick = () => {
      localStorage.setItem(seenKey, '1')
      modal.classList.add('hidden')
    }
    secondary.classList.add('hidden')
  }
  modal.classList.remove('hidden')
}

// Phase 3：校準連結帶上當前 annotator
const navCalibration = document.getElementById('nav-calibration')
if (navCalibration) {
  navCalibration.href = `/calibration?annotator=${encodeURIComponent(annotator)}`
}

// Phase 13-D:reference (amber) 自己不該看「我的報告」 — 她是 ground truth
const navMyReport = document.getElementById('nav-my-report')
if (navMyReport && annotator && annotator !== 'amber' && annotator !== 'guest') {
  navMyReport.href = `/calibration/report?annotator=${encodeURIComponent(annotator)}`
  navMyReport.classList.remove('hidden')
}

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
  // Phase 6+ cloud 模式：身分由 OAuth/Cloudflare Access 決定,dropdown 切不動人。
  // 顯示靜態文字避免誤導(原本 dropdown 在 cloud 切了會被 auth.js 強拉回原身分 + 後端忽略 ?annotator=)。
  // dev 模式 (me.email=null) 走下面 fallback 維持原 dropdown 邏輯。
  try {
    const meRes = await fetch('/api/me')
    if (meRes.ok) {
      const me = await meRes.json()
      if (me.email) {
        ANNOTATOR_SELECT.classList.add('hidden')
        const display = document.getElementById('annotator-display')
        if (display) {
          display.textContent = me.name || me.annotator_id || annotator
          display.classList.remove('hidden')
        }
        return
      }
    }
  } catch (err) {
    console.warn('/api/me 偵測失敗,fallback 到 dropdown', err)
  }

  let known = []
  try {
    const res = await fetch('/api/annotations/annotators')
    if (res.ok) known = await res.json()
  } catch (err) {
    console.warn('載入 annotators 失敗', err)
  }
  // 確保當前 annotator 出現在選項裡（即使 DB 尚未紀錄過）
  const all = Array.from(new Set([annotator, ...known])).filter(Boolean).sort()
  ANNOTATOR_SELECT.innerHTML = all.map(a =>
    `<option value="${escapeHtml(a)}"${a === annotator ? ' selected' : ''}>${escapeHtml(a)}</option>`
  ).join('')
}

ANNOTATOR_SELECT.addEventListener('change', () => {
  const next = ANNOTATOR_SELECT.value
  if (next && next !== annotator) {
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
  // Phase 12-C:資料品質 status badge
  const statusBadge = renderStatusBadge(item.status)
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
      <td class="p-3">${statusBadge}</td>
      <td class="p-3 text-right font-mono text-slate-500 dark:text-slate-400 text-xs">${formatDuration(item.duration_sec)}</td>
    </tr>
  `
}

// 三角架構 status badge(對齊 Dashboard 卡顏色)
function renderStatusBadge(status) {
  const map = {
    untouched:         { label: '未標', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400' },
    draft:             { label: '初標', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400' },
    creator_draft:     { label: 'creator 初標', cls: 'bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300' },
    industry_only:     { label: '待 creator', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400' },
    needs_arbitration: { label: '待仲裁', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300' },
    fast_confirmable:  { label: '盲審待仲裁', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' },
    creator_ready:     { label: '✅ Creator Ready', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' },
  }
  const m = map[status] || map.untouched
  return `<span class="text-xs px-2 py-0.5 rounded ${m.cls}">${m.label}</span>`
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
