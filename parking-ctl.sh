#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# 邻里停 (Community Parking) 跨平台服务一键运维脚本 (macOS Apple Silicon/Intel & Linux)
# 用法:
#   ./parking-ctl.sh {start|stop|restart|status|health|logs|foreground}
#   macOS 自启守护: ./parking-ctl.sh {install-launchd|uninstall-launchd}
#   Linux 自启守护: ./parking-ctl.sh {install-systemd|uninstall-systemd}
# ==============================================================================

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
  VENV_PY="${APP_DIR}/.venv/bin/python"
else
  VENV_PY="$(command -v python3 || command -v python)"
fi
HOST="${PARKING_HOST:-127.0.0.1}"
PORT="${PARKING_PORT:-8790}"
MOBILE_HOST="${PARKING_MOBILE_HOST:-0.0.0.0}"
MOBILE_PORT="${PARKING_MOBILE_PORT:-18790}"
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
  local target_host mobile_target
  target_host="$(probe_host)"
  mobile_target="$(probe_mobile_host)"
  local retries=20
  for ((i = 1; i <= retries; i++)); do
    if curl --noproxy '*' -fsS -m 2 "http://${target_host}:${PORT}/api/health" >/dev/null 2>&1 \
      && curl --noproxy '*' -fsS -m 2 "http://${mobile_target}:${MOBILE_PORT}/api/health" >/dev/null 2>&1; then
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
    echo "✅ 服务已在运行 (PID: ${pid}) -> 控制台 http://${HOST}:${PORT} | 手机寻位 http://${lan_ip}:${MOBILE_PORT}"
    cmd_health
    return 0
  fi

  rotate_log_if_needed
  echo "🚀 正在启动邻里停服务 (控制台 ${HOST}:${PORT}, 手机寻位 ${MOBILE_HOST}:${MOBILE_PORT}, FTP:${FTP_PORT})..."
  nohup "${VENV_PY}" "${APP_DIR}/run.py" \
    --host "${HOST}" \
    --port "${PORT}" \
    --mobile-host "${MOBILE_HOST}" \
    --mobile-port "${MOBILE_PORT}" \
    --ftp-port "${FTP_PORT}" \
    >> "${LOG_FILE}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${PID_FILE}"

  if wait_healthy; then
    local lan_ip
    lan_ip="$(detect_lan_ip)"
    echo "✅ 启动成功! PID=${new_pid}"
    echo "   🖥️  管理控制台 : http://${HOST}:${PORT}"
    echo "   📱 手机寻位端 : http://${lan_ip}:${MOBILE_PORT}  (本机 http://127.0.0.1:${MOBILE_PORT})"
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
    echo "   📱 手机寻位端 : http://${lan_ip}:${MOBILE_PORT}  (本机 http://127.0.0.1:${MOBILE_PORT})"
    cmd_health
  else
    echo "⚪ 未运行 | 端口 ${PORT} / ${MOBILE_PORT} 未监听"
    return 1
  fi
}

cmd_health() {
  local target_host mobile_target resp mob_status
  target_host="$(probe_host)"
  mobile_target="$(probe_mobile_host)"
  if curl --noproxy '*' -fsS -m 2 "http://${mobile_target}:${MOBILE_PORT}/api/health" >/dev/null 2>&1; then
    mob_status="在线(:${MOBILE_PORT})"
  else
    mob_status="未响应(:${MOBILE_PORT})"
  fi
  if resp=$(curl --noproxy '*' -fsS -m 3 "http://${target_host}:${PORT}/api/overview" 2>/dev/null); then
    "${VENV_PY}" -c "
import sys, json
d = json.loads(sys.stdin.read())
ps = d.get('privacy_stats', {})
used_kb = ps.get('cache_used_kb', ps.get('signature_cache_kb', 0.0))
budget_mb = ps.get('cache_budget_mb', 64.0)
human_cache = f'{used_kb/1024:.2f} MB' if used_kb >= 1024 else f'{used_kb:.1f} KB'
vlm_hits = ps.get('vlm_cache_hits', 0)
tpl_hits = ps.get('template_cache_hits', 0)
print(f\"   📊 概览: 总车位 {d.get('total_capacity')} | 净余空位 {d.get('total_effective_free')} | 手机端 ${mob_status} | 落盘 {ps.get('raw_images_written_to_disk', 0)} | 瞬时销毁 {ps.get('frames_processed_in_ram', 0)} 帧 | 热快取 {human_cache}/{budget_mb:.0f}MB (命中: 模板{tpl_hits}/VLM{vlm_hits})\")
" <<< "${resp}"
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
ExecStart=${VENV_PY} ${APP_DIR}/run.py --host ${HOST} --port ${PORT} --mobile-host ${MOBILE_HOST} --mobile-port ${MOBILE_PORT} --ftp-port ${FTP_PORT}
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
  *)
    echo "用法: $0 {start|stop|restart|status|health|logs|foreground|install-launchd|uninstall-launchd|install-systemd|uninstall-systemd}"
    exit 1
    ;;
esac
