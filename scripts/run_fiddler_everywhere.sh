#!/usr/bin/env bash
# Fiddler Everywhere 安装后辅助脚本（Linux）
# 用法：
#   1. 从 https://www.telerik.com/download/fiddler-everywhere-linux 下载 AppImage
#   2. 将 AppImage 放到 ~/.local/bin/ 或当前目录，命名为 fiddler-everywhere.AppImage（或含 Fiddler 的任意名）
#   3. 运行: ./scripts/run_fiddler_everywhere.sh

set -e

APPIMAGE=""
for candidate in \
    "$HOME/.local/bin/fiddler-everywhere.AppImage" \
    "$HOME/.local/bin/Fiddler Everywhere-"*".AppImage" \
    "./Fiddler Everywhere-"*".AppImage" \
    "./fiddler-everywhere.AppImage" \
    "$(dirname "$0")/Fiddler Everywhere-"*".AppImage"
do
    if [ -f $candidate ] 2>/dev/null; then
        APPIMAGE="$candidate"
        break
    fi
done

if [ -z "$APPIMAGE" ]; then
    echo "未找到 Fiddler Everywhere AppImage。"
    echo "请从 https://www.telerik.com/download/fiddler-everywhere-linux 下载，"
    echo "并放到 ~/.local/bin/ 或项目 scripts 目录，再运行本脚本。"
    exit 1
fi

# 确保可执行
chmod +x "$APPIMAGE"

# Ubuntu 22.04+ 若报 FUSE 相关错误，先执行: sudo apt install libfuse2
if command -v apt-get &>/dev/null; then
    (dpkg -l libfuse2 2>/dev/null | grep -q "ii libfuse2") || echo "提示: 若启动报错，可尝试 sudo apt install libfuse2"
fi

echo "启动: $APPIMAGE"
echo "若出现沙箱错误，请改用: $APPIMAGE --no-sandbox"
exec "$APPIMAGE" "$@"
