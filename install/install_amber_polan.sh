#!/usr/bin/env bash
# 珀瀾聲音標註工具 — Amber Mac 一鍵安裝腳本
# 執行：chmod +x ~/Downloads/install_amber_polan.sh && ~/Downloads/install_amber_polan.sh
#
# 做 9 件事：
#   1. 確認 macOS + Xcode CLT
#   2. 確認 polan-data.tar.gz 在 ~/Downloads
#   3. 裝 uv（已裝跳過）
#   4. clone repo（已存在跳過）
#   5. 解壓 tarball 到 data/
#   6. 建 venv + 裝依賴
#   7. 跑 pytest 驗證
#   8. 裝桌面 icon
#   9. 印「下一步：雙擊桌面 icon」

set -e   # 任一步失敗立刻停

REPO_URL="https://github.com/Aaronyu1229/polan-annotator.git"
PROJECT_DIR="$HOME/Desktop/polan-annotator"
TARBALL="$HOME/Downloads/polan-data.tar.gz"

# ── 顏色 ─────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

step() { echo ""; echo -e "${BLUE}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# ── 1. 環境 ─────────────────────────
step "1/9 確認 macOS + Xcode Command Line Tools"
sw_vers | head -2
if ! xcode-select -p >/dev/null 2>&1; then
    fail "Xcode CLT 未裝。請先跑：xcode-select --install，等 GUI 安裝完再重跑此腳本。"
fi
ok "Xcode CLT OK"

# ── 2. tarball 在不在 ────────────────
step "2/9 確認 polan-data.tar.gz 在 ~/Downloads"
if [ ! -f "$TARBALL" ]; then
    fail "找不到 $TARBALL — 請先 AirDrop 接收 Aaron 傳來的 polan-data.tar.gz"
fi
SIZE=$(du -h "$TARBALL" | awk '{print $1}')
ok "tarball 存在（$SIZE）"

# ── 3. uv ─────────────────────────
step "3/9 安裝 uv（Python 套件管理）"
if command -v uv >/dev/null 2>&1; then
    ok "uv 已裝：$(uv --version)"
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 立刻載入 uv 到當前 shell
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        fail "uv 安裝失敗 — 請手動跑：curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
    ok "uv 裝好：$(uv --version)"
fi

# ── 4. clone repo ─────────────────
step "4/9 Clone GitHub repo 到 ~/Desktop/polan-annotator"
if [ -d "$PROJECT_DIR/.git" ]; then
    warn "$PROJECT_DIR 已存在，跳過 clone（執行 git pull 拿最新）"
    cd "$PROJECT_DIR"
    git pull --rebase
else
    cd "$HOME/Desktop"
    git clone "$REPO_URL"
    cd "$PROJECT_DIR"
fi
LATEST=$(git log --oneline -1)
ok "最新 commit：$LATEST"

# ── 5. 解壓 tarball ───────────────
step "5/9 解壓 polan-data.tar.gz（音檔 + DB）"
cd "$PROJECT_DIR"
tar -xzf "$TARBALL"
AUDIO_COUNT=$(ls data/audio/ 2>/dev/null | wc -l | tr -d ' ')
ANN_COUNT=$(sqlite3 data/annotations.db "SELECT COUNT(*) FROM annotation;" 2>/dev/null || echo 0)
if [ "$AUDIO_COUNT" -ne 33 ]; then
    fail "音檔數量不對：$AUDIO_COUNT（期望 33）— tarball 可能損壞"
fi
ok "音檔 $AUDIO_COUNT 個，既有標註 $ANN_COUNT 筆"

# ── 6. venv + 依賴 ────────────────
step "6/9 建 venv + 裝依賴（首次裝 numpy/librosa 約 1-3 分鐘）"
uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e .
ok "依賴裝完"

# ── 7. pytest ─────────────────────
step "7/9 跑 pytest 驗證程式正常"
if pytest -q 2>&1 | tail -1 | grep -q "passed"; then
    PYTEST_LINE=$(pytest -q 2>&1 | tail -1)
    ok "$PYTEST_LINE"
else
    warn "pytest 似乎有失敗 — 請看上面紅字"
    pytest -q 2>&1 | tail -10
fi

# ── 8. 桌面 icon ──────────────────
step "8/9 安裝桌面 icon"
bash scripts/install_desktop_shortcut.sh
ok "桌面 icon 已建立"

# ── 9. 完成 ───────────────────────
step "9/9 完成"
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ ALL DONE${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "下一步："
echo "  1. 雙擊桌面「啟動珀瀾標註工具」icon"
echo "  2. 第一次會跳「無法驗證開發者」 → 系統設定 → 隱私權與安全性 → 強制打開"
echo "  3. 再雙擊一次 → 跳黑視窗 + 瀏覽器自動開"
echo ""
echo "Project: $PROJECT_DIR"
echo "DB:      $PROJECT_DIR/data/annotations.db"
echo ""
