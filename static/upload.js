// Phase 6 — 音源上傳頁前端
//
// 行為：
//   1. fetch /api/me 確認 admin（401 → /login；非 admin → 跳回 /）
//   2. drag-drop / 檔案選擇 → 加入佇列
//   3. 每個 row 可獨立上傳，或「全部上傳」
//   4. 進度條走 XMLHttpRequest.upload.onprogress
//
// vanilla ES module，無 framework / axios。

const STATE = {
  // queue 是 Map<id, item>，item 形如：
  //   { id, file, status, message, progress, replace, audio }
  queue: new Map(),
  nextId: 1,
}

const SELECTORS = {
  forbidden: '#forbidden-banner',
  main: '#main-content',
  dropZone: '#drop-zone',
  fileInput: '#file-input',
  filePicker: '#file-picker-btn',
  queueList: '#queue-list',
  queueEmpty: '#queue-empty',
  uploadAll: '#upload-all-btn',
}

function $(sel) { return document.querySelector(sel) }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[c])
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// ─── auth gate ─────────────────────────────────────────────

async function gateAdmin() {
  let res
  try {
    res = await fetch('/api/me', { credentials: 'same-origin' })
  } catch (err) {
    console.warn('upload.js fetch /api/me failed', err)
    return false
  }
  if (res.status === 401) {
    const next = encodeURIComponent('/upload')
    window.location.href = `/login?next=${next}`
    return false
  }
  if (!res.ok) {
    console.warn('upload.js /api/me unexpected status', res.status)
    return false
  }
  let user
  try {
    user = await res.json()
  } catch (err) {
    console.warn('upload.js /api/me parse failed', err)
    return false
  }
  if (!user.is_admin) {
    $(SELECTORS.forbidden).classList.remove('hidden')
    setTimeout(() => {
      window.alert('需要 admin 權限才能上傳音源')
      window.location.href = '/'
    }, 800)
    return false
  }
  return true
}

// ─── queue 操作 ────────────────────────────────────────────

function addFiles(files) {
  for (const f of files) {
    const id = `q${STATE.nextId++}`
    STATE.queue.set(id, {
      id,
      file: f,
      status: 'pending',
      message: '',
      progress: 0,
      replace: false,
      audio: null,
    })
  }
  renderQueue()
}

function renderQueue() {
  const list = $(SELECTORS.queueList)
  const empty = $(SELECTORS.queueEmpty)
  const uploadAll = $(SELECTORS.uploadAll)

  if (STATE.queue.size === 0) {
    empty.classList.remove('hidden')
    list.innerHTML = ''
    list.appendChild(empty)
    uploadAll.classList.add('hidden')
    return
  }
  empty.classList.add('hidden')
  uploadAll.classList.remove('hidden')

  const rows = []
  for (const item of STATE.queue.values()) {
    rows.push(renderRow(item))
  }
  list.innerHTML = rows.join('')

  // bind row buttons
  for (const item of STATE.queue.values()) {
    const removeBtn = list.querySelector(`[data-action="remove"][data-id="${item.id}"]`)
    if (removeBtn) {
      removeBtn.addEventListener('click', () => {
        STATE.queue.delete(item.id)
        renderQueue()
      })
    }
    const uploadBtn = list.querySelector(`[data-action="upload"][data-id="${item.id}"]`)
    if (uploadBtn) {
      uploadBtn.addEventListener('click', () => uploadOne(item.id))
    }
    const replaceCheckbox = list.querySelector(`[data-action="replace"][data-id="${item.id}"]`)
    if (replaceCheckbox) {
      replaceCheckbox.addEventListener('change', e => {
        const cur = STATE.queue.get(item.id)
        if (cur) cur.replace = e.target.checked
      })
    }
  }
}

function renderRow(item) {
  const statusBadge = renderStatusBadge(item)
  const progressBar = item.status === 'uploading'
    ? `<div class="h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden mt-2">
         <div class="h-full bg-amber-500" style="width: ${item.progress}%"></div>
       </div>`
    : ''
  const detail = item.audio
    ? `<div class="text-xs text-emerald-700 dark:text-emerald-300 mt-1">
         ${escapeHtml(item.audio.game_name)} — ${escapeHtml(item.audio.game_stage)}
       </div>`
    : ''
  const errorLine = (item.status === 'error' && item.message)
    ? `<div class="text-xs text-red-600 dark:text-red-400 mt-1">${escapeHtml(item.message)}</div>`
    : ''
  const isBusy = item.status === 'uploading'
  const isDone = item.status === 'success'
  return `
    <li class="p-3 flex items-center gap-3" data-row="${item.id}">
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-2">
          <span class="font-mono text-sm truncate" title="${escapeHtml(item.file.name)}">
            ${escapeHtml(item.file.name)}
          </span>
          <span class="text-xs text-slate-500 dark:text-slate-400 shrink-0">${formatBytes(item.file.size)}</span>
        </div>
        ${detail}
        ${errorLine}
        ${progressBar}
      </div>
      <label class="text-xs text-slate-600 dark:text-slate-300 flex items-center gap-1 shrink-0">
        <input type="checkbox" data-action="replace" data-id="${item.id}"
          ${item.replace ? 'checked' : ''} ${isBusy || isDone ? 'disabled' : ''}>
        覆蓋
      </label>
      ${statusBadge}
      <button type="button" data-action="upload" data-id="${item.id}"
        ${isBusy || isDone ? 'disabled' : ''}
        class="px-2.5 py-1 text-xs rounded font-medium
               ${isBusy || isDone
                 ? 'bg-slate-200 text-slate-400 dark:bg-slate-700 dark:text-slate-500 cursor-not-allowed'
                 : 'bg-amber-500 hover:bg-amber-600 text-slate-900'}">
        上傳
      </button>
      <button type="button" data-action="remove" data-id="${item.id}"
        ${isBusy ? 'disabled' : ''}
        class="text-xs text-slate-500 hover:text-red-600 px-2 ${isBusy ? 'opacity-30 cursor-not-allowed' : ''}">
        移除
      </button>
    </li>
  `
}

function renderStatusBadge(item) {
  if (item.status === 'success') {
    return '<span class="text-emerald-600 dark:text-emerald-400 text-sm shrink-0" title="上傳成功">✓</span>'
  }
  if (item.status === 'error') {
    return '<span class="text-red-600 dark:text-red-400 text-sm shrink-0" title="上傳失敗">✗</span>'
  }
  if (item.status === 'uploading') {
    return `<span class="text-amber-600 dark:text-amber-400 text-xs shrink-0">${item.progress}%</span>`
  }
  return '<span class="text-slate-400 dark:text-slate-500 text-sm shrink-0" title="待上傳">○</span>'
}

// ─── 上傳 ──────────────────────────────────────────────────

function uploadOne(id) {
  const item = STATE.queue.get(id)
  if (!item) return
  if (item.status === 'uploading' || item.status === 'success') return
  return new Promise(resolve => {
    item.status = 'uploading'
    item.progress = 0
    item.message = ''
    item.audio = null
    renderQueue()

    const form = new FormData()
    form.append('file', item.file, item.file.name)
    const url = item.replace ? '/api/audio/upload?replace=true' : '/api/audio/upload'

    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.responseType = 'json'
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return
      const cur = STATE.queue.get(id)
      if (!cur) return
      cur.progress = Math.round((e.loaded / e.total) * 100)
      renderQueue()
    }
    xhr.onload = () => {
      const cur = STATE.queue.get(id)
      if (!cur) return resolve()
      if (xhr.status >= 200 && xhr.status < 300) {
        cur.status = 'success'
        cur.progress = 100
        cur.audio = xhr.response || null
        cur.message = ''
      } else {
        cur.status = 'error'
        const detail = (xhr.response && xhr.response.detail) || `HTTP ${xhr.status}`
        cur.message = String(detail)
      }
      renderQueue()
      resolve()
    }
    xhr.onerror = () => {
      const cur = STATE.queue.get(id)
      if (!cur) return resolve()
      cur.status = 'error'
      cur.message = '網路錯誤，請稍後重試'
      renderQueue()
      resolve()
    }
    xhr.send(form)
  })
}

async function uploadAll() {
  // 序列化（避免一次塞 N 個大檔讓 server 壓力過大）
  for (const item of STATE.queue.values()) {
    if (item.status === 'pending' || item.status === 'error') {
      await uploadOne(item.id)
    }
  }
}

// ─── 拖放 / 檔案選擇 ───────────────────────────────────────

function bindDropZone() {
  const zone = $(SELECTORS.dropZone)
  const input = $(SELECTORS.fileInput)
  const picker = $(SELECTORS.filePicker)

  zone.addEventListener('click', e => {
    // 避免點到「選擇檔案」按鈕時被父元素再開一次 file picker
    if (e.target.closest('#file-picker-btn')) return
    input.click()
  })
  picker.addEventListener('click', e => {
    e.stopPropagation()
    input.click()
  })
  input.addEventListener('change', () => {
    if (input.files && input.files.length > 0) {
      addFiles(input.files)
      input.value = ''
    }
  })

  const setHover = on => {
    zone.classList.toggle('border-amber-500', on)
    zone.classList.toggle('bg-amber-50', on)
    zone.classList.toggle('dark:bg-amber-900/20', on)
  }

  zone.addEventListener('dragover', e => {
    e.preventDefault()
    setHover(true)
  })
  zone.addEventListener('dragleave', e => {
    e.preventDefault()
    setHover(false)
  })
  zone.addEventListener('drop', e => {
    e.preventDefault()
    setHover(false)
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files)
    }
  })
}

function bindUploadAll() {
  $(SELECTORS.uploadAll).addEventListener('click', () => uploadAll())
}

// ─── boot ──────────────────────────────────────────────────

async function boot() {
  const ok = await gateAdmin()
  if (!ok) return
  $(SELECTORS.main).classList.remove('hidden')
  bindDropZone()
  bindUploadAll()
  renderQueue()
}

boot()
