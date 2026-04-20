// Phase 1 音檔清單頁：fetch /api/audio 後渲染表格。

const STATUS = document.getElementById('status')
const META = document.getElementById('meta')
const TBODY = document.getElementById('audio-list')

const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || 'amber'

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[c])
}

function renderRow(item) {
  const stage = item.game_stage ? escapeHtml(item.game_stage) : '<span class="text-slate-300">—</span>'
  const tag = item.is_brand_theme
    ? '<span class="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 rounded">品牌主題</span>'
    : '<span class="text-xs px-2 py-0.5 bg-slate-100 text-slate-500 rounded">遊戲音樂</span>'
  const href = `/annotate?audio_id=${encodeURIComponent(item.id)}&annotator=${encodeURIComponent(annotator)}`
  return `
    <tr class="border-t border-slate-100">
      <td class="p-3">${escapeHtml(item.game_name)}</td>
      <td class="p-3 text-slate-600">${stage}</td>
      <td class="p-3">${tag}</td>
      <td class="p-3 text-right">
        <a href="${href}" class="text-blue-600 hover:underline">標註</a>
      </td>
    </tr>
  `
}

async function load() {
  try {
    const res = await fetch('/api/audio')
    if (!res.ok) {
      throw new Error(`API 回傳 HTTP ${res.status}`)
    }
    const items = await res.json()
    TBODY.innerHTML = items.map(renderRow).join('')
    STATUS.textContent = items.length === 0
      ? '尚未掃描到任何音檔。請確認 data/audio/ 下有 .wav 檔案，並重啟 server。'
      : `共 ${items.length} 個音檔`
    META.textContent = `標註者：${annotator}`
  } catch (err) {
    STATUS.textContent = `載入失敗：${err.message}`
    STATUS.className = 'text-red-600 text-sm mb-4'
  }
}

load()
