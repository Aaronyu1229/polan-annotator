#!/bin/bash
# 珀瀾聲音標註工具 — 一鍵啟動
# 雙擊此檔案即可啟動 server 並自動開瀏覽器。
# 此檔放在 polan-annotator/scripts/ 裡；桌面上的是 symlink。
# 腳本會自動解 symlink 找到真正 project 位置，Amber 搬 project 時不用改腳本。

set -u  # 未定義變數視為錯誤；不加 -e 因為要自己 handle

# ========== 定位 project 目錄（含 symlink 解析） ==========
SCRIPT_ARG="$0"
if command -v python3 >/dev/null 2>&1; then
    SCRIPT_PATH="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SCRIPT_ARG")"
elif [ -x /usr/bin/python3 ]; then
    SCRIPT_PATH="$(/usr/bin/python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SCRIPT_ARG")"
else
    # 沒 python3 — 假設非 symlink、$0 是真實路徑
    SCRIPT_PATH="$SCRIPT_ARG"
fi

SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ========== 驗證 project 結構 ==========
if [ ! -f "$PROJECT_DIR/src/main.py" ]; then
    echo "❌ 找不到 project（src/main.py 不在 ${PROJECT_DIR}）"
    echo ""
    echo "可能原因：project 被搬走了。請 Aaron 在新位置重跑一次："
    echo "    bash scripts/install_desktop_shortcut.sh"
    echo "然後再雙擊桌面的捷徑。"
    echo ""
    read -n 1 -s -r -p "按任意鍵關閉視窗..."
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    echo "❌ 虛擬環境未建立（$PROJECT_DIR/.venv 不存在）"
    echo ""
    echo "請聯絡 Aaron。他需要到 project 目錄跑："
    echo "    uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'"
    echo ""
    read -n 1 -s -r -p "按任意鍵關閉視窗..."
    exit 1
fi

cd "$PROJECT_DIR" || {
    echo "❌ 無法進入 $PROJECT_DIR"
    read -n 1 -s -r -p "按任意鍵關閉視窗..."
    exit 1
}

# ========== 啟動訊息 ==========
echo "🎵 珀瀾聲音標註工具"
echo "=================="
echo "專案位置：$PROJECT_DIR"
echo ""

# Port 8000 檢查（R2）— 若已佔用仍繼續，但告知使用者
if lsof -ti:8000 >/dev/null 2>&1; then
    echo "⚠️  Port 8000 好像已經有 server 在跑。"
    echo "    我會直接開瀏覽器讓你看看 — 若已經能用就不用管這個 terminal；"
    echo "    若不能用，關閉此視窗跟先前的視窗後再雙擊一次。"
    echo ""
fi

source .venv/bin/activate

# 背景 3 秒後自動開瀏覽器（等 uvicorn 起好）
(sleep 3 && open "http://localhost:8000/?annotator=amber") &

echo "✓ Server 啟動中，瀏覽器 3 秒後會自動打開"
echo ""
echo "⚠️  不要關這個視窗！關了 server 會停。"
echo "⚠️  結束工作時按 Ctrl+C 再關視窗。"
echo ""

# 前景跑 uvicorn — Ctrl+C 可終止
uvicorn src.main:app --reload

# server 正常結束（Ctrl+C）後的訊息
echo ""
echo "Server 已關閉。可以關視窗了。"
read -n 1 -s -r -p "按任意鍵關閉視窗..."
