#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# 邻里停 (Community Parking) 跨平台服务一键运维脚本 (macOS Apple Silicon/Intel & Linux)
# 用法:
#   原生进程模式: ./parking-ctl.sh {start|stop|restart|status|health|logs|foreground}
#   macOS 自启守护: ./parking-ctl.sh {install-launchd|uninstall-launchd}
#   Linux 自启守护: ./parking-ctl.sh {install-systemd|uninstall-systemd}
#   Docker / Spot 容器模式: ./parking-ctl.sh {docker-up|docker-down|docker-restart|docker-logs}
# ==============================================================================

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
  VENV_PY="${APP_DIR}/.venv/bin/python"
else
  VENV_PY="$(command -v python3 || command -v python || true)"
fi
HOST="${PARKING_HOST:-127.0.0.1}"
PORT="${PARKING_PORT:-8790}"
MOBILE_HOST="${PARKING_MOBILE_HOST:-0.0.0.0}"
MOBILE_PORT="${PARKING_MOBILE_PORT:-18790}"
VOICE_HOST="${PARKING_VOICE_HOST:-0.0.0.0}"
VOICE_PORT="${PARKING_VOICE_PORT:-18791}"
FTP_PORT="${PARKING_FTP_PORT:-2121}"
OS_NAME="$(uname -s)"

DATA_DIR="${APP_DIR}/data"
PID_FILE="${DATA_DIR}/parking-${PORT}.pid"
LOG_FILE="${DATA_DIR}/parking-${PORT}.log"
MAX_LOG_LINES=2000

PLIST_LABEL="com.community-parking.server"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
SYSTEMD_UNIT="community-parking.service"
SYSTEMD_USER_PATH="${HOME}/.config/systemd/user/${SYSTEMD_UNIT}"

mkdir -p "${DATA_DIR}"

rotate_log_if_needed() {
  if [[ -f "${LOG_FILE}" ]]; then
    local lines
    lines=$(wc -l < "${LOG_FILE}" | tr -d ' ')
    if (( lines > MAX_LOG_LINES )); then
      tail -n 1000 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
    fi
  fi
}

find_port_pid() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
  elif command -v ss >/dev/null 2>&1; then
    ss -lptn "sport = :${PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1 || true
  else
    true
  fi
}

probe_host() {
  if [[ "${HOST}" == "0.0.0.0" || "${HOST}" == "::" ]]; then
    echo "127.0.0.1"
  else
    echo "${HOST}"
  fi
}

probe_mobile_host() {
  if [[ "${MOBILE_HOST}" == "0.0.0.0" || "${MOBILE_HOST}" == "::" ]]; then
    echo "127.0.0.1"
  else
    echo "${MOBILE_HOST}"
  fi
}

probe_voice_host() {
  if [[ "${VOICE_HOST}" == "0.0.0.0" || "${VOICE_HOST}" == "::" ]]; then
    echo "127.0.0.1"
  else
    echo "${VOICE_HOST}"
  fi
}

detect_lan_ip() {
  "${VENV_PY}" -c "
import socket
ip = '127.0.0.1'
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('10.255.255.255', 1))
    ip = s.getsockname()[0]
    s.close()
except Exception:
    pass
print(ip)
" 2>/dev/null || echo "127.0.0.1"
}

get_running_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid=$(tr -d '[:space:]' < "${PID_FILE}")
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "${pid}"
      return 0
    fi
    rm -f "${PID_FILE}"
  fi
  local port_pid
  port_pid=$(find_port_pid)
  if [[ -n "${port_pid}" ]]; then
    echo "${port_pid}" > "${PID_FILE}"
    echo "${port_pid}"
    return 0
  fi
  return 1
}

wait_healthy() {
  local target_host mobile_target voice_target
  target_host="$(probe_host)"
  mobile_target="$(probe_mobile_host)"
  voice_target="$(probe_voice_host)"
  local retries=45
  for ((i = 1; i <= retries; i++)); do
    if curl --noproxy '*' -fsS -m 2 "http://${target_host}:${PORT}/api/health" >/dev/null 2>&1 \
      && curl --noproxy '*' -fsS -m 2 "http://${mobile_target}:${MOBILE_PORT}/api/health" >/dev/null 2>&1 \
      && curl --noproxy '*' -fsS -m 2 "http://${voice_target}:${VOICE_PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.4
  done
  return 1
}

cmd_start() {
  if pid=$(get_running_pid); then
    local lan_ip
    lan_ip="$(detect_lan_ip)"
    echo "✅ 服务已在运行 (PID: ${pid}) -> 控制台 :${PORT} | 微信看图 :${MOBILE_PORT} | Siri语音 :${VOICE_PORT}"
    cmd_health
    return 0
  fi

  rotate_log_if_needed
  echo "🚀 正在启动邻里停服务 (控制台 ${HOST}:${PORT}, 手机看图 ${MOBILE_HOST}:${MOBILE_PORT}, Siri语音网关 ${VOICE_HOST}:${VOICE_PORT}, FTP:${FTP_PORT})..."
  nohup "${VENV_PY}" "${APP_DIR}/run.py" \
    --host "${HOST}" \
    --port "${PORT}" \
    --mobile-host "${MOBILE_HOST}" \
    --mobile-port "${MOBILE_PORT}" \
    --voice-host "${VOICE_HOST}" \
    --voice-port "${VOICE_PORT}" \
    --ftp-port "${FTP_PORT}" \
    >> "${LOG_FILE}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${PID_FILE}"

  if wait_healthy; then
    local lan_ip
    lan_ip="$(detect_lan_ip)"
    echo "✅ 启动成功! PID=${new_pid}"
    echo "   🖥️  管理控制台 : http://${HOST}:${PORT}"
    echo "   📱 微信看图端 : http://${lan_ip}:${MOBILE_PORT}  (本机 http://127.0.0.1:${MOBILE_PORT})"
    echo "   🎙️  Siri语音端 : http://${lan_ip}:${VOICE_PORT}  (本机 http://127.0.0.1:${VOICE_PORT})"
    echo "   📡 内存 FTP   : ftp://${HOST}:${FTP_PORT}"
    echo "   📜 日志文件   : ${LOG_FILE}"
  else
    echo "❌ 启动超时，最近日志如下："
    tail -n 30 "${LOG_FILE}" || true
    exit 1
  fi
}

cmd_stop() {
  if ! pid=$(get_running_pid); then
    echo "ℹ️  服务当前未运行 (端口 ${PORT} 空闲)"
    return 0
  fi

  echo "🛑 正在停止服务 (PID: ${pid})..."
  kill "${pid}" 2>/dev/null || true
  for ((i = 1; i <= 15; i++)); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${PID_FILE}"
      echo "✅ 服务已优雅停止"
      return 0
    fi
    sleep 0.3
  done

  echo "⚠️  进程未及时退出，执行强制终止 (kill -9 ${pid})..."
  kill -9 "${pid}" 2>/dev/null || true
  rm -f "${PID_FILE}"
  echo "✅ 服务已停止"
}

cmd_status() {
  if pid=$(get_running_pid); then
    local rss_kb rss_mb lan_ip
    rss_kb=$(ps -o rss= -p "${pid}" 2>/dev/null | tr -d ' ' || echo "0")
    rss_mb=$(awk "BEGIN { printf \"%.1f\", ${rss_kb:-0} / 1024 }")
    lan_ip="$(detect_lan_ip)"
    echo "🟢 运行中 | PID: ${pid} | 物理内存(RSS): ${rss_mb} MB"
    echo "   🖥️  管理控制台 : http://${HOST}:${PORT}"
    echo "   📱 微信看图端 : http://${lan_ip}:${MOBILE_PORT}  (本机 http://127.0.0.1:${MOBILE_PORT})"
    echo "   🎙️  Siri语音端 : http://${lan_ip}:${VOICE_PORT}  (本机 http://127.0.0.1:${VOICE_PORT})"
    cmd_health
  else
    echo "⚪ 未运行 | 端口 ${PORT} / ${MOBILE_PORT} / ${VOICE_PORT} 未监听"
    return 1
  fi
}

cmd_health() {
  local target_host mobile_target voice_target resp mob_status voice_status
  target_host="$(probe_host)"
  mobile_target="$(probe_mobile_host)"
  voice_target="$(probe_voice_host)"
  if curl --noproxy '*' -fsS -m 2 "http://${mobile_target}:${MOBILE_PORT}/api/health" >/dev/null 2>&1; then
    mob_status="在线(:${MOBILE_PORT})"
  else
    mob_status="未响应(:${MOBILE_PORT})"
  fi
  if curl --noproxy '*' -fsS -m 2 "http://${voice_target}:${VOICE_PORT}/api/health" >/dev/null 2>&1; then
    voice_status="在线(:${VOICE_PORT})"
  else
    voice_status="未响应(:${VOICE_PORT})"
  fi
  if resp=$(curl --noproxy '*' -fsS -m 3 "http://${target_host}:${PORT}/api/overview" 2>/dev/null); then
    if [[ -n "${VENV_PY}" ]]; then
      "${VENV_PY}" -c "
import sys, json
d = json.loads(sys.stdin.read())
ps = d.get('privacy_stats', {})
used_kb = ps.get('cache_used_kb', ps.get('signature_cache_kb', 0.0))
budget_mb = ps.get('cache_budget_mb', 64.0)
human_cache = f'{used_kb/1024:.2f} MB' if used_kb >= 1024 else f'{used_kb:.1f} KB'
vlm_hits = ps.get('vlm_cache_hits', 0)
tpl_hits = ps.get('template_cache_hits', 0)
print(f\"   📊 概览: 总车位 {d.get('total_capacity')} | 净余空位 {d.get('total_effective_free')} | 手机看图 ${mob_status} | Siri语音 ${voice_status} | 落盘 {ps.get('raw_images_written_to_disk', 0)} | 瞬时销毁 {ps.get('frames_processed_in_ram', 0)} 帧 | 热快取 {human_cache}/{budget_mb:.0f}MB (命中: 模板{tpl_hits}/VLM{vlm_hits})\")
" <<< "${resp}"
    else
      echo "   📊 三端口健康状态: 管理台 在线(:${PORT}) | 手机看图 ${mob_status} | Siri语音 ${voice_status}"
    fi
  else
    echo "⚠️  健康检查未响应: http://${target_host}:${PORT}/api/health"
    return 1
  fi
}

cmd_foreground() {
  rotate_log_if_needed
  echo "$$" > "${PID_FILE}"
  exec "${VENV_PY}" "${APP_DIR}/run.py" \
    --host "${HOST}" \
    --port "${PORT}" \
    --mobile-host "${MOBILE_HOST}" \
    --mobile-port "${MOBILE_PORT}" \
    --voice-host "${VOICE_HOST}" \
    --voice-port "${VOICE_PORT}" \
    --ftp-port "${FTP_PORT}"
}

cmd_logs() {
  touch "${LOG_FILE}"
  echo "📜 正在跟随日志 ${LOG_FILE} (按 Ctrl+C 退出)..."
  tail -n 50 -f "${LOG_FILE}"
}

cmd_install_launchd() {
  mkdir -p "${HOME}/Library/LaunchAgents"
  cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${APP_DIR}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${VENV_PY}</string>
    <string>${APP_DIR}/run.py</string>
    <string>--host</string>
    <string>${HOST}</string>
    <string>--port</string>
    <string>${PORT}</string>
    <string>--mobile-host</string>
    <string>${MOBILE_HOST}</string>
    <string>--mobile-port</string>
    <string>${MOBILE_PORT}</string>
    <string>--voice-host</string>
    <string>${VOICE_HOST}</string>
    <string>--voice-port</string>
    <string>${VOICE_PORT}</string>
    <string>--ftp-port</string>
    <string>${FTP_PORT}</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_FILE}</string>
</dict>
</plist>
EOF
  cmd_stop >/dev/null 2>&1 || true
  launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
  launchctl load "${PLIST_PATH}"
  wait_healthy && echo "✅ 已注册 macOS 开机自启与崩溃自动拉起 (${PLIST_PATH})"
}

cmd_uninstall_launchd() {
  if [[ -f "${PLIST_PATH}" ]]; then
    launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
    rm -f "${PLIST_PATH}"
    echo "✅ 已移除 macOS 开机自启守护"
  else
    echo "ℹ️  尚未安装 launchd 守护"
  fi
}

cmd_install_systemd() {
  if [[ "${OS_NAME}" != "Linux" ]]; then
    echo "⚠️  install-systemd 仅适用于 Linux 环境 (当前系统: ${OS_NAME})"
    exit 1
  fi
  mkdir -p "$(dirname "${SYSTEMD_USER_PATH}")"
  cat > "${SYSTEMD_USER_PATH}" <<EOF
[Unit]
Description=Community Parking Smart Guidance Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_PY} ${APP_DIR}/run.py --host ${HOST} --port ${PORT} --mobile-host ${MOBILE_HOST} --mobile-port ${MOBILE_PORT} --voice-host ${VOICE_HOST} --voice-port ${VOICE_PORT} --ftp-port ${FTP_PORT}
Restart=always
RestartSec=3
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=default.target
EOF
  cmd_stop >/dev/null 2>&1 || true
  systemctl --user daemon-reload
  systemctl --user enable --now "${SYSTEMD_UNIT}"
  wait_healthy && echo "✅ 已注册 Linux systemd 用户级自启与崩溃守护 (${SYSTEMD_USER_PATH})"
  echo "💡 若需服务器重启后未登录也自动拉起，请执行一次: sudo loginctl enable-linger $(whoami)"
}

cmd_uninstall_systemd() {
  if command -v systemctl >/dev/null 2>&1 && [[ -f "${SYSTEMD_USER_PATH}" ]]; then
    systemctl --user disable --now "${SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    rm -f "${SYSTEMD_USER_PATH}"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    echo "✅ 已移除 Linux systemd 自启守护"
  else
    echo "ℹ️  尚未安装 systemd 用户级守护"
  fi
}

DOCKER_CMD=(docker)

ensure_docker_ready() {
  if ! command -v docker >/dev/null 2>&1; then
    if [[ "${OS_NAME}" == "Linux" ]] && command -v apt-get >/dev/null 2>&1; then
      echo "📦 检测到当前 Ubuntu/Debian 尚未安装 Docker，正在自动安装 Docker Engine 与 Compose v2..."
      if curl -fsSL --connect-timeout 10 -m 30 https://get.docker.com -o /tmp/get-docker.sh 2>/dev/null; then
        sudo sh /tmp/get-docker.sh && rm -f /tmp/get-docker.sh
      else
        sudo apt-get update && sudo apt-get install -y docker.io docker-buildx docker-compose-v2
      fi
      sudo systemctl enable --now docker
      sudo usermod -aG docker "${USER:-$(whoami)}" || true
      echo "✅ Docker 安装完成并已设为开机自启"
    else
      echo "❌ 未检测到 docker 命令，请先安装 Docker Desktop 或 Docker Engine。"
      exit 1
    fi
  fi
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
  else
    echo "🔐 当前 SSH 会话尚未刷新 docker 用户组，本次自动使用 sudo 调用 Docker..."
    DOCKER_CMD=(sudo --preserve-env=CLOUDFLARE_TUNNEL_TOKEN,PUBLIC_MOBILE_URL,PUBLIC_VOICE_URL,VOICE_GATEWAY_TOKEN,VOICE_ICLOUD_SHORTCUT_URL,PARKING_ADMIN_BIND,PARKING_PORT,PARKING_MOBILE_PORT,PARKING_VOICE_PORT,PARKING_FTP_PORT docker)
  fi
}

case "${1:-status}" in
  start)             cmd_start ;;
  stop)              cmd_stop ;;
  restart)           cmd_stop; cmd_start ;;
  status)            cmd_status ;;
  health)            cmd_health ;;
  logs)              cmd_logs ;;
  foreground)        cmd_foreground ;;
  install-launchd)   cmd_install_launchd ;;
  uninstall-launchd) cmd_uninstall_launchd ;;
  install-systemd)   cmd_install_systemd ;;
  uninstall-systemd) cmd_uninstall_systemd ;;
  docker-up)
    cd "${APP_DIR}"
    ensure_docker_ready
    if [[ ! -f "${APP_DIR}/.env" && -f "${APP_DIR}/.env.example" ]]; then
      cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
      echo "📝 已自动从 .env.example 初始化 .env 配置文件"
    fi
    CF_TOK="${CLOUDFLARE_TUNNEL_TOKEN:-}"
    if [[ -z "${CF_TOK}" && -f "${APP_DIR}/.env" ]]; then
      CF_TOK="$(grep -E '^CLOUDFLARE_TUNNEL_TOKEN=' "${APP_DIR}/.env" | tail -n 1 | cut -d'=' -f2- | tr -d '[:space:]\"'\''')"
    fi
    if [[ -n "${CF_TOK}" ]]; then
      CLOUDFLARE_TUNNEL_TOKEN="${CF_TOK}" "${DOCKER_CMD[@]}" compose --profile tunnel up -d --build
    else
      "${DOCKER_CMD[@]}" compose up -d --build
    fi
    wait_healthy && cmd_health
    ;;
  docker-down)
    cd "${APP_DIR}" && ensure_docker_ready && "${DOCKER_CMD[@]}" compose --profile tunnel down
    ;;
  docker-restart)
    cd "${APP_DIR}" && ensure_docker_ready && "${DOCKER_CMD[@]}" compose restart && wait_healthy && cmd_health
    ;;
  docker-logs)
    cd "${APP_DIR}" && ensure_docker_ready && "${DOCKER_CMD[@]}" compose logs -f --tail=80
    ;;
  sign-pack)
    # Pre-sign full multi-mode Ubuntu-Ready Apple Shortcut pack (`text`, `map`, `both`, `silent`, `ask` + `menu` + fixed units)
    BASE_ARG="${2:-https://voice.listingworker.com}"
    INC_UNITS="${3:-1}"
    "${VENV_PY}" -c "
import sys
from app.database import Database
from app.server import ParkingAppContext
ctx = ParkingAppContext(database=Database('data/parking.db'))
res = ctx.voice_gateway.pre_sign_domain_pack(base_url=sys.argv[1], include_fixed_units=(sys.argv[2] != '0'))
if not res.get('ok'):
    print('❌ 预签名包生成失败:', res.get('error'))
    sys.exit(1)
print(f\"✅ 已生成 Ubuntu Ready 多模式 Apple 官方签名包 ({res['count']} 个 .shortcut 文件):\")
print(f\"   目标地址 : {res['base_url']}\")
print(f\"   输出目录 : {res['dir']}\")
print(f\"   覆盖模式 : 仅文字(text) · 仅平面图(map) · 文字+平面图同屏(both) · 纯语音(silent) · 大模型问答(ask) + 单元直连/首运行选一次/动态菜单\")
" "${BASE_ARG}" "${INC_UNITS}"
    ;;
  sync-ubuntu)
    REMOTE_HOST="${2:-ericwang@40.82.129.122}"
    REMOTE_DIR="${3:-~/community-parking}"
    echo "🚀 正在同步代码、预签名包与本地已标定数据(data/) 至 ${REMOTE_HOST}:${REMOTE_DIR} ..."
    rsync -avz --progress \
      --exclude '.venv' \
      --exclude '__pycache__' \
      --exclude '.pytest_cache' \
      --exclude '.DS_Store' \
      --exclude 'data/*.pid' \
      --exclude 'data/*.log*' \
      --exclude 'data/shortcuts/apple_map_helper*' \
      "${APP_DIR}/" "${REMOTE_HOST}:${REMOTE_DIR}/"
    echo "✅ 同步完成！登录 Ubuntu 执行: cd ${REMOTE_DIR} && cp -n .env.example .env && ./parking-ctl.sh docker-up"
    ;;
  sign-shortcut)
    # Pre-sign universal Apple Shortcut (AEA1) on macOS into data/shortcuts/universal-signed.shortcut
    # so Docker / Ubuntu / Windows deployments mounting ./data can distribute signed .shortcut files directly.
    BASE_ARG="${2:-}"
    UNIT_ARG="${3:-8-1}"
    "${VENV_PY}" -c "
import sys
from app.voice_gateway import VoiceGatewayService
class _DummyCtx:
    pass
svc = VoiceGatewayService(_DummyCtx())
res = svc.pre_sign_universal_shortcut(default_base_url=sys.argv[1], default_unit_id=sys.argv[2])
if not res.get('ok'):
    print('❌ 签名失败:', res.get('error'))
    sys.exit(1)
print(f\"✅ 已生成 Apple 官方签名通用快捷指令 (AEA1, {res['bytes']} bytes): {res['path']}\")
print(f\"   默认网关地址: {res['default_base_url']} | 默认单元: {res['default_unit_id']}\")
print('   提示: 将 data/ 目录随 Docker 挂载至 Linux/Windows 云端后，车主点击下载即可直接导入，不再提示未签名！')
" "${BASE_ARG}" "${UNIT_ARG}"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|health|logs|foreground|sign-pack|sync-ubuntu|sign-shortcut|install-launchd|uninstall-launchd|install-systemd|uninstall-systemd|docker-up|docker-down|docker-restart|docker-logs}"
    exit 1
    ;;
esac
