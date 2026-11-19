# 邻里停（Community Parking）跨平台安装与部署手册

本手册面向 **macOS（Apple Silicon M 芯片）**、**Windows（x86_64 与 ARM64）** 以及 **Linux（Ubuntu / Debian 等）** 环境，提供开箱即用的标准化安装步骤。系统零外部中间件依赖（内嵌 SQLite WAL + 内存级 HTTP/FTP 服务），单机 3 分钟即可完成部署。

---

## 一、环境与端口准备

| 项目 | 最低配置 | 推荐配置（8~32 路摄像头） |
| :--- | :--- | :--- |
| **处理器架构** | Apple M1+ / x86_64 / ARM64（双核） | Apple M 系列 / 4 核 x86_64 或 ARM64 边缘主机 |
| **内存（RAM）** | 2 GB | 4 GB+（本系统常驻物理内存仅约 `120~260 MB`） |
| **操作系统** | macOS 13+ / Windows 10 21H2+ / Ubuntu 20.04+ | macOS 14+ / Windows 11 / Ubuntu 22.04 或 24.04 LTS |
| **Python 版本** | Python `3.10` ~ `3.12`（64 位原生版） | **Python `3.11` 或 `3.12`**（全平台预编译 Wheel 最齐全） |
| **网络端口** | TCP `8790`（管理端）+ `18790`（微信H5看图）+ `18791`（Siri免手语音/AI问答） | 如需摄像头主动 FTP 推图，额外开放 TCP `2121` |

> **三端口安全隔离与 Cloudflare 零成本公网架构**：
> - **`8790` 端口（管理与标定控制台）**：默认仅绑定宿主机回环 `127.0.0.1:8790`，承载摄像头管理、主备冷切槽位配置、空间标定与大模型密钥管理，**永不暴露到公网**。
> - **`18790` 端口（微信浮窗极简看图端）**：默认监听 `0.0.0.0:18790`，**纯只读安全隔离**，供业主在普通微信置顶/浮窗秒看实时红绿车位平面图与 1遍/2遍持续播报。
> - **`18791` 端口（开车免手语音 · Siri CarPlay · 自有大模型问答 · 普通微信助手网关）**：默认监听 `0.0.0.0:18791`，提供 `<20ms` 极速双遍播报口（`/api/voice/brief`）、iOS 快捷指令 `.shortcut` 一键下载、复用自有大模型的自然语言问答（`/api/voice/ask`）及可选普通微信 `openclaw-weixin` 出站长轮询桥接。

---

## 一（推荐）、Docker 容器化与云端 Spot 实例抗重启部署（Ubuntu / Windows / Mac 通用）

针对**云端 Ubuntu VM、高频抢占/重启的 Spot 竞价机器、以及物业本地 Windows / Linux 主机**，强烈推荐使用仓库内置的多架构 `Dockerfile` + `docker-compose.yml`（原生兼容 `linux/amd64` 与 `linux/arm64`）。

### 1. 为什么 Spot 竞价实例 + Docker 是最佳拍档？
1. **断电/抢占重启自动在线**：`docker-compose.yml` 配置了 `restart: always`，配合系统 `docker.service` 开机自启，Spot 实例被云平台回收重拉或物业断电来电后，容器在数秒内自动恢复 `:8790` + `:18790` + `:18791` 全套服务。
2. **30 秒抢占窗口零数据损坏**：容器内置 `tini` PID 1 信号转发与 `stop_grace_period: 25s`，收到云平台 `SIGTERM` 回收信号时立即停止后台拉流并执行 `PRAGMA wal_checkpoint(TRUNCATE)`，将 SQLite WAL 日志完整落盘到宿主机 `./data` 挂载卷。

### 2. Ubuntu / Spot 云主机 2 分钟启动步骤

```bash
# 1. 安装 Docker Engine 与 Compose 插件，并设为开机自启（Spot 重启自动恢复的关键！）
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# 2. 拉取代码并一键构建启动（自动挂载 ./data 持久化数据库与 runtime_config.json）
git clone https://github.com/wanggaoxiong/community-parking.git
cd community-parking
./parking-ctl.sh docker-up
```

> **Windows 物业主机容器化**：安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)（开启开机自启 `Start Docker Desktop when you sign in`），在 PowerShell 进入仓库执行 `.\parking-ctl.ps1 docker-up` 即可。

### 3. 零成本接入 Cloudflare Tunnel（0 元免备案服务 1,000+ 业主）
已有 Cloudflare 域名时，无需购买公网 IP、无需开放云安全组入站端口、无需 ICP 备案（Cloudflare Free 计划免费支持 1,000 条隧道，公开 Web/API 访问不受 50 席位限制）：
1. 登录 [Cloudflare Zero Trust 控制台](https://one.dash.cloudflare.com/) -> **Networks -> Tunnels -> Create a tunnel（Cloudflared）**，复制生成的 `Tunnel Token`。
2. 在该 Tunnel 的 **Public Hostname** 添加两条子域名映射（**切勿映射 `8790`**）：
   - `parking.你的域名.com` -> `Service: HTTP`，`URL: community-parking:18790`（若宿主机直跑填 `127.0.0.1:18790`）
   - `voice.你的域名.com` -> `Service: HTTP`，`URL: community-parking:18791`（若宿主机直跑填 `127.0.0.1:18791`）
3. 一键拉起含隧道侧车的容器栈：

```bash
CLOUDFLARE_TUNNEL_TOKEN="粘贴你的Token" ./parking-ctl.sh docker-up
```

4. 打开本机管理台 `http://127.0.0.1:8790` -> 右上角「配置 Azure 模型」，在下方 **`:18791` 免手语音网关** 区域设置访问口令（如 `xiaoqu888`）并填入上述两个 `https://` 公网域名保存即可！

---

## 二、macOS（M1 / M2 / M3 / M4 Apple Silicon）部署

### 1. 安装基础工具与原生 ARM64 Python
打开「终端（Terminal）」，确认当前运行在原生 `arm64` 模式（输出应为 `arm64`）：

```bash
uname -m
```

若尚未安装 Python 3.11+，推荐使用 Homebrew 安装原生 Apple Silicon 版本：

```bash
xcode-select --install 2>/dev/null || true
brew install python@3.11 git
```

### 2. 拉取代码并创建虚拟环境

```bash
git clone https://github.com/wanggaoxiong/community-parking.git
cd community-parking

/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chmod +x parking-ctl.sh
```

> 若系统自带 `python3` 已满足 `>=3.9`，也可直接执行 `python3 -m venv .venv`。

### 3. 启动服务与验证

```bash
# 本机模式启动（127.0.0.1:8790）
./parking-ctl.sh start

# 或：局域网共享模式启动（允许手机/摄像头访问本机局域网 IP）
PARKING_HOST=0.0.0.0 ./parking-ctl.sh start

# 验证健康状态
./parking-ctl.sh health
```

浏览器打开 `http://127.0.0.1:8790` 即可使用。如需注册 macOS 登录自启与崩溃自动拉起：

```bash
PARKING_HOST=0.0.0.0 ./parking-ctl.sh install-launchd
```

---

## 三、Windows（x86_64 与 ARM64 骁龙本/虚拟机）部署

### 1. 安装对应架构的 Python 与 Git
1. 前往 [python.org/downloads/windows](https://www.python.org/downloads/windows/) 下载 **Python 3.11 或 3.12**：
   - **Intel / AMD 电脑（x86_64）**：下载 `Windows installer (64-bit)`。
   - **骁龙 / Surface / Apple Silicon Parallels（Windows on ARM）**：推荐下载原生 `Windows installer (ARM64)`（Python 3.11/3.12 下 `numpy`、`Pillow`、`opencv-python-headless` 均已提供成熟预编译包；若某特定 ARM64 子版本缺少原生 OpenCV wheel，也可直接安装 `Windows installer (64-bit)` 走 Windows 11 Prism x64 仿真，零编译即可运行）。
2. 安装时**务必勾选底部的 `Add python.exe to PATH`**。
3. 安装 [Git for Windows](https://git-scm.com/download/win)。

### 2. 拉取代码并安装依赖
以普通用户或管理员打开 **PowerShell**，执行：

```powershell
# 允许当前用户运行本地运维脚本（仅首次执行一次）
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

git clone https://github.com/wanggaoxiong/community-parking.git
cd community-parking

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### 3. 启动服务与防火墙放行（可选局域网访问）

```powershell
# 本机模式启动
.\parking-ctl.ps1 start

# 或：局域网共享模式启动（允许同 Wi-Fi 手机与摄像头访问）
$env:PARKING_HOST = "0.0.0.0"
.\parking-ctl.ps1 start

# 检查健康状态
.\parking-ctl.ps1 health
```

若启用了 `0.0.0.0` 局域网访问，以**管理员身份**在 PowerShell 放行端口一次即可：

```powershell
New-NetFirewallRule -DisplayName "CommunityParking-Mobile" -Direction Inbound -Protocol TCP -LocalPort 18790 -Action Allow
New-NetFirewallRule -DisplayName "CommunityParking-Web" -Direction Inbound -Protocol TCP -LocalPort 8790 -Action Allow
New-NetFirewallRule -DisplayName "CommunityParking-FTP" -Direction Inbound -Protocol TCP -LocalPort 2121 -Action Allow
```

如需注册 Windows 开机登录自动启动与异常自动重启：

```powershell
$env:PARKING_HOST = "0.0.0.0"
.\parking-ctl.ps1 install-task
```

---

## 四、Linux（Ubuntu / Debian x86_64 与 ARM64）部署

兼容 Ubuntu `20.04 / 22.04 / 24.04 LTS`（含云服务器、物业工控机、N100 小主机、树莓派 4B/5、RK3588 边缘盒子）。

### 1. 安装系统依赖包
`opencv-python-headless` 已免去 X11 桌面依赖，仅需补充最小基础运行库：

```bash
sudo apt update
sudo apt install -y git curl lsof python3 python3-venv python3-pip libglib2.0-0 libgl1 ffmpeg
```

> Ubuntu 24.04 若提示 `libgl1` 包名正常安装即可；`ffmpeg` 为可选增强项，便于后续流媒体联调。

### 2. 拉取代码并初始化虚拟环境

```bash
git clone https://github.com/wanggaoxiong/community-parking.git
cd community-parking

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chmod +x parking-ctl.sh
```

### 3. 启动服务、开放防火墙与配置 `systemd` 守护

```bash
# 生产服务器通常监听 0.0.0.0 供局域网访问
PARKING_HOST=0.0.0.0 ./parking-ctl.sh start
./parking-ctl.sh health
```

如启用了 `ufw` 防火墙，放行 `8790`（及按需放行 `2121`）：

```bash
sudo ufw allow 8790/tcp
sudo ufw allow 2121/tcp
```

注册 `systemd` 后台常驻与断电开机自启（无需手写 unit 文件）：

```bash
PARKING_HOST=0.0.0.0 ./parking-ctl.sh install-systemd
sudo loginctl enable-linger "$USER"
```

---

## 五、部署后 5 分钟初始化清单

1. **自动化回归自检（可选）**：
   - macOS / Linux：`.venv/bin/python -m unittest discover -s tests -v`
   - Windows：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
2. **配置多模态大模型（右上角「模型设置」）**：
   - 选择预设（如 `Azure AI Foundry`、`OpenAI`、`阿里云百炼 Qwen-VL` 或本地离线 `Ollama`），填入 Endpoint 与 API Key，点击「测试连通性」后保存（配置自动持久化到 `data/runtime_config.json`，不会随 Git 提交泄露）。
   - 无外网环境可将模式切换为 `local_only`（纯本地 OpenCV 引擎）。
3. **构建小区 2F 态势平面图（「寻位看板」-> 态势图工作区）**：
   - 上传小区平面底图一键生成或手工微调单元楼栋、单/双向行车环路、人行步道及车位带。
4. **接入并绑定主流摄像头（「摄像头管理」Tab ->「+ 新增摄像头」）**：
   - 选择海康、大华、TP-Link、萤石、ONVIF 等预设模板，点击「测试抓图预览」出图后绑定到平面图对应车位带，最后前往「空间标定」框选地面车位框并保存空场基准模板。
5. **配置 `:18791` Siri 车载免手播报与苹果签名说明**：
   - **macOS 本机运行**：`:18791` 点击「📲 一键下载『小区车位』Siri 快捷指令」时，后端会自动调用系统内置 `/usr/bin/shortcuts sign --mode anyone` 生成 **Apple 官方签名包（`AEA1`）** 并缓存于 `data/shortcuts/`，双击或发送至 iPhone 即可直接添加为「小区车位」（不再报错“不支持导入未签名快捷指令”），导入后直接喊 **“嘿 Siri，小区车位”** 即可双遍播报。
   - **Ubuntu / Docker / Spot VM / Windows 云端部署（二选一免签分发）**：
     - **方式 A（推荐，全平台永久有效）**：在 Mac 或 iPhone 导入「小区车位」快捷指令后，点「分享 → 拷贝 iCloud 链接（`https://www.icloud.com/shortcuts/...`）」，粘贴到 `:8790` 右上角「🎙️ 18791 Siri / 语音网关」弹窗的 **iCloud 快捷指令分享链接** 中保存；
     - **方式 B（随 `data/` 目录携带通用签名包）**：在任意 Mac 执行一次 `./parking-ctl.sh sign-shortcut https://voice.你的域名.com 8-1`，会在 `data/shortcuts/universal-signed.shortcut` 生成带单元提问向导的 Apple 签名包，随 `./data:/app/data` 挂载到 Linux Docker 容器即可直接下载导入。

