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

function empty(message, hot) {
  return `<div class="empty${hot ? ' error' : ''}">${message}</div>`
}

function renderPending(tab) {
  $(tab).innerHTML = empty('此區資料渲染中')
}

function renderTab(tab) {
  if (renderedTabs.has(tab)) return
  renderPending(`p${tab}`)
  renderedTabs.add(tab)
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
