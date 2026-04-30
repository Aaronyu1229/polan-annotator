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
    injectUserBadge(user)
    injectAdminNav(user)
  } catch (err) {
    console.warn('auth.js /api/me parse failed', err)
  }
}

bootAuth()
