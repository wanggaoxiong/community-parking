# 邻里停 (Community Parking) · 小区公益智能寻位系统

一套专为地面车位“先到先得”的普通住宅小区打造的**超低成本、零隐私泄露、免施工**开源公益寻位方案。

通过热心业主窗台百元级网络摄像机（定时抓拍 + 移动侦测抓拍上传）与 **本地 OpenCV + Azure AI Foundry 多模态视觉模型** 协同分析，在车主驶入小区大门时以毫秒级语音推荐**离单元门最近、好掉头且不会被前车抢位**的空余车位片区。

---

## 核心特性

1. **隐私安全“四不红线”**
   - **原图零落盘**：内嵌纯内存 FTP / HTTP 接收器，全景抓拍在 RAM 中完成车位裁切后**毫秒级销毁**，磁盘永不保存任何一张原始照片。
   - **前端零实景图**：车主端仅渲染 Apple Maps 风格的抽象红绿车位平面图，杜绝车牌识别与邻里行踪记录。
2. **AI 参考图自我标定 + 永久参考模板 + 异形标定台**
   - **单图自动标定与建档（Reference Auto-Calibration）**：不同楼层、路灯视角的畸变和排布各异——只需上传一张该机位的实拍或 AI 生成参考图，系统自动叠加 `0..1000` 归一化坐标尺交由多模态模型解析全部车位四边形、地面车位编号（如 `01`–`07`）及初始空满状态，并结合 OpenCV `MORPH_TOPHAT` 白色标线亚像素吸附焊合相邻车位边线。
   - **脱敏参考模板持续赋能**：自动对参考图中的车牌区域打码脱敏后存为机位**参考模板**；后续日常抓拍自动利用 ORB 单应性矩阵抵消窗台相机微晃动偏移，并与空车位路面基准签名比对提升准确率。
   - **双层独立标定工作区（无车地面标定 vs 车辆停入标定）**：将路面标线层与车辆 3D 停入轮廓层拆分为完全解耦的独立工作区，支持分别上传「无车空场底图」与「有车停入参考图」、独立编辑与按层保存（`calibration_layer = ground | vehicle | both`），互不覆盖干扰。
   - **手动精细标定工具**：同时支持 **4 点连排透视切分（Homography）**、**弧形车位带样条切分**、**任意多边形异形车位**、**无划线弹性顺停带** 以及单顶点/单边拖拽微调。
   - **结构化机位命名与全量 CRUD**：支持机位新增、改名/改 ID、删除（可选解绑保留平面图车位为 `UNASSIGNED`），内置编码解析器（如 `5LV7` = `5号楼左侧竖着7个停车位`、`10BH2` = `10号楼后面横着2个停车位`、单机位多组 `10BV7+10BH2` + 拍摄地址备注，兼容 `left/right/front/backend` 与 `horizontal/vertical`）。
3. **拖拽式态势平面图工作室 + 车行/人行双路网寻位**
   - 支持上传小区平面图自动生成态势图，在 2F 平面图上绘制带单/双向语义的**行车环路**与**庭院人行步道**，以到单元门真实步行距离优先推荐主选与细分备选车位带，并支持自由拖拽/缩放/旋转与机位解绑/换绑。
4. **主流摄像头接入管理中心 + 多机位主备冷切冗余（16+ 品牌预设 · 1 主 + 最多 3 备）**
   - 内置海康威视、大华、萤石、乐橙、TP-Link/Tapo、宇视、华为好望、天地伟业、Reolink、Axis/韩华、小米/Aqara 桥接、ONVIF 通用、4G 太阳能抓拍机、手机 IP Webcam / ESP32-CAM、USB 直连等预设模板，支持局域网自动扫描、ONVIF SOAP 探测、HTTP Digest/Basic 抓图、RTSP/MJPEG/HLS 按需抽帧及平面图车位带一键绑定。
   - **Active-Standby 冷备防断流**：同一车位带可挂载 `1 主 + 最多 3 备` 摄像头（平时仅 `#0` 主机采集，备用机零拉流零算力开销）；主机连续断流达阈值时自动冷切至备用机并**热切换该槽位独立标定框与参考底板**，主机恢复后自动探测切回。
5. **黄金双层免备案车主端（`:18791` Siri 车载免手语音 + 自有大模型问答 + `:18790` 微信浮窗看图）**
   - **`:18791` 车载盲操与 AI 网关**：提供 `<20ms` 零大模型延迟双遍播报口（`/api/voice/brief`）、一键生成的 iOS Siri / CarPlay `.shortcut` 快捷指令（上车喊“嘿 Siri，小区车位”直接开口播报两遍）、复用自有大模型的自然语言问答口（`/api/voice/ask`，内置温和防白嫖领域护栏）以及可选官方普通微信 `openclaw-weixin` 扫码出站长轮询助手。
   - **`:18790` 微信浮窗只读平面图 + 免费 Cloudflare Tunnel**：支持通过 `docker compose --profile tunnel` 零成本、免公网 IP、免 ICP 备案映射 `:18790` 与 `:18791` 服务上千名小区车主，而 `:8790` 管理后台与内网摄像头完全物理隔离。
6. **三级视觉漏斗 + Azure AI Foundry 无缝接入**
   - **Stage 1（0算力）**：32×32 结构帧差拦截 80%+ 静止画面。
   - **Stage 2（<15ms CPU）**：本地 OpenCV 二维梯度能量 + 车窗动态范围对比度 + 参考模板路面残差 + 锥桶色彩检测。
   - **Stage 3（多模态慢路仲裁）**：原生适配 **Azure AI Foundry**（同时支持 `/openai/responses` Responses API 与 `/chat/completions` Target URI、`*.openai.azure.com`、`*.cognitiveservices.azure.com`、`*.services.ai.azure.com/models`）及 OpenAI / 通义千问 / Gemini / Ollama，精准识别**雪糕桶/电瓶车霸位（`BLOCKED`）**与复杂光影。
7. **双帧防抖状态机 + 悲观净余空位 + 在途软锁**
   - **宁少勿多防虚报**：首帧识别进入占用/占道待确认态时立即扣减净余空位，过滤过路车瞬态干扰的同时绝不虚报空位。
   - **在途软锁定**：推荐某片区后自动冻结在途名额，防止同单元前后两辆车被引向同一个孤立车位。

---

## 文档导航（部署 · 运维 · 技术说明）

- 📦 **[跨平台安装与部署手册 (`docs/DEPLOYMENT_GUIDE.md`)](docs/DEPLOYMENT_GUIDE.md)**：涵盖 **Docker Compose 多架构容器一键部署（含 Spot 竞价实例断电/抢占自动在线与 Cloudflare 免费隧道）**，以及 **macOS（Apple Silicon M 芯片）**、**Windows（x86_64 与 ARM64）**、**Linux（Ubuntu / Debian 等）** 三大环境从零部署。
- 🛠️ **[日常运维与排障速查手册 (`docs/OPERATIONS_GUIDE.md`)](docs/OPERATIONS_GUIDE.md)**：原生与 Docker 容器启动 / 关闭 / 重启 / 巡检 / 日志跟随、Spot 机器回收与断电自愈、多摄像头主备冷切与独立标定、SQLite WAL 在线热备份恢复及 1 分钟排障速查表。
- 📐 **[系统方案、架构设计与关键技术说明 (`docs/TECHNICAL_ARCHITECTURE.md`)](docs/TECHNICAL_ARCHITECTURE.md)**：三端口职责隔离架构（`:8790` + `:18790` + `:18791`）、Active-Standby 多摄像头冷切与按槽位标定热切换、Siri CarPlay + 自有大模型平滑防白嫖护栏 + 普通微信 `openclaw-weixin` 出站桥接、Cloudflare 免费隧道拓扑及 Spot 实例抗抢占设计。

---

## 快速启动（支持 Docker 容器与跨平台原生虚拟环境）

```bash
# 方式 A：Docker Compose 一键启动（推荐云端 Ubuntu / Spot 竞价实例 / 物业机）
./parking-ctl.sh docker-up
# 可选：连同免费 Cloudflare Tunnel 一键打通公网 HTTPS（0 元免备案服务 1,000+ 业主）：
# CLOUDFLARE_TUNNEL_TOKEN=你的Token ./parking-ctl.sh docker-up

# 方式 B：macOS / Linux / Windows 原生虚拟环境启动（Windows 使用 .\parking-ctl.ps1）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./parking-ctl.sh start && ./parking-ctl.sh status
```

启动后自动就绪 **三个安全隔离端口**：
1. 🖥️ **`http://127.0.0.1:8790`（管理与标定控制台，仅内网/本机）**：配置 Azure 模型、接入摄像头与主备冷切冗余槽位、空间标定及 `:18791` 访问口令/普通微信扫码配对。
2. 📱 **`http://<局域网IP或Cloudflare域名>:18790`（微信浮窗极简看图端，纯只读）**：每台手机独立记忆目的地单元，支持 **1遍 / 2遍 / 静音** 与 **持续播报**，单指平移、双指捏合缩放实时平面图。
3. 🎙️ **`http://<局域网IP或Cloudflare域名>:18791`（开车免手语音 · Siri CarPlay · 自有大模型问答门户）**：一键下载安装 iOS Siri / CarPlay `.shortcut` 快捷指令（上车喊 **“嘿 Siri，小区车位”** `<20ms` 车载音响播报两遍），支持麦克风口述问自有大模型与 2D 态势图速览。

## 运行自动化测试

```bash
python3 -m unittest discover -s tests -v
```

## 开源许可

本项目采用 [MIT License](LICENSE) 开源，欢迎各小区极客与业委会免费部署改造。
