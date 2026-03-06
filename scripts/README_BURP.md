# Burp Suite Community 安装说明（Linux 18.04）

## 一步：安装 Java 21（Burp 新版必须）

**Ubuntu 18.04 自带源没有 Java 21**，可选两种方式：

### 方式 A：便携版 Java 21（推荐，无需 sudo）

在项目目录执行：

```bash
chmod +x scripts/setup_java21_for_burp.sh
./scripts/setup_java21_for_burp.sh
```

会把 OpenJDK 21 下载到 `~/.local/java21/`，之后 `run_burp.sh` 会自动用这个 Java 启动 Burp。

### 方式 B：系统安装 Java 21（需要 sudo）

若系统是 Ubuntu 20.04+，可直接：`sudo apt install openjdk-21-jre`。  
18.04 可加 PPA 后安装，或从 [Adoptium](https://adoptium.net/temurin/releases/?os=linux&arch=x64&package=jre&version=21) 下载 .tar.gz 解压到 `~/.local/java21/`。

## 二步：安装并运行 Burp

```bash
cd /media/cheny/D/code_src/projectCode/qq/qqmusic-crawler
chmod +x scripts/install_burp_community.sh
./scripts/install_burp_community.sh
```

- 脚本会尝试自动下载 Burp JAR；若失败，会提示你用浏览器打开  
  https://portswigger.net/burp/communitydownload 下载 Linux 版 JAR，放到 `~/.local/burp-community/` 后重新运行脚本。
- 首次运行会生成 `~/.local/burp-community/run_burp.sh`，之后可直接执行该脚本启动 Burp。

## 抓包简要设置

1. 启动 Burp → **Proxy** → **Options**，确认监听 `127.0.0.1:8080`。
2. 浏览器或系统代理设为：`127.0.0.1`，端口 `8080`。
3. 浏览器访问 `http://burp`，下载并安装 Burp 的 CA 证书，才能解密 HTTPS。
4. 抓微信/小程序：可用手机连同一 WiFi，手机 WiFi 代理设为电脑 IP + 8080，并在手机安装 Burp 证书。

---

若 Burp 安装失败或无法使用，可参考 **[抓包工具备选](README_抓包工具备选.md)**（mitmproxy、Charles、Fiddler、HTTP Toolkit 等）。
