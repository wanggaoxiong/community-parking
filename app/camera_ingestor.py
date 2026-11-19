"""Mainstream IP/NVR/ONVIF/Push/USB camera ingestion engine (Zero-Disk RAM-Only).

Supports:
- 16 mainstream camera vendors & community deployment presets (Hikvision, Dahua, TP-LINK/Tapo,
  EZVIZ, Imou, Uniview, Huawei HoloSens, Tiandy, Reolink, Axis, Xiaomi/HA/go2rtc bridge,
  Generic ONVIF, 4G Solar FTP/HTTP Push, Volunteer IP-Webcam/ESP32-CAM, USB UVC, Custom Stream).
- Active pull protocols strictly in RAM (`raw_images_written_to_disk == 0`):
  * HTTP/HTTPS CGI Snapshot (Digest Auth, Basic Auth, Bearer Token, query auth, LAN no-proxy)
  * RTSP / RTSPS / RTMP / HLS (.m3u8) single-frame extraction via OpenCV FFmpeg with TCP preflight
  * MJPEG multipart stream frame extraction
  * ONVIF SOAP 1.2 profile/URI auto-discovery (`GetDeviceInformation`, `GetProfiles`,
    `GetSnapshotUri`, `GetStreamUri`) + UDP WS-Discovery LAN scan
  * Local USB / UVC camera capture
- Passive push protocols:
  * Built-in RAM FTP server (`ftp://<server>:2121/<camera_id>/snap.jpg`)
  * HTTP/Webhook Snapshot Push (`POST /api/cameras/<camera_id>/snapshot`, optional `X-Camera-Token`)
- Bounded-concurrency `CameraIngestionScheduler` with backoff & memory caps.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import datetime
import hashlib
import ipaddress
import logging
import os
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("parking.camera_ingestor")

MAX_PULL_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB strict RAM ceiling per snapshot
MAX_MJPEG_SCAN_BYTES = 4 * 1024 * 1024   # 4 MB ring scan cap for MJPEG stream
MAX_FRAME_LONG_SIDE = 1920               # Downscale 4K/8MP frames in RAM immediately
MAX_CONCURRENT_PULLS = 2                 # Cap concurrent stream decodes to protect RAM

ACTIVE_PULL_PROTOCOLS = frozenset({
    "http_snapshot",
    "rtsp",
    "onvif",
    "mjpeg",
    "rtmp",
    "hls",
    "usb",
})
PASSIVE_PUSH_PROTOCOLS = frozenset({"ftp_push", "http_push"})
ALL_PROTOCOLS = ACTIVE_PULL_PROTOCOLS | PASSIVE_PUSH_PROTOCOLS

_FFMPEG_ENV_LOCK = threading.Lock()
_CAPTURE_SEM = threading.Semaphore(MAX_CONCURRENT_PULLS)
_CAPTURE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cam-grab")


CAMERA_VENDOR_PRESETS: List[Dict[str, Any]] = [
    {
        "vendor": "hikvision",
        "label": "海康威视 Hikvision",
        "subtitle": "商用 IPC 网络枪球机 · NVR 硬盘录像机多通道",
        "badge": "市占率第一",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "onvif", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/ISAPI/Streaming/channels/{hik_channel}/picture",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/Streaming/Channels/{hik_channel}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "海康 IPC 第 1 通道编码为 101(主)/102(子)；接海康 NVR 时将「通道号」填为对应路数（如第 3 路自动映射为 301/302）。HTTP ISAPI 抓图默认启用 Digest 摘要认证，最省带宽与内存。",
    },
    {
        "vendor": "dahua",
        "label": "大华股份 Dahua",
        "subtitle": "大华 IPC 网络摄像机 · NVR 多路录像机",
        "badge": "主流安防",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "onvif", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/cgi-bin/snapshot.cgi?channel={channel}",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/cam/realmonitor?channel={channel}&subtype={subtype_zero}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "大华 CGI 抓图默认支持 HTTP Digest 认证；接大华 NVR 时直接修改「通道号（1~32）」即可按路拉取对应机位画面。",
    },
    {
        "vendor": "tplink",
        "label": "普联 TP-LINK / Tapo / 水星",
        "subtitle": "社区性价比首选 · 商用安防 IPC 与家用 Tapo 窗台机",
        "badge": "社区高人气",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "onvif", "http_snapshot", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 2020,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/stream{subtype_one}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
            "http_snapshot": "http://{host}:{http_port}/jpg/image.jpg",
        },
        "tips": "TP-LINK / Tapo 主码流为 /stream1、子码流为 /stream2；ONVIF 服务端口通常为 2020（部分商用机型为 80）。Tapo 家用机请在 App「高级设置 → 摄像头账号」中开启第三方 RTSP/ONVIF 账号。",
    },
    {
        "vendor": "ezviz",
        "label": "萤石云 EZVIZ",
        "subtitle": "海康民用室外/窗台机（C3W / C6 / C8W 等）",
        "badge": "窗台常用",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "hls", "rtmp", "http_snapshot"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 20,
        },
        "templates": {
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/h264/ch{channel}/{stream_word}/av_stream",
            "hls": "{custom_url}",
            "rtmp": "{custom_url}",
        },
        "tips": "萤石局域网取流请在萤石云视频 App「设置 → 局域网设备预览」开启；默认用户名 admin，密码为机身底部贴纸上的 6 位大写字母「验证码」。亦支持填入萤石开放平台 HLS(.m3u8)/RTMP 直播地址。",
    },
    {
        "vendor": "imou",
        "label": "乐橙 Imou",
        "subtitle": "大华民用智能摄像机 · 窗台/户外云台机",
        "badge": "民用安防",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "onvif", "hls"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 20,
        },
        "templates": {
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/cam/realmonitor?channel={channel}&subtype={subtype_zero}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
            "hls": "{custom_url}",
        },
        "tips": "乐橙局域网 RTSP 默认用户名 admin，密码为设备「安全码」（机身标签 8 位字符）或在乐橙 App 中修改的设备密码。",
    },
    {
        "vendor": "uniview",
        "label": "宇视科技 Uniview (UNV)",
        "subtitle": "园区物业级 IPC · NVR 安防监控",
        "badge": "物业园区",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "onvif", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/LAPI/V1.0/Channels/{channel}/Media/Video/Streams/{subtype_one}/Snapshot",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/unicast/c{channel}/s{subtype_zero}/live",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "宇视 IPC/NVR 支持 LAPI 瞬时抓图与标准 RTSP `/unicast/c1/s1/live`（s0 主码流、s1 子码流）。",
    },
    {
        "vendor": "huawei_holosens",
        "label": "华为好望 HoloSens",
        "subtitle": "华为机器视觉软件定义摄像机 SDC",
        "badge": "园区智能",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "onvif", "http_snapshot"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/LiveMedia/ch{channel}/Media{subtype_one}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
            "http_snapshot": "https://{host}/Snapshot/1/RemoteImageCapture?ImageFormat=jpg",
        },
        "tips": "华为好望摄像机主码流为 Media1，子码流为 Media2；支持通过 ONVIF 一键探测获取精确通道 URI。",
    },
    {
        "vendor": "tiandy",
        "label": "天地伟业 Tiandy",
        "subtitle": "超星光全彩夜视 IPC · NVR 通道",
        "badge": "夜视强光",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "http_snapshot", "onvif", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/{channel}/{subtype_one}",
            "http_snapshot": "http://{host}:{http_port}/Snapshot/{channel}/RemoteImageCapture?ImageFormat=jpg",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "天地伟业支持标准 ONVIF（端口 80/8080）与 RTSP 拉流，夜间低照度车位场景建议启用子码流定时抽帧。",
    },
    {
        "vendor": "reolink",
        "label": "睿联 Reolink",
        "subtitle": "POE / Wi-Fi 户外枪球机 · 原生 HTTP Snap CGI",
        "badge": "极客优选",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "rtmp", "onvif", "ftp_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 8000,
            "username": "admin",
            "auth_mode": "none",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/cgi-bin/api.cgi?cmd=Snap&channel={channel_zero}&user={user_q}&password={pass_q}",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/h264Preview_{channel_pad2}_{stream_word}",
            "rtmp": "rtmp://{host}:1935/bcs/channel{channel_zero}_{stream_word}.bcs?channel={channel_zero}&stream={subtype_zero}&user={user_q}&password={pass_q}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "Reolink 需在客户端「网络设置 → 高级 → 服务器设置」中打开 HTTP/RTSP/ONVIF 开关（ONVIF 默认端口 8000）。",
    },
    {
        "vendor": "axis",
        "label": "安讯士 Axis / 韩华 Wisenet",
        "subtitle": "VAPIX 标准抓图接口 · 企业级网络摄像机",
        "badge": "国际标准",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "onvif"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "root",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/axis-cgi/jpg/image.cgi?camera={channel}",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/axis-media/media.amp?camera={channel}",
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "Axis VAPIX `/axis-cgi/jpg/image.cgi` 支持毫秒级内存抓拍与 Digest 认证。",
    },
    {
        "vendor": "xiaomi",
        "label": "小米米家 / Aqara / HomeKit 桥接",
        "subtitle": "经 go2rtc · Home Assistant · Scrypted · Frigate 接入",
        "badge": "智能家居",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "rtsp", "http_push"],
        "defaults": {
            "http_port": 1984,
            "rtsp_port": 8554,
            "onvif_port": 80,
            "username": "",
            "auth_mode": "none",
            "channel": 1,
            "stream_subtype": "sub",
            "stream_name": "xiaomi_window_cam",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/api/frame.jpeg?src={stream_name}",
            "rtsp": "rtsp://{auth}{host}:{rtsp_port}/{stream_name}",
        },
        "tips": "米家/Aqara 原生采用私有加密云，推荐通过开源 go2rtc（内置 xiaomi 插件，HTTP 1984 / RTSP 8554）或 Home Assistant `camera_proxy`（认证方式选 Bearer Token 填入长期访问令牌）无损接入。",
    },
    {
        "vendor": "onvif_generic",
        "label": "通用 ONVIF 标准协议设备",
        "subtitle": "支持任意兼容 ONVIF Profile S/T 的摄像机与录像机",
        "badge": "自动探测",
        "recommended_protocol": "onvif",
        "supported_protocols": ["onvif", "rtsp", "http_snapshot"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "admin",
            "auth_mode": "digest",
            "channel": 1,
            "stream_subtype": "sub",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "onvif": "http://{host}:{onvif_port}/onvif/device_service",
        },
        "tips": "点击「一键 ONVIF 探测」，系统会自动协商 80/2020/8000/8899 端口，提取设备型号、码流 Profile、抓图 URI 与 RTSP 地址。",
    },
    {
        "vendor": "solar_4g",
        "label": "4G 太阳能抓拍机 / 免公网主动推送",
        "subtitle": "摄像头无固定局域网 IP · 通过 FTP(2121) 或 HTTP 主动上送",
        "badge": "免穿透零运维",
        "recommended_protocol": "ftp_push",
        "supported_protocols": ["ftp_push", "http_push"],
        "defaults": {
            "poll_interval_sec": 30,
            "auth_mode": "none",
            "require_push_token": False,
        },
        "templates": {},
        "tips": "适合户外立杆 4G 物联网卡太阳能相机或跨网段志愿者相机：无需给相机做端口映射，在相机后台配置定时抓图上传至本服务内置纯内存 FTP（端口 2121）或 HTTP Webhook 即可。",
    },
    {
        "vendor": "volunteer_diy",
        "label": "旧手机 IP Webcam / ESP32-CAM",
        "subtitle": "业主闲置安卓/iOS 手机窗台机 · 开源低功耗固件",
        "badge": "零成本公益",
        "recommended_protocol": "http_snapshot",
        "supported_protocols": ["http_snapshot", "mjpeg", "http_push", "ftp_push"],
        "defaults": {
            "http_port": 8080,
            "rtsp_port": 554,
            "username": "",
            "auth_mode": "basic",
            "poll_interval_sec": 15,
        },
        "templates": {
            "http_snapshot": "http://{host}:{http_port}/shot.jpg",
            "mjpeg": "http://{host}:{http_port}/video",
        },
        "tips": "Android「IP摄像头 (IP Webcam)」默认抓拍地址为 `:8080/shot.jpg`（MJPEG 流为 `:8080/video`）；ESP32-CAM 默认抓拍为 `:80/capture`（流为 `:81/stream`）。",
    },
    {
        "vendor": "usb_uvc",
        "label": "本地 USB / UVC 免驱摄像头",
        "subtitle": "直接插在运行本服务的 Mac / Linux / 工控盒 USB 口",
        "badge": "即插即用",
        "recommended_protocol": "usb",
        "supported_protocols": ["usb"],
        "defaults": {
            "device_index": 0,
            "poll_interval_sec": 15,
        },
        "templates": {
            "usb": "usb://device/{device_index}",
        },
        "tips": "设备序号 0 通常为默认摄像头，外接 USB 摄像头一般为 0 或 1；每次抽帧后立即释放句柄与内存。",
    },
    {
        "vendor": "custom",
        "label": "自定义流 / 云平台直播地址",
        "subtitle": "支持任意 RTSP / RTSPS / RTMP / HLS(.m3u8) / MJPEG / HTTP 图片 URL",
        "badge": "全协议兼容",
        "recommended_protocol": "rtsp",
        "supported_protocols": ["rtsp", "http_snapshot", "mjpeg", "hls", "rtmp", "ftp_push", "http_push"],
        "defaults": {
            "http_port": 80,
            "rtsp_port": 554,
            "auth_mode": "auto",
            "rtsp_transport": "tcp",
            "poll_interval_sec": 15,
        },
        "templates": {
            "rtsp": "{custom_url}",
            "http_snapshot": "{custom_url}",
            "mjpeg": "{custom_url}",
            "hls": "{custom_url}",
            "rtmp": "{custom_url}",
        },
        "tips": "直接粘贴完整的流地址或抓图 URL，系统将在纯内存中完成解码抽帧与车位识别，绝不写入任何原始图片文件。",
    },
]

VENDOR_ALIASES: Dict[str, str] = {
    "huawei": "huawei_holosens",
    "holosens": "huawei_holosens",
    "xiaomi_bridge": "xiaomi",
    "mijia": "xiaomi",
    "aqara": "xiaomi",
    "solar_4g_push": "solar_4g",
    "usb_local": "usb_uvc",
    "custom_stream": "custom",
}

for _preset_entry in CAMERA_VENDOR_PRESETS:
    _d = _preset_entry.setdefault("defaults", {})
    if "http_port" in _d and "port" not in _d:
        _d["port"] = _d["http_port"]
    if "stream_subtype" in _d and "stream" not in _d:
        _d["stream"] = _d["stream_subtype"]
    if "device_index" in _d and "usb_index" not in _d:
        _d["usb_index"] = _d["device_index"]

VENDOR_PRESET_MAP: Dict[str, Dict[str, Any]] = {
    item["vendor"]: item for item in CAMERA_VENDOR_PRESETS
}

PROTOCOL_LABELS: Dict[str, str] = {
    "http_snapshot": "HTTP/CGI 定时抓图",
    "rtsp": "RTSP 视频流抽帧",
    "onvif": "ONVIF 标准协议自动取流",
    "mjpeg": "MJPEG 实时流抽帧",
    "hls": "HLS (.m3u8) 直播流抽帧",
    "rtmp": "RTMP 直播流抽帧",
    "ftp_push": "FTP 相机主动推送 (2121)",
    "http_push": "HTTP Webhook 主动推送",
    "usb": "USB/UVC 本地直连",
}
CAMERA_PROTOCOL_SPECS = PROTOCOL_LABELS


def format_local_ts(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


def get_vendor_presets_payload(ftp_port: int = 2121) -> Dict[str, Any]:
    return {
        "vendors": CAMERA_VENDOR_PRESETS,
        "protocols": [
            {
                "protocol": proto,
                "label": label,
                "mode": "pull" if proto in ACTIVE_PULL_PROTOCOLS else "push",
                "ingest_mode": "active_pull" if proto in ACTIVE_PULL_PROTOCOLS else "passive_push",
                "is_pull": proto in ACTIVE_PULL_PROTOCOLS,
            }
            for proto, label in PROTOCOL_LABELS.items()
        ],
        "ftp_server": {
            "port": int(ftp_port),
            "default_user": "camera",
            "default_pass_hint": "parking2026（或启动参数自定义）",
            "path_template": "/{camera_id}/snap.jpg",
        },
        "default_ingest_config": sanitize_ingest_config_for_public(None, camera_id="<camera_id>", ftp_port=ftp_port),
    }


def list_camera_presets_payload(ftp_port: int = 2121) -> Dict[str, Any]:
    return get_vendor_presets_payload(ftp_port=ftp_port)


def mask_url_credentials(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    # Mask user:password@host
    masked = re.sub(r"(://[^:/?#\s]+):([^@/\s]+)@", r"\1:******@", raw)
    # Mask query params password=... / token=... / secret=...
    masked = re.sub(
        r"([?&](?:password|passwd|pwd|token|secret|access_token)=)([^&#\s]+)",
        r"\1******",
        masked,
        flags=re.IGNORECASE,
    )
    return masked


MAX_STANDBY_SLOTS = 3


def _normalize_single_slot_fields(
    raw_cfg: Optional[Dict[str, Any]],
    existing_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize stream/auth connection fields for a single camera slot (primary or standby)."""
    prev = dict(existing_cfg or {})
    src = dict(raw_cfg or {})

    vendor = str(src.get("vendor") or prev.get("vendor") or "hikvision").strip().lower()
    vendor = VENDOR_ALIASES.get(vendor, vendor)
    if vendor not in VENDOR_PRESET_MAP:
        vendor = "custom"
    preset = VENDOR_PRESET_MAP[vendor]
    p_defaults = preset.get("defaults", {})

    protocol = str(
        src.get("protocol")
        or prev.get("protocol")
        or preset.get("recommended_protocol")
        or "http_snapshot"
    ).strip().lower()
    if protocol not in ALL_PROTOCOLS:
        protocol = str(preset.get("recommended_protocol") or "http_snapshot")

    def _int_val(key: str, fallback: int, lo: int, hi: int, alias_key: Optional[str] = None) -> int:
        if key in src and src.get(key) is not None and src.get(key) != "":
            val = src.get(key)
        elif alias_key and alias_key in src and src.get(alias_key) is not None and src.get(alias_key) != "":
            val = src.get(alias_key)
        elif key in prev and prev.get(key) is not None:
            val = prev.get(key)
        elif alias_key and alias_key in prev and prev.get(alias_key) is not None:
            val = prev.get(alias_key)
        else:
            val = p_defaults.get(key, fallback)
        try:
            iv = int(val)
        except (TypeError, ValueError):
            iv = fallback
        return max(lo, min(hi, iv))

    if "host" in src:
        host = str(src.get("host") or "").strip()
    elif "ip" in src:
        host = str(src.get("ip") or "").strip()
    else:
        host = str(prev.get("host", "")).strip()
    if "://" in host:
        try:
            parsed_h = urllib.parse.urlsplit(host)
            if parsed_h.hostname:
                host = parsed_h.hostname
        except Exception:
            pass

    username = str(
        src.get("username")
        if "username" in src
        else prev.get("username", p_defaults.get("username", ""))
    ).strip()

    clear_password = bool(src.get("clear_password", False))
    if clear_password:
        password = ""
    elif "password" in src and str(src.get("password") or "") != "":
        password = str(src.get("password"))
    else:
        password = str(prev.get("password") or "")

    clear_token = bool(src.get("clear_auth_token", False))
    if clear_token:
        auth_token = ""
    elif "auth_token" in src and str(src.get("auth_token") or "") != "":
        auth_token = str(src.get("auth_token")).strip()
    else:
        auth_token = str(prev.get("auth_token") or "")

    if bool(src.get("clear_push_token", False)):
        push_token = ""
    elif bool(src.get("regenerate_push_token", False)):
        push_token = secrets.token_urlsafe(18)
    elif "push_token" in src and str(src.get("push_token") or "").strip():
        push_token = str(src.get("push_token")).strip()
    elif prev.get("push_token"):
        push_token = str(prev.get("push_token"))
    else:
        push_token = ""

    if "require_push_token" in src:
        require_push_token = bool(src.get("require_push_token"))
    elif bool(src.get("clear_push_token", False)):
        require_push_token = False
    elif ("push_token" in src and str(src.get("push_token") or "").strip()) or bool(
        src.get("regenerate_push_token", False)
    ):
        require_push_token = True
    else:
        require_push_token = bool(prev.get("require_push_token", bool(push_token)))

    stream_subtype = str(
        src.get("stream_subtype")
        or src.get("stream")
        or prev.get("stream_subtype")
        or prev.get("stream")
        or p_defaults.get("stream_subtype")
        or "sub"
    ).strip().lower()
    if stream_subtype not in ("main", "sub"):
        stream_subtype = "sub"

    auth_mode = str(
        src.get("auth_mode")
        or prev.get("auth_mode")
        or p_defaults.get("auth_mode")
        or "auto"
    ).strip().lower()
    if auth_mode not in ("auto", "digest", "basic", "bearer", "none"):
        auth_mode = "auto"

    rtsp_transport = str(
        src.get("rtsp_transport")
        or prev.get("rtsp_transport")
        or p_defaults.get("rtsp_transport")
        or "tcp"
    ).strip().lower()
    if rtsp_transport not in ("tcp", "udp"):
        rtsp_transport = "tcp"

    enabled_raw = src.get("enabled", prev.get("enabled", protocol in PASSIVE_PUSH_PROTOCOLS))
    enabled = bool(enabled_raw)
    http_port_val = _int_val("http_port", int(p_defaults.get("http_port", 80)), 1, 65535, alias_key="port")
    dev_idx_val = _int_val("device_index", int(p_defaults.get("device_index", 0)), 0, 16, alias_key="usb_index")
    is_pull = protocol in ACTIVE_PULL_PROTOCOLS

    cfg: Dict[str, Any] = {
        "vendor": vendor,
        "vendor_label": preset["label"],
        "protocol": protocol,
        "protocol_label": PROTOCOL_LABELS.get(protocol, protocol),
        "mode": "active_pull" if is_pull else "passive_push",
        "is_pull": is_pull,
        "enabled": enabled,
        "host": host,
        "http_port": http_port_val,
        "port": http_port_val,
        "rtsp_port": _int_val("rtsp_port", int(p_defaults.get("rtsp_port", 554)), 1, 65535),
        "onvif_port": _int_val("onvif_port", int(p_defaults.get("onvif_port", 80)), 1, 65535),
        "username": username,
        "password": password,
        "auth_mode": auth_mode,
        "auth_token": auth_token,
        "channel": _int_val("channel", int(p_defaults.get("channel", 1)), 1, 128),
        "stream_subtype": stream_subtype,
        "stream": stream_subtype,
        "stream_name": str(
            src.get("stream_name")
            if "stream_name" in src
            else prev.get("stream_name", p_defaults.get("stream_name", ""))
        ).strip(),
        "rtsp_transport": rtsp_transport,
        "use_tls": bool(src.get("use_tls", prev.get("use_tls", False))),
        "verify_tls": bool(src.get("verify_tls", prev.get("verify_tls", False))),
        "device_index": dev_idx_val,
        "usb_index": dev_idx_val,
        "poll_interval_sec": _int_val(
            "poll_interval_sec", int(p_defaults.get("poll_interval_sec", 15)), 2, 3600
        ),
        "timeout_sec": _int_val("timeout_sec", 6, 2, 20),
        "custom_url": str(
            src.get("custom_url") if "custom_url" in src else prev.get("custom_url", "")
        ).strip(),
        "override_url": str(
            src.get("override_url")
            if "override_url" in src
            else (
                src.get("snapshot_url")
                or src.get("rtsp_url")
                or src.get("stream_url")
                or src.get("url")
                or prev.get("override_url", "")
            )
        ).strip(),
        "onvif_profile_token": str(
            src.get("onvif_profile_token")
            if "onvif_profile_token" in src
            else prev.get("onvif_profile_token", "")
        ).strip(),
        "onvif_snapshot_uri": str(
            src.get("onvif_snapshot_uri")
            if "onvif_snapshot_uri" in src
            else prev.get("onvif_snapshot_uri", "")
        ).strip(),
        "onvif_rtsp_uri": str(
            src.get("onvif_rtsp_uri")
            if "onvif_rtsp_uri" in src
            else prev.get("onvif_rtsp_uri", "")
        ).strip(),
        "require_push_token": require_push_token,
        "push_token": push_token,
        "auto_fallback_rtsp": bool(
            src.get("auto_fallback_rtsp", prev.get("auto_fallback_rtsp", True))
        ),
        "notes": str(src.get("notes") if "notes" in src else prev.get("notes", "")).strip()[:300],
    }

    urls = synthesize_ingest_urls(cfg)
    cfg["resolved_preview_masked"] = urls["masked_url"]
    return cfg


def is_slot_pull_ready(slot_cfg: Optional[Dict[str, Any]]) -> bool:
    """Return True if a normalized camera slot config has active-pull parameters configured."""
    if not isinstance(slot_cfg, dict):
        return False
    proto = str(slot_cfg.get("protocol") or "").strip().lower()
    if proto not in ACTIVE_PULL_PROTOCOLS:
        return False
    if proto == "usb":
        return True
    return bool(
        slot_cfg.get("host")
        or slot_cfg.get("override_url")
        or slot_cfg.get("custom_url")
        or slot_cfg.get("onvif_snapshot_uri")
        or slot_cfg.get("onvif_rtsp_uri")
    )


def normalize_ingest_config(
    raw_cfg: Optional[Dict[str, Any]],
    existing_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize and merge camera ingestion config (including Active-Standby cold failover slots)."""
    prev = dict(existing_cfg or {})
    src = dict(raw_cfg or {})

    cfg = _normalize_single_slot_fields(src, prev)
    cfg["primary_label"] = (
        str(src.get("primary_label") if "primary_label" in src else prev.get("primary_label", "主机位")).strip()[:36]
        or "主机位"
    )

    prev_standbys_raw = prev.get("standby_slots")
    prev_standbys: List[Dict[str, Any]] = (
        [dict(x) for x in prev_standbys_raw if isinstance(x, dict)]
        if isinstance(prev_standbys_raw, list)
        else []
    )
    prev_by_id: Dict[str, Dict[str, Any]] = {
        str(item.get("slot_id")): item for item in prev_standbys if item.get("slot_id")
    }

    raw_standbys = src.get("standby_slots") if "standby_slots" in src else prev_standbys
    norm_standbys: List[Dict[str, Any]] = []
    if isinstance(raw_standbys, list):
        for idx, raw_slot in enumerate(raw_standbys):
            if len(norm_standbys) >= MAX_STANDBY_SLOTS:
                break
            if not isinstance(raw_slot, dict):
                continue
            slot_id = str(raw_slot.get("slot_id") or "").strip() or f"standby_{idx + 1}"
            matched_prev = prev_by_id.get(slot_id)
            if matched_prev is None and idx < len(prev_standbys):
                matched_prev = prev_standbys[idx]
            slot_norm = _normalize_single_slot_fields(raw_slot, matched_prev)
            # Skip completely blank unconfigured rows
            if slot_norm.get("protocol") != "usb" and not (
                slot_norm.get("host")
                or slot_norm.get("custom_url")
                or slot_norm.get("override_url")
                or slot_norm.get("onvif_snapshot_uri")
                or slot_norm.get("onvif_rtsp_uri")
            ):
                continue
            slot_idx = len(norm_standbys) + 1
            slot_norm["slot_id"] = slot_id
            slot_norm["slot_index"] = slot_idx
            slot_norm["slot_label"] = (
                str(
                    raw_slot.get("slot_label")
                    if "slot_label" in raw_slot
                    else (
                        raw_slot.get("label")
                        if "label" in raw_slot
                        else (matched_prev or {}).get("slot_label", f"备机 #{slot_idx}")
                    )
                ).strip()[:36]
                or f"备机 #{slot_idx}"
            )
            norm_standbys.append(slot_norm)

    cfg["standby_slots"] = norm_standbys

    if "failover_enabled" in src and src.get("failover_enabled") is not None:
        failover_enabled = bool(src.get("failover_enabled"))
    elif "failover_enabled" in prev and "standby_slots" not in src:
        failover_enabled = bool(prev.get("failover_enabled")) and len(norm_standbys) > 0
    else:
        failover_enabled = len(norm_standbys) > 0
    cfg["failover_enabled"] = failover_enabled if len(norm_standbys) > 0 else False

    try:
        ft_raw = src.get("failover_threshold", prev.get("failover_threshold", 2))
        failover_threshold = max(1, min(10, int(ft_raw)))
    except (TypeError, ValueError):
        failover_threshold = 2
    cfg["failover_threshold"] = failover_threshold

    cfg["auto_restore_primary"] = bool(
        src.get("auto_restore_primary", prev.get("auto_restore_primary", True))
    )

    try:
        probe_raw = src.get("primary_probe_interval_sec", prev.get("primary_probe_interval_sec", 180))
        probe_sec = max(15, min(3600, int(probe_raw)))
    except (TypeError, ValueError):
        probe_sec = 180
    cfg["primary_probe_interval_sec"] = probe_sec

    try:
        act_raw = src.get("active_slot_index", prev.get("active_slot_index", 0))
        active_slot_index = max(0, min(len(norm_standbys), int(act_raw)))
    except (TypeError, ValueError):
        active_slot_index = 0
    cfg["active_slot_index"] = active_slot_index

    # Preserve per-slot calibration snapshots across config edits
    if bool(src.get("clear_calibration_profiles", False)):
        calib_profiles: Dict[str, Any] = {}
    else:
        calib_profiles = {}
        if isinstance(prev.get("calibration_profiles"), dict):
            calib_profiles.update(prev["calibration_profiles"])
        if isinstance(src.get("calibration_profiles"), dict):
            calib_profiles.update(src["calibration_profiles"])
    cfg["calibration_profiles"] = calib_profiles
    return cfg


def resolve_slot_ingest_config(
    cfg: Dict[str, Any],
    slot_index: Optional[int] = None,
) -> Tuple[int, str, Dict[str, Any]]:
    """Return (resolved_slot_index, slot_label, single_slot_cfg) for active-standby camera groups."""
    standbys = cfg.get("standby_slots")
    standby_list = standbys if isinstance(standbys, list) else []
    max_idx = len(standby_list)
    if slot_index is None:
        try:
            idx = int(cfg.get("active_slot_index") or 0)
        except (TypeError, ValueError):
            idx = 0
    else:
        try:
            idx = int(slot_index)
        except (TypeError, ValueError):
            idx = 0
    idx = max(0, min(max_idx, idx))
    if idx == 0:
        label = str(cfg.get("primary_label") or "主机位")
        primary_cfg = {
            k: v
            for k, v in cfg.items()
            if k not in ("standby_slots", "calibration_profiles")
        }
        return 0, label, primary_cfg

    slot_dict = dict(standby_list[idx - 1])
    slot_dict["enabled"] = bool(cfg.get("enabled", True))
    slot_dict["poll_interval_sec"] = int(cfg.get("poll_interval_sec") or slot_dict.get("poll_interval_sec") or 15)
    label = str(slot_dict.get("slot_label") or f"备机 #{idx}")
    return idx, label, slot_dict


def _sanitize_single_slot_for_public(slot_norm: Dict[str, Any]) -> Dict[str, Any]:
    has_pwd = bool(slot_norm.get("password"))
    has_tok = bool(slot_norm.get("auth_token"))
    has_push_tok = bool(slot_norm.get("push_token"))
    urls = synthesize_ingest_urls(slot_norm)
    pub = dict(slot_norm)
    pub["password"] = ""
    pub["auth_token"] = ""
    pub["push_token"] = ""
    pub["has_password"] = has_pwd
    pub["has_auth_token"] = has_tok
    pub["has_push_token"] = has_push_tok
    pub["custom_url"] = mask_url_credentials(pub.get("custom_url", ""))
    pub["override_url"] = mask_url_credentials(pub.get("override_url", ""))
    pub["onvif_snapshot_uri"] = mask_url_credentials(pub.get("onvif_snapshot_uri", ""))
    pub["onvif_rtsp_uri"] = mask_url_credentials(pub.get("onvif_rtsp_uri", ""))
    pub["resolved_preview_masked"] = urls["masked_url"]
    pub["fallback_preview_masked"] = urls.get("masked_fallback_url", "")
    return pub


def sanitize_ingest_config_for_public(
    cfg: Optional[Dict[str, Any]],
    camera_id: str = "",
    ftp_port: int = 2121,
) -> Dict[str, Any]:
    """Strip plaintext passwords/tokens before returning camera info over REST APIs."""
    if not cfg:
        return {
            "configured": False,
            "vendor": "solar_4g",
            "vendor_label": "被动推送 / 待配置协议",
            "protocol": "http_push",
            "protocol_label": PROTOCOL_LABELS["http_push"],
            "mode": "passive_push",
            "is_pull": False,
            "enabled": False,
            "host": "",
            "http_port": 80,
            "port": 80,
            "rtsp_port": 554,
            "onvif_port": 80,
            "username": "",
            "has_password": False,
            "has_auth_token": False,
            "has_push_token": False,
            "auth_mode": "auto",
            "channel": 1,
            "stream_subtype": "sub",
            "stream": "sub",
            "device_index": 0,
            "usb_index": 0,
            "poll_interval_sec": 15,
            "resolved_preview_masked": "",
            "primary_label": "主机位",
            "standby_slots": [],
            "standby_count": 0,
            "failover_enabled": False,
            "failover_threshold": 2,
            "auto_restore_primary": True,
            "primary_probe_interval_sec": 180,
            "active_slot_index": 0,
            "active_slot_label": "主机位",
            "calibrated_slots": [],
            "ftp_push_hint": f"ftp://<服务器IP>:{ftp_port}/{camera_id or 'CAM_ID'}/snap.jpg",
            "http_push_path": f"/api/cameras/{camera_id or 'CAM_ID'}/snapshot",
        }

    norm = normalize_ingest_config(cfg)
    pub = _sanitize_single_slot_for_public(norm)
    pub["configured"] = True
    raw_standbys = norm.get("standby_slots") or []
    pub["standby_slots"] = [
        _sanitize_single_slot_for_public(s) for s in raw_standbys if isinstance(s, dict)
    ]
    pub["standby_count"] = len(pub["standby_slots"])
    act_idx, act_label, _ = resolve_slot_ingest_config(norm)
    pub["active_slot_index"] = act_idx
    pub["active_slot_label"] = act_label
    calib_profiles = norm.get("calibration_profiles")
    pub.pop("calibration_profiles", None)
    pub["calibrated_slots"] = (
        sorted([int(k) for k in calib_profiles.keys() if str(k).isdigit()])
        if isinstance(calib_profiles, dict)
        else []
    )
    pub["ftp_push_hint"] = f"ftp://<服务器IP>:{ftp_port}/{camera_id or 'CAM_ID'}/snap.jpg"
    pub["http_push_path"] = f"/api/cameras/{camera_id or 'CAM_ID'}/snapshot"
    return pub


def synthesize_ingest_urls(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Build primary & fallback stream/snapshot URLs from vendor preset + config fields."""
    vendor = str(cfg.get("vendor") or "hikvision").lower()
    vendor = VENDOR_ALIASES.get(vendor, vendor)
    preset = VENDOR_PRESET_MAP.get(vendor, VENDOR_PRESET_MAP["custom"])
    protocol = str(cfg.get("protocol") or preset.get("recommended_protocol") or "http_snapshot")

    override_url = str(cfg.get("override_url") or "").strip()
    custom_url = str(cfg.get("custom_url") or "").strip()

    host = str(cfg.get("host") or "").strip()
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "")
    channel = max(1, int(cfg.get("channel") or 1))
    subtype = str(cfg.get("stream_subtype") or "sub").lower()
    use_tls = bool(cfg.get("use_tls", False))

    user_q = urllib.parse.quote(username, safe="")
    pass_q = urllib.parse.quote(password, safe="")
    if username and password:
        auth = f"{user_q}:{pass_q}@"
    elif username:
        auth = f"{user_q}@"
    else:
        auth = ""

    hik_channel = channel * 100 + (1 if subtype == "main" else 2)
    subtype_zero = 0 if subtype == "main" else 1
    subtype_one = 1 if subtype == "main" else 2
    stream_word = "main" if subtype == "main" else "sub"

    fmt_vars = {
        "host": host or "<摄像头IP>",
        "http_port": int(cfg.get("http_port") or 80),
        "rtsp_port": int(cfg.get("rtsp_port") or 554),
        "onvif_port": int(cfg.get("onvif_port") or 80),
        "auth": auth,
        "user_q": user_q,
        "pass_q": pass_q,
        "channel": channel,
        "channel_zero": max(0, channel - 1),
        "channel_pad2": f"{channel:02d}",
        "hik_channel": hik_channel,
        "subtype_zero": subtype_zero,
        "subtype_one": subtype_one,
        "stream_word": stream_word,
        "stream_name": str(cfg.get("stream_name") or "camera1").strip() or "camera1",
        "device_index": int(cfg.get("device_index") or 0),
        "custom_url": custom_url,
    }

    templates = preset.get("templates", {})

    def _apply_tls_scheme(u: str) -> str:
        if not use_tls or not u:
            return u
        if u.startswith("http://"):
            return "https://" + u[len("http://"):]
        if u.startswith("rtsp://"):
            return "rtsps://" + u[len("rtsp://"):]
        return u

    primary_url = ""
    fallback_url = ""

    if override_url:
        primary_url = _inject_credentials_if_needed(override_url, username, password, protocol)
    elif custom_url:
        primary_url = _inject_credentials_if_needed(custom_url, username, password, protocol)
    elif protocol == "onvif":
        snap_u = str(cfg.get("onvif_snapshot_uri") or "").strip()
        rtsp_u = str(cfg.get("onvif_rtsp_uri") or "").strip()
        if snap_u:
            primary_url = snap_u
            if rtsp_u:
                fallback_url = _inject_credentials_if_needed(rtsp_u, username, password, "rtsp")
        elif rtsp_u:
            primary_url = _inject_credentials_if_needed(rtsp_u, username, password, "rtsp")
        else:
            tmpl = templates.get("onvif") or "http://{host}:{onvif_port}/onvif/device_service"
            primary_url = _apply_tls_scheme(tmpl.format(**fmt_vars))
    elif protocol == "usb":
        primary_url = f"usb://device/{fmt_vars['device_index']}"
    elif protocol in PASSIVE_PUSH_PROTOCOLS:
        primary_url = ""
    else:
        tmpl = templates.get(protocol) or custom_url
        if tmpl:
            primary_url = _apply_tls_scheme(tmpl.format(**fmt_vars))
        if protocol == "http_snapshot" and "rtsp" in templates and cfg.get("auto_fallback_rtsp", True):
            fallback_url = _apply_tls_scheme(templates["rtsp"].format(**fmt_vars))

    resolved_primary = primary_url if host or override_url or custom_url or protocol == "usb" else ""
    return {
        "url": resolved_primary,
        "primary_url": resolved_primary,
        "fallback_url": fallback_url if host else "",
        "masked_url": mask_url_credentials(resolved_primary) if resolved_primary else "",
        "masked_fallback_url": mask_url_credentials(fallback_url) if (fallback_url and host) else "",
    }


def synthesize_camera_url(raw_cfg: Dict[str, Any]) -> Dict[str, str]:
    """Normalize raw camera ingest config and synthesize primary + masked URLs."""
    return synthesize_ingest_urls(normalize_ingest_config(raw_cfg))


def _inject_credentials_if_needed(url: str, username: str, password: str, protocol: str) -> str:
    raw = str(url or "").strip()
    if not raw or not username:
        return raw
    if protocol not in ("rtsp", "rtmp", "hls"):
        return raw
    try:
        sp = urllib.parse.urlsplit(raw)
        if sp.username or not sp.hostname:
            return raw
        user_q = urllib.parse.quote(username, safe="")
        pass_q = urllib.parse.quote(password or "", safe="")
        auth = f"{user_q}:{pass_q}@" if password else f"{user_q}@"
        netloc = f"{auth}{sp.hostname}"
        if sp.port:
            netloc += f":{sp.port}"
        return urllib.parse.urlunsplit((sp.scheme, netloc, sp.path, sp.query, sp.fragment))
    except Exception:
        return raw


def _is_bypass_proxy_host(hostname: str) -> bool:
    h = str(hostname or "").strip().lower()
    if not h:
        return True
    if h in ("localhost", "ip6-localhost") or h.endswith(".local") or h.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _decode_and_normalize_frame_ram(raw_bytes: bytes) -> Tuple[bytes, int, int]:
    """Validate image bytes in RAM, downscale if >1920px to protect memory, return JPEG bytes."""
    if not raw_bytes:
        raise RuntimeError("摄像头返回空图像数据")
    if len(raw_bytes) > MAX_PULL_IMAGE_BYTES:
        raise RuntimeError(f"单帧体积超限 ({len(raw_bytes)} bytes > {MAX_PULL_IMAGE_BYTES} bytes)")

    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    del arr
    if img is None or img.size == 0:
        raise RuntimeError("返回内容无法解码为有效图像（请检查抓图 URL、认证密码或通道号是否正确）")

    h, w = img.shape[:2]
    long_side = max(w, h)
    if long_side > MAX_FRAME_LONG_SIDE:
        scale = MAX_FRAME_LONG_SIDE / float(long_side)
        new_w = max(64, int(round(w * scale)))
        new_h = max(64, int(round(h * scale)))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        del img
        img = resized
        h, w = new_h, new_w
        ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        del img
        if not ok:
            raise RuntimeError("内存图像缩放编码失败")
        return enc.tobytes(), w, h

    # If already reasonably sized JPEG, avoid unnecessary re-encode; otherwise encode to clean JPEG
    if len(raw_bytes) >= 4 and raw_bytes[:2] == b"\xff\xd8" and len(raw_bytes) <= 2_500_000:
        del img
        return raw_bytes, w, h

    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    del img
    if not ok:
        raise RuntimeError("内存 JPEG 编码失败")
    return enc.tobytes(), w, h


def _build_http_opener(
    target_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "auto",
    verify_tls: bool = False,
) -> Tuple[urllib.request.OpenerDirector, str, Dict[str, str]]:
    """Build urllib opener supporting LAN proxy bypass, Digest + Basic Auth, and TLS options."""
    sp = urllib.parse.urlsplit(target_url)
    eff_user = username
    eff_pass = password
    clean_url = target_url

    if sp.username:
        eff_user = urllib.parse.unquote(sp.username)
        eff_pass = urllib.parse.unquote(sp.password or "") if sp.password is not None else eff_pass
        host_part = sp.hostname or ""
        if ":" in host_part and not host_part.startswith("["):
            host_part = f"[{host_part}]"
        if sp.port:
            host_part = f"{host_part}:{sp.port}"
        clean_url = urllib.parse.urlunsplit((sp.scheme, host_part, sp.path, sp.query, sp.fragment))

    handlers: List[Any] = []
    if _is_bypass_proxy_host(sp.hostname or ""):
        handlers.append(urllib.request.ProxyHandler({}))

    if sp.scheme.lower() == "https":
        if verify_tls:
            ssl_ctx = ssl.create_default_context()
        else:
            ssl_ctx = ssl._create_unverified_context()
        handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))

    extra_headers: Dict[str, str] = {
        "User-Agent": "CommunityParking-EdgeIngestor/2.0",
        "Accept": "image/jpeg, image/*, multipart/x-mixed-replace, */*",
    }

    if eff_user and auth_mode in ("auto", "digest", "basic"):
        origin = f"{sp.scheme}://{sp.hostname}"
        if sp.port:
            origin_with_port = f"{origin}:{sp.port}"
        else:
            origin_with_port = origin
        pwd_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pwd_mgr.add_password(None, clean_url, eff_user, eff_pass)
        pwd_mgr.add_password(None, origin_with_port, eff_user, eff_pass)
        pwd_mgr.add_password(None, origin, eff_user, eff_pass)
        if auth_mode in ("auto", "digest"):
            handlers.append(urllib.request.HTTPDigestAuthHandler(pwd_mgr))
        if auth_mode in ("auto", "basic"):
            handlers.append(urllib.request.HTTPBasicAuthHandler(pwd_mgr))
        if auth_mode == "basic":
            b64_cred = base64.b64encode(f"{eff_user}:{eff_pass}".encode("utf-8")).decode("ascii")
            extra_headers["Authorization"] = f"Basic {b64_cred}"

    opener = urllib.request.build_opener(*handlers)
    return opener, clean_url, extra_headers


def _extract_first_jpeg_from_stream(resp: Any, max_bytes: int = MAX_MJPEG_SCAN_BYTES) -> bytes:
    """Scan chunked HTTP response (single JPEG or multipart MJPEG) in RAM for first SOI..EOI."""
    buf = bytearray()
    soi = -1
    while len(buf) < max_bytes:
        chunk = resp.read(16384)
        if not chunk:
            break
        buf.extend(chunk)
        if soi < 0:
            soi = buf.find(b"\xff\xd8")
            if soi > 0:
                del buf[:soi]
                soi = 0
        if soi >= 0:
            eoi = buf.find(b"\xff\xd9", soi + 2)
            if eoi >= 0:
                return bytes(buf[soi : eoi + 2])
    return bytes(buf)


def grab_http_or_mjpeg_frame(
    url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "auto",
    auth_token: str = "",
    verify_tls: bool = False,
    timeout_sec: float = 6.0,
    is_mjpeg: bool = False,
) -> Tuple[bytes, int, int, Dict[str, Any]]:
    if not url:
        raise ValueError("未配置抓拍 HTTP 地址（请填写摄像头 IP 或完整抓图 URL）")

    opener, clean_url, headers = _build_http_opener(
        url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        verify_tls=verify_tls,
    )
    if auth_mode == "bearer" and auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif auth_token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(clean_url, headers=headers, method="GET")
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=max(2.0, float(timeout_sec))) as resp:
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            if is_mjpeg or "multipart/x-mixed-replace" in ctype:
                raw_bytes = _extract_first_jpeg_from_stream(resp, MAX_MJPEG_SCAN_BYTES)
            else:
                raw_bytes = resp.read(MAX_PULL_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                "摄像头认证失败 (HTTP 401)：请检查用户名、密码/设备验证码，或尝试切换 Digest / Basic 认证模式"
            ) from exc
        if exc.code == 403:
            raise RuntimeError(
                "摄像头拒绝访问 (HTTP 403)：请在相机后台开启 HTTP CGI / ISAPI / ONVIF 抓图权限"
            ) from exc
        if exc.code == 404:
            raise RuntimeError(
                f"抓图接口路径不存在 (HTTP 404)：该型号可能未启用 CGI 抓图，建议切换为「RTSP 视频流抽帧」或「ONVIF 自动探测」"
            ) from exc
        raise RuntimeError(f"HTTP 抓拍请求返回错误状态码: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接摄像头 HTTP 接口: {exc.reason}") from exc
    except socket.timeout as exc:
        raise RuntimeError(f"连接摄像头超时 ({timeout_sec:.1f}s)，请检查 IP 与网络连通性") from exc

    jpeg_bytes, w, h = _decode_and_normalize_frame_ram(raw_bytes)
    del raw_bytes
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return (
        jpeg_bytes,
        w,
        h,
        {
            "transport": "mjpeg" if is_mjpeg else "http_snapshot",
            "latency_ms": latency_ms,
            "url_masked": mask_url_credentials(url),
        },
    )


def _tcp_preflight(url: str, timeout_sec: float) -> None:
    """Fast TCP handshake check for rtsp:// and rtmp:// URLs so unreachable hosts fail in <2s."""
    sp = urllib.parse.urlsplit(url)
    scheme = sp.scheme.lower()
    if scheme not in ("rtsp", "rtsps", "rtmp", "rtmps"):
        return
    host = sp.hostname
    if not host:
        raise ValueError("流地址缺少有效主机名或 IP")
    default_port = 322 if scheme == "rtsps" else (554 if scheme == "rtsp" else 1935)
    port = sp.port or default_port
    sock_timeout = min(2.5, max(1.0, float(timeout_sec) * 0.5))
    try:
        with socket.create_connection((host, port), timeout=sock_timeout):
            return
    except OSError as exc:
        raise RuntimeError(
            f"无法连接摄像头流端口 {host}:{port} ({exc.strerror or exc}) —— 请确认摄像头在线、端口正确且已开启 {scheme.upper()} 服务"
        ) from exc


def _opencv_capture_worker(
    source: Any,
    rtsp_transport: str,
    timeout_sec: float,
    is_usb: bool = False,
) -> np.ndarray:
    us_timeout = int(max(2.0, float(timeout_sec)) * 1_000_000)
    cap = None
    try:
        if is_usb:
            cap = cv2.VideoCapture(int(source))
        else:
            opts = (
                f"rtsp_transport;{rtsp_transport}"
                f"|stimeout;{us_timeout}"
                f"|rw_timeout;{us_timeout}"
                f"|fflags;nobuffer"
                f"|flags;low_delay"
            )
            with _FFMPEG_ENV_LOCK:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
                cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
            if cap is not None:
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

        if cap is None or not cap.isOpened():
            raise RuntimeError("视频流打开失败（请核对流路径、账号密码或码流编码格式）")

        frame = None
        # Read up to 3 frames so initial black/corrupt decoder warmup frame is skipped if needed
        for attempt in range(3):
            ok, candidate = cap.read()
            if ok and candidate is not None and candidate.size > 0:
                frame = candidate
                if attempt >= 1 or float(np.mean(candidate)) > 2.0:
                    break
        if frame is None:
            raise RuntimeError("连接已建立但未能解码出有效视频帧（建议切换为子码流 H.264 格式重试）")
        return frame
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def grab_opencv_stream_frame(
    url_or_device: str,
    rtsp_transport: str = "tcp",
    timeout_sec: float = 6.0,
    is_usb: bool = False,
) -> Tuple[bytes, int, int, Dict[str, Any]]:
    t0 = time.perf_counter()
    if not is_usb:
        if not url_or_device:
            raise ValueError("未配置视频流地址")
        _tcp_preflight(url_or_device, timeout_sec)
        usb_idx = 0
    else:
        m = re.search(r"(\d+)$", str(url_or_device))
        usb_idx = int(m.group(1)) if m else 0

    acquired = _CAPTURE_SEM.acquire(timeout=max(2.0, float(timeout_sec)))
    if not acquired:
        raise RuntimeError("当前并发视频解码通道繁忙，已触发内存保护限流，请稍后重试")
    try:
        fut = _CAPTURE_POOL.submit(
            _opencv_capture_worker,
            usb_idx if is_usb else url_or_device,
            rtsp_transport,
            timeout_sec,
            is_usb,
        )
        try:
            frame = fut.result(timeout=max(2.5, float(timeout_sec) + 1.5))
        except FuturesTimeoutError as exc:
            raise RuntimeError(
                f"视频流拉取超时 ({timeout_sec:.1f}s)，已中断等待以保护系统内存"
            ) from exc

        h, w = frame.shape[:2]
        long_side = max(w, h)
        if long_side > MAX_FRAME_LONG_SIDE:
            scale = MAX_FRAME_LONG_SIDE / float(long_side)
            w = max(64, int(round(w * scale)))
            h = max(64, int(round(h * scale)))
            resized = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            del frame
            frame = resized

        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        del frame
        if not ok:
            raise RuntimeError("视频帧内存 JPEG 编码失败")
        jpeg_bytes = enc.tobytes()
        del enc
    finally:
        _CAPTURE_SEM.release()

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return (
        jpeg_bytes,
        w,
        h,
        {
            "transport": "usb" if is_usb else f"opencv_stream({rtsp_transport})",
            "latency_ms": latency_ms,
            "url_masked": f"usb://device/{usb_idx}" if is_usb else mask_url_credentials(url_or_device),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python ONVIF SOAP 1.2 Probe & WS-Discovery LAN Scanner
# ─────────────────────────────────────────────────────────────────────────────

def _build_wsse_header_xml(username: str, password: str) -> str:
    if not username:
        return ""
    nonce_raw = os.urandom(16)
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest_bytes = hashlib.sha1(
        nonce_raw + created.encode("utf-8") + (password or "").encode("utf-8")
    ).digest()
    pwd_digest = base64.b64encode(digest_bytes).decode("ascii")
    nonce_b64 = base64.b64encode(nonce_raw).decode("ascii")
    esc_user = (
        username.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        '<s:Header>'
        '<Security s:mustUnderstand="1" '
        'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        '<UsernameToken>'
        f'<Username>{esc_user}</Username>'
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f'{pwd_digest}</Password>'
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f'{nonce_b64}</Nonce>'
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f'{created}</Created>'
        '</UsernameToken>'
        '</Security>'
        '</s:Header>'
    )


def _onvif_soap_call(
    service_url: str,
    body_inner_xml: str,
    username: str = "",
    password: str = "",
    timeout_sec: float = 3.5,
) -> ET.Element:
    header_xml = _build_wsse_header_xml(username, password)
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f'{header_xml}'
        f'<s:Body>{body_inner_xml}</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    opener, clean_url, headers = _build_http_opener(
        service_url,
        username=username,
        password=password,
        auth_mode="auto",
        verify_tls=False,
    )
    headers["Content-Type"] = "application/soap+xml; charset=utf-8"
    req = urllib.request.Request(clean_url, data=envelope, headers=headers, method="POST")
    with opener.open(req, timeout=max(1.5, float(timeout_sec))) as resp:
        xml_bytes = resp.read(512 * 1024)
    root = ET.fromstring(xml_bytes)
    # Check SOAP Fault
    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local == "Fault":
            reasons = [
                (r.text or "").strip()
                for r in elem.iter()
                if r.tag.split("}")[-1] in ("Text", "faultstring") and (r.text or "").strip()
            ]
            raise RuntimeError(
                f"ONVIF 拒绝请求: {' / '.join(reasons) if reasons else '请核对 ONVIF 账号密码或相机系统时间同步'}"
            )
    return root


def _xml_find_local_text(elem: ET.Element, local_name: str) -> str:
    for child in elem.iter():
        if child.tag.split("}")[-1] == local_name and child.text:
            return child.text.strip()
    return ""


def _infer_vendor_from_text(hint: str) -> str:
    low = str(hint or "").lower()
    if any(k in low for k in ("hikvision", "hik", "ds-2cd", "ds-7")):
        return "hikvision"
    if any(k in low for k in ("dahua", "dh-ipc", "imou")):
        return "imou" if "imou" in low else "dahua"
    if any(k in low for k in ("tp-link", "tplink", "tapo", "mercury", "tl-ipc")):
        return "tplink"
    if "ezviz" in low:
        return "ezviz"
    if any(k in low for k in ("uniview", "unv", "ipc2", "ipc3")):
        return "uniview"
    if any(k in low for k in ("huawei", "holosens")):
        return "huawei_holosens"
    if "tiandy" in low:
        return "tiandy"
    if "reolink" in low:
        return "reolink"
    if any(k in low for k in ("axis", "hanwha", "wisenet")):
        return "axis"
    return "onvif_generic"


def probe_onvif_device(
    host: str,
    port: Optional[int] = None,
    username: str = "admin",
    password: str = "",
    use_tls: bool = False,
    timeout_sec: float = 3.5,
) -> Dict[str, Any]:
    """Probe ONVIF camera/NVR to extract device model, profiles, Snapshot URI & RTSP Stream URI."""
    clean_host = str(host or "").strip()
    if not clean_host:
        raise ValueError("请先填写摄像头的局域网 IP 地址")
    if "://" in clean_host:
        sp = urllib.parse.urlsplit(clean_host)
        clean_host = sp.hostname or clean_host
        if sp.port and not port:
            port = sp.port

    scheme = "https" if use_tls else "http"
    candidate_ports: List[int] = []
    if port:
        candidate_ports.append(int(port))
    for p in (80, 2020, 8000, 8899, 8080):
        if p not in candidate_ports:
            candidate_ports.append(p)

    device_service_url = ""
    active_port = candidate_ports[0]
    dev_info: Dict[str, str] = {}
    last_err: Optional[Exception] = None

    for cand_port in candidate_ports:
        url = f"{scheme}://{clean_host}:{cand_port}/onvif/device_service"
        try:
            root = _onvif_soap_call(
                url,
                "<tds:GetDeviceInformation/>",
                username=username,
                password=password,
                timeout_sec=timeout_sec,
            )
            mfr_val = _xml_find_local_text(root, "Manufacturer")
            mdl_val = _xml_find_local_text(root, "Model")
            fw_val = _xml_find_local_text(root, "FirmwareVersion")
            sn_val = _xml_find_local_text(root, "SerialNumber")
            dev_info = {
                "manufacturer": mfr_val,
                "model": mdl_val,
                "firmware": fw_val,
                "serial_number": sn_val,
                "Manufacturer": mfr_val,
                "Model": mdl_val,
                "FirmwareVersion": fw_val,
                "SerialNumber": sn_val,
            }
            device_service_url = url
            active_port = cand_port
            break
        except Exception as exc:
            last_err = exc
            # Still test GetCapabilities if GetDeviceInformation requires auth or differs
            try:
                cap_root = _onvif_soap_call(
                    url,
                    "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>",
                    username=username,
                    password=password,
                    timeout_sec=timeout_sec,
                )
                if _xml_find_local_text(cap_root, "XAddr"):
                    device_service_url = url
                    active_port = cand_port
                    break
            except Exception:
                continue

    if not device_service_url:
        raise RuntimeError(
            f"未能通过 ONVIF 连接 {clean_host}（已探测端口 {candidate_ports[:4]}）：{last_err or '请检查相机是否开启 ONVIF 服务及独立 ONVIF 账号'}"
        )

    # Resolve Media service XAddr
    media_url = f"{scheme}://{clean_host}:{active_port}/onvif/media_service"
    try:
        cap_root = _onvif_soap_call(
            device_service_url,
            "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>",
            username=username,
            password=password,
            timeout_sec=timeout_sec,
        )
        for elem in cap_root.iter():
            if elem.tag.split("}")[-1] == "Media":
                xaddr = _xml_find_local_text(elem, "XAddr")
                if xaddr:
                    media_url = xaddr
                    break
    except Exception:
        pass

    # Query Profiles
    prof_root = _onvif_soap_call(
        media_url,
        "<trt:GetProfiles/>",
        username=username,
        password=password,
        timeout_sec=timeout_sec,
    )
    profiles: List[Dict[str, Any]] = []
    for elem in prof_root.iter():
        if elem.tag.split("}")[-1] == "Profiles":
            token = elem.attrib.get("token") or ""
            if not token:
                continue
            pname = _xml_find_local_text(elem, "Name") or token
            w_txt = _xml_find_local_text(elem, "Width")
            h_txt = _xml_find_local_text(elem, "Height")
            enc_txt = _xml_find_local_text(elem, "Encoding")
            profiles.append(
                {
                    "token": token,
                    "name": pname,
                    "resolution": f"{w_txt}x{h_txt}" if w_txt and h_txt else "",
                    "encoding": enc_txt,
                }
            )

    # Fetch SnapshotUri & StreamUri for up to first 4 profiles
    for prof in profiles[:4]:
        tok_esc = prof["token"].replace("&", "&amp;").replace("<", "&lt;")
        snap_uri = ""
        rtsp_uri = ""
        try:
            s_root = _onvif_soap_call(
                media_url,
                f"<trt:GetSnapshotUri><trt:ProfileToken>{tok_esc}</trt:ProfileToken></trt:GetSnapshotUri>",
                username=username,
                password=password,
                timeout_sec=timeout_sec,
            )
            snap_uri = _xml_find_local_text(s_root, "Uri")
        except Exception:
            snap_uri = ""

        try:
            st_root = _onvif_soap_call(
                media_url,
                "<trt:GetStreamUri>"
                "<trt:StreamSetup>"
                "<tt:Stream>RTP-Unicast</tt:Stream>"
                "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                "</trt:StreamSetup>"
                f"<trt:ProfileToken>{tok_esc}</trt:ProfileToken>"
                "</trt:GetStreamUri>",
                username=username,
                password=password,
                timeout_sec=timeout_sec,
            )
            rtsp_uri = _xml_find_local_text(st_root, "Uri")
        except Exception:
            rtsp_uri = ""

        prof["snapshot_uri"] = snap_uri
        prof["snapshot_uri_masked"] = mask_url_credentials(snap_uri)
        prof["rtsp_uri"] = rtsp_uri
        prof["rtsp_uri_masked"] = mask_url_credentials(rtsp_uri)

    # Choose preferred profile (substream if available for low-memory recognition, else first)
    chosen = profiles[0] if profiles else {}
    if len(profiles) >= 2:
        chosen = profiles[1] if (profiles[1].get("snapshot_uri") or profiles[1].get("rtsp_uri")) else profiles[0]

    inferred_vendor = _infer_vendor_from_text(
        f"{dev_info.get('manufacturer', '')} {dev_info.get('model', '')}"
    )

    return {
        "host": clean_host,
        "onvif_port": active_port,
        "device_service_url": device_service_url,
        "media_service_url": media_url,
        "device_info": dev_info,
        "inferred_vendor": inferred_vendor,
        "inferred_vendor_label": VENDOR_PRESET_MAP.get(
            inferred_vendor, VENDOR_PRESET_MAP["onvif_generic"]
        )["label"],
        "profiles": [
            {
                "token": p["token"],
                "name": p["name"],
                "resolution": p["resolution"],
                "encoding": p["encoding"],
                "snapshot_uri": p.get("snapshot_uri", ""),
                "snapshot_uri_masked": p.get("snapshot_uri_masked", ""),
                "rtsp_uri": p.get("rtsp_uri", ""),
                "rtsp_uri_masked": p.get("rtsp_uri_masked", ""),
            }
            for p in profiles
        ],
        "recommended": {
            "profile_token": chosen.get("token", ""),
            "snapshot_uri": chosen.get("snapshot_uri", ""),
            "rtsp_uri": chosen.get("rtsp_uri", ""),
        },
    }


def probe_onvif_camera(
    host: str,
    onvif_port: int = 80,
    username: str = "admin",
    password: str = "",
    use_tls: bool = False,
    timeout_sec: float = 3.5,
) -> Dict[str, Any]:
    """Safe REST-friendly wrapper around `probe_onvif_device`."""
    try:
        res = probe_onvif_device(
            host=host,
            port=onvif_port,
            username=username,
            password=password,
            use_tls=use_tls,
            timeout_sec=timeout_sec,
        )
        profs = res.get("profiles") or []
        rec = res.get("recommended") or {}
        main_rtsp = profs[0].get("rtsp_uri", "") if profs else rec.get("rtsp_uri", "")
        sub_rtsp = rec.get("rtsp_uri", "")
        return {
            "ok": True,
            **res,
            "suggested_vendor": res.get("inferred_vendor", "onvif_generic"),
            "primary_snapshot_url": mask_url_credentials(rec.get("snapshot_uri", "")),
            "primary_rtsp_url": mask_url_credentials(main_rtsp),
            "sub_rtsp_url": mask_url_credentials(sub_rtsp),
            "raw_snapshot_uri": rec.get("snapshot_uri", ""),
            "raw_rtsp_uri": sub_rtsp or main_rtsp,
        }
    except Exception as exc:
        return {
            "ok": False,
            "host": str(host or "").strip(),
            "onvif_port": int(onvif_port or 80),
            "error": str(exc),
        }


def discover_lan_cameras(
    target_hosts: Optional[List[str]] = None,
    timeout_sec: float = 1.4,
) -> List[Dict[str, Any]]:
    """Discover ONVIF cameras on local LAN via WS-Discovery UDP multicast + optional IP port probe."""
    discovered: Dict[str, Dict[str, Any]] = {}

    probe_msg = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        '<e:Header>'
        f'<w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>'
        '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        '<w:Action a:mustUnderstand="true" xmlns:a="http://www.w3.org/2003/05/soap-envelope">'
        'http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
        '</e:Header>'
        '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>'
        '</e:Envelope>'
    ).encode("utf-8")

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.35)
        sock.sendto(probe_msg, ("239.255.255.250", 3702))
        deadline = time.monotonic() + max(0.5, min(3.0, float(timeout_sec)))
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            ip = addr[0]
            xml_txt = data.decode("utf-8", errors="ignore")
            xaddrs_match = re.search(r"<[^:>]*:?XAddrs[^>]*>([^<]+)</", xml_txt)
            scopes_match = re.search(r"<[^:>]*:?Scopes[^>]*>([^<]+)</", xml_txt)
            xaddrs = (xaddrs_match.group(1).strip().split() if xaddrs_match else [])
            scopes = urllib.parse.unquote(scopes_match.group(1).strip()) if scopes_match else ""
            onvif_port = 80
            service_url = ""
            for xa in xaddrs:
                if xa.startswith("http"):
                    service_url = xa
                    sp = urllib.parse.urlsplit(xa)
                    if sp.hostname:
                        ip = sp.hostname
                    if sp.port:
                        onvif_port = sp.port
                    break
            vendor = _infer_vendor_from_text(f"{scopes} {service_url}")
            name_m = re.search(r"onvif://www\.onvif\.org/name/([^\s/]+)", scopes, re.IGNORECASE)
            hw_m = re.search(r"onvif://www\.onvif\.org/hardware/([^\s/]+)", scopes, re.IGNORECASE)
            model_hint = " ".join(
                p for p in [name_m.group(1) if name_m else "", hw_m.group(1) if hw_m else ""] if p
            ).strip()
            discovered[ip] = {
                "ip": ip,
                "host": ip,
                "onvif_port": onvif_port,
                "rtsp_port": 554,
                "http_port": 80,
                "vendor": vendor,
                "vendor_label": VENDOR_PRESET_MAP.get(vendor, VENDOR_PRESET_MAP["onvif_generic"])["label"],
                "model": model_hint or "ONVIF 网络摄像机",
                "model_hint": model_hint or "ONVIF 网络摄像机",
                "service_url": service_url or f"http://{ip}:{onvif_port}/onvif/device_service",
                "source": "ws_discovery",
            }
    except OSError:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    # Optional explicit target IP or IP:port probe (helpful when Wi-Fi AP isolates multicast UDP)
    clean_targets = [
        str(h).strip()
        for h in (target_hosts or [])
        if str(h or "").strip()
    ][:24]
    if clean_targets:
        def _probe_host_ports(raw_target: str) -> Optional[Dict[str, Any]]:
            target_str = raw_target
            explicit_port: Optional[int] = None
            if "://" in target_str:
                sp = urllib.parse.urlsplit(target_str)
                ip_str = sp.hostname or target_str
                explicit_port = sp.port
            elif ":" in target_str and target_str.count(":") == 1:
                h_part, p_part = target_str.rsplit(":", 1)
                ip_str = h_part.strip()
                try:
                    explicit_port = int(p_part.strip())
                except ValueError:
                    explicit_port = None
            else:
                ip_str = target_str

            candidate_ports: List[int] = []
            if explicit_port and 1 <= explicit_port <= 65535:
                candidate_ports.append(explicit_port)
            for p in (554, 80, 2020, 8000, 8080, 8554, 1984):
                if p not in candidate_ports:
                    candidate_ports.append(p)

            open_ports: List[int] = []
            for p in candidate_ports:
                try:
                    with socket.create_connection((ip_str, p), timeout=0.45):
                        open_ports.append(p)
                except OSError:
                    continue
            if not open_ports:
                return None

            vend = ""
            onvif_mfr_model = ""
            onvif_cand = explicit_port if (explicit_port in open_ports) else (
                2020 if 2020 in open_ports else (8000 if 8000 in open_ports else (80 if 80 in open_ports else open_ports[0]))
            )
            if onvif_cand != 554 and onvif_cand != 8554:
                try:
                    root = _onvif_soap_call(
                        f"http://{ip_str}:{onvif_cand}/onvif/device_service",
                        "<tds:GetDeviceInformation/>",
                        username="",
                        password="",
                        timeout_sec=0.6,
                    )
                    mfr = _xml_find_local_text(root, "Manufacturer")
                    mdl = _xml_find_local_text(root, "Model")
                    if mfr or mdl:
                        onvif_mfr_model = f"{mfr} {mdl}".strip()
                        vend = _infer_vendor_from_text(onvif_mfr_model)
                except Exception:
                    pass

            if not vend:
                if 2020 in open_ports:
                    vend = "tplink"
                elif 1984 in open_ports or 8554 in open_ports:
                    vend = "xiaomi"
                elif 8080 in open_ports and 554 not in open_ports:
                    vend = "volunteer_diy"
                else:
                    vend = "hikvision"

            model_str = onvif_mfr_model or f"开放端口 {', '.join(str(p) for p in open_ports)}"
            return {
                "ip": ip_str,
                "host": ip_str,
                "onvif_port": onvif_cand,
                "rtsp_port": 8554 if 8554 in open_ports else 554,
                "http_port": explicit_port if (explicit_port in open_ports and explicit_port not in (554, 8554)) else (
                    1984 if 1984 in open_ports else (8080 if 8080 in open_ports else 80)
                ),
                "open_ports": open_ports,
                "vendor": vend,
                "vendor_label": VENDOR_PRESET_MAP.get(vend, VENDOR_PRESET_MAP["onvif_generic"])["label"],
                "model": model_str,
                "model_hint": model_str,
                "service_url": f"http://{ip_str}:{onvif_cand}/onvif/device_service" if onvif_mfr_model else "",
                "source": "port_scan",
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            for item in pool.map(_probe_host_ports, clean_targets):
                if item and item["host"] not in discovered:
                    discovered[item["host"]] = item

    return sorted(discovered.values(), key=lambda d: d["host"])


# ─────────────────────────────────────────────────────────────────────────────
# Unified Single-Frame Grabber & Background Ingestion Scheduler
# ─────────────────────────────────────────────────────────────────────────────

def grab_camera_frame_bytes(
    raw_cfg: Dict[str, Any],
    timeout_override: Optional[float] = None,
) -> Tuple[bytes, int, int, Dict[str, Any]]:
    """Grab one live JPEG frame strictly in RAM according to normalized camera ingest config."""
    cfg = normalize_ingest_config(raw_cfg)
    protocol = cfg["protocol"]
    timeout_sec = float(timeout_override or cfg.get("timeout_sec") or 6.0)

    if protocol in PASSIVE_PUSH_PROTOCOLS:
        raise ValueError(
            f"当前机位配置为「{PROTOCOL_LABELS.get(protocol, protocol)}」被动接收模式，由相机主动推送画面；如需服务端主动拉流，请切换为 HTTP 抓图 / RTSP / ONVIF 协议。"
        )

    if protocol == "usb":
        return grab_opencv_stream_frame(
            f"usb://device/{cfg.get('device_index', 0)}",
            timeout_sec=timeout_sec,
            is_usb=True,
        )

    if protocol == "onvif":
        snap_uri = str(cfg.get("onvif_snapshot_uri") or "").strip()
        rtsp_uri = str(cfg.get("onvif_rtsp_uri") or "").strip()
        probed_meta: Optional[Dict[str, Any]] = None
        if not snap_uri and not rtsp_uri:
            probed_meta = probe_onvif_device(
                host=cfg.get("host", ""),
                port=int(cfg.get("onvif_port") or 80),
                username=cfg.get("username", ""),
                password=cfg.get("password", ""),
                use_tls=bool(cfg.get("use_tls", False)),
                timeout_sec=min(4.0, timeout_sec),
            )
            rec = probed_meta.get("recommended", {})
            snap_uri = str(rec.get("snapshot_uri") or "").strip()
            rtsp_uri = str(rec.get("rtsp_uri") or "").strip()

        last_onvif_err: Optional[Exception] = None
        if snap_uri:
            try:
                jpg, w, h, meta = grab_http_or_mjpeg_frame(
                    snap_uri,
                    username=cfg.get("username", ""),
                    password=cfg.get("password", ""),
                    auth_mode="auto",
                    verify_tls=bool(cfg.get("verify_tls", False)),
                    timeout_sec=timeout_sec,
                )
                meta["transport"] = "onvif_snapshot"
                if probed_meta:
                    meta["onvif_discovered"] = probed_meta.get("recommended", {})
                    meta["device_info"] = probed_meta.get("device_info", {})
                return jpg, w, h, meta
            except Exception as exc:
                last_onvif_err = exc

        if rtsp_uri:
            full_rtsp = _inject_credentials_if_needed(
                rtsp_uri, cfg.get("username", ""), cfg.get("password", ""), "rtsp"
            )
            jpg, w, h, meta = grab_opencv_stream_frame(
                full_rtsp,
                rtsp_transport=cfg.get("rtsp_transport", "tcp"),
                timeout_sec=timeout_sec,
            )
            meta["transport"] = "onvif_rtsp"
            if probed_meta:
                meta["onvif_discovered"] = probed_meta.get("recommended", {})
                meta["device_info"] = probed_meta.get("device_info", {})
            return jpg, w, h, meta

        if last_onvif_err:
            raise last_onvif_err
        raise RuntimeError("ONVIF 设备未返回可用的抓拍或 RTSP 地址")

    urls = synthesize_ingest_urls(cfg)
    primary_url = urls["primary_url"]
    fallback_url = urls["fallback_url"]

    if protocol in ("http_snapshot", "mjpeg"):
        try:
            return grab_http_or_mjpeg_frame(
                primary_url,
                username=cfg.get("username", ""),
                password=cfg.get("password", ""),
                auth_mode=cfg.get("auth_mode", "auto"),
                auth_token=cfg.get("auth_token", ""),
                verify_tls=bool(cfg.get("verify_tls", False)),
                timeout_sec=timeout_sec,
                is_mjpeg=(protocol == "mjpeg"),
            )
        except Exception as primary_exc:
            if protocol == "http_snapshot" and fallback_url and cfg.get("auto_fallback_rtsp", True):
                jpg, w, h, meta = grab_opencv_stream_frame(
                    fallback_url,
                    rtsp_transport=cfg.get("rtsp_transport", "tcp"),
                    timeout_sec=timeout_sec,
                )
                meta["transport"] = "http_fallback_to_rtsp"
                meta["fallback_reason"] = str(primary_exc)
                return jpg, w, h, meta
            raise

    # rtsp / rtmp / hls
    return grab_opencv_stream_frame(
        primary_url,
        rtsp_transport=cfg.get("rtsp_transport", "tcp"),
        timeout_sec=timeout_sec,
        is_usb=False,
    )


def test_camera_connection(
    raw_cfg: Dict[str, Any],
    timeout_sec: Optional[float] = None,
    ftp_port: int = 2121,
) -> Dict[str, Any]:
    """Test camera connectivity in RAM and return masked URL + live JPEG preview."""
    cfg = normalize_ingest_config(raw_cfg)
    protocol = cfg["protocol"]
    urls = synthesize_ingest_urls(cfg)
    if protocol in PASSIVE_PUSH_PROTOCOLS:
        return {
            "ok": True,
            "mode": "passive_push",
            "protocol": protocol,
            "protocol_label": PROTOCOL_LABELS.get(protocol, protocol),
            "transport": protocol,
            "resolved_url_masked": urls.get("masked_url", ""),
            "ftp_push_hint": f"ftp://<服务器IP>:{ftp_port}/<camera_id>/snap.jpg",
            "http_push_path": "/api/cameras/<camera_id>/snapshot",
            "message": "当前为相机主动推送模式（FTP / HTTP Webhook），无需服务端轮询连接；请按下方推送地址在相机后台开启定时抓拍上传。",
        }
    try:
        jpg_bytes, w, h, meta = grab_camera_frame_bytes(cfg, timeout_override=timeout_sec)
        b64_str = base64.b64encode(jpg_bytes).decode("ascii")
        del jpg_bytes
        out: Dict[str, Any] = {
            "ok": True,
            "mode": "active_pull",
            "protocol": protocol,
            "protocol_label": PROTOCOL_LABELS.get(protocol, protocol),
            "width": w,
            "height": h,
            "latency_ms": meta.get("latency_ms", 0.0),
            "transport": meta.get("transport", protocol),
            "resolved_url_masked": urls.get("masked_url", ""),
            "preview_jpeg_b64": b64_str,
        }
        if isinstance(meta.get("onvif_discovered"), dict):
            disc = meta["onvif_discovered"]
            out["onvif_discovered"] = {
                "profile_token": disc.get("profile_token", ""),
                "snapshot_uri_masked": mask_url_credentials(disc.get("snapshot_uri", "")),
                "rtsp_uri_masked": mask_url_credentials(disc.get("rtsp_uri", "")),
            }
        if isinstance(meta.get("device_info"), dict) and meta["device_info"]:
            out["device_info"] = meta["device_info"]
        return out
    except Exception as exc:
        return {
            "ok": False,
            "mode": "active_pull",
            "protocol": protocol,
            "protocol_label": PROTOCOL_LABELS.get(protocol, protocol),
            "resolved_url_masked": urls.get("masked_url", ""),
            "error": str(exc),
        }


class CameraIngestionScheduler:
    """Background active-pull scheduler keeping configured cameras streaming into VisionPipeline in RAM."""

    def __init__(
        self,
        db: Any,
        process_frame_fn: Optional[Callable[[str, bytes, str], Dict[str, Any]]] = None,
        vision_pipeline: Any = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        tick_sec: float = 2.0,
    ) -> None:
        self.db = db
        self.vision_pipeline = vision_pipeline
        if process_frame_fn is not None:
            self.process_frame_fn = process_frame_fn
        elif vision_pipeline is not None:
            self.process_frame_fn = lambda cid, img_bytes, _src: vision_pipeline.process_snapshot_bytes(
                cid, img_bytes
            )
        else:
            raise ValueError("Either process_frame_fn or vision_pipeline must be supplied")
        self.event_callback = event_callback
        self.tick_sec = max(1.0, float(tick_sec))
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_due: Dict[str, float] = {}
        self._last_primary_probe_ts: Dict[str, float] = {}
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._worker_pool: Optional[ThreadPoolExecutor] = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def start(self, tick_seconds: Optional[float] = None) -> None:
        if tick_seconds is not None:
            self.tick_sec = max(0.5, float(tick_seconds))
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        if self._worker_pool is None:
            self._worker_pool = ThreadPoolExecutor(
                max_workers=MAX_CONCURRENT_PULLS, thread_name_prefix="cam-sched"
            )
        self._thread = threading.Thread(
            target=self._run_loop, name="camera-ingest-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.5)
        self._thread = None
        if self._worker_pool is not None:
            try:
                self._worker_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._worker_pool.shutdown(wait=False)
            self._worker_pool = None

    def notify_config_changed(self, camera_id: Optional[str] = None) -> None:
        with self._lock:
            if camera_id:
                self._next_due[camera_id] = 0.0
            else:
                self._next_due.clear()
        self._wake_event.set()

    def _persist_discovered_onvif_uris(
        self,
        camera_id: str,
        full_cfg: Dict[str, Any],
        slot_index: int,
        discovered: Any,
    ) -> None:
        if not isinstance(discovered, dict) or not (
            discovered.get("snapshot_uri") or discovered.get("rtsp_uri")
        ):
            return
        merged_cfg = dict(full_cfg)
        if slot_index == 0:
            if discovered.get("snapshot_uri"):
                merged_cfg["onvif_snapshot_uri"] = discovered["snapshot_uri"]
            if discovered.get("rtsp_uri"):
                merged_cfg["onvif_rtsp_uri"] = discovered["rtsp_uri"]
            if discovered.get("profile_token"):
                merged_cfg["onvif_profile_token"] = discovered["profile_token"]
        else:
            standbys = [dict(x) for x in (merged_cfg.get("standby_slots") or []) if isinstance(x, dict)]
            if 0 <= slot_index - 1 < len(standbys):
                target_s = standbys[slot_index - 1]
                if discovered.get("snapshot_uri"):
                    target_s["onvif_snapshot_uri"] = discovered["snapshot_uri"]
                if discovered.get("rtsp_uri"):
                    target_s["onvif_rtsp_uri"] = discovered["rtsp_uri"]
                if discovered.get("profile_token"):
                    target_s["onvif_profile_token"] = discovered["profile_token"]
                merged_cfg["standby_slots"] = standbys
        self.db.save_camera_ingest_config(camera_id, merged_cfg)

    def _finalize_grabbed_frame(
        self,
        *,
        camera_id: str,
        full_cfg: Dict[str, Any],
        slot_index: int,
        slot_label: str,
        slot_cfg: Dict[str, Any],
        cam_secret: Dict[str, Any],
        jpg_bytes: bytes,
        w: int,
        h: int,
        grab_meta: Dict[str, Any],
        now_ts: float,
        process_vision: bool,
        save_reference_frame: bool,
        save_reference_if_empty: bool,
        force_save_reference: bool,
        raise_on_error: bool,
        failover_switched: bool = False,
        failover_restored_primary: bool = False,
        failover_reason: str = "",
    ) -> Dict[str, Any]:
        self._persist_discovered_onvif_uris(
            camera_id, full_cfg, slot_index, grab_meta.get("onvif_discovered")
        )
        preview_b64 = ""
        should_save_ref = bool(
            save_reference_frame
            or force_save_reference
            or failover_switched
            or failover_restored_primary
            or (save_reference_if_empty and not cam_secret.get("has_reference_frame"))
        )
        if should_save_ref or not raise_on_error:
            preview_b64 = base64.b64encode(jpg_bytes).decode("ascii")
            if should_save_ref:
                self.db.save_camera_latest_frame(camera_id, preview_b64, w, h)

        vision_res: Optional[Dict[str, Any]] = None
        if process_vision:
            src_label = f"ingest:slot{slot_index}:{slot_cfg.get('protocol', 'pull')}"
            vision_res = self.process_frame_fn(
                camera_id,
                jpg_bytes,
                src_label,
            )
            if self.event_callback and isinstance(vision_res, dict):
                slot_tag = f" · {slot_label}" if (full_cfg.get("standby_slots") or slot_index > 0) else ""
                self.event_callback(
                    {
                        "ts": now_ts,
                        "source": f"相机自动拉流{slot_tag} ({slot_cfg.get('vendor_label', slot_cfg.get('protocol', 'pull'))})",
                        "camera_id": camera_id,
                        "elapsed_ms": vision_res.get("elapsed_ms", 0.0),
                        "processed_spots": len(vision_res.get("reports") or vision_res.get("spots") or []),
                    }
                )
        del jpg_bytes

        # Refresh ingest_status in case switch_camera_active_slot updated failover counters
        latest_rec = self.db.get_camera_ingest_record(camera_id) or cam_secret
        prev_st = latest_rec.get("ingest_status") or {}
        standby_cnt = len(full_cfg.get("standby_slots") or [])
        frames_cnt = int(prev_st.get("frames_pulled") or 0) + 1
        status_obj: Dict[str, Any] = {
            "state": "online",
            "last_pull_at": now_ts,
            "last_ok_ts": now_ts,
            "last_ok_at": format_local_ts(now_ts),
            "frames_pulled": frames_cnt,
            "last_latency_ms": grab_meta.get("latency_ms", 0.0),
            "last_resolution": f"{w}x{h}",
            "transport": grab_meta.get("transport", slot_cfg.get("protocol", "")),
            "consecutive_failures": 0,
            "last_error": "",
            "active_slot_index": slot_index,
            "active_slot_label": slot_label,
            "standby_count": standby_cnt,
            "failover_active": bool(slot_index > 0),
        }
        if failover_reason:
            status_obj["last_failover_reason"] = failover_reason[:200]
            status_obj["last_failover_at"] = format_local_ts(now_ts)
        self.db.update_camera_ingest_status(camera_id, status_obj)
        out: Dict[str, Any] = {
            "ok": True,
            "camera_id": camera_id,
            "slot_index": slot_index,
            "slot_label": slot_label,
            "failover_switched": failover_switched,
            "failover_restored_primary": failover_restored_primary,
            "width": w,
            "height": h,
            "ingest_status": status_obj,
            "vision": vision_res,
        }
        if preview_b64 and not raise_on_error:
            out["preview_jpeg_b64"] = preview_b64
        return out

    def pull_camera_once(
        self,
        camera_id: str,
        process_vision: bool = True,
        save_reference_frame: bool = False,
        save_reference_if_empty: bool = True,
        force_save_reference: bool = False,
        raise_on_error: bool = False,
        slot_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        cam_secret = self.db.get_camera_ingest_record(camera_id)
        if not cam_secret:
            err = f"未找到摄像头: {camera_id}"
            if raise_on_error:
                raise ValueError(err)
            return {"ok": False, "camera_id": camera_id, "error": err}
        cfg = cam_secret.get("ingest_config") or {}
        if not cfg:
            err = f"机位 {camera_id} 尚未配置主动拉流参数"
            if raise_on_error:
                raise ValueError(err)
            return {"ok": False, "camera_id": camera_id, "error": err}

        now_ts = time.time()
        standby_slots = cfg.get("standby_slots") or []
        active_idx, active_label, active_cfg = resolve_slot_ingest_config(cfg, slot_override)

        # Periodic primary-camera health probe when currently running on a standby slot
        if (
            slot_override is None
            and active_idx > 0
            and bool(cfg.get("failover_enabled"))
            and bool(cfg.get("auto_restore_primary", True))
        ):
            probe_interval = max(15.0, float(cfg.get("primary_probe_interval_sec") or 180.0))
            last_probe = self._last_primary_probe_ts.get(camera_id, 0.0)
            if now_ts - last_probe >= probe_interval:
                self._last_primary_probe_ts[camera_id] = now_ts
                _, pri_label, pri_cfg = resolve_slot_ingest_config(cfg, 0)
                if is_slot_pull_ready(pri_cfg):
                    try:
                        pri_bytes, pw, ph, pri_meta = grab_camera_frame_bytes(pri_cfg)
                        restore_reason = f"{pri_label}恢复在线，自动从{active_label}切回主路"
                        if hasattr(self.db, "switch_camera_active_slot"):
                            self.db.switch_camera_active_slot(camera_id, 0, reason=restore_reason)
                        if self.vision_pipeline is not None and hasattr(
                            self.vision_pipeline, "clear_frame_cache"
                        ):
                            self.vision_pipeline.clear_frame_cache(camera_id)
                        if self.event_callback:
                            self.event_callback(
                                {
                                    "ts": now_ts,
                                    "source": "主备相机自动回切",
                                    "camera_id": camera_id,
                                    "detail": restore_reason,
                                }
                            )
                        refreshed_rec = self.db.get_camera_ingest_record(camera_id) or cam_secret
                        refreshed_cfg = refreshed_rec.get("ingest_config") or cfg
                        return self._finalize_grabbed_frame(
                            camera_id=camera_id,
                            full_cfg=refreshed_cfg,
                            slot_index=0,
                            slot_label=pri_label,
                            slot_cfg=pri_cfg,
                            cam_secret=refreshed_rec,
                            jpg_bytes=pri_bytes,
                            w=pw,
                            h=ph,
                            grab_meta=pri_meta,
                            now_ts=now_ts,
                            process_vision=process_vision,
                            save_reference_frame=save_reference_frame,
                            save_reference_if_empty=save_reference_if_empty,
                            force_save_reference=force_save_reference,
                            raise_on_error=raise_on_error,
                            failover_restored_primary=True,
                            failover_reason=restore_reason,
                        )
                    except Exception as probe_exc:
                        logger.debug(
                            "Primary slot recovery probe still unreachable for %s: %s",
                            camera_id,
                            probe_exc,
                        )

        try:
            jpg_bytes, w, h, grab_meta = grab_camera_frame_bytes(active_cfg)
            return self._finalize_grabbed_frame(
                camera_id=camera_id,
                full_cfg=cfg,
                slot_index=active_idx,
                slot_label=active_label,
                slot_cfg=active_cfg,
                cam_secret=cam_secret,
                jpg_bytes=jpg_bytes,
                w=w,
                h=h,
                grab_meta=grab_meta,
                now_ts=now_ts,
                process_vision=process_vision,
                save_reference_frame=save_reference_frame,
                save_reference_if_empty=save_reference_if_empty,
                force_save_reference=force_save_reference,
                raise_on_error=raise_on_error,
            )
        except Exception as exc:
            prev_st = cam_secret.get("ingest_status") or {}
            fails = int(prev_st.get("consecutive_failures") or 0) + 1
            err_msg = str(exc)
            threshold = max(1, int(cfg.get("failover_threshold") or 2))

            # Cold Failover: only probe standby slots when active slot reaches consecutive failure threshold
            if (
                slot_override is None
                and bool(cfg.get("failover_enabled"))
                and len(standby_slots) > 0
                and fails >= threshold
            ):
                total_slots = len(standby_slots) + 1
                candidate_indices = [
                    (active_idx + offset) % total_slots for offset in range(1, total_slots)
                ]
                for cand_idx in candidate_indices:
                    _, cand_label, cand_cfg = resolve_slot_ingest_config(cfg, cand_idx)
                    if not is_slot_pull_ready(cand_cfg):
                        continue
                    try:
                        cand_bytes, cw, ch, cand_meta = grab_camera_frame_bytes(cand_cfg)
                        switch_reason = (
                            f"{active_label}连续{fails}次拉流失败，自动冷切至{cand_label}"
                        )
                        if hasattr(self.db, "switch_camera_active_slot"):
                            self.db.switch_camera_active_slot(
                                camera_id, cand_idx, reason=switch_reason
                            )
                        if self.vision_pipeline is not None and hasattr(
                            self.vision_pipeline, "clear_frame_cache"
                        ):
                            self.vision_pipeline.clear_frame_cache(camera_id)
                        self._last_primary_probe_ts[camera_id] = now_ts
                        if self.event_callback:
                            self.event_callback(
                                {
                                    "ts": now_ts,
                                    "source": "主备相机自动冷切容灾",
                                    "camera_id": camera_id,
                                    "detail": switch_reason,
                                }
                            )
                        refreshed_rec = self.db.get_camera_ingest_record(camera_id) or cam_secret
                        refreshed_cfg = refreshed_rec.get("ingest_config") or cfg
                        return self._finalize_grabbed_frame(
                            camera_id=camera_id,
                            full_cfg=refreshed_cfg,
                            slot_index=cand_idx,
                            slot_label=cand_label,
                            slot_cfg=cand_cfg,
                            cam_secret=refreshed_rec,
                            jpg_bytes=cand_bytes,
                            w=cw,
                            h=ch,
                            grab_meta=cand_meta,
                            now_ts=now_ts,
                            process_vision=process_vision,
                            save_reference_frame=save_reference_frame,
                            save_reference_if_empty=save_reference_if_empty,
                            force_save_reference=force_save_reference,
                            raise_on_error=raise_on_error,
                            failover_switched=True,
                            failover_reason=switch_reason,
                        )
                    except Exception as cand_exc:
                        err_msg = f"{active_label}失败({exc}); {cand_label}亦不可达({cand_exc})"

            status_obj = {
                "state": "error",
                "last_pull_at": now_ts,
                "last_ok_ts": prev_st.get("last_ok_ts"),
                "last_ok_at": prev_st.get("last_ok_at"),
                "frames_pulled": int(prev_st.get("frames_pulled") or 0),
                "last_latency_ms": prev_st.get("last_latency_ms"),
                "last_resolution": prev_st.get("last_resolution", ""),
                "transport": active_cfg.get("protocol", ""),
                "consecutive_failures": fails,
                "last_error": err_msg[:260],
                "active_slot_index": active_idx,
                "active_slot_label": active_label,
                "standby_count": len(standby_slots),
                "failover_active": bool(active_idx > 0),
            }
            self.db.update_camera_ingest_status(camera_id, status_obj)
            if raise_on_error:
                raise
            return {
                "ok": False,
                "camera_id": camera_id,
                "slot_index": active_idx,
                "slot_label": active_label,
                "error": err_msg,
                "ingest_status": status_obj,
            }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self.tick_sec)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            try:
                records = self.db.list_active_pull_camera_records()
            except Exception as exc:
                if self._stop_event.is_set() or "unable to open database file" in str(exc):
                    break
                logger.warning("Failed listing active pull cameras: %s", exc)
                continue

            now = time.monotonic()
            for rec in records:
                if self._stop_event.is_set():
                    break
                cid = str(rec["camera_id"])
                cfg = rec.get("ingest_config") or {}
                if not cfg.get("enabled"):
                    continue
                _, _, active_slot_cfg = resolve_slot_ingest_config(cfg)
                if not is_slot_pull_ready(active_slot_cfg):
                    continue

                base_interval = max(5.0, float(cfg.get("poll_interval_sec") or 15.0))
                with self._lock:
                    if cid in self._in_flight:
                        continue
                    due = self._next_due.get(cid, 0.0)
                    if now < due:
                        continue
                    self._in_flight.add(cid)

                if self._worker_pool is not None:
                    self._worker_pool.submit(self._execute_scheduled_pull, cid, base_interval)

    def _execute_scheduled_pull(self, camera_id: str, base_interval: float) -> None:
        backoff = base_interval
        try:
            self.pull_camera_once(camera_id, save_reference_if_empty=True, raise_on_error=True)
        except Exception as exc:
            rec = self.db.get_camera_ingest_record(camera_id) or {}
            fails = int((rec.get("ingest_status") or {}).get("consecutive_failures") or 1)
            backoff = min(max(base_interval * 4.0, 120.0), base_interval * (1.6 ** min(fails, 5)))
            logger.debug("Scheduled camera pull failed for %s: %s (retry in %.1fs)", camera_id, exc, backoff)
        finally:
            with self._lock:
                self._in_flight.discard(camera_id)
                self._next_due[camera_id] = time.monotonic() + backoff
