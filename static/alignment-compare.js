const qs = new URLSearchParams(window.location.search)
const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
const refClasses = ['A', 'B', 'C', 'D']

const CTX = {
  session_id: qs.get('session_id') || 's1',
  level_id: qs.get('level_id') || '',
  annotator_id: qs.get('annotator_id') || '',
  refs: [],
  roles: [],
  clientId: '',
  engineerId: '',
  deliverable: '',
  versions: []
}

let dimensions = []
let sets = []
let currentTab = '1'
const renderedTabs = new Set()
const selectedRefs = { 4: '' }

const $ = (id) => document.getElementById(id)

async function fetchJson(url, opts) {
  const res = await fetch(url, opts)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `${url} 回傳 ${res.status}`)
  return body
}

function uniqPush(list, value) {
  if (value && !list.includes(value)) list.push(value)
}

function compareNumber(a, b) {
  return a - b
}

function discoverContext() {
  const roles = []
  const refs = []
  const versions = []
  let deliverable = ''
  let clientId = qs.get('annotator_id') || ''
  let engineerId = ''

  sets.forEach((set) => {
    uniqPush(roles, set.annotator_role)
    if (set.audio_role === 'ref') uniqPush(refs, set.audio_id)
    if (set.audio_role === 'deliverable') {
      deliverable = deliverable || set.audio_id
      uniqPush(versions, set.version)
    }
    if (set.annotator_role === 'client') clientId = clientId || set.annotator_id
    if (set.annotator_role === 'engineer') engineerId = engineerId || set.annotator_id
  })

  CTX.roles = roles
  CTX.refs = refs
  CTX.clientId = clientId
  CTX.engineerId = engineerId || clientId
  CTX.deliverable = deliverable
  CTX.versions = versions.filter((version) => Number.isFinite(version) && version > 0).sort(compareNumber)
}

function renderChrome() {
  $('session-label').textContent = CTX.session_id || '-'
  $('level-label').textContent = CTX.level_id || '未指定'
  $('dimension-count').textContent = String(dimensions.length)
}

function refLabel(audioId) {
  const index = CTX.refs.indexOf(audioId)
  const letter = letters[index] || '?'
  return `${letter} ${audioId}`
}

function formatValue(value) {
  if (typeof value !== 'number') return '-'
  return `.${Math.round(value * 100).toString().padStart(2, '0')}`
}

function formatDelta(value) {
  if (typeof value !== 'number') return '-'
  return value.toFixed(2)
}

function formatSigned(value) {
  if (Math.abs(value) < 0.005) return '0.00'
  const sign = value > 0 ? '+' : '-'
  return `${sign}.${Math.round(Math.abs(value) * 100).toString().padStart(2, '0')}`
}

// Spec §7：門檻與 P2 alignment.js 同源；避免回頭抽共用模組動到已驗收頁。
function deltaBadge(delta, mode) {
  if (delta < 0.1) {
    return { label: mode === 'variance' ? '鎖定 · 保留' : '對齊', klass: 'ok' }
  }
  if (delta < 0.2) {
    return { label: mode === 'variance' ? '偏鎖定' : '接近', klass: 'mid' }
  }
  return { label: mode === 'variance' ? '分歧 · 需確認' : '認知落差', klass: 'hot' }
}

function findSet(role, audioId, readingType, version, audioRole) {
  return sets.find((set) =>
    set.annotator_role === role &&
    set.audio_id === audioId &&
    set.audio_role === audioRole &&
    set.version === version &&
    set.reading_type === readingType
  )
}

function valueOf(role, audioId, readingType, version, dim, audioRole = 'ref') {
  const set = findSet(role, audioId, readingType, version, audioRole)
  return set ? set.values[dim] : undefined
}

function identityFromSet(set) {
  return {
    session_id: set.session_id,
    level_id: set.level_id,
    annotator_id: set.annotator_id,
    annotator_role: set.annotator_role,
    audio_id: set.audio_id,
    audio_role: set.audio_role,
    version: set.version,
    reading_type: set.reading_type
  }
}

async function postJson(url, body) {
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
}

function trackDots(dots) {
  const dotHtml = dots.map((dot) => {
    const left = Math.max(0, Math.min(1, dot.value || 0)) * 100
    const targetClass = dot.target ? ' t' : ''
    return `<span class="dot ${dot.klass}${targetClass}" style="left:${left}%"></span>`
  }).join('')
  return `<div class="mtrack">${dotHtml}</div>`
}

function setupHtml(lockHtml, changeHtml, hint) {
  return `
    <div class="setup">
      <div class="seg"><span class="lbl">按住：</span>${lockHtml}</div>
      <div class="seg"><span class="lbl">變動：</span>${changeHtml}</div>
      <span class="hint">${hint}</span>
    </div>
  `
}

function refPicker(tab, selectedRef) {
  const buttons = CTX.refs.map((audioId) => `
    <button type="button" class="${audioId === selectedRef ? 'on' : ''}" data-ref-tab="${tab}" data-ref="${audioId}">${refLabel(audioId)}</button>
  `).join('')
  return `<div class="refpick">${buttons}</div>`
}

function empty(message, hot) {
  return `<div class="empty${hot ? ' error' : ''}">${message}</div>`
}

function renderPending(tab) {
  $(tab).innerHTML = empty('此區資料渲染中')
}

async function renderTab(tab) {
  if (renderedTabs.has(tab)) return
  const panel = $(`p${tab}`)
  renderPending(`p${tab}`)
  try {
    if (tab === '1') await renderTab1(panel)
    if (tab === '4') await renderTab4(panel)
    if (tab === '3') panel.innerHTML = empty('同關卡多 ref 將於 Task 3 完成')
    if (tab === '2') panel.innerHTML = empty('第一版 vs 第二版將於 Task 3 完成')
    renderedTabs.add(tab)
  } catch (err) {
    panel.innerHTML = empty(`載入失敗：${err.message}`, true)
  }
}

function show(tab) {
  currentTab = String(tab)
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.remove('on'))
  $(`p${currentTab}`).classList.add('on')
  document.querySelectorAll('.tab').forEach((button) => {
    button.classList.toggle('on', button.dataset.tab === currentTab)
  })
  renderTab(currentTab)
}

async function renderTab1(panel) {
  const ref = CTX.refs[0]
  if (!ref) {
    panel.innerHTML = empty('此關卡尚無 ref reading')
    return
  }

  const engineerSet = findSet('engineer', ref, 'perceived', 0, 'ref')
  const clientSet = findSet('client', ref, 'perceived', 0, 'ref')
  if (!engineerSet || !clientSet) {
    const missing = !engineerSet ? '音效師尚未標此 ref' : '客戶尚未標此 ref'
    panel.innerHTML = setupHtml(
      `<span class="pill lock">ref ${refLabel(ref)}</span><span class="pill lock">perceived</span>`,
      '<span class="pill eng">音效師</span><span class="vs">↔</span><span class="pill cli">客戶</span>',
      '差距＝認知落差，越大越要開工前對齊'
    ) + empty(missing)
    return
  }

  const pair = await postJson('/api/alignment/compare/pair', {
    a: identityFromSet(engineerSet),
    b: identityFromSet(clientSet)
  })

  let maxDim = dimensions[0]
  let maxDelta = -1
  const rows = dimensions.map((dim) => {
    const engineerValue = valueOf('engineer', ref, 'perceived', 0, dim.key)
    const clientValue = valueOf('client', ref, 'perceived', 0, dim.key)
    const delta = pair.diffs[dim.key] || 0
    const badge = deltaBadge(delta, 'diff')
    if (delta > maxDelta) {
      maxDim = dim
      maxDelta = delta
    }
    return `
      <tr class="${badge.klass === 'hot' ? 'row-hot' : ''}">
        <td class="dimn">${dim.display_name}</td>
        <td>${trackDots([{ klass: 'eng', value: engineerValue }, { klass: 'cli', value: clientValue }])}</td>
        <td class="center num">${formatValue(engineerValue)}</td>
        <td class="center num">${formatValue(clientValue)}</td>
        <td class="center num">${formatDelta(delta)}</td>
        <td class="center"><span class="badge b-${badge.klass}">${badge.label}</span></td>
      </tr>
    `
  }).join('')

  const trustedCount = Math.max(dimensions.length - 1, 0)
  const engMax = valueOf('engineer', ref, 'perceived', 0, maxDim.key)
  const cliMax = valueOf('client', ref, 'perceived', 0, maxDim.key)
  panel.innerHTML = setupHtml(
    `<span class="pill lock">ref ${refLabel(ref)}</span><span class="pill lock">perceived</span>`,
    '<span class="pill eng">音效師</span><span class="vs">↔</span><span class="pill cli">客戶</span>',
    '差距＝認知落差，越大越要開工前對齊'
  ) + `
    <table>
      <tr><th style="width:150px">維度</th><th>位置</th><th class="center" style="width:90px">音效師</th><th class="center" style="width:90px">客戶</th><th class="center" style="width:80px">Δ</th><th class="center" style="width:110px">判讀</th></tr>
      ${rows}
    </table>
    <div class="read warm"><b>${maxDim.display_name}落差 ${formatDelta(maxDelta)}</b>：音效師 ${formatValue(engMax)}、客戶 ${formatValue(cliMax)}；其餘 ${trustedCount} 維一致、可信任。</div>
  `
}

function directionText(dim, diff) {
  if (Math.abs(diff) < 0.005) return { arrow: '=', klass: 'eq', text: '保持' }
  if (diff > 0 && dim.key === 'emotional_warmth') return { arrow: '↑', klass: 'up', text: '更烈一點' }
  if (diff < 0 && dim.key === 'emotional_warmth') return { arrow: '↓', klass: 'down', text: '更柔一點' }
  if (diff > 0) return { arrow: '↑', klass: 'up', text: '更正向' }
  return { arrow: '↓', klass: 'down', text: '更弱化' }
}

async function renderTab4(panel) {
  const ref = selectedRefs[4] || CTX.refs[0]
  selectedRefs[4] = ref
  if (!ref) {
    panel.innerHTML = empty('此關卡尚無 ref reading')
    return
  }

  const perceivedSet = findSet('client', ref, 'perceived', 0, 'ref')
  const targetSet = findSet('client', ref, 'target', 0, 'ref')
  const setup = setupHtml(
    `<span class="pill cli">客戶</span><span class="pill lock">ref ${refLabel(ref)}</span>`,
    '<span class="pill lock">perceived 聽到</span><span class="vs">↔</span><span class="pill lock">target 預期</span>',
    '這就是「像這首，但是…」的數字化'
  )

  if (!perceivedSet || !targetSet) {
    const missing = !perceivedSet ? '客戶尚未標此 ref 的 perceived' : '客戶尚未標此 ref 的 target'
    panel.innerHTML = refPicker(4, ref) + setup + empty(missing)
    bindRefPicker(panel, 4)
    return
  }

  await postJson('/api/alignment/compare/pair', {
    a: identityFromSet(perceivedSet),
    b: identityFromSet(targetSet)
  })

  const instructions = []
  const rows = dimensions.map((dim) => {
    const perceivedValue = valueOf('client', ref, 'perceived', 0, dim.key)
    const targetValue = valueOf('client', ref, 'target', 0, dim.key)
    const diff = targetValue - perceivedValue
    const dir = directionText(dim, diff)
    if (dir.text !== '保持') instructions.push(`${dim.display_name}${dir.text}`)
    return `
      <tr class="${Math.abs(diff) >= 0.1 ? 'row-hot' : ''}">
        <td class="dimn">${dim.display_name}</td>
        <td>${trackDots([{ klass: 'cli', value: perceivedValue }, { klass: 'cli', value: targetValue, target: true }])}</td>
        <td class="center num">${formatValue(perceivedValue)}</td>
        <td class="center num">${formatValue(targetValue)}</td>
        <td class="center"><span class="arrow ${dir.klass}">${dir.arrow}</span> <span class="num">${formatSigned(diff)}</span> ${dir.text}</td>
      </tr>
    `
  }).join('')

  const summary = instructions.length ? instructions.join('、') : '各維保持目前方向'
  panel.innerHTML = refPicker(4, ref) + setup + `
    <table>
      <tr><th style="width:150px">維度</th><th>聽到 → 預期</th><th class="center" style="width:80px">聽到</th><th class="center" style="width:80px">預期</th><th class="center" style="width:150px">方向</th></tr>
      ${rows}
    </table>
    <div class="read"><b>新曲製作指令（這首 ref 版）：</b>${summary}。</div>
  `
  bindRefPicker(panel, 4)
}

function bindRefPicker(panel, tab) {
  panel.querySelectorAll(`[data-ref-tab="${tab}"]`).forEach((button) => {
    button.addEventListener('click', () => {
      selectedRefs[tab] = button.dataset.ref
      renderedTabs.delete(String(tab))
      renderTab(String(tab))
    })
  })
}

async function init() {
  try {
    const params = new URLSearchParams({ session_id: CTX.session_id, level_id: CTX.level_id })
    const [dimsRes, readingsRes] = await Promise.all([
      fetchJson('/api/alignment/dimensions'),
      fetchJson(`/api/alignment/readings?${params.toString()}`)
    ])
    dimensions = dimsRes.dimensions || []
    sets = readingsRes.sets || []
    discoverContext()
    renderChrome()
    show(currentTab)
  } catch (err) {
    document.querySelectorAll('.panel').forEach((panel) => {
      panel.innerHTML = empty(`載入失敗：${err.message}`, true)
    })
  }
}

document.querySelectorAll('.tab').forEach((button) => {
  button.addEventListener('click', () => show(button.dataset.tab))
})

init()
