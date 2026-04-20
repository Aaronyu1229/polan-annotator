#!/bin/bash
# 在桌面建 symlink 指向 scripts/start_annotator.command。
# 用 symlink 而非 cp：之後更新 .command 不需重 install；Amber 搬 project 只要重跑此腳本更新 symlink 即可。
#
# 使用：
#     bash scripts/install_desktop_shortcut.sh
#
# 跑完桌面會多出「啟動珀瀾標註工具.command」symlink。

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/start_annotator.command"
TARGET="$HOME/Desktop/啟動珀瀾標註工具.command"

if [ ! -f "$SOURCE" ]; then
    echo "❌ 找不到來源腳本：$SOURCE"
    echo "   請確認你在 polan-annotator/ 根目錄下執行這個安裝腳本。"
    exit 1
fi

# 確保來源可執行
chmod +x "$SOURCE"

# 桌面已有同名檔 / symlink → 先移除再建（避免 ln -s 因檔案已存在失敗）
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    rm -f "$TARGET"
fi

ln -s "$SOURCE" "$TARGET"

echo "✓ 已建立桌面 symlink：$TARGET"
echo "  → 指向 $SOURCE"
echo "✓ 來源腳本已設為可執行"
echo ""
echo "Amber 接下來只要雙擊桌面上的「啟動珀瀾標註工具」就能啟動工具。"
echo ""
echo "⚠️  第一次雙擊 macOS 會跳「無法打開，因為無法驗證開發者」的警告。"
echo "    處理方式：到 系統設定 → 隱私權與安全性 → 捲到最下面會看到"
echo "    「已封鎖『啟動珀瀾標註工具』」 → 點旁邊的「強制打開」。"
echo "    之後就不會再跳警告。"
echo ""
echo "⚠️  若 Amber 搬 project 到別的位置（例如 ~/Documents），重跑一次："
echo "    bash scripts/install_desktop_shortcut.sh"
echo "    即可更新桌面 symlink。"
