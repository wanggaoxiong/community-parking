#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# 邻里停 (Community Parking) 8790 服务一键运维脚本
# 用法: ./parking-ctl.sh {start|stop|restart|status|logs|health|install-launchd|uninstall-launchd}
# ==============================================================================

APP_DIR="/Users/ericwang/Projects/community-parking"
VENV_PY="${APP_DIR}/.venv/bin/python"
HOST="${PARKING_HOST:-127.0.0.1}"
PORT="${PARKING_PORT:-8790}"
FTP_PORT="${PARKING_FTP_PORT:-2121}"

DATA_DIR="${APP_DIR}/data"
PID_FILE="${DATA_DIR}/parking-${PORT}.pid"
LOG_FILE="${DATA_DIR}/parking-${PORT}.log"
MAX_LOG_LINES=2000

PLIST_LABEL="com.community-parking.server"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"

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
  lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
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
  local retries=20
  for ((i = 1; i <= retries; i++)); do
    if curl --noproxy '*' -fsS -m 2 "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.4
  done
  return 1
}

cmd_start() {
  if pid=$(get_running_pid); then
    echo "✅ 服务已在运行 (PID: ${pid}) -> http://${HOST}:${PORT}"
    cmd_health
    return 0
  fi

  rotate_log_if_needed
  echo "🚀 正在启动邻里停服务 (${HOST}:${PORT}, FTP:${FTP_PORT})..."
  nohup "${VENV_PY}" "${APP_DIR}/run.py" \
    --host "${HOST}" \
    --port "${PORT}" \
    --ftp-port "${FTP_PORT}" \
    >> "${LOG_FILE}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${PID_FILE}"

  if wait_healthy; then
    echo "✅ 启动成功! PID=${new_pid}"
    echo "   Web 控制台 : http://${HOST}:${PORT}"
    echo "   内存 FTP   : ftp://${HOST}:${FTP_PORT}"
    echo "   日志文件   : ${LOG_FILE}"
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
    local rss_kb
    rss_kb=$(ps -o rss= -p "${pid}" 2>/dev/null | tr -d ' ' || echo "0")
    local rss_mb
    rss_mb=$(awk "BEGIN { printf \"%.1f\", ${rss_kb:-0} / 1024 }")
    echo "🟢 运行中 | PID: ${pid} | 物理内存(RSS): ${rss_mb} MB | 地址: http://${HOST}:${PORT}"
    cmd_health
  else
    echo "⚪ 未运行 | 端口 ${PORT} 未监听"
    return 1
  fi
}

cmd_health() {
  local resp
  if resp=$(curl --noproxy '*' -fsS -m 3 "http://${HOST}:${PORT}/api/overview" 2>/dev/null); then
    "${VENV_PY}" -c "
import sys, json
d = json.loads(sys.stdin.read())
ps = d.get('privacy_stats', {})
print(f\"   📊 概览: 总车位 {d.get('total_capacity')} | 净余空位 {d.get('total_effective_free')} | 原图落盘 {ps.get('raw_images_written_to_disk', 0)} | 累计内存处理帧 {ps.get('frames_processed_in_ram', 0)}\")
" <<< "${resp}"
  else
    echo "⚠️  健康检查未响应: http://${HOST}:${PORT}/api/health"
    return 1
  fi
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

case "${1:-status}" in
  start)             cmd_start ;;
  stop)              cmd_stop ;;
  restart)           cmd_stop; cmd_start ;;
  status)            cmd_status ;;
  health)            cmd_health ;;
  logs)              cmd_logs ;;
  install-launchd)   cmd_install_launchd ;;
  uninstall-launchd) cmd_uninstall_launchd ;;
  *)
    echo "用法: $0 {start|stop|restart|status|health|logs|install-launchd|uninstall-launchd}"
    exit 1
    ;;
esac
