#!/bin/bash
# 在 Ubuntu 18.04 等没有 Java 21 的系统上：下载便携版 OpenJDK 21，专门用于运行 Burp。
# 用法：chmod +x scripts/setup_java21_for_burp.sh && ./scripts/setup_java21_for_burp.sh

set -e
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/java21}"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "=== 检查是否已有 Java 21 ==="
if [[ -x "$INSTALL_DIR/bin/java" ]]; then
    ver=$("$INSTALL_DIR/bin/java" -version 2>&1)
    if [[ "$ver" == *"21"* ]]; then
        echo "已存在 Java 21: $INSTALL_DIR"
        echo "可直接用以下命令启动 Burp："
        echo "  $INSTALL_DIR/bin/java -jar ~/.local/burp-community/burpsuite_community_*.jar"
        exit 0
    fi
fi

echo "=== 下载 Adoptium Temurin OpenJDK 21 (Linux x64) ==="
# 使用 Adoptium API 获取最新 21 LTS 的下载链接（JRE 体积较小）
URL="https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse?project=jdk"
if ! wget -q --show-progress -O jdk21.tar.gz "$URL"; then
    echo "自动下载失败。请手动安装 Java 21："
    echo "  1. 打开 https://adoptium.net/temurin/releases/?os=linux&arch=x64&package=jre&version=21"
    echo "  2. 下载 .tar.gz，解压到: $INSTALL_DIR"
    echo "  3. 确保 $INSTALL_DIR/bin/java 存在，再运行 Burp："
    echo "     $INSTALL_DIR/bin/java -jar ~/.local/burp-community/burpsuite_community_*.jar"
    exit 1
fi

echo "=== 解压 ==="
rm -rf jdk-21* 2>/dev/null || true
tar -xzf jdk21.tar.gz
rm -f jdk21.tar.gz
# 解压后可能是 jdk-21.0.x+xx 这样的目录
DIR=( jdk-21* )
if [[ -d "${DIR[0]}" ]]; then
    mv "${DIR[0]}"/* .
    rmdir "${DIR[0]}" 2>/dev/null || true
fi

if [[ ! -x "$INSTALL_DIR/bin/java" ]]; then
    echo "解压后未找到 bin/java，请检查 $INSTALL_DIR 目录结构。"
    exit 1
fi

"$INSTALL_DIR/bin/java" -version
echo ""
echo "Java 21 已安装到: $INSTALL_DIR"
echo "用其启动 Burp："
echo "  $INSTALL_DIR/bin/java -jar ~/.local/burp-community/burpsuite_community_*.jar"
echo "或把 run_burp.sh 里的 java 改成: $INSTALL_DIR/bin/java"
