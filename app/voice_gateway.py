from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import hmac
import importlib.util
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

from app.ad_operations import detect_client_platform
from app.config import BASE_DIR, DATA_DIR, config_manager
from app.spot_tags import (
    SPOT_TAG_CATALOG,
    get_tag_display_info,
    match_spot_tag_from_text,
    resolve_tag_filter_code,
)
from app.vision_pipeline import VisionPipeline
from app.workspaces import extract_short_site_name, strip_greeting_for_repeat

logger = logging.getLogger("parking.voice_gateway")

STATIC_DIR = BASE_DIR / "static"
SHORTCUTS_CACHE_DIR = DATA_DIR / "shortcuts"
PRESIGNED_BUNDLES_DIR = BASE_DIR / "app" / "presigned_shortcuts"
DATA_PRESIGNED_DIR = DATA_DIR / "shortcuts" / "presigned_packs"
MACOS_SHORTCUTS_BIN = "/usr/bin/shortcuts"


def _pack_slug_for_base_url(base_url: str) -> str:
    clean = str(base_url or "").strip().rstrip("/")
    if not clean:
        return ""
    parsed = urlparse(clean)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = parsed.port
    default_port = 443 if scheme == "https" else 80
    port_part = f"_{port}" if (port and port != default_port) else ""
    safe_host = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
    return f"{scheme}_{safe_host}{port_part}"


def _infer_paired_mobile_url(voice_base_url: str, configured_mobile_url: str = "") -> str:
    cfg_mob = str(configured_mobile_url or "").strip().rstrip("/")
    if cfg_mob:
        return cfg_mob
    parsed = urlparse(str(voice_base_url or "").strip())
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "http").lower()
    if host.startswith("voice.") and len(host) > 6:
        root_dom = host[6:]
        port_sfx = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
        return f"{scheme}://parking.{root_dom}{port_sfx}"
    mob_port = int(config_manager.get().get("mobile_http_port", 18790))
    if host:
        return f"{scheme}://{host}:{mob_port}"
    return f"http://127.0.0.1:{mob_port}"


# ---------------------------------------------------------------------------
# Multi-Language & Geo-Aware Dialect Registry (Mandarin / Cantonese / English /
# Sichuanese / Northeastern + Extensible Regional Packs)
# ---------------------------------------------------------------------------

VOICE_LANG_PROFILES: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    [
        (
            "zh-CN",
            {
                "code": "zh-CN",
                "label": "普通话（标准播报）",
                "short_label": "🎙️ 普通话",
                "menu_item": "🎙️ 普通话（标准播报） [zh-CN]",
                "apple_tts_lang": "zh-CN",
                "apple_tts_voice": "",
                "apple_preinstalled": True,
                "web_tts_lang": "zh-CN",
                "sr_lang": "zh-CN",
                "slot_token": "1",  # 1 char -> Shortcut count(Characters) < 2
                "default_enabled": True,
                "geo_regions": ["CN", "BJ", "TJ", "HE", "SD", "HA", "HB", "HN", "JS", "ZJ", "AH", "JX", "FJ"],
            },
        ),
        (
            "zh-HK",
            {
                "code": "zh-HK",
                "label": "粤语 · 广东话",
                "short_label": "🇭🇰 粤语",
                "menu_item": "🇭🇰 粤语 · 广东话 [zh-HK]",
                # Apple ActionKit `WFSpeakTextLanguage` strictly requires `yue-HK` (NOT `zh-HK`!)
                "apple_tts_lang": "yue-HK",
                "apple_tts_voice": "com.apple.voice.compact.zh-HK.Sinji",
                "apple_preinstalled": True,
                "web_tts_lang": "zh-HK",
                "sr_lang": "yue-Hant-HK",
                "slot_token": "22",  # 2 chars -> Shortcut count(Characters) < 3
                "default_enabled": True,
                "geo_regions": ["GD", "HK", "MO", "GX", "GUANGDONG", "HONG KONG", "MACAU"],
            },
        ),
        (
            "en-US",
            {
                "code": "en-US",
                "label": "English（英文播报）",
                "short_label": "🌐 English",
                "menu_item": "🌐 English（英文播报） [en-US]",
                "apple_tts_lang": "en-US",
                "apple_tts_voice": "com.apple.voice.compact.en-US.Samantha",
                "apple_preinstalled": True,
                "web_tts_lang": "en-US",
                "sr_lang": "en-US",
                "slot_token": "333",  # 3 chars -> Shortcut count(Characters) < 4
                "default_enabled": True,
                "geo_regions": ["INTL", "US", "GB", "AU", "CA", "SG", "NZ", "IE"],
            },
        ),
        (
            "zh-SC",
            {
                "code": "zh-SC",
                "label": "四川话 · 川渝方言",
                "short_label": "🌶️ 四川话",
                "menu_item": "🌶️ 四川话 · 川渝方言 [zh-SC]",
                "apple_tts_lang": "zh-CN",
                "apple_tts_voice": "com.apple.voice.compact.zh-CN-u-sd-cnsc.Fangfang",
                "apple_preinstalled": False,
                "cloud_neural_voice": "",
                "cloud_neural_rate": "+6%",
                "web_tts_lang": "zh-CN",
                "sr_lang": "zh-CN",
                "slot_token": "4444",  # 4 chars -> Shortcut count(Characters) < 5
                "default_enabled": True,
                "geo_regions": ["SC", "CQ", "GZ", "YN", "SICHUAN", "CHONGQING", "CHENGDU"],
            },
        ),
        (
            "zh-NE",
            {
                "code": "zh-NE",
                "label": "东北话 · 东北方言",
                "short_label": "❄️ 东北话",
                "menu_item": "❄️ 东北话 · 东北方言 [zh-NE]",
                "apple_tts_lang": "zh-CN",
                "apple_tts_voice": "com.apple.voice.enhanced.zh-CN-u-sd-cnln.Dongmei",
                "apple_preinstalled": False,
                "cloud_neural_voice": "zh-CN-liaoning-XiaobeiNeural",
                "cloud_neural_rate": "+10%",
                "web_tts_lang": "zh-CN-liaoning",
                "sr_lang": "zh-CN",
                "slot_token": "55555",  # 5 chars -> Shortcut count(Characters) < 6 (Xiaobei neural MP3!)
                "default_enabled": True,
                "geo_regions": ["NE", "LN", "JL", "HL", "LIAONING", "JILIN", "HEILONGJIANG", "SHENYANG", "HARBIN", "DALIAN"],
            },
        ),
        # Pre-wired regional dialect packs for future geo-location switching (no Shortcut re-signing needed!)
        (
            "zh-SN",
            {
                "code": "zh-SN",
                "label": "陕西话 · 关中方言",
                "short_label": "🏮 陕西话",
                "menu_item": "🏮 陕西话 · 关中方言 [zh-SN]",
                "apple_tts_lang": "zh-CN",
                "apple_tts_voice": "com.apple.voice.enhanced.zh-CN-u-sd-cnsn.Haohao",
                "apple_preinstalled": False,
                "cloud_neural_voice": "zh-CN-shaanxi-XiaoniNeural",
                "cloud_neural_rate": "+8%",
                "web_tts_lang": "zh-CN-shaanxi",
                "sr_lang": "zh-CN",
                "slot_token": "55555",  # 5 chars -> Shares Cloud Neural MP3 branch (`tts.mp3`)!
                "default_enabled": False,
                "geo_regions": ["SN", "SHAANXI", "XIAN"],
            },
        ),
        (
            "zh-TW",
            {
                "code": "zh-TW",
                "label": "国语 · 闽台腔",
                "short_label": "🌿 闽台国语",
                "menu_item": "🌿 国语 · 闽台温柔播报 [zh-TW]",
                "apple_tts_lang": "zh-TW",
                "apple_tts_voice": "com.apple.voice.compact.zh-TW.Meijia",
                "apple_preinstalled": True,
                "web_tts_lang": "zh-TW",
                "sr_lang": "zh-TW",
                "slot_token": "7777777",  # 7 chars -> Shortcut else branch
                "default_enabled": False,
                "geo_regions": ["TW", "TAIWAN", "TAIPEI"],
            },
        ),
    ]
)

DEFAULT_ACTIVE_VOICE_LANGS: List[str] = ["zh-CN", "zh-HK", "en-US", "zh-SC", "zh-NE"]


def normalize_voice_lang(raw: Optional[str], default: str = "zh-CN") -> str:
    """Normalize arbitrary user/menu/API input into a canonical `VOICE_LANG_PROFILES` key."""
    s = str(raw or "").strip()
    if not s or s.lower() in ("auto", "default", "none"):
        return default if default in VOICE_LANG_PROFILES else "zh-CN"

    # 1) Extract explicit `[code]` tag from `/api/voice/languages.txt` menu item if present
    m_tag = re.search(r"\[([a-zA-Z]{2,3}-[a-zA-Z0-9_-]{2,12})\]", s)
    if m_tag:
        cand_tag = m_tag.group(1)
        for k in VOICE_LANG_PROFILES:
            if k.lower() == cand_tag.lower():
                return k

    low = s.lower().replace("_", "-")
    for k in VOICE_LANG_PROFILES:
        if low == k.lower():
            return k

    if any(w in low for w in ("zh-hk", "yue", "zh-mo", "canton")) or any(
        w in s for w in ("粤", "粵", "广东", "廣東", "香港", "白话")
    ):
        return "zh-HK"
    if low.startswith("en") or "english" in low or any(w in s for w in ("英文", "英语")):
        return "en-US"
    if any(w in low for w in ("zh-sc", "cnsc", "sichuan", "chuan", "chengdu", "chongqing")) or any(
        w in s for w in ("四川", "川渝", "成都", "重庆", "巴适")
    ):
        return "zh-SC"
    if any(w in low for w in ("zh-ne", "cnln", "liaoning", "dongbei")) or any(
        w in s for w in ("东北", "辽宁", "吉林", "黑龙江", "沈阳", "哈尔滨", "老铁")
    ):
        return "zh-NE"
    if any(w in low for w in ("zh-sn", "cnsn", "shaanxi", "xian")) or any(
        w in s for w in ("陕西", "西安", "关中")
    ):
        return "zh-SN"
    if any(w in low for w in ("zh-tw", "taiwan", "minnan")) or any(
        w in s for w in ("台湾", "臺灣", "台灣", "闽台")
    ):
        return "zh-TW"
    if any(w in low for w in ("zh-cn", "mandarin", "cmn", "putonghua")) or any(
        w in s for w in ("普通话", "标准")
    ):
        return "zh-CN"
    return default if default in VOICE_LANG_PROFILES else "zh-CN"


def _edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def is_shortcut_voice_supported(
    lang_code: Optional[str],
    dialect_pack_ready: bool = False,
    allow_cloud_audio: bool = False,
) -> bool:
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    prof = VOICE_LANG_PROFILES.get(norm, VOICE_LANG_PROFILES["zh-CN"])
    if prof.get("apple_preinstalled", True) or dialect_pack_ready:
        return True
    if allow_cloud_audio and prof.get("cloud_neural_voice") and _edge_tts_available():
        return True
    return False


def uses_cloud_neural_shortcut_audio(
    lang_code: Optional[str],
    dialect_pack_ready: bool = False,
    map_mode: str = "text",
) -> bool:
    # CRITICAL: In Apple Shortcuts, `is.workflow.actions.playsound` is strictly blocking (`audioPlayerDidFinishPlaying:`)
    # and delays `previewdocument` / `showresult` until audio finishes!
    # Therefore, ONLY `map_mode == "silent"` (pure voice, zero visual popup) may use `playsound`.
    # All visual modes (`both`, `map`, `text`) MUST use `speaktext(WFSpeakTextWait=False)` so voice and
    # `文字 + 平面图同屏` launch 100% simultaneously in a single screen!
    if str(map_mode or "").strip().lower() != "silent":
        return False
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    prof = VOICE_LANG_PROFILES.get(norm, VOICE_LANG_PROFILES["zh-CN"])
    if prof.get("apple_preinstalled", True) or dialect_pack_ready:
        return False
    return bool(prof.get("cloud_neural_voice")) and _edge_tts_available()


def get_active_voice_languages(
    enabled_codes: Optional[List[str]] = None,
    geo_region: str = "",
    preinstalled_only: bool = False,
    allow_cloud_audio: bool = False,
) -> List[Dict[str, Any]]:
    """
    Return the active voice language profiles, supporting dynamic geo-region ordering &
    regional dialect activation (`geo_region` from Cloudflare headers, workspace, or query).
    When `preinstalled_only=True` (used by Apple Shortcuts `setup_once` visual modes),
    returns languages with non-blocking `speaktext(WFSpeakTextWait=False)` voices (`zh-CN`,
    `yue-HK`, `en-US`) so `文字 + 平面图同屏` always launches simultaneously without delay.
    """
    base_codes: List[str] = []
    raw_list = enabled_codes if isinstance(enabled_codes, list) and enabled_codes else DEFAULT_ACTIVE_VOICE_LANGS
    for item in raw_list:
        c = normalize_voice_lang(item, default="")
        if c and c in VOICE_LANG_PROFILES and c not in base_codes:
            base_codes.append(c)
    for core_c in DEFAULT_ACTIVE_VOICE_LANGS:
        if core_c not in base_codes:
            base_codes.append(core_c)

    reg_upper = str(geo_region or "").strip().upper()
    promoted_code: Optional[str] = None
    if reg_upper and reg_upper not in ("AUTO", "ALL", "DEFAULT", "CN"):
        for code, prof in VOICE_LANG_PROFILES.items():
            regions = [str(r).upper() for r in (prof.get("geo_regions") or [])]
            if reg_upper in regions:
                if code not in base_codes:
                    base_codes.append(code)
                if code != "zh-CN":
                    promoted_code = code
                break

    if promoted_code and promoted_code in base_codes:
        # Keep Mandarin #1 and promote the detected local regional language/dialect right to #2!
        rest = [c for c in base_codes if c not in ("zh-CN", promoted_code)]
        ordered_codes = (["zh-CN", promoted_code] + rest) if "zh-CN" in base_codes else ([promoted_code] + rest)
    else:
        ordered_codes = base_codes

    if preinstalled_only:
        ordered_codes = [
            c
            for c in ordered_codes
            if is_shortcut_voice_supported(c, dialect_pack_ready=False, allow_cloud_audio=allow_cloud_audio)
        ]
        if not ordered_codes:
            ordered_codes = ["zh-CN"]

    out: List[Dict[str, Any]] = []
    for c in ordered_codes:
        item = dict(VOICE_LANG_PROFILES[c])
        item["shortcut_ready"] = is_shortcut_voice_supported(c, dialect_pack_ready=False, allow_cloud_audio=False)
        item["cloud_tts_ready"] = bool(item.get("cloud_neural_voice")) and _edge_tts_available()
        out.append(item)
    return out


def coerce_shortcut_safe_voice_lang(
    lang_code: Optional[str],
    dialect_pack_ready: bool = False,
    allow_cloud_audio: bool = False,
) -> str:
    """
    Ensure Shortcut `speaktext` never reads dialect slang in a Mandarin voice AND never blocks
    visual `文字 + 平面图同屏` popups: unless `dialect_pack_ready=True` (device has Apple's `冬梅`/`盼盼`
    installed) or `allow_cloud_audio=True` (`silent` pure-audio mode), uninstalled dialects cleanly
    fall back to standard Mandarin (`zh-CN`).
    """
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    if is_shortcut_voice_supported(
        norm, dialect_pack_ready=dialect_pack_ready, allow_cloud_audio=allow_cloud_audio
    ):
        return norm
    return "zh-CN"


def voice_lang_to_apple_tts(lang_code: Optional[str]) -> str:
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    return str(VOICE_LANG_PROFILES.get(norm, VOICE_LANG_PROFILES["zh-CN"])["apple_tts_lang"])


def voice_lang_to_apple_voice_id(lang_code: Optional[str]) -> str:
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    return str(VOICE_LANG_PROFILES.get(norm, VOICE_LANG_PROFILES["zh-CN"]).get("apple_tts_voice") or "")


def _build_apple_speaktext_params(lang_code: str, wf_text_obj: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "WFSpeakTextWait": False,
        "WFSpeakTextLanguage": voice_lang_to_apple_tts(lang_code),
        "WFText": wf_text_obj,
    }
    vid = voice_lang_to_apple_voice_id(lang_code)
    if vid:
        params["WFSpeakTextVoice"] = vid
    return params


def voice_lang_to_slot_token(lang_code: Optional[str]) -> str:
    norm = normalize_voice_lang(lang_code, default="zh-CN")
    return str(VOICE_LANG_PROFILES.get(norm, VOICE_LANG_PROFILES["zh-CN"])["slot_token"])


def translate_parking_term_to_english(raw_text: str, fallback_code: str = "") -> str:
    """Deterministic, natural English translation for Chinese community/building/zone terms so `en-US` TTS never hits raw CJK."""
    s = str(raw_text or "").strip()
    if not s:
        return fallback_code or "Parking Zone"

    # Strip parenthetical notes like "（中心区）" or "(近南门)" if we already have building/unit info
    s_clean = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s_clean = re.sub(r"(\d+)\s*(?:号楼|栋|幢|座)", r" Building \1 ", s_clean)
    s_clean = re.sub(r"([A-Za-z]\d*)\s*座", r" Block \1 ", s_clean)
    s_clean = re.sub(r"(\d+)\s*单元门?", r" Unit \1 ", s_clean)
    s_clean = re.sub(r"(\d+)\s*号主?展馆", r" Hall \1 ", s_clean)

    phrase_map = [
        ("外围行车道路右侧停车带", "Outer Ring Road Right Strip"),
        ("外围行车道路左侧停车带", "Outer Ring Road Left Strip"),
        ("无划线弹性带", "Flexible Roadside Strip"),
        ("路侧顺停区", "Roadside Strip"),
        ("路侧停车带", "Roadside Strip"),
        ("地面访客停车区", "Visitor Parking Lot"),
        ("访客与接送停车带", "Drop-off & Visitor Strip"),
        ("贵宾地面停车场", "VIP Surface Lot"),
        ("地面停车带", "Surface Parking Strip"),
        ("地面停车区", "Surface Parking Zone"),
        ("窗台俯拍区", "Window-View Zone"),
        ("断头路尽头", "Dead-End Zone"),
        ("主干道顺停区", "Main Road Strip"),
        ("主干道", "Main Road"),
        ("研发塔楼大堂", "R&D Tower Lobby"),
        ("办公大堂", "Office Lobby"),
        ("研发大堂", "R&D Lobby"),
        ("门诊医技楼大厅", "Outpatient Main Hall"),
        ("住院部", "Inpatient"),
        ("综合行政楼入口", "Admin Building Entrance"),
        ("接送等候区", "Pickup Zone"),
        ("会议中心入口", "Convention Entrance"),
        ("检票口", "Gate"),
        ("停车带", "Parking Strip"),
        ("停车区", "Parking Zone"),
        ("顺停区", "Roadside Strip"),
        ("车位带", "Parking Strip"),
        ("楼下", "Downstairs"),
        ("靠近", "near "),
        ("左侧", "Left Side"),
        ("右侧", "Right Side"),
        ("东侧", "East Side"),
        ("西侧", "West Side"),
        ("南侧", "South Side"),
        ("北侧", "North Side"),
        ("后面", "Rear"),
        ("后侧", "Rear"),
        ("前面", "Front"),
        ("前侧", "Front"),
        ("东校门", "East Gate"),
        ("东门", "East Gate"),
        ("西门", "West Gate"),
        ("南门", "South Gate"),
        ("北门", "North Gate"),
        ("入口", "Entrance"),
        ("大厅", "Lobby"),
        ("大堂", "Lobby"),
    ]
    for zh_p, en_p in phrase_map:
        s_clean = s_clean.replace(zh_p, f" {en_p} ")

    # Remove remaining CJK punctuation/characters cleanly
    s_ascii = re.sub(r"[\u4e00-\u9fff·•，。、：；！？]+", " ", s_clean)
    s_ascii = re.sub(r"\s+", " ", s_ascii).strip(" -_,.")
    if s_ascii and re.search(r"[A-Za-z]", s_ascii):
        return s_ascii
    if fallback_code:
        if re.match(r"^\d+-\d+$", fallback_code):
            parts = fallback_code.split("-")
            return f"Building {parts[0]} Unit {parts[1]}"
        return f"Zone {fallback_code}"
    return "Recommended Zone"


def _append_multilang_speaktext_actions(
    actions: List[Dict[str, Any]],
    speech_output_uuid: str,
    lang_count_uuid: str,
    tts_mp3_url: Optional[Any] = None,
    allow_blocking_playsound: bool = False,
) -> None:
    """
    Append nested numeric `conditional` (`count(Characters) < N`, `WFCondition=0`) branches:
      - 1 char  (`< 2`): `zh-CN`  (Mandarin)
      - 2 chars (`< 3`): `yue-HK` + `Sinji`    (Cantonese · 善怡)
      - 3 chars (`< 4`): `en-US`  + `Samantha` (American English)
      - 4 chars (`< 5`): `zh-CN`  + `Fangfang` (Sichuanese · 盼盼 when dialect_ready=1)
      - 5 chars (`< 6`): `zh-CN`  + `Dongmei`  (Northeastern · 冬梅 with `WFSpeakTextWait=False` in all
                         visual modes so `文字 + 平面图同屏` pops up simultaneously at 0ms; or `playsound`
                         ONLY when `allow_blocking_playsound=True`, i.e. `silent` pure-audio mode!)
      - 7+ chars (`else`): `zh-TW` + `Meijia`  (Taiwan Mandarin · 美佳)
    NEVER emits `is.workflow.actions.notification`, ensuring `文字 + 平面图同屏` ALWAYS displays together!
    """
    branches = [
        ("2", "zh-CN"),
        ("3", "zh-HK"),
        ("4", "en-US"),
        ("5", "zh-SC"),
        ("6", "zh-NE"),
    ]
    speech_text_token = {
        "Value": {
            "attachmentsByRange": {
                "{0, 1}": {
                    "OutputName": "URL的内容",
                    "OutputUUID": speech_output_uuid,
                    "Type": "ActionOutput",
                }
            },
            "string": "\ufffc",
        },
        "WFSerializationType": "WFTextTokenString",
    }
    count_var_input = {
        "Type": "Variable",
        "Variable": {
            "Value": {
                "OutputName": "数量",
                "OutputUUID": lang_count_uuid,
                "Type": "ActionOutput",
            },
            "WFSerializationType": "WFTextTokenAttachment",
        },
    }
    group_ids: List[str] = []
    for threshold_str, prof_code in branches:
        grp = str(uuid.uuid4()).upper()
        group_ids.append(grp)
        actions.append(
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                "WFWorkflowActionParameters": {
                    "GroupingIdentifier": grp,
                    "WFControlFlowMode": 0,
                    "WFCondition": 0,
                    "WFNumberValue": threshold_str,
                    "WFInput": count_var_input,
                },
            }
        )
        if prof_code == "zh-NE" and allow_blocking_playsound and tts_mp3_url is not None:
            mp3_dl_uuid = str(uuid.uuid4()).upper()
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": mp3_dl_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": tts_mp3_url,
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.playsound",
                    "WFWorkflowActionParameters": {
                        "WFInput": {
                            "Value": {
                                "OutputName": "URL的内容",
                                "OutputUUID": mp3_dl_uuid,
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        }
                    },
                }
            )
        else:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                    "WFWorkflowActionParameters": _build_apple_speaktext_params(prof_code, speech_text_token),
                }
            )
        actions.append(
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                "WFWorkflowActionParameters": {
                    "GroupingIdentifier": grp,
                    "WFControlFlowMode": 1,
                },
            }
        )

    # Final fallback (`7+` chars -> `zh-TW`)
    actions.append(
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
            "WFWorkflowActionParameters": _build_apple_speaktext_params("zh-TW", speech_text_token),
        }
    )

    for grp in reversed(group_ids):
        actions.append(
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                "WFWorkflowActionParameters": {
                    "GroupingIdentifier": grp,
                    "WFControlFlowMode": 2,
                },
            }
        )
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
        self._tts_mp3_cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._tts_mp3_bytes_total: int = 0
        self._rate_buckets: Dict[str, Deque[float]] = {}
        self._daily_date: str = ""
        self._daily_llm_calls: int = 0
        self._total_brief_calls: int = 0
        self._total_ask_calls: int = 0
        self.weixin_bridge = WeixinClawBridge(self)

    def synthesize_tts_mp3(self, text: str, voice_lang: str = "zh-NE") -> Optional[bytes]:
        """
        Synthesize authentic dialect/language neural MP3 bytes (`晓北` for `zh-NE`, `晓妮` for `zh-SN`,
        `晓曼` for `zh-HK`, `Jenny` for `en-US`, `晓晓` for `zh-CN`) with a bounded 4 MB RAM LRU cache.
        """
        clean_text = re.sub(r"\s+", " ", str(text or "")).strip()[:420]
        if not clean_text or not _edge_tts_available():
            return None

        norm_lang = normalize_voice_lang(voice_lang, default="zh-CN")
        prof = VOICE_LANG_PROFILES.get(norm_lang, VOICE_LANG_PROFILES["zh-CN"])
        voice_name = str(prof.get("cloud_neural_voice") or "").strip()
        rate_str = str(prof.get("cloud_neural_rate") or "+6%").strip()
        if not voice_name:
            fallback_voices = {
                "zh-HK": ("zh-HK-HiuMaanNeural", "+6%"),
                "en-US": ("en-US-JennyNeural", "+4%"),
                "zh-TW": ("zh-TW-HsiaoChenNeural", "+6%"),
                "zh-CN": ("zh-CN-XiaoxiaoNeural", "+6%"),
            }
            voice_name, rate_str = fallback_voices.get(norm_lang, ("zh-CN-XiaoxiaoNeural", "+6%"))

        ckey = hashlib.sha256(f"{voice_name}|{rate_str}|{clean_text}".encode("utf-8")).hexdigest()[:20]
        with self._lock:
            hit = self._tts_mp3_cache.get(ckey)
            if hit:
                self._tts_mp3_cache.move_to_end(ckey)
                return hit

        async def _run_edge() -> bytes:
            import edge_tts

            comm = edge_tts.Communicate(clean_text, voice_name, rate=rate_str)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf.extend(chunk.get("data") or b"")
            return bytes(buf)

        try:
            loop = asyncio.new_event_loop()
            try:
                audio_bytes = loop.run_until_complete(asyncio.wait_for(_run_edge(), timeout=6.0))
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("Cloud neural TTS (%s) failed: %s", voice_name, exc)
            return None

        if not audio_bytes or len(audio_bytes) < 256:
            return None

        max_entries = 48
        max_bytes = 4 * 1024 * 1024  # 4 MB hard cap
        with self._lock:
            if ckey in self._tts_mp3_cache:
                self._tts_mp3_bytes_total -= len(self._tts_mp3_cache.pop(ckey))
            self._tts_mp3_cache[ckey] = audio_bytes
            self._tts_mp3_bytes_total += len(audio_bytes)
            while len(self._tts_mp3_cache) > max_entries or self._tts_mp3_bytes_total > max_bytes:
                _, evicted = self._tts_mp3_cache.popitem(last=False)
                self._tts_mp3_bytes_total = max(0, self._tts_mp3_bytes_total - len(evicted))
        return audio_bytes

    def prewarm_tts_async(self, unit_id: Optional[str], voice_lang: Optional[str], vehicle_type: str = "sedan") -> None:
        """Asynchronously pre-warm RAM MP3 cache so subsequent Web / silent Shortcut playback hits in <1ms."""
        norm_l = normalize_voice_lang(voice_lang, default="zh-CN")
        prof = VOICE_LANG_PROFILES.get(norm_l, {})
        if not prof.get("cloud_neural_voice") or not _edge_tts_available():
            return

        def _worker() -> None:
            try:
                br = self.build_voice_brief(
                    unit_id=unit_id,
                    repeat=1,
                    vehicle_type=vehicle_type,
                    channel="siri_prewarm",
                    voice_lang=norm_l,
                )
                self.synthesize_tts_mp3(str(br.get("single_text") or ""), voice_lang=norm_l)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

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
        pack_domains: List[str] = []
        for root_d in (DATA_PRESIGNED_DIR, PRESIGNED_BUNDLES_DIR):
            if root_d.is_dir():
                for mf in sorted(root_d.glob("*/manifest.json")):
                    try:
                        mdata = json.loads(mf.read_text(encoding="utf-8"))
                        b_url = str(mdata.get("base_url") or "").strip()
                        if b_url and b_url not in pack_domains:
                            pack_domains.append(b_url)
                            has_presigned = True
                    except Exception:
                        continue
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
            "presigned_pack_domains": pack_domains,
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
        pub_voice = str(vg.get("public_voice_url") or "")
        pub_mob = _infer_paired_mobile_url(pub_voice, str(vg.get("public_mobile_url") or ""))
        geo_reg = str(prof.get("geo_region") or vg.get("geo_region") or "auto")
        active_langs = get_active_voice_languages(vg.get("enabled_voice_langs"), geo_reg)
        return {
            "workspace_id": self._ws_id(),
            "workspace_name": str(prof.get("name") or cfg.get("community_name") or "邻里公益寻位"),
            "scene_type": str(prof.get("scene_type") or "community"),
            "preferred_voice_lang": str(prof.get("preferred_voice_lang") or ""),
            "default_voice_lang": normalize_voice_lang(
                prof.get("preferred_voice_lang") or vg.get("default_voice_lang") or "zh-CN"
            ),
            "geo_region": geo_reg,
            "voice_languages": active_langs,
            "enabled": bool(vg.get("enabled", True)),
            "port": int(cfg.get("voice_http_port", 18791)),
            "mobile_port": int(cfg.get("mobile_http_port", 18790)),
            "token_required": bool(eff_tok),
            "default_unit_id": str(prof.get("default_unit_id") or vg.get("default_unit_id") or "8-1"),
            "default_repeat": int(vg.get("default_repeat") or 2),
            "public_mobile_url": pub_mob,
            "public_voice_url": pub_voice,
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
        if v in ("ev", "new_energy", "electric", "新能源", "充电", "电车"):
            return "ev"
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
        voice_lang: str = "zh-CN",
    ) -> str:
        clean = str(single_text or "").strip()
        lang = normalize_voice_lang(voice_lang, default="zh-CN")
        if not clean:
            if lang == "en-US":
                return "No live parking broadcast information is currently available."
            if lang == "zh-HK":
                return "目前暫無可用車位播報資訊。"
            return "当前暂无可用停车播报信息。"
        if int(repeat_count or 1) <= 1:
            return clean
        core = str(repeat_core or "").strip() or strip_greeting_for_repeat(clean)
        connectors = {
            "zh-CN": f"再为您重复播报一遍：{core}",
            "zh-HK": f"再為您重複播報一次：{core}",
            "en-US": f"Repeating once more: {core}",
            "zh-SC": f"再给您摆一遍哈：{core}",
            "zh-NE": f"再给您念叨一遍哈：{core}",
            "zh-SN": f"再给咱嘹一遍：{core}",
            "zh-TW": f"再為您重複播報一遍：{core}",
        }
        return f"{clean} {connectors.get(lang, connectors['zh-CN'])}"

    def resolve_effective_voice_lang(
        self,
        explicit_lang: Optional[str] = None,
        binding: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool]:
        """Return `(canonical_lang_code, is_user_or_binding_selected)`."""
        raw_exp = str(explicit_lang or "").strip()
        if raw_exp and raw_exp.lower() not in ("auto", "default"):
            return normalize_voice_lang(raw_exp, default="zh-CN"), True
        if binding and str(binding.get("voice_lang") or "").strip():
            return normalize_voice_lang(binding.get("voice_lang"), default="zh-CN"), True
        prof = self._ws_profile()
        vg_cfg = config_manager.get().get("voice_gateway", {})
        ws_pref = str(prof.get("preferred_voice_lang") or vg_cfg.get("default_voice_lang") or "zh-CN").strip()
        return normalize_voice_lang(ws_pref, default="zh-CN"), False

    def _localize_broadcast_texts(
        self,
        voice_lang: str,
        user_selected_lang: bool,
        rec: Dict[str, Any],
        total_free: int,
        total_spots: int,
        unit_id: str,
        unit_name: str,
    ) -> Tuple[str, str, str]:
        """
        Render `(single_text, repeat_text, display_text)` in `voice_lang`:
        - `zh-CN`: Standard Mandarin (preserves custom workspace greeting templates 100%).
        - `zh-HK`: Authentic spoken Cantonese (`歡迎返屋企 / 仲有 / 優先推介 / 行返去`).
        - `en-US`: Crisp in-car American English with translated building/strip names (`Samantha`-ready).
        - `zh-SC`: Warm idiomatic Sichuan/Chongqing dialect (`欢迎回屋头 / 巴适得很 / 走拢单元门`).
        - `zh-NE`: Upbeat Northeastern dialect (`妥妥滴 / 咱院里 / 溜达回单元门`).
        - `zh-SN` / `zh-TW`: Pre-wired regional dialect templates for geo-switching.
        """
        prof = self._ws_profile()
        cfg = config_manager.get()
        full_comm = str(prof.get("name") or cfg.get("community_name") or "邻里公益寻位").strip()
        short_site = str(prof.get("short_name") or "").strip() or extract_short_site_name(full_comm)
        scene_type = str(prof.get("scene_type") or "community").strip().lower()
        role_title = str(prof.get("role_title") or ("业主" if scene_type == "community" else "贵宾")).strip()
        has_custom_tpl = bool(str(prof.get("greeting_template") or "").strip())

        base_single = str(rec.get("voice_text") or "").strip() or f"全区共{total_spots}个监测车位，当前净余{total_free}位。"
        base_repeat = str(rec.get("repeat_voice_text") or "").strip() or strip_greeting_for_repeat(base_single)
        base_display = str(rec.get("display_text") or "").strip() or base_single

        lang = normalize_voice_lang(voice_lang, default="zh-CN")
        # Preserve admin-authored custom workspace template when user didn't explicitly override dialect
        if lang == "zh-CN" or (not user_selected_lang and has_custom_tpl and lang != "en-US"):
            return base_single, base_repeat, base_display

        has_rec = bool(rec.get("has_recommendation")) and isinstance(rec.get("primary"), dict)
        prim = rec.get("primary") or {}
        bkp = rec.get("backup") if isinstance(rec.get("backup"), dict) else None
        p_zone = str(prim.get("zone_name") or prim.get("group_name") or "")
        p_free = int(prim.get("effective_free") or 0)
        p_walk = int(round(float(prim.get("walk_meters") or 0)))
        movable_cnt = int(prim.get("movable_free") or 0)
        ped_cnt = int(prim.get("pedestrian_free") or 0)
        clean_cnt = int(prim.get("clean_free") or p_free)
        active_tag_code = str(rec.get("tag_filter") or "").strip()
        active_tag_cn = str(rec.get("tag_label") or "").strip()
        tag_tot_n = int(rec.get("tag_total_spots") or 0)
        tag_free_n = int(rec.get("tag_effective_free") or 0)
        en_tag_map = {
            "ev_charging": "EV charging",
            "rentable": "rentable shared",
            "private": "private reserved",
            "large_friendly": "wide SUV-friendly",
            "accessible": "accessible",
            "visitor": "visitor",
        }

        if active_tag_code and lang != "zh-CN":
            if lang == "en-US":
                en_t = en_tag_map.get(active_tag_code, active_tag_code)
                en_unit = translate_parking_term_to_english(unit_name, fallback_code=unit_id)
                if not has_rec:
                    if tag_tot_n <= 0:
                        s_en = f"No {en_t} parking spots are configured yet. {total_free} regular spots are open."
                    else:
                        s_en = f"All {tag_tot_n} {en_t} spots are currently occupied. {total_free} regular spots remain available."
                    return s_en, f"No {en_t} spots currently open.", base_display
                en_pz = translate_parking_term_to_english(
                    p_zone, fallback_code=str(prim.get("group_code") or prim.get("zone_id") or "")
                )
                s_en = (
                    f"Welcome! {tag_free_n} of {tag_tot_n} {en_t} spots available. "
                    f"Best match: {en_pz}, {p_free} open, about {p_walk} meters walk to {en_unit}."
                )
                return s_en, f"Best {en_t} match: {en_pz}, {p_free} open.", base_display
            if lang == "zh-HK":
                if not has_rec:
                    s_hk = (
                        f"目前屋苑暫未配置「{active_tag_cn}」車位。"
                        if tag_tot_n <= 0
                        else f"目前屋苑 {tag_tot_n} 個「{active_tag_cn}」車位已泊滿晒，普通車位仲有 {total_free} 個。"
                    )
                    return s_hk, f"{active_tag_cn}車位目前已滿。", base_display
                s_hk = (
                    f"目前屋苑「{active_tag_cn}」車位共 {tag_tot_n} 個、仲有 {tag_free_n} 個空位，"
                    f"優先為您推介 {p_zone}，而家仲有 {p_free} 個{active_tag_cn}空位，行返去單元門口約 {p_walk} 米。"
                )
                return s_hk, f"優先推介 {p_zone}，仲有 {p_free} 個{active_tag_cn}位。", base_display
            if lang == "zh-SC":
                if not has_rec:
                    s_sc = (
                        f"院里头暂时还没配「{active_tag_cn}」车位哈。"
                        if tag_tot_n <= 0
                        else f"不好意思哈，院里 {tag_tot_n} 个「{active_tag_cn}」车位都停满完了，普通车位还有 {total_free} 个。"
                    )
                    return s_sc, f"{active_tag_cn}车位暂时停满了。", base_display
                s_sc = (
                    f"巴适得很，院里「{active_tag_cn}」车位共 {tag_tot_n} 个、还有 {tag_free_n} 个空位，"
                    f"优先给您推荐切 {p_zone}，这儿还有 {p_free} 个{active_tag_cn}位子，走拢单元门才 {p_walk} 米。"
                )
                return s_sc, f"优先切 {p_zone}，还有 {p_free} 个{active_tag_cn}位。", base_display
            if lang == "zh-NE":
                if not has_rec:
                    s_ne = (
                        f"咱院里暂时还没设「{active_tag_cn}」车位呢。"
                        if tag_tot_n <= 0
                        else f"哎呀，咱院里 {tag_tot_n} 个「{active_tag_cn}」车位这会儿全满了，普通车位还有 {total_free} 个。"
                    )
                    return s_ne, f"{active_tag_cn}车位这会儿停满了。", base_display
                s_ne = (
                    f"妥妥滴，咱院里「{active_tag_cn}」车位共 {tag_tot_n} 个、这会儿还有 {tag_free_n} 个空位，"
                    f"优先推荐您奔 {p_zone}，那块儿还有 {p_free} 个{active_tag_cn}空位，溜达回单元门也就 {p_walk} 米。"
                )
                return s_ne, f"优先奔 {p_zone}，还有 {p_free} 个{active_tag_cn}位，老省心了。", base_display

        if lang == "en-US":
            en_unit = translate_parking_term_to_english(unit_name, fallback_code=unit_id)
            if not has_rec:
                s_en = (
                    "Welcome! All monitored parking spots are currently occupied. "
                    "Please use a nearby overflow parking lot."
                )
                r_en = "All monitored parking spots are currently full."
                d_en = f"📍 {en_unit} · Full (0/{total_spots} free)\nPlease head to a nearby overflow lot."
                return s_en, r_en, d_en

            en_p_zone = translate_parking_term_to_english(p_zone, fallback_code=str(prim.get("group_code") or prim.get("zone_id") or ""))
            en_hint = ""
            if clean_cnt <= 0 and ped_cnt > 0:
                en_hint = " Drive slowly as pedestrians may be nearby."
            elif clean_cnt <= 0 and movable_cnt > 0:
                en_hint = " Light movable items may need to be shifted aside."

            en_bkp_full = ""
            en_bkp_short = ""
            en_bkp_card = ""
            if bkp:
                en_b_zone = translate_parking_term_to_english(
                    str(bkp.get("zone_name") or ""), fallback_code=str(bkp.get("group_code") or "")
                )
                b_free = int(bkp.get("effective_free") or 0)
                b_walk = int(round(float(bkp.get("walk_meters") or 0)))
                en_bkp_full = f" Backup option: {en_b_zone}, {b_free} open, {b_walk} meters walk."
                en_bkp_short = f", backup {en_b_zone} with {b_free} open"
                en_bkp_card = f"\n↪️ Backup: {en_b_zone} ({b_free} free · {b_walk}m)"

            welcome_en = "Welcome home!" if scene_type == "community" else "Welcome!"
            spot_word_tot = "spot" if total_free == 1 else "spots"
            spot_word_p = "spot" if p_free == 1 else "spots"
            s_en = (
                f"{welcome_en} {total_free} parking {spot_word_tot} available. "
                f"Best match: {en_p_zone}, {p_free} {spot_word_p} open, "
                f"about {p_walk} meters walk to {en_unit}.{en_hint}{en_bkp_full}"
            )
            r_en = f"Best match: {en_p_zone}, {p_free} {spot_word_p} open{en_bkp_short}."
            d_en = (
                f"📍 {en_unit} · Total {total_free}/{total_spots} free\n"
                f"✅ Recommended: {en_p_zone} ({p_free} free · {p_walk}m walk){en_bkp_card}"
            )
            return s_en, r_en, d_en

        if lang == "zh-HK":
            greet_hk = (
                f"尊貴嘅{short_site}業主，歡迎返屋企！"
                if scene_type == "community"
                else f"歡迎蒞臨{short_site}！"
            )
            dest_hk = "單元門口" if scene_type == "community" else unit_name
            if not has_rec:
                s_hk = f"{greet_hk}唔好意思，目前場內監測車位已經泊滿晒，建議您直接前往周邊後備停車場。"
                return s_hk, "目前場內車位已泊滿，建議前往周邊車場。", base_display

            hint_hk = ""
            if clean_cnt <= 0 and ped_cnt > 0:
                hint_hk = "車位附近有人臨時停留，慢駛靠近會自動讓開。"
            elif clean_cnt <= 0 and movable_cnt > 0:
                hint_hk = "車位內有輕巧雜物，稍微移開即可泊入。"
            bkp_hk = ""
            bkp_short_hk = ""
            if bkp:
                bz = str(bkp.get("zone_name") or "")
                bf = int(bkp.get("effective_free") or 0)
                bw = int(round(float(bkp.get("walk_meters") or 0)))
                bkp_hk = f"後備可以揀 {bz}，仲有 {bf} 個位，行路大約 {bw} 米。"
                bkp_short_hk = f"，後備 {bz} 仲有 {bf} 個"
            s_hk = (
                f"{greet_hk}目前屋苑仲有 {total_free} 個車位，優先為您推介 {p_zone}，"
                f"而家仲有 {p_free} 個位，行返去{dest_hk}大約 {p_walk} 米。{hint_hk}{bkp_hk}"
            )
            r_hk = f"優先推介 {p_zone}，仲有 {p_free} 個車位{bkp_short_hk}。"
            return s_hk, r_hk, base_display

        if lang == "zh-SC":
            greet_sc = (
                f"尊贵的{short_site}{role_title}，欢迎回屋头！"
                if scene_type == "community"
                else f"欢迎来到{short_site}！"
            )
            dest_sc = "单元门" if scene_type == "community" else unit_name
            if not has_rec:
                s_sc = f"{greet_sc}不好意思哈，现在院坝头车位已经停满完了，建议您直接切周边备选停车场。"
                return s_sc, "院里头车位已经停满完了，建议切周边备选车场。", base_display

            hint_sc = ""
            if clean_cnt <= 0 and ped_cnt > 0:
                hint_sc = "车位旁有人临时站倒起，慢点开拢自然就让开了。"
            elif clean_cnt <= 0 and movable_cnt > 0:
                hint_sc = "里头有个轻巧物件，顺手挪一下就能停进去。"
            bkp_sc = ""
            bkp_short_sc = ""
            if bkp:
                bz = str(bkp.get("zone_name") or "")
                bf = int(bkp.get("effective_free") or 0)
                bw = int(round(float(bkp.get("walk_meters") or 0)))
                bkp_sc = f"备选还可以切 {bz}，那边还有 {bf} 个位子，走过去 {bw} 米。"
                bkp_short_sc = f"，备选切 {bz} 还有 {bf} 个"
            s_sc = (
                f"{greet_sc}巴适得很，现在院里拢共还有 {total_free} 个车位，优先给您推荐 {p_zone}，"
                f"这儿还有 {p_free} 个空位，下车走拢{dest_sc}才 {p_walk} 米。{hint_sc}{bkp_sc}"
            )
            r_sc = f"优先推荐切 {p_zone}，还有 {p_free} 个车位{bkp_short_sc}，安逸得很。"
            return s_sc, r_sc, base_display

        if lang == "zh-NE":
            greet_ne = (
                f"欢迎回家呀{short_site}家人！"
                if scene_type == "community"
                else f"欢迎来到{short_site}呀老铁！"
            )
            dest_ne = "单元门" if scene_type == "community" else unit_name
            if not has_rec:
                s_ne = f"{greet_ne}哎呀不好意思，咱院里车位这会儿全停满了，建议您直接奔周边备选停车场吧。"
                return s_ne, "咱院里车位这会儿停满了，建议直接奔周边停车场。", base_display

            hint_ne = ""
            if clean_cnt <= 0 and ped_cnt > 0:
                hint_ne = "那块儿有人临时待着呢，慢点儿往前靠就让开了。"
            elif clean_cnt <= 0 and movable_cnt > 0:
                hint_ne = "位上有个小物件，搭把手挪一下就能停。"
            bkp_ne = ""
            bkp_short_ne = ""
            if bkp:
                bz = str(bkp.get("zone_name") or "")
                bf = int(bkp.get("effective_free") or 0)
                bw = int(round(float(bkp.get("walk_meters") or 0)))
                bkp_ne = f"备选还能奔 {bz}，那旮沓还有 {bf} 个位，走道儿 {bw} 米。"
                bkp_short_ne = f"，备选奔 {bz} 还有 {bf} 个"
            s_ne = (
                f"{greet_ne}妥妥滴，咱院里现在还有 {total_free} 个车位，优先推荐您奔 {p_zone}，"
                f"那块儿还有 {p_free} 个空位，停好车溜达回{dest_ne}也就 {p_walk} 米。{hint_ne}{bkp_ne}"
            )
            r_ne = f"优先奔 {p_zone}，还有 {p_free} 个车位{bkp_short_ne}，老省心了。"
            return s_ne, r_ne, base_display

        if lang == "zh-SN":
            if not has_rec:
                return f"欢迎回{short_site}！这会儿院里车位满咧，建议去周边备选停车场。", "院里车位满咧，请去周边车场。", base_display
            bkp_sn = f"备选还能去 {bkp.get('zone_name')}，还有 {int(bkp.get('effective_free') or 0)} 个位。" if bkp else ""
            s_sn = (
                f"欢迎回家！美得很，{short_site}现在还有 {total_free} 个车位，优先推荐 {p_zone}，"
                f"还有 {p_free} 个空位，走到单元门 {p_walk} 米。{bkp_sn}"
            )
            r_sn = f"优先推荐 {p_zone}，还有 {p_free} 个车位。"
            return s_sn, r_sn, base_display

        if lang == "zh-TW":
            if not has_rec:
                return f"歡迎回到{short_site}，目前社區車位已滿，建議您前往周邊停車場。", "目前車位已滿，請前往周邊停車場。", base_display
            bkp_tw = f"備選推薦 {bkp.get('zone_name')}，尚餘 {int(bkp.get('effective_free') or 0)} 位。" if bkp else ""
            s_tw = (
                f"歡迎回家！目前{short_site}尚餘 {total_free} 個停車位，優先為您推薦 {p_zone}，"
                f"目前還有 {p_free} 個空位，步行至單元門約 {p_walk} 公尺。{bkp_tw}"
            )
            r_tw = f"優先推薦 {p_zone}，尚餘 {p_free} 個車位。"
            return s_tw, r_tw, base_display

        return base_single, base_repeat, base_display

    @staticmethod
    def _match_voice_lang_command_from_text(text: str) -> Optional[str]:
        s = str(text or "").strip()
        if not s:
            return None
        low = s.lower()
        has_switch_verb = any(
            v in low
            for v in (
                "切换",
                "换成",
                "改成",
                "改用",
                "设为",
                "设置",
                "换用",
                "改讲",
                "改说",
                "播报语言",
                "用粤语",
                "用四川",
                "用东北",
                "用英文",
                "用英语",
                "用普通话",
                "讲粤语",
                "讲四川",
                "讲东北",
                "说粤语",
                "说四川",
                "说东北",
                "switch to",
                "speak in",
                "in english",
                "use english",
                "in cantonese",
            )
        )
        if not has_switch_verb:
            return None
        detected = normalize_voice_lang(s, default="")
        return detected or None

    def build_voice_brief(
        self,
        unit_id: Optional[str] = None,
        repeat: Optional[int] = None,
        vehicle_type: Optional[str] = None,
        entry_gate_id: Optional[str] = None,
        channel: str = "siri",
        external_uid: str = "",
        voice_lang: Optional[str] = None,
        tag_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        with self._lock:
            self._total_brief_calls += 1

        cfg = config_manager.get()
        vg_cfg = cfg.get("voice_gateway", {})
        resolved_uid, resolved_uname, binding = self.resolve_unit_id(unit_id, channel, external_uid)
        eff_lang, user_lang_sel = self.resolve_effective_voice_lang(voice_lang, binding)

        if repeat is None:
            rep = int(binding.get("repeat_count") if binding else (vg_cfg.get("default_repeat") or 2))
        else:
            rep = 1 if int(repeat) <= 1 else 2

        norm_tag = resolve_tag_filter_code(tag_filter)
        vtype = self._normalize_vehicle_type(
            vehicle_type or ("ev" if norm_tag == "ev_charging" else (binding.get("vehicle_type") if binding else "sedan"))
        )
        rec = self.ctx.recommender.recommend(
            unit_id=resolved_uid,
            vehicle_type=vtype,
            driver_tag="18791免手语音播报",
            acquire_lock=False,
            entry_gate_id=entry_gate_id,
            tag_filter=norm_tag or None,
        )
        zones = self.ctx.recommender.get_zone_summaries(vehicle_type=vtype)
        tag_availability = self.ctx.recommender.build_tag_availability(
            zone_summaries=zones,
            selected_unit_id=resolved_uid,
        )
        total_free = sum(int(z.get("effective_free") or 0) for z in zones)
        total_spots = sum(int(z.get("total_capacity") or z.get("total_spots") or 0) for z in zones)

        single_text, repeat_text, display_text = self._localize_broadcast_texts(
            voice_lang=eff_lang,
            user_selected_lang=user_lang_sel,
            rec=rec,
            total_free=total_free,
            total_spots=total_spots,
            unit_id=resolved_uid,
            unit_name=resolved_uname,
        )
        speech_text = self.format_repeated_speech(
            single_text, rep, repeat_core=repeat_text, voice_lang=eff_lang
        )
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
            "voice_lang": eff_lang,
            "apple_tts_lang": voice_lang_to_apple_tts(eff_lang),
            "repeat": rep,
            "vehicle_type": vtype,
            "tag_filter": rec.get("tag_filter", ""),
            "tag_label": rec.get("tag_label", ""),
            "tag_total_spots": rec.get("tag_total_spots", 0),
            "tag_effective_free": rec.get("tag_effective_free", 0),
            "tag_availability": tag_availability,
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
        voice_lang: Optional[str] = None,
        tag_filter: Optional[str] = None,
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
        cmd_lang = self._match_voice_lang_command_from_text(clean_q)

        # 1) Fast-path: voice binding command ("绑定8号楼1单元" / "切换粤语" / "用四川话播报")
        has_unit_bind_kw = any(
            kw in clean_q for kw in ("绑定", "设为我家", "设置我家", "默认去", "默认单元", "记住我家")
        )
        if has_unit_bind_kw or cmd_lang:
            matched_u = self._match_unit_from_text(clean_q, units) if has_unit_bind_kw else None
            if matched_u or cmd_lang:
                resolved_cur_u, resolved_cur_uname, cur_bind = self.resolve_unit_id(
                    unit_id, channel, external_uid
                )
                target_uid = str(matched_u["unit_id"]) if matched_u else resolved_cur_u
                target_uname = str(matched_u["name"]) if matched_u else resolved_cur_uname
                rep_bind = (
                    1
                    if ("一遍" in clean_q or "1遍" in clean_q)
                    else int(cur_bind.get("repeat_count") if cur_bind else 2)
                )
                eff_bind_lang = cmd_lang or voice_lang
                if external_uid:
                    self.ctx.db.upsert_im_user_binding(
                        channel=channel,
                        external_uid=external_uid,
                        unit_id=target_uid,
                        repeat_count=rep_bind,
                        voice_lang=eff_bind_lang,
                    )
                brief = self.build_voice_brief(
                    unit_id=target_uid,
                    repeat=1,
                    vehicle_type=vehicle_type,
                    channel=channel,
                    external_uid=external_uid,
                    voice_lang=eff_bind_lang,
                )
                active_l = brief.get("voice_lang") or "zh-CN"
                lang_label = VOICE_LANG_PROFILES.get(active_l, VOICE_LANG_PROFILES["zh-CN"])["label"]
                if matched_u and cmd_lang:
                    prefix_ack = (
                        f"Saved default destination {translate_parking_term_to_english(target_uname, target_uid)} and English voice. "
                        if active_l == "en-US"
                        else f"已为您绑定目的地{target_uname}并切换为{lang_label}。"
                    )
                elif matched_u:
                    prefix_ack = (
                        f"Saved default destination {translate_parking_term_to_english(target_uname, target_uid)}. "
                        if active_l == "en-US"
                        else f"已为您绑定目的地{target_uname}。"
                    )
                else:
                    ack_by_lang = {
                        "en-US": "Switched broadcast language to English. ",
                        "zh-HK": "已為您切換為粵語播報。",
                        "zh-SC": "巴适，已给您切成四川话播报！",
                        "zh-NE": "妥妥滴，已给您换成东北话播报！",
                    }
                    prefix_ack = ack_by_lang.get(active_l, f"已为您切换为{lang_label}。")
                ans = f"{prefix_ack}{brief['single_text']}"
                return {
                    **brief,
                    "engine": "fast_bind",
                    "question": clean_q,
                    "answer": ans,
                    "speech_text": self.format_repeated_speech(ans, repeat or 1, voice_lang=active_l),
                    "bound_unit_id": target_uid,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                }

        # Extract inline unit mention & natural-language spot tag mention (e.g. "新能源车位还有空吗", "有没有对外招租车位")
        inline_unit = self._match_unit_from_text(clean_q, units)
        effective_unit = str(inline_unit["unit_id"]) if inline_unit else unit_id

        builtin_tag_codes = {c["code"] for c in SPOT_TAG_CATALOG}
        custom_tags_in_db: List[str] = []
        for sp_db in self.ctx.db.list_spots():
            for t_db in sp_db.get("tags") or []:
                if t_db not in builtin_tag_codes and t_db not in custom_tags_in_db:
                    custom_tags_in_db.append(t_db)
        detected_tag = resolve_tag_filter_code(tag_filter) or (
            match_spot_tag_from_text(clean_q, custom_tags_in_db) or ""
        )

        if not vehicle_type and detected_tag == "ev_charging":
            vehicle_type = "ev"
        elif not vehicle_type and any(w in clean_q for w in ("小车", "微型", "MINI", "mini", "紧凑")):
            vehicle_type = "compact"
        elif not vehicle_type and any(w in clean_q for w in ("商务车", "SUV", "suv", "MPV", "大车")):
            vehicle_type = "suv"

        # 2) Fast-path: pure wake words ("", "回家", "车位", "找车位") -> <20ms deterministic brief
        if normalized_wake in WAKE_BRIEF_WORDS and not detected_tag:
            brief = self.build_voice_brief(
                unit_id=effective_unit,
                repeat=repeat,
                vehicle_type=vehicle_type,
                channel=channel,
                external_uid=external_uid,
                voice_lang=voice_lang,
            )
            return {
                **brief,
                "engine": "fast_brief",
                "question": clean_q,
                "answer": brief["single_text"],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        # Always compute deterministic baseline first (honoring `detected_tag` so even zero-LLM mode answers tag queries accurately!)
        brief = self.build_voice_brief(
            unit_id=effective_unit,
            repeat=repeat if repeat is not None else 1,
            vehicle_type=vehicle_type,
            channel=channel,
            external_uid=external_uid,
            voice_lang=voice_lang,
            tag_filter=detected_tag or None,
        )
        eff_lang = str(brief.get("voice_lang") or "zh-CN")

        ads_mgr = getattr(self.ctx, "ads", None)
        matched_ad = (
            ads_mgr.match_ad_for_question(
                clean_q,
                unit_id=str(brief.get("unit_id") or ""),
                vehicle_type=str(brief.get("vehicle_type") or "sedan"),
                total_effective_free=int(brief.get("total_effective_free") or 0),
            )
            if ads_mgr is not None
            else None
        )
        if matched_ad and ads_mgr is not None:
            ads_mgr.record_event(
                campaign_id=str(matched_ad["campaign_id"]),
                event_type="impression",
                platform="ios_siri" if channel == "siri" else channel,
                surface="18791_ai_ask",
                client_ip=client_ip,
                external_uid=external_uid or "ask",
                unit_id=str(brief.get("unit_id") or ""),
                vehicle_type=str(brief.get("vehicle_type") or "sedan"),
            )

        def _build_partner_tip(ad_item: Dict[str, Any]) -> str:
            offer = str(ad_item.get("offer_value") or ad_item.get("subtitle") or "").strip()
            dist = str(ad_item.get("distance_hint") or ad_item.get("nav_address") or "").strip()
            parts = [f"为您推荐周边合作商户「{ad_item['title']}」"]
            if offer:
                parts.append(offer)
            if dist:
                parts.append(dist)
            return "，".join(parts) + "。"

        # Handle general "特色/标签车位空余汇总" question deterministically when user asks about all tags
        asking_all_tags_summary = (
            not detected_tag
            and any(kw in clean_q for kw in ("标签车位", "特色车位", "带标签", "哪些标签", "分类空位"))
        )
        active_tag_rows = [
            ta for ta in (brief.get("tag_availability") or []) if int(ta.get("total_spots") or 0) > 0
        ]
        if asking_all_tags_summary:
            if active_tag_rows:
                seg_txts = [
                    f"{ta['label']}净余 {ta['effective_free']}/{ta['total_spots']} 位"
                    for ta in active_tag_rows
                ]
                sum_ans = f"当前小区特色标签车位空余情况：{'；'.join(seg_txts)}。全区总计净余 {brief['total_effective_free']} 位。"
            else:
                sum_ans = f"当前小区暂未标记特色标签车位，全区普通车位净余 {brief['total_effective_free']} 位。"
            rep_s = int(repeat) if repeat is not None else 1
            return {
                **brief,
                "engine": "tag_summary_fast",
                "question": clean_q,
                "answer": sum_ans,
                "speech_text": self.format_repeated_speech(sum_ans, rep_s, voice_lang=eff_lang),
                "matched_ad": matched_ad,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        if not vg_cfg.get("llm_qa_enabled", True):
            det_ans = brief["single_text"]
            if matched_ad and eff_lang != "en-US":
                det_ans = f"{det_ans} {_build_partner_tip(matched_ad)}"
            rep_det = int(repeat) if repeat is not None else 1
            return {
                **brief,
                "engine": "deterministic_only",
                "question": clean_q,
                "answer": det_ans,
                "speech_text": self.format_repeated_speech(det_ans, rep_det, voice_lang=eff_lang),
                "matched_ad": matched_ad,
            }

        # Zero-false-positive pre-shield only for unmistakable prompt-injection / code-generation abuse;
        # everyday conversational parking/route/EV/rental/tag/car-life questions always flow smoothly.
        q_low = clean_q.lower()
        has_parking_intent = bool(
            inline_unit
            or detected_tag
            or matched_ad
            or any(
                kw in clean_q
                for kw in (
                    "车位", "停车", "空位", "停哪", "号楼", "单元", "大门", "怎么走", "倒车",
                    "SUV", "小车", "电瓶", "挪车", "新能源", "充电", "电桩", "电车", "绿牌",
                    "招租", "出租", "包月", "月租", "共享", "宽体", "无障碍", "轮椅", "访客", "产权", "标签",
                    "洗车", "保养", "修车", "补胎", "车险", "加油", "生鲜", "便利店", "周边", "优惠",
                )
            )
            or any(kw in q_low for kw in ("park", "spot", "stall", "unit", "building", "gate", "ev", "charg", "rent", "wash"))
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
                f"I am the dedicated parking voice assistant for this community. {brief['single_text']}"
                if eff_lang == "en-US"
                else f"我是本小区专属车载寻位语音助手，仅提供实时车位与步行导引服务。{brief['single_text']}"
            )
            rep_guard = int(repeat) if repeat is not None else 1
            return {
                **brief,
                "engine": "domain_guardrail",
                "question": clean_q,
                "answer": polite_ans,
                "speech_text": self.format_repeated_speech(polite_ans, rep_guard, voice_lang=eff_lang),
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
        lang_persona_rule = {
            "zh-CN": "请全程使用自然亲切的标准普通话口语作答。",
            "zh-HK": "请全程使用地道香港/广东粤语口语用词作答（例如「歡迎返屋企、仲有、優先推介、行返去單元門口」），方便粤语语音引擎朗读。",
            "en-US": "Respond strictly in natural, concise American English suitable for Apple CarPlay TTS (< 50 words). Translate Chinese building/zone names into clear English (e.g. Building 8 Unit 1, Building 6 Left Parking Strip) so English TTS contains zero Chinese characters.",
            "zh-SC": "请全程使用亲切地道的四川/重庆方言口语作答（自然融入「欢迎回屋头、巴适、安逸、切哪边、走拢单元门」等日常川渝表达，但数字和楼栋名保持清晰易懂）。",
            "zh-NE": "请全程使用热情爽朗的东北方言口语作答（自然融入「妥妥滴、咱院里、那块儿、溜达回单元门、老省心了」等日常东北表达，但数字和楼栋名保持清晰易懂）。",
            "zh-SN": "请全程使用亲切自然的陕西关中方言口语作答。",
            "zh-TW": "請全程使用溫柔親切的繁體國語口語作答。",
        }.get(eff_lang, "请全程使用自然亲切的标准普通话口语作答。")
        tag_ctx_lines: List[str] = []
        for ta in active_tag_rows:
            z_parts: List[str] = []
            for tz in (ta.get("zones") or [])[:3]:
                sm = tz.get("sample_meta") or {}
                meta_bits = [
                    sm.get("charger_power", ""),
                    sm.get("rental_window", ""),
                    sm.get("rental_price", ""),
                    f"联系:{sm['rental_contact']}" if sm.get("rental_contact") else "",
                ]
                meta_str = f"({' / '.join(b for b in meta_bits if b)})" if any(meta_bits) else ""
                z_parts.append(
                    f"{tz['zone_name']}余{tz['effective_free']}/{tz['total_spots']}位(步{tz['walk_meters']}m){meta_str}"
                )
            tag_ctx_lines.append(
                f"- [{ta['label']}({ta['short_badge']})]：全区净余 {ta['effective_free']}/{ta['total_spots']} 位"
                + (f" -> {'；'.join(z_parts)}" if z_parts else "")
            )
        if not tag_ctx_lines:
            tag_ctx_lines.append("- 当前暂未单独标记新能源充电或对外招租等特色标签车位。")

        partner_ctx_block = ""
        if matched_ad:
            partner_ctx_block = (
                "\n- 关联社区合作车主服务（若车主询问相关服务或院内满位，可自然顺带推荐一句）："
                f"{matched_ad['title']}（{matched_ad.get('offer_value') or ''} {matched_ad.get('subtitle') or ''}，"
                f"{matched_ad.get('distance_hint') or matched_ad.get('nav_address') or ''}）"
            )

        sys_prompt = (
            f"你是「{comm_name}」车载免手语音寻位助手，正在为开车的业主提供自然亲切的实时停车引导。\n"
            "【回答铁律】\n"
            f"1. {lang_persona_rule}适合车载语音直接朗读（控制在 2~3 句话、90 字以内，不要使用 Markdown 星号、列表符号或代码块）。\n"
            "2. 严禁播报任何具体车位编号或字母数字代码（例如绝对不要说 10LH2-02、5BV7-01、02号车位、Spot 02 等），开车时听编号费劲且无意义；只需要清楚告诉车主：目前剩余几个车位（若询问新能源充电、对外招租、宽体、无障碍等标签车位，务必明确播报该标签车位共几个、当前净余几个、推荐哪个停车带、步行约多少米，以及公开的充电功率或招租时段/月租说明）。\n"
            "3. 严格依据下方实时车位数据作答，宁少勿多，绝不编造空车位。\n"
            "4. 若用户提问与本场所停车、车位、充电、招租、周边车主服务、楼栋步行路线无关，请礼貌用一句话说明你仅提供实时寻位服务，并顺带播报当前总剩余车位数。\n\n"
            f"【实时态势数据】\n"
            f"- 车主目标目的地：{brief['unit_name']}\n"
            + (
                f"- 车主当前指定查询标签：{brief['tag_label']}（净余 {brief['tag_effective_free']} / {brief['tag_total_spots']} 位）\n"
                if detected_tag
                else ""
            )
            + f"- 全区实时统计：共 {brief['total_spots']} 个监测车位，当前剩余可停 {brief['total_effective_free']} 个\n"
            + f"- 系统推荐参考：{brief['single_text']}\n"
            + "- 带标签特色车位实时空余明细：\n"
            + "\n".join(tag_ctx_lines)
            + "\n- 可选停车带实时余位：\n"
            + "\n".join(zone_lines)
            + partner_ctx_block
        )

        llm_ans, llm_err = self._call_custom_llm_text(
            cfg.get("vlm", {}),
            system_prompt=sys_prompt,
            user_question=clean_q,
            max_tokens=220,
        )
        if not llm_ans:
            fb_ans = brief["single_text"]
            if matched_ad and eff_lang != "en-US":
                fb_ans = f"{fb_ans} {_build_partner_tip(matched_ad)}"
            rep_fb = int(repeat) if repeat is not None else 1
            return {
                **brief,
                "engine": "fallback_deterministic",
                "fallback_reason": llm_err or "llm_unavailable",
                "question": clean_q,
                "answer": fb_ans,
                "speech_text": self.format_repeated_speech(fb_ans, rep_fb, voice_lang=eff_lang),
                "matched_ad": matched_ad,
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
            "speech_text": self.format_repeated_speech(clean_ans, rep_out, voice_lang=eff_lang),
            "matched_ad": matched_ad,
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
        voice_lang: str = "zh-CN",
        dialect_ready: bool = False,
        ask_lang_on_first_run: bool = True,
    ) -> bytes:
        """
        Generate an Apple Shortcuts `.shortcut` workflow definition (binary plist):
        - Avoids `WFWorkflowImportQuestions` during normal Chrome/Safari import (which triggers an iOS 26
          Chrome `openInPlace` setup-sheet hang), supporting:
          * `fixed` (default): directly uses the unit & language chosen on `:18791`, 1-tap install & 0-prompt run!
          * `setup_once`: 1-tap install (no import wizard!). If `ask_lang_on_first_run=True`, prompts ONCE on 1st
            run for Unit (1/2) + Broadcast Language (2/2); if user already picked a specific language on `:18791`,
            prompts ONCE only for Unit and binds their chosen language immediately!
          * `menu`: prompts unit list on every run.
        - Uses `is.workflow.actions.text.split` (`WFTextComponentsAction`, verified against `WorkflowKit`),
          native `yue-HK` + `Sinji` for Cantonese, `Samantha` for English, and Cloud Neural `晓北` (`tts.mp3` +
          `playsound`) for Northeastern `zh-NE`!
        """
        clean_base = str(base_url or "").strip().rstrip("/")
        safe_name = self.sanitize_shortcut_name(shortcut_name, "小区车位")
        rep_val = 1 if int(repeat or 2) <= 1 else 2
        vtype = self._normalize_vehicle_type(vehicle_type)
        act_mode = "ask" if str(mode or "").strip().lower() == "ask" else "brief"
        u_mode = self._normalize_unit_mode(unit_mode)
        map_mode = self._normalize_show_map_mode(show_map)
        is_silent_audio = act_mode == "brief" and map_mode == "silent"
        norm_vlang = coerce_shortcut_safe_voice_lang(
            voice_lang, dialect_pack_ready=dialect_ready, allow_cloud_audio=is_silent_audio
        )
        vlang_label = VOICE_LANG_PROFILES.get(norm_vlang, VOICE_LANG_PROFILES["zh-CN"])["label"]
        resolved_uid, unit_display, all_units = self._resolve_unit_display_and_list(unit_id)

        place_clean = str(arrival_place or "").strip()
        radius_clean = max(50, min(2000, int(arrival_radius_m or 200)))

        vtype_label = {"sedan": "标准轿车", "suv": "大型SUV/MPV", "compact": "紧凑小车"}.get(vtype, "标准轿车")
        mode_desc = "Siri 语音提问自有大模型" if act_mode == "ask" else f"极速车位播报（{rep_val} 遍 · {vtype_label}）"
        if u_mode == "menu":
            dest_desc = f"每次运行时从单元门列表点选（默认备选 {resolved_uid}）｜ 语言：{vlang_label}"
        elif u_mode in ("setup_once", "import_question"):
            if ask_lang_on_first_run:
                dest_desc = f"首次运行时弹窗选一次单元门与播报语言并永久记住（默认 {unit_display}）"
            else:
                dest_desc = f"首次运行时弹窗选一次单元门（已锁定语言：{vlang_label}）"
        else:
            dest_desc = f"{unit_display} ｜ 语言：{vlang_label}"
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

            unit_prompt_text = (
                "首次设置 (1/2)：请选择您的默认回家单元门（之后永久免选）"
                if ask_lang_on_first_run
                else f"首次设置：请选择您的默认回家单元门（已选语言：{vlang_label}，之后永久免选）"
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                    "WFWorkflowActionParameters": {
                        "GroupingIdentifier": mn_grp,
                        "WFControlFlowMode": 0,
                        "WFMenuPrompt": unit_prompt_text,
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
            if ask_lang_on_first_run:
                # Step 2/2: Live geo-aware language picker (`/api/voice/languages.txt` -> `text.split`!)
                lg_dl_uuid = str(uuid.uuid4()).upper()
                lg_sp_uuid = str(uuid.uuid4()).upper()
                lg_ch_uuid = str(uuid.uuid4()).upper()
                lg_enc_uuid = str(uuid.uuid4()).upper()
                cloud_q = "audio=cloud" if is_silent_audio else ""
                extra_q = tok_qs.lstrip("&")
                langs_qs_parts = [p for p in (cloud_q, extra_q) if p]
                langs_url_qs = ("?" + "&".join(langs_qs_parts)) if langs_qs_parts else ""
                actions.extend(
                    [
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "UUID": lg_dl_uuid,
                                "WFHTTPMethod": "GET",
                                "WFURL": f"{clean_base}/api/voice/languages.txt{langs_url_qs}",
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.text.split",
                            "WFWorkflowActionParameters": {
                                "UUID": lg_sp_uuid,
                                "WFTextSeparator": "New Lines",
                                "text": {
                                    "Value": {
                                        "OutputName": "URL的内容",
                                        "OutputUUID": lg_dl_uuid,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
                            "WFWorkflowActionParameters": {
                                "UUID": lg_ch_uuid,
                                "WFChooseFromListActionPrompt": "首次设置 (2/2)：请选择播报语言与方言（之后永久免选）",
                                "WFInput": {
                                    "Value": {
                                        "OutputName": "拆分文本",
                                        "OutputUUID": lg_sp_uuid,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                            "WFWorkflowActionParameters": {
                                "UUID": lg_enc_uuid,
                                "WFEncodeMode": "Encode",
                                "WFInput": {
                                    "Value": {
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputName": "选取的项目",
                                                "OutputUUID": lg_ch_uuid,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": "\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            },
                        },
                    ]
                )
                qb_prefix = f"{clean_base}/api/voice/quick-bind?uid={quote(bind_id)}&unit="
                qb_mid = "&lang="
                qb_u_pos = len(qb_prefix)
                qb_l_pos = qb_u_pos + 1 + len(qb_mid)
                qb_str = f"{qb_prefix}\ufffc{qb_mid}\ufffc{tok_qs}"
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "WFHTTPMethod": "GET",
                            "WFURL": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{" + str(qb_u_pos) + ", 1}": {
                                            "OutputName": "URL编码后的文本",
                                            "OutputUUID": mn_enc_uuid,
                                            "Type": "ActionOutput",
                                        },
                                        "{" + str(qb_l_pos) + ", 1}": {
                                            "OutputName": "URL编码后的文本",
                                            "OutputUUID": lg_enc_uuid,
                                            "Type": "ActionOutput",
                                        },
                                    },
                                    "string": qb_str,
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    }
                )
            else:
                qb_prefix = f"{clean_base}/api/voice/quick-bind?uid={quote(bind_id)}&lang={quote(norm_vlang)}&unit="
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

        if not use_server_bound_uid and norm_vlang != "zh-CN":
            d_flag = "&dialect_ready=1" if dialect_ready else ("&audio=cloud" if is_silent_audio else "")
            tok_qs = f"&lang={quote(norm_vlang)}{d_flag}{tok_qs}"

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

        # If `setup_once` (`use_server_bound_uid`), resolve bound language slot before audio starts;
        # otherwise use the exact Apple TTS language chosen on `:18791` directly with zero extra network calls!
        lang_cnt_uuid: Optional[str] = None
        if use_server_bound_uid:
            my_lang_dl_uuid = str(uuid.uuid4()).upper()
            lang_cnt_uuid = str(uuid.uuid4()).upper()
            ml_parts = [p for p in (("audio=cloud" if is_silent_audio else ""), tok_qs.lstrip("&")) if p]
            ml_qs = ("?" + "&".join(ml_parts)) if ml_parts else ""
            actions.extend(
                [
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "UUID": my_lang_dl_uuid,
                            "WFHTTPMethod": "GET",
                            "WFURL": f"{clean_base}/api/voice/my-lang{ml_qs}",
                        },
                    },
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.count",
                        "WFWorkflowActionParameters": {
                            "UUID": lang_cnt_uuid,
                            "WFCountType": "Characters",
                            "Input": {
                                "Value": {
                                    "OutputName": "URL的内容",
                                    "OutputUUID": my_lang_dl_uuid,
                                    "Type": "ActionOutput",
                                },
                                "WFSerializationType": "WFTextTokenAttachment",
                            },
                        },
                    },
                ]
            )

        # Start speaking immediately (`WFSpeakTextWait=False`), then ~5ms later pop up `文字 + 平面图同屏`
        # (`previewdocument` / `showresult`) in ONE unified view — NEVER `notification` first + map later!
        if lang_cnt_uuid:
            tts_mp3_bound_url: Optional[Any] = None
            if is_silent_audio:
                tts_mp3_bound_url = _wrap_dl_param(
                    _make_token_url(
                        "/api/voice/tts.mp3?unit=",
                        f"&repeat=1&vehicle_type={quote(vtype)}{tok_qs}",
                    )
                )
            _append_multilang_speaktext_actions(
                actions,
                speech_out_uuid,
                lang_cnt_uuid,
                tts_mp3_url=tts_mp3_bound_url,
                allow_blocking_playsound=is_silent_audio,
            )
        elif uses_cloud_neural_shortcut_audio(norm_vlang, dialect_pack_ready=dialect_ready, map_mode=map_mode):
            # Pure-audio `silent` mode ONLY (no visual popup to block!):
            mp3_direct_uuid = str(uuid.uuid4()).upper()
            mp3_url_param = _wrap_dl_param(
                _make_token_url(
                    "/api/voice/tts.mp3?unit=",
                    f"&repeat=1&vehicle_type={quote(vtype)}{tok_qs}",
                )
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": mp3_direct_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": mp3_url_param,
                    },
                }
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.playsound",
                    "WFWorkflowActionParameters": {
                        "WFInput": {
                            "Value": {
                                "OutputName": "URL的内容",
                                "OutputUUID": mp3_direct_uuid,
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        }
                    },
                }
            )
        else:
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                    "WFWorkflowActionParameters": _build_apple_speaktext_params(
                        norm_vlang,
                        {
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
                    ),
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
            for attempt in range(4):
                out_p.unlink(missing_ok=True)
                try:
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
                except subprocess.TimeoutExpired:
                    logger.warning("macOS shortcuts sign timeout on attempt %d, retrying...", attempt + 1)
                if attempt < 3:
                    time.sleep(0.8 * (attempt + 1))
            logger.warning("macOS shortcuts sign failed after retries")
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

    def build_ubuntu_pack_variant_plist(
        self,
        base_url: str,
        variant_kind: str = "dynamic",
        show_map: str = "text",
        mode: str = "brief",
        shortcut_name: str = "小区车位",
        fixed_unit_id: str = "",
        locked_lang: str = "",
    ) -> bytes:
        """
        Build a universal domain-bound Apple Shortcut plist designed for Ubuntu / Linux deployment:
        - `variant_kind == "dynamic"`: Zero `WFWorkflowImportQuestions` (1-tap install in Chrome & Safari!).
          Uses `/api/voice/check-bind` (`count < 1`): if already pre-bound via `:18791` webpage or prior run,
          speaks & pops up `text` / `map` / `both` immediately in the bound language; otherwise runs a 2-step
          first-run wizard (`/api/voice/units.txt` + `/api/voice/languages.txt` -> `/api/voice/quick-bind`) ONCE!
        - `variant_kind == "menu"`: Dynamically fetches live `/api/voice/units.txt` (`choosefromlist`) on every run
          so newly added buildings/units on Ubuntu work immediately without re-signing!
        - `variant_kind == "fixed"`: Hardcodes `unit=<fixed_unit_id>` (CGNAT-immune unit!) while dispatching
          Apple TTS via `/api/voice/my-lang` so Mandarin / Cantonese / English / Sichuanese / Northeastern all work!
        """
        clean_base = str(base_url or "").strip().rstrip("/")
        safe_name = self.sanitize_shortcut_name(shortcut_name, "小区车位")
        act_mode = "ask" if str(mode or "").strip().lower() == "ask" else "brief"
        map_mode = self._normalize_show_map_mode(show_map)
        popup_desc = {
            "text": "播报同时弹出「精炼寻位文字」",
            "map": "播报同时弹出「纯净动态平面图」",
            "both": "播报同时弹出「精炼文字 + 动态平面图」",
            "silent": "纯语音播报（不弹画面）",
        }.get(map_mode, "播报同时弹出「精炼寻位文字」")

        actions: List[Dict[str, Any]] = [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
                "WFWorkflowActionParameters": {
                    "WFCommentActionText": (
                        f"【{safe_name}】免手语音寻位（Apple 官方签名 · Ubuntu 多语种云端直连版）\n"
                        f"网关：{clean_base} ｜ 显示：{popup_desc}"
                    )
                },
            }
        ]

        if act_mode == "ask":
            ask_q_uuid = str(uuid.uuid4()).upper()
            enc_q_uuid = str(uuid.uuid4()).upper()
            ans_uuid = str(uuid.uuid4()).upper()
            my_lang_uuid = str(uuid.uuid4()).upper()
            lang_cnt_uuid = str(uuid.uuid4()).upper()
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
            ask_prefix = f"{clean_base}/api/voice/ask?format=text&q="
            actions.extend(
                [
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "UUID": ans_uuid,
                            "WFHTTPMethod": "GET",
                            "WFURL": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{" + str(len(ask_prefix)) + ", 1}": {
                                            "OutputName": "URL编码后的文本",
                                            "OutputUUID": enc_q_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": f"{ask_prefix}\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    },
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "UUID": my_lang_uuid,
                            "WFHTTPMethod": "GET",
                            "WFURL": f"{clean_base}/api/voice/my-lang",
                        },
                    },
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.count",
                        "WFWorkflowActionParameters": {
                            "UUID": lang_cnt_uuid,
                            "WFCountType": "Characters",
                            "Input": {
                                "Value": {
                                    "OutputName": "URL的内容",
                                    "OutputUUID": my_lang_uuid,
                                    "Type": "ActionOutput",
                                },
                                "WFSerializationType": "WFTextTokenAttachment",
                            },
                        },
                    },
                ]
            )
            _append_multilang_speaktext_actions(
                actions,
                ans_uuid,
                lang_cnt_uuid,
                allow_blocking_playsound=False,
            )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                    "WFWorkflowActionParameters": {
                        "Text": {
                            "Value": {
                                "attachmentsByRange": {
                                    "{0, 1}": {
                                        "OutputName": "URL的内容",
                                        "OutputUUID": ans_uuid,
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
            enc_menu_unit_uuid: Optional[str] = None
            fixed_u_clean = quote(str(fixed_unit_id or "").strip())
            if variant_kind == "menu":
                u_dl = str(uuid.uuid4()).upper()
                u_sp = str(uuid.uuid4()).upper()
                u_ch = str(uuid.uuid4()).upper()
                enc_menu_unit_uuid = str(uuid.uuid4()).upper()
                actions.extend(
                    [
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "UUID": u_dl,
                                "WFHTTPMethod": "GET",
                                "WFURL": f"{clean_base}/api/voice/units.txt",
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.text.split",
                            "WFWorkflowActionParameters": {
                                "UUID": u_sp,
                                "WFTextSeparator": "New Lines",
                                "text": {
                                    "Value": {
                                        "OutputName": "URL的内容",
                                        "OutputUUID": u_dl,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
                            "WFWorkflowActionParameters": {
                                "UUID": u_ch,
                                "WFChooseFromListActionPrompt": "请选择回家目的单元门",
                                "WFInput": {
                                    "Value": {
                                        "OutputName": "拆分文本",
                                        "OutputUUID": u_sp,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                            "WFWorkflowActionParameters": {
                                "UUID": enc_menu_unit_uuid,
                                "WFEncodeMode": "Encode",
                                "WFInput": {
                                    "Value": {
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputName": "选取的项目",
                                                "OutputUUID": u_ch,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": "\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            },
                        },
                    ]
                )
            elif variant_kind == "fixed":
                pass
            else:
                chk_uuid = str(uuid.uuid4()).upper()
                cnt_uuid = str(uuid.uuid4()).upper()
                if_grp = str(uuid.uuid4()).upper()
                u_dl = str(uuid.uuid4()).upper()
                u_sp = str(uuid.uuid4()).upper()
                u_ch = str(uuid.uuid4()).upper()
                u_en = str(uuid.uuid4()).upper()
                lg_dl = str(uuid.uuid4()).upper()
                lg_sp = str(uuid.uuid4()).upper()
                lg_ch = str(uuid.uuid4()).upper()
                lg_en = str(uuid.uuid4()).upper()
                qb_prefix = f"{clean_base}/api/voice/quick-bind?unit="
                qb_mid = "&lang="
                qb_u_pos = len(qb_prefix)
                qb_l_pos = qb_u_pos + 1 + len(qb_mid)
                actions.extend(
                    [
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "UUID": chk_uuid,
                                "WFHTTPMethod": "GET",
                                "WFURL": f"{clean_base}/api/voice/check-bind",
                            },
                        },
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
                        },
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
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "UUID": u_dl,
                                "WFHTTPMethod": "GET",
                                "WFURL": f"{clean_base}/api/voice/units.txt",
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.text.split",
                            "WFWorkflowActionParameters": {
                                "UUID": u_sp,
                                "WFTextSeparator": "New Lines",
                                "text": {
                                    "Value": {
                                        "OutputName": "URL的内容",
                                        "OutputUUID": u_dl,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
                            "WFWorkflowActionParameters": {
                                "UUID": u_ch,
                                "WFChooseFromListActionPrompt": (
                                    f"首次设置：请点选默认回家单元门（已锁定 {VOICE_LANG_PROFILES.get(locked_lang, {}).get('short_label', locked_lang)}）"
                                    if locked_lang
                                    else "首次设置 (1/2)：请点选默认回家单元门（之后永久免选）"
                                ),
                                "WFInput": {
                                    "Value": {
                                        "OutputName": "拆分文本",
                                        "OutputUUID": u_sp,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                            "WFWorkflowActionParameters": {
                                "UUID": u_en,
                                "WFEncodeMode": "Encode",
                                "WFInput": {
                                    "Value": {
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputName": "选取的项目",
                                                "OutputUUID": u_ch,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": "\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            },
                        },
                    ]
                )
                if locked_lang:
                    qb_locked_p = f"{clean_base}/api/voice/quick-bind?lang={quote(locked_lang)}&unit="
                    actions.append(
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "WFHTTPMethod": "GET",
                                "WFURL": {
                                    "Value": {
                                        "attachmentsByRange": {
                                            "{" + str(len(qb_locked_p)) + ", 1}": {
                                                "OutputName": "URL编码后的文本",
                                                "OutputUUID": u_en,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": f"{qb_locked_p}\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            },
                        }
                    )
                else:
                    actions.extend(
                        [
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                                "WFWorkflowActionParameters": {
                                    "UUID": lg_dl,
                                    "WFHTTPMethod": "GET",
                                    "WFURL": (
                                        f"{clean_base}/api/voice/languages.txt?audio=cloud"
                                        if map_mode == "silent"
                                        else f"{clean_base}/api/voice/languages.txt"
                                    ),
                                },
                            },
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.text.split",
                                "WFWorkflowActionParameters": {
                                    "UUID": lg_sp,
                                    "WFTextSeparator": "New Lines",
                                    "text": {
                                        "Value": {
                                            "OutputName": "URL的内容",
                                            "OutputUUID": lg_dl,
                                            "Type": "ActionOutput",
                                        },
                                        "WFSerializationType": "WFTextTokenAttachment",
                                    },
                                },
                            },
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
                                "WFWorkflowActionParameters": {
                                    "UUID": lg_ch,
                                    "WFChooseFromListActionPrompt": "首次设置 (2/2)：请选择播报语言与方言（之后永久免选）",
                                    "WFInput": {
                                        "Value": {
                                            "OutputName": "拆分文本",
                                            "OutputUUID": lg_sp,
                                            "Type": "ActionOutput",
                                        },
                                        "WFSerializationType": "WFTextTokenAttachment",
                                    },
                                },
                            },
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
                                "WFWorkflowActionParameters": {
                                    "UUID": lg_en,
                                    "WFEncodeMode": "Encode",
                                    "WFInput": {
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputName": "选取的项目",
                                                "OutputUUID": lg_ch,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": "\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            },
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                                "WFWorkflowActionParameters": {
                                    "WFHTTPMethod": "GET",
                                    "WFURL": {
                                        "Value": {
                                            "attachmentsByRange": {
                                                "{" + str(qb_u_pos) + ", 1}": {
                                                    "OutputName": "URL编码后的文本",
                                                    "OutputUUID": u_en,
                                                    "Type": "ActionOutput",
                                                },
                                                "{" + str(qb_l_pos) + ", 1}": {
                                                    "OutputName": "URL编码后的文本",
                                                    "OutputUUID": lg_en,
                                                    "Type": "ActionOutput",
                                                },
                                            },
                                            "string": f"{qb_prefix}\ufffc{qb_mid}\ufffc",
                                        },
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                },
                            },
                        ]
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

            def _pack_url(path_prefix: str, suffix: str = "") -> Any:
                lang_q = f"&lang={quote(locked_lang)}" if locked_lang else ""
                if enc_menu_unit_uuid:
                    full_p = f"{clean_base}{path_prefix}"
                    return {
                        "Value": {
                            "attachmentsByRange": {
                                "{" + str(len(full_p)) + ", 1}": {
                                    "OutputName": "URL编码后的文本",
                                    "OutputUUID": enc_menu_unit_uuid,
                                    "Type": "ActionOutput",
                                }
                            },
                            "string": f"{full_p}\ufffc{suffix}{lang_q}",
                        },
                        "WFSerializationType": "WFTextTokenString",
                    }
                if variant_kind == "fixed" and fixed_u_clean:
                    return f"{clean_base}{path_prefix}{fixed_u_clean}{suffix}{lang_q}"
                return f"{clean_base}{path_prefix}{suffix}{lang_q}"

            speech_uuid = str(uuid.uuid4()).upper()
            my_lang_uuid: Optional[str] = None
            lang_cnt_uuid: Optional[str] = None
            is_pack_silent = map_mode == "silent"
            cloud_sfx = "&audio=cloud" if is_pack_silent else ""
            if not locked_lang:
                my_lang_uuid = str(uuid.uuid4()).upper()
                lang_cnt_uuid = str(uuid.uuid4()).upper()
                actions.extend(
                    [
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                            "WFWorkflowActionParameters": {
                                "UUID": my_lang_uuid,
                                "WFHTTPMethod": "GET",
                                "WFURL": (
                                    f"{clean_base}/api/voice/my-lang?audio=cloud"
                                    if is_pack_silent
                                    else f"{clean_base}/api/voice/my-lang"
                                ),
                            },
                        },
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.count",
                            "WFWorkflowActionParameters": {
                                "UUID": lang_cnt_uuid,
                                "WFCountType": "Characters",
                                "Input": {
                                    "Value": {
                                        "OutputName": "URL的内容",
                                        "OutputUUID": my_lang_uuid,
                                        "Type": "ActionOutput",
                                    },
                                    "WFSerializationType": "WFTextTokenAttachment",
                                },
                            },
                        },
                    ]
                )
            actions.append(
                {
                    "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                    "WFWorkflowActionParameters": {
                        "UUID": speech_uuid,
                        "WFHTTPMethod": "GET",
                        "WFURL": _pack_url("/api/voice/brief?unit=", f"&repeat=2&format=speech{cloud_sfx}"),
                    },
                }
            )

            card_uuid: Optional[str] = None
            hud_uuid: Optional[str] = None
            if map_mode == "text":
                card_uuid = str(uuid.uuid4()).upper()
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "UUID": card_uuid,
                            "WFHTTPMethod": "GET",
                            "WFURL": _pack_url("/api/voice/brief?unit=", "&format=card"),
                        },
                    }
                )
            elif map_mode in ("map", "both"):
                hud_uuid = str(uuid.uuid4()).upper()
                v_q = "map" if map_mode == "map" else "both"
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                        "WFWorkflowActionParameters": {
                            "UUID": hud_uuid,
                            "WFHTTPMethod": "GET",
                            "WFURL": _pack_url(
                                "/api/voice/hud/%E5%AE%9E%E6%97%B6%E6%80%81%E5%8A%BF.html?unit=",
                                f"&view={v_q}",
                            ),
                        },
                    }
                )

            if locked_lang:
                if uses_cloud_neural_shortcut_audio(locked_lang, dialect_pack_ready=False, map_mode=map_mode):
                    mp3_pack_uuid = str(uuid.uuid4()).upper()
                    actions.extend(
                        [
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                                "WFWorkflowActionParameters": {
                                    "UUID": mp3_pack_uuid,
                                    "WFHTTPMethod": "GET",
                                    "WFURL": _pack_url("/api/voice/tts.mp3?unit=", "&repeat=1"),
                                },
                            },
                            {
                                "WFWorkflowActionIdentifier": "is.workflow.actions.playsound",
                                "WFWorkflowActionParameters": {
                                    "WFInput": {
                                        "Value": {
                                            "OutputName": "URL的内容",
                                            "OutputUUID": mp3_pack_uuid,
                                            "Type": "ActionOutput",
                                        },
                                        "WFSerializationType": "WFTextTokenAttachment",
                                    }
                                },
                            },
                        ]
                    )
                else:
                    actions.append(
                        {
                            "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                            "WFWorkflowActionParameters": _build_apple_speaktext_params(
                                locked_lang,
                                {
                                    "Value": {
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputName": "URL的内容",
                                                "OutputUUID": speech_uuid,
                                                "Type": "ActionOutput",
                                            }
                                        },
                                        "string": "\ufffc",
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                            ),
                        }
                    )
            elif lang_cnt_uuid:
                _append_multilang_speaktext_actions(
                    actions,
                    speech_uuid,
                    lang_cnt_uuid,
                    tts_mp3_url=(_pack_url("/api/voice/tts.mp3?unit=", "&repeat=1") if is_pack_silent else None),
                    allow_blocking_playsound=is_pack_silent,
                )

            if map_mode == "text" and card_uuid:
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                        "WFWorkflowActionParameters": {
                            "Text": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "URL的内容",
                                            "OutputUUID": card_uuid,
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
            elif map_mode in ("map", "both") and hud_uuid:
                actions.append(
                    {
                        "WFWorkflowActionIdentifier": "is.workflow.actions.previewdocument",
                        "WFWorkflowActionParameters": {
                            "WFInput": {
                                "Value": {
                                    "OutputName": "URL的内容",
                                    "OutputUUID": hud_uuid,
                                    "Type": "ActionOutput",
                                },
                                "WFSerializationType": "WFTextTokenAttachment",
                            }
                        },
                    }
                )

        workflow = {
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
        return plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY)

    def pre_sign_domain_pack(
        self,
        base_url: str,
        include_fixed_units: bool = True,
        target_root: Optional[Path] = None,
        force_resign: bool = True,
    ) -> Dict[str, Any]:
        """
        Pre-sign a complete multi-mode & multi-language Ubuntu-Ready `.shortcut` pack (`text`,
        `map`, `both`, `silent`, `ask` + `menu` + optional per-unit `fixed` presets) on macOS
        so Ubuntu 24.04 serves Apple-signed `AEA1` shortcuts for EVERY popup & dialect option.
        """
        clean_base = str(base_url or "").strip().rstrip("/")
        if not clean_base:
            return {"ok": False, "error": "请指定公网域名或服务器地址，例如 https://voice.listingworker.com"}
        if not _can_macos_sign_shortcuts():
            return {"ok": False, "error": "请在 macOS 上执行一次 sign-pack 生成预签名包"}

        slug = _pack_slug_for_base_url(clean_base)
        out_dir = (target_root or PRESIGNED_BUNDLES_DIR) / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        jobs: List[Tuple[str, bytes]] = []
        # 1) Dynamic Smart-Once variants (`text`, `map`, `both`, `silent`, `ask`) — 2-step Unit + Language picker!
        for mm in ("text", "map", "both", "silent"):
            jobs.append(
                (
                    f"dynamic_{mm}.shortcut",
                    self.build_ubuntu_pack_variant_plist(clean_base, variant_kind="dynamic", show_map=mm, mode="brief"),
                )
            )
        jobs.append(
            (
                "dynamic_ask.shortcut",
                self.build_ubuntu_pack_variant_plist(clean_base, variant_kind="dynamic", show_map="text", mode="ask"),
            )
        )
        # 2) Live-Server Menu variants (`text`, `map`, `both`, `silent`) + CGNAT-immune `zh-HK` / `en-US` / `zh-NE` locked packs!
        for mm in ("text", "map", "both", "silent"):
            jobs.append(
                (
                    f"menu_{mm}.shortcut",
                    self.build_ubuntu_pack_variant_plist(clean_base, variant_kind="menu", show_map=mm, mode="brief"),
                )
            )
            for lcode in ("zh-HK", "en-US", "zh-NE"):
                jobs.append(
                    (
                        f"dynamic_{mm}_{lcode}.shortcut",
                        self.build_ubuntu_pack_variant_plist(
                            clean_base, variant_kind="dynamic", show_map=mm, mode="brief", locked_lang=lcode
                        ),
                    )
                )
                jobs.append(
                    (
                        f"menu_{mm}_{lcode}.shortcut",
                        self.build_ubuntu_pack_variant_plist(
                            clean_base, variant_kind="menu", show_map=mm, mode="brief", locked_lang=lcode
                        ),
                    )
                )
        # 3) Dedicated CGNAT-immune per-unit fixed presets (`text`, `map`, `both`) with multi-language `my-lang` dispatch!
        unit_ids_signed: List[str] = []
        if include_fixed_units and hasattr(self.ctx, "db") and self.ctx.db is not None:
            for u in (self.ctx.db.list_units() or [])[:16]:
                uid = str(u.get("unit_id") or "").strip()
                if not uid:
                    continue
                unit_ids_signed.append(uid)
                safe_u = re.sub(r"[^a-zA-Z0-9_-]+", "_", uid)
                for mm in ("text", "map", "both"):
                    jobs.append(
                        (
                            f"fixed_{mm}_{safe_u}.shortcut",
                            self.build_ubuntu_pack_variant_plist(
                                base_url=clean_base,
                                variant_kind="fixed",
                                show_map=mm,
                                mode="brief",
                                fixed_unit_id=uid,
                            ),
                        )
                    )

        signed_files: Dict[str, int] = {}
        with self._sign_lock:
            for fname, raw_plist in jobs:
                dest = out_dir / fname
                if not force_resign and dest.is_file() and dest.read_bytes()[:4] == b"AEA1":
                    signed_files[fname] = dest.stat().st_size
                    continue
                signed_b = self._sign_plist_via_macos_cli(raw_plist)
                if not signed_b:
                    return {"ok": False, "error": f"签名 {fname} 失败，请重试"}
                dest.write_bytes(signed_b)
                signed_files[fname] = len(signed_b)

        manifest = {
            "base_url": clean_base,
            "slug": slug,
            "pack_version": "2.0-multilang",
            "modes": ["text", "map", "both", "silent", "ask"],
            "unit_modes": ["fixed", "setup_once", "menu"],
            "voice_languages": list(VOICE_LANG_PROFILES.keys()),
            "fixed_units": unit_ids_signed,
            "files": signed_files,
            "generated_at": datetime.datetime.now().isoformat(),
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "ok": True,
            "slug": slug,
            "dir": str(out_dir),
            "base_url": clean_base,
            "count": len(signed_files),
            "fixed_units": unit_ids_signed,
        }

    @staticmethod
    def match_presigned_pack_shortcut(
        effective_base: str,
        act_mode: str,
        map_mode: str,
        u_mode: str,
        resolved_uid: str,
        voice_lang: str = "zh-CN",
        ask_lang_on_first_run: bool = True,
    ) -> Optional[Tuple[bytes, str]]:
        """
        Locate the exact Apple-signed (`AEA1`) `.shortcut` file for `(effective_base, act_mode, map_mode, u_mode, resolved_uid, voice_lang)`
        when running on Ubuntu / Linux without `/usr/bin/shortcuts`.
        """
        slug = _pack_slug_for_base_url(effective_base)
        cand_dirs: List[Path] = []
        if slug:
            for root_d in (DATA_PRESIGNED_DIR, PRESIGNED_BUNDLES_DIR):
                d = root_d / slug
                if d.is_dir():
                    cand_dirs.append(d)

        # If accessed via SSH tunnel (127.0.0.1) on Ubuntu before PUBLIC_VOICE_URL is set, fallback to available pack
        if not cand_dirs:
            for root_d in (DATA_PRESIGNED_DIR, PRESIGNED_BUNDLES_DIR):
                if root_d.is_dir():
                    for sub in sorted(root_d.iterdir()):
                        if sub.is_dir() and (sub / "manifest.json").is_file():
                            cand_dirs.append(sub)

        if not cand_dirs:
            return None

        norm_map = map_mode if map_mode in ("text", "map", "both", "silent") else "both"
        safe_u = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(resolved_uid or "").strip())
        safe_l = coerce_shortcut_safe_voice_lang(
            voice_lang, dialect_pack_ready=False, allow_cloud_audio=(norm_map == "silent")
        )

        candidates_rel: List[str] = []
        if act_mode == "ask":
            candidates_rel.append("dynamic_ask.shortcut")
        else:
            if u_mode == "menu":
                if safe_l in ("zh-HK", "en-US", "zh-NE"):
                    candidates_rel.append(f"menu_{norm_map}_{safe_l}.shortcut")
                candidates_rel.append(f"menu_{norm_map}.shortcut")
            elif u_mode == "fixed":
                if safe_l in ("zh-HK", "en-US", "zh-NE"):
                    # Language-locked variant guarantees `yue-HK` / `en-US` / `zh-NE` voice even across 5G IP changes!
                    candidates_rel.append(f"dynamic_{norm_map}_{safe_l}.shortcut")
                if safe_u:
                    candidates_rel.append(f"fixed_{norm_map}_{safe_u}.shortcut")
                candidates_rel.append(f"dynamic_{norm_map}.shortcut")
            else:
                if not ask_lang_on_first_run and safe_l in ("zh-HK", "en-US", "zh-NE"):
                    candidates_rel.append(f"dynamic_{norm_map}_{safe_l}.shortcut")
                candidates_rel.append(f"dynamic_{norm_map}.shortcut")
            candidates_rel.append(f"menu_{norm_map}.shortcut")
            candidates_rel.append("dynamic_text.shortcut")

        for pack_dir in cand_dirs:
            for rel_name in candidates_rel:
                fp = pack_dir / rel_name
                if fp.is_file():
                    try:
                        b = fp.read_bytes()
                        if b[:4] == b"AEA1":
                            return b, f"presigned_pack:{pack_dir.name}/{rel_name}"
                    except Exception:
                        continue
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
        voice_lang: str = "zh-CN",
        dialect_ready: bool = False,
        ask_lang_on_first_run: bool = True,
    ) -> Tuple[bytes, bool, str]:
        """
        Build and return `(shortcut_bytes, is_apple_signed, delivery_source)`.
        1. Checks memory LRU + `data/shortcuts/signed_<hash>.shortcut` (`AEA1`).
        2. On macOS, invokes `/usr/bin/shortcuts sign --mode anyone` automatically and
           caches the signed `AEA1` archive.
        3. On Linux/Docker/Windows, falls back to `app/presigned_shortcuts` / `universal-signed.shortcut`.
        """
        effective_base = self._prefer_lan_base_url(base_url)
        rep_val = 1 if int(repeat or 2) <= 1 else 2
        vtype = self._normalize_vehicle_type(vehicle_type)
        safe_name = self.sanitize_shortcut_name(shortcut_name, "小区车位")
        act_mode = "ask" if str(mode or "").strip().lower() == "ask" else "brief"
        u_mode = self._normalize_unit_mode(unit_mode)
        map_mode = self._normalize_show_map_mode(show_map)
        norm_vlang = coerce_shortcut_safe_voice_lang(
            voice_lang,
            dialect_pack_ready=dialect_ready,
            allow_cloud_audio=(act_mode == "brief" and map_mode == "silent"),
        )
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
            f"v15|{self._ws_id()}|{effective_base}|{resolved_uid}|{units_digest}|{rep_val}|{vtype}|"
            f"{act_mode}|{safe_name}|{u_mode}|{map_mode}|{norm_vlang}|{int(dialect_ready)}|{int(ask_lang_on_first_run)}|{place_clean}|{radius_clean}|{mobile_base_url}|{clean_dev_uid}|{token}"
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
            voice_lang=norm_vlang,
            dialect_ready=dialect_ready,
            ask_lang_on_first_run=ask_lang_on_first_run,
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

        pack_match = self.match_presigned_pack_shortcut(
            effective_base=effective_base,
            act_mode=act_mode,
            map_mode=map_mode,
            u_mode=u_mode,
            resolved_uid=resolved_uid,
            voice_lang=norm_vlang,
            ask_lang_on_first_run=ask_lang_on_first_run,
        )
        if pack_match is not None:
            return pack_match[0], True, pack_match[1]

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

            sp_tags_hud = sp.get("tags") or []
            is_ev_hud = bool(sp.get("is_ev")) or ("ev_charging" in sp_tags_hud)
            is_rent_hud = bool(sp.get("is_rentable")) or ("rentable" in sp_tags_hud)
            tag_suffix_hud = (
                f"{' · ⚡新能源' if is_ev_hud else ''}{' · 租/共享' if is_rent_hud else ''}"
            )
            info_attr = f"{zname} · {lbl}号位：{st_txt}{tag_suffix_hud}"
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
            if is_ev_hud:
                bx_ev = round(sw * 0.34, 2)
                by_ev = round(-sh * 0.36, 2)
                parts.append(
                    f'<g transform="translate({bx_ev},{by_ev}) rotate({-ang:.1f})">'
                    f'<circle r="0.72" fill="#0F172A" stroke="#FACC15" stroke-width="0.16"/>'
                    f'<polygon points="0.08,-0.46 -0.34,0.06 -0.02,0.06 -0.10,0.46 0.34,-0.06 0.02,-0.06" fill="#FACC15"/>'
                    f'</g>'
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
        voice_lang: Optional[str] = None,
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
            voice_lang=voice_lang,
        )
        eff_lang = str(brief.get("voice_lang") or "zh-CN")
        is_en = eff_lang == "en-US"
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
        if is_en:
            uname = translate_parking_term_to_english(uname, str(brief.get("unit_id") or ""))
        tot_free = int(brief.get("total_effective_free") or 0)
        tot_spots = int(brief.get("total_spots") or 0)
        is_map_only = str(view_mode or "").strip().lower() in ("map", "map_only", "only_map")

        pz = brief.get("primary_zone")
        if pz:
            raw_zn = str(pz.get("zone_name") or "推荐停车区")
            headline = _esc(translate_parking_term_to_english(raw_zn) if is_en else raw_zn)
            sub_metrics = (
                f"{int(pz.get('effective_free') or 0)} free · ~{int(round(float(pz.get('distance_m') or 0)))}m walk"
                if is_en
                else f"推荐区余 {int(pz.get('effective_free') or 0)} 位 · 步行约 {int(round(float(pz.get('distance_m') or 0)))} 米"
            )
        else:
            headline = "All Monitored Spots Occupied" if is_en else "当前院内监测车位已满"
            sub_metrics = "Please head to a nearby overflow lot" if is_en else "建议前往周边备选停车场"

        alt_line = ""
        alts = brief.get("alternatives") or []
        if alts:
            a0 = alts[0]
            a0_name = (
                translate_parking_term_to_english(str(a0.get("display_name") or ""))
                if is_en
                else str(a0.get("display_name") or "")
            )
            alt_line = (
                f'<div class="alt">Backup: {_esc(a0_name)} ({int(a0.get("effective_free") or 0)} free'
                f' · {int(round(float(a0.get("distance_m") or 0)))}m)</div>'
                if is_en
                else f'<div class="alt">备选：{_esc(a0_name)}（余 {int(a0.get("effective_free") or 0)} 位'
                f' · 步行 {int(round(float(a0.get("distance_m") or 0)))}m）</div>'
            )

        pill_cls = "pill-ok" if tot_free > 0 else "pill-full"
        pill_short = f"{tot_free}/{tot_spots} Free" if is_en else f"余 {tot_free}/{tot_spots} 位"
        pill_long = f"Total {tot_free}/{tot_spots} Free" if is_en else f"总余 {tot_free}/{tot_spots} 位"
        top_card_html = (
            f"""<div class="mini-head">
    <span class="dest">📍 {_esc(short_site)} · {_esc(uname)}</span>
    <span class="pill {pill_cls}">{pill_short}</span>
  </div>"""
            if is_map_only
            else f"""<section class="card">
    <div class="row-top">
      <span class="dest">📍 {_esc(short_site)} · {_esc(uname)}</span>
      <span class="pill {pill_cls}">{pill_long}</span>
    </div>
    <h1 class="zone">{headline}</h1>
    <div class="metrics">{_esc(sub_metrics)}</div>
    {alt_line}
  </section>"""
        )
        lg_free = "Free" if is_en else "空闲"
        lg_occ = "Occupied" if is_en else "已停"
        lg_drv = "Drive" if is_en else "行车"
        lg_wlk = "Walk" if is_en else "步行"
        lg_tip = "Pinch to zoom · Tap stall" if is_en else "双指缩放 · 点车位看状态"

        # Inline Quick Look-safe Car-Owner Welfare / Sponsor Slot (zero external network needed!)
        hud_ad_html_both = ""
        hud_ad_html_map = ""
        ads_mgr = getattr(self.ctx, "ads", None)
        if ads_mgr is not None:
            siri_ad_sel = ads_mgr.select_for_siri_execution(
                unit_id=str(brief.get("unit_id") or ""),
                vehicle_type=str(brief.get("vehicle_type") or "sedan"),
                total_effective_free=tot_free,
                include_voice_tail=False,
            )
            hud_ad = siri_ad_sel.get("hud_ad")
            if hud_ad:
                surf_name = "siri_hud_map" if is_map_only else "siri_hud_both"
                ads_mgr.record_event(
                    campaign_id=str(hud_ad["campaign_id"]),
                    event_type="impression",
                    platform="ios_siri",
                    surface=surf_name,
                    external_uid=external_uid or "ios_siri_hud",
                    unit_id=str(brief.get("unit_id") or ""),
                    vehicle_type=str(brief.get("vehicle_type") or "sedan"),
                )
                vg_cfg_h = config_manager.get().get("voice_gateway", {})
                pub_v_url = str(vg_cfg_h.get("public_voice_url") or request_base_url or "").strip().rstrip("/")
                ws_id_cur = getattr(self.ctx, "workspace_id", "default")
                ws_prefix = f"/w/{quote(ws_id_cur)}" if ws_id_cur != "default" else ""
                redir_href = f"{pub_v_url}{ws_prefix}/r/{quote(str(hud_ad['campaign_id']))}?platform=ios_siri&surface={surf_name}"
                thumb_b64 = str(hud_ad.get("hud_thumb_data_url") or "")
                icon_str = _esc(hud_ad.get("icon") or "🎁")
                thumb_el = (
                    f'<img class="ad-thumb" src="{_esc(thumb_b64)}" alt="" />'
                    if thumb_b64.startswith("data:image/")
                    else f'<span class="ad-ic">{icon_str}</span>'
                )
                badge_lbl = _esc(hud_ad.get("badge_text") or "车主福利")
                ad_title = _esc(hud_ad.get("title") or "")
                ad_sub = _esc(hud_ad.get("subtitle") or hud_ad.get("terms_note") or "")
                offer_pill = (
                    f'<span class="ad-offer">{_esc(hud_ad["offer_value"])}</span>'
                    if hud_ad.get("offer_value")
                    else ""
                )
                dist_txt = _esc(hud_ad.get("distance_hint") or hud_ad.get("nav_address") or "")
                phone_val = str(hud_ad.get("phone") or "").strip()
                cta_lbl = _esc(hud_ad.get("cta_text") or "查看详情")

                action_btns = []
                if phone_val:
                    action_btns.append(f'<a class="ad-btn ad-btn-ghost" href="tel:{_esc(phone_val)}">📞 拨号</a>')
                if hud_ad.get("action_type") == "coupon":
                    cp_ev = ads_mgr.record_event(
                        campaign_id=str(hud_ad["campaign_id"]),
                        event_type="issue_coupon",
                        platform="ios_siri",
                        surface=surf_name,
                        external_uid=external_uid or "ios_siri_hud",
                        unit_id=str(brief.get("unit_id") or ""),
                    )
                    c_code = _esc((cp_ev.get("coupon") or {}).get("coupon_code") or "VIP-8888")
                    action_btns.append(f'<span class="ad-code">核销码：{c_code}</span>')
                if hud_ad.get("action_type") in ("url", "map_nav") or hud_ad.get("nav_name"):
                    action_btns.append(f'<a class="ad-btn" href="{_esc(redir_href)}">{cta_lbl} ↗</a>')
                actions_html = f'<div class="ad-actions">{"".join(action_btns)}</div>' if action_btns else ""

                if is_map_only:
                    hud_ad_html_map = f"""<details class="ad-micro">
      <summary><span class="ad-tag">{badge_lbl}</span><span class="ad-m-ttl">{ad_title}</span>{offer_pill}</summary>
      <div class="ad-m-body">{ad_sub}{(' · ' + dist_txt) if dist_txt else ''}{actions_html}</div>
    </details>"""
                else:
                    hud_ad_html_both = f"""<section class="ad-card">
    <div class="ad-main">
      {thumb_el}
      <div class="ad-copy">
        <div class="ad-top"><span class="ad-tag">{badge_lbl}</span>{offer_pill}{f'<span class="ad-dist">{dist_txt}</span>' if dist_txt else ''}</div>
        <div class="ad-ttl">{ad_title}</div>
        {f'<div class="ad-sub">{ad_sub}</div>' if ad_sub else ''}
      </div>
    </div>
    {actions_html}
  </section>"""

        return f"""<!doctype html>
<html lang="{'en' if is_en else 'zh-CN'}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=4,viewport-fit=cover" />
<title>{_esc(short_site)} · {'Live Parking' if is_en else '实时车位态势'}</title>
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
.ad-card{{background:linear-gradient(145deg,#ffffff,#f8fafc);border:1px solid var(--line);border-radius:15px;padding:11px 13px;display:flex;flex-direction:column;gap:8px;box-shadow:0 2px 10px rgba(0,0,0,0.035)}}
.ad-main{{display:flex;align-items:center;gap:10px}}
.ad-thumb{{width:48px;height:48px;border-radius:10px;object-fit:cover;flex-shrink:0;border:1px solid var(--line)}}
.ad-ic{{width:40px;height:40px;border-radius:10px;background:rgba(0,113,227,0.08);display:inline-flex;align-items:center;justify-content:center;font-size:19px;flex-shrink:0}}
.ad-copy{{flex:1;min-width:0}}
.ad-top{{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:2px}}
.ad-tag{{font-size:10.5px;font-weight:700;padding:1.5px 6px;border-radius:5px;background:rgba(0,113,227,0.1);color:#0071e3}}
.ad-offer{{font-size:10.5px;font-weight:800;padding:1.5px 6px;border-radius:5px;background:rgba(245,158,11,0.14);color:#b45309}}
.ad-dist{{font-size:11px;color:var(--muted)}}
.ad-ttl{{font-size:13.5px;font-weight:700;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ad-sub{{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ad-actions{{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}}
.ad-btn{{text-decoration:none;font-size:12px;font-weight:700;padding:5px 11px;border-radius:999px;background:#0071e3;color:#fff}}
.ad-btn-ghost{{background:#f1f5f9;color:#1e293b}}
.ad-code{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px;font-weight:700;padding:4px 9px;border-radius:7px;background:#fef3c7;color:#92400e;border:1px dashed #f59e0b}}
.ad-micro{{border-top:1px solid var(--line);background:#fcfcfd;padding:6px 12px;font-size:11.5px}}
.ad-micro summary{{list-style:none;display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:600}}
.ad-micro summary::-webkit-details-marker{{display:none}}
.ad-m-ttl{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}}
.ad-m-body{{margin-top:6px;color:var(--muted);display:flex;flex-direction:column;gap:6px}}
</style>
</head>
<body>
<div class="wrap">
  {top_card_html}
  <section class="map-card">
    <div class="map-stage" id="mapStage">{svg_markup}</div>
    <div class="legend">
      <div class="dots">
        <span class="dot"><i style="background:#16A34A"></i>{lg_free}</span>
        <span class="dot"><i style="background:#EF4444"></i>{lg_occ}</span>
        <span class="dot"><i style="background:#0071E3"></i>{lg_drv}</span>
        <span class="dot"><i style="background:#0D9488"></i>{lg_wlk}</span>
      </div>
      <span id="spTip">{lg_tip}</span>
    </div>
    {hud_ad_html_map}
  </section>
  {hud_ad_html_both}
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

        def _geo_region_hint(self, qs: Dict[str, List[str]], vg: VoiceGatewayService) -> str:
            q_reg = (qs.get("region") or qs.get("geo") or [""])[0].strip()
            if q_reg:
                return q_reg
            cf_reg = (
                self.headers.get("CF-Region-Code")
                or self.headers.get("CF-Region")
                or self.headers.get("X-Region-Code")
                or ""
            ).strip()
            if cf_reg:
                return cf_reg
            cf_country = (self.headers.get("CF-IPCountry") or "").strip().upper()
            if cf_country and cf_country not in ("CN", "XX", "T1"):
                return cf_country
            prof = vg._ws_profile()
            vg_cfg = config_manager.get().get("voice_gateway", {})
            return str(prof.get("geo_region") or vg_cfg.get("geo_region") or "auto").strip()

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
            self.send_header("Content-Length", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-Parking-Token, X-Parking-Workspace",
            )
            self.send_header("Connection", "close")
            self.end_headers()

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path, _ = self._resolve_ws(parsed.path, qs)
            if path in ("/api/voice/siri.shortcut", "/siri.shortcut"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-apple-shortcut")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if path.startswith("/api/"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Connection", "close")
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

                from app.server import try_handle_public_ad_get

                if try_handle_public_ad_get(self, ctx, path, qs, default_surface="18791_voice"):
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
                    geo_hint = self._geo_region_hint(qs, vg)
                    vg_cfg_st = config_manager.get().get("voice_gateway", {})
                    voice_langs = get_active_voice_languages(vg_cfg_st.get("enabled_voice_langs"), geo_hint)
                    st_zones = ctx.recommender.get_zone_summaries()
                    def_uid_st = metrics.get("default_unit_id", "8-1")
                    tag_avail_st = ctx.recommender.build_tag_availability(
                        zone_summaries=st_zones,
                        units=units,
                        selected_unit_id=def_uid_st,
                    )
                    ads_mgr_st = getattr(ctx, "ads", None)
                    ads_18791_st = (
                        ads_mgr_st.select_for_voice_18791(
                            unit_id=str(def_uid_st),
                            total_effective_free=sum(int(z.get("effective_free") or 0) for z in st_zones),
                        )
                        if ads_mgr_st is not None
                        else {"enabled": False, "live_capsule": None, "portal_items": []}
                    )
                    self._send_json(
                        {
                            "ok": True,
                            "workspace_id": getattr(ctx, "workspace_id", "default"),
                            "workspace": ws_pub,
                            "community_name": metrics.get("workspace_name", ""),
                            "preferred_voice_lang": metrics.get("preferred_voice_lang", ""),
                            "default_voice_lang": metrics.get("default_voice_lang", "zh-CN"),
                            "geo_region": geo_hint,
                            "voice_languages": voice_langs,
                            "spot_tag_catalog": SPOT_TAG_CATALOG,
                            "tag_availability": tag_avail_st,
                            "ads": ads_18791_st,
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
                    vlang = (qs.get("lang") or qs.get("voice_lang") or [""])[0] or None
                    tag_flt = (qs.get("tag") or qs.get("tag_filter") or [""])[0] or None
                    uid = (qs.get("user") or qs.get("uid") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    fmt = (qs.get("format") or ["json"])[0].lower()
                    d_ready = ((qs.get("dialect_ready") or ["0"])[0]).lower() in ("1", "true", "yes")
                    allow_cloud = ((qs.get("audio") or [""])[0]).lower() == "cloud"

                    eff_req_lang = vlang
                    if fmt in ("text", "txt", "plain", "speech", "card", "display") and not d_ready:
                        bound_b = ctx.db.get_im_user_binding("siri", uid) if uid else None
                        resolved_l, _ = vg.resolve_effective_voice_lang(vlang, bound_b)
                        eff_req_lang = coerce_shortcut_safe_voice_lang(
                            resolved_l, dialect_pack_ready=False, allow_cloud_audio=allow_cloud
                        )

                    res = vg.build_voice_brief(
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        channel="siri",
                        external_uid=uid,
                        voice_lang=eff_req_lang,
                        tag_filter=tag_flt,
                    )
                    ads_mgr_b = getattr(ctx, "ads", None)
                    ua_b = str(self.headers.get("User-Agent") or "")
                    if fmt not in ("card", "display", "text", "txt", "plain", "speech"):
                        vg.prewarm_tts_async(res.get("unit_id"), res.get("voice_lang"), vtype or "sedan")
                    if fmt in ("card", "display"):
                        card_out = str(res["display_text"])
                        if ads_mgr_b is not None and str(res.get("voice_lang") or "zh-CN") != "en-US":
                            siri_card_sel = ads_mgr_b.select_for_siri_execution(
                                unit_id=str(res.get("unit_id") or ""),
                                vehicle_type=str(res.get("vehicle_type") or "sedan"),
                                tag_filter=str(res.get("tag_filter") or ""),
                                total_effective_free=int(res.get("total_effective_free") or 0),
                                include_voice_tail=False,
                            )
                            c_ad = siri_card_sel.get("hud_ad")
                            if c_ad:
                                ads_mgr_b.record_event(
                                    campaign_id=str(c_ad["campaign_id"]),
                                    event_type="impression",
                                    platform=detect_client_platform(ua_b) if ua_b else "ios_siri",
                                    surface="siri_text_card",
                                    client_ip=self._client_ip(),
                                    user_agent=ua_b,
                                    external_uid=uid,
                                    unit_id=str(res.get("unit_id") or ""),
                                )
                                offer_sfx = f"（{c_ad['offer_value']}）" if c_ad.get("offer_value") else ""
                                phone_sfx = f" · ☎️{c_ad['phone']}" if c_ad.get("phone") else ""
                                card_out = (
                                    f"{card_out}\n──────────\n"
                                    f"🎁 {c_ad.get('badge_text') or '车主福利'}：{c_ad['title']}{offer_sfx}{phone_sfx}"
                                )
                        self._send_text(card_out)
                    elif fmt in ("text", "txt", "plain", "speech"):
                        sp_out = str(res["speech_text"])
                        if ads_mgr_b is not None and str(res.get("voice_lang") or "zh-CN") != "en-US":
                            siri_sp_sel = ads_mgr_b.select_for_siri_execution(
                                unit_id=str(res.get("unit_id") or ""),
                                vehicle_type=str(res.get("vehicle_type") or "sedan"),
                                tag_filter=str(res.get("tag_filter") or ""),
                                total_effective_free=int(res.get("total_effective_free") or 0),
                                include_voice_tail=True,
                            )
                            vt_ad = siri_sp_sel.get("voice_tail_ad")
                            vt_txt = str(siri_sp_sel.get("voice_tail_text") or "").strip()
                            if vt_ad and vt_txt:
                                ads_mgr_b.record_event(
                                    campaign_id=str(vt_ad["campaign_id"]),
                                    event_type="audio_play",
                                    platform=detect_client_platform(ua_b) if ua_b else "ios_siri",
                                    surface="voice_tail",
                                    client_ip=self._client_ip(),
                                    user_agent=ua_b,
                                    external_uid=uid,
                                    unit_id=str(res.get("unit_id") or ""),
                                )
                                pass1 = f"{res['single_text']} {vt_txt}"
                                sp_out = vg.format_repeated_speech(
                                    pass1,
                                    int(res.get("repeat") or 1),
                                    repeat_core=str(res.get("repeat_text") or ""),
                                    voice_lang=str(res.get("voice_lang") or "zh-CN"),
                                )
                        self._send_text(sp_out)
                    else:
                        if ads_mgr_b is not None:
                            res["ads"] = ads_mgr_b.select_for_voice_18791(
                                unit_id=str(res.get("unit_id") or ""),
                                vehicle_type=str(res.get("vehicle_type") or "sedan"),
                                tag_filter=str(res.get("tag_filter") or ""),
                                total_effective_free=int(res.get("total_effective_free") or 0),
                            )
                        self._send_json(res)
                    return

                if path in ("/api/voice/tts.mp3", "/api/voice/tts"):
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    rep_raw = (qs.get("repeat") or ["1"])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else 1
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or [""])[0] or None
                    vlang = (qs.get("lang") or qs.get("voice_lang") or [""])[0] or None
                    tag_flt = (qs.get("tag") or qs.get("tag_filter") or [""])[0] or None
                    uid = (qs.get("user") or qs.get("uid") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    direct_text = (qs.get("text") or [""])[0].strip()

                    bound_t = ctx.db.get_im_user_binding("siri", uid) if uid else None
                    eff_t_lang, _ = vg.resolve_effective_voice_lang(vlang, bound_t)
                    eff_t_lang = coerce_shortcut_safe_voice_lang(
                        eff_t_lang, dialect_pack_ready=False, allow_cloud_audio=True
                    )

                    if direct_text:
                        tts_text = direct_text
                    else:
                        br_tts = vg.build_voice_brief(
                            unit_id=unit_id,
                            repeat=rep,
                            vehicle_type=vtype,
                            channel="siri",
                            external_uid=uid,
                            voice_lang=eff_t_lang,
                            tag_filter=tag_flt,
                        )
                        tts_text = str(br_tts["speech_text"] if rep > 1 else br_tts["single_text"])
                        ads_mgr_t = getattr(ctx, "ads", None)
                        if ads_mgr_t is not None and eff_t_lang != "en-US":
                            siri_t_sel = ads_mgr_t.select_for_siri_execution(
                                unit_id=str(br_tts.get("unit_id") or ""),
                                vehicle_type=str(br_tts.get("vehicle_type") or "sedan"),
                                tag_filter=str(br_tts.get("tag_filter") or ""),
                                total_effective_free=int(br_tts.get("total_effective_free") or 0),
                                include_voice_tail=True,
                            )
                            vt_ad_t = siri_t_sel.get("voice_tail_ad")
                            vt_txt_t = str(siri_t_sel.get("voice_tail_text") or "").strip()
                            if vt_ad_t and vt_txt_t:
                                ua_t = str(self.headers.get("User-Agent") or "")
                                ads_mgr_t.record_event(
                                    campaign_id=str(vt_ad_t["campaign_id"]),
                                    event_type="audio_play",
                                    platform=detect_client_platform(ua_t),
                                    surface="voice_tail_mp3",
                                    client_ip=self._client_ip(),
                                    user_agent=ua_t,
                                    external_uid=uid,
                                    unit_id=str(br_tts.get("unit_id") or ""),
                                )
                                pass1_t = f"{br_tts['single_text']} {vt_txt_t}"
                                tts_text = vg.format_repeated_speech(
                                    pass1_t,
                                    rep,
                                    repeat_core=str(br_tts.get("repeat_text") or ""),
                                    voice_lang=eff_t_lang,
                                )

                    mp3_bytes = vg.synthesize_tts_mp3(tts_text, voice_lang=eff_t_lang)
                    if not mp3_bytes:
                        self._send_json({"ok": False, "error": "云端方言语音合成暂不可用"}, 503)
                        return
                    self._send_bytes(mp3_bytes, content_type="audio/mpeg")
                    return

                if path == "/api/voice/ask":
                    q = (qs.get("q") or qs.get("question") or qs.get("text") or [""])[0]
                    unit_id = (qs.get("unit") or qs.get("unit_id") or [""])[0] or None
                    rep_raw = (qs.get("repeat") or [""])[0]
                    rep = int(rep_raw) if rep_raw.strip().isdigit() else None
                    vtype = (qs.get("vehicle_type") or qs.get("vehicle") or [""])[0] or None
                    vlang = (qs.get("lang") or qs.get("voice_lang") or [""])[0] or None
                    tag_flt = (qs.get("tag") or qs.get("tag_filter") or [""])[0] or None
                    uid = (qs.get("user") or qs.get("uid") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    fmt = (qs.get("format") or ["json"])[0].lower()
                    d_ready = ((qs.get("dialect_ready") or ["0"])[0]).lower() in ("1", "true", "yes")

                    eff_ask_lang = vlang
                    if fmt in ("text", "txt", "plain", "speech") and not d_ready:
                        bound_a = ctx.db.get_im_user_binding("siri", uid) if uid else None
                        resolved_al, _ = vg.resolve_effective_voice_lang(vlang, bound_a)
                        eff_ask_lang = coerce_shortcut_safe_voice_lang(resolved_al, dialect_pack_ready=False)

                    res = vg.ask_voice_assistant(
                        question=q,
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        channel="siri",
                        external_uid=uid,
                        client_ip=self._client_ip(),
                        voice_lang=eff_ask_lang,
                        tag_filter=tag_flt,
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
                    tag_flt = (qs.get("tag") or qs.get("tag_filter") or [""])[0] or None
                    self._send_json(
                        _build_mobile_overview_payload(
                            ctx, unit_id=unit_id, vehicle_type=vtype, tag_filter=tag_flt
                        )
                    )
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

                if path == "/api/voice/units.txt":
                    units_all = ctx.db.list_units() or []
                    lines = []
                    for u in units_all[:40]:
                        u_id = str(u.get("unit_id") or "").strip()
                        u_nm = str(u.get("name") or u_id).strip()
                        if u_id:
                            lines.append(f"{u_nm} ({u_id})" if u_id not in u_nm else u_nm)
                    if not lines:
                        lines.append("8号楼1单元 (8-1)")
                    self._send_text("\n".join(lines))
                    return

                if path == "/api/voice/languages.txt":
                    geo_hint = self._geo_region_hint(qs, vg)
                    inc_dialects = ((qs.get("include_dialects") or qs.get("all") or qs.get("dialect_ready") or ["0"])[0]).lower() in ("1", "true", "yes")
                    allow_cloud_lg = ((qs.get("audio") or [""])[0]).lower() == "cloud"
                    vg_cfg_lg = config_manager.get().get("voice_gateway", {})
                    langs = get_active_voice_languages(
                        vg_cfg_lg.get("enabled_voice_langs"),
                        geo_hint,
                        preinstalled_only=(not inc_dialects),
                        allow_cloud_audio=allow_cloud_lg,
                    )
                    self._send_text("\n".join(item["menu_item"] for item in langs))
                    return

                if path == "/api/voice/my-lang":
                    uid_ml = (qs.get("uid") or qs.get("user") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    exp_lang = (qs.get("lang") or qs.get("voice_lang") or [""])[0].strip() or None
                    d_ready_ml = ((qs.get("dialect_ready") or ["0"])[0]).lower() in ("1", "true", "yes")
                    allow_cloud_ml = ((qs.get("audio") or [""])[0]).lower() == "cloud"
                    bound_ml = ctx.db.get_im_user_binding("siri", uid_ml) if uid_ml else None
                    raw_eff_ml, _ = vg.resolve_effective_voice_lang(exp_lang, bound_ml)
                    eff_lang_ml = coerce_shortcut_safe_voice_lang(
                        raw_eff_ml, dialect_pack_ready=d_ready_ml, allow_cloud_audio=allow_cloud_ml
                    )
                    fmt_ml = (qs.get("format") or ["slot"])[0].strip().lower()
                    if fmt_ml == "json":
                        self._send_json(
                            {
                                "ok": True,
                                "voice_lang": eff_lang_ml,
                                "requested_voice_lang": raw_eff_ml,
                                "apple_tts_lang": voice_lang_to_apple_tts(eff_lang_ml),
                                "apple_tts_voice": voice_lang_to_apple_voice_id(eff_lang_ml),
                                "slot_token": voice_lang_to_slot_token(eff_lang_ml),
                            }
                        )
                    elif fmt_ml == "code":
                        self._send_text(eff_lang_ml)
                    else:
                        self._send_text(voice_lang_to_slot_token(eff_lang_ml))
                    return

                if path == "/api/voice/check-bind":
                    uid_chk = (qs.get("uid") or qs.get("user") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    bound = ctx.db.get_im_user_binding("siri", uid_chk) if uid_chk else None
                    self._send_text(str(bound["unit_id"]) if (bound and bound.get("unit_id")) else "")
                    return

                if path == "/api/voice/quick-bind":
                    uid_qb = (qs.get("uid") or qs.get("user") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    raw_u_qb = (qs.get("unit") or qs.get("unit_id") or [""])[0].strip()
                    raw_l_qb = (qs.get("lang") or qs.get("voice_lang") or [""])[0].strip()
                    norm_l_qb = normalize_voice_lang(raw_l_qb, default="") if raw_l_qb else None
                    if uid_qb and (raw_u_qb or norm_l_qb):
                        resolved_uid, _uname, cur_b = vg.resolve_unit_id(raw_u_qb or None, "siri", uid_qb)
                        ctx.db.upsert_im_user_binding(
                            channel="siri",
                            external_uid=uid_qb,
                            unit_id=resolved_uid,
                            repeat_count=int(cur_b.get("repeat_count") if cur_b else 2),
                            voice_lang=norm_l_qb,
                        )
                        if norm_l_qb:
                            vg.prewarm_tts_async(resolved_uid, norm_l_qb)
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
                    vlang = (qs.get("lang") or qs.get("voice_lang") or [""])[0] or None
                    ext_uid = (qs.get("uid") or qs.get("user") or [""])[0].strip() or f"ip:{self._client_ip()}"
                    html_doc = vg.render_voice_hud_html(
                        request_base_url=self._request_base_url(),
                        unit_id=unit_id,
                        repeat=rep,
                        vehicle_type=vtype,
                        token=tok,
                        view_mode=vmode,
                        external_uid=ext_uid,
                        voice_lang=vlang,
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
                    show_map = (qs.get("show_map") or qs.get("popup_mode") or ["text"])[0]
                    raw_sc_lang = (qs.get("lang") or qs.get("voice_lang") or [""])[0].strip()
                    raw_lang_mode = (qs.get("lang_mode") or [""])[0].strip().lower()
                    ask_lang_once = (
                        raw_sc_lang.lower() in ("", "ask_once", "prompt_once", "auto")
                        or raw_lang_mode in ("ask_once", "prompt_once")
                    )
                    d_ready_sc = ((qs.get("dialect_ready") or ["0"])[0]).lower() in ("1", "true", "yes")
                    dev_uid = (qs.get("device_uid") or qs.get("uid") or [""])[0].strip()
                    vg_cfg = config_manager.get().get("voice_gateway", {})
                    clean_raw_lang = "" if raw_sc_lang.lower() in ("ask_once", "prompt_once", "auto") else raw_sc_lang
                    norm_show_map = vg._normalize_show_map_mode(show_map)
                    is_sc_silent = str(sc_mode or "brief").strip().lower() != "ask" and norm_show_map == "silent"
                    eff_sc_lang = coerce_shortcut_safe_voice_lang(
                        clean_raw_lang or vg._ws_profile().get("preferred_voice_lang") or vg_cfg.get("default_voice_lang") or "zh-CN",
                        dialect_pack_ready=d_ready_sc,
                        allow_cloud_audio=is_sc_silent,
                    )
                    raw_name = (qs.get("name") or [""])[0].strip()
                    safe_sc_name = vg.sanitize_shortcut_name(raw_name, "小区车位")
                    arr_place = (qs.get("arrival_place") or [str(vg_cfg.get("arrival_place") or "")])[0].strip()
                    arr_rad_raw = (qs.get("arrival_radius") or [str(vg_cfg.get("arrival_radius_m") or 200)])[0]
                    arr_rad = int(arr_rad_raw) if str(arr_rad_raw).strip().isdigit() else 200
                    want_raw = ((qs.get("raw") or qs.get("unsigned") or ["0"])[0]).lower() in ("1", "true", "yes")
                    want_prepare = ((qs.get("prepare") or qs.get("prewarm") or ["0"])[0]).lower() in ("1", "true", "yes")

                    pub_base = str(vg_cfg.get("public_voice_url") or "").strip() or self._request_base_url()
                    pub_mob = _infer_paired_mobile_url(pub_base, str(vg_cfg.get("public_mobile_url") or ""))
                    cfg_tok = vg._effective_access_token()

                    # On Ubuntu/Linux (`not _can_macos_sign_shortcuts()`), sync `ip:<client_ip>` binding during `prepare=1`
                    # so `dynamic_<mode>.shortcut` / `fixed_*` / `menu_*` know the exact unit & language selected on the webpage
                    # (or resets binding for `setup_once` so Run #1 prompts for Unit + Language)!
                    if want_prepare:
                        ip_bind_key = f"ip:{self._client_ip()}"
                        norm_um = vg._normalize_unit_mode(u_mode)
                        if norm_um in ("fixed", "menu"):
                            r_uid, _r_uname, _ = vg.resolve_unit_id(unit_id, "siri", "")
                            ctx.db.upsert_im_user_binding(
                                channel="siri",
                                external_uid=ip_bind_key,
                                unit_id=r_uid,
                                repeat_count=rep,
                                vehicle_type=vtype,
                                voice_lang=eff_sc_lang,
                            )
                            vg.prewarm_tts_async(r_uid, eff_sc_lang, vtype)
                        elif norm_um == "setup_once":
                            ctx.db.delete_im_user_binding("siri", ip_bind_key)
                            if not ask_lang_once:
                                vg.prewarm_tts_async(unit_id, eff_sc_lang, vtype)

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
                            voice_lang=eff_sc_lang,
                            dialect_ready=d_ready_sc,
                            ask_lang_on_first_run=ask_lang_once,
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
                        voice_lang=eff_sc_lang,
                        dialect_ready=d_ready_sc,
                        ask_lang_on_first_run=ask_lang_once,
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

            if path in ("/api/public/ad-event", "/api/public/ad-partner-redeem"):
                from app.server import try_handle_public_ad_post

                try_handle_public_ad_post(self, ctx, path, qs=qs, default_surface="18791_voice")
                return

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
                    vlang = str(body.get("voice_lang") or body.get("lang") or (qs.get("lang") or [""])[0]) or None
                    tag_flt = str(
                        body.get("tag_filter")
                        or body.get("tag")
                        or (qs.get("tag") or qs.get("tag_filter") or [""])[0]
                    ) or None
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
                        voice_lang=vlang,
                        tag_filter=tag_flt,
                    )
                    if fmt in ("text", "txt", "plain", "speech"):
                        self._send_text(res["speech_text"])
                    else:
                        self._send_json(res)
                    return

                if path == "/api/voice/bind":
                    uid = str(body.get("user") or body.get("uid") or "").strip()
                    raw_unit = str(body.get("unit") or body.get("unit_id") or "").strip()
                    raw_vlang = str(body.get("voice_lang") or body.get("lang") or "").strip()
                    norm_vlang = normalize_voice_lang(raw_vlang, default="") if raw_vlang else None
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
                        voice_lang=norm_vlang,
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
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
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
                self.send_header("Connection", "close")
                self.end_headers()
                if not head_only:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return VoiceGatewayRequestHandler


class VoiceThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128


def create_voice_http_server(
    host: str = "0.0.0.0", port: int = 18791, ctx: Any = None
) -> ThreadingHTTPServer:
    handler_cls = make_voice_http_handler(ctx)
    return VoiceThreadingHTTPServer((host, port), handler_cls)
