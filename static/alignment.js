// BGM 對齊標註頁前端骨架。
// 載入 BGM 維度 view + 風格白名單 → 渲染每維雙值滑桿（perceived 現在感覺 / target 希望調整成）
// → 規格區（loop / loop_length）→ submit 串 POST /readings ×2（perceived+target）+ POST /spec。
// 沿用既有慣例：vanilla、無分號、polan-slider、chip toggle、繁中文案、無多餘動畫。

// ========== context（從 query string 取，給合理預設） ==========
const qs = new URLSearchParams(window.location.search)
const CTX = {
  session_id: qs.get('session_id') || 's1',
  annotator_id: qs.get('annotator_id') || 'guest',
  annotator_role: qs.get('annotator_role') || 'client',
  audio_id: qs.get('audio_id') || '',
  audio_role: qs.get('audio_role') || 'ref',
  version: parseInt(qs.get('version') || '0', 10),
}

const $ = (id) => document.getElementById(id)

function showBanner(msg, ok) {
  const el = $('banner')
  el.textContent = msg
  el.className = `mb-4 px-3 py-2 rounded text-sm ${ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`
  el.classList.remove('hidden')
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `${url} 回傳 ${res.status}`)
  return body
}

// ========== 渲染感受維度（雙值滑桿） ==========
// state[dim] = { perceived: 0.5, target: 0.5 }
const state = {}
let STYLE_TAGS = []  // world_immersion 下方的多選風格（白名單）
let LOOP = null
let LOOP_LENGTH = null

function sliderRow(dim, kind, label) {
  // kind: 'perceived' | 'target'
  const wrap = document.createElement('div')
  wrap.className = 'mt-2'
  const head = document.createElement('div')
  head.className = 'flex justify-between text-xs text-slate-500'
  const valSpan = document.createElement('span')
  valSpan.textContent = '0.50'
  head.innerHTML = `<span>${label}</span>`
  head.appendChild(valSpan)
  const slider = document.createElement('input')
  slider.type = 'range'
  slider.min = '0'; slider.max = '1'; slider.step = '0.01'; slider.value = '0.5'
  slider.className = `polan-slider ${kind === 'target' ? 'target-slider' : ''}`
  slider.addEventListener('input', () => {
    const v = parseFloat(slider.value)
    state[dim][kind] = v
    valSpan.textContent = v.toFixed(2)
  })
  wrap.appendChild(head)
  wrap.appendChild(slider)
  return wrap
}

function dimensionCard(dim) {
  state[dim.key] = { perceived: 0.5, target: 0.5 }
  const card = document.createElement('div')
  card.className = 'card border rounded-lg p-4 bg-white'
  card.innerHTML = `
    <div class="font-semibold">${dim.display_name}</div>
    <div class="text-sm text-slate-600 mt-1">${dim.client_question || ''}</div>
    <div class="grid grid-cols-3 gap-1 text-xs text-slate-400 mt-2">
      <span>低：${dim.low_anchor}</span>
      <span class="text-center">中：${dim.mid_anchor || ''}</span>
      <span class="text-right">高：${dim.high_anchor}</span>
    </div>`
  card.appendChild(sliderRow(dim.key, 'perceived', '現在的感覺'))
  card.appendChild(sliderRow(dim.key, 'target', '希望調整成'))

  // world_immersion 下方掛風格標籤（額外調味，不進數值比對）
  if (dim.key === 'world_immersion') {
    const tagWrap = document.createElement('div')
    tagWrap.className = 'mt-3'
    tagWrap.innerHTML = '<div class="text-sm mb-2">想額外加的元素（可多選）</div>'
    const tagBox = document.createElement('div')
    tagBox.className = 'flex flex-wrap gap-2'
    tagBox.id = 'style-chips'
    tagWrap.appendChild(tagBox)
    card.appendChild(tagWrap)
  }
  return card
}

// ========== chip 工具（toggle / single-select） ==========
function makeChip(label, value, onToggle) {
  const chip = document.createElement('span')
  chip.className = 'chip'
  chip.textContent = label
  chip.dataset.on = '0'
  chip.dataset.value = value
  chip.addEventListener('click', () => onToggle(chip))
  return chip
}

function renderStyleChips() {
  const box = $('style-chips')
  if (!box) return
  STYLE_TAGS.forEach((tag) => {
    box.appendChild(makeChip(tag, tag, (chip) => {
      chip.dataset.on = chip.dataset.on === '1' ? '0' : '1'
    }))
  })
}

function renderSingleSelect(boxId, options, onPick) {
  const box = $(boxId)
  options.forEach(([label, value]) => {
    box.appendChild(makeChip(label, value, (chip) => {
      // single-select：先清同組
      box.querySelectorAll('.chip').forEach((c) => { c.dataset.on = '0' })
      chip.dataset.on = '1'
      onPick(value)
    }))
  })
}

function collectStyleTags() {
  const box = $('style-chips')
  if (!box) return []
  return [...box.querySelectorAll('.chip[data-on="1"]')].map((c) => c.dataset.value)
}

// ========== submit ==========
async function submit() {
  const perceived = {}
  const target = {}
  Object.keys(state).forEach((dim) => {
    perceived[dim] = state[dim].perceived
    target[dim] = state[dim].target
  })
  const base = {
    session_id: CTX.session_id, annotator_id: CTX.annotator_id,
    annotator_role: CTX.annotator_role, audio_id: CTX.audio_id,
    audio_role: CTX.audio_role, version: CTX.version,
  }
  try {
    await fetchJson('/api/alignment/readings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...base, reading_type: 'perceived', values: perceived }),
    })
    await fetchJson('/api/alignment/readings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...base, reading_type: 'target', values: target }),
    })
    await fetchJson('/api/alignment/spec', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...base, loop: LOOP, loop_length: LOOP_LENGTH, style_tags: collectStyleTags() }),
    })
    showBanner('已儲存對齊標註（perceived + target + 規格）', true)
  } catch (err) {
    showBanner(`儲存失敗：${err.message}`, false)
  }
}

// ========== init ==========
async function init() {
  $('context-line').textContent =
    `session ${CTX.session_id} ・ ${CTX.annotator_role} ${CTX.annotator_id} ・ ${CTX.audio_role} v${CTX.version}` +
    (CTX.audio_id ? ` ・ ${CTX.audio_id}` : '（未指定 audio_id）')

  if (CTX.audio_id) {
    const player = $('player')
    player.src = `/api/audio/${encodeURIComponent(CTX.audio_id)}/stream`
    player.classList.remove('hidden')
  }

  try {
    const [dimsRes, styleRes] = await Promise.all([
      fetchJson('/api/alignment/dimensions'),
      fetchJson('/api/alignment/style-options'),
    ])
    STYLE_TAGS = styleRes.style_tags
    const section = $('dimensions')
    dimsRes.dimensions.forEach((dim) => section.appendChild(dimensionCard(dim)))
    renderStyleChips()
    renderSingleSelect('loop-chips', [['無縫循環', 'loop'], ['一次性', 'one_shot']], (v) => { LOOP = v })
    renderSingleSelect('loop-length-chips', [['~15s', 15], ['~30s', 30], ['~60s', 60]], (v) => { LOOP_LENGTH = v })
  } catch (err) {
    showBanner(`載入失敗：${err.message}`, false)
  }

  $('submit-btn').addEventListener('click', submit)
}

init()
