#!/bin/bash
# Burp Suite Community 安装脚本（Ubuntu 18.04 / 其他 Linux）
# 请在终端中执行：chmod +x scripts/install_burp_community.sh && ./scripts/install_burp_community.sh

set -e
BURP_DIR="${BURP_DIR:-$HOME/.local/burp-community}"
mkdir -p "$BURP_DIR"
cd "$BURP_DIR"

echo "=== 1. 检查 Java ==="
if ! command -v java &>/dev/null; then
    echo "未检测到 Java，请先安装 Java 11 或以上："
    echo "  sudo apt-get update"
    echo "  sudo apt-get install -y openjdk-11-jre"
    echo ""
    echo "安装完成后重新运行本脚本。"
    exit 1
fi
java -version

echo ""
echo "=== 2. 检查 Burp JAR ==="
# 优先使用已下载的 JAR（按常见命名）
JAR=""
for name in burpsuite_community*.jar burpsuite_community.jar; do
    if [[ -f "$name" ]]; then
        JAR="$name"
        break
    fi
done

if [[ -z "$JAR" ]]; then
    echo "未找到 Burp Suite Community 的 JAR，尝试自动下载..."
    # 官方下载页需浏览器同意协议，以下为常见版本链接，可能需登录/协议后才有效
    for ver in 2024.11.1 2024.9.2 2024.7.4; do
        if wget -q -O "$BURP_DIR/burpsuite_community.jar" \
            "https://portswigger.net/burp/releases/download?product=community&version=${ver}&type=jar" 2>/dev/null; then
            if [[ $(file -b "$BURP_DIR/burpsuite_community.jar") == *"Java archive"* ]]; then
                JAR="burpsuite_community.jar"
                echo "已下载: $JAR (version $ver)"
                break
            fi
        fi
        rm -f "$BURP_DIR/burpsuite_community.jar"
    done
fi

if [[ -z "$JAR" ]]; then
    echo "自动下载未成功。请手动安装："
    echo "  1. 安装 Java: sudo apt-get update && sudo apt-get install -y openjdk-11-jre"
    echo "  2. 浏览器打开: https://portswigger.net/burp/communitydownload"
    echo "  3. 下载 Linux 版 JAR，保存到: $BURP_DIR"
    echo "  4. 重新运行本脚本"
    exit 1
fi

echo "使用 JAR: $JAR"
echo ""
echo "=== 3. 启动 Burp Suite Community ==="
echo "安装目录: $BURP_DIR"
echo "下次可直接运行: $BURP_DIR/run_burp.sh"
echo ""

# 生成启动脚本（优先用 Java 21，避免 UnsupportedClassVersionError）
JAVA21="$HOME/.local/java21/bin/java"
cat > "$BURP_DIR/run_burp.sh" << RUN
#!/bin/bash
BURP_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
cd "\$BURP_DIR"
JAR=\$(ls -t burpsuite_community*.jar 2>/dev/null | head -1)
if [[ -z "\$JAR" ]]; then
    echo "未找到 JAR，请将 burpsuite_community_*.jar 放到: \$BURP_DIR"
    exit 1
fi
# 优先 Java 21（Burp 新版需要），否则用系统 java
JAVA="java"
[[ -x $JAVA21 ]] && JAVA=$JAVA21
exec "\$JAVA" -jar "\$JAR" "\$@"
RUN
chmod +x "$BURP_DIR/run_burp.sh"

exec java -jar "$JAR" "$@"
