// Phase 11 — 仲裁頁邏輯。
// Amber 看 audio + 其他人標 → 拖滑桿做自己決定 → 儲存 = POST /api/annotations as amber

const AUDIO_ID = decodeURIComponent(window.location.pathname.split('/').pop())
const $ = id => document.getElementById(id)

const HUMAN_DIMS = [
  ['valence',           'Valence 情緒正負向'],
  ['arousal',           'Arousal 喚醒程度'],
  ['emotional_warmth',  'Emotional Warmth 情緒溫度'],
  ['tension_direction', 'Tension Direction 張力方向'],
  ['temporal_position', 'Temporal Position 時序位置'],
  ['event_significance','Event Significance 事件重要性'],
  ['world_immersion',   'World Immersion 世界沉浸感'],
]

// 其他標註員的顏色映射(amber 不顯示在 marker — 因為 amber 是當前編輯者)
const RATER_COLOR = {
  yyslin1024: 'bg-sky-500',
  guest:      'bg-slate-400',
  vvgosick:   'bg-purple-500',
}

const state = {
  audio: null,
  annotations: [],            // 全部 annotators 的 annotation
  amberAnnotation: null,      // 若 amber 已標過,作為起始值
  values: {},                 // Amber 當前編輯值
  selectedSourceTypes: new Set(),
  selectedFunctionRoles: new Set(),
  selectedLoop: new Set(),
  notes: '',
}

let wavesurfer = null

load()

async function load() {
  try {
    const res = await fetch(`/api/admin/reconcile/${encodeURIComponent(AUDIO_ID)}`)
    if (res.status === 403) {
      $('audio-meta').textContent = '需要 admin 權限。'
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    state.audio = data.audio
    state.annotations = data.annotations || []
    state.amberAnnotation = state.annotations.find(a => a.annotator_id === 'amber') || null

    // 初始化 values:若 amber 已標,沿用;否則取其他人平均當起點
    initState()
    renderHeader()
    initWavesurfer()
    renderDimensions()
    renderTags()
  } catch (err) {
    $('audio-meta').textContent = `載入失敗:${err.message}`
  }
}

function initState() {
  for (const [dim] of HUMAN_DIMS) {
    if (state.amberAnnotation && state.amberAnnotation[dim] != null) {
      state.values[dim] = state.amberAnnotation[dim]
    } else {
      // 沒 amber 標 → 取其他人平均當起點(讓 Amber 微調更直覺)
      const others = state.annotations
        .filter(a => a.annotator_id !== 'amber' && a[dim] != null)
        .map(a => a[dim])
      state.values[dim] = others.length
        ? others.reduce((s, v) => s + v, 0) / others.length
        : 0.5
    }
  }
  // tags 初始化(若 amber 已標,沿用)
  if (state.amberAnnotation) {
    state.selectedSourceTypes = new Set(state.amberAnnotation.source_type || [])
    state.selectedFunctionRoles = new Set(state.amberAnnotation.function_roles || [])
    state.selectedLoop = new Set(state.amberAnnotation.loop_capability || [])
    state.notes = state.amberAnnotation.notes || ''
  }
}

function renderHeader() {
  // Phase 13-E:Amber 沒標過 → 補標(她要新增 annotation);標過 → 仲裁(她要參考別人改自己)
  const hasAmber = state.annotations.some(a => a.annotator_id === 'amber')
  const titleEl = document.querySelector('h1')
  if (titleEl) {
    titleEl.innerHTML = hasAmber
      ? `⚖️ 仲裁 <span id="audio-title" class="text-slate-500">${escapeHtml(state.audio.filename)}</span>`
      : `✏️ 補標 <span id="audio-title" class="text-slate-500">${escapeHtml(state.audio.filename)}</span>`
  }

  $('audio-meta').textContent =
    (hasAmber ? '🔄 你已標過此檔,參考其他人值微調 · ' : '🆕 你還沒標過此檔,聽完音檔做自己的判斷 · ') +
    `${state.audio.game_name} / ${state.audio.game_stage} · ` +
    `${state.audio.duration_sec ? state.audio.duration_sec.toFixed(1) + 's' : '—'} · ` +
    `${state.annotations.length} 位標註員:${state.annotations.map(a => a.annotator_id).join(', ')}`
}

function initWavesurfer() {
  wavesurfer = WaveSurfer.create({
    container: '#waveform',
    waveColor: '#94a3b8',
    progressColor: '#f59e0b',
    height: 60,
    barWidth: 2,
    cursorColor: '#f59e0b',
  })
  wavesurfer.load(`/api/audio/${encodeURIComponent(AUDIO_ID)}/stream`)
  wavesurfer.on('ready', () => {
    const dur = wavesurfer.getDuration()
    $('play-time').textContent = `0.0 / ${dur.toFixed(1)}s`
  })
  wavesurfer.on('audioprocess', () => {
    const t = wavesurfer.getCurrentTime()
    const dur = wavesurfer.getDuration()
    $('play-time').textContent = `${t.toFixed(1)} / ${dur.toFixed(1)}s`
  })
  wavesurfer.on('finish', () => {
    if ($('loop-toggle').checked) {
      wavesurfer.play(0)
    } else {
      $('play-btn').textContent = '▶ 播放'
    }
  })
  $('play-btn').addEventListener('click', () => {
    if (wavesurfer.isPlaying()) {
      wavesurfer.pause()
      $('play-btn').textContent = '▶ 播放'
    } else {
      wavesurfer.play()
      $('play-btn').textContent = '⏸ 暫停'
    }
  })
}

function renderDimensions() {
  const wrap = $('dims-list')
  wrap.innerHTML = HUMAN_DIMS.map(([key, label]) => {
    return `
      <div data-dim-row="${escapeAttr(key)}" class="space-y-1">
        <div class="flex items-baseline justify-between">
          <div class="text-sm font-medium">${escapeHtml(label)}</div>
          <div class="text-xs text-slate-500 font-mono" data-current-val="${escapeAttr(key)}">—</div>
        </div>
        <div class="relative h-6 flex items-center">
          <input
            type="range" min="0" max="1" step="0.05"
            class="w-full polan-slider"
            data-dim="${escapeAttr(key)}"
          >
          <!-- 其他人的 markers 由 JS 插入 -->
        </div>
        <div class="flex flex-wrap gap-2 text-xs" data-other-raters="${escapeAttr(key)}"></div>
      </div>
    `
  }).join('')

  HUMAN_DIMS.forEach(([key]) => {
    const slider = wrap.querySelector(`input[type="range"][data-dim="${cssEscape(key)}"]`)
    const valDisp = wrap.querySelector(`[data-current-val="${cssEscape(key)}"]`)
    slider.value = state.values[key]
    valDisp.textContent = `Amber: ${Number(state.values[key]).toFixed(2)}`
    slider.addEventListener('input', () => {
      state.values[key] = parseFloat(slider.value)
      valDisp.textContent = `Amber: ${state.values[key].toFixed(2)}`
    })
    // 渲染其他標註員的 markers(slider 軌道上的小色塊)
    const sliderContainer = slider.parentElement
    state.annotations
      .filter(a => a.annotator_id !== 'amber' && a[key] != null)
      .forEach(a => {
        const m = document.createElement('div')
        m.className = `rater-marker ${RATER_COLOR[a.annotator_id] || 'bg-slate-400'}`
        m.style.left = `calc(${a[key] * 100}% - 1px)`
        m.title = `${a.annotator_id}: ${a[key].toFixed(2)}`
        sliderContainer.appendChild(m)
      })
    // 同步顯示底下的 chips 給每個其他人的明確值
    const chipsWrap = wrap.querySelector(`[data-other-raters="${cssEscape(key)}"]`)
    chipsWrap.innerHTML = state.annotations
      .filter(a => a.annotator_id !== 'amber' && a[key] != null)
      .map(a => {
        const color = RATER_COLOR[a.annotator_id] || 'bg-slate-400'
        return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900">
          <span class="w-2 h-2 rounded-full ${color}"></span>
          ${escapeHtml(a.annotator_id)}: <span class="font-mono">${a[key].toFixed(2)}</span>
        </span>`
      }).join('')
  })
}

function renderTags() {
  const wrap = $('tags-section')
  // 收集每個 tag 種類的「其他人選了什麼」(union)
  const collect = (field) => {
    const all = new Set()
    state.annotations.filter(a => a.annotator_id !== 'amber').forEach(a => {
      const v = a[field]
      if (Array.isArray(v)) v.forEach(x => all.add(x))
      else if (v) all.add(v)
    })
    return [...all]
  }
  const othersST   = collect('source_type')
  const othersFR   = collect('function_roles')
  const othersLoop = collect('loop_capability').map(String)
  const othersGenre = collect('genre_tag')
  const othersWV   = collect('worldview_tag')
  const othersStyle = collect('style_tag')

  // Amber's genre/worldview/style chip-style selectable（worldview 已改多選）
  state.selectedGenre = new Set(state.amberAnnotation?.genre_tag || [])
  state.selectedWorldview = new Set(state.amberAnnotation?.worldview_tag || [])
  state.selectedStyle = new Set(state.amberAnnotation?.style_tag || [])

  wrap.innerHTML = `
    <div>
      <div class="text-sm font-medium mb-1">Source Type(其他人:${othersST.length ? othersST.join(', ') : '無'})</div>
      <input id="source-types-input" type="text" class="w-full px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded font-mono" placeholder="逗號分隔" value="${escapeAttr([...state.selectedSourceTypes].join(','))}">
    </div>
    <div>
      <div class="text-sm font-medium mb-1">Function Roles(其他人:${othersFR.length ? othersFR.join(', ') : '無'})</div>
      <input id="function-roles-input" type="text" class="w-full px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded font-mono" placeholder="逗號分隔(必至少 1 個)" value="${escapeAttr([...state.selectedFunctionRoles].join(','))}">
    </div>
    <div>
      <div class="text-sm font-medium mb-1">Loop Capability(其他人:${othersLoop.join(', ') || '無'})</div>
      <input id="loop-input" type="text" class="w-full px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded font-mono" placeholder="逗號分隔 0.0 / 0.5 / 1.0" value="${escapeAttr([...state.selectedLoop].join(','))}">
    </div>

    <!-- Phase 12-B:genre / worldview / style 加 chip 編輯 -->
    <div>
      <div class="text-sm font-medium mb-1">Genre tag(多選)</div>
      ${renderOthersChips(othersGenre)}
      <div id="genre-chips" class="flex flex-wrap gap-1 mb-1"></div>
      <div class="flex gap-1">
        <input id="genre-input" list="genre-suggest" type="text" class="flex-1 px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded" placeholder="輸入後按 Enter">
        <datalist id="genre-suggest"></datalist>
        <button type="button" id="genre-add" class="px-2 text-sm bg-slate-200 dark:bg-slate-700 rounded hover:bg-amber-200 dark:hover:bg-amber-900">+</button>
      </div>
    </div>

    <div>
      <div class="text-sm font-medium mb-1">Worldview tag(多選)</div>
      ${renderOthersChips(othersWV)}
      <div id="worldview-chips" class="flex flex-wrap gap-1 mb-1"></div>
      <div class="flex gap-1">
        <input id="worldview-input" list="worldview-suggest" type="text" class="flex-1 px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded" placeholder="輸入後按 Enter">
        <datalist id="worldview-suggest"></datalist>
        <button type="button" id="worldview-add" class="px-2 text-sm bg-slate-200 dark:bg-slate-700 rounded hover:bg-amber-200 dark:hover:bg-amber-900">+</button>
      </div>
    </div>

    <div>
      <div class="text-sm font-medium mb-1">Style tag(多選)</div>
      ${renderOthersChips(othersStyle)}
      <div id="style-chips" class="flex flex-wrap gap-1 mb-1"></div>
      <div class="flex gap-1">
        <input id="style-input" list="style-suggest" type="text" class="flex-1 px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded" placeholder="輸入後按 Enter">
        <datalist id="style-suggest"></datalist>
        <button type="button" id="style-add" class="px-2 text-sm bg-slate-200 dark:bg-slate-700 rounded hover:bg-amber-200 dark:hover:bg-amber-900">+</button>
      </div>
    </div>

    <div>
      <div class="text-sm font-medium mb-1">Notes</div>
      <textarea id="notes-input" class="w-full px-2 py-1 text-sm bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded" rows="2">${escapeHtml(state.notes)}</textarea>
    </div>
  `

  // chip 渲染 + 互動
  refreshChips('genre-chips', state.selectedGenre)
  refreshChips('worldview-chips', state.selectedWorldview)
  refreshChips('style-chips', state.selectedStyle)

  // 從 server 撈 autocomplete suggestions(fail-safe)
  fillSuggestions('genre',     'genre-suggest')
  fillSuggestions('worldview', 'worldview-suggest')
  fillSuggestions('style',     'style-suggest')

  // 加 chip handlers
  $('genre-add').addEventListener('click', () => {
    addChipFromInput($('genre-input'), state.selectedGenre, 'genre-chips')
  })
  $('genre-input').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('genre-add').click() }
  })
  $('worldview-add').addEventListener('click', () => {
    addChipFromInput($('worldview-input'), state.selectedWorldview, 'worldview-chips')
  })
  $('worldview-input').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('worldview-add').click() }
  })
  $('style-add').addEventListener('click', () => {
    addChipFromInput($('style-input'), state.selectedStyle, 'style-chips')
  })
  $('style-input').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('style-add').click() }
  })
}

function renderOthersChips(values) {
  if (!values.length) {
    return '<div class="text-xs text-slate-500 mb-1">(其他人:無)</div>'
  }
  return `<div class="text-xs mb-1">
    <span class="text-slate-500 mr-1">其他人:</span>
    ${values.map(v => `<span class="inline-block px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900 text-slate-600 dark:text-slate-400 mr-1">${escapeHtml(v)}</span>`).join('')}
  </div>`
}

function refreshChips(containerId, set) {
  const wrap = $(containerId)
  wrap.innerHTML = [...set].map(v => `
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-300 text-xs">
      ${escapeHtml(v)}
      <button type="button" data-remove="${escapeAttr(v)}" class="hover:text-rose-600">×</button>
    </span>
  `).join('')
  wrap.querySelectorAll('button[data-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      set.delete(btn.dataset.remove)
      refreshChips(containerId, set)
    })
  })
}

function addChipFromInput(inputEl, set, containerId) {
  const v = inputEl.value.trim()
  if (!v) return
  set.add(v)
  inputEl.value = ''
  refreshChips(containerId, set)
}

async function fillSuggestions(field, datalistId) {
  try {
    const res = await fetch(`/api/tag-suggestions?field=${encodeURIComponent(field)}`)
    if (!res.ok) return
    // API 回 { field, suggestions: string[] }，不是物件陣列
    const { suggestions = [] } = await res.json()
    $(datalistId).innerHTML = suggestions.map(v => `<option value="${escapeAttr(v)}">`).join('')
  } catch {
    // 靜默,autocomplete 不可用就讓 user 手打
  }
}

$('save-btn').addEventListener('click', save)

async function save() {
  $('save-status').textContent = '儲存中…'
  const sourceTypes = parseCommaList($('source-types-input').value)
  const functionRoles = parseCommaList($('function-roles-input').value)
  const loop = parseCommaList($('loop-input').value).map(s => parseFloat(s)).filter(n => !isNaN(n))
  const notes = $('notes-input').value.trim()

  const payload = {
    audio_id: AUDIO_ID,
    annotator_id: 'amber',
    ...state.values,
    loop_capability: loop,
    source_type: sourceTypes,
    function_roles: functionRoles,
    genre_tag: [...(state.selectedGenre || [])],
    worldview_tag: [...(state.selectedWorldview || [])],
    style_tag: [...(state.selectedStyle || [])],
    notes: notes || null,
  }

  try {
    const res = await fetch('/api/annotations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      $('save-status').textContent = `❌ 儲存失敗:${err.detail || '未知錯誤'}`
      return
    }
    const data = await res.json()
    $('save-status').textContent = data.is_complete
      ? '✅ 已儲存(is_complete=true)。回清單可看到此檔可能已掉到 lockable。'
      : '⚠️ 已儲存但 is_complete=false(缺必填欄位)。'
  } catch (err) {
    $('save-status').textContent = `❌ 網路錯誤:${err.message}`
  }
}

function parseCommaList(s) {
  return String(s || '').split(',').map(x => x.trim()).filter(Boolean)
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s) }
function cssEscape(s) { return String(s).replace(/"/g, '\\"') }
