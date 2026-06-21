const qs = new URLSearchParams(window.location.search)
const palette = ['#3f6f8f', '#dc7a18', '#5b8f3f', '#8f3f6f']
const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')

const CTX = {
  session_id: qs.get('session_id') || 's1',
  level_id: qs.get('level_id') || '',
  level_label: qs.get('level_label') || qs.get('level_id') || '未命名關卡',
  annotator_id: qs.get('annotator_id') || 'guest',
  annotator_role: qs.get('annotator_role') || 'client',
  audio_ids: parseAudioIds(qs.get('audio_ids') || qs.get('audio_id') || ''),
  deliverable_id: qs.get('deliverable_id') || ''
}

const state = {}
const spec = {}
let dimensions = []
let styleTags = []

const $ = (id) => document.getElementById(id)

function parseAudioIds(value) {
  return value.split(',').map((id) => id.trim()).filter(Boolean)
}

function showBanner(message, type) {
  const banner = $('banner')
  banner.textContent = message
  banner.className = `banner ${type === 'ok' ? 'ok' : 'err'}`
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `${url} 回傳 ${res.status}`)
  return body
}

function refMeta(audioId) {
  const index = CTX.audio_ids.indexOf(audioId)
  return {
    id: audioId,
    index,
    letter: letters[index] || '?',
    color: palette[index % palette.length],
    name: audioId
  }
}

function formatValue(value) {
  return `.${Math.round(value * 100).toString().padStart(2, '0')}`
}

function setRole(role) {
  CTX.annotator_role = role
  document.body.className = role
  $('role-client').classList.toggle('on', role === 'client')
  $('role-engineer').classList.toggle('on', role === 'engineer')
  $('role-label').textContent = `${role === 'client' ? '客戶' : '音效師'} ${CTX.annotator_id}`
}

function ensureState() {
  CTX.audio_ids.forEach((audioId) => {
    state[audioId] = state[audioId] || {}
    spec[audioId] = spec[audioId] || { style_tags: [], loop: null, loop_length: null }
    dimensions.forEach((dim) => {
      state[audioId][dim.key] = state[audioId][dim.key] || { perceived: 0.5, target: 0.5 }
    })
  })
}

function renderRefbar() {
  const refbar = $('refbar')
  refbar.innerHTML = '<span class="reflabel">本關 ref：</span>'
  CTX.audio_ids.forEach((audioId) => {
    const meta = refMeta(audioId)
    const chip = document.createElement('div')
    chip.className = 'refchip'
    chip.innerHTML = `
      <span class="refdot" style="background:${meta.color}">${meta.letter}</span>
      <span>${meta.name}</span>
      <a class="play" href="/api/alignment/audio/${encodeURIComponent(audioId)}/stream" target="_blank" rel="noopener">▶</a>
      <span class="dur">ref</span>
    `
    refbar.appendChild(chip)
  })
}

function renderGrid() {
  const grid = $('grid')
  grid.innerHTML = `
    <div class="ghead">
      <div>維度</div>
      <div class="axis"><span>0.0 低</span><span>0.5</span><span>1.0 高</span></div>
      <div style="text-align:center">兩首 Δ（A↔B）</div>
    </div>
  `

  dimensions.forEach((dim) => {
    const row = document.createElement('div')
    row.className = 'row lock'
    row.dataset.dim = dim.key

    const dimCell = document.createElement('div')
    dimCell.className = 'dim'
    dimCell.innerHTML = `
      <div class="nm">${dim.display_name}</div>
      <div class="q">${dim.client_question || ''}</div>
      <div class="ends"><span>${dim.low_anchor || '低'}</span><span>${dim.high_anchor || '高'}</span></div>
    `

    const trackCell = document.createElement('div')
    trackCell.className = 'track-cell'
    const track = document.createElement('div')
    track.className = 'track'
    track.innerHTML = `
      <span class="gl" style="left:25%"></span>
      <span class="gl" style="left:50%"></span>
      <span class="gl" style="left:75%"></span>
    `

    CTX.audio_ids.forEach((audioId, index) => {
      const meta = refMeta(audioId)
      const value = state[audioId][dim.key]
      const handle = document.createElement('span')
      handle.className = 'handle'
      handle.dataset.audioId = audioId
      handle.dataset.dim = dim.key
      handle.dataset.kind = 'perceived'
      handle.style.left = `${value.perceived * 100}%`
      handle.style.background = meta.color
      handle.textContent = meta.letter
      track.appendChild(handle)

      const perceivedTag = document.createElement('span')
      perceivedTag.className = 'vtag'
      perceivedTag.dataset.audioId = audioId
      perceivedTag.dataset.dim = dim.key
      perceivedTag.dataset.kind = 'perceived'
      perceivedTag.style.left = `${value.perceived * 100}%`
      perceivedTag.style.top = index % 2 === 0 ? '18px' : '-22px'
      perceivedTag.style.color = meta.color
      perceivedTag.textContent = formatValue(value.perceived)
      track.appendChild(perceivedTag)

      const ring = document.createElement('span')
      ring.className = 'ring targetonly'
      ring.dataset.audioId = audioId
      ring.dataset.dim = dim.key
      ring.dataset.kind = 'target'
      ring.style.left = `${value.target * 100}%`
      ring.style.border = `2.5px solid ${meta.color}`
      track.appendChild(ring)

      const targetTag = document.createElement('span')
      targetTag.className = 'vtag targetonly'
      targetTag.dataset.audioId = audioId
      targetTag.dataset.dim = dim.key
      targetTag.dataset.kind = 'target'
      targetTag.style.left = `${value.target * 100}%`
      targetTag.style.top = index % 2 === 0 ? '-22px' : '18px'
      targetTag.style.color = meta.color
      targetTag.textContent = `→${formatValue(value.target)}`
      track.appendChild(targetTag)
    })

    const delta = document.createElement('div')
    delta.className = 'delta lock'
    delta.dataset.dim = dim.key
    delta.innerHTML = '<span class="dv">0.00</span><span class="badge">鎖定 · 保留</span>'

    trackCell.appendChild(track)
    row.appendChild(dimCell)
    row.appendChild(trackCell)
    row.appendChild(delta)
    grid.appendChild(row)
  })
}

function renderFooter() {
  $('foot').innerHTML = ''
}

function renderChrome() {
  $('level-label').textContent = CTX.level_label
  $('session-label').textContent = CTX.session_id
  $('compare-link').href = `/static/alignment-compare.html?session_id=${encodeURIComponent(CTX.session_id)}&level_id=${encodeURIComponent(CTX.level_id)}`
  $('progress').innerHTML = `本關進度　<b>0 / ${CTX.audio_ids.length} 首已標</b>（你 · ${CTX.annotator_role === 'client' ? '客戶' : '音效師'}）　·　對方：<b>0 / ${CTX.audio_ids.length}</b>`
  setRole(CTX.annotator_role)
}

async function loadContext() {
  try {
    const ctx = await fetchJson('/api/alignment/context')
    if (ctx.role === 'client') {
      CTX.session_id = ctx.session_id
      CTX.annotator_id = ctx.annotator_id
      CTX.annotator_role = 'client'
      CTX.audio_ids = ctx.alignment_audio_id ? [ctx.alignment_audio_id] : CTX.audio_ids
    }
  } catch (err) {
    if (!CTX.audio_ids.length) {
      showBanner(`無法載入存取資訊：${err.message}`, 'err')
    }
  }
}

async function init() {
  await loadContext()
  if (!CTX.audio_ids.length) {
    CTX.audio_ids = ['refA']
  }

  try {
    const [dimsRes, styleRes] = await Promise.all([
      fetchJson('/api/alignment/dimensions'),
      fetchJson('/api/alignment/style-options')
    ])
    dimensions = dimsRes.dimensions || []
    styleTags = styleRes.style_tags || []
    ensureState()
    renderChrome()
    renderRefbar()
    renderGrid()
    renderFooter()
  } catch (err) {
    showBanner(`載入失敗：${err.message}`, 'err')
  }
}

$('role-client').addEventListener('click', () => setRole('client'))
$('role-engineer').addEventListener('click', () => setRole('engineer'))

init()
