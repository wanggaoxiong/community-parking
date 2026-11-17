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
| **网络端口** | TCP `8790`（管理控制台）+ TCP `18790`（手机端极简寻位专口） | 如需摄像头主动 FTP 推图，额外开放 TCP `2121` |

> **双端口安全分流说明**：
> - **`8790` 端口（管理控制台）**：默认仅绑定本机 `127.0.0.1:8790`，包含摄像头管理、空间标定、路网编辑与模型配置；若需局域网电脑管理或接收摄像头 HTTP 推图可设 `PARKING_HOST=0.0.0.0`。
> - **`18790` 端口（手机端业主极简寻位页）**：默认自动监听 `0.0.0.0:18790`，**纯只读安全隔离**（禁止任何后台配置或写入接口，且完全剥离摄像头 IP/密码等敏感字段），同局域网业主手机浏览器直接打开 `http://<主机局域网IP>:18790` 即可设置各自目的地、开启 1遍/2遍持续语音播报并双指缩放查看实时平面图。

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
