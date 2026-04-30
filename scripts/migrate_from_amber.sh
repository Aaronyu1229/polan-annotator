#!/usr/bin/env bash
# 切換日：把 Amber Mac 上的本機 DB + 音檔搬到 VPS
#
# 用法：
#     bash scripts/migrate_from_amber.sh <amber-ssh-host> [<vps-host>]
#
# 範例：
#     bash scripts/migrate_from_amber.sh amber@192.168.1.42
#     bash scripts/migrate_from_amber.sh amber@amber-mac.tail-scale.ts.net root@68.183.232.52
#
# 環境變數覆寫：
#     AMBER_REPO   Amber Mac 上 polan-annotator repo 的位置（預設 ~/Desktop/polan-annotator）
#
# 前提：
#     1. Amber 已收工、按過存檔、關掉本地 server
#     2. Aaron 對 amber-ssh-host 跟 vps-host 都有 SSH 公鑰登入權限
#     3. VPS 上 /opt/polan-annotator 已 deploy 好（docker compose up -d 跑著）

set -euo pipefail

# ───── 顏色 / 印 helper ─────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'
step() { echo ""; echo -e "${BLUE}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# ───── 參數解析 ─────
usage() {
    cat <<EOF
用法：
    bash scripts/migrate_from_amber.sh <amber-ssh-host> [<vps-host>]

參數：
    amber-ssh-host   必填，例如 amber@192.168.1.42 或 amber@amber-mac.tail-scale.ts.net
    vps-host         選填，預設 root@68.183.232.52

環境變數：
    AMBER_REPO       Amber Mac 上 repo 位置，預設 ~/Desktop/polan-annotator
EOF
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage
    exit 1
fi

AMBER_HOST="$1"
VPS_HOST="${2:-root@68.183.232.52}"
AMBER_REPO="${AMBER_REPO:-~/Desktop/polan-annotator}"
VPS_DATA_DIR="/opt/polan-annotator/data"
APP_URL="https://annotate.dolcenforte.com"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
VPS_BACKUP_PATH="${VPS_DATA_DIR}/annotations.db.pre-migration.${TIMESTAMP}"

# ───── 暫存目錄 + cleanup trap ─────
TMP_DIR="$(mktemp -d -t polan-migrate-XXXXXX)"
cleanup() {
    local exit_code=$?
    if [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
    if [ "$exit_code" -ne 0 ]; then
        echo ""
        warn "腳本中斷（exit code $exit_code）。VPS 應該維持原狀；若 step 4/5 已開始，備份在 ${VPS_BACKUP_PATH}"
    fi
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ───── 確認前置 ─────
echo ""
echo -e "${YELLOW}⚠️  即將從 ${AMBER_HOST} 抓資料、覆蓋 VPS ${VPS_HOST} 上的 data/。${NC}"
echo "   Amber repo 位置：${AMBER_REPO}（可用 AMBER_REPO 環境變數覆寫）"
echo "   VPS data 目錄：  ${VPS_DATA_DIR}"
echo "   暫存目錄：       ${TMP_DIR}"
echo ""
echo "請確認 Amber 已收工、按存檔、關掉本地 server。"
read -p "繼續嗎? (y/N) " -n 1 -r
echo ""
if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    echo "取消。"
    exit 0
fi

# ───── 1/7 確認 Amber Mac server 已停 ─────
step "[步驟 1/7] 確認 Amber Mac 已停止 server..."
PGREP_OUT="$(ssh "$AMBER_HOST" "pgrep -f 'uvicorn.*src.main:app' || echo 'STOPPED'" 2>&1)" || \
    fail "SSH 連 ${AMBER_HOST} 失敗。檢查：ssh ${AMBER_HOST} 'echo ok'"
if [[ "$PGREP_OUT" != *"STOPPED"* ]]; then
    fail "Amber Mac 上 uvicorn 還在跑（PID: $PGREP_OUT）。請先：
    ssh ${AMBER_HOST}
    # 然後在 server 視窗按 Ctrl+C，或：
    pkill -f 'uvicorn.*src.main:app'
然後重跑本腳本。"
fi
ok "Amber Mac server 已停"

# ───── 2/7 rsync DB 到本機暫存 ─────
step "[步驟 2/7] 從 Amber Mac 複製 DB 到本機暫存..."
if ! rsync -avz --progress "${AMBER_HOST}:${AMBER_REPO}/data/annotations.db" "${TMP_DIR}/annotations.db"; then
    fail "rsync DB 失敗。檢查：
    ssh ${AMBER_HOST} 'ls -l ${AMBER_REPO}/data/annotations.db'
若 repo 不在預設位置，重跑時加環境變數：AMBER_REPO=/path/to/repo bash scripts/migrate_from_amber.sh $AMBER_HOST $VPS_HOST"
fi
[ -f "${TMP_DIR}/annotations.db" ] || fail "rsync 看似成功但 ${TMP_DIR}/annotations.db 不存在"
ok "DB 已下載到 ${TMP_DIR}/annotations.db"

# 算 Amber DB annotation 筆數（before count）
if command -v sqlite3 >/dev/null 2>&1; then
    AMBER_COUNT="$(sqlite3 "${TMP_DIR}/annotations.db" 'SELECT COUNT(*) FROM annotation' 2>/dev/null || echo "?")"
else
    warn "本機沒裝 sqlite3，跳過 annotation 計數"
    AMBER_COUNT="?"
fi
ok "Amber DB annotation 筆數：${AMBER_COUNT}"

# ───── 3/7 rsync audio/ 到本機暫存 ─────
step "[步驟 3/7] 從 Amber Mac 複製 audio/ 到本機暫存..."
mkdir -p "${TMP_DIR}/audio"
if ! rsync -avz --progress "${AMBER_HOST}:${AMBER_REPO}/data/audio/" "${TMP_DIR}/audio/"; then
    fail "rsync audio 失敗。檢查：
    ssh ${AMBER_HOST} 'ls ${AMBER_REPO}/data/audio/ | head'"
fi
AMBER_AUDIO_COUNT="$(find "${TMP_DIR}/audio" -type f | wc -l | tr -d ' ')"
ok "音檔已下載：${AMBER_AUDIO_COUNT} 個檔"

# ───── 4/7 上傳 DB 到 VPS（先備份既有 DB） ─────
step "[步驟 4/7] 上傳 DB 到 VPS（先備份既有 DB）..."
if ! ssh "$VPS_HOST" "test -f ${VPS_DATA_DIR}/annotations.db && cp ${VPS_DATA_DIR}/annotations.db ${VPS_BACKUP_PATH} || echo 'no existing DB to back up'"; then
    fail "SSH 連 ${VPS_HOST} 或備份既有 DB 失敗。檢查：
    ssh ${VPS_HOST} 'ls -l ${VPS_DATA_DIR}/'"
fi
ok "VPS 既有 DB 已備份到 ${VPS_BACKUP_PATH}（若原本沒 DB 則跳過）"

if ! scp "${TMP_DIR}/annotations.db" "${VPS_HOST}:${VPS_DATA_DIR}/annotations.db"; then
    fail "scp DB 上 VPS 失敗。手動還原備份：
    ssh ${VPS_HOST} 'cp ${VPS_BACKUP_PATH} ${VPS_DATA_DIR}/annotations.db'"
fi
ok "DB 已上傳到 VPS"

# ───── 5/7 上傳 audio/ 到 VPS ─────
step "[步驟 5/7] 上傳 audio/ 到 VPS..."
# 確保 VPS 上 audio/ 目錄存在
ssh "$VPS_HOST" "mkdir -p ${VPS_DATA_DIR}/audio" || fail "VPS 建立 audio/ 目錄失敗"
if ! rsync -avz --progress "${TMP_DIR}/audio/" "${VPS_HOST}:${VPS_DATA_DIR}/audio/"; then
    fail "rsync audio 上 VPS 失敗。檢查：
    ssh ${VPS_HOST} 'df -h ${VPS_DATA_DIR}'（磁碟可能滿了）"
fi
ok "音檔已上傳到 VPS"

# ───── 6/7 重啟 VPS 上的 container ─────
step "[步驟 6/7] 重啟 VPS 上的 container..."
if ! ssh "$VPS_HOST" "cd /opt/polan-annotator && docker compose restart app"; then
    fail "docker compose restart 失敗。檢查：
    ssh ${VPS_HOST} 'cd /opt/polan-annotator && docker compose ps && docker compose logs --tail=50 app'"
fi
ok "container 已 restart，等 5 秒讓 app 起好..."
sleep 5

# ───── 7/7 健康檢查 ─────
step "[步驟 7/7] 健康檢查..."
HEALTH_OK=0
for attempt in 1 2 3; do
    if curl -fsS "${APP_URL}/api/dimensions" > /dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    warn "第 ${attempt} 次健康檢查失敗，5 秒後重試..."
    sleep 5
done
if [ "$HEALTH_OK" -ne 1 ]; then
    fail "健康檢查 3 次都失敗：${APP_URL}/api/dimensions
看 container log：
    ssh ${VPS_HOST} 'cd /opt/polan-annotator && docker compose logs --tail=100 app'
若需手動還原 DB：
    ssh ${VPS_HOST} 'cp ${VPS_BACKUP_PATH} ${VPS_DATA_DIR}/annotations.db && cd /opt/polan-annotator && docker compose restart app'"
fi
ok "健康檢查通過（${APP_URL}/api/dimensions）"

# ───── 算 VPS DB 筆數（after count） ─────
VPS_COUNT="$(ssh "$VPS_HOST" "sqlite3 ${VPS_DATA_DIR}/annotations.db 'SELECT COUNT(*) FROM annotation'" 2>/dev/null || echo "?")"
VPS_AUDIO_COUNT="$(ssh "$VPS_HOST" "find ${VPS_DATA_DIR}/audio -type f | wc -l | tr -d ' '" 2>/dev/null || echo "?")"

# ───── 完成輸出 ─────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✅ 切換完成${NC}"
echo ""
echo "Amber 端 DB:    ${AMBER_COUNT} → VPS DB:  ${VPS_COUNT} 筆 annotation"
echo "音檔:           ${AMBER_AUDIO_COUNT} → ${VPS_AUDIO_COUNT} 個"
echo "VPS 備份位置:   ${VPS_BACKUP_PATH}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "給 Amber 的 LINE 訊息（複製貼上）："
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat <<'EOF'
嗨 Amber！標註工具升級雲端版了，從今天起：

👉 改用這個網址：https://annotate.dolcenforte.com
👉 用你的 Google 帳號（polanmusic2025@gmail.com）登入
👉 桌面那個「啟動珀瀾標註工具」之後不用點了，可以丟到垃圾桶
👉 所有員工也是用各自的 Gmail 登入（我已加白名單）

你之前標的資料都搬上去了，繼續從上次的進度往下標。
有任何問題隨時 LINE 我。
EOF
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
