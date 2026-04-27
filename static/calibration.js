// 校準首頁：抓 ?annotator= 或 input 取 id，fetch /api/calibration/queue 渲染清單。
// 點任一檔進 /calibration/{audio_id}?annotator=... — annotate.html 會自動進入 calibration mode。

const $ = id => document.getElementById(id)
const annotatorInput = $('annotator-input')
const queueList = $('queue-list')
const statusEl = $('status')

const qs = new URLSearchParams(window.location.search)
const initialAnnotator = qs.get('annotator') || ''

if (initialAnnotator) {
  annotatorInput.value = initialAnnotator
  loadQueue(initialAnnotator)
}

annotatorInput.addEventListener('change', () => {
  const id = annotatorInput.value.trim()
  if (!id) {
    statusEl.textContent = '輸入你的 id 後載入清單…'
    queueList.innerHTML = ''
    return
  }
  // 同步到 URL，方便分享 / 重新整理
  const url = new URL(window.location.href)
  url.searchParams.set('annotator', id)
  window.history.replaceState({}, '', url)
  loadQueue(id)
})
annotatorInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') annotatorInput.dispatchEvent(new Event('change'))
})

async function loadQueue(annotator) {
  statusEl.textContent = '載入中…'
  queueList.innerHTML = ''
  try {
    const res = await fetch(`/api/calibration/queue?annotator=${encodeURIComponent(annotator)}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const items = await res.json()
    if (!items.length) {
      if (annotator === 'amber') {
        statusEl.textContent = '你就是參考標註員 (amber)，無需校準。'
      } else {
        statusEl.textContent = '🎉 沒有待校準的檔案！你已標完 amber 標過的全部音檔。'
      }
      return
    }
    statusEl.textContent = `共 ${items.length} 個檔案待校準。`
    queueList.innerHTML = items.map(it => `
      <tr class="border-t border-slate-200 dark:border-slate-700/60 hover:bg-slate-100 dark:hover:bg-slate-700/40 cursor-pointer"
          onclick="window.location.href='/calibration/${encodeURIComponent(it.id)}?annotator=${encodeURIComponent(annotator)}'">
        <td class="p-3">
          <div class="font-medium">${escapeHtml(it.game_name)}</div>
          <div class="text-xs text-slate-500 dark:text-slate-400">${escapeHtml(it.game_stage)}</div>
        </td>
        <td class="p-3 text-slate-600 dark:text-slate-400 text-xs">
          ${it.is_brand_theme ? '品牌主題曲' : 'BGM'}
        </td>
        <td class="p-3 text-right text-slate-500 dark:text-slate-500 text-xs">
          ${it.duration_sec != null ? it.duration_sec.toFixed(1) + 's' : '—'}
        </td>
      </tr>
    `).join('')
  } catch (err) {
    statusEl.textContent = `載入失敗：${err.message}`
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
