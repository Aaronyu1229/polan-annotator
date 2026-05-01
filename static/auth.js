// Phase 6 — 前端 auth 小工具
//
// 行為：
//   1. 載入時 fetch /api/me。
//   2. 401 → window.location 改 /login（OAuth 模式必登）。
//   3. 200 → 注入右上角「email · 登出」小工具（dev 模式 email 為 null 就不顯示）。
//
// 不引入 framework；vanilla JS，跟其他 static/*.js 一致。

const AUTH_FETCH_TIMEOUT_MS = 5000

async function fetchMe() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), AUTH_FETCH_TIMEOUT_MS)
  try {
    const res = await fetch('/api/me', {
      credentials: 'same-origin',
      signal: controller.signal,
    })
    return res
  } finally {
    clearTimeout(timer)
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[c])
}

function injectUserBadge(user) {
  // 只有 OAuth 模式（email 非 null）才顯示登出按鈕
  if (!user || !user.email) return

  const wrap = document.createElement('div')
  wrap.id = 'auth-badge'
  wrap.style.cssText =
    'position:fixed;top:8px;right:12px;z-index:50;display:flex;align-items:center;gap:8px;' +
    'font-size:12px;color:#475569;background:rgba(255,255,255,0.92);padding:4px 10px;' +
    'border:1px solid #e2e8f0;border-radius:9999px;backdrop-filter:blur(4px);'
  wrap.innerHTML = `
    <span title="${escapeHtml(user.email)}">${escapeHtml(user.name || user.email)}</span>
    <form action="/logout" method="post" style="display:inline">
      <button type="submit"
        style="background:transparent;border:0;cursor:pointer;color:#b91c1c;font-size:12px;padding:0;">
        登出
      </button>
    </form>
  `
  document.body.appendChild(wrap)
}

function injectAdminNav(user) {
  // admin 才顯示「音源管理」連結；只在不是 /upload 自身時顯示
  if (!user || !user.is_admin) return
  if (window.location.pathname === '/upload') return
  if (document.getElementById('admin-nav-upload')) return

  const link = document.createElement('a')
  link.id = 'admin-nav-upload'
  link.href = '/upload'
  link.title = '上傳新音源（admin）'
  link.textContent = '音源管理'
  link.style.cssText =
    'position:fixed;top:8px;right:12px;z-index:49;font-size:12px;color:#475569;' +
    'background:rgba(255,255,255,0.92);padding:4px 10px;border:1px solid #e2e8f0;' +
    'border-radius:9999px;backdrop-filter:blur(4px);text-decoration:none;'
  // 若已注入 user badge（OAuth 模式），把 admin link 排到 badge 左邊
  const badge = document.getElementById('auth-badge')
  if (badge) {
    const badgeRect = badge.getBoundingClientRect()
    link.style.right = `${Math.round(window.innerWidth - badgeRect.left + 8)}px`
  }
  document.body.appendChild(link)
}

function syncAnnotatorUrl(user) {
  // 修 ?annotator=guest 假象：登入後若 URL annotator query 跟 server 認定的 annotator_id
  // 不符（或缺），整路 replace 一次 — 讓 list.js / annotate.js 等其他 script 用對的 ID
  // 抓 sessionStorage、發 API 請求、設 dropdown 預選值。
  // 只在「真實有 email 的登入態」做（CF Access / OAuth），dev 模式 email=null 不動。
  if (!user || !user.email || !user.annotator_id) return false
  const params = new URLSearchParams(window.location.search)
  if (params.get('annotator') === user.annotator_id) return false
  params.set('annotator', user.annotator_id)
  const newUrl = window.location.pathname + '?' + params.toString() + window.location.hash
  // replace（不是 assign）— 不留 history，避免上一頁回到 ?annotator=guest
  window.location.replace(newUrl)
  return true
}

async function bootAuth() {
  let res
  try {
    res = await fetchMe()
  } catch (err) {
    // 網路掛了不要無限轉 /login，靜默處理
    console.warn('auth.js fetch /api/me failed', err)
    return
  }
  if (res.status === 401) {
    // 未登入 → 跳登入頁。current path 帶在 ?next=（後端目前未用，但保留給日後）
    const next = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.href = `/login?next=${next}`
    return
  }
  if (!res.ok) {
    console.warn('auth.js /api/me unexpected status', res.status)
    return
  }
  try {
    const user = await res.json()
    // URL 不對先校正，會 reload；reload 後第二趟 syncAnnotatorUrl 回 false 才繼續注入 UI
    if (syncAnnotatorUrl(user)) return
    injectUserBadge(user)
    injectAdminNav(user)
  } catch (err) {
    console.warn('auth.js /api/me parse failed', err)
  }
}

bootAuth()
