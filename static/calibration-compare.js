// 校準比對頁：抓 self + amber 對同一 audio 的 annotation，畫雷達圖 + 差距表。
// loop_capability 是 multi_discrete，比對時略過（只用 9 個連續維度）。

const $ = id => document.getElementById(id)
const pathParts = window.location.pathname.split('/').filter(Boolean)
// /calibration/compare/{audio_id}
const AUDIO_ID = pathParts[2]
const qs = new URLSearchParams(window.location.search)
const ANNOTATOR = qs.get('annotator') || 'guest'

// 9 個連續維度（loop_capability 是 multi_discrete，跳過）
const COMPARE_DIMS = [
  ['valence',           'Valence 情緒正負向'],
  ['arousal',           'Arousal 喚醒程度'],
  ['emotional_warmth',  'Emotional Warmth 情緒溫度'],
  ['tension_direction', 'Tension Direction 張力方向'],
  ['temporal_position', 'Temporal Position 時序位置'],
  ['event_significance','Event Significance 事件重要性'],
  ['tonal_noise_ratio', 'Tonal-Noise Ratio 樂音/噪音比'],
  ['spectral_density',  'Spectral Density 聲音密度'],
  ['world_immersion',   'World Immersion 世界沉浸感'],
]

const backLink = $('back-link')
const backToQueue = $('back-to-queue')
const queueHref = `/calibration?annotator=${encodeURIComponent(ANNOTATOR)}`
backLink.href = queueHref
backToQueue.href = queueHref

const nextBtn = $('next-btn')

loadAndCompare()

async function loadAndCompare() {
  try {
    const [audioRes, refRes] = await Promise.all([
      fetch(`/api/audio/${encodeURIComponent(AUDIO_ID)}?annotator=${encodeURIComponent(ANNOTATOR)}`),
      fetch(`/api/calibration/reference/${encodeURIComponent(AUDIO_ID)}`),
    ])
    if (!audioRes.ok) throw new Error(`audio fetch ${audioRes.status}`)
    if (!refRes.ok) {
      const err = await refRes.json().catch(() => ({ detail: 'reference fetch failed' }))
      throw new Error(err.detail || `reference fetch ${refRes.status}`)
    }
    const audio = await audioRes.json()
    const reference = await refRes.json()
    const self = audio.existing_annotation
    if (!self) {
      throw new Error('找不到你對此音檔的標註，請先回去標。')
    }

    renderHeader(audio)
    renderRadar(self, reference)
    renderDiffTable(self, reference)
    renderSummary(self, reference)
  } catch (err) {
    showError(err.message)
  }
}

function renderHeader(audio) {
  $('audio-title').textContent = `${audio.game_name} — ${audio.game_stage}`
  const parts = []
  if (audio.duration_sec != null) parts.push(`${audio.duration_sec.toFixed(1)}s`)
  if (audio.bpm != null) parts.push(`BPM ${Math.round(audio.bpm)}`)
  $('audio-meta').textContent = parts.join(' · ')
}

function getDiffs(self, reference) {
  return COMPARE_DIMS.map(([key, label]) => {
    const s = self[key]
    const r = reference[key]
    const diff = (s != null && r != null) ? (s - r) : null
    return { key, label, self: s, reference: r, diff }
  }).filter(d => d.diff !== null)
}

function renderSummary(self, reference) {
  const diffs = getDiffs(self, reference)
  const absDiffs = diffs.map(d => Math.abs(d.diff))
  const mae = absDiffs.length
    ? (absDiffs.reduce((a, b) => a + b, 0) / absDiffs.length)
    : 0
  $('mae-value').textContent = mae.toFixed(3)

  if (diffs.length) {
    const maxDiff = diffs.slice().sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))[0]
    $('max-diff-dim').textContent = maxDiff.label
    const arrow = maxDiff.diff > 0 ? '▲ 你比 amber 高' : '▼ 你比 amber 低'
    $('max-diff-value').textContent = `${arrow} ${Math.abs(maxDiff.diff).toFixed(3)}`
  }
  $('dim-count').textContent = String(diffs.length)
}

function renderRadar(self, reference) {
  const labels = COMPARE_DIMS.map(([_, label]) => label.split(' ')[0])  // 顯示英文 short name
  const selfData = COMPARE_DIMS.map(([key]) => self[key] ?? 0)
  const refData = COMPARE_DIMS.map(([key]) => reference[key] ?? 0)

  // eslint-disable-next-line no-undef
  new Chart($('radar-chart'), {
    type: 'radar',
    data: {
      labels,
      datasets: [
        {
          label: `你 (${ANNOTATOR})`,
          data: selfData,
          backgroundColor: 'rgba(245, 158, 11, 0.2)',
          borderColor: 'rgb(245, 158, 11)',
          pointBackgroundColor: 'rgb(245, 158, 11)',
          borderWidth: 2,
        },
        {
          label: 'amber',
          data: refData,
          backgroundColor: 'rgba(6, 182, 212, 0.2)',
          borderColor: 'rgb(6, 182, 212)',
          pointBackgroundColor: 'rgb(6, 182, 212)',
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      scales: {
        r: {
          min: 0,
          max: 1,
          ticks: { stepSize: 0.2 },
          pointLabels: { font: { size: 11 } },
        },
      },
      plugins: {
        legend: { position: 'top' },
      },
    },
  })
}

function renderDiffTable(self, reference) {
  const diffs = getDiffs(self, reference)
    .slice()
    .sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))
  $('diff-table').innerHTML = diffs.map(d => {
    const arrow = d.diff > 0 ? '<span class="text-amber-600 dark:text-amber-400">▲</span>'
                : d.diff < 0 ? '<span class="text-cyan-600 dark:text-cyan-400">▼</span>'
                : '<span class="text-slate-400">＝</span>'
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700/60">
        <td class="p-3">${escapeHtml(d.label)}</td>
        <td class="p-3 text-right font-mono text-amber-600 dark:text-amber-400">${d.self.toFixed(2)}</td>
        <td class="p-3 text-right font-mono text-cyan-600 dark:text-cyan-400">${d.reference.toFixed(2)}</td>
        <td class="p-3 text-right font-mono">${arrow} ${Math.abs(d.diff).toFixed(3)}</td>
      </tr>
    `
  }).join('')
}

nextBtn.addEventListener('click', async () => {
  try {
    const res = await fetch(`/api/calibration/queue?annotator=${encodeURIComponent(ANNOTATOR)}`)
    const items = await res.json()
    if (!items.length) {
      window.location.href = queueHref
      return
    }
    const next = items[0]
    window.location.href = `/calibration/${encodeURIComponent(next.id)}?annotator=${encodeURIComponent(ANNOTATOR)}`
  } catch (err) {
    showError(`找下一個失敗：${err.message}`)
  }
})

function showError(msg) {
  const el = $('error-msg')
  el.textContent = msg
  el.classList.remove('hidden')
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
