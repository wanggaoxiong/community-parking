# syntax=docker/dockerfile:1
# ==============================================================================
# 邻里停 (Community Parking) 多架构生产容器镜像
# 原生支持: linux/amd64 (x86_64 云主机/Windows) & linux/arm64 (Mac M芯片/ARM Spot)
# 暴露服务:
#   - 8790  : 物业/志愿者管理与空间标定控制台
#   - 18790 : 手机极简寻位播报与实时平面图端 (可绑 Cloudflare Tunnel)
#   - 18791 : 开车免手语音 / Siri 快捷指令 / 自有大模型问答 / 普通微信助手网关
#   - 2121  : 纯内存零落盘摄像头 FTP 抓拍接收口
# ==============================================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 安装轻量系统依赖:
# - tini: PID 1 信号转发与僵尸进程回收，确保 Spot 实例抢占/重启 SIGTERM 毫秒级刷写 SQLite WAL
# - ffmpeg + libglib2.0-0: RTSP / ONVIF / OpenCV 跨平台拉流解码
# - fonts-noto-cjk: 容器内生成 2D 脱敏车位态势 PNG 时正确渲染中文楼栋与片区名
# - curl: 容器自愈 HEALTHCHECK 探针
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini \
      curl \
      ffmpeg \
      libgl1 \
      libglib2.0-0 \
      libsm6 \
      libxext6 \
      fonts-noto-cjk \
      tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY static /app/static
COPY run.py /app/run.py
COPY parking-ctl.sh /app/parking-ctl.sh

RUN mkdir -p /app/data && chmod +x /app/parking-ctl.sh

VOLUME ["/app/data"]

EXPOSE 8790 18790 18791 2121

# 同时探测 :8790 (控制台) + :18790 (微信H5看图) + :18791 (Siri免手语音网关)
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --noproxy '*' -fsS http://127.0.0.1:8790/api/health >/dev/null \
   && curl --noproxy '*' -fsS http://127.0.0.1:18790/api/health >/dev/null \
   && curl --noproxy '*' -fsS http://127.0.0.1:18791/api/health >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/run.py", \
     "--host", "0.0.0.0", "--port", "8790", \
     "--mobile-host", "0.0.0.0", "--mobile-port", "18790", \
     "--voice-host", "0.0.0.0", "--voice-port", "18791", \
     "--ftp-port", "2121"]
