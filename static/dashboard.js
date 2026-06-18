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

// /api/me 快取一次（dashboard 進度列依此決定名字是否可點進詳細頁）
let _mePromise = null
function getMe() {
  if (!_mePromise) {
    _mePromise = fetch('/api/me')
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null)
  }
  return _mePromise
}

loadAll()

async function loadAll() {
  const includeFixture = includeFixtureBox.checked ? 'true' : 'false'
  await Promise.all([
    loadIcc(includeFixture),     // 只用於各標註員進度（不再渲染舊 ICC 表）
    loadAgreement(),             // Phase 8 — 業界對齊 CCC（取代誤導的 yyslin×Vic ICC）
    loadOverlap(includeFixture),
    loadPendingAnnotators(),  // Phase 8 — 待校準 widget(admin only)
    loadStatusCards(),        // Phase 10 — 資料品質狀態分布
    loadExportReadiness(),    // 出貨軌：Dual-View / Expert 可出貨量
    loadVicCredibility(),     // Vic 可信度：Dual-View 賣點
    loadAlignmentLinks(),     // Task 8 — 發佈客戶對齊連結 (admin only)
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

// 出貨軌：兩條獨立軌可出貨量(Dual-View 不靠 Amber / Expert 靠 Amber 仲裁)
async function loadExportReadiness() {
  try {
    const res = await fetch('/api/admin/export_readiness')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    const dual = $('ship-dual')
    const expert = $('ship-expert')
    if (dual) dual.textContent = d.dual_view_shippable ?? '—'
    if (expert) expert.textContent = d.expert_shippable ?? '—'
  } catch {
    // 靜默 — 載入失敗不阻斷其他 widget
  }
}

// Vic 可信度：三訊號合成狀態 + 賣點 statement
async function loadVicCredibility() {
  try {
    const res = await fetch('/api/admin/vic_credibility')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    const badge = $('vic-badge')
    if (badge) {
      // Tailwind CDN JIT 需看到完整 class 字串
      const styles = {
        trusted: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
        watch: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
        suspect: 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300',
        insufficient: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
      }
      const labels = { trusted: '可信', watch: '觀察中', suspect: '疑慮', insufficient: '資料不足' }
      badge.className = `text-xs px-2 py-0.5 rounded font-medium ${styles[d.status] || styles.insufficient}`
      badge.textContent = labels[d.status] || d.status
    }
    const stmt = $('vic-statement')
    if (stmt) stmt.textContent = d.statement || ''
    const wrap = $('vic-signals')
    if (wrap) {
      const s = d.signals || {}
      const v = s.variance || {}
      const e = s.extreme_consensus || {}
      const i = s.intra_rater || {}
      const cell = (title, body) =>
        `<div class="rounded border border-slate-200 dark:border-slate-700 p-3">
          <div class="text-xs text-slate-500">${title}</div>
          <div class="text-sm mt-1">${body}</div>
        </div>`
      const variance = v.insufficient
        ? '資料不足'
        : (v.suspect ? `疑似亂標（${(v.low_variance_dims || []).length} 維低變異）` : '通過 ✓')
      const extreme = e.insufficient
        ? `探針僅 ${e.checked} 道,資料不足`
        : `${e.checked} 道、違反 ${e.violations}（${Math.round(e.violation_rate * 100)}%）${e.pass ? '✓' : '✗'}`
      const intra = i.insufficient ? '待埋重複題' : `自穩 ${i.value}${i.pass ? ' ✓' : ' ✗'}`
      wrap.innerHTML = cell('每維方差', variance) + cell('極端共識探針', extreme) + cell('test-retest', intra)
    }
  } catch {
    // 靜默 — 載入失敗不阻斷其他 widget
  }
}

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
        key: 'creator_draft', label: 'creator 初標',
        cls: 'bg-sky-50 dark:bg-sky-950/30 border-sky-200 dark:border-sky-900',
        labelCls: 'text-sky-700 dark:text-sky-300',
        numCls: 'text-sky-900 dark:text-sky-200',
      },
      {
        key: 'industry_only', label: '待 creator',
        cls: 'bg-slate-50 dark:bg-slate-950/30 border-slate-200 dark:border-slate-800',
        labelCls: 'text-slate-700 dark:text-slate-300',
        numCls: 'text-slate-900 dark:text-slate-200',
      },
      {
        key: 'needs_arbitration', label: '待仲裁',
        cls: 'bg-indigo-50 dark:bg-indigo-950/30 border-indigo-200 dark:border-indigo-900',
        labelCls: 'text-indigo-700 dark:text-indigo-300',
        numCls: 'text-indigo-900 dark:text-indigo-200',
      },
      {
        key: 'fast_confirmable', label: '盲審待仲裁',
        cls: 'bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-900',
        labelCls: 'text-amber-700 dark:text-amber-300',
        numCls: 'text-amber-900 dark:text-amber-200',
      },
      {
        key: 'creator_ready', label: '✅ Creator Ready',
        cls: 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-900',
        labelCls: 'text-emerald-700 dark:text-emerald-300',
        numCls: 'text-emerald-900 dark:text-emerald-200',
      },
    ]
    // needs_arbitration / fast_confirmable 卡片若 > 0 可點到待仲裁清單。
    // fast_confirmable 現在只剩盲審抽中的對齊檔（非盲審對齊檔已自動晉升），須走完整仲裁。
    const linkFor = (key, n) => {
      if (n <= 0) return null
      if (key === 'needs_arbitration') return { href: '/admin/reconcile', title: '點開待仲裁清單' }
      if (key === 'fast_confirmable')  return { href: '/admin/reconcile', title: '盲審抽中：點開走完整仲裁' }
      return null
    }
    wrap.innerHTML = cards.map(c => {
      const n = data[c.key] || 0
      const pct = total > 0 ? Math.round((n / total) * 100) : 0
      const link = linkFor(c.key, n)
      const inner = `
        <div class="text-xs mb-1 ${c.labelCls}">${c.label}${link ? ' →' : ''}</div>
        <div class="text-2xl font-semibold font-mono ${c.numCls}">${n}</div>
        <div class="text-xs ${c.labelCls}">${pct}%</div>
      `
      if (link) {
        return `<a href="${link.href}" title="${link.title}" class="block p-3 rounded border cursor-pointer hover:shadow ${c.cls}">${inner}</a>`
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
    // 舊 ICC 表已停用（yyslin×Vic 對 0.7 會把商品當缺陷）；這裡只取 annotators 驅動進度列。
    loadProgressForAll(data.annotators, data.reference_annotator)
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

async function loadProgressForAll(annotators, referenceAnnotator) {
  const me = await getMe()
  const list = $('progress-list')
  if (!annotators.length) {
    list.innerHTML = '<div class="text-sm text-slate-500 dark:text-slate-400">尚無標註員資料</div>'
    return
  }
  list.innerHTML = annotators.map(a => {
    const clickable = me && (me.is_admin || a === me.annotator_id)
    const nameCell = clickable
      ? `<a href="/annotator/${encodeURIComponent(a)}" class="hover:underline hover:text-amber-600 dark:hover:text-amber-400">${escapeHtml(a)}</a>`
      : escapeHtml(a)
    // Amber (reference) 角色標示:仲裁者 / 校準基準,跟 L1 標註員區分
    const roleBadge = a === referenceAnnotator
      ? ` <span class="text-[10px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 whitespace-nowrap">仲裁者 / 校準基準</span>`
      : ''
    return `
    <div class="flex items-center gap-3" data-annotator="${escapeAttr(a)}">
      <div class="w-52 text-sm font-medium flex items-center gap-1.5 min-w-0">
        <span class="truncate">${nameCell}</span>${roleBadge}
      </div>
      <div class="flex-1 h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full bg-amber-500" data-bar style="width: 0%"></div>
      </div>
      <div class="w-28 text-right text-sm text-slate-500 dark:text-slate-400 font-mono" data-text>—</div>
    </div>
  `
  }).join('')

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

async function loadAgreement() {
  try {
    const res = await fetch('/api/stats/agreement')
    if (!res.ok) { $('icc-meta').textContent = `載入對齊資料失敗：HTTP ${res.status}`; return }
    renderAgreement(await res.json())
  } catch (err) {
    $('icc-meta').textContent = `載入對齊資料失敗：${err.message}`
  }
}

function renderAgreement(data) {
  const align = data.industry_alignment || {}
  const overall = data.overall_three_way || {}
  const dimKeys = Object.keys(align)
  const anyValue = dimKeys.some(k => !align[k].insufficient)
  $('icc-meta').textContent = anyValue
    ? 'creator × industry 對齊（CCC + 95% CI）；gate 在 CI 下界 ≥ 0.7。'
    : `資料不足（需 ≥ ${data.agreement_min_n || 30} 筆 creator+industry 共同標註）才出 CCC。`

  if (!dimKeys.length) {
    $('icc-table').innerHTML = '<tr><td colspan="5" class="p-3 text-sm text-slate-500 dark:text-slate-400">無資料</td></tr>'
    return
  }
  $('icc-table').innerHTML = dimKeys.map(k => {
    const a = align[k]
    const o = overall[k] || {}
    let cccCell, ciCell, statusCell
    if (a.insufficient) {
      cccCell = `<span class="text-slate-400">— (n=${a.n})</span>`
      ciCell = '—'
      statusCell = '<span class="text-slate-400">—</span>'
    } else {
      cccCell = a.value.toFixed(3)
      ciCell = `[${a.ci_low.toFixed(2)}, ${a.ci_high.toFixed(2)}]`
      statusCell = a.pass
        ? '<span class="text-emerald-600 dark:text-emerald-400">🟢</span>'
        : '<span class="text-red-600 dark:text-red-400">🔴</span>'
    }
    const iccRef = o.insufficient ? '—' : o.value.toFixed(3)
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700/60">
        <td class="p-3 font-medium">${escapeHtml(k)}</td>
        <td class="p-3 text-right font-mono ${a.insufficient ? 'text-slate-400' : ''}">${cccCell}</td>
        <td class="p-3 text-right font-mono text-slate-500 dark:text-slate-400 text-xs">${ciCell}</td>
        <td class="p-3 text-right font-mono text-slate-400 text-xs">${iccRef}</td>
        <td class="p-3 text-center">${statusCell}</td>
      </tr>
    `
  }).join('')
  $('icc-skipped').textContent = '三人整體 ICC（含 audience）僅供參考：偏低是預期（專業 vs 大眾分歧 = 商品特性），不作為品質 gate。'
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

// 發佈客戶對齊連結（admin only；403 → 靜默隱藏整個 section）
async function loadAlignmentLinks() {
  const section = $('alignment-links-section')
  try {
    const res = await fetch('/api/admin/alignment/links')
    if (res.status === 403) { section.classList.add('hidden'); return }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    section.classList.remove('hidden')
    await fillAudioOptions()
    renderAlignmentLinks((await res.json()).links)
    const btn = $('al-publish')
    if (!btn.dataset.bound) {
      btn.dataset.bound = '1'
      btn.addEventListener('click', publishAlignmentLink)
    }
  } catch {
    section.classList.add('hidden')
  }
}

async function fillAudioOptions() {
  const sel = $('al-audio')
  if (sel.dataset.filled) return
  try {
    const res = await fetch('/api/audio')
    if (!res.ok) return
    const items = await res.json()
    sel.innerHTML = items.map(a =>
      `<option value="${escapeAttr(a.filename)}">${escapeHtml(a.game_name)} – ${escapeHtml(a.game_stage)}</option>`
    ).join('')
    sel.dataset.filled = '1'
  } catch {
    // 靜默
  }
}

async function publishAlignmentLink() {
  const filename = $('al-audio').value
  const label = $('al-label').value.trim()
  if (!filename || !label) { alert('請選音檔並填客戶標籤'); return }
  const btn = $('al-publish')
  btn.disabled = true
  try {
    const res = await fetch('/api/admin/alignment/publish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, label, role: 'client', annotator_id: label }),
    })
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(e.detail || `HTTP ${res.status}`)
    }
    const d = await res.json()
    const box = $('al-result')
    box.classList.remove('hidden')
    box.innerHTML =
      `連結（只顯示一次，請複製）：<input readonly value="${escapeAttr(d.client_url)}"
        class="w-full border rounded px-2 py-1 mt-1 font-mono text-xs" onclick="this.select()" />`
    $('al-label').value = ''
    await loadAlignmentLinks()
  } catch (err) {
    alert(`發佈失敗：${err.message}`)
  } finally {
    btn.disabled = false
  }
}

function renderAlignmentLinks(links) {
  const wrap = $('al-links')
  if (!links.length) { wrap.innerHTML = '<div class="text-sm text-slate-500">尚無連結</div>'; return }
  wrap.innerHTML = links.map(l => `
    <div class="flex items-center gap-3 p-2 border-t border-slate-200 dark:border-slate-700">
      <div class="flex-1 min-w-0">
        <span class="font-medium">${escapeHtml(l.label)}</span>
        <span class="text-xs text-slate-500">(${escapeHtml(l.role)}・session ${escapeHtml(l.session_id || '—')})</span>
        ${l.revoked ? '<span class="text-xs text-rose-600">已撤銷</span>' : ''}
      </div>
      ${l.revoked ? '' : `<button type="button" data-revoke="${escapeAttr(l.id)}"
        class="px-2 py-1 text-xs rounded bg-rose-600 text-white hover:bg-rose-700">撤銷</button>`}
    </div>
  `).join('')
  wrap.querySelectorAll('button[data-revoke]').forEach(b =>
    b.addEventListener('click', () => revokeAlignmentLink(b.dataset.revoke)))
}

async function revokeAlignmentLink(linkId) {
  if (!confirm('確定撤銷此連結？客戶將立即無法存取。')) return
  try {
    const res = await fetch(`/api/admin/alignment/links/${encodeURIComponent(linkId)}/revoke`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await loadAlignmentLinks()
  } catch (err) {
    alert(`撤銷失敗：${err.message}`)
  }
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
