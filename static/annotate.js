// Phase 2 標註頁前端邏輯。
// 負責：載入 dimensions 與 audio metadata、渲染滑桿/chip、WaveSurfer 控制、
// localStorage 草稿（3 秒 idle 才寫）、upsert 到 POST /api/annotations、
// 鍵盤快捷鍵（Space/←/→/L/Home/Esc/Enter/Cmd+S）。

// ========== 基本狀態 ==========
const pathParts = window.location.pathname.split('/').filter(Boolean)
const audioIdFromPath = pathParts[0] === 'annotate' ? pathParts[1] : null
const qs = new URLSearchParams(window.location.search)
const AUDIO_ID = audioIdFromPath || qs.get('audio_id')
const ANNOTATOR = qs.get('annotator') || 'guest'

const DRAFT_KEY = `draft:${ANNOTATOR}:${AUDIO_ID}`
const DRAFT_DEBOUNCE_MS = 3000

// Phase 5 #2：session count 由 list.js 與此頁共同維護。連續儲存時中間的 annotate 頁
// 仍會收到 ?just_saved=1 flag — 各頁各自 +1，才能讓最終回首頁時 count 正確。
const SESSION_COUNT_KEY = `session_completed_count:${ANNOTATOR}`

function consumeJustSavedFlag() {
  const url = new URL(window.location.href)
  if (url.searchParams.get('just_saved') === '1') {
    const prev = parseInt(sessionStorage.getItem(SESSION_COUNT_KEY) || '0', 10)
    sessionStorage.setItem(SESSION_COUNT_KEY, String(prev + 1))
    url.searchParams.delete('just_saved')
    const newUrl = url.pathname + (url.search ? url.search : '')
    window.history.replaceState({}, '', newUrl)
  }
}

consumeJustSavedFlag()

const DIM_ORDER_LEFT = ['valence', 'arousal', 'emotional_warmth', 'tension_direction']
const DIM_ORDER_RIGHT = [
  'temporal_position', 'event_significance', 'loop_capability',
  'tonal_noise_ratio', 'spectral_density', 'world_immersion',
]
const ALL_DIMS = [...DIM_ORDER_LEFT, ...DIM_ORDER_RIGHT]

const LOOP_CAP_LABELS = { 0.0: 'one-shot', 0.5: '可循環', 1.0: '完整循環' }

const state = {
  dimensions: {},        // from /api/dimensions
  audio: null,           // from /api/audio/:id
  values: {},            // 維度當前值 dim_key -> number
  sourceType: null,      // string
  functionRoles: new Set(),
  genre: '',
  worldview: '',
  styles: [],
  notes: '',
  draftTimer: null,
  allAudioIds: [],       // 排序後所有 id，用於計算 position
  feedbacks: {},         // Phase 5 #3：dim_key -> { feedback_type, note_text }
}

// ========== DOM refs ==========
const $ = id => document.getElementById(id)
const dimsLeft = $('dims-left')
const dimsRight = $('dims-right')
const audioTitle = $('audio-title')
const audioMeta = $('audio-meta')
const audioWarning = $('audio-warning')
const playToggle = $('play-toggle')
const loopToggle = $('loop-toggle')
const playTimeEl = $('play-time')
const playDurationEl = $('play-duration')
const sourceTypesEl = $('source-types')
const functionRolesEl = $('function-roles')
const tagsToggle = $('tags-toggle')
const tagsPanel = $('tags-panel')
const tagsCaret = $('tags-caret')
const genreInput = $('genre-input')
const worldviewInput = $('worldview-input')
const styleInput = $('style-input')
const styleAdd = $('style-add')
const styleChips = $('style-chips')
const notesInput = $('notes-input')
const saveNextBtn = $('save-next-btn')
const saveStayBtn = $('save-stay-btn')
const skipBtn = $('skip-btn')
const saveError = $('save-error')
const draftStatus = $('draft-status')
const progressBar = $('progress-bar')
const progressPercent = $('progress-percent')
const currentIndex = $('current-index')
const totalCount = $('total-count')
const annotatorSelect = $('annotator-select')
const draftModal = $('draft-modal')
const draftModalMeta = $('draft-modal-meta')
const draftResume = $('draft-resume')
const draftDiscard = $('draft-discard')
const backLink = $('back-link')
// Phase 5 #3：feedback modal refs
const feedbackModal = $('feedback-modal')
const feedbackDimTitle = $('feedback-dim-title')
const feedbackNoteText = $('feedback-note-text')
const feedbackError = $('feedback-error')
const feedbackCancelBtn = $('feedback-cancel')
const feedbackSubmitBtn = $('feedback-submit')
let currentFeedbackDim = null  // 目前 popup 開給哪個維度

backLink.href = `/?annotator=${encodeURIComponent(ANNOTATOR)}`

const NEW_ANNOTATOR_OPTION = '__new__'

async function populateAnnotatorSelect() {
  let known = []
  try {
    const res = await fetch('/api/annotations/annotators')
    if (res.ok) known = await res.json()
  } catch (err) {
    console.warn('載入 annotators 失敗', err)
  }
  const all = Array.from(new Set([ANNOTATOR, ...known])).filter(Boolean).sort()
  const options = [
    ...all.map(a => `<option value="${escapeAttr(a)}"${a === ANNOTATOR ? ' selected' : ''}>${escapeHtml(a)}</option>`),
    `<option value="${NEW_ANNOTATOR_OPTION}">+ 新增…</option>`,
  ]
  annotatorSelect.innerHTML = options.join('')
}

annotatorSelect.addEventListener('change', () => {
  let next = annotatorSelect.value
  if (next === NEW_ANNOTATOR_OPTION) {
    const name = window.prompt('新增標註員 id（例如 amber、aaron）：', '')
    if (!name || !name.trim()) {
      annotatorSelect.value = ANNOTATOR
      return
    }
    next = name.trim()
  }
  if (next !== ANNOTATOR) {
    // Phase 5 #3 invariant #6：切換 annotator 顯式清空 feedback state，防呆未來 SPA 化
    resetFeedbackState()
    const url = new URL(window.location.href)
    url.searchParams.set('annotator', next)
    window.location.href = url.toString()
  }
})

// ========== 資料來源 ==========
// Layer 1 / Layer 2 label mapping — 和 src/constants.py 同步
const SOURCE_TYPES = [
  ['weapon',             '武器動作'],
  ['explosion',          '爆炸破壞'],
  ['impact',             '衝擊打擊'],
  ['character_vocal',    '角色發聲'],
  ['dialogue_vo',        '台詞對白'],
  ['ambience',           '環境氛圍'],
  ['environmental',      '環境點綴'],
  ['mechanical_vehicle', '機械載具'],
  ['creature_foley',     '生物擬音'],
  ['synthetic_designed', '抽象合成'],
]

const FUNCTION_ROLES = [
  ['ui',                'UI 介面'],
  ['gameplay_core',     '核心玩法'],
  ['reward_feedback',   '獎勵回饋'],
  ['negative_feedback', '失敗/負面回饋'],
  ['cinematic',         '過場/敘事'],
  ['musical_sfx',       '音樂化音效'],
  ['atmosphere',        '氛圍營造'],
  ['hybrid',            '混合型'],
]

// ========== HTTP 載入 ==========
async function fetchJson(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`)
  return res.json()
}

async function loadInitial() {
  if (!AUDIO_ID) {
    audioTitle.textContent = '缺少 audio_id'
    return
  }
  try {
    const [dims, audio, all] = await Promise.all([
      fetchJson('/api/dimensions'),
      fetchJson(`/api/audio/${encodeURIComponent(AUDIO_ID)}?annotator=${encodeURIComponent(ANNOTATOR)}`),
      fetchJson(`/api/audio?annotator=${encodeURIComponent(ANNOTATOR)}`),
    ])
    state.dimensions = dims
    state.audio = audio
    state.allAudioIds = all.map(a => a.id)

    renderAudioHeader()
    renderProgressHeader(all)
    renderDimensions()
    renderSourceTypes()
    renderFunctionRoles()
    await Promise.all([loadTagSuggestions(), loadFeedbacks()])
    // loadFeedbacks 回來後 state.feedbacks 已填；重新 render 維度把 💬 換成 ✅
    refreshAllFeedbackIcons()

    // baseline：先套 DB 既有值（若無）則套預設 / auto_compute 建議。
    // 這樣 draft modal「重新開始」時 baseline 已存在，不會留空滑桿。
    const hasDbPrefill = applyPrefillFromExisting()
    if (!hasDbPrefill) {
      applyDefaults()
    }

    // 再檢查 localStorage：有 draft 就跳 modal（無條件，spec 明說 draft 可以同時
    // 存在於 DB 已有 complete annotation 的狀況 — 讓使用者自己決定要不要 resume）
    await maybeOfferDraft()

    initWaveSurfer(audio)
  } catch (err) {
    audioTitle.textContent = `載入失敗：${err.message}`
    audioTitle.className = 'text-2xl font-semibold text-red-400'
  }
}

function renderAudioHeader() {
  const a = state.audio
  const title = a.is_brand_theme
    ? `${a.game_name} — ${a.game_stage}`
    : `${a.game_name} — ${a.game_stage}`
  audioTitle.textContent = title
  const parts = []
  if (a.duration_sec != null) parts.push(`duration ${a.duration_sec.toFixed(1)}s`)
  if (a.bpm != null) parts.push(`BPM ${Math.round(a.bpm)} (偵測)`)
  else parts.push('BPM N/A')
  if (a.existing_annotation && a.existing_annotation.updated_at) {
    const d = new Date(a.existing_annotation.updated_at)
    const complete = a.existing_annotation.is_complete ? '已標' : '草稿'
    parts.push(`${complete}於 ${d.toLocaleString('zh-TW')}`)
  }
  audioMeta.textContent = parts.join(' · ')

  if (a.is_brand_theme) {
    audioWarning.classList.remove('hidden')
    audioWarning.textContent = '三段式品牌主題曲 — 時序位置請手動選擇（不套用檔名建議）'
  }
}

function renderProgressHeader(allItems) {
  const total = allItems.length
  const idx = allItems.findIndex(a => a.id === AUDIO_ID)
  const done = allItems.filter(a => a.is_annotated_by_current_annotator).length
  currentIndex.textContent = idx >= 0 ? (idx + 1) : '—'
  totalCount.textContent = total
  const pct = total === 0 ? 0 : Math.round((done / total) * 100)
  progressBar.style.width = `${pct}%`
  progressPercent.textContent = `${pct}%`
}

// ========== 維度渲染 ==========
function renderDimensions() {
  dimsLeft.innerHTML = DIM_ORDER_LEFT.map(renderDimensionHtml).join('')
  dimsRight.innerHTML = DIM_ORDER_RIGHT.map(renderDimensionHtml).join('')
  ALL_DIMS.forEach(wireDimension)
}

function renderDimensionHtml(key) {
  const d = state.dimensions[key]
  if (!d) return ''
  const warning = d.amber_confirmed === false
    ? `<span class="text-amber-600 dark:text-amber-400 cursor-help" title="${escapeAttr(d.todo_amber || '尚未 Amber 確認')}">⚠️</span>`
    : ''
  const info = `<span class="text-slate-400 dark:text-slate-500 cursor-help" title="${escapeAttr(buildTooltip(d))}">ⓘ</span>`
  const valueDisplay = `<span class="font-mono text-xs text-amber-600 dark:text-amber-300" data-value="${key}">—</span>`

  // 💬/✅ slot 的 container；初始渲染先空，loadFeedbacks 回來再 fill
  const feedbackSlot = `<span class="dim-feedback-slot inline-flex" data-feedback-slot="${key}"></span>`

  if (d.type === 'discrete') {
    const opts = (d.options || [0.0, 0.5, 1.0]).map(v => {
      const label = LOOP_CAP_LABELS[v] || v
      return `
        <label class="flex items-center gap-1 text-sm cursor-pointer">
          <input type="radio" name="dim-${key}" value="${v}" class="dim-radio" data-dim="${key}">
          <span>${label}</span>
        </label>
      `
    }).join('')
    return `
      <div class="bg-white border border-slate-200 dark:bg-slate-800/60 dark:border-transparent p-3 rounded" data-dim-box="${key}">
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-medium flex items-center gap-1">
            ${escapeHtml(d.label_zh)} ${warning} ${info}
          </div>
          ${feedbackSlot}
        </div>
        <div class="flex items-center gap-4 text-slate-800 dark:text-slate-200">${opts}</div>
      </div>
    `
  }

  const suggestBtn = d.auto_compute
    ? `<button class="text-xs text-blue-600 hover:text-blue-500 dark:text-blue-400 dark:hover:text-blue-300 auto-suggest-btn" data-dim="${key}" data-suggest="">建議：—</button>`
    : ''

  return `
    <div class="bg-white border border-slate-200 dark:bg-slate-800/60 dark:border-transparent p-3 rounded" data-dim-box="${key}">
      <div class="flex items-center justify-between mb-1">
        <div class="text-sm font-medium flex items-center gap-1">
          ${escapeHtml(d.label_zh)} ${warning} ${info}
        </div>
        <div class="flex items-center gap-2">
          ${valueDisplay}
          ${feedbackSlot}
        </div>
      </div>
      <input type="range" min="0" max="1" step="0.05" value="0.5"
        class="polan-slider" data-dim="${key}">
      <div class="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-500 mt-1">
        <span>${escapeHtml(d.low_anchor || '低端')}</span>
        <span class="text-right">${escapeHtml(d.high_anchor || '高端')}</span>
      </div>
      <div class="mt-1">${suggestBtn}</div>
    </div>
  `
}

function buildTooltip(d) {
  const parts = [d.definition]
  if (d.low_anchor) parts.push(`低：${d.low_anchor}`)
  if (d.high_anchor) parts.push(`高：${d.high_anchor}`)
  return parts.join('\n')
}

function wireDimension(key) {
  const d = state.dimensions[key]
  if (!d) return
  if (d.type === 'discrete') {
    document.querySelectorAll(`input[type="radio"][data-dim="${key}"]`).forEach(el => {
      el.addEventListener('change', () => {
        state.values[key] = parseFloat(el.value)
        scheduleDraftSave()
      })
    })
    return
  }
  const slider = document.querySelector(`input[type="range"][data-dim="${key}"]`)
  const valueEl = document.querySelector(`[data-value="${key}"]`)
  slider.addEventListener('input', () => {
    const v = parseFloat(slider.value)
    state.values[key] = v
    valueEl.textContent = v.toFixed(2)
    scheduleDraftSave()
  })

  if (d.auto_compute) {
    const btn = document.querySelector(`.auto-suggest-btn[data-dim="${key}"]`)
    const suggested = state.audio?.auto_computed?.[key]
    if (btn && suggested != null) {
      btn.textContent = `建議：${suggested.toFixed(2)} [套用]`
      btn.dataset.suggest = suggested
      btn.addEventListener('click', () => {
        applyDimensionValue(key, suggested)
        scheduleDraftSave()
      })
    } else if (btn) {
      btn.textContent = '建議：N/A'
      btn.disabled = true
    }
  }
}

function applyDimensionValue(key, value) {
  const d = state.dimensions[key]
  state.values[key] = value
  if (d?.type === 'discrete') {
    document.querySelectorAll(`input[type="radio"][data-dim="${key}"]`).forEach(el => {
      el.checked = Math.abs(parseFloat(el.value) - value) < 1e-6
    })
  } else {
    const slider = document.querySelector(`input[type="range"][data-dim="${key}"]`)
    const valueEl = document.querySelector(`[data-value="${key}"]`)
    if (slider) slider.value = value
    if (valueEl) valueEl.textContent = value.toFixed(2)
  }
}

// ========== Layer 1 / Layer 2 chips ==========
// 選中 / 未選視覺靠 CSS .chip / .chip-active 控制（light + dark 同時處理）
function renderSourceTypes() {
  sourceTypesEl.innerHTML = SOURCE_TYPES.map(([key, label]) => `
    <button type="button" class="chip px-3 py-2 text-sm rounded" data-source="${key}">
      ${escapeHtml(label)}
    </button>
  `).join('')
  sourceTypesEl.querySelectorAll('[data-source]').forEach(btn => {
    btn.addEventListener('click', () => {
      setSourceType(btn.dataset.source)
      scheduleDraftSave()
    })
  })
}

function setSourceType(key) {
  state.sourceType = key
  sourceTypesEl.querySelectorAll('[data-source]').forEach(b => {
    b.classList.toggle('chip-active', b.dataset.source === key)
  })
}

function renderFunctionRoles() {
  functionRolesEl.innerHTML = FUNCTION_ROLES.map(([key, label]) => `
    <button type="button" class="chip px-3 py-2 text-sm rounded" data-role="${key}">
      ${escapeHtml(label)}
    </button>
  `).join('')
  functionRolesEl.querySelectorAll('[data-role]').forEach(btn => {
    btn.addEventListener('click', () => {
      toggleFunctionRole(btn.dataset.role)
      scheduleDraftSave()
    })
  })
}

function toggleFunctionRole(key) {
  if (state.functionRoles.has(key)) state.functionRoles.delete(key)
  else state.functionRoles.add(key)
  refreshFunctionRoleChips()
}

function refreshFunctionRoleChips() {
  functionRolesEl.querySelectorAll('[data-role]').forEach(b => {
    b.classList.toggle('chip-active', state.functionRoles.has(b.dataset.role))
  })
}

// ========== Phase 5 #3：維度 feedback ==========
// state.feedbacks: dim_key -> { feedback_type, note_text }
// 權威源是 DB（/api/feedback/dimension）— 不存 localStorage。
// 切換音檔 / 切換 annotator 時一律清空後重抓（invariant #6）。

function resetFeedbackState() {
  state.feedbacks = {}
}

async function loadFeedbacks() {
  // 切入此頁時先清空（Aaron invariant #6 的 loadAudio 等效位置），再 fetch 新音檔的 feedbacks
  resetFeedbackState()
  if (!AUDIO_ID || !ANNOTATOR) return
  try {
    const url = `/api/feedback/dimension?annotator=${encodeURIComponent(ANNOTATOR)}&audio_file_id=${encodeURIComponent(AUDIO_ID)}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    ;(data.feedbacks || []).forEach(f => {
      state.feedbacks[f.dimension_key] = {
        feedback_type: f.feedback_type,
        note_text: f.note_text,
      }
    })
  } catch (err) {
    console.warn('[feedback] 載入失敗，fallback 空狀態:', err)
  }
}

function feedbackIconHtml(dimKey) {
  const f = state.feedbacks[dimKey]
  if (f) {
    const title = f.feedback_type === 'note'
      ? `筆記：${f.note_text || '(空)'}`
      : `上次選擇：${f.feedback_type}`
    return `<button type="button" class="dim-feedback-btn text-emerald-600 dark:text-emerald-400 text-base cursor-pointer" data-feedback-dim="${dimKey}" title="${escapeAttr(title)}">✅</button>`
  }
  return `<button type="button" class="dim-feedback-btn text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 text-base cursor-pointer" data-feedback-dim="${dimKey}" title="對此維度定義給回饋">💬</button>`
}

function refreshAllFeedbackIcons() {
  document.querySelectorAll('[data-feedback-slot]').forEach(slot => {
    const dimKey = slot.getAttribute('data-feedback-slot')
    slot.innerHTML = feedbackIconHtml(dimKey)
  })
  wireAllFeedbackButtons()
}

function refreshFeedbackIcon(dimKey) {
  const slot = document.querySelector(`[data-feedback-slot="${dimKey}"]`)
  if (slot) slot.innerHTML = feedbackIconHtml(dimKey)
  wireAllFeedbackButtons()
}

function wireAllFeedbackButtons() {
  document.querySelectorAll('[data-feedback-dim]').forEach(btn => {
    // 先移除舊 listener 再綁新的（re-render 時避免重複觸發）
    const clone = btn.cloneNode(true)
    btn.parentNode.replaceChild(clone, btn)
    clone.addEventListener('click', (e) => {
      e.preventDefault()
      openFeedbackPopup(clone.getAttribute('data-feedback-dim'))
    })
  })
}

function openFeedbackPopup(dimKey) {
  currentFeedbackDim = dimKey
  const dimDef = state.dimensions[dimKey]
  feedbackDimTitle.textContent = dimDef?.label_zh || dimKey
  const existing = state.feedbacks[dimKey]
  feedbackModal.querySelectorAll('input[name="feedback-type"]').forEach(r => {
    r.checked = existing?.feedback_type === r.value
  })
  feedbackNoteText.value = existing?.note_text || ''
  syncNoteTextVisibility()
  feedbackError.classList.add('hidden')
  feedbackError.textContent = ''
  feedbackModal.classList.remove('hidden')
}

function closeFeedbackPopup() {
  feedbackModal.classList.add('hidden')
  currentFeedbackDim = null
}

function syncNoteTextVisibility() {
  const selected = feedbackModal.querySelector('input[name="feedback-type"]:checked')
  if (selected?.value === 'note') {
    feedbackNoteText.classList.remove('hidden')
  } else {
    feedbackNoteText.classList.add('hidden')
  }
}

feedbackModal.querySelectorAll('input[name="feedback-type"]').forEach(r => {
  r.addEventListener('change', syncNoteTextVisibility)
})
feedbackCancelBtn.addEventListener('click', closeFeedbackPopup)

feedbackSubmitBtn.addEventListener('click', async () => {
  if (!currentFeedbackDim) return
  const selected = feedbackModal.querySelector('input[name="feedback-type"]:checked')
  if (!selected) {
    showFeedbackError('請選擇一個選項')
    return
  }
  const ftype = selected.value
  const note = feedbackNoteText.value.trim()
  if (ftype === 'note' && !note) {
    showFeedbackError('選「加筆記」時 note 內容不能空白')
    return
  }
  try {
    const body = {
      audio_file_id: AUDIO_ID,
      annotator_id: ANNOTATOR,
      dimension_key: currentFeedbackDim,
      feedback_type: ftype,
      note_text: ftype === 'note' ? note : null,
    }
    const res = await fetch('/api/feedback/dimension', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      showFeedbackError(`送出失敗：${errBody.detail || '未知錯誤'}`)
      return
    }
    // 成功 — 更新 state + 重畫 icon 為 ✅ + 關 popup
    state.feedbacks[currentFeedbackDim] = {
      feedback_type: ftype,
      note_text: ftype === 'note' ? note : null,
    }
    refreshFeedbackIcon(currentFeedbackDim)
    closeFeedbackPopup()
  } catch (err) {
    showFeedbackError(`網路錯誤：${err.message}`)
  }
})

function showFeedbackError(msg) {
  feedbackError.textContent = msg
  feedbackError.classList.remove('hidden')
}

// ========== Tags 區塊 ==========
tagsToggle.addEventListener('click', () => {
  const hidden = tagsPanel.classList.toggle('hidden')
  tagsCaret.textContent = hidden ? '▸' : '▾'
})

async function loadTagSuggestions() {
  const fields = ['genre', 'worldview', 'style']
  const lists = { genre: $('genre-list'), worldview: $('worldview-list'), style: $('style-list') }
  for (const f of fields) {
    try {
      const data = await fetchJson(`/api/tag-suggestions?field=${f}`)
      lists[f].innerHTML = data.suggestions
        .map(v => `<option value="${escapeAttr(v)}">`).join('')
    } catch (err) {
      console.warn(`tag-suggestions ${f} 載入失敗`, err)
    }
  }
}

genreInput.addEventListener('input', () => { state.genre = genreInput.value; scheduleDraftSave() })
worldviewInput.addEventListener('input', () => { state.worldview = worldviewInput.value; scheduleDraftSave() })
notesInput.addEventListener('input', () => { state.notes = notesInput.value; scheduleDraftSave() })

styleAdd.addEventListener('click', addStyleFromInput)
styleInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault()
    addStyleFromInput()
  }
})

function addStyleFromInput() {
  const v = styleInput.value.trim()
  if (!v) return
  if (!state.styles.includes(v)) {
    state.styles.push(v)
    renderStyleChips()
    scheduleDraftSave()
  }
  styleInput.value = ''
}

function renderStyleChips() {
  styleChips.innerHTML = state.styles.map((s, i) => `
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200 rounded text-xs">
      ${escapeHtml(s)}
      <button data-style-remove="${i}" class="text-amber-700 hover:text-red-600 dark:text-amber-300 dark:hover:text-white">×</button>
    </span>
  `).join('')
  styleChips.querySelectorAll('[data-style-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.styleRemove, 10)
      state.styles.splice(idx, 1)
      renderStyleChips()
      scheduleDraftSave()
    })
  })
}

// ========== 初始值 / prefill / draft ==========
function applyDefaults() {
  // 連續維度預設 0.5；loop_capability 離散預設 0.5；auto_compute 採用建議值
  ALL_DIMS.forEach(key => {
    const d = state.dimensions[key]
    if (!d) return
    let v = 0.5
    if (d.auto_compute) {
      const suggested = state.audio?.auto_computed?.[key]
      if (suggested != null) v = suggested
    }
    applyDimensionValue(key, v)
  })

  // temporal_position 依檔名建議（但品牌主題曲跳過）
  if (!state.audio?.is_brand_theme) {
    const tp = state.dimensions['temporal_position']
    const mapping = tp?.filename_mapping || {}
    const suggested = mapping[state.audio?.game_stage]
    if (suggested != null) {
      applyDimensionValue('temporal_position', suggested)
    }
  }
}

function applyPrefillFromExisting() {
  const ea = state.audio?.existing_annotation
  if (!ea) return false
  ALL_DIMS.forEach(key => {
    if (ea[key] != null) applyDimensionValue(key, ea[key])
    else {
      // 既有 annotation 沒填此維度 → 走 default 邏輯
      const d = state.dimensions[key]
      let v = 0.5
      if (d?.auto_compute) {
        const s = state.audio?.auto_computed?.[key]
        if (s != null) v = s
      }
      applyDimensionValue(key, v)
    }
  })
  if (ea.source_type) setSourceType(ea.source_type)
  if (Array.isArray(ea.function_roles)) {
    ea.function_roles.forEach(r => state.functionRoles.add(r))
    refreshFunctionRoleChips()
  }
  state.genre = ea.genre_tag || ''
  state.worldview = ea.worldview_tag || ''
  state.styles = Array.isArray(ea.style_tag) ? [...ea.style_tag] : []
  state.notes = ea.notes || ''
  genreInput.value = state.genre
  worldviewInput.value = state.worldview
  notesInput.value = state.notes
  renderStyleChips()
  return true
}

function currentSnapshot() {
  return {
    values: { ...state.values },
    sourceType: state.sourceType,
    functionRoles: [...state.functionRoles],
    genre: state.genre,
    worldview: state.worldview,
    styles: [...state.styles],
    notes: state.notes,
    savedAt: new Date().toISOString(),
  }
}

function scheduleDraftSave() {
  clearTimeout(state.draftTimer)
  draftStatus.textContent = '尚未儲存'
  state.draftTimer = setTimeout(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(currentSnapshot()))
      const now = new Date()
      const hh = String(now.getHours()).padStart(2, '0')
      const mm = String(now.getMinutes()).padStart(2, '0')
      const ss = String(now.getSeconds()).padStart(2, '0')
      draftStatus.textContent = `草稿已存 ${hh}:${mm}:${ss}`
    } catch (err) {
      console.warn('localStorage 寫入失敗', err)
    }
  }, DRAFT_DEBOUNCE_MS)
}

async function maybeOfferDraft() {
  const raw = localStorage.getItem(DRAFT_KEY)
  if (!raw) {
    console.info('[draft] no draft at key', DRAFT_KEY)
    return
  }

  let snapshot
  try {
    snapshot = JSON.parse(raw)
  } catch (err) {
    // 壞掉的 JSON 直接清掉，沒有救的價值
    console.warn('[draft] JSON 損壞，已移除:', err)
    localStorage.removeItem(DRAFT_KEY)
    return
  }

  console.info('[draft] found draft at key', DRAFT_KEY, '→ 跳 modal')

  return new Promise(resolve => {
    const savedAt = snapshot.savedAt ? new Date(snapshot.savedAt) : new Date()
    draftModalMeta.textContent = `上次編輯時間：${savedAt.toLocaleString('zh-TW')}`
    draftModal.classList.remove('hidden')
    draftResume.onclick = () => {
      applyDraft(snapshot)
      draftModal.classList.add('hidden')
      resolve()
    }
    draftDiscard.onclick = () => {
      localStorage.removeItem(DRAFT_KEY)
      draftModal.classList.add('hidden')
      resolve()
    }
  })
}

function applyDraft(s) {
  Object.entries(s.values || {}).forEach(([k, v]) => applyDimensionValue(k, v))
  if (s.sourceType) setSourceType(s.sourceType)
  state.functionRoles = new Set(s.functionRoles || [])
  refreshFunctionRoleChips()
  state.genre = s.genre || ''
  state.worldview = s.worldview || ''
  state.styles = Array.isArray(s.styles) ? [...s.styles] : []
  state.notes = s.notes || ''
  genreInput.value = state.genre
  worldviewInput.value = state.worldview
  notesInput.value = state.notes
  renderStyleChips()
}

// ========== 儲存 ==========
async function submitAnnotation({ goNext }) {
  saveError.classList.add('hidden')
  saveError.textContent = ''

  if (state.functionRoles.size < 1) {
    showError('請至少選一個功能角色')
    return
  }

  const body = {
    audio_id: AUDIO_ID,
    annotator_id: ANNOTATOR,
    valence: state.values.valence ?? null,
    arousal: state.values.arousal ?? null,
    emotional_warmth: state.values.emotional_warmth ?? null,
    tension_direction: state.values.tension_direction ?? null,
    temporal_position: state.values.temporal_position ?? null,
    event_significance: state.values.event_significance ?? null,
    loop_capability: state.values.loop_capability ?? null,
    tonal_noise_ratio: state.values.tonal_noise_ratio ?? null,
    spectral_density: state.values.spectral_density ?? null,
    world_immersion: state.values.world_immersion ?? null,
    source_type: state.sourceType,
    function_roles: [...state.functionRoles],
    genre_tag: state.genre || null,
    worldview_tag: state.worldview || null,
    style_tag: state.styles,
    notes: state.notes || null,
  }

  try {
    const res = await fetch('/api/annotations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      showError(`儲存失敗：${err.detail || '未知錯誤'}`)
      return
    }
    const data = await res.json()
    draftStatus.textContent = data.is_complete ? '已提交 ✓' : '已儲存（半成品）'
    localStorage.removeItem(DRAFT_KEY)
    if (goNext) {
      // ?just_saved=1 讓下一頁（無論是下一個 audio 還是首頁）sessionStorage count +1
      if (data.next_audio_id) {
        window.location.href = `/annotate/${encodeURIComponent(data.next_audio_id)}?annotator=${encodeURIComponent(ANNOTATOR)}&just_saved=1`
      } else {
        alert('全部標完了 🎉')
        window.location.href = `/?annotator=${encodeURIComponent(ANNOTATOR)}&just_saved=1`
      }
    }
  } catch (err) {
    showError(`網路錯誤：${err.message}`)
  }
}

function showError(msg) {
  saveError.classList.remove('hidden')
  saveError.textContent = msg
}

saveNextBtn.addEventListener('click', () => submitAnnotation({ goNext: true }))
saveStayBtn.addEventListener('click', () => submitAnnotation({ goNext: false }))
skipBtn.addEventListener('click', skipToNext)

async function skipToNext() {
  try {
    const list = await fetchJson(`/api/audio?annotator=${encodeURIComponent(ANNOTATOR)}`)
    const next = findNextIncomplete(list, AUDIO_ID)
    if (next) {
      window.location.href = `/annotate/${encodeURIComponent(next)}?annotator=${encodeURIComponent(ANNOTATOR)}`
    } else {
      window.location.href = `/?annotator=${encodeURIComponent(ANNOTATOR)}`
    }
  } catch {
    window.location.href = `/?annotator=${encodeURIComponent(ANNOTATOR)}`
  }
}

function findNextIncomplete(list, currentId) {
  const ids = list.map(a => a.id)
  const completed = new Set(list.filter(a => a.is_annotated_by_current_annotator).map(a => a.id))
  const idx = ids.indexOf(currentId)
  const start = idx >= 0 ? idx + 1 : 0
  const ordered = ids.slice(start).concat(ids.slice(0, start))
  for (const aid of ordered) {
    if (aid !== currentId && !completed.has(aid)) return aid
  }
  return null
}

// ========== WaveSurfer ==========
let ws = null
let wsLoopAll = false
let nativeAudio = null

function initWaveSurfer(audio) {
  const streamUrl = `/api/audio/${encodeURIComponent(audio.id)}/stream`
  const fallback = () => mountNativeAudio(streamUrl)

  if (typeof WaveSurfer === 'undefined') {
    console.warn('WaveSurfer 未載入，fallback 到 <audio>')
    fallback()
    return
  }

  try {
    ws = WaveSurfer.create({
      container: '#waveform',
      waveColor: '#64748b',
      progressColor: '#f59e0b',
      height: 80,
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      cursorColor: '#f59e0b',
    })
    ws.load(streamUrl)

    ws.on('ready', () => {
      playDurationEl.textContent = `${ws.getDuration().toFixed(1)}s`
    })
    ws.on('timeupdate', t => { playTimeEl.textContent = t.toFixed(1) })
    ws.on('play', () => { playToggle.textContent = '⏸ 暫停' })
    ws.on('pause', () => { playToggle.textContent = '▶ 播放' })
    ws.on('finish', () => {
      if (wsLoopAll) {
        ws.setTime(0)
        ws.play()
      } else {
        playToggle.textContent = '▶ 播放'
      }
    })
    ws.on('error', err => {
      console.warn('WaveSurfer error, fallback:', err)
      fallback()
    })
  } catch (err) {
    console.warn('WaveSurfer 建立失敗：', err)
    fallback()
  }
}

function mountNativeAudio(streamUrl) {
  const el = $('native-audio-fallback')
  el.src = streamUrl
  el.classList.remove('hidden')
  nativeAudio = el
  playToggle.textContent = '使用瀏覽器播放器'
  playToggle.disabled = true
}

playToggle.addEventListener('click', () => { if (ws) ws.playPause() })
loopToggle.addEventListener('click', () => {
  wsLoopAll = !wsLoopAll
  loopToggle.textContent = wsLoopAll ? '🔁 循環中 ●' : '🔁 循環整首'
  loopToggle.classList.toggle('bg-amber-500', wsLoopAll)
  loopToggle.classList.toggle('text-slate-900', wsLoopAll)
})

// ========== 鍵盤快捷鍵 ==========
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase()
  const inInput = tag === 'input' || tag === 'textarea'

  // Cmd/Ctrl+S — 全域（包含 input focus）都攔截，否則會觸發瀏覽器另存
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    submitAnnotation({ goNext: false })
    return
  }

  // input/textarea focus 時不要攔其他快捷鍵（避免打字被搶）
  if (inInput) return

  if (e.key === ' ' || e.code === 'Space') {
    e.preventDefault()
    if (ws) ws.playPause()
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    if (ws) ws.setTime(Math.max(0, ws.getCurrentTime() - 5))
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    if (ws) ws.setTime(Math.min(ws.getDuration(), ws.getCurrentTime() + 5))
  } else if (e.key === 'Home') {
    e.preventDefault()
    if (ws) ws.setTime(0)
  } else if (e.key.toLowerCase() === 'l') {
    e.preventDefault()
    loopToggle.click()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    submitAnnotation({ goNext: true })
  } else if (e.key === 'Escape') {
    e.preventDefault()
    skipToNext()
  }
})

// ========== 工具 ==========
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, '&#10;') }

// 「儲存並下一個」後自動捲到頁面頂端（下一頁載入前的最後動作）
window.addEventListener('beforeunload', () => window.scrollTo(0, 0))

populateAnnotatorSelect()
loadInitial()
