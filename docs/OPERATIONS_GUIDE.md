# 邻里停（Community Parking）日常运维与排障速查手册

本手册提供日常值守所需的**启动、关闭、平滑重启、状态巡检、日志查看、数据备份、版本升级与常见故障速查**，命令简洁统一，可直接复制执行。

---

## 一、三大平台高频运维命令速查表

进入项目根目录 `community-parking` 后执行对应命令：

| 运维动作 | macOS（M 芯片 / Intel） | Linux（Ubuntu / Debian） | Windows（x86_64 / ARM64 PowerShell） |
| :--- | :--- | :--- | :--- |
| **启动服务** | `./parking-ctl.sh start` | `./parking-ctl.sh start` | `.\parking-ctl.ps1 start` |
| **局域网模式启动** | `PARKING_HOST=0.0.0.0 ./parking-ctl.sh start` | `PARKING_HOST=0.0.0.0 ./parking-ctl.sh start` | `$env:PARKING_HOST="0.0.0.0"; .\parking-ctl.ps1 start` |
| **平滑重启服务** | `./parking-ctl.sh restart` | `./parking-ctl.sh restart` | `.\parking-ctl.ps1 restart` |
| **优雅关闭服务** | `./parking-ctl.sh stop` | `./parking-ctl.sh stop` | `.\parking-ctl.ps1 stop` |
| **查看进程与内存** | `./parking-ctl.sh status` | `./parking-ctl.sh status` | `.\parking-ctl.ps1 status` |
| **业务与缓存健康检查** | `./parking-ctl.sh health` | `./parking-ctl.sh health` | `.\parking-ctl.ps1 health` |
| **实时跟随运行日志** | `./parking-ctl.sh logs` | `./parking-ctl.sh logs` | `.\parking-ctl.ps1 logs` |
| **前台调试运行** | `./parking-ctl.sh foreground` | `./parking-ctl.sh foreground` | `.\parking-ctl.ps1 foreground` |
| **开启开机自启守护** | `./parking-ctl.sh install-launchd` | `./parking-ctl.sh install-systemd` | `.\parking-ctl.ps1 install-task` |
| **取消开机自启守护** | `./parking-ctl.sh uninstall-launchd` | `./parking-ctl.sh uninstall-systemd` | `.\parking-ctl.ps1 uninstall-task` |

> **注意**：若已启用系统级守护（macOS `launchd` 或 Linux `systemd`），守护进程具备崩溃自动拉起能力（`KeepAlive` / `Restart=always`）。此时：
> - **重启服务**：
>   - macOS：`launchctl kickstart -k gui/$(id -u)/com.community-parking.server`（或重新执行 `./parking-ctl.sh install-launchd`）
>   - Linux：`systemctl --user restart community-parking.service`
> - **彻底停止（不再自动拉起）**：先执行对应的 `uninstall-launchd` / `uninstall-systemd`，或 Linux 下执行 `systemctl --user stop community-parking.service`。

---

## 二、端口与运行参数调整

无需修改代码，通过环境变量即可覆盖默认监听地址与端口：

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PARKING_HOST` | `127.0.0.1` | 管理控制台绑定地址；设为 `0.0.0.0` 可对局域网管理电脑或 HTTP 推图摄像头开放 |
| `PARKING_PORT` | `8790` | 管理控制台与摄像头 HTTP Webhook 抓图推送端口 |
| `PARKING_MOBILE_HOST` | `0.0.0.0` | 业主手机端极简寻位专口绑定地址（默认面向局域网手机直接开放） |
| `PARKING_MOBILE_PORT` | `18790` | 业主手机端极简寻位与语音播报只读端口（设为 `0` 可关闭该端口） |
| `PARKING_FTP_PORT` | `2121` | 内嵌零落盘内存 FTP 接收端口（供不支持 HTTP 抓图的摄像头定时传图） |

示例（自定义端口 `8800` 重启）：
- macOS / Linux：`PARKING_HOST=0.0.0.0 PARKING_PORT=8800 ./parking-ctl.sh restart`
- Windows：`$env:PARKING_HOST="0.0.0.0"; $env:PARKING_PORT="8800"; .\parking-ctl.ps1 restart`

> **业务参数在线热更新**：VLM 模型地址/密钥、双帧防抖秒数（默认 `3s`）、在途软锁时长（默认 `40s`）、热快取内存上限（默认 `64 MB`）均可在 Web 右上角**「模型设置」**直接保存并实时生效，**无需重启服务**。

---

## 三、目录资产与数据备份 / 恢复 / 迁移

系统严格遵守零实景落盘设计，所有持久化状态均集中在 `data/` 目录下的 **2 个核心文件**：

| 文件路径 | 内容说明 | 是否需要备份 |
| :--- | :--- | :--- |
| `data/parking.db`（及 `-wal`/`-shm`） | 小区平面图、路网、单元楼栋、摄像头台账与接入配置、空间标定多边形、空场脱敏参考底板、车位实时状态 | **必备份** |
| `data/runtime_config.json` | 多模态模型 Endpoint / API Key、防抖阈值、FTP 账号密码、缓存预算 | **必备份（含敏感密钥，勿上传公开仓库）** |
| `data/parking-8790.log` | 运行日志（每次启动/重启自动轮转截断至最近 1000~2000 行，防撑爆磁盘） | 无需备份 |
| `data/parking-8790.pid` | 当前服务进程 PID 文件 | 无需备份 |

### 1. 在线热备份（无需停机）
由于 SQLite 启用了 WAL 并发日志模式，在线热备份建议使用 Python 内置 SQLite backup API（跨平台通用，不会拷漏 `-wal` 未落盘事务）：

```bash
# macOS / Linux 在线一致性备份到 backups/ 目录
mkdir -p backups
.venv/bin/python -c "import sqlite3, shutil, time; ts=time.strftime('%Y%m%d-%H%M%S'); src=sqlite3.connect('data/parking.db'); dst=sqlite3.connect(f'backups/parking-{ts}.db'); src.backup(dst); dst.close(); src.close(); shutil.copy2('data/runtime_config.json', f'backups/runtime_config-{ts}.json'); print('Backup OK:', ts)"
```

Windows PowerShell 同理将开头 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe` 即可。

### 2. 停机冷恢复 / 跨机器迁移
将备份的 `parking.db` 与 `runtime_config.json` 拷贝到新机器的 `data/` 目录：
1. 先停止目标机器服务（`stop`）；
2. 删除旧的 `data/parking.db-wal` 和 `data/parking.db-shm`（如有），覆盖放入备份的 `data/parking.db` 与 `data/runtime_config.json`；
3. 启动服务（`start`）并执行 `health` 验证车位总数一致。

---

## 四、版本升级与快速回滚标准流程（SOP）

```bash
# 1. 先做一次在线备份（见上一节命令）

# 2. 拉取最新代码并同步依赖（不会改动被 .gitignore 保护的 data/ 目录）
git pull origin main
.venv/bin/pip install -r requirements.txt   # Windows 用 .\.venv\Scripts\pip.exe install -r requirements.txt

# 3. 平滑重启并检查健康度（数据库新字段会在启动时自动平滑迁移）
./parking-ctl.sh restart                     # Windows 用 .\parking-ctl.ps1 restart
./parking-ctl.sh health                      # Windows 用 .\parking-ctl.ps1 health
```

如需紧急回滚代码版本：`git checkout <上一个稳定CommitSHA>` 后执行 `restart` 即可。

---

## 五、巡检指标解读与常见故障 1 分钟排障

### 1. 健康检查输出怎么看？
执行 `./parking-ctl.sh status`（或 `.\parking-ctl.ps1 status`）正常输出示例：
```text
🟢 运行中 | PID: 66798 | 物理内存(RSS): 148.5 MB | 地址: http://127.0.0.1:8790
   📊 概览: 总车位 46 | 净余空位 12 | 落盘 0 | 瞬时销毁 1580 帧 | 热快取 12.57 MB/64MB (命中: 模板920/VLM310)
```
- **落盘 `0`**：证明全部摄像头抓拍原图均在 RAM 中解码裁剪并瞬时释放，硬盘零实景图片残留。
- **热快取 `X MB / 64MB`**：内存 LRU 签名/模板/VLM 感知缓存实时占用，受 64MB 硬上限淘汰保护，长期运行不会内存泄漏。
- **物理内存(RSS)**：Python + OpenCV 常驻物理内存通常稳定在 `120 ~ 280 MB`。

### 2. 常见故障排障表

| 现象 | 常见原因 | 1 分钟解决办法 |
| :--- | :--- | :--- |
| 本机 `curl http://127.0.0.1:8790` 报 `502 / 503` | 系统开启了科学上网/企业全局代理，劫持了 `127.0.0.1` 流量 | 命令加 `--noproxy '*'`（`parking-ctl` 已内置绕过代理）；浏览器把 `127.0.0.1` 加入代理白名单 |
| 启动提示端口 `8790` 被占用 | 旧实例或其他程序占用了 `8790` | 执行 `./parking-ctl.sh restart`（或 `.\parking-ctl.ps1 restart`）自动回收旧进程，或改用 `PARKING_PORT=8800` |
| 同 Wi-Fi 手机打不开 `http://<电脑IP>:8790` | 服务默认仅监听了回环地址 `127.0.0.1` 或系统防火墙拦截 | 使用 `PARKING_HOST=0.0.0.0` 重启服务，并检查 Windows Defender 防火墙 / Linux `ufw` 放行 `8790` |
| 海康/大华测试抓图报 `HTTP 401 Unauthorized` | 摄像头开启了 Digest 摘要认证，或抓图账号密码错误 | 在「摄像头管理 -> 编辑」确认认证方式为 **Digest（摘要认证，预设已默认）**，核对摄像机本地 Web 密码（非萤石云验证码，部分萤石/乐橙局域网密码为机身 6 位安全码） |
| RTSP 拉流超时但能 ping 通摄像头 | 子码流端口/通道号不匹配，或 UDP 丢包 | 优先改用厂商推荐的 **HTTP CGI 抓图**（最省内存带宽）；若必须走 RTSP，确认端口 `554`、传输协议选 `TCP`、接 NVR 时正确填写通道号 |
| 治理看板提示某机位「疑似大风偏移 / 起雾遮挡」 | 摄像头支架被外力碰歪或镜头脏污 | 系统内置 RootSIFT 已自动补偿轻中度位移；若偏移过大，擦拭镜头或在「空间标定」重新抓拍更新一次空场基准帧即可消除告警 |
