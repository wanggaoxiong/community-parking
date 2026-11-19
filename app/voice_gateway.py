from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import io
import json
import logging
import math
import os
import plistlib
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

from app.config import BASE_DIR, DATA_DIR, config_manager
from app.vision_pipeline import VisionPipeline
from app.workspaces import extract_short_site_name, strip_greeting_for_repeat

logger = logging.getLogger("parking.voice_gateway")

STATIC_DIR = BASE_DIR / "static"
SHORTCUTS_CACHE_DIR = DATA_DIR / "shortcuts"
MACOS_SHORTCUTS_BIN = "/usr/bin/shortcuts"
_MAP_HELPER_LOCK = threading.Lock()
_PLACE_SEARCH_CACHE: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()

_APPLE_MAP_SWIFT_SRC = r"""
import Foundation
import MapKit
import CoreLocation

final class CompleterBox: NSObject, MKLocalSearchCompleterDelegate {
    var items: [[String: Any]] = []
    var done = false
    func completerDidUpdateResults(_ completer: MKLocalSearchCompleter) {
        for r in completer.results.prefix(8) {
            let t = r.title.trimmingCharacters(in: .whitespacesAndNewlines)
            let s = r.subtitle.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty {
                items.append(["title": t, "subtitle": s, "full_address": s.isEmpty ? t : "\(s) \(t)", "source": "apple_maps"])
            }
        }
        done = true
    }
    func completer(_ completer: MKLocalSearchCompleter, didFailWithError error: Error) {
        done = true
    }
}

let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "search"

if mode == "reverse" && args.count >= 4, let lat = Double(args[2]), let lon = Double(args[3]) {
    var out: [[String: Any]] = []
    var finished = false
    let loc = CLLocation(latitude: lat, longitude: lon)
    CLGeocoder().reverseGeocodeLocation(loc) { placemarks, _ in
        for p in (placemarks ?? []).prefix(5) {
            let name = p.name ?? ""
            let sub = [p.administrativeArea, p.locality, p.subLocality, p.thoroughfare, p.subThoroughfare].compactMap { $0 }.joined()
            let title = !name.isEmpty ? name : sub
            if !title.isEmpty {
                out.append([
                    "title": title,
                    "subtitle": sub,
                    "full_address": sub.contains(title) ? sub : "\(sub)\(title)",
                    "lat": lat,
                    "lon": lon,
                    "source": "apple_maps"
                ])
            }
        }
        finished = true
    }
    let dl = Date().addingTimeInterval(3.0)
    while !finished && Date() < dl {
        RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.04))
    }
    if let data = try? JSONSerialization.data(withJSONObject: out), let s = String(data: data, encoding: .utf8) {
        print(s)
    } else {
        print("[]")
    }
    exit(0)
}

let query = args.count > 2 ? args[2] : ""
if query.isEmpty {
    print("[]")
    exit(0)
}

let box = CompleterBox()
let comp = MKLocalSearchCompleter()
comp.delegate = box
comp.queryFragment = query

var searchItems: [[String: Any]] = []
var searchDone = false
let req = MKLocalSearch.Request()
req.naturalLanguageQuery = query
MKLocalSearch(request: req).start { resp, _ in
    for it in (resp?.mapItems ?? []).prefix(6) {
        let p = it.placemark
        let title = (it.name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let sub = [p.administrativeArea, p.locality, p.subLocality, p.thoroughfare, p.subThoroughfare].compactMap { $0 }.joined()
        if !title.isEmpty {
            searchItems.append([
                "title": title,
                "subtitle": sub,
                "full_address": sub.isEmpty ? title : "\(sub)\(title)",
                "lat": p.coordinate.latitude,
                "lon": p.coordinate.longitude,
                "source": "apple_maps"
            ])
        }
    }
    searchDone = true
}

let deadline = Date().addingTimeInterval(2.8)
while (!box.done || !searchDone) && Date() < deadline {
    RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.04))
}

var merged: [[String: Any]] = []
var seen = Set<String>()
for item in (searchItems + box.items) {
    let t = (item["title"] as? String) ?? ""
    if t.isEmpty || seen.contains(t) { continue }
    seen.insert(t)
    merged.append(item)
    if merged.count >= 8 { break }
}
// Prioritize gate / parking entrance items at top for arrival geofence convenience
merged.sort { a, b in
    let ta = (a["title"] as? String) ?? ""
    let tb = (b["title"] as? String) ?? ""
    let sa = (ta.contains("门") || ta.contains("入口") || ta.contains("停车")) ? 0 : 1
    let sb = (tb.contains("门") || tb.contains("入口") || tb.contains("停车")) ? 0 : 1
    return sa < sb
}
if let data = try? JSONSerialization.data(withJSONObject: merged), let s = String(data: data, encoding: .utf8) {
    print(s)
} else {
    print("[]")
}
"""


def _ensure_apple_map_helper_bin() -> Optional[Path]:
    import shutil

    if os.uname().sysname != "Darwin" or not shutil.which("swiftc"):
        return None
    bin_dir = DATA_DIR / "shortcuts"
    bin_path = bin_dir / "apple_map_helper_v1"
    if bin_path.is_file() and os.access(bin_path, os.X_OK):
        return bin_path
    with _MAP_HELPER_LOCK:
        if bin_path.is_file() and os.access(bin_path, os.X_OK):
            return bin_path
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
            src_path = bin_dir / ".apple_map_helper_v1.swift"
            tmp_bin = bin_dir / ".apple_map_helper_v1.tmp"
            src_path.write_text(_APPLE_MAP_SWIFT_SRC, encoding="utf-8")
            proc = subprocess.run(
                ["swiftc", "-O", str(src_path), "-o", str(tmp_bin)],
                capture_output=True,
                text=True,
                timeout=25,
            )
            try:
                src_path.unlink(missing_ok=True)
            except Exception:
                pass
            if proc.returncode == 0 and tmp_bin.is_file():
                os.chmod(tmp_bin, 0o755)
                tmp_bin.replace(bin_path)
                return bin_path
        except Exception as exc:
            logger.warning("Apple MapKit helper build skipped: %s", exc)
    return None


def search_local_map_places(
    query: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Search or reverse-geocode real addresses using native Apple Maps (`MapKit`) on macOS,
    with automatic fallback to OpenStreetMap Nominatim on Linux/Docker/Windows.
    """
    clean_q = re.sub(r"\s+", " ", str(query or "")).strip()[:80]
    is_reverse = lat is not None and lon is not None and not clean_q
    cache_key = f"rev:{lat:.5f},{lon:.5f}" if is_reverse else f"q:{clean_q}"
    if not is_reverse and not clean_q:
        return {"ok": True, "engine": "idle", "places": []}

    if cache_key in _PLACE_SEARCH_CACHE:
        _PLACE_SEARCH_CACHE.move_to_end(cache_key)
        cached = _PLACE_SEARCH_CACHE[cache_key]
        eng = cached[0].get("source", "cache") if cached else "cache"
        return {"ok": True, "engine": eng, "places": cached}

    places: List[Dict[str, Any]] = []
    engine_used = "nominatim"

    helper_bin = _ensure_apple_map_helper_bin()
    if helper_bin is not None:
        try:
            cmd = (
                [str(helper_bin), "reverse", f"{float(lat):.6f}", f"{float(lon):.6f}"]
                if is_reverse
                else [str(helper_bin), "search", clean_q]
            )
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4.5)
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout.strip())
                if isinstance(parsed, list) and parsed:
                    places = parsed[:8]
                    engine_used = "apple_maps"
        except Exception:
            places = []

    if not places:
        try:
            headers = {"User-Agent": "CommunityParkingAssistant/1.0"}
            if is_reverse:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={
                        "lat": f"{float(lat):.6f}",
                        "lon": f"{float(lon):.6f}",
                        "format": "jsonv2",
                        "accept-language": "zh-CN",
                    },
                    headers=headers,
                    timeout=4.0,
                )
                if resp.ok:
                    d = resp.json()
                    disp = str(d.get("display_name") or "").strip()
                    nm = str(d.get("name") or disp.split(",")[0]).strip()
                    if nm:
                        places.append(
                            {
                                "title": nm,
                                "subtitle": disp,
                                "full_address": disp or nm,
                                "lat": float(d.get("lat") or lat),
                                "lon": float(d.get("lon") or lon),
                                "source": "osm_map",
                            }
                        )
            else:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": clean_q,
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "limit": 6,
                        "accept-language": "zh-CN",
                    },
                    headers=headers,
                    timeout=4.0,
                )
                if resp.ok and isinstance(resp.json(), list):
                    for it in resp.json()[:6]:
                        disp = str(it.get("display_name") or "").strip()
                        nm = str(it.get("name") or disp.split(",")[0]).strip()
                        if nm:
                            places.append(
                                {
                                    "title": nm,
                                    "subtitle": disp,
                                    "full_address": disp or nm,
                                    "lat": float(it.get("lat") or 0),
                                    "lon": float(it.get("lon") or 0),
                                    "source": "osm_map",
                                }
                            )
        except Exception:
            pass

    if places:
        _PLACE_SEARCH_CACHE[cache_key] = places
        while len( _PLACE_SEARCH_CACHE) > 128:
            _PLACE_SEARCH_CACHE.popitem(last=False)

    return {"ok": True, "engine": engine_used, "places": places}


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def _can_macos_sign_shortcuts() -> bool:
    return os.path.isfile(MACOS_SHORTCUTS_BIN) and os.access(MACOS_SHORTCUTS_BIN, os.X_OK)

VOICE_ALLOWED_STATIC_FILES = {
    "voice.html",
    "voice.css",
    "voice.js",
    "mobile-icon.svg",
}

WAKE_BRIEF_WORDS = {
    "",
    "车位",
    "小区车位",
    "找车位",
    "看车位",
    "查车位",
    "空车位",
    "空位",
    "停车",
    "回家",
    "回家停车",
    "播报",
    "语音播报",
    "刷新",
    "现在有车位吗",
    "还有车位吗",
}


def _compute_voice_static_version() -> str:
    h = hashlib.sha256()
    for fname in ("voice.html", "voice.css", "voice.js"):
        fpath = STATIC_DIR / fname
        try:
            st = fpath.stat()
            h.update(f"{fname}:{st.st_mtime_ns}:{st.st_size};".encode("utf-8"))
        except OSError:
            pass
    return h.hexdigest()[:12] or "1"


def _load_cjk_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\msyh.ttc",
        "C:\\Windows\\Fonts\\simhei.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


class WeixinClawBridge:
    """
    Optional outbound long-poll bridge compatible with Tencent's official
    personal WeChat ClawBot protocol (`@tencent-weixin/openclaw-weixin` / ilinkai.weixin.qq.com).
    Uses pure outbound HTTPS (zero public webhook / zero ICP filing required).
    """

    CHANNEL_VERSION = "1.0.2"

    def __init__(self, gateway: "VoiceGatewayService") -> None:
        self._gateway = gateway
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sync_cursor: str = ""
        self._context_tokens: Dict[str, str] = {}
        self._active_qr: Dict[str, Any] = {}
        self._status: Dict[str, Any] = {
            "running": False,
            "connected": False,
            "last_poll_at": 0.0,
            "last_message_at": 0.0,
            "messages_handled": 0,
            "last_error": "",
        }

    @staticmethod
    def _random_wechat_uin() -> str:
        val = int.from_bytes(os.urandom(4), "big")
        return base64.b64encode(str(val).encode("utf-8")).decode("ascii")

    def _build_headers(self, bot_token: str = "") -> Dict[str, str]:
        hdrs = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._random_wechat_uin(),
        }
        if bot_token:
            hdrs["Authorization"] = f"Bearer {bot_token}"
        return hdrs

    def get_status(self) -> Dict[str, Any]:
        cfg = config_manager.get().get("voice_gateway", {})
        with self._lock:
            st = dict(self._status)
            qr = dict(self._active_qr) if self._active_qr else None
        st["enabled"] = bool(cfg.get("weixin_enabled"))
        st["has_bot_token"] = bool(str(cfg.get("weixin_bot_token") or "").strip())
        st["account_name"] = str(cfg.get("weixin_account_name") or "")
        st["api_base"] = str(cfg.get("weixin_api_base") or "https://ilinkai.weixin.qq.com")
        if qr:
            st["pending_qr"] = {
                "qrcode": qr.get("qrcode", ""),
                "qrcode_img_content": qr.get("qrcode_img_content", ""),
                "status": qr.get("status", "wait"),
                "created_at": qr.get("created_at", 0.0),
            }
        return st

    def start_qr_login(self) -> Dict[str, Any]:
        cfg = config_manager.get().get("voice_gateway", {})
        api_base = str(cfg.get("weixin_api_base") or "https://ilinkai.weixin.qq.com").rstrip("/")
        url = f"{api_base}/ilink/bot/get_bot_qrcode?bot_type=3"
        resp = requests.get(url, headers=self._build_headers(), timeout=12)
        resp.raise_for_status()
        data = resp.json()
        qr_info = {
            "qrcode": str(data.get("qrcode") or ""),
            "qrcode_img_content": str(data.get("qrcode_img_content") or ""),
            "status": "wait",
            "created_at": time.time(),
        }
        if not qr_info["qrcode"]:
            raise RuntimeError(f"微信接口未返回有效配对二维码: {str(data)[:160]}")
        with self._lock:
            self._active_qr = qr_info
        return qr_info

    def poll_qr_login(self, qrcode_override: str = "") -> Dict[str, Any]:
        cfg = config_manager.get().get("voice_gateway", {})
        api_base = str(cfg.get("weixin_api_base") or "https://ilinkai.weixin.qq.com").rstrip("/")
        with self._lock:
            qr_code = qrcode_override or str(self._active_qr.get("qrcode") or "")
        if not qr_code:
            return {"ok": False, "status": "none", "message": "当前没有进行中的扫码配对会话"}

        url = f"{api_base}/ilink/bot/get_qrcode_status?qrcode={quote(qr_code)}"
        resp = requests.get(
            url,
            headers={"iLink-App-ClientVersion": "1", **self._build_headers()},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status") or "wait")
        with self._lock:
            if self._active_qr:
                self._active_qr["status"] = status

        if status == "confirmed" and data.get("bot_token"):
            bot_token = str(data["bot_token"]).strip()
            new_base = str(data.get("baseurl") or api_base).strip()
            bot_id = str(data.get("ilink_bot_id") or "WeChat-ClawBot").strip()
            config_manager.update(
                {
                    "voice_gateway": {
                        "weixin_enabled": True,
                        "weixin_bot_token": bot_token,
                        "weixin_api_base": new_base,
                        "weixin_account_name": bot_id,
                    }
                }
            )
            with self._lock:
                self._active_qr = {}
            self.sync_worker_state()
            return {
                "ok": True,
                "status": "confirmed",
                "account_name": bot_id,
                "message": "普通微信助手扫码配对成功，已自动开启免备案长轮询通道！",
            }
        return {"ok": True, "status": status, "raw": {k: v for k, v in data.items() if k != "bot_token"}}

    def sync_worker_state(self) -> None:
        cfg = config_manager.get().get("voice_gateway", {})
        should_run = bool(cfg.get("weixin_enabled")) and bool(str(cfg.get("weixin_bot_token") or "").strip())
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            if should_run and not alive:
                self._stop_event.clear()
                self._thread = threading.Thread(
                    target=self._poll_loop,
                    name="OpenClawWeixinLongPoll",
                    daemon=True,
                )
                self._thread.start()
            elif not should_run and alive:
                self._stop_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=2.5)

    def _send_text_reply(
        self, api_base: str, bot_token: str, to_user_id: str, context_token: str, text: str
    ) -> None:
        url = f"{api_base.rstrip('/')}/ilink/bot/sendmessage"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"parking-{uuid.uuid4().hex[:16]}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": self.CHANNEL_VERSION},
        }
        r = requests.post(url, headers=self._build_headers(bot_token), json=payload, timeout=15)
        r.raise_for_status()

    def _extract_incoming_text(self, msg: Dict[str, Any]) -> str:
        parts: List[str] = []
        for item in msg.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            itype = int(item.get("type") or 0)
            if itype == 1 and isinstance(item.get("text_item"), dict):
                t = str(item["text_item"].get("text") or "").strip()
                if t:
                    parts.append(t)
            elif itype == 3 and isinstance(item.get("voice_item"), dict):
                # WeChat voice message automatically includes speech-to-text in voice_item.text!
                vt = str(item["voice_item"].get("text") or "").strip()
                if vt:
                    parts.append(vt)
        return " ".join(parts).strip()

    def _poll_loop(self) -> None:
        with self._lock:
            self._status["running"] = True
            self._status["last_error"] = ""
        backoff = 2.0
        while not self._stop_event.is_set():
            cfg = config_manager.get().get("voice_gateway", {})
            if not cfg.get("weixin_enabled"):
                break
            bot_token = str(cfg.get("weixin_bot_token") or "").strip()
            if not bot_token:
                break
            api_base = str(cfg.get("weixin_api_base") or "https://ilinkai.weixin.qq.com").rstrip("/")
            try:
                resp = requests.post(
                    f"{api_base}/ilink/bot/getupdates",
                    headers=self._build_headers(bot_token),
                    json={
                        "get_updates_buf": self._sync_cursor,
                        "base_info": {"channel_version": self.CHANNEL_VERSION},
                    },
                    timeout=40,
                )
                resp.raise_for_status()
                data = resp.json()
                ret_code = int(data.get("ret", 0) or 0)
                err_code = int(data.get("errcode", 0) or 0)
                if ret_code != 0 or err_code != 0:
                    err_msg = str(data.get("errmsg") or f"ret={ret_code}, errcode={err_code}")
                    with self._lock:
                        self._status["connected"] = False
                        self._status["last_error"] = err_msg
                    if err_code == -14:
                        # Session token expired; wait until re-paired
                        self._stop_event.wait(30.0)
                    else:
                        self._stop_event.wait(min(30.0, backoff))
                        backoff = min(30.0, backoff * 1.6)
                    continue

                backoff = 2.0
                if data.get("get_updates_buf"):
                    self._sync_cursor = str(data["get_updates_buf"])
                with self._lock:
                    self._status["connected"] = True
                    self._status["last_poll_at"] = time.time()
                    self._status["last_error"] = ""

                for msg in data.get("msgs") or []:
                    if not isinstance(msg, dict):
                        continue
                    if int(msg.get("message_type") or 1) != 1:
                        continue
                    from_uid = str(msg.get("from_user_id") or "").strip()
                    ctx_tok = str(msg.get("context_token") or "").strip()
                    if not from_uid or not ctx_tok:
                        continue
                    self._context_tokens[from_uid] = ctx_tok
                    user_text = self._extract_incoming_text(msg)
                    reply_obj = self._gateway.ask_voice_assistant(
                        question=user_text or "车位",
                        unit_id=None,
                        repeat=1,
                        vehicle_type="sedan",
                        channel="weixin",
                        external_uid=from_uid,
                        client_ip="weixin_ilink",
                    )
                    reply_txt = str(reply_obj.get("answer") or reply_obj.get("speech_text") or "").strip()
                    mob_url = str(cfg.get("public_mobile_url") or "").strip()
                    if mob_url and ("图" in user_text or "哪" in user_text or user_text in WAKE_BRIEF_WORDS):
                        reply_txt = f"{reply_txt}\n\n🗺️ 实时平面图：{mob_url}"
                    if reply_txt:
                        self._send_text_reply(api_base, bot_token, from_uid, ctx_tok, reply_txt)
                        with self._lock:
                            self._status["last_message_at"] = time.time()
                            self._status["messages_handled"] = int(self._status["messages_handled"]) + 1
            except Exception as exc:
                with self._lock:
                    self._status["connected"] = False
                    self._status["last_error"] = str(exc)[:200]
                self._stop_event.wait(min(25.0, backoff))
                backoff = min(25.0, backoff * 1.6)

        with self._lock:
            self._status["running"] = False
            self._status["connected"] = False


class VoiceGatewayService:
    """
    Port :18791 Hands-Free Driving Voice / Siri CarPlay Shortcuts / Custom-LLM Q&A /
    Optional Personal WeChat (`openclaw-weixin`) Gateway.
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._lock = threading.RLock()
        self._sign_lock = threading.Lock()
        self._signed_shortcut_cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._rate_buckets: Dict[str, Deque[float]] = {}
        self._daily_date: str = ""
        self._daily_llm_calls: int = 0
        self._total_brief_calls: int = 0
        self._total_ask_calls: int = 0
        self.weixin_bridge = WeixinClawBridge(self)

    def start_background_workers(self) -> None:
        self.weixin_bridge.sync_worker_state()

    def stop_background_workers(self) -> None:
        self.weixin_bridge.stop()

    def _ws_id(self) -> str:
        return str(getattr(self.ctx, "workspace_id", "default") or "default")

    def _ws_profile(self) -> Dict[str, Any]:
        if hasattr(self.ctx, "get_workspace_profile"):
            return self.ctx.get_workspace_profile() or {}
        cfg = config_manager.get()
        vg = cfg.get("voice_gateway", {})
        return {
            "workspace_id": "default",
            "name": str(cfg.get("community_name") or "邻里公益寻位"),
            "scene_type": "community",
            "role_title": "业主",
            "access_token": str(vg.get("access_token") or ""),
            "arrival_place": str(vg.get("arrival_place") or ""),
            "arrival_radius_m": int(vg.get("arrival_radius_m") or 200),
            "default_unit_id": str(vg.get("default_unit_id") or "8-1"),
        }

    def _effective_access_token(self) -> str:
        prof = self._ws_profile()
        ws_tok = str(prof.get("access_token") or "").strip()
        if ws_tok:
            return ws_tok
        if self._ws_id() == "default":
            return str(config_manager.get().get("voice_gateway", {}).get("access_token") or "").strip()
        return ""

    def verify_token(self, provided_token: str) -> bool:
        expected = self._effective_access_token()
        if not expected:
            return True
        cand = str(provided_token or "").strip()
        if not cand:
            return False
        return hmac.compare_digest(expected.encode("utf-8"), cand.encode("utf-8"))

    def _shortcut_delivery_info(self, vg: Dict[str, Any]) -> Dict[str, Any]:
        icloud_url = str(vg.get("icloud_shortcut_url") or "").strip()
        macos_signer = _can_macos_sign_shortcuts()
        universal_path = SHORTCUTS_CACHE_DIR / "universal-signed.shortcut"
        has_presigned = False
        if universal_path.is_file():
            try:
                has_presigned = universal_path.read_bytes()[:4] == b"AEA1"
            except Exception:
                has_presigned = False
        if icloud_url:
            delivery_mode = "icloud_link"
        elif macos_signer or has_presigned:
            delivery_mode = "apple_signed_file"
        else:
            delivery_mode = "manual_wizard"
        return {
            "icloud_shortcut_url": icloud_url,
            "macos_signer_available": macos_signer,
            "presigned_universal_available": has_presigned,
            "shortcut_signed_ready": bool(icloud_url or macos_signer or has_presigned),
            "shortcut_delivery_mode": delivery_mode,
        }

    def get_gateway_metrics(self) -> Dict[str, Any]:
        cfg = config_manager.get()
        vg = cfg.get("voice_gateway", {})
        vlm = cfg.get("vlm", {})
        prof = self._ws_profile()
        today = datetime.date.today().isoformat()
        with self._lock:
            daily_used = self._daily_llm_calls if self._daily_date == today else 0
            tot_brief = self._total_brief_calls
            tot_ask = self._total_ask_calls
        sc_info = self._shortcut_delivery_info(vg)
        eff_tok = self._effective_access_token()
        return {
            "workspace_id": self._ws_id(),
            "workspace_name": str(prof.get("name") or cfg.get("community_name") or "邻里公益寻位"),
            "scene_type": str(prof.get("scene_type") or "community"),
            "preferred_voice_lang": str(prof.get("preferred_voice_lang") or ""),
            "enabled": bool(vg.get("enabled", True)),
            "port": int(cfg.get("voice_http_port", 18791)),
            "mobile_port": int(cfg.get("mobile_http_port", 18790)),
            "token_required": bool(eff_tok),
            "default_unit_id": str(prof.get("default_unit_id") or vg.get("default_unit_id") or "8-1"),
            "default_repeat": int(vg.get("default_repeat") or 2),
            "public_mobile_url": str(vg.get("public_mobile_url") or ""),
            "public_voice_url": str(vg.get("public_voice_url") or ""),
            "arrival_place": str(prof.get("arrival_place") or vg.get("arrival_place") or ""),
            "arrival_radius_m": int(prof.get("arrival_radius_m") or vg.get("arrival_radius_m") or 200),
            **sc_info,
            "llm_qa_enabled": bool(vg.get("llm_qa_enabled", True)),
            "llm_configured": bool(
                str(vlm.get("base_url") or "").strip() and str(vlm.get("api_key") or "").strip()
            ),
            "llm_model": str(vlm.get("model") or ""),
            "rate_limit_per_minute": int(vg.get("rate_limit_per_minute") or 12),
            "daily_llm_call_limit": int(vg.get("daily_llm_call_limit") or 300),
            "daily_llm_calls_used": daily_used,
            "total_brief_calls": tot_brief,
            "total_ask_calls": tot_ask,
            "bound_users_count": len(self.ctx.db.list_im_user_bindings()),
            "weixin": self.weixin_bridge.get_status(),
        }

    def resolve_unit_id(
        self,
        raw_unit: Optional[str],
        channel: str = "siri",
        external_uid: str = "",
    ) -> Tuple[str, str, Optional[Dict[str, Any]]]:
        units = self.ctx.db.list_units()
        by_id = {str(u["unit_id"]): u for u in units}

        binding: Optional[Dict[str, Any]] = None
        if external_uid:
            binding = self.ctx.db.get_im_user_binding(channel, external_uid)

        cand = str(raw_unit or "").strip()
        if not cand and binding:
            cand = str(binding.get("unit_id") or "").strip()

        if cand:
            matched = self._match_unit_from_text(cand, units)
            if matched:
                return str(matched["unit_id"]), str(matched["name"]), binding
            if cand in by_id:
                u = by_id[cand]
                return str(u["unit_id"]), str(u["name"]), binding

        vg_cfg = config_manager.get().get("voice_gateway", {})
        def_uid = str(vg_cfg.get("default_unit_id") or "8-1").strip()
        if def_uid in by_id:
            u = by_id[def_uid]
            return str(u["unit_id"]), str(u["name"]), binding
        if units:
            return str(units[0]["unit_id"]), str(units[0]["name"]), binding
        return "8-1", "8号楼1单元", binding

    @staticmethod
    def _match_unit_from_text(text: str, units: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        s = str(text or "").strip()
        if not s:
            return None
        by_id = {str(u["unit_id"]): u for u in units}
        if s in by_id:
            return by_id[s]

        # Match patterns like "8-1", "8号楼1单元", "8栋1单元", "8幢1"
        m = re.search(r"(\d{1,2})\s*(?:号楼|栋|幢|号|-|_|单元)\s*(\d{1,2})?", s)
        if m:
            b_num = int(m.group(1))
            u_num = int(m.group(2)) if m.group(2) else 1
            key = f"{b_num}-{u_num}"
            if key in by_id:
                return by_id[key]
            # Fallback to building match
            for u in units:
                if str(u["unit_id"]).startswith(f"{b_num}-"):
                    return u

        for u in units:
            uname = str(u.get("name") or "")
            if uname and uname in s:
                return u
        return None

    @staticmethod
    def _normalize_vehicle_type(vtype: Optional[str]) -> str:
        v = str(vtype or "sedan").strip().lower()
        if v in ("compact", "mini", "小型车", "紧凑"):
            return "compact"
        if v in ("suv", "mpv", "大型车", "商务车"):
            return "suv"
        return "sedan"

    @staticmethod
    def format_repeated_speech(
        single_text: str,
        repeat_count: int,
        repeat_core: str = "",
    ) -> str:
        clean = str(single_text or "").strip()
        if not clean:
            return "当前暂无可用停车播报信息。"
        if int(repeat_count or 1) <= 1:
            return clean
        core = str(repeat_core or "").strip() or strip_greeting_for_repeat(clean)
        return f"{clean} 再为您重复播报一遍：{core}"

    def build_voice_brief(
        self,
        unit_id: Optional[str] = None,
        repeat: Optional[int] = None,
        vehicle_type: Optional[str] = None,
        entry_gate_id: Optional[str] = None,
        channel: str = "siri",
        external_uid: str = "",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        with self._lock:
            self._total_brief_calls += 1

        cfg = config_manager.get()
        vg_cfg = cfg.get("voice_gateway", {})
        resolved_uid, resolved_uname, binding = self.resolve_unit_id(unit_id, channel, external_uid)

        if repeat is None:
            rep = int(binding.get("repeat_count") if binding else (vg_cfg.get("default_repeat") or 2))
        else:
            rep = 1 if int(repeat) <= 1 else 2

        vtype = self._normalize_vehicle_type(
            vehicle_type or (binding.get("vehicle_type") if binding else "sedan")
        )
        rec = self.ctx.recommender.recommend(
            unit_id=resolved_uid,
            vehicle_type=vtype,
            driver_tag="18791免手语音播报",
            acquire_lock=False,
            entry_gate_id=entry_gate_id,
        )
        zones = self.ctx.recommender.get_zone_summaries()
        total_free = sum(int(z.get("effective_free") or 0) for z in zones)
        total_spots = sum(int(z.get("total_capacity") or z.get("total_spots") or 0) for z in zones)

        single_text = str(rec.get("voice_text") or "").strip()
        if not single_text:
            single_text = f"全区共{total_spots}个监测车位，当前净余{total_free}位。"
        repeat_text = str(rec.get("repeat_voice_text") or "").strip() or strip_greeting_for_repeat(single_text)
        display_text = str(rec.get("display_text") or "").strip() or single_text
        speech_text = self.format_repeated_speech(single_text, rep, repeat_core=repeat_text)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        raw_primary = rec.get("primary")
        primary_zone = None
        if isinstance(raw_primary, dict):
            primary_zone = {
                "zone_id": raw_primary.get("zone_id", ""),
                "zone_name": raw_primary.get("zone_name", ""),
                "effective_free": int(raw_primary.get("effective_free") or 0),
                "distance_m": float(raw_primary.get("walk_meters") or raw_primary.get("distance_m") or 0),
            }
        alternatives: List[Dict[str, Any]] = []
        for cand in (rec.get("all_candidates") or [])[1:4]:
            if isinstance(cand, dict) and int(cand.get("effective_free") or 0) > 0:
                alternatives.append(
                    {
                        "zone_id": cand.get("zone_id", ""),
                        "display_name": cand.get("zone_name") or cand.get("group_name") or cand.get("zone_id", ""),
                        "effective_free": int(cand.get("effective_free") or 0),
                        "distance_m": float(cand.get("walk_meters") or cand.get("distance_m") or 0),
                    }
                )

        prof = self._ws_profile()
        comm_label = str(prof.get("name") or cfg.get("community_name") or "邻里公益寻位")
        return {
            "ok": True,
            "workspace_id": self._ws_id(),
            "engine": "zero_llm_brief",
            "community_name": comm_label,
            "unit_id": resolved_uid,
            "unit_name": resolved_uname,
            "repeat": rep,
            "vehicle_type": vtype,
            "has_recommendation": bool(rec.get("has_recommendation")),
            "total_effective_free": total_free,
            "total_spots": total_spots,
            "total_capacity": total_spots,
            "single_text": single_text,
            "repeat_text": repeat_text,
            "display_text": display_text,
            "speech_text": speech_text,
            "primary": raw_primary,
            "primary_zone": primary_zone,
            "backup": rec.get("backup"),
            "alternatives": alternatives,
            "public_mobile_url": str(vg_cfg.get("public_mobile_url") or ""),
            "elapsed_ms": elapsed_ms,
        }

    def _check_and_consume_llm_budget(self, client_key: str, vg_cfg: Dict[str, Any]) -> Tuple[bool, str]:
        now = time.time()
        per_min = max(2, int(vg_cfg.get("rate_limit_per_minute") or 12))
        daily_cap = max(10, int(vg_cfg.get("daily_llm_call_limit") or 300))
        today = datetime.date.today().isoformat()

        with self._lock:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_llm_calls = 0

            bucket = self._rate_buckets.setdefault(client_key, deque())
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if len(bucket) >= per_min:
                return False, "rate_limited"
            if self._daily_llm_calls >= daily_cap:
                return False, "daily_budget_reached"

            bucket.append(now)
            self._daily_llm_calls += 1
            return True, ""

    def _call_custom_llm_text(
        self, vlm_cfg: Dict[str, Any], system_prompt: str, user_question: str, max_tokens: int = 240
    ) -> Tuple[Optional[str], Optional[str]]:
        url, api_style, primary_headers, fallback_headers = VisionPipeline.resolve_vlm_request_target(
            vlm_cfg
        )
        if not str(vlm_cfg.get("base_url") or "").strip() or not str(vlm_cfg.get("api_key") or "").strip():
            return None, "未配置大模型 Endpoint 或 API Key"

        timeout = min(18.0, float(vlm_cfg.get("timeout_seconds") or 18.0))
        model_name = str(vlm_cfg.get("model") or "gpt-4o").strip()

        if api_style == "responses":
            payload: Dict[str, Any] = {
                "model": model_name,
                "reasoning": {"effort": "low"},
                "max_output_tokens": max(max_tokens, 512),
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_question}]},
                ],
            }
        else:
            payload = {
                "model": model_name,
                "temperature": 0.2,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_question},
                ],
            }

        try:
            resp = requests.post(url, headers=primary_headers, json=payload, timeout=timeout)
            if resp.status_code == 401 and fallback_headers != primary_headers:
                resp = requests.post(url, headers=fallback_headers, json=payload, timeout=timeout)
            if resp.status_code == 400 and api_style == "chat_completions" and "max_tokens" in payload:
                alt_payload = dict(payload)
                alt_payload.pop("max_tokens", None)
                alt_payload.pop("temperature", None)
                alt_payload["max_completion_tokens"] = max(max_tokens, 512)
                resp = requests.post(url, headers=primary_headers, json=alt_payload, timeout=timeout)

            if resp.status_code >= 400:
                return None, f"HTTP {resp.status_code}: {resp.text[:160]}"
            data = resp.json()
            txt = VisionPipeline._extract_responses_api_text(data)
            return (txt or None), (None if txt else "大模型返回内容为空")
        except Exception as exc:
            return None, str(exc)[:180]

    def ask_voice_assistant(
        self,
        question: str,
        unit_id: Optional[str] = None,
        repeat: Optional[int] = None,
        vehicle_type: Optional[str] = None,
        channel: str = "siri",
        external_uid: str = "",
        client_ip: str = "local",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        with self._lock:
            self._total_ask_calls += 1

        cfg = config_manager.get()
        vg_cfg = cfg.get("voice_gateway", {})
        max_chars = max(60, min(600, int(vg_cfg.get("max_question_chars") or 300)))
        clean_q = re.sub(r"\s+", " ", str(question or "")).strip()[:max_chars]
        normalized_wake = re.sub(r"[，。！？,.!?、\s]+", "", clean_q)

        units = self.ctx.db.list_units()

        # 1) Fast-path: voice binding command ("绑定8号楼1单元" / "把我家设为6-1" / "默认去5号楼")
        if any(kw in clean_q for kw in ("绑定", "设为我家", "设置我家", "默认去", "默认单元", "记住我家")):
            matched_u = self._match_unit_from_text(clean_q, units)
            if matched_u:
                target_uid = str(matched_u["unit_id"])
                target_uname = str(matched_u["name"])
                rep_bind = 1 if ("一遍" in clean_q or "1遍" in clean_q) else 2
                if external_uid:
                    self.ctx.db.upsert_im_user_binding(
                        channel=channel,
                        external_uid=external_uid,
                        unit_id=target_uid,
                        repeat_count=rep_bind,
                    )
                brief = self.build_voice_brief(
                    unit_id=target_uid,
                    repeat=1,
                    vehicle_type=vehicle_type,
                    channel=channel,
                    external_uid=external_uid,
                )
                ans = f"已为您绑定目的地{target_uname}。{brief['single_text']}"
                return {
                    **brief,
                    "engine": "fast_bind",
                    "question": clean_q,
                    "answer": ans,
                    "speech_text": self.format_repeated_speech(ans, repeat or 1),
                    "bound_unit_id": target_uid,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                }

        # Extract inline unit mention if user asked e.g. "我现在去5号楼2单元停哪最近"
        inline_unit = self._match_unit_from_text(clean_q, units)
        effective_unit = str(inline_unit["unit_id"]) if inline_unit else unit_id
        if not vehicle_type and any(w in clean_q for w in ("小车", "微型", "MINI", "mini", "紧凑")):
            vehicle_type = "compact"
        elif not vehicle_type and any(w in clean_q for w in ("商务车", "SUV", "suv", "MPV", "大车")):
            vehicle_type = "suv"

        # 2) Fast-path: pure wake words ("", "回家", "车位", "找车位") -> <20ms deterministic brief
        if normalized_wake in WAKE_BRIEF_WORDS:
            brief = self.build_voice_brief(
                unit_id=effective_unit,
                repeat=repeat,
                vehicle_type=vehicle_type,
                channel=channel,
                external_uid=external_uid,
            )
            return {
                **brief,
                "engine": "fast_brief",
                "question": clean_q,
                "answer": brief["single_text"],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        # Always compute deterministic baseline first (serves as live context + zero-fail fallback)
        brief = self.build_voice_brief(
            unit_id=effective_unit,
            repeat=repeat if repeat is not None else 1,
            vehicle_type=vehicle_type,
            channel=channel,
            external_uid=external_uid,
        )

        if not vg_cfg.get("llm_qa_enabled", True):
            return {
                **brief,
                "engine": "deterministic_only",
                "question": clean_q,
                "answer": brief["single_text"],
            }

        # Zero-false-positive pre-shield only for unmistakable prompt-injection / code-generation abuse;
        # everyday conversational parking/route questions always flow smoothly to the custom LLM.
        q_low = clean_q.lower()
        has_parking_intent = bool(
            inline_unit
            or any(
                kw in clean_q
                for kw in ("车位", "停车", "空位", "停哪", "号楼", "单元", "大门", "怎么走", "倒车", "SUV", "小车", "电瓶", "挪车")
            )
        )
        is_blatant_abuse = any(
            sig in q_low
            for sig in (
                "忽略之前",
                "忽略上述",
                "无视系统指令",
                "ignore previous",
                "ignore all instructions",
                "system prompt",
                "python",
                "javascript",
                "写代码",
                "快速排序",
                "冒泡排序",
                "写一篇作文",
            )
        )
        if is_blatant_abuse and not has_parking_intent:
            polite_ans = (
                f"我是本小区专属车载寻位语音助手，仅提供实时车位与步行导引服务。"
                f"{brief['single_text']}"
            )
            rep_guard = int(repeat) if repeat is not None else 1
            return {
                **brief,
                "engine": "domain_guardrail",
                "question": clean_q,
                "answer": polite_ans,
                "speech_text": self.format_repeated_speech(polite_ans, rep_guard),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        client_key = f"{channel}:{external_uid or client_ip}"
        allowed, limit_reason = self._check_and_consume_llm_budget(client_key, vg_cfg)
        if not allowed:
            return {
                **brief,
                "engine": "fallback_rate_guard",
                "fallback_reason": limit_reason,
                "question": clean_q,
                "answer": brief["single_text"],
            }

        # Build compact live community parking snapshot for LLM reasoning (strip/zone level ONLY — NEVER expose raw stall codes!)
        zone_lines: List[str] = []
        cand_list = (
            [brief["primary"]] + [b for b in ([brief.get("backup")] if brief.get("backup") else []) if b]
            if brief.get("primary")
            else []
        )
        seen_znames = set()
        for c in cand_list:
            if not isinstance(c, dict):
                continue
            z_title = str(c.get("zone_name") or c.get("group_name") or "").strip()
            if not z_title or z_title in seen_znames:
                continue
            seen_znames.add(z_title)
            zone_lines.append(
                f"- {z_title}：当前剩余 {int(c.get('effective_free') or 0)} 个停车位，步行到{brief['unit_name']}约 {int(c.get('walk_meters') or 0)} 米"
            )
        for alt in brief.get("alternatives") or []:
            z_title = str(alt.get("display_name") or "").strip()
            if not z_title or z_title in seen_znames:
                continue
            seen_znames.add(z_title)
            zone_lines.append(
                f"- {z_title}：当前剩余 {int(alt.get('effective_free') or 0)} 个停车位，步行约 {int(round(float(alt.get('distance_m') or 0)))} 米"
            )
        if not zone_lines:
            for z in self.ctx.recommender.get_zone_summaries()[:10]:
                z_title = str(z.get("name") or z.get("zone_name") or z.get("zone_id") or "").strip()
                z_cap = int(z.get("total_capacity") or z.get("total_spots") or 0)
                zone_lines.append(
                    f"- {z_title}：当前剩余 {int(z.get('effective_free') or 0)}/{z_cap} 个停车位"
                )

        prof = self._ws_profile()
        comm_name = str(prof.get("name") or cfg.get("community_name") or "小区公益寻位")
        sys_prompt = (
            f"你是「{comm_name}」车载免手语音寻位助手，正在为开车的业主提供自然亲切的实时停车引导。\n"
            "【回答铁律】\n"
            "1. 自然亲切、简洁口语化，适合车载语音直接朗读（控制在 2~3 句话、90 字以内，不要使用 Markdown 星号、列表符号或代码块）。\n"
            "2. 严禁播报任何具体车位编号或字母数字代码（例如绝对不要说 10LH2-02、5BV7-01、02号车位 等），开车时听编号费劲且无意义；只需要清楚告诉车主：目前剩余几个车位、优先推荐停在哪个停车带（剩余几个停车位、步行到单元门约多少米），如有备选停车带再补充备选哪里、剩余几个即可。\n"
            "3. 严格依据下方实时车位数据作答，宁少勿多，绝不编造空车位。\n"
            "4. 若用户提问与本场所停车、车位、楼栋步行路线无关，请礼貌用一句话说明你仅提供实时寻位服务，并顺带播报当前总剩余车位数。\n\n"
            f"【实时态势数据】\n"
            f"- 车主目标目的地：{brief['unit_name']}\n"
            f"- 全区实时统计：共 {brief['total_spots']} 个监测车位，当前剩余可停 {brief['total_effective_free']} 个\n"
            f"- 系统推荐参考：{brief['single_text']}\n"
            "- 可选停车带实时余位：\n" + "\n".join(zone_lines)
        )

        llm_ans, llm_err = self._call_custom_llm_text(
            cfg.get("vlm", {}),
            system_prompt=sys_prompt,
            user_question=clean_q,
            max_tokens=220,
        )
        if not llm_ans:
            return {
                **brief,
                "engine": "fallback_deterministic",
                "fallback_reason": llm_err or "llm_unavailable",
                "question": clean_q,
                "answer": brief["single_text"],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        clean_ans = re.sub(r"[*#`]+", "", llm_ans).strip()
        # Strip any accidental raw stall code mentions like "，车位号是10LH2-02" or "(10LH2-02)"
        clean_ans = re.sub(
            r"[，,、；;\s]*(?:具体)?车位(?:编)?号(?:是|为|:|：)?\s*[A-Za-z0-9_-]+(?:号(?:车位|位)?)?",
            "",
            clean_ans,
        )
        clean_ans = re.sub(r"[（(]\s*[0-9]{1,2}[A-Za-z]{1,3}[0-9]-[0-9]{1,2}\s*[）)]", "", clean_ans).strip()
        rep_out = int(repeat) if repeat is not None else 1
        return {
            **brief,
            "engine": "custom_llm",
            "question": clean_q,
            "answer": clean_ans,
            "speech_text": self.format_repeated_speech(clean_ans, rep_out),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }

    @staticmethod
    def _prefer_lan_base_url(base_url: str) -> str:
        clean_base = str(base_url or "").strip().rstrip("/")
        if not clean_base:
            return clean_base
        parsed = urlparse(clean_base)
        host_only = (parsed.hostname or "").lower()
        if host_only in ("127.0.0.1", "localhost"):
            bind_host = str(config_manager.get().get("voice_http_host", "0.0.0.0")).strip()
            if bind_host in ("0.0.0.0", "::", ""):
                lan_ip = _detect_lan_ip()
                if lan_ip and lan_ip != "127.0.0.1":
                    port_part = f":{parsed.port}" if parsed.port else ""
                    return f"{parsed.scheme or 'http'}://{lan_ip}{port_part}"
        return clean_base

    @staticmethod
    def sanitize_shortcut_name(raw_name: Optional[str], fallback: str = "小区车位") -> str:
        # Strip dots (`.` / `．`) as well as path chars so `<name>.shortcut` NEVER has a double extension
        # that breaks iOS/macOS Uniform Type Identifier (`com.apple.shortcut`) detection!
        cleaned = re.sub(r'[\\/:*?"<>|.\r\n\t．]+', "", str(raw_name or "")).strip()
        return (cleaned[:32] if cleaned else fallback).strip() or fallback

    def _resolve_unit_display_and_list(self, unit_id: str) -> Tuple[str, str, List[Dict[str, str]]]:
        raw_uid = str(unit_id or "8-1").strip() or "8-1"
        units: List[Dict[str, str]] = []
        if hasattr(self.ctx, "db") and self.ctx.db is not None:
            try:
                for u in self.ctx.db.list_units() or []:
                    uid = str(u.get("unit_id") or "").strip()
                    uname = str(u.get("name") or uid).strip()
                    if uid:
                        units.append({"unit_id": uid, "name": uname})
            except Exception:
                units = []
        for u in units:
            if u["unit_id"] == raw_uid or u["name"] == raw_uid:
                disp = f"{u['name']} ({u['unit_id']})" if u["unit_id"] not in u["name"] else u["name"]
                return u["unit_id"], disp, units
        matched = self._match_unit_from_text(raw_uid, units) if units else None
        if matched:
            uid = str(matched["unit_id"])
            uname = str(matched.get("name") or uid)
            disp = f"{uname} ({uid})" if uid not in uname else uname
            return uid, disp, units
        return raw_uid, raw_uid, units

    @staticmethod
    def _normalize_show_map_mode(show_map: Any) -> str:
        s = str(show_map or "").strip().lower()
        if s in ("map", "map_only", "only_map"):
            return "map"
        if s in ("both", "hud", "dynamic", "interactive", "card", "inline", "1", "true", "yes"):
            return "both"
        if s in ("silent", "voice_only", "mute_popup", "0"):
            return "silent"
        if s in ("webpage", "safari"):
            return "webpage"
        if s in ("snapshot", "png", "image", "screenshot", "quicklook"):
            return "snapshot"
        if s in ("browser", "open", "18790", "full"):
            return "browser"
        return "text"

    @staticmethod
    def _normalize_unit_mode(unit_mode: Any) -> str:
        m = str(unit_mode or "fixed").strip().lower()
        if m == "menu":
            return "menu"
        if m in ("setup_once", "first_run"):
            return "setup_once"
        if m == "import_question":
            return "import_question"
        return "fixed"

    def build_siri_shortcut_plist(
        self,
        base_url: str,
        unit_id: str = "8-1",
        repeat: int = 2,
        token: str = "",
        vehicle_type: str = "sedan",
        mode: str = "brief",
        shortcut_name: str = "小区车位",
        unit_mode: str = "fixed",
        show_map: str = "text",
        arrival_place: str = "",
        arrival_radius_m: int = 200,
        mobile_base_url: str = "",
        universal_server_question: bool = False,
        device_uid: str = "",
    ) -> bytes:
        """
        Generate an Apple Shortcuts `.shortcut` workflow definition (binary plist):
        - Avoids `WFWorkflowImportQuestions` during normal Chrome/Safari import (which triggers an iOS 26
          Chrome `openInPlace` setup-sheet hang), supporting:
          * `fixed` (default): directly uses the unit chosen on `:18791`, 1-tap install & 0-prompt run!
          * `setup_once`: 1-tap install (no import wizard!), prompts ONCE on 1st run and saves permanently!
          * `menu`: prompts unit list on every run.
        - Supports 3 popup modes (`text` only, `map` only, `both` text + map, plus `silent`) and runs
          all `downloadurl` calls BEFORE `speaktext(WFSpeakTextWait=False)` so voice and visual popup
          launch nearly simultaneously (~30ms apart) without XPC races!
        """
        clean_base = str(base_url or "").strip().rstrip("/")
        safe_name = self.sanitize_shortcut_name(shortcut_name, "小区车位")
        rep_val = 1 if int(repeat or 2) <= 1 else 2
        vtype = self._normalize_vehicle_type(vehicle_type)
        act_mode = "ask" if str(mode or "").strip().lower() == "ask" else "brief"
        u_mode = self._normalize_unit_mode(unit_mode)
        map_mode = self._normalize_show_map_mode(show_map)
        resolved_uid, unit_display, all_units = self._resolve_unit_display_and_list(unit_id)

        place_clean = str(arrival_place or "").strip()
        radius_clean = max(50, min(2000, int(arrival_radius_m or 200)))

        vtype_label = {"sedan": "标准轿车", "suv": "大型SUV/MPV", "compact": "紧凑小车"}.get(vtype, "标准轿车")
        mode_desc = "Siri 语音提问自有大模型" if act_mode == "ask" else f"极速车位播报（{rep_val} 遍 · {vtype_label}）"
        if u_mode == "menu":
            dest_desc = f"每次运行时从单元门列表点选（默认备选 {resolved_uid}）"
        elif u_mode in ("setup_once", "import_question"):
            dest_desc = f"首次运行时弹窗选一次单元并永久记住（默认 {unit_display}）"
        else:
            dest_desc = unit_display
        popup_desc = {
            "text": "播报同时弹出「精炼寻位文字」",
            "map": "播报同时弹出「纯净动态平面图」",
            "both": "播报同时弹出「精炼文字 + 动态平面图」",
            "silent": "纯语音播报（不弹画面）",
            "webpage": "弹出网页版动态平面图",
            "snapshot": "弹出当时一刻高清平面图截图",
            "browser": "跳出全屏交互平面图网页",
        }[map_mode]
        ws_id_cur = self._ws_id()
        ws_qs = f"&ws={quote(ws_id_cur)}" if ws_id_cur and ws_id_cur != "default" else ""
        prof_cur = self._ws_profile()
        site_title = str(prof_cur.get("name") or "")
        comment_lines = [
            f"【{safe_name}】免手语音寻位（支持 嘿 Siri · 主屏幕一键点播 · CarPlay）",
            (f"所属场所：{site_title} ｜ " if site_title else "") + f"目的地：{dest_desc} ｜ 模式：{mode_desc}",
            f"屏幕显示：{popup_desc}",
        ]
        if place_clean:
            comment_lines.append(
                f"📍 进小区自动播报：打开快捷指令「自动化 → 到达」搜索「{place_clean}」（{radius_clean}米），选「立即运行」并绑定「{safe_name}」即可到家门口自动播报"
            )

        actions: List[Dict[str, Any]] = [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
                "WFWorkflowActionParameters": {
                    "WFCommentActionText": "\n".join(comment_lines),
                },
            },
        ]
        import_questions: List[Dict[str, Any]] = []

        tok_qs = f"{ws_qs}&token={quote(token)}" if token else ws_qs
        srv_uuid: Optional[str] = None
        if universal_server_question:
            srv_uuid = str(uuid.uuid4()).upper()
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                    "WFWorkflowActionParameters": {
                        "UUID": srv_uuid,
                        "WFTextActionText": clean_base,
                    },
                }
            )
            import_questions.append(
                {
                    "ActionIndex": len(actions) - 1,
                    "Category": "Parameter",
                    "DefaultValue": clean_base,
                    "ParameterKey": "WFTextActionText",
                    "Text": "请确认语音网关地址（如 https://voice.你的域名.com 或 http://局域网IP:18791）：",
                }
            )

        raw_unit_uuid = str(uuid.uuid4()).upper()
        enc_unit_uuid = str(uuid.uuid4()).upper()
        unit_out_name = "目的地单元"
        use_server_bound_uid = False
        clean_dev_uid = re.sub(r"[^a-zA-Z0-9_-]+", "", str(device_uid or "").strip())[:36]
        if u_mode == "setup_once" and all_units and not universal_server_question:
            # First-run-only unit setup:
            # - `/api/voice/check-bind?uid=<bind_id>` returns `204 No Content` (empty) when unbound,
            #   and `200 OK` (bound unit_id) once bound!
            # - `WFCondition: 101` ("没有任何值 / Does Not Have Any Value") works natively on `URL的内容`
            #   with ZERO extra parameter (never triggers iOS "参数问题"!), runs ONCE on Run #1, and skips forever on Run #2+!
            use_server_bound_uid = True
            bind_id = clean_dev_uid or f"sc_{hashlib.sha256(f'{self._ws_id()}|{safe_name}|{resolved_uid}'.encode('utf-8')).hexdigest()[:12]}"
            chk_uuid = str(uuid.uuid4()).upper()
            cnt_uuid = str(uuid.uuid4()).upper()
            if_grp = str(uuid.uuid4()).upper()
            mn_grp = str(uuid.uuid4()).upper()
            mn_out_uuid = str(uuid.uuid4()).upper()
            mn_enc_uuid = str(uuid.uuid4()).upper()

            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": chk_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": f"{clean_base}/api/voice/check-bind?uid={quote(bind_id)}{tok_qs}",
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.count",
                    "WFWorkflowActionParameters": {
                        "UUID": cnt_uuid,
                        "WFCountType": "Characters",
                        "Input": {
                            "Value": {
                                "OutputName": "URL的内容",
                                "OutputUUID": chk_uuid,
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        },
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": if_grp,
                        "WFControlFlowMode": 0,
                        "WFCondition": 0,
                        "WFNumberValue": "1",
                        "WFInput": {
                            "Type": "Variable",
                            "Variable": {
                                "Value": {
                                    "OutputName": "数量",
                                    "OutputUUID": cnt_uuid,
                                    "Type": "ActionOutput",
                                },
                                "WFSerializationType": "WFTextTokenAttachment",
                            },
                        },
                    },
                }
            )
            menu_titles_once: List[str] = []
            menu_pairs_once: List[Tuple[str, str]] = []
            for u in all_units[:24]:
                uid_i = u["unit_id"]
                uname_i = u["name"]
                title_i = f"{uname_i} ({uid_i})" if uid_i not in uname_i else uname_i
                menu_titles_once.append(title_i)
                menu_pairs_once.append((title_i, uid_i))
            manual_title_once = "✏️ 手动输入其他单元"
            menu_titles_once.append(manual_title_once)

            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": mn_grp,
                        "WFControlFlowMode": 0,
                        "WFMenuPrompt": "首次使用请选择您的默认单元门（仅需选这一次，后续永久直连播报）",
                        "WFMenuItems": menu_titles_once,
                    },
                }
            )
            for m_title, m_val in menu_pairs_once:
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                        "WFWorkflowActionParameters": {
                            "GroupingIdentifier": mn_grp,
                            "WFControlFlowMode": 1,
                            "WFMenuItemTitle": m_title,
                        },
                    }
                )
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                        "WFWorkflowActionParameters": {"WFTextActionText": m_val},
                    }
                )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": mn_grp,
                        "WFControlFlowMode": 1,
                        "WFMenuItemTitle": manual_title_once,
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
                    "WFWorkflowActionParameters": {
                        "WFAskActionPrompt": "请输入回家单元号（如 8-1 或 9-2）",
                        "WFAskActionDefaultAnswer": resolved_uid,
                        "WFInputType": "Text",
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": mn_grp,
                        "UUID": mn_out_uuid,
                        "WFControlFlowMode": 2,
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                    "WFWorkflowActionParameters": {
                        "UUID": mn_enc_uuid,
                        "WFEncodeMode": "Encode",
                        "WFInput": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": "菜单结果",
                                        "OutputUUID": mn_out_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": "\ufffc",
                            },
                            "WFSerializationType": "WFTextTokenString",
                        },
                    },
                }
            )
            qb_prefix = f"{clean_base}/api/voice/quick-bind?uid={quote(bind_id)}&unit="
            qb_str = f"{qb_prefix}\ufffc{tok_qs}"
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "WFHTTPMethod": "GET",
                        "WFURL": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{" + str(len(qb_prefix)) + ", 1}": {
                                        "OutputName": "URL编码后的文本",
                                        "OutputUUID": mn_enc_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": qb_str,
                            },
                            "WFSerializationType": "WFTextTokenString",
                        },
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": if_grp,
                        "WFControlFlowMode": 2,
                    },
                }
            )
            tok_qs = f"&uid={quote(bind_id)}{tok_qs}"
        elif u_mode == "menu" and all_units and not universal_server_question:
            group_id = str(uuid.uuid4()).upper()
            menu_titles: List[str] = []
            menu_pairs: List[Tuple[str, str]] = []
            for u in all_units[:24]:
                uid_i = u["unit_id"]
                uname_i = u["name"]
                title_i = f"{uname_i} ({uid_i})" if uid_i not in uname_i else uname_i
                menu_titles.append(title_i)
                menu_pairs.append((title_i, title_i))
            manual_title = "✏️ 手动输入其他单元（备选）"
            menu_titles.append(manual_title)

            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": group_id,
                        "WFControlFlowMode": 0,
                        "WFMenuPrompt": "请选择回家目的单元门",
                        "WFMenuItems": menu_titles,
                    },
                }
            )
            for m_title, m_val in menu_pairs:
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                        "WFWorkflowActionParameters": {
                            "GroupingIdentifier": group_id,
                            "WFControlFlowMode": 1,
                            "WFMenuItemTitle": m_title,
                        },
                    }
                )
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                        "WFWorkflowActionParameters": {
                            "WFTextActionText": m_val,
                        },
                    }
                )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": group_id,
                        "WFControlFlowMode": 1,
                        "WFMenuItemTitle": manual_title,
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
                    "WFWorkflowActionParameters": {
                        "WFAskActionPrompt": "请输入回家单元号（例如 8-1 或 3-1）",
                        "WFAskActionDefaultAnswer": resolved_uid,
                        "WFInputType": "Text",
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": group_id,
                        "UUID": raw_unit_uuid,
                        "WFControlFlowMode": 2,
                    },
                }
            )
            unit_out_name = "菜单结果"
        else:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                    "WFWorkflowActionParameters": {
                        "UUID": raw_unit_uuid,
                        "WFTextActionText": unit_display,
                    },
                }
            )
            unit_out_name = "文本"
            if universal_server_question or u_mode == "import_question":
                import_questions.append(
                    {
                        "ActionIndex": len(actions) - 1,
                        "Category": "Parameter",
                        "DefaultValue": unit_display,
                        "ParameterKey": "WFTextActionText",
                        "Text": "请输入或确认您的回家目的单元门（如 8-1、3-1 或 8号楼1单元）：",
                    }
                )

        if not use_server_bound_uid:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                    "WFWorkflowActionParameters": {
                        "UUID": enc_unit_uuid,
                        "WFEncodeMode": "Encode",
                        "WFInput": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": unit_out_name,
                                        "OutputUUID": raw_unit_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": "\ufffc",
                            },
                            "WFSerializationType": "WFTextTokenString",
                        },
                    },
                }
            )

        def _make_token_url(path_and_prefix: str, suffix_str: str, extra_tokens: Optional[List[Tuple[str, str, str]]] = None) -> Any:
            """Build a URL string or `WFTextTokenString` supporting bound-uid or encoded unit + tokens."""
            attachments: Dict[str, Any] = {}
            if srv_uuid:
                s_builder = "\ufffc" + path_and_prefix
                attachments["{0, 1}"] = {
                    "OutputName": "文本",
                    "OutputUUID": srv_uuid,
                    "Type": "ActionOutput",
                }
            else:
                s_builder = clean_base + path_and_prefix
            if not use_server_bound_uid:
                u_pos = len(s_builder)
                s_builder += "\ufffc"
                attachments["{" + str(u_pos) + ", 1}"] = {
                    "OutputName": "URL编码后的文本",
                    "OutputUUID": enc_unit_uuid,
                    "Type": "ActionOutput",
                }
            if extra_tokens:
                for mid_lit, tok_uuid, tok_name in extra_tokens:
                    s_builder += mid_lit
                    t_pos = len(s_builder)
                    s_builder += "\ufffc"
                    attachments["{" + str(t_pos) + ", 1}"] = {
                        "OutputName": tok_name,
                        "OutputUUID": tok_uuid,
                        "Type": "ActionOutput",
                    }
            s_builder += suffix_str
            if not attachments:
                return s_builder
            return {
                "Value": {
                    "attachmentsByRange": attachments,
                    "string": s_builder,
                },
                "WFSerializationType": "WFTextTokenString",
            }

        speech_out_uuid = str(uuid.uuid4()).upper()
        if act_mode == "ask":
            # Use `is.workflow.actions.ask` instead of `dictatetext`:
            # - Via "Hey Siri" / CarPlay: Siri speaks the prompt and listens by voice hands-free!
            # - On macOS / Home Screen tap: opens native input without crashing when Keyboard Dictation is disabled!
            ask_q_uuid = str(uuid.uuid4()).upper()
            enc_q_uuid = str(uuid.uuid4()).upper()
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
                    "WFWorkflowActionParameters": {
                        "UUID": ask_q_uuid,
                        "WFAskActionPrompt": "请说出或输入您想问的车位问题",
                        "WFAskActionDefaultAnswer": "现在停哪个区域走回家最近？",
                        "WFInputType": "Text",
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                    "WFWorkflowActionParameters": {
                        "UUID": enc_q_uuid,
                        "WFEncodeMode": "Encode",
                        "WFInput": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": "提供的输入",
                                        "OutputUUID": ask_q_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": "\ufffc",
                            },
                            "WFSerializationType": "WFTextTokenString",
                        },
                    },
                }
            )
            wf_api_url = _make_token_url(
                "/api/voice/ask?unit=",
                f"&repeat=1&vehicle_type={quote(vtype)}&format=text{tok_qs}",
                extra_tokens=[("&q=", enc_q_uuid, "URL编码后的文本")],
            )
        else:
            # Pure plain-text speech endpoint (`format=speech`) -> 100% iOS/macOS compatible without dictionary coercion!
            wf_api_url = _make_token_url(
                "/api/voice/brief?unit=",
                f"&repeat={rep_val}&vehicle_type={quote(vtype)}&format=speech{tok_qs}",
            )

        def _wrap_dl_param(url_token_dict: Dict[str, Any]) -> Any:
            if not universal_server_question:
                return url_token_dict
            comp_uuid = str(uuid.uuid4()).upper()
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                    "WFWorkflowActionParameters": {
                        "UUID": comp_uuid,
                        "WFTextActionText": url_token_dict,
                    },
                }
            )
            return {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "文本",
                            "OutputUUID": comp_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            }

        wf_download_param = _wrap_dl_param(wf_api_url)
        actions.append(
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                "WFWorkflowActionParameters": {
                    "UUID": speech_out_uuid,
                    "WFHTTPMethod": "GET",
                    "WFURL": wf_download_param,
                },
            }
        )

        # Fetch ONLY the exact visual payload needed BEFORE `speaktext`, so all HTTP `downloadurl`
        # calls finish cleanly before audio starts, AND `WFSpeakTextWait=False` can launch speech +
        # visual popup almost simultaneously (~30ms apart) without waiting for speech to finish!
        display_source_uuid = speech_out_uuid
        display_source_name = "URL的内容"
        if act_mode == "brief" and map_mode in ("text", "browser"):
            card_out_uuid = str(uuid.uuid4()).upper()
            card_url_param = _make_token_url(
                "/api/voice/brief?unit=",
                f"&vehicle_type={quote(vtype)}&format=card{tok_qs}",
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": card_out_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": _wrap_dl_param(card_url_param),
                    },
                }
            )
            display_source_uuid = card_out_uuid

        hud_dl_uuid: Optional[str] = None
        snap_dl_uuid: Optional[str] = None
        if map_mode in ("both", "map") and act_mode == "brief":
            # Borderless Quick Look Dynamic SVG Map (`/api/voice/hud/...html` -> `previewdocument`)!
            hud_dl_uuid = str(uuid.uuid4()).upper()
            hud_doc_slug = quote(f"{safe_name}-实时态势.html")
            hud_view_q = "map" if map_mode == "map" else "both"
            hud_url_param = _make_token_url(
                f"/api/voice/hud/{hud_doc_slug}?unit=",
                f"&view={hud_view_q}&repeat={rep_val}&vehicle_type={quote(vtype)}{tok_qs}",
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": hud_dl_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": _wrap_dl_param(hud_url_param),
                    },
                }
            )
        elif map_mode == "snapshot" and act_mode == "brief":
            snap_dl_uuid = str(uuid.uuid4()).upper()
            snap_url_param = _make_token_url(
                "/api/voice/map.jpg?unit=",
                f"&view=both&vehicle_type={quote(vtype)}{tok_qs}",
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": snap_dl_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": _wrap_dl_param(snap_url_param),
                    },
                }
            )

        # Start speaking immediately (`WFSpeakTextWait=False`), then ~30ms later pop up the chosen view
        # (`仅文字` / `仅平面图` / `文字+平面图`) nearly simultaneously without waiting for speech to finish!
        actions.append(
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                "WFWorkflowActionParameters": {
                    "WFSpeakTextWait": False,
                    "WFSpeakTextLanguage": "zh-CN",
                    "WFText": {
                        "Value": {
                            "attachmentsByRange": {
                                "{0, 1}": {
                                    "OutputName": "URL的内容",
                                    "OutputUUID": speech_out_uuid,
                                    "Type": "ActionOutput",
                                }
                            },
                            "string": "\ufffc",
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                },
            }
        )

        if map_mode in ("both", "map") and act_mode == "brief" and hud_dl_uuid:
            # Borderless Quick Look (`previewdocument`) — renders either Map-Only (`view=map`) or Text+Map (`view=both`)!
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.previewdocument",
                    "WFWorkflowActionParameters": {
                        "WFInput": {
                            "Value": {
                                "OutputName": "URL的内容",
                                "OutputUUID": hud_dl_uuid,
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        }
                    },
                }
            )
        elif map_mode == "silent" and act_mode == "brief":
            pass
        elif map_mode == "webpage" and act_mode == "brief":
            hud_url_param = _make_token_url(
                "/api/voice/hud?unit=",
                f"&repeat={rep_val}&vehicle_type={quote(vtype)}{tok_qs}",
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.showwebpage",
                    "WFWorkflowActionParameters": {
                        "WFURL": hud_url_param,
                        "WFEnterSafariReader": False,
                    },
                }
            )
        elif map_mode == "snapshot" and act_mode == "brief" and snap_dl_uuid:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.previewdocument",
                    "WFWorkflowActionParameters": {
                        "WFInput": {
                            "Value": {
                                "OutputName": "实时车位截图",
                                "OutputUUID": snap_dl_uuid,
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        }
                    },
                }
            )
        elif map_mode == "browser":
            mob_base = str(mobile_base_url or "").strip().rstrip("/")
            if not mob_base:
                mob_port = int(config_manager.get().get("mobile_http_port", 18790))
                parsed_b = urlparse(clean_base)
                if parsed_b.hostname:
                    mob_base = f"{parsed_b.scheme or 'http'}://{parsed_b.hostname}:{mob_port}"
                else:
                    mob_base = clean_base.replace(":18791", f":{mob_port}")
            mob_ws_seg = f"/w/{quote(ws_id_cur)}" if ws_id_cur and ws_id_cur != "default" else ""
            mob_prefix = f"{mob_base}{mob_ws_seg}/?unit="
            mob_tail = f"&mute=1&backdrop=1{('&token=' + quote(token)) if token else ''}"
            mob_str = f"{mob_prefix}\ufffc{mob_tail}"
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
                    "WFWorkflowActionParameters": {
                        "WFInput": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{" + str(len(mob_prefix)) + ", 1}": {
                                        "OutputName": "URL编码后的文本",
                                        "OutputUUID": enc_unit_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": mob_str,
                            },
                            "WFSerializationType": "WFTextTokenString",
                        }
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                    "WFWorkflowActionParameters": {
                        "Text": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": display_source_name,
                                        "OutputUUID": display_source_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": "\ufffc",
                            },
                            "WFSerializationType": "WFTextTokenString",
                        }
                    },
                }
            )
        else:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                    "WFWorkflowActionParameters": {
                        "Text": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": display_source_name,
                                        "OutputUUID": display_source_uuid,
                                        "Type": "ActionOutput",
                                    }
                                },
                                "string": "\ufffc",
                            },
                            "WFSerializationType": "WFTextTokenString",
                        }
                    },
                }
            )

        workflow: Dict[str, Any] = {
            "WFWorkflowClientVersion": "2605.0.5",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowIcon": {
                "WFWorkflowIconStartColor": 463140863,
                "WFWorkflowIconGlyphNumber": 59676,
            },
            "WFWorkflowTypes": ["NCWidget"],
            "WFWorkflowInputContentItemClasses": [],
            "WFWorkflowActions": actions,
        }
        if import_questions:
            workflow["WFWorkflowImportQuestions"] = import_questions
        return plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY)

    @staticmethod
    def _sign_plist_via_macos_cli(unsigned_bytes: bytes) -> Optional[bytes]:
        if not _can_macos_sign_shortcuts():
            return None
        in_p: Optional[Path] = None
        out_p: Optional[Path] = None
        try:
            SHORTCUTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tag = uuid.uuid4().hex[:10]
            in_p = SHORTCUTS_CACHE_DIR / f".tmp_sign_in_{tag}.shortcut"
            out_p = SHORTCUTS_CACHE_DIR / f".tmp_sign_out_{tag}.shortcut"
            in_p.write_bytes(unsigned_bytes)
            os.chmod(in_p, 0o644)
            proc = subprocess.run(
                [
                    MACOS_SHORTCUTS_BIN,
                    "sign",
                    "--mode",
                    "anyone",
                    "--input",
                    str(in_p),
                    "--output",
                    str(out_p),
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if proc.returncode == 0 and out_p.is_file():
                signed = out_p.read_bytes()
                if signed[:4] == b"AEA1":
                    return signed
            logger.warning("macOS shortcuts sign non-AEA1 rc=%s err=%s", proc.returncode, proc.stderr[:160])
        except Exception as exc:
            logger.warning("macOS shortcuts sign failed: %s", exc)
        finally:
            for p in (in_p, out_p):
                if p is not None:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
        return None

    def pre_sign_universal_shortcut(
        self,
        default_base_url: str = "",
        default_unit_id: str = "8-1",
        repeat: int = 2,
        token: str = "",
    ) -> Dict[str, Any]:
        """
        Pre-sign a universal parameterized `.shortcut` (`AEA1`) into
        `data/shortcuts/universal-signed.shortcut` on macOS so Linux/Docker/Windows
        containers mounting `./data` can also distribute an Apple-signed shortcut.
        """
        vg_cfg = config_manager.get().get("voice_gateway", {})
        voice_port = int(config_manager.get().get("voice_http_port", 18791))
        base = (
            str(default_base_url or vg_cfg.get("public_voice_url") or "").strip().rstrip("/")
            or f"http://{_detect_lan_ip()}:{voice_port}"
        )
        tok = str(token or vg_cfg.get("access_token") or "").strip()
        raw_plist = self.build_siri_shortcut_plist(
            base_url=base,
            unit_id=default_unit_id,
            repeat=repeat,
            token=tok,
            universal_server_question=True,
        )
        with self._sign_lock:
            signed = self._sign_plist_via_macos_cli(raw_plist)
            if not signed:
                return {"ok": False, "error": "当前系统不支持 /usr/bin/shortcuts 签名（请在 macOS 上执行一次）"}
            SHORTCUTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            out_file = SHORTCUTS_CACHE_DIR / "universal-signed.shortcut"
            out_file.write_bytes(signed)
            return {
                "ok": True,
                "path": str(out_file),
                "bytes": len(signed),
                "default_base_url": base,
                "default_unit_id": default_unit_id,
            }

    def build_signed_siri_shortcut(
        self,
        base_url: str,
        unit_id: str = "8-1",
        repeat: int = 2,
        token: str = "",
        vehicle_type: str = "sedan",
        mode: str = "brief",
        shortcut_name: str = "小区车位",
        unit_mode: str = "fixed",
        show_map: str = "text",
        arrival_place: str = "",
        arrival_radius_m: int = 200,
        mobile_base_url: str = "",
        device_uid: str = "",
    ) -> Tuple[bytes, bool, str]:
        """
        Build and return `(shortcut_bytes, is_apple_signed, delivery_source)`.
        1. Checks memory LRU + `data/shortcuts/signed_<hash>.shortcut` (`AEA1`).
        2. On macOS, invokes `/usr/bin/shortcuts sign --mode anyone` automatically and
           caches the signed `AEA1` archive.
        3. On Linux/Docker/Windows, falls back to `data/shortcuts/universal-signed.shortcut`
           if pre-signed in `./data`.
        """
        effective_base = self._prefer_lan_base_url(base_url)
        rep_val = 1 if int(repeat or 2) <= 1 else 2
        vtype = self._normalize_vehicle_type(vehicle_type)
        safe_name = self.sanitize_shortcut_name(shortcut_name, "小区车位")
        act_mode = "ask" if str(mode or "").strip().lower() == "ask" else "brief"
        u_mode = self._normalize_unit_mode(unit_mode)
        map_mode = self._normalize_show_map_mode(show_map)
        place_clean = str(arrival_place or "").strip()
        radius_clean = max(50, min(2000, int(arrival_radius_m or 200)))
        clean_dev_uid = re.sub(r"[^a-zA-Z0-9_-]+", "", str(device_uid or "").strip())[:36]
        resolved_uid, unit_display, all_units = self._resolve_unit_display_and_list(unit_id)
        units_digest = (
            ",".join(f"{u['unit_id']}:{u['name']}" for u in all_units)
            if u_mode in ("menu", "setup_once")
            else unit_display
        )

        key_raw = (
            f"v11|{self._ws_id()}|{effective_base}|{resolved_uid}|{units_digest}|{rep_val}|{vtype}|"
            f"{act_mode}|{safe_name}|{u_mode}|{map_mode}|{place_clean}|{radius_clean}|{mobile_base_url}|{clean_dev_uid}|{token}"
        )
        cache_key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]

        with self._lock:
            mem_hit = self._signed_shortcut_cache.get(cache_key)
            if mem_hit and mem_hit[:4] == b"AEA1":
                self._signed_shortcut_cache.move_to_end(cache_key)
                return mem_hit, True, "memory_cache"

        disk_file = SHORTCUTS_CACHE_DIR / f"signed_{cache_key}.shortcut"
        if disk_file.is_file():
            try:
                disk_bytes = disk_file.read_bytes()
                if disk_bytes[:4] == b"AEA1":
                    with self._lock:
                        self._signed_shortcut_cache[cache_key] = disk_bytes
                        while len(self._signed_shortcut_cache) > 32:
                            self._signed_shortcut_cache.popitem(last=False)
                    return disk_bytes, True, "disk_cache"
            except Exception:
                pass

        unsigned_plist = self.build_siri_shortcut_plist(
            base_url=effective_base,
            unit_id=unit_id,
            repeat=rep_val,
            token=token,
            vehicle_type=vtype,
            mode=act_mode,
            shortcut_name=safe_name,
            unit_mode=u_mode,
            show_map=map_mode,
            arrival_place=place_clean,
            arrival_radius_m=radius_clean,
            mobile_base_url=mobile_base_url,
            device_uid=clean_dev_uid,
        )

        if _can_macos_sign_shortcuts():
            with self._sign_lock:
                # Re-check disk cache in case a concurrent request signed it
                if disk_file.is_file():
                    disk_bytes = disk_file.read_bytes()
                    if disk_bytes[:4] == b"AEA1":
                        return disk_bytes, True, "disk_cache"
                signed_bytes = self._sign_plist_via_macos_cli(unsigned_plist)
                if signed_bytes and signed_bytes[:4] == b"AEA1":
                    try:
                        SHORTCUTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        disk_file.write_bytes(signed_bytes)
                    except Exception:
                        pass
                    with self._lock:
                        self._signed_shortcut_cache[cache_key] = signed_bytes
                        while len(self._signed_shortcut_cache) > 32:
                            self._signed_shortcut_cache.popitem(last=False)
                    return signed_bytes, True, "macos_shortcuts_sign"

        for univ_file in (
            SHORTCUTS_CACHE_DIR / "universal-signed.shortcut",
            DATA_DIR / "shortcuts" / "universal-signed.shortcut",
        ):
            if univ_file.is_file():
                try:
                    univ_bytes = univ_file.read_bytes()
                    if univ_bytes[:4] == b"AEA1":
                        return univ_bytes, True, "presigned_universal"
                except Exception:
                    pass

        return unsigned_plist, False, "unsigned"

    def render_inline_dynamic_map_svg(
        self,
        target_unit_id: Optional[str] = None,
        vehicle_type: str = "sedan",
    ) -> str:
        """
        Render a 100% self-contained dynamic vector `<svg>` floor-plan map matching `:18790`
        (inline base64 backdrop, animated driving & walking trajectories, pulsing green
        recommended stalls, rotated stall boxes, unit door & gate pins, zero external links)
        so Apple Quick Look (`previewdocument`) renders the live interactive map with ZERO
        IP address / domain / Safari URL bar!
        """
        def _esc(val: Any) -> str:
            return (
                str(val or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        smap = self.ctx.db.get_site_map(include_backdrop=True)
        aspect = max(1.15, min(2.2, float(smap.get("aspect_ratio") or 1.45)))
        world_h = 100.0
        world_w = round(world_h * aspect, 2)

        zones = self.ctx.recommender.get_zone_summaries(vehicle_type=vehicle_type)
        db_units = self.ctx.db.list_units()
        topo = smap.get("topology") or {}
        resolved_uid, _uname, _ = self.resolve_unit_id(target_unit_id, "siri_hud", "")
        rec = self.ctx.recommender.recommend(
            unit_id=resolved_uid,
            vehicle_type=vehicle_type,
            driver_tag="动态态势图",
            acquire_lock=False,
        )

        projected_spots: List[Dict[str, Any]] = []
        for z in zones:
            for sp in z.get("spots") or []:
                if sp.get("map_placed", True):
                    projected_spots.append({**sp, "_zone_name": z.get("name") or sp.get("zone_id") or ""})
        if not projected_spots:
            projected_spots = [s for s in self.ctx.db.list_spots() if s.get("map_placed", True)]

        sample_vals: List[float] = []
        for sp in projected_spots:
            if sp.get("map_x") is not None and sp.get("map_y") is not None:
                sample_vals.extend([float(sp["map_x"]), float(sp["map_y"])])
        for u in db_units:
            if u.get("map_x") is not None and u.get("map_y") is not None:
                sample_vals.extend([float(u["map_x"]), float(u["map_y"])])
        coord_scale = 1000.0 if (sample_vals and max(sample_vals) > 150.0) else 100.0

        def tx(mx: Any) -> float:
            return round((float(mx) / coord_scale) * world_w, 2)

        def ty(my: Any) -> float:
            return round((float(my) / coord_scale) * world_h, 2)

        parts: List[str] = [
            f'<svg id="dynMapSvg" viewBox="0 0 {world_w} {world_h}" xmlns="http://www.w3.org/2000/svg">'
        ]
        parts.append(
            f'<rect x="0.2" y="0.2" width="{round(world_w - 0.4, 2)}" height="{round(world_h - 0.4, 2)}" '
            f'rx="2.4" fill="#F8FAFC" stroke="#E2E8F0" stroke-width="0.35"/>'
        )

        b64_bg = str(smap.get("backdrop_jpeg_b64") or "").strip()
        has_bg = False
        if b64_bg:
            raw_b64_part = b64_bg.split(",", 1)[1] if "," in b64_bg else b64_bg
            try:
                bg_im = Image.open(io.BytesIO(base64.b64decode(raw_b64_part))).convert("RGB")
                if max(bg_im.size) > 760:
                    ratio_bg = 760.0 / float(max(bg_im.size))
                    bg_im = bg_im.resize(
                        (max(1, int(bg_im.width * ratio_bg)), max(1, int(bg_im.height * ratio_bg))),
                        Image.Resampling.LANCZOS,
                    )
                compact_buf = io.BytesIO()
                bg_im.save(compact_buf, format="JPEG", quality=72, optimize=True)
                data_uri = "data:image/jpeg;base64," + base64.b64encode(compact_buf.getvalue()).decode("ascii")
            except Exception:
                data_uri = b64_bg if b64_bg.startswith("data:") else f"data:image/jpeg;base64,{b64_bg}"
            bg_alpha = float((smap.get("meta") or {}).get("backdrop_alpha") or 0.62)
            parts.append(
                f'<image href="{_esc(data_uri)}" x="0.2" y="0.2" width="{round(world_w - 0.4, 2)}" '
                f'height="{round(world_h - 0.4, 2)}" preserveAspectRatio="none" opacity="{bg_alpha:.2f}"/>'
            )
            has_bg = True

        rd_opacity = "0.48" if has_bg else "0.92"
        for road in topo.get("roads") or []:
            pts = [
                f"{tx(p[0])},{ty(p[1])}"
                for p in (road.get("points") or [])
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if len(pts) >= 2:
                rw = max(1.6, round((float(road.get("width") or 4.0) / coord_scale) * world_w, 2))
                parts.append(
                    f'<polyline points="{" ".join(pts)}" fill="none" stroke="#CBD5E1" '
                    f'stroke-width="{rw}" stroke-linecap="round" stroke-linejoin="round" opacity="{rd_opacity}"/>'
                )

        for wway in topo.get("walkways") or []:
            pts = [
                f"{tx(p[0])},{ty(p[1])}"
                for p in (wway.get("points") or [])
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if len(pts) >= 2:
                parts.append(
                    f'<polyline points="{" ".join(pts)}" fill="none" stroke="#14B8A6" '
                    f'stroke-width="0.65" stroke-dasharray="1.2,1.0" opacity="0.55"/>'
                )

        def _svg_chevrons(pts_num: List[Tuple[float, float]], color: str, scale: float = 0.85) -> str:
            out_c: List[str] = []
            step_w = 10.5
            nxt = 4.5
            acc = 0.0
            for i in range(len(pts_num) - 1):
                x1, y1 = pts_num[i]
                x2, y2 = pts_num[i + 1]
                dx, dy = x2 - x1, y2 - y1
                sl = math.hypot(dx, dy)
                if sl < 0.05:
                    continue
                ang_c = math.degrees(math.atan2(dy, dx))
                while nxt <= acc + sl:
                    t = max(0.1, min(0.9, (nxt - acc) / sl))
                    cx_c = x1 + dx * t
                    cy_c = y1 + dy * t
                    out_c.append(
                        f'<g transform="translate({cx_c:.2f},{cy_c:.2f}) rotate({ang_c:.1f})">'
                        f'<polygon points="{1.25 * scale:.2f},0 {-0.85 * scale:.2f},{-0.85 * scale:.2f} '
                        f'{-0.38 * scale:.2f},0 {-0.85 * scale:.2f},{0.85 * scale:.2f}" fill="{color}"/>'
                        f'</g>'
                    )
                    nxt += step_w
                acc += sl
            return "".join(out_c)

        # Animated Blue Driving Trajectory (Gate -> Parking Spot)
        rec_route = rec.get("recommended_route") or {}
        drive_raw = rec_route.get("guide_points") or rec_route.get("points") or []
        drive_num = [
            (tx(p[0]), ty(p[1]))
            for p in drive_raw
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(drive_num) >= 2:
            d_str = " ".join(f"{x},{y}" for x, y in drive_num)
            parts.append(
                f'<polyline points="{d_str}" fill="none" stroke="#FFFFFF" stroke-width="1.9" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="0.88"/>'
            )
            parts.append(
                f'<polyline points="{d_str}" fill="none" stroke="#0071E3" stroke-width="1.15" '
                f'stroke-dasharray="2.4,1.6" stroke-linecap="round" stroke-linejoin="round">'
                f'<animate attributeName="stroke-dashoffset" from="8" to="0" dur="1.1s" repeatCount="indefinite"/>'
                f'</polyline>'
            )
            parts.append(_svg_chevrons(drive_num, "#1D4ED8", 0.85))

        # Animated Teal Pedestrian Walking Trajectory (Parked Spot -> Unit Door!)
        walk_obj = rec.get("door_walk_path") or {}
        walk_raw = walk_obj.get("path_points") or []
        walk_num = [
            (tx(p[0]), ty(p[1]))
            for p in walk_raw
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(walk_num) >= 2:
            w_str = " ".join(f"{x},{y}" for x, y in walk_num)
            parts.append(
                f'<polyline points="{w_str}" fill="none" stroke="#FFFFFF" stroke-width="1.4" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
            )
            parts.append(
                f'<polyline points="{w_str}" fill="none" stroke="#0D9488" stroke-width="0.85" '
                f'stroke-dasharray="1.4,1.2" stroke-linecap="round" stroke-linejoin="round">'
                f'<animate attributeName="stroke-dashoffset" from="5.2" to="0" dur="0.9s" repeatCount="indefinite"/>'
                f'</polyline>'
            )
            parts.append(_svg_chevrons(walk_num, "#0F766E", 0.72))

        prim_zone_id = str((rec.get("primary") or {}).get("zone_id") or "")
        prim_group = str((rec.get("primary") or {}).get("group_code") or "")

        for sp in projected_spots:
            mx = sp.get("map_x")
            my = sp.get("map_y")
            if mx is None or my is None:
                continue
            cx = tx(mx)
            cy = ty(my)
            sw = max(1.3, round((float(sp.get("map_w") or (2.0 if coord_scale == 100 else 20.0)) / coord_scale) * world_w, 2))
            sh = max(1.8, round((float(sp.get("map_h") or (3.6 if coord_scale == 100 else 36.0)) / coord_scale) * world_h, 2))
            ang = float(sp.get("map_angle") or 0.0)
            st = str(sp.get("effective_status") or sp.get("status") or "UNKNOWN").upper()
            lbl = _esc(sp.get("label") or sp.get("spot_id") or "")
            zname = _esc(sp.get("group_name") or sp.get("_zone_name") or sp.get("zone_id") or "")
            is_rec_spot = st == "FREE" and str(sp.get("zone_id") or "") == prim_zone_id and (
                not prim_group or str(sp.get("group_code") or "") == prim_group
            )

            if st == "FREE":
                fill_c = "#16A34A" if is_rec_spot else "#22C55E"
                stroke_c = "#FFFFFF"
                st_txt = "空闲可停"
            elif st == "OCCUPIED":
                fill_c = "#EF4444"
                stroke_c = "#FFFFFF"
                st_txt = "已停车辆"
            elif st == "BLOCKED":
                fill_c = "#F59E0B"
                stroke_c = "#FFFFFF"
                st_txt = "占道禁停"
            else:
                fill_c = "#94A3B8"
                stroke_c = "#FFFFFF"
                st_txt = "待刷新"

            info_attr = f"{zname} · {lbl}号位：{st_txt}"
            parts.append(f'<g class="sp" data-info="{info_attr}" transform="translate({cx},{cy}) rotate({ang:.1f})">')
            if is_rec_spot:
                parts.append(
                    f'<rect x="{round(-sw * 0.62, 2)}" y="{round(-sh * 0.62, 2)}" '
                    f'width="{round(sw * 1.24, 2)}" height="{round(sh * 1.24, 2)}" rx="0.65" '
                    f'fill="none" stroke="#16A34A" stroke-width="0.38" opacity="0.75">'
                    f'<animate attributeName="opacity" values="0.85;0.18;0.85" dur="1.6s" repeatCount="indefinite"/>'
                    f'</rect>'
                )
            parts.append(
                f'<rect x="{round(-sw / 2, 2)}" y="{round(-sh / 2, 2)}" width="{sw}" height="{sh}" '
                f'rx="0.42" fill="{fill_c}" stroke="{stroke_c}" stroke-width="0.24"/>'
            )
            fsize = round(max(0.85, min(1.45, min(sw, sh) * 0.52)), 2)
            parts.append(
                f'<text x="0" y="{round(fsize * 0.34, 2)}" text-anchor="middle" fill="#FFFFFF" '
                f'font-size="{fsize}" font-weight="700" font-family="-apple-system,sans-serif">{lbl[:4]}</text>'
            )
            parts.append("</g>")

        # Entrance gates
        for g in topo.get("gates") or []:
            if g.get("x") is not None and g.get("y") is not None:
                gx, gy = tx(g["x"]), ty(g["y"])
                gname = _esc(g.get("name") or "大门")
                parts.append(
                    f'<g transform="translate({gx},{gy})">'
                    f'<rect x="-3.4" y="-1.7" width="6.8" height="3.4" rx="1.7" fill="#0F172A" stroke="#FFFFFF" stroke-width="0.3"/>'
                    f'<text x="0" y="0.65" text-anchor="middle" fill="#FFFFFF" font-size="1.55" font-weight="700">{gname[:4]}</text>'
                    f'</g>'
                )

        # Unit doors (highlight target unit door in Apple Blue)
        for u in db_units:
            ux_raw = u.get("door_x") if u.get("door_x") is not None else u.get("map_x")
            uy_raw = u.get("door_y") if u.get("door_y") is not None else u.get("map_y")
            if ux_raw is None or uy_raw is None:
                continue
            ux, uy = tx(ux_raw), ty(uy_raw)
            is_target = str(u.get("unit_id")) == resolved_uid
            uname_s = _esc(u.get("unit_id") or u.get("name") or "")
            if is_target:
                parts.append(
                    f'<g transform="translate({ux},{uy})">'
                    f'<circle r="2.6" fill="#0071E3" opacity="0.22">'
                    f'<animate attributeName="r" values="2.1;3.6;2.1" dur="1.6s" repeatCount="indefinite"/>'
                    f'</circle>'
                    f'<circle r="1.65" fill="#0071E3" stroke="#FFFFFF" stroke-width="0.38"/>'
                    f'<rect x="-4.2" y="-4.6" width="8.4" height="2.4" rx="1.2" fill="#0071E3"/>'
                    f'<text x="0" y="-2.95" text-anchor="middle" fill="#FFFFFF" font-size="1.45" font-weight="700">🎯{uname_s[:5]}</text>'
                    f'</g>'
                )
            else:
                parts.append(
                    f'<g transform="translate({ux},{uy})" opacity="0.82">'
                    f'<circle r="1.05" fill="#334155" stroke="#FFFFFF" stroke-width="0.25"/>'
                    f'</g>'
                )

        parts.append("</svg>")
        return "".join(parts)

    def render_voice_hud_html(
        self,
        request_base_url: str,
        unit_id: Optional[str] = None,
        repeat: int = 2,
        vehicle_type: Optional[str] = None,
        token: str = "",
        view_mode: str = "both",
        external_uid: str = "",
    ) -> str:
        """
        Render a 100% self-contained, borderless Apple Quick Look Dynamic Map Card.
        - `view_mode="both"` (default): Concise 1x Text Card + Dynamic SVG Map.
        - `view_mode="map"`: Pure Dynamic SVG Map + compact destination bar (no extra text block).
        """
        brief = self.build_voice_brief(
            unit_id=unit_id,
            repeat=repeat,
            vehicle_type=vehicle_type,
            channel="siri",
            external_uid=external_uid,
        )
        svg_markup = self.render_inline_dynamic_map_svg(
            target_unit_id=brief.get("unit_id"),
            vehicle_type=brief.get("vehicle_type") or "sedan",
        )

        def _esc(val: Any) -> str:
            return (
                str(val or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        short_site = extract_short_site_name(str(brief.get("community_name") or "小区"))
        uname = str(brief.get("unit_name") or brief.get("unit_id") or "")
        tot_free = int(brief.get("total_effective_free") or 0)
        tot_spots = int(brief.get("total_spots") or 0)
        is_map_only = str(view_mode or "").strip().lower() in ("map", "map_only", "only_map")

        pz = brief.get("primary_zone")
        if pz:
            headline = _esc(pz.get("zone_name") or "推荐停车区")
            sub_metrics = (
                f"推荐区余 {int(pz.get('effective_free') or 0)} 位 · "
                f"步行约 {int(round(float(pz.get('distance_m') or 0)))} 米"
            )
        else:
            headline = "当前院内监测车位已满"
            sub_metrics = "建议前往周边备选停车场"

        alt_line = ""
        alts = brief.get("alternatives") or []
        if alts:
            a0 = alts[0]
            alt_line = (
                f'<div class="alt">备选：{_esc(a0.get("display_name"))}（余 {int(a0.get("effective_free") or 0)} 位'
                f' · 步行 {int(round(float(a0.get("distance_m") or 0)))}m）</div>'
            )

        pill_cls = "pill-ok" if tot_free > 0 else "pill-full"
        top_card_html = (
            f"""<div class="mini-head">
    <span class="dest">📍 {_esc(short_site)} · {_esc(uname)}</span>
    <span class="pill {pill_cls}">余 {tot_free}/{tot_spots} 位</span>
  </div>"""
            if is_map_only
            else f"""<section class="card">
    <div class="row-top">
      <span class="dest">📍 {_esc(short_site)} · {_esc(uname)}</span>
      <span class="pill {pill_cls}">总余 {tot_free}/{tot_spots} 位</span>
    </div>
    <h1 class="zone">{headline}</h1>
    <div class="metrics">{_esc(sub_metrics)}</div>
    {alt_line}
  </section>"""
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=4,viewport-fit=cover" />
<title>{_esc(short_site)} · 实时车位态势</title>
<style>
:root{{--bg:#f5f5f7;--card:#ffffff;--text:#1d1d1f;--muted:#6e6e73;--accent:#0071e3;--line:rgba(0,0,0,0.07)}}
*{{box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{margin:0;padding:12px;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:660px;margin:0 auto;display:flex;flex-direction:column;gap:10px}}
.mini-head{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 4px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:13px 15px;box-shadow:0 2px 10px rgba(0,0,0,0.04)}}
.row-top{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}}
.dest{{font-size:13px;font-weight:700;color:var(--accent)}}
.pill{{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700}}
.pill-ok{{background:rgba(24,169,52,0.13);color:#117a27}}
.pill-full{{background:rgba(240,50,38,0.13);color:#c81e1e}}
.zone{{font-size:18px;font-weight:800;letter-spacing:-0.02em;margin:0 0 3px}}
.metrics{{font-size:13.5px;font-weight:600;color:#117a27}}
.alt{{font-size:12.5px;color:var(--muted);margin-top:4px}}
.map-card{{background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid var(--line);box-shadow:0 4px 16px rgba(0,0,0,0.05);position:relative}}
.map-stage{{width:100%;overflow:hidden;touch-action:pan-x pan-y pinch-zoom;background:#f8fafc}}
.map-stage svg{{display:block;width:100%;height:auto;transform-origin:center center;transition:transform 0.08s ease-out}}
.sp{{cursor:pointer}}
.legend{{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted);background:#fbfbfd}}
.dots{{display:flex;gap:10px;align-items:center}}
.dot{{display:inline-flex;align-items:center;gap:4px;font-weight:600}}
.dot i{{width:8px;height:8px;border-radius:2px;display:inline-block}}
#spTip{{font-weight:600;color:var(--text)}}
</style>
</head>
<body>
<div class="wrap">
  {top_card_html}
  <section class="map-card">
    <div class="map-stage" id="mapStage">{svg_markup}</div>
    <div class="legend">
      <div class="dots">
        <span class="dot"><i style="background:#16A34A"></i>空闲</span>
        <span class="dot"><i style="background:#EF4444"></i>已停</span>
        <span class="dot"><i style="background:#0071E3"></i>行车</span>
        <span class="dot"><i style="background:#0D9488"></i>步行</span>
      </div>
      <span id="spTip">双指缩放 · 点车位看状态</span>
    </div>
  </section>
</div>
<script>
document.querySelectorAll('.sp').forEach(function(el){{
  el.addEventListener('click',function(){{
    var t=document.getElementById('spTip');
    if(t)t.textContent=el.getAttribute('data-info')||'';
  }});
}});
</script>
</body>
</html>"""

    def build_siri_guide(
        self, request_base_url: str, unit_id: str = "8-1", repeat: int = 2
    ) -> Dict[str, Any]:
        vg_cfg = config_manager.get().get("voice_gateway", {})
        pub_voice = str(vg_cfg.get("public_voice_url") or "").strip().rstrip("/")
        pub_mobile = str(vg_cfg.get("public_mobile_url") or "").strip().rstrip("/")
        base = pub_voice or self._prefer_lan_base_url(request_base_url)
        tok = str(vg_cfg.get("access_token") or "").strip()
        sc_info = self._shortcut_delivery_info(vg_cfg)

        rep = 1 if int(repeat or 2) <= 1 else 2
        tok_qs = f"&token={quote(tok)}" if tok else ""
        brief_text_url = f"{base}/api/voice/brief?unit={quote(unit_id)}&repeat={rep}&format=text{tok_qs}"
        brief_json_url = f"{base}/api/voice/brief?unit={quote(unit_id)}&repeat={rep}{tok_qs}"
        ask_url = f"{base}/api/voice/ask{('?token=' + quote(tok)) if tok else ''}"

        return {
            "ok": True,
            "unit_id": unit_id,
            "repeat": rep,
            "token_configured": bool(tok),
            "brief_text_url": brief_text_url,
            "brief_json_url": brief_json_url,
            "ask_url": ask_url,
            **sc_info,
            "mobile_viewer_url": pub_mobile or base.replace(":18791", ":18790"),
            "ios_fast_broadcast_steps": [
                "打开 iPhone 自带「快捷指令 (Shortcuts)」App，点击右上角「+」新建快捷指令，重命名为「小区车位」（这就是唤醒词：嘿 Siri，小区车位）。",
                f"添加第 1 个动作「获取 URL 内容 (Get Contents of URL)」，粘贴地址：{brief_text_url}",
                "添加第 2 个动作「朗读文本 (Speak Text)」，输入变量自动关联为「URL 的内容」。完成！连上 CarPlay 或蓝牙即可全程免手唤醒双遍播报。",
            ],
            "ios_geofence_tip": "进阶零口令自动化：在快捷指令「自动化」页新建「到达（小区附近 200 米）」-> 选择「立即运行」-> 执行「小区车位」，每天开车驶近小区大门自动开口播报！",
            "ios_llm_ask_steps": [
                "新建第二个快捷指令，命名为「问问车位」（唤醒词：嘿 Siri，问问车位）。",
                "动作 1：「听写文本 (Dictate Text)」；动作 2：「获取 URL 内容」，地址填 "
                + ask_url
                + f"，方法选 POST，请求体 JSON 填入 question=(听写的文本)、unit={unit_id}；动作 3：「获取词典值 (answer)」->「朗读文本」。",
            ],
            "android_steps": [
                "华为小艺建议 / 小米澎湃自动连招 / Vivo 快捷指令：新建语音指令「小区车位」-> 执行 HTTP GET 请求获取上述播报地址文本 -> 调用系统 TTS 朗读返回内容；或直接把 :18790 平面图网页设为微信浮窗一键点播。",
            ],
        }

    def render_live_sitemap_png(
        self,
        target_unit_id: Optional[str] = None,
        vehicle_type: str = "sedan",
        view_mode: str = "both",
        external_uid: str = "",
        image_format: str = "PNG",
    ) -> bytes:
        """
        Render a high-clarity Apple Light (`#F5F5F7` / `#FFFFFF`) 2D live floor-plan image:
        - `view_mode="both"` (default): Renders crisp white recommendation summary header + floor-plan map.
        - `view_mode="map"`: Renders pure floor-plan map + compact legend bar (no extra text header).
        - Supports `image_format="JPEG"` (~95 KB fast XPC-free iOS Quick Look) and `"PNG"`.
        - Draws directional arrowheads: Driving (`大门 -> 停车位`) and Walking (`停车位 -> 单元门`)!
        """
        smap = self.ctx.db.get_site_map(include_backdrop=True)
        aspect = max(0.65, min(2.2, float(smap.get("aspect_ratio") or 1.45)))
        is_map_only = str(view_mode or "").strip().lower() in ("map", "map_only", "only_map")
        is_jpeg = str(image_format or "PNG").strip().upper() in ("JPG", "JPEG")
        W = 1080 if is_jpeg else 1280
        HEADER_H = 54 if is_map_only else 112
        PAD = 20
        canvas_w = W - PAD * 2
        canvas_h = int(round(canvas_w / aspect))
        H = HEADER_H + canvas_h + PAD * 2

        img = Image.new("RGBA", (W, H), (245, 245, 247, 255))
        draw = ImageDraw.Draw(img, "RGBA")

        f_title = _load_cjk_font(23 if is_jpeg else 26)
        f_sub = _load_cjk_font(15 if is_jpeg else 16)
        f_lbl = _load_cjk_font(13 if is_jpeg else 14)
        f_spot = _load_cjk_font(11)

        prof = self._ws_profile()
        comm_name = extract_short_site_name(
            str(prof.get("name") or config_manager.get().get("community_name") or "邻里公益智能寻位")
        )
        zones = self.ctx.recommender.get_zone_summaries(vehicle_type=vehicle_type)
        db_units = self.ctx.db.list_units()
        topo = smap.get("topology") or {}

        resolved_uid, resolved_uname, _ = self.resolve_unit_id(target_unit_id, "siri", external_uid)
        rec = self.ctx.recommender.recommend(
            unit_id=resolved_uid,
            vehicle_type=vehicle_type,
            driver_tag="实时快照生成",
            acquire_lock=False,
        )

        tot_free = sum(int(z.get("effective_free") or 0) for z in zones)
        tot_spots = sum(int(z.get("total_capacity") or z.get("total_spots") or 0) for z in zones)

        draw.rectangle([0, 0, W, HEADER_H], fill=(255, 255, 255, 255))
        draw.line([(0, HEADER_H - 1), (W, HEADER_H - 1)], fill=(226, 232, 240, 255), width=2)

        legend_items = [
            ((24, 169, 52, 255), f"空闲 {tot_free}"),
            ((240, 50, 38, 255), "已停"),
            ((13, 148, 136, 255), "步行回家"),
            ((0, 113, 227, 255), "行车引导"),
        ]

        if is_map_only:
            draw.text((24, 15), f"📍 {comm_name} · {resolved_uname}", fill=(29, 29, 31, 255), font=f_sub)
            lx = W - 430
            for col, ltxt in legend_items:
                draw.rounded_rectangle([lx, 18, lx + 14, 32], radius=4, fill=col)
                draw.text((lx + 19, 15), ltxt, fill=(51, 65, 85, 255), font=f_sub)
                lx += 104
        else:
            draw.text((24, 16), f"📍 {comm_name} · {resolved_uname}（总余 {tot_free}/{tot_spots} 位）", fill=(29, 29, 31, 255), font=f_title)
            prim = rec.get("primary")
            bkp = rec.get("backup")
            if isinstance(prim, dict):
                sub_txt = (
                    f"优先推荐：{prim.get('zone_name')}（剩余 {prim.get('effective_free')} 个 · "
                    f"步行到单元门 {prim.get('walk_meters')}m）"
                )
                if isinstance(bkp, dict) and bkp.get("zone_name"):
                    sub_txt += f" ｜ 备选：{bkp.get('zone_name')}（余 {bkp.get('effective_free')} 个）"
            else:
                sub_txt = f"当前院内监测车位已停满，建议前往周边备选停车场"
            draw.text((24, 58), sub_txt, fill=(17, 122, 39, 255) if prim else (180, 83, 9, 255), font=f_sub)

            lx = W - 430
            for col, ltxt in legend_items:
                draw.rounded_rectangle([lx, 20, lx + 14, 34], radius=4, fill=col)
                draw.text((lx + 19, 17), ltxt, fill=(51, 65, 85, 255), font=f_sub)
                lx += 104

        board_x0, board_y0 = PAD, HEADER_H + PAD
        board_x1, board_y1 = W - PAD, H - PAD
        draw.rounded_rectangle(
            [board_x0 - 4, board_y0 - 4, board_x1 + 4, board_y1 + 4],
            radius=18,
            fill=(255, 255, 255, 255),
            outline=(203, 213, 225, 255),
            width=2,
        )

        # Composite uploaded architectural site map backdrop (just like :18790!)
        has_backdrop_drawn = False
        b64_bg = str(smap.get("backdrop_jpeg_b64") or "").strip()
        if b64_bg:
            try:
                if "," in b64_bg:
                    b64_bg = b64_bg.split(",", 1)[1]
                bg_img = Image.open(io.BytesIO(base64.b64decode(b64_bg))).convert("RGBA")
                bg_resized = bg_img.resize((canvas_w, canvas_h), Image.Resampling.LANCZOS)
                # Blend softly with white (86% opacity) so crisp vector overlays pop cleanly
                white_veil = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 36))
                bg_blended = Image.alpha_composite(bg_resized, white_veil)
                img.paste(bg_blended, (board_x0, board_y0))
                has_backdrop_drawn = True
            except Exception:
                has_backdrop_drawn = False

        if not has_backdrop_drawn:
            draw.rectangle([board_x0, board_y0, board_x1, board_y1], fill=(248, 250, 252, 255))

        projected_spots: List[Dict[str, Any]] = []
        for z in zones:
            for sp in z.get("spots") or []:
                if sp.get("map_placed", True):
                    projected_spots.append(sp)
        if not projected_spots:
            projected_spots = [s for s in self.ctx.db.list_spots() if s.get("map_placed", True)]

        sample_vals: List[float] = []
        for sp in projected_spots:
            if sp.get("map_x") is not None and sp.get("map_y") is not None:
                sample_vals.extend([float(sp["map_x"]), float(sp["map_y"])])
        for u in db_units:
            if u.get("map_x") is not None and u.get("map_y") is not None:
                sample_vals.extend([float(u["map_x"]), float(u["map_y"])])
        coord_scale = 1000.0 if (sample_vals and max(sample_vals) > 150.0) else 100.0

        def to_px(mx: float, my: float) -> Tuple[float, float]:
            return (
                board_x0 + (float(mx) / coord_scale) * canvas_w,
                board_y0 + (float(my) / coord_scale) * canvas_h,
            )

        # 1) Roads & courtyard walkways (subtle translucent overlay when backdrop is present)
        rd_alpha = 115 if has_backdrop_drawn else 235
        for road in topo.get("roads") or []:
            pts = road.get("points") or []
            px_pts = [to_px(p[0], p[1]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(px_pts) >= 2:
                rw = max(10, int(float(road.get("width") or 4.2) * canvas_w / coord_scale))
                draw.line(px_pts, fill=(148, 163, 184, rd_alpha), width=rw, joint="curve")
        for wway in topo.get("walkways") or []:
            pts = wway.get("points") or []
            px_pts = [to_px(p[0], p[1]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(px_pts) >= 2:
                draw.line(px_pts, fill=(20, 184, 166, 130), width=4, joint="curve")

        def _draw_arrows_along(pts_px: List[Tuple[float, float]], color: Tuple[int, int, int, int], step_px: float = 95.0, sz: float = 8.5) -> None:
            next_d = 42.0
            acc = 0.0
            for i in range(len(pts_px) - 1):
                x1, y1 = pts_px[i]
                x2, y2 = pts_px[i + 1]
                dx, dy = x2 - x1, y2 - y1
                seg_l = math.hypot(dx, dy)
                if seg_l < 4.0:
                    continue
                ux_v, uy_v = dx / seg_l, dy / seg_l
                px_v, py_v = -uy_v, ux_v
                while next_d <= acc + seg_l:
                    t = max(0.1, min(0.9, (next_d - acc) / seg_l))
                    cx_a, cy_a = x1 + dx * t, y1 + dy * t
                    tip = (cx_a + ux_v * sz, cy_a + uy_v * sz)
                    left_w = (cx_a - ux_v * sz * 0.75 + px_v * sz * 0.72, cy_a - uy_v * sz * 0.75 + py_v * sz * 0.72)
                    right_w = (cx_a - ux_v * sz * 0.75 - px_v * sz * 0.72, cy_a - uy_v * sz * 0.75 - py_v * sz * 0.72)
                    draw.polygon([tip, left_w, right_w], fill=color, outline=(255, 255, 255, 230))
                    next_d += step_px
                acc += seg_l

        # 2) Active Driving Guidance Polyline (Apple Blue: Gate -> Spot) + Door Walking Path (Teal: Spot -> Unit Door!)
        rec_route = rec.get("recommended_route") or {}
        drive_pts = rec_route.get("guide_points") or rec_route.get("points") or []
        if len(drive_pts) >= 2:
            d_px = [to_px(p[0], p[1]) for p in drive_pts if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(d_px) >= 2:
                draw.line(d_px, fill=(255, 255, 255, 220), width=11, joint="curve")
                draw.line(d_px, fill=(0, 113, 227, 235), width=6, joint="curve")
                _draw_arrows_along(d_px, (0, 90, 205, 255), step_px=105.0, sz=8.5)

        walk_obj = rec.get("door_walk_path") or {}
        walk_pts = walk_obj.get("path_points") or []
        if len(walk_pts) >= 2:
            w_px = [to_px(p[0], p[1]) for p in walk_pts if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(w_px) >= 2:
                draw.line(w_px, fill=(255, 255, 255, 230), width=9, joint="curve")
                draw.line(w_px, fill=(13, 148, 136, 245), width=5, joint="curve")
                _draw_arrows_along(w_px, (15, 118, 110, 255), step_px=80.0, sz=7.8)

        # 3) Calibrated / placed parking spots (matching :18790 crisp palette)
        for sp in projected_spots:
            mx = sp.get("map_x")
            my = sp.get("map_y")
            if mx is None or my is None:
                continue
            sx, sy = to_px(float(mx), float(my))
            default_w = 22.0 if coord_scale > 500 else 2.2
            default_h = 40.0 if coord_scale > 500 else 4.0
            sw = max(15.0, float(sp.get("map_w") or default_w) * canvas_w / coord_scale)
            sh = max(11.0, float(sp.get("map_h") or default_h) * canvas_h / coord_scale)
            angle_rad = math.radians(float(sp.get("map_angle") or 0.0))
            cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
            corners = []
            for dx, dy in ((-sw / 2, -sh / 2), (sw / 2, -sh / 2), (sw / 2, sh / 2), (-sw / 2, sh / 2)):
                rx = sx + dx * cos_a - dy * sin_a
                ry = sy + dx * sin_a + dy * cos_a
                corners.append((rx, ry))

            st = str(sp.get("status") or "UNKNOWN")
            pst = sp.get("pending_status")
            eff = pst if (st == "FREE" and pst in ("OCCUPIED", "BLOCKED")) else st
            if eff == "FREE":
                fcol, ocol = (24, 169, 52, 238), (255, 255, 255, 255)
            elif eff == "BLOCKED":
                fcol, ocol = (245, 138, 0, 238), (255, 255, 255, 255)
            elif eff == "OCCUPIED":
                fcol, ocol = (240, 50, 38, 232), (255, 255, 255, 255)
            else:
                fcol, ocol = (100, 116, 139, 215), (255, 255, 255, 240)

            draw.polygon(corners, fill=fcol, outline=ocol)
            lbl = str(sp.get("label") or "")[-2:]
            if lbl:
                draw.text((sx - 6, sy - 6), lbl, fill=(255, 255, 255, 255), font=f_spot)

        # 4) Gates & Building Unit badges (crisp white pills with dark text)
        for g in topo.get("gates") or []:
            if g.get("x") is not None and g.get("y") is not None:
                gx, gy = to_px(float(g["x"]), float(g["y"]))
                gname = str(g.get("name") or "大门")[:6]
                draw.rounded_rectangle(
                    [gx - 32, gy - 13, gx + 32, gy + 13],
                    radius=7,
                    fill=(0, 113, 227, 245),
                    outline=(255, 255, 255, 255),
                    width=2,
                )
                draw.text((gx - 24, gy - 8), gname, fill=(255, 255, 255, 255), font=f_spot)

        for u in db_units:
            uid = str(u.get("unit_id") or "")
            ux_raw = u.get("door_x") if u.get("door_x") is not None else u.get("map_x")
            uy_raw = u.get("door_y") if u.get("door_y") is not None else u.get("map_y")
            if ux_raw is None or uy_raw is None:
                continue
            ux, uy = to_px(float(ux_raw), float(uy_raw))
            is_target = uid == resolved_uid
            r_dot = 11 if is_target else 7
            dot_fill = (0, 113, 227, 255) if is_target else (15, 118, 110, 235)
            draw.ellipse(
                [ux - r_dot, uy - r_dot, ux + r_dot, uy + r_dot],
                fill=dot_fill,
                outline=(255, 255, 255, 255),
                width=2,
            )
            uname = str(u.get("name") or uid)[:9]
            pill_w = max(54, len(uname) * 14 + 14)
            pill_bg = (0, 113, 227, 245) if is_target else (255, 255, 255, 235)
            pill_fg = (255, 255, 255, 255) if is_target else (29, 29, 31, 255)
            draw.rounded_rectangle(
                [ux + r_dot + 4, uy - 12, ux + r_dot + 4 + pill_w, uy + 12],
                radius=7,
                fill=pill_bg,
                outline=(203, 213, 225, 255),
                width=1,
            )
            draw.text((ux + r_dot + 10, uy - 8), uname, fill=pill_fg, font=f_lbl)

        out_rgb = img.convert("RGB")
        buf = io.BytesIO()
        if is_jpeg:
            out_rgb.save(buf, format="JPEG", quality=88, optimize=True)
        else:
            out_rgb.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def make_voice_http_handler(root_ctx: Any):
    """
    Isolated HTTP handler for port :18791 (Hands-Free Siri / CarPlay Shortcuts +
    Custom-LLM Voice Q&A + Multi-Workspace Identity Routing).
    Exposes zero admin mutations, zero camera RTSP credentials, and zero raw camera frames.
    """

    class VoiceGatewayRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _resolve_ws(self, raw_path: str, qs: Dict[str, List[str]]) -> Tuple[str, Any]:
            m = re.match(r"^/w/([a-zA-Z0-9_-]{1,40})(?:/(.*))?$", raw_path or "/")
            if m:
                ws_id = m.group(1)
                rest = m.group(2) or ""
                norm_p = "/" + rest if rest else "/"
            else:
                norm_p = raw_path
                ws_id = (
                    (qs.get("ws") or qs.get("workspace") or [""])[0].strip()
                    or str(self.headers.get("X-Parking-Workspace") or "").strip()
                    or "default"
                )
            if hasattr(root_ctx, "resolve_workspace_context"):
                return norm_p, root_ctx.resolve_workspace_context(ws_id)
            return norm_p, root_ctx

        def _client_ip(self) -> str:
            cf_ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") or ""
            if cf_ip:
                return cf_ip.split(",")[0].strip()
            return self.client_address[0] if self.client_address else "local"

        def _request_base_url(self) -> str:
            proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
            host = self.headers.get("Host") or f"127.0.0.1:{config_manager.get().get('voice_http_port', 18791)}"
            return f"{proto}://{host}"

        def _extract_token(self, qs: Dict[str, List[str]], body: Optional[Dict[str, Any]] = None) -> str:
            if qs.get("token"):
                return str(qs["token"][0])
            if qs.get("key"):
                return str(qs["key"][0])
            hdr_tok = self.headers.get("X-Parking-Token") or ""
            if hdr_tok:
                return hdr_tok.strip()
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            if isinstance(body, dict) and body.get("token"):
                return str(body["token"])
            return ""

        def _send_json(self, data: Any, status: int = 200) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_text(self, text: str, status: int = 200) -> None:
            raw = str(text or "").encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_bytes(
            self,
            raw: bytes,
            content_type: str,
            status: int = 200,
            download_name: str = "",
            utf8_download_name: str = "",
        ) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                if download_name or utf8_download_name:
                    ascii_name = download_name or "community-parking.shortcut"
                    if utf8_download_name:
                        encoded_utf8 = quote(utf8_download_name)
                        disp = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_utf8}"
                    else:
                        disp = f'attachment; filename="{ascii_name}"'
                    self.send_header("Content-Disposition", disp)
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_html(self, html_text: str, status: int = 200) -> None:
            raw = html_text.encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_small_json(self, max_bytes: int = 32768) -> Dict[str, Any]:
            clen = int(self.headers.get("Content-Length") or 0)
            if clen <= 0:
                return {}
            if clen > max_bytes:
                raise ValueError("请求体过大")
            raw = self.rfile.read(clen)
            if not raw:
                return {}
            text = raw.decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                return {"question": str(parsed)}
            except json.JSONDecodeError:
                # Allow Siri Shortcuts plain-text POST body directly!
                return {"question": text}

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-Parking-Token, X-Parking-Workspace",
            )
            self.end_headers()

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path, _ = self._resolve_ws(parsed.path, qs)
            if path in ("/api/voice/siri.shortcut", "/siri.shortcut"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-apple-shortcut")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.end_headers()
                return
            if path.startswith("/api/"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                return
            self._serve_voice_static(path, head_only=True)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path, ctx = self._resolve_ws(parsed.path, qs)
            vg: VoiceGatewayService = ctx.voice_gateway

            try:
                if path == "/api/health":
                    cfg = config_manager.get()
                    metrics = vg.get_gateway_metrics()
                    self._send_json(
                        {
                            "status": "ok",
                            "mode": "voice_gateway",
                            "workspace_id": getattr(ctx, "workspace_id", "default"),
                            "port": int(cfg.get("voice_http_port", 18791)),
                            "community": metrics.get("workspace_name") or cfg.get("community_name", ""),
                            "token_required": bool(metrics.get("token_required")),
                        }
                    )
                    return

                if path == "/api/voice/status":
                    units = ctx.db.list_units()
                    metrics = vg.get_gateway_metrics()
                    provided_tok = self._extract_token(qs)
                    is_auth = vg.verify_token(provided_tok)
                    ws_pub = (
                        ctx.get_public_workspace_profile(include_templates=False)
                        if hasattr(ctx, "get_public_workspace_profile")
                        else {}
                    )
                    self._send_json(
                        {
                            "ok": True,
                            "workspace_id": getattr(ctx, "workspace_id", "default"),
                            "workspace": ws_pub,
                            "community_name": metrics.get("workspace_name", ""),
                            "preferred_voice_lang": metrics.get("preferred_voice_lang", ""),
                            "units": units,
                            "auth_required": bool(metrics.get("token_required")),
                            "authorized": bool(is_auth),
                            "default_unit_id": metrics.get("default_unit_id", "8-1"),
                            "default_repeat": metrics.get("default_repeat", 2),
                            "public_mobile_url": metrics.get("public_mobile_url", ""),
                            "public_voice_url": metrics.get("public_voice_url", ""),
                            "arrival_place": metrics.get("arrival_place", ""),
                            "arrival_radius_m": metrics.get("arrival_radius_m", 200),
                            "icloud_shortcut_url": metrics.get("icloud_shortcut_url", ""),
                            "shortcut_signed_ready": bool(metrics.get("shortcut_signed_ready")),
                            "shortcut_delivery_mode": metrics.get("shortcut_delivery_mode", "manual_wizard"),
                            "vlm_configured": bool(metrics.get("llm_configured")),
                            "llm_qa_enabled": bool(metrics.get("llm_qa_enabled")),
                            "weixin": metrics.get("weixin"),
                            "gateway": metrics,
                        }
                    )
                    return

                # Token check for functional voice/map/shortcut endpoints
                tok = self._extract_token(qs)
                if path.startswith("/api/voice/") and not vg.verify_token(tok):
                    fmt = (qs.get("format") or ["json"])[0].lower()
                    if fmt in ("text", "txt", "plain", "speech", "card", "display"):
                        self._send_text("口令校验未通过，请在快捷指令或链接中携带正确的访问口令。", 401)
                    else:
                        self._send_json({"ok": False, "error": "口令校验未通过 (Unauthorized)"}, 401)
                    return

                if path == "/api/voice/brief":
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    rep_raw = (qs.get("repeat") or [""])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else None
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or [""])[0] or None
                    uid = (qs.get("user") or qs.get("uid") or [""])[0]
                    fmt = (qs.get("format") or ["json"])[0].lower()

                    res = vg.build_voice_brief(
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        channel="siri",
                        external_uid=uid,
                    )
                    if fmt in ("card", "display"):
                        self._send_text(res["display_text"])
                    elif fmt in ("text", "txt", "plain", "speech"):
                        self._send_text(res["speech_text"])
                    else:
                        self._send_json(res)
                    return

                if path == "/api/voice/ask":
                    q = (qs.get("q") or qs.get("question") or qs.get("text") or [""])[0]
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    rep_raw = (qs.get("repeat") or [""])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else None
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or [""])[0] or None
                    uid = (qs.get("user") or qs.get("uid") or [""])[0]
                    fmt = (qs.get("format") or ["json"])[0].lower()

                    res = vg.ask_voice_assistant(
                        question=q,
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        channel="siri",
                        external_uid=uid,
                        client_ip=self._client_ip(),
                    )
                    if fmt in ("text", "txt", "plain", "speech"):
                        self._send_text(res["speech_text"])
                    else:
                        self._send_json(res)
                    return

                if path == "/api/voice/map-overview":
                    from app.server import _build_mobile_overview_payload

                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    vtype = vg._normalize_vehicle_type((qs.get("vehicle_type") or qs.get("vehicle") or ["sedan"])[0])
                    self._send_json(_build_mobile_overview_payload(ctx, unit_id=unit_id, vehicle_type=vtype))
                    return

                if path == "/api/voice/map-backdrop":
                    smap = ctx.db.get_site_map(include_backdrop=True)
                    b64 = smap.get("backdrop_jpeg_b64")
                    self._send_json(
                        {
                            "has_backdrop": bool(b64),
                            "aspect_ratio": smap.get("aspect_ratio", 1.45),
                            "backdrop_jpeg_b64": b64 if b64 else None,
                        }
                    )
                    return

                if path == "/api/voice/places":
                    q_place = (qs.get("q") or qs.get("query") or [""])[0]
                    lat_raw = (qs.get("lat") or [""])[0].strip()
                    lon_raw = (qs.get("lon") or [""])[0].strip()
                    lat_v: Optional[float] = None
                    lon_v: Optional[float] = None
                    try:
                        if lat_raw and lon_raw:
                            lat_v = float(lat_raw)
                            lon_v = float(lon_raw)
                    except ValueError:
                        lat_v, lon_v = None, None
                    self._send_json(search_local_map_places(query=q_place, lat=lat_v, lon=lon_v))
                    return

                if path == "/api/voice/siri-guide":
                    unit_id = (qs.get("unit") or qs.get("unit_id") or ["8-1"])[0]
                    rep_raw = (qs.get("repeat") or ["2"])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else 2
                    self._send_json(vg.build_siri_guide(self._request_base_url(), unit_id=unit_id, repeat=rep))
                    return

                if path == "/api/voice/check-bind":
                    uid_chk = (qs.get("uid") or qs.get("user") or [""])[0].strip()
                    bound = ctx.db.get_im_user_binding("siri", uid_chk) if uid_chk else None
                    # Returns "" (0 chars -> Count < 1 is TRUE on Run #1) when unbound,
                    # and bound unit_id (e.g. "9-2", >= 3 chars -> Count < 1 is FALSE on Run #2+) once bound!
                    self._send_text(str(bound["unit_id"]) if (bound and bound.get("unit_id")) else "")
                    return

                if path == "/api/voice/quick-bind":
                    uid_qb = (qs.get("uid") or qs.get("user") or [""])[0].strip()
                    raw_u_qb = (qs.get("unit") or qs.get("unit_id") or [""])[0].strip()
                    if uid_qb and raw_u_qb:
                        resolved_uid, _uname, _ = vg.resolve_unit_id(raw_u_qb, "siri", "")
                        ctx.db.upsert_im_user_binding(
                            channel="siri",
                            external_uid=uid_qb,
                            unit_id=resolved_uid,
                            repeat_count=2,
                        )
                        self._send_text(resolved_uid)
                    else:
                        self._send_text("8-1")
                    return

                if path == "/api/voice/hud" or path.startswith("/api/voice/hud/"):
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    rep_raw = (qs.get("repeat") or ["2"])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else 2
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or ["sedan"])[0]
                    vmode = (qs.get("view") or ["both"])[0]
                    ext_uid = (qs.get("uid") or qs.get("user") or [""])[0]
                    html_doc = vg.render_voice_hud_html(
                        request_base_url=self._request_base_url(),
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        token=tok,
                        view_mode=vmode,
                        external_uid=ext_uid,
                    )
                    self._send_html(html_doc, 200)
                    return

                if path in ("/api/voice/siri.shortcut", "/siri.shortcut"):
                    unit_id = (qs.get("unit") or qs.get("unit_id") or ["8-1"])[0]
                    rep_raw = (qs.get("repeat") or ["2"])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else 2
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or ["sedan"])[0]
                    sc_mode = (qs.get("mode") or ["brief"])[0]
                    u_mode = (qs.get("unit_mode") or ["fixed"])[0]
                    show_map = (qs.get("show_map") or ["text"])[0]
                    dev_uid = (qs.get("device_uid") or qs.get("uid") or [""])[0].strip()
                    vg_cfg = config_manager.get().get("voice_gateway", {})
                    raw_name = (qs.get("name") or [""])[0].strip()
                    safe_sc_name = vg.sanitize_shortcut_name(raw_name, "小区车位")
                    arr_place = (qs.get("arrival_place") or [str(vg_cfg.get("arrival_place") or "")])[0].strip()
                    arr_rad_raw = (qs.get("arrival_radius") or [str(vg_cfg.get("arrival_radius_m") or 200)])[0]
                    arr_rad = int(arr_rad_raw) if str(arr_rad_raw).strip().isdigit() else 200
                    want_raw = ((qs.get("raw") or qs.get("unsigned") or ["0"])[0]).lower() in ("1", "true", "yes")
                    want_prepare = ((qs.get("prepare") or qs.get("prewarm") or ["0"])[0]).lower() in ("1", "true", "yes")

                    pub_base = str(vg_cfg.get("public_voice_url") or "").strip() or self._request_base_url()
                    pub_mob = str(vg_cfg.get("public_mobile_url") or "").strip()
                    cfg_tok = vg._effective_access_token()

                    if want_raw:
                        plist_bytes = vg.build_siri_shortcut_plist(
                            base_url=pub_base,
                            unit_id=unit_id,
                            repeat=rep,
                            token=cfg_tok,
                            vehicle_type=vtype,
                            mode=sc_mode,
                            shortcut_name=safe_sc_name,
                            unit_mode=u_mode,
                            show_map=show_map,
                            arrival_place=arr_place,
                            arrival_radius_m=arr_rad,
                            mobile_base_url=pub_mob,
                            device_uid=dev_uid,
                        )
                        self._send_bytes(
                            plist_bytes,
                            content_type="application/x-apple-shortcut",
                            download_name="community-parking.shortcut",
                            utf8_download_name=f"{safe_sc_name}.shortcut",
                        )
                        return

                    sc_bytes, is_signed, _src = vg.build_signed_siri_shortcut(
                        base_url=pub_base,
                        unit_id=unit_id,
                        repeat=rep,
                        token=cfg_tok,
                        vehicle_type=vtype,
                        mode=sc_mode,
                        shortcut_name=safe_sc_name,
                        unit_mode=u_mode,
                        show_map=show_map,
                        arrival_place=arr_place,
                        arrival_radius_m=arr_rad,
                        mobile_base_url=pub_mob,
                        device_uid=dev_uid,
                    )
                    if want_prepare:
                        icloud_url = str(vg_cfg.get("icloud_shortcut_url") or "").strip()
                        self._send_json(
                            {
                                "ok": True,
                                "signed": bool(is_signed),
                                "source": _src,
                                "bytes": len(sc_bytes),
                                "has_icloud": bool(icloud_url),
                            }
                        )
                        return
                    if is_signed:
                        self._send_bytes(
                            sc_bytes,
                            content_type="application/x-apple-shortcut",
                            download_name="community-parking.shortcut",
                            utf8_download_name=f"{safe_sc_name}.shortcut",
                        )
                        return

                    icloud_url = str(vg_cfg.get("icloud_shortcut_url") or "").strip()
                    if icloud_url:
                        self.send_response(HTTPStatus.FOUND)
                        self.send_header("Location", icloud_url)
                        self.end_headers()
                        return

                    guide = vg.build_siri_guide(self._request_base_url(), unit_id=unit_id, repeat=rep)
                    safe_brief_url = guide["brief_text_url"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    wizard_html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>添加「小区车位」Siri 快捷指令</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f172a;color:#f8fafc;padding:24px;max-width:560px;margin:0 auto;line-height:1.6}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:20px;margin-top:16px}}
.btn{{display:inline-block;background:#2563eb;color:#fff;text-decoration:none;border:none;border-radius:10px;padding:12px 18px;font-size:15px;font-weight:600;cursor:pointer;margin-right:8px;margin-top:8px}}
code{{display:block;background:#0f172a;padding:10px;border-radius:8px;word-break:break-all;color:#38bdf8;margin:8px 0}}
</style></head><body>
<h2>🎙️ 20 秒添加「小区车位」Siri 快捷指令</h2>
<div class="card">
<p>因苹果 iOS 15+ 限制直接导入未由 macOS 签名的快捷指令文件，而当前服务端运行在非 macOS 环境且尚未配置预签名模板，请按以下两步免签添加：</p>
<p><strong>第 1 步：复制您的专属极速播报链接</strong></p>
<code id="u">{safe_brief_url}</code>
<button class="btn" onclick="navigator.clipboard.writeText(document.getElementById('u').textContent);this.textContent='✅ 已复制链接'">📋 一键复制播报链接</button>
<a class="btn" style="background:#059669" href="shortcuts://create-shortcut">🚀 打开快捷指令 App 新建</a>
<p style="margin-top:14px"><strong>第 2 步：在快捷指令中添加 2 个动作并命名为「小区车位」</strong><br>
① 添加动作「获取 URL 内容」，粘贴刚才复制的链接；<br>
② 添加动作「朗读文本」（自动关联 URL 的内容）。即可喊“嘿 Siri，小区车位”车载播报！</p>
<p style="font-size:13px;color:#94a3b8">💡 管理员提示：在任意 Mac 执行一次 <code>./parking-ctl.sh sign-shortcut</code> 生成苹果官方签名包（随 <code>data/</code> 目录挂载），或在 <code>:8790</code> 填入 iCloud 快捷指令分享链接后，全小区业主即可一键直接导入。</p>
</div></body></html>"""
                    self._send_html(wizard_html, 200)
                    return

                if path in ("/api/voice/map.png", "/api/voice/map.jpg"):
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or ["sedan"])[0]
                    vmode = (qs.get("view") or ["both"])[0]
                    ext_uid = (qs.get("uid") or qs.get("user") or [""])[0]
                    is_jpg = path.endswith(".jpg")
                    img_bytes = vg.render_live_sitemap_png(
                        target_unit_id=unit_id,
                        vehicle_type=vtype,
                        view_mode=vmode,
                        external_uid=ext_uid,
                        image_format="JPEG" if is_jpg else "PNG",
                    )
                    self._send_bytes(
                        img_bytes,
                        content_type="image/jpeg" if is_jpg else "image/png",
                    )
                    return

                if path.startswith("/api/"):
                    self._send_json(
                        {"ok": False, "error": "该端口仅开放 18791 免手语音播报与智能问答接口"},
                        403,
                    )
                    return

                self._serve_voice_static(path, head_only=False)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path, ctx = self._resolve_ws(parsed.path, qs)
            vg: VoiceGatewayService = ctx.voice_gateway

            try:
                body = self._read_small_json()
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 413)
                return

            try:
                tok = self._extract_token(qs, body)
                if not vg.verify_token(tok):
                    self._send_json({"ok": False, "error": "口令校验未通过 (Unauthorized)"}, 401)
                    return

                if path == "/api/voice/ask":
                    q = str(
                        body.get("question")
                        or body.get("q")
                        or body.get("text")
                        or body.get("prompt")
                        or (qs.get("q") or [""])[0]
                    )
                    unit_id = str(body.get("unit") or body.get("unit_id") or (qs.get("unit") or [""])[0]) or None
                    rep_val = body.get("repeat")
                    rep = int(rep_val) if rep_val is not None and str(rep_val).isdigit() else None
                    vtype = str(body.get("vehicle_type") or "") or None
                    uid = str(body.get("user") or body.get("uid") or "")
                    ch = str(body.get("channel") or "siri")
                    fmt = str(body.get("format") or (qs.get("format") or ["json"])[0]).lower()

                    res = vg.ask_voice_assistant(
                        question=q,
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        channel=ch,
                        external_uid=uid,
                        client_ip=self._client_ip(),
                    )
                    if fmt in ("text", "txt", "plain", "speech"):
                        self._send_text(res["speech_text"])
                    else:
                        self._send_json(res)
                    return

                if path == "/api/voice/bind":
                    uid = str(body.get("user") or body.get("uid") or "").strip()
                    raw_unit = str(body.get("unit") or body.get("unit_id") or "").strip()
                    ch = str(body.get("channel") or "siri").strip()
                    rep = int(body.get("repeat") or 2)
                    vtype = str(body.get("vehicle_type") or "standard").strip()
                    dname = str(body.get("display_name") or "").strip()
                    if not uid or not raw_unit:
                        self._send_json({"ok": False, "error": "请提供 user 与 unit 参数"}, 400)
                        return
                    resolved_uid, resolved_uname, _ = vg.resolve_unit_id(raw_unit, ch, uid)
                    saved = ctx.db.upsert_im_user_binding(
                        channel=ch,
                        external_uid=uid,
                        unit_id=resolved_uid,
                        repeat_count=rep,
                        vehicle_type=vtype,
                        display_name=dname,
                    )
                    self._send_json(
                        {
                            "ok": True,
                            "binding": saved,
                            "unit_name": resolved_uname,
                            "message": f"已绑定默认目的地：{resolved_uname}",
                        }
                    )
                    return

                self._send_json(
                    {"ok": False, "error": "该端口仅开放 /api/voice/ask 与 /api/voice/bind"},
                    403,
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)

        def _serve_voice_static(self, req_path: str, head_only: bool = False) -> None:
            clean_path = unquote(req_path)
            if clean_path in ("/", "", "/index.html", "/voice", "/voice/", "/voice.html"):
                rel = "voice.html"
            elif clean_path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            else:
                rel = clean_path.lstrip("/")

            if rel not in VOICE_ALLOWED_STATIC_FILES:
                self._send_json({"error": "Not found"}, 404)
                return

            file_path = (STATIC_DIR / rel).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.is_file():
                self._send_json({"error": "Not found"}, 404)
                return

            content = file_path.read_bytes()
            content_type = "application/octet-stream"
            if rel.endswith(".html"):
                ver = _compute_voice_static_version()
                html_txt = content.decode("utf-8")
                html_txt = re.sub(r'/voice\.css(?:\?v=[^"]*)?', f"/voice.css?v={ver}", html_txt)
                html_txt = re.sub(r'/voice\.js(?:\?v=[^"]*)?', f"/voice.js?v={ver}", html_txt)
                content = html_txt.encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif rel.endswith(".css"):
                content_type = "text/css; charset=utf-8"
            elif rel.endswith(".js"):
                content_type = "application/javascript; charset=utf-8"
            elif rel.endswith(".svg"):
                content_type = "image/svg+xml"

            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                if not head_only:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return VoiceGatewayRequestHandler


class VoiceThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_voice_http_server(
    host: str = "0.0.0.0", port: int = 18791, ctx: Any = None
) -> ThreadingHTTPServer:
    handler_cls = make_voice_http_handler(ctx)
    return VoiceThreadingHTTPServer((host, port), handler_cls)
