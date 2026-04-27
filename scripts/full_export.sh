#!/usr/bin/env bash
# 一鍵打包資料集：warm cache → 啟 server → export 3 endpoints → validate → 寫 exports/<日期>/
#
# 用法：
#     bash scripts/full_export.sh                  # 寫到 exports/<今天日期>/
#     bash scripts/full_export.sh --snapshot       # 寫到 exports/snapshot-<今天日期>/（會被 git 追）
#     bash scripts/full_export.sh --output /path   # 自訂目錄
#
# 自動偵測 server 是否已開：沒開的話幫你啟（背景 + 結束時 kill）。

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ── 顏色 ─────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'
step() { echo ""; echo -e "${BLUE}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# ── 解析參數 ─────
TODAY=$(date +%Y-%m-%d)
USE_SNAPSHOT=0
CUSTOM_OUT=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --snapshot) USE_SNAPSHOT=1 ;;
        --output) shift; CUSTOM_OUT="$1" ;;
        *) fail "未知參數：$1" ;;
    esac
    shift
done

if [ -n "$CUSTOM_OUT" ]; then
    OUT_DIR="$CUSTOM_OUT"
elif [ "$USE_SNAPSHOT" -eq 1 ]; then
    OUT_DIR="$PROJECT_DIR/exports/snapshot-$TODAY"
else
    OUT_DIR="$PROJECT_DIR/exports/$TODAY"
fi

# ── 確認 venv ────
step "0/5 確認 venv + 依賴"
if [ ! -f .venv/bin/activate ]; then
    fail ".venv 不存在，請先 uv venv && uv pip install -e ."
fi
# shellcheck disable=SC1091
source .venv/bin/activate
ok "venv ready"

# ── 1. warm cache ────
step "1/5 預熱 librosa cache（讓 audio_metadata 齊全）"
python scripts/warm_audio_cache.py

# ── 2. 確認 server（沒開就啟） ────
step "2/5 確認 server 在 port 8000 跑"
SERVER_PID=""
SHOULD_KILL=0
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ 2>/dev/null | grep -q "200"; then
    ok "Server 已在跑（沿用既有）"
else
    warn "Server 未開，啟一個臨時 server（結束時自動關）"
    nohup uvicorn src.main:app --host 127.0.0.1 --port 8000 \
        > /tmp/full_export-uvicorn.log 2>&1 &
    SERVER_PID=$!
    SHOULD_KILL=1
    sleep 5
    if ! curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ | grep -q "200"; then
        fail "Server 啟動失敗，看 /tmp/full_export-uvicorn.log"
    fi
    ok "Server 啟動成功（PID $SERVER_PID）"
fi

# 結束時關掉臨時 server
cleanup() {
    if [ "$SHOULD_KILL" -eq 1 ] && [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        echo ""
        echo "[cleanup] 已關閉臨時 server PID $SERVER_PID"
    fi
}
trap cleanup EXIT

# ── 3. export 3 個 endpoint ────
step "3/5 匯出到 $OUT_DIR"
mkdir -p "$OUT_DIR"
curl -fsS http://127.0.0.1:8000/api/export/dataset.json > "$OUT_DIR/dataset.json"
curl -fsS http://127.0.0.1:8000/api/export/calibration_set.json > "$OUT_DIR/calibration_set.json"
curl -fsS "http://127.0.0.1:8000/api/export/individual.json?annotator=amber" \
    > "$OUT_DIR/individual_amber.json" || warn "individual_amber 失敗（amber 可能尚無 is_complete）"
ls -lh "$OUT_DIR"/*.json

# ── 4. validate ────
step "4/5 跑 validator"
ALL_GREEN=1
for f in "$OUT_DIR"/*.json; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    if python scripts/validate_export.py "$f" > /tmp/validate-out.txt 2>&1; then
        ok "$name"
    else
        ALL_GREEN=0
        fail "$name 驗證失敗 — 請看 /tmp/validate-out.txt"
    fi
done

# ── 5. 完成 ────
step "5/5 完成"
if [ "$ALL_GREEN" -eq 1 ]; then
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✅ ALL DONE${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "輸出目錄：$OUT_DIR"
    if [ "$USE_SNAPSHOT" -eq 1 ]; then
        echo ""
        echo "下一步建議（snapshot 模式）："
        echo "  git add exports/snapshot-$TODAY/"
        echo "  git commit -m 'snapshot: $TODAY 的 dataset/calibration_set/individual'"
        echo "  git tag v0.X.Y-<說明>"
        echo "  git push --tags"
    fi
fi
