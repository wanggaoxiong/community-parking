"""Multi-Surface Ad Operations, Smart Targeting & Anti-Fraud Reconciliation Engine.

Supports:
- Differentiated placements & creative layouts across `:18790` (WeChat/Mobile Viewer),
  `:18791` (Web / Android Quick-Launch PWA / AI Assistant), and iOS Siri Shortcuts
  (`both` & `map` Quick Look single-file HUD, `text` card, `silent` 1st-pass voice tail).
- 4D Contextual Targeting: Date/Season × Peak Time Windows × Gate/Building LBS ×
  Vehicle/Tag/Full-Lot Trigger (`when_full`, `ev`, `suv`, etc.).
- 3-Tier Waterfall Priority (`1=物业公益置顶` -> `2=直客商业高优` -> `3=联盟闲时兜底`) + Weighted Rotation.
- 5 Commercial Billing Models (`CPT`, `CPM`, `CPC`, `CPA`, `CPS`, `PUBLIC`) with IAB-grade
  anti-spam impression/UV/click deduplication, dynamic offline coupon codes, CPA lead capture,
  UTM-signed 302 click redirects, CSV export, and read-only Merchant Partner Reconciliation Portal.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import csv
import datetime
import hashlib
import hmac
import html
import io
import json
import random
import re
import secrets
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image


AD_BILLING_MODELS: Dict[str, Dict[str, Any]] = {
    "CPT": {
        "code": "CPT",
        "label": "CPT 包时段/包栏位",
        "unit_label": "元/天",
        "short_desc": "按排期天数固定买断或轮播，对账核心为在线履约天数、展示PV与独立车主UV",
    },
    "CPM": {
        "code": "CPM",
        "label": "CPM 千次有效曝光",
        "unit_label": "元/千次",
        "short_desc": "IAB标准入屏≥1秒计1次有效曝光（含音频真实播报），30分钟UV去重防轮询注水",
    },
    "CPC": {
        "code": "CPC",
        "label": "CPC 按有效点击",
        "unit_label": "元/次点击",
        "short_desc": "60秒防误触去重，自动生成带 UTM 归因参数的 302 追踪跳转链接",
    },
    "CPA": {
        "code": "CPA",
        "label": "CPA 按线索/拨号转化",
        "unit_label": "元/条线索",
        "short_desc": "按有效拨号、复制微信号或3秒极简预约留资单数结算，适合车险/保养/装桩/长租",
    },
    "CPS": {
        "code": "CPS",
        "label": "CPS 领券到店核销",
        "unit_label": "元/单核销",
        "short_desc": "自动签发唯一动态核销码（如 WK-1121-8392），车主到店出示、店员输码即核销对账",
    },
    "PUBLIC": {
        "code": "PUBLIC",
        "label": "公益/物业免费通知",
        "unit_label": "免费",
        "short_desc": "物业温馨提示、挪车通知、恶劣天气提醒或社区公益活动，优先置顶且不计商业费用",
    },
}

AD_CATEGORIES: List[Dict[str, Any]] = [
    {"code": "car_wash", "group": "车后刚需", "label": "🚿 洗车美容", "icon": "🚿", "accent": "#0071E3", "ai_kw": ("洗车", "精洗", "打蜡", "镀晶", "洗个车", "内饰清洁")},
    {"code": "ev_charge", "group": "车后刚需", "label": "⚡ 新能源充电/装桩", "icon": "⚡", "accent": "#059669", "ai_kw": ("充电", "快充", "超充", "装桩", "充电站", "电费", "谷电")},
    {"code": "fuel", "group": "车后刚需", "label": "⛽ 加油特惠", "icon": "⛽", "accent": "#EA580C", "ai_kw": ("加油", "油站", "汽油", "92号", "95号", "油价")},
    {"code": "insurance", "group": "车后刚需", "label": "🛡️ 车险续保", "icon": "🛡️", "accent": "#4F46E5", "ai_kw": ("车险", "保险", "续保", "交强险", "出险", "理赔")},
    {"code": "maintenance", "group": "车后刚需", "label": "🔧 维修保养/救援", "icon": "🔧", "accent": "#0284C7", "ai_kw": ("保养", "机油", "修车", "汽修", "换胎", "补胎", "搭电", "电瓶亏电", "救援")},
    {"code": "overflow_parking", "group": "车后刚需", "label": "🅿️ 周边临停/月租车位", "icon": "🅿️", "accent": "#0D9488", "ai_kw": ("停满", "没车位", "周边停车", "附近停车场", "地下车库", "包月车位", "月卡")},
    {"code": "fresh_food", "group": "社区生活", "label": "🥬 社区生鲜/超市", "icon": "🥬", "accent": "#16A34A", "ai_kw": ("买菜", "生鲜", "水果", "超市", "晚市", "熟食", "牛奶")},
    {"code": "apparel_life", "group": "社区生活", "label": "👔 服饰百货/干洗家政", "icon": "👔", "accent": "#9333EA", "ai_kw": ("衣服", "服装", "干洗", "洗衣", "羽绒服", "家政", "保洁")},
    {"code": "dining", "group": "社区生活", "label": "🍜 周边餐饮/咖啡", "icon": "🍜", "accent": "#D97706", "ai_kw": ("吃饭", "早餐", "咖啡", "夜宵", "餐厅", "外卖", "火锅")},
    {"code": "property_notice", "group": "物业与联盟", "label": "📢 物业温馨通知", "icon": "📢", "accent": "#2563EB", "ai_kw": ("物业", "通知", "公告", "管家")},
    {"code": "ad_network", "group": "物业与联盟", "label": "🌐 广告联盟/活动外链", "icon": "🌐", "accent": "#475569", "ai_kw": ("优惠活动", "福利")},
]

AD_CATEGORY_MAP: Dict[str, Dict[str, Any]] = {c["code"]: c for c in AD_CATEGORIES}

AD_INDUSTRY_PRESETS: List[Dict[str, Any]] = [
    {
        "preset_id": "wash_cps",
        "name": "🚿 门口精洗券 (CPS核销)",
        "title": "南门匠心精洗 · 业主晚间普洗 ¥19.9",
        "subtitle": "含全车泡沫精洗+内饰吸尘+轮胎上光，出示专属核销码即享",
        "advertiser_name": "匠心车美养护中心(南门旁)",
        "category": "car_wash",
        "billing_model": "CPS",
        "unit_price": 5.0,
        "priority_tier": 2,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "capsule",
            "p18791_web_enabled": True,
            "p18791_layout": "portal_card",
            "siri_hud_enabled": True,
            "voice_tail_enabled": False,
            "voice_tail_text": "",
            "voice_quiet_hours": "22:00-07:30",
        },
        "creative": {
            "badge_text": "业主专享",
            "accent_color": "#0071E3",
            "cta_text": "领券立减",
            "action_type": "coupon",
            "offer_value": "¥19.9",
            "coupon_prefix": "XC",
            "distance_hint": "距南门 80m",
            "phone": "13800138000",
            "nav_name": "匠心车美养护中心",
        },
        "targeting": {"trigger_mode": "always"},
    },
    {
        "preset_id": "full_lot_cpt",
        "name": "🅿️ 满位周边地库导引 (CPT包月)",
        "title": "院内暂满？隔壁万科中心B2夜间封顶 ¥10",
        "subtitle": "晚18:30至次日08:00业主凭券享夜间封顶10元，充足空位免排队",
        "advertiser_name": "万科中心商业地下停车场",
        "category": "overflow_parking",
        "billing_model": "CPT",
        "unit_price": 30.0,
        "priority_tier": 2,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "card",
            "p18791_web_enabled": True,
            "p18791_layout": "capsule",
            "siri_hud_enabled": True,
            "voice_tail_enabled": True,
            "voice_tail_text": "满位可停万科中心地库享夜间十元封顶。",
            "voice_quiet_hours": "23:00-07:00",
        },
        "creative": {
            "badge_text": "满位特惠导引",
            "accent_color": "#0D9488",
            "cta_text": "领券并导航",
            "action_type": "coupon",
            "offer_value": "¥10封顶",
            "coupon_prefix": "PK",
            "distance_hint": "距东门 150m",
            "nav_name": "万科中心地下停车场入口",
        },
        "targeting": {"trigger_mode": "when_full"},
    },
    {
        "preset_id": "ev_charge_cpc",
        "name": "⚡ 特来电谷电快充 (新能源定向)",
        "title": "北门120kW超充站 · 23点后服务费5折",
        "subtitle": "双枪液冷超充免收2小时停车费，绿牌业主一键导航直达",
        "advertiser_name": "北门阳光超充驿站",
        "category": "ev_charge",
        "billing_model": "CPC",
        "unit_price": 0.8,
        "priority_tier": 2,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "capsule",
            "p18791_web_enabled": True,
            "p18791_layout": "portal_card",
            "siri_hud_enabled": True,
            "voice_tail_enabled": False,
        },
        "creative": {
            "badge_text": "⚡ 充电特惠",
            "accent_color": "#059669",
            "cta_text": "导航去充电",
            "action_type": "map_nav",
            "offer_value": "服务费5折",
            "distance_hint": "距北门 110m",
            "nav_name": "阳光120kW超充站",
        },
        "targeting": {"trigger_mode": "always", "vehicle_types": ["ev"], "spot_tags": ["ev_charging"]},
    },
    {
        "preset_id": "insure_cpa",
        "name": "🛡️ 车险续保顾问 (CPA留资)",
        "title": "本小区车主续保团购 · 返¥300洗车保养卡",
        "subtitle": "人保/平安/太平洋三家比价直降，留下电话3分钟出精准报价单",
        "advertiser_name": "社区车管家直营车险服务部",
        "category": "insurance",
        "billing_model": "CPA",
        "unit_price": 35.0,
        "priority_tier": 2,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "card",
            "p18791_web_enabled": True,
            "p18791_layout": "portal_card",
            "siri_hud_enabled": False,
            "voice_tail_enabled": False,
        },
        "creative": {
            "badge_text": "车险比价",
            "accent_color": "#4F46E5",
            "cta_text": "预约比价",
            "action_type": "lead_form",
            "offer_value": "赠¥300卡",
            "phone": "13900139000",
        },
        "targeting": {"trigger_mode": "always"},
    },
    {
        "preset_id": "fresh_evening_cpm",
        "name": "🥬 晚高峰生鲜出清 (16:30-21:00)",
        "title": "回家顺路买好菜 · 南门鲜生晚7点后全场7折",
        "subtitle": "当日日配土猪排骨、活虾与有机蔬菜不隔夜，停好车步行即达",
        "advertiser_name": "邻里鲜生社区超市(南门店)",
        "category": "fresh_food",
        "billing_model": "CPM",
        "unit_price": 45.0,
        "priority_tier": 2,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "video",
            "p18791_web_enabled": True,
            "p18791_layout": "portal_card",
            "siri_hud_enabled": True,
            "voice_tail_enabled": False,
        },
        "creative": {
            "badge_text": "晚市生鲜",
            "accent_color": "#16A34A",
            "cta_text": "领满减券",
            "action_type": "coupon",
            "offer_value": "晚市7折",
            "coupon_prefix": "SX",
            "distance_hint": "南门入户必经",
        },
        "targeting": {"trigger_mode": "always", "time_windows": ["16:30-21:30"]},
    },
    {
        "preset_id": "prop_public",
        "name": "📢 物业公益温馨提示 (免费置顶)",
        "title": "本周六上午地面树木修剪 · 请5/6栋车主留意",
        "subtitle": "09:00-11:30 园林高空修枝养护，如需临时挪车管家将提前致电",
        "advertiser_name": "小区物业服务中心",
        "category": "property_notice",
        "billing_model": "PUBLIC",
        "unit_price": 0.0,
        "priority_tier": 1,
        "placements": {
            "p18790_enabled": True,
            "p18790_layout": "capsule",
            "p18791_web_enabled": True,
            "p18791_layout": "capsule",
            "siri_hud_enabled": True,
            "voice_tail_enabled": False,
        },
        "creative": {
            "badge_text": "物业通知",
            "accent_color": "#2563EB",
            "cta_text": "联系管家",
            "action_type": "call",
            "phone": "027-88886666",
        },
        "targeting": {"trigger_mode": "always"},
    },
]


DEFAULT_AD_SETTINGS: Dict[str, Any] = {
    "ads_enabled": True,
    "voice_tail_master_enabled": True,
    "max_carousel_items": 3,
    "default_quiet_hours": "22:00-07:30",
}


def _clean_text(val: Any, max_len: int = 120) -> str:
    s = str(val or "").strip()
    s = "".join(ch for ch in s if ch.isprintable() and ch not in "\r\n\t")
    return s[:max_len].strip()


def _compress_ad_image_data_url(
    raw_data_url: str,
    max_edge: int = 720,
    jpeg_quality: int = 80,
) -> str:
    """Downscale & JPEG-compress uploaded base64 poster in RAM so SQLite stays tiny."""
    s = str(raw_data_url or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "/")):
        return s[:500]
    if not s.startswith("data:image/"):
        return ""
    try:
        _, b64_part = s.split(",", 1)
        raw_bytes = base64.b64decode(b64_part)
        if len(raw_bytes) > 8 * 1024 * 1024:
            raise ValueError("广告图片大小不能超过 8MB")
        with Image.open(io.BytesIO(raw_bytes)) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            if max(w, h) > max_edge:
                scale = float(max_edge) / float(max(w, h))
                rgb = rgb.resize(
                    (max(24, int(round(w * scale))), max(24, int(round(h * scale)))),
                    Image.Resampling.LANCZOS,
                )
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except ValueError:
        raise
    except Exception:
        return ""


def _make_hud_micro_thumb(data_url: str) -> str:
    """Create an ultra-light <=140px inline base64 thumbnail safe for Apple Siri Quick Look sandbox."""
    s = str(data_url or "").strip()
    if not s.startswith("data:image/"):
        return ""
    return _compress_ad_image_data_url(s, max_edge=140, jpeg_quality=72)


def detect_client_platform(user_agent: str, explicit_hint: str = "") -> str:
    hint = str(explicit_hint or "").strip().lower()
    if hint in ("ios_siri", "wechat", "ios_web", "android_web", "voice_api", "desktop_web"):
        return hint
    ua = str(user_agent or "").lower()
    if "micromessenger" in ua:
        return "wechat"
    if "shortcuts" in ua or "siri" in ua or ("cfnetwork" in ua and "darwin" in ua):
        return "ios_siri"
    if "android" in ua:
        return "android_web"
    if any(k in ua for k in ("iphone", "ipad", "ipod")):
        return "ios_web"
    if any(k in ua for k in ("mozilla/", "safari/", "chrome/")):
        return "desktop_web"
    return "voice_api"


def _current_season(dt: datetime.date) -> str:
    m = dt.month
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return "winter"


def _is_within_hhmm_window(now_minutes: int, window_expr: str) -> bool:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", str(window_expr or ""))
    if not m:
        return False
    s_min = int(m.group(1)) * 60 + int(m.group(2))
    e_min = int(m.group(3)) * 60 + int(m.group(4))
    if s_min <= e_min:
        return s_min <= now_minutes <= e_min
    # Overnight window such as 22:00-07:30
    return now_minutes >= s_min or now_minutes <= e_min


def append_utm_params(url: str, campaign_id: str, platform: str, surface: str) -> str:
    clean = str(url or "").strip()
    if not clean.startswith(("http://", "https://")):
        return ""
    parsed = urlparse(clean)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q.setdefault("utm_source", "community_parking")
    q.setdefault("utm_medium", surface or platform or "app")
    q.setdefault("utm_campaign", campaign_id)
    return urlunparse(parsed._replace(query=urlencode(q)))


class AdOperationsManager:
    """Per-workspace Ad Campaign, Smart Targeting, Anti-Fraud Attribution & Reconciliation Service."""

    MAX_DEDUP_ENTRIES = 4096

    def __init__(self, database: Any, workspace_id_getter: Optional[Any] = None) -> None:
        self.db = database
        self._ws_id_getter = workspace_id_getter
        self._lock = threading.Lock()
        self._impression_dedup: "OrderedDict[str, float]" = OrderedDict()
        self._uv_dedup: "OrderedDict[str, float]" = OrderedDict()
        self._click_dedup: "OrderedDict[str, float]" = OrderedDict()
        self._coupon_dedup: "OrderedDict[str, str]" = OrderedDict()

    def _ws_id(self) -> str:
        if callable(self._ws_id_getter):
            return str(self._ws_id_getter() or "default")
        return "default"

    def _check_dedup_locked(
        self, store: "OrderedDict[str, float]", key: str, ttl_sec: float, now_ts: float
    ) -> bool:
        """Return True if `key` is a duplicate within `ttl_sec`; otherwise record and return False."""
        prev = store.get(key)
        if prev is not None and (now_ts - prev) < ttl_sec:
            store.move_to_end(key)
            return True
        store[key] = now_ts
        store.move_to_end(key)
        while len(store) > self.MAX_DEDUP_ENTRIES:
            store.popitem(last=False)
        return False

    @staticmethod
    def _client_fingerprint(client_ip: str, user_agent: str, external_uid: str = "") -> str:
        raw = f"{external_uid.strip()}|{client_ip.strip()}|{user_agent[:80].strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Normalization & Campaign CRUD
    # ------------------------------------------------------------------

    def get_settings(self) -> Dict[str, Any]:
        saved = self.db.get_ad_settings()
        merged = dict(DEFAULT_AD_SETTINGS)
        merged.update(saved)
        merged["ads_enabled"] = bool(merged.get("ads_enabled", True))
        merged["voice_tail_master_enabled"] = bool(merged.get("voice_tail_master_enabled", True))
        merged["max_carousel_items"] = max(1, min(6, int(merged.get("max_carousel_items") or 3)))
        return merged

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cur = self.get_settings()
        if "ads_enabled" in patch:
            cur["ads_enabled"] = bool(patch["ads_enabled"])
        if "voice_tail_master_enabled" in patch:
            cur["voice_tail_master_enabled"] = bool(patch["voice_tail_master_enabled"])
        if "max_carousel_items" in patch:
            cur["max_carousel_items"] = max(1, min(6, int(patch["max_carousel_items"] or 3)))
        if "default_quiet_hours" in patch:
            cur["default_quiet_hours"] = _clean_text(patch["default_quiet_hours"], 16) or "22:00-07:30"
        self.db.save_ad_settings(cur)
        return cur

    @staticmethod
    def normalize_campaign_payload(
        payload: Dict[str, Any],
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = dict(existing or {})
        cid = _clean_text(payload.get("campaign_id") or base.get("campaign_id"), 40)
        if not cid:
            cid = f"AD_{datetime.datetime.now().strftime('%m%d')}_{secrets.token_hex(3).upper()}"
        cid = re.sub(r"[^A-Za-z0-9_-]+", "_", cid)[:40]

        title = _clean_text(payload.get("title", base.get("title", "")), 64)
        if not title:
            raise ValueError("推广标题不能为空")

        subtitle = _clean_text(payload.get("subtitle", base.get("subtitle", "")), 140)
        advertiser = _clean_text(
            payload.get("advertiser_name", base.get("advertiser_name", "")), 64
        ) or "社区合作商户"

        cat = _clean_text(payload.get("category", base.get("category", "car_wash")), 32).lower()
        if cat not in AD_CATEGORY_MAP:
            cat = "car_wash"
        cat_meta = AD_CATEGORY_MAP[cat]

        billing = _clean_text(
            payload.get("billing_model", base.get("billing_model", "CPS")), 16
        ).upper()
        if billing not in AD_BILLING_MODELS:
            billing = "CPS"

        try:
            unit_price = max(0.0, round(float(payload.get("unit_price", base.get("unit_price", 0.0)) or 0.0), 2))
        except (TypeError, ValueError):
            unit_price = 0.0
        if billing == "PUBLIC":
            unit_price = 0.0

        default_tier = 1 if billing == "PUBLIC" else (3 if cat == "ad_network" else 2)
        try:
            priority_tier = int(payload.get("priority_tier", base.get("priority_tier", default_tier)) or default_tier)
        except (TypeError, ValueError):
            priority_tier = default_tier
        priority_tier = max(1, min(3, priority_tier))

        try:
            weight = int(payload.get("weight", base.get("weight", 5)) or 5)
        except (TypeError, ValueError):
            weight = 5
        weight = max(1, min(10, weight))

        enabled = bool(payload.get("enabled", base.get("enabled", True)))

        # Placements across :18790, :18791 Web/PWA, Siri QuickLook/Card, and Voice Tail
        prev_pl = dict(base.get("placements") or {})
        raw_pl = payload.get("placements") if isinstance(payload.get("placements"), dict) else {}
        merged_pl = {**prev_pl, **raw_pl}
        p18790_layout = str(merged_pl.get("p18790_layout") or "capsule").strip().lower()
        if p18790_layout not in ("capsule", "card", "video", "carousel"):
            p18790_layout = "capsule"
        p18791_layout = str(merged_pl.get("p18791_layout") or "portal_card").strip().lower()
        if p18791_layout not in ("capsule", "portal_card", "video", "ai_only"):
            p18791_layout = "portal_card"

        placements = {
            "p18790_enabled": bool(merged_pl.get("p18790_enabled", True)),
            "p18790_layout": p18790_layout,
            "p18791_web_enabled": bool(merged_pl.get("p18791_web_enabled", True)),
            "p18791_layout": p18791_layout,
            "siri_hud_enabled": bool(merged_pl.get("siri_hud_enabled", True)),
            "ai_recommend_enabled": bool(merged_pl.get("ai_recommend_enabled", True)),
            "voice_tail_enabled": bool(merged_pl.get("voice_tail_enabled", False)),
            "voice_tail_text": _clean_text(merged_pl.get("voice_tail_text", ""), 28),
            "voice_quiet_hours": _clean_text(merged_pl.get("voice_quiet_hours", "22:00-07:30"), 16) or "22:00-07:30",
        }

        # Creative assets & CTA actions
        prev_cr = dict(base.get("creative") or {})
        raw_cr = payload.get("creative") if isinstance(payload.get("creative"), dict) else {}
        merged_cr = {**prev_cr, **raw_cr}

        action_type = str(merged_cr.get("action_type") or "coupon").strip().lower()
        if action_type not in ("coupon", "call", "copy", "lead_form", "url", "map_nav"):
            action_type = "coupon"

        if "image_data_url" in raw_cr:
            img_url = _compress_ad_image_data_url(str(raw_cr.get("image_data_url") or ""))
        else:
            img_url = str(prev_cr.get("image_data_url") or "")
        hud_thumb = _make_hud_micro_thumb(img_url) if img_url.startswith("data:image/") else ""

        nav_lat = merged_cr.get("nav_lat")
        nav_lon = merged_cr.get("nav_lon")
        try:
            nav_lat_f = round(float(nav_lat), 6) if nav_lat not in (None, "") else None
            nav_lon_f = round(float(nav_lon), 6) if nav_lon not in (None, "") else None
        except (TypeError, ValueError):
            nav_lat_f, nav_lon_f = None, None

        raw_prefix = re.sub(r"[^A-Za-z0-9]+", "", str(merged_cr.get("coupon_prefix") or "CP")).upper()[:4] or "CP"
        accent_col = _clean_text(merged_cr.get("accent_color"), 16)
        if not re.match(r"^#[0-9A-Fa-f]{6}$", accent_col):
            accent_col = cat_meta["accent"]

        creative = {
            "badge_text": _clean_text(
                merged_cr.get("badge_text"), 14
            ) or ("物业通知" if billing == "PUBLIC" else "业主礼遇"),
            "accent_color": accent_col,
            "icon": _clean_text(merged_cr.get("icon"), 8) or cat_meta["icon"],
            "image_data_url": img_url,
            "hud_thumb_data_url": hud_thumb,
            "video_url": _clean_text(merged_cr.get("video_url"), 400),
            "cta_text": _clean_text(merged_cr.get("cta_text"), 14) or "查看详情",
            "action_type": action_type,
            "offer_value": _clean_text(merged_cr.get("offer_value"), 18),
            "coupon_prefix": raw_prefix,
            "distance_hint": _clean_text(merged_cr.get("distance_hint"), 24),
            "target_url": _clean_text(merged_cr.get("target_url"), 400),
            "phone": _clean_text(merged_cr.get("phone"), 32),
            "wechat_id": _clean_text(merged_cr.get("wechat_id"), 48),
            "nav_name": _clean_text(merged_cr.get("nav_name"), 48),
            "nav_address": _clean_text(merged_cr.get("nav_address"), 80),
            "nav_lat": nav_lat_f,
            "nav_lon": nav_lon_f,
            "terms_note": _clean_text(merged_cr.get("terms_note"), 100),
        }

        # 4D Smart Targeting
        prev_tg = dict(base.get("targeting") or {})
        raw_tg = payload.get("targeting") if isinstance(payload.get("targeting"), dict) else {}
        merged_tg = {**prev_tg, **raw_tg}

        def _norm_str_list(val: Any, lower: bool = False) -> List[str]:
            if isinstance(val, str):
                parts = [p.strip() for p in re.split(r"[,，;\s]+", val) if p.strip()]
            elif isinstance(val, (list, tuple, set)):
                parts = [str(p or "").strip() for p in val if str(p or "").strip()]
            else:
                parts = []
            out_l: List[str] = []
            for p in parts:
                v = p.lower() if lower else p
                if v and v not in out_l:
                    out_l.append(v)
            return out_l

        trig = str(merged_tg.get("trigger_mode") or "always").strip().lower()
        if trig not in ("always", "when_full", "when_tag"):
            trig = "always"

        start_date = _clean_text(merged_tg.get("start_date"), 10)
        end_date = _clean_text(merged_tg.get("end_date"), 10)
        if start_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date):
            start_date = ""
        if end_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
            end_date = ""

        targeting = {
            "start_date": start_date,
            "end_date": end_date,
            "time_windows": _norm_str_list(merged_tg.get("time_windows"))[:6],
            "seasons": [
                s for s in _norm_str_list(merged_tg.get("seasons"), lower=True)
                if s in ("spring", "summer", "autumn", "winter")
            ],
            "trigger_mode": trig,
            "vehicle_types": [
                v for v in _norm_str_list(merged_tg.get("vehicle_types"), lower=True)
                if v in ("sedan", "suv", "compact", "ev")
            ],
            "spot_tags": _norm_str_list(merged_tg.get("spot_tags"))[:8],
            "gates": _norm_str_list(merged_tg.get("gates"), lower=True)[:8],
            "units": _norm_str_list(merged_tg.get("units"))[:16],
            "ai_keywords": _norm_str_list(merged_tg.get("ai_keywords"))[:12],
        }

        partner_token = _clean_text(base.get("partner_token"), 48)
        if not partner_token or bool(payload.get("rotate_partner_token")):
            partner_token = secrets.token_urlsafe(14)

        return {
            "campaign_id": cid,
            "title": title,
            "subtitle": subtitle,
            "advertiser_name": advertiser,
            "category": cat,
            "billing_model": billing,
            "unit_price": unit_price,
            "priority_tier": priority_tier,
            "weight": weight,
            "enabled": enabled,
            "placements": placements,
            "creative": creative,
            "targeting": targeting,
            "partner_token": partner_token,
        }

    def save_campaign(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cid_hint = _clean_text(payload.get("campaign_id"), 40)
        existing = self.db.get_ad_campaign(cid_hint) if cid_hint else None
        norm = self.normalize_campaign_payload(payload, existing=existing)
        saved = self.db.upsert_ad_campaign(norm)
        return self._attach_settlement_to_campaign(saved, self.db.get_ad_stats_by_campaign(days=30))

    def toggle_campaign(self, campaign_id: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
        cur = self.db.get_ad_campaign(campaign_id)
        if not cur:
            raise ValueError(f"未找到推广计划: {campaign_id}")
        next_en = (not bool(cur.get("enabled"))) if enabled is None else bool(enabled)
        cur["enabled"] = next_en
        saved = self.db.upsert_ad_campaign(cur)
        return self._attach_settlement_to_campaign(saved, self.db.get_ad_stats_by_campaign(days=30))

    def delete_campaign(self, campaign_id: str) -> bool:
        return self.db.delete_ad_campaign(campaign_id)

    def rotate_partner_token(self, campaign_id: str) -> str:
        cur = self.db.get_ad_campaign(campaign_id)
        if not cur:
            return ""
        new_tok = secrets.token_urlsafe(14)
        cur["partner_token"] = new_tok
        self.db.upsert_ad_campaign(cur)
        return new_tok

    def resolve_redirect_url(
        self,
        campaign_id: str,
        surface: str = "18790_mobile",
        platform: str = "desktop_web",
        client_ip: str = "local",
        user_agent: str = "",
        external_uid: str = "",
    ) -> str:
        camp = self.db.get_ad_campaign(campaign_id)
        if not camp:
            return ""
        cr = camp.get("creative") or {}
        raw_target = str(cr.get("target_url") or "").strip()
        if not raw_target and cr.get("nav_name"):
            # Fallback to mobile-friendly Amap H5 marker/search URL when redirect_path is tapped directly
            from urllib.parse import quote as _url_quote

            if cr.get("nav_lat") is not None and cr.get("nav_lon") is not None:
                raw_target = (
                    f"https://uri.amap.com/marker?position={cr['nav_lon']},{cr['nav_lat']}"
                    f"&name={_url_quote(str(cr['nav_name']))}&src=community_parking"
                )
            else:
                raw_target = f"https://uri.amap.com/search?keyword={_url_quote(str(cr['nav_name']))}&src=community_parking"
        if not raw_target:
            return ""
        self.record_event(
            campaign_id=campaign_id,
            event_type="click",
            platform=platform,
            surface=surface,
            client_ip=client_ip,
            user_agent=user_agent,
            external_uid=external_uid,
        )
        return append_utm_params(raw_target, campaign_id, platform, surface) or raw_target

    # ------------------------------------------------------------------
    # 4D Targeting, Waterfall Priority & Public Projection
    # ------------------------------------------------------------------

    @staticmethod
    def matches_targeting(
        campaign: Dict[str, Any],
        unit_id: str = "",
        vehicle_type: str = "sedan",
        tag_filter: str = "",
        entry_gate_id: str = "",
        total_effective_free: int = 1,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Tuple[bool, float]:
        """Evaluate 4D targeting rules and return `(matched, relevance_bonus_score)`."""
        if not campaign.get("enabled"):
            return False, 0.0

        dt = now_dt or datetime.datetime.now()
        today_iso = dt.date().isoformat()
        tg = campaign.get("targeting") or {}

        s_date = str(tg.get("start_date") or "").strip()
        e_date = str(tg.get("end_date") or "").strip()
        if s_date and today_iso < s_date:
            return False, 0.0
        if e_date and today_iso > e_date:
            return False, 0.0

        seasons = tg.get("seasons") or []
        if seasons and _current_season(dt.date()) not in seasons:
            return False, 0.0

        t_windows = tg.get("time_windows") or []
        if t_windows:
            now_mins = dt.hour * 60 + dt.minute
            if not any(_is_within_hhmm_window(now_mins, w) for w in t_windows):
                return False, 0.0

        bonus = 0.0
        trig = str(tg.get("trigger_mode") or "always").lower()
        is_full = int(total_effective_free or 0) <= 0
        if trig == "when_full":
            if not is_full:
                return False, 0.0
            bonus += 120.0
        elif is_full and campaign.get("category") == "overflow_parking":
            bonus += 90.0

        v_types = tg.get("vehicle_types") or []
        s_tags = tg.get("spot_tags") or []
        v_clean = str(vehicle_type or "sedan").lower()
        t_clean = str(tag_filter or "").strip()

        if trig == "when_tag":
            tag_hit = bool(t_clean and t_clean in s_tags)
            veh_hit = bool(v_types and v_clean in v_types)
            if not (tag_hit or veh_hit):
                return False, 0.0
            bonus += 65.0
        else:
            if v_types:
                # Allow EV-targeted ads to match when user filters `ev_charging` even if vehicle dropdown is default
                ev_tag_equiv = ("ev" in v_types and t_clean == "ev_charging")
                if v_clean not in v_types and not ev_tag_equiv:
                    return False, 0.0
                bonus += 25.0
            if s_tags and t_clean and t_clean in s_tags:
                bonus += 35.0

        gates = tg.get("gates") or []
        g_clean = str(entry_gate_id or "").strip().lower()
        if gates:
            if not g_clean or g_clean == "auto" or g_clean not in gates:
                return False, 0.0
            bonus += 20.0

        units_rule = tg.get("units") or []
        u_clean = str(unit_id or "").strip()
        if units_rule:
            if not u_clean:
                return False, 0.0
            u_bldg = u_clean.split("-")[0]
            matched_u = any(
                u_clean == ur or u_clean.startswith(ur) or u_bldg == ur.rstrip("-#栋号楼")
                for ur in units_rule
            )
            if not matched_u:
                return False, 0.0
            bonus += 25.0

        return True, bonus

    @staticmethod
    def to_public_ad(
        campaign: Dict[str, Any],
        surface: str,
        include_full_image: bool = True,
    ) -> Dict[str, Any]:
        """Sanitize campaign into resident-safe payload (never exposes unit_price or partner_token)."""
        cr = campaign.get("creative") or {}
        pl = campaign.get("placements") or {}
        cat_code = str(campaign.get("category") or "car_wash")
        cat_meta = AD_CATEGORY_MAP.get(cat_code, AD_CATEGORIES[0])
        cid = campaign["campaign_id"]
        layout = (
            pl.get("p18790_layout", "capsule")
            if surface.startswith("18790")
            else pl.get("p18791_layout", "portal_card")
        )
        return {
            "campaign_id": cid,
            "title": campaign["title"],
            "subtitle": campaign.get("subtitle", ""),
            "advertiser_name": campaign.get("advertiser_name", ""),
            "category": cat_code,
            "category_label": cat_meta["label"],
            "billing_model": campaign.get("billing_model", "CPS"),
            "is_public_notice": campaign.get("billing_model") == "PUBLIC",
            "priority_tier": int(campaign.get("priority_tier") or 2),
            "layout": layout,
            "badge_text": cr.get("badge_text") or "业主礼遇",
            "accent_color": cr.get("accent_color") or cat_meta["accent"],
            "icon": cr.get("icon") or cat_meta["icon"],
            "image_data_url": (cr.get("image_data_url") or "") if include_full_image else "",
            "hud_thumb_data_url": cr.get("hud_thumb_data_url") or "",
            "video_url": cr.get("video_url") or "",
            "cta_text": cr.get("cta_text") or "查看详情",
            "action_type": cr.get("action_type") or "coupon",
            "offer_value": cr.get("offer_value") or "",
            "distance_hint": cr.get("distance_hint") or "",
            "phone": cr.get("phone") or "",
            "wechat_id": cr.get("wechat_id") or "",
            "nav_name": cr.get("nav_name") or "",
            "nav_address": cr.get("nav_address") or "",
            "nav_lat": cr.get("nav_lat"),
            "nav_lon": cr.get("nav_lon"),
            "terms_note": cr.get("terms_note") or "",
            "redirect_path": f"/r/{cid}",
        }

    def _select_waterfall_campaigns(
        self,
        surface_filter_fn: Any,
        unit_id: str = "",
        vehicle_type: str = "sedan",
        tag_filter: str = "",
        entry_gate_id: str = "",
        total_effective_free: int = 1,
        limit: int = 3,
        now_dt: Optional[datetime.datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Rank eligible campaigns by Waterfall Tier (1->2->3), Contextual Bonus, and Weighted Rotation."""
        settings = self.get_settings()
        if not settings.get("ads_enabled", True):
            return []
        all_camps = self.db.list_ad_campaigns(only_enabled=True)
        if not all_camps:
            return []

        scored: List[Tuple[int, float, Dict[str, Any]]] = []
        for camp in all_camps:
            if not surface_filter_fn(camp):
                continue
            ok, bonus = self.matches_targeting(
                camp,
                unit_id=unit_id,
                vehicle_type=vehicle_type,
                tag_filter=tag_filter,
                entry_gate_id=entry_gate_id,
                total_effective_free=total_effective_free,
                now_dt=now_dt,
            )
            if not ok:
                continue
            tier = int(camp.get("priority_tier") or 2)
            # Contextual overflow/tag match promotes urgency ahead of generic tier-2 ads
            eff_tier = 0 if bonus >= 90.0 else tier
            w = max(1, int(camp.get("weight") or 5))
            # Smooth weighted jitter so multiple equal-tier advertisers share impressions fairly proportional to weight
            jitter = random.random() * float(w) * 4.0
            scored.append((eff_tier, -(bonus + w * 6.0 + jitter), camp))

        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], item[1]))
        # Waterfall rule: Tier 3 (ad network fallback) only serves if no Tier 0/1/2 direct/notice ads matched,
        # OR to fill remaining carousel slots when allowed.
        top_tier = scored[0][0]
        if top_tier <= 2:
            primary_pool = [c for t, _, c in scored if t <= 2]
            fallback_pool = [c for t, _, c in scored if t > 2]
            ordered = primary_pool + fallback_pool
        else:
            ordered = [c for _, _, c in scored]
        return ordered[: max(1, limit)]

    def select_for_mobile_18790(
        self,
        unit_id: str = "",
        vehicle_type: str = "sedan",
        tag_filter: str = "",
        entry_gate_id: str = "",
        total_effective_free: int = 1,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        settings = self.get_settings()
        max_items = int(settings.get("max_carousel_items") or 3)
        matched = self._select_waterfall_campaigns(
            surface_filter_fn=lambda c: bool((c.get("placements") or {}).get("p18790_enabled", True)),
            unit_id=unit_id,
            vehicle_type=vehicle_type,
            tag_filter=tag_filter,
            entry_gate_id=entry_gate_id,
            total_effective_free=total_effective_free,
            limit=max_items,
            now_dt=now_dt,
        )
        if not matched:
            return {"enabled": bool(settings.get("ads_enabled", True)), "items": [], "primary": None}
        pub_items = [self.to_public_ad(c, surface="18790_mobile", include_full_image=True) for c in matched]
        return {
            "enabled": True,
            "primary": pub_items[0],
            "items": pub_items,
        }

    def select_for_voice_18791(
        self,
        unit_id: str = "",
        vehicle_type: str = "sedan",
        tag_filter: str = "",
        total_effective_free: int = 1,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        settings = self.get_settings()
        if not settings.get("ads_enabled", True):
            return {"enabled": False, "live_capsule": None, "portal_items": []}
        web_matched = self._select_waterfall_campaigns(
            surface_filter_fn=lambda c: bool((c.get("placements") or {}).get("p18791_web_enabled", True)),
            unit_id=unit_id,
            vehicle_type=vehicle_type,
            tag_filter=tag_filter,
            total_effective_free=total_effective_free,
            limit=4,
            now_dt=now_dt,
        )
        pub_list = [self.to_public_ad(c, surface="18791_web", include_full_image=True) for c in web_matched]
        return {
            "enabled": True,
            "live_capsule": pub_list[0] if pub_list else None,
            "portal_items": pub_list,
        }

    def select_for_siri_execution(
        self,
        unit_id: str = "",
        vehicle_type: str = "sedan",
        tag_filter: str = "",
        total_effective_free: int = 1,
        include_voice_tail: bool = True,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Select ad for Siri Shortcut post-execution surfaces (`both`, `map`, `text`, `silent`)."""
        settings = self.get_settings()
        if not settings.get("ads_enabled", True):
            return {"hud_ad": None, "voice_tail_ad": None, "voice_tail_text": ""}
        dt = now_dt or datetime.datetime.now()

        hud_list = self._select_waterfall_campaigns(
            surface_filter_fn=lambda c: bool((c.get("placements") or {}).get("siri_hud_enabled", True)),
            unit_id=unit_id,
            vehicle_type=vehicle_type,
            tag_filter=tag_filter,
            total_effective_free=total_effective_free,
            limit=1,
            now_dt=dt,
        )
        hud_ad = (
            self.to_public_ad(hud_list[0], surface="18791_siri_hud", include_full_image=False)
            if hud_list
            else None
        )

        voice_tail_ad = None
        voice_tail_text = ""
        if include_voice_tail and settings.get("voice_tail_master_enabled", True):
            now_mins = dt.hour * 60 + dt.minute

            def _voice_ok(camp: Dict[str, Any]) -> bool:
                pl = camp.get("placements") or {}
                if not pl.get("voice_tail_enabled") or not str(pl.get("voice_tail_text") or "").strip():
                    return False
                quiet = str(pl.get("voice_quiet_hours") or settings.get("default_quiet_hours") or "22:00-07:30")
                if quiet and _is_within_hhmm_window(now_mins, quiet):
                    return False
                return True

            v_list = self._select_waterfall_campaigns(
                surface_filter_fn=_voice_ok,
                unit_id=unit_id,
                vehicle_type=vehicle_type,
                tag_filter=tag_filter,
                total_effective_free=total_effective_free,
                limit=1,
                now_dt=dt,
            )
            if v_list:
                voice_tail_ad = v_list[0]
                voice_tail_text = str((voice_tail_ad.get("placements") or {}).get("voice_tail_text") or "").strip()

        return {
            "hud_ad": hud_ad,
            "voice_tail_ad": voice_tail_ad,
            "voice_tail_text": voice_tail_text,
        }

    def match_ad_for_question(
        self,
        question: str,
        unit_id: str = "",
        vehicle_type: str = "sedan",
        total_effective_free: int = 1,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Match a contextual service/partner card ONLY when user's voice question expresses matching intent."""
        settings = self.get_settings()
        if not settings.get("ads_enabled", True):
            return None
        q = str(question or "").strip().lower()
        if not q:
            return None

        candidates = self._select_waterfall_campaigns(
            surface_filter_fn=lambda c: bool((c.get("placements") or {}).get("ai_recommend_enabled", True)),
            unit_id=unit_id,
            vehicle_type=vehicle_type,
            total_effective_free=total_effective_free,
            limit=8,
            now_dt=now_dt,
        )
        for camp in candidates:
            cat_meta = AD_CATEGORY_MAP.get(str(camp.get("category") or ""), {})
            cat_kws = tuple(cat_meta.get("ai_kw") or ())
            custom_kws = tuple((camp.get("targeting") or {}).get("ai_keywords") or ())
            all_kws = [str(k).lower() for k in (*cat_kws, *custom_kws) if str(k).strip()]
            if any(kw in q for kw in all_kws):
                return self.to_public_ad(camp, surface="18791_ai_ask", include_full_image=True)
        return None

    # ------------------------------------------------------------------
    # Event Tracking, Anti-Spam Attribution, Coupons & CPA Leads
    # ------------------------------------------------------------------

    def record_event(
        self,
        campaign_id: str,
        event_type: str,
        platform: str = "desktop_web",
        surface: str = "18790_mobile",
        client_ip: str = "local",
        user_agent: str = "",
        external_uid: str = "",
        unit_id: str = "",
        vehicle_type: str = "sedan",
        lead_payload: Optional[Dict[str, Any]] = None,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        cid = _clean_text(campaign_id, 40)
        camp = self.db.get_ad_campaign(cid)
        if not camp:
            raise ValueError(f"推广计划不存在: {cid}")

        ev_aliases = {
            "coupon_claim": "issue_coupon",
            "claim_coupon": "issue_coupon",
            "lead_submit": "submit_lead",
            "lead": "submit_lead",
            "map_nav": "navigate",
        }
        ev = ev_aliases.get(str(event_type or "impression").strip().lower(), str(event_type or "impression").strip().lower())
        valid_events = {
            "impression",
            "audio_play",
            "video_view",
            "click",
            "call",
            "copy",
            "navigate",
            "issue_coupon",
            "submit_lead",
        }
        if ev not in valid_events:
            raise ValueError(f"不支持的事件类型: {ev}")

        ts = float(now_ts if now_ts is not None else time.time())
        stat_date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        plat = detect_client_platform(user_agent, explicit_hint=platform)
        surf = _clean_text(surface, 32) or "18790_mobile"
        fp = self._client_fingerprint(client_ip, user_agent, external_uid)

        dedup_skipped = False
        is_new_uv = False
        issued_coupon: Optional[Dict[str, Any]] = None
        saved_lead: Optional[Dict[str, Any]] = None

        with self._lock:
            if ev == "impression":
                # 30-second anti-polling guard per surface + 30-minute UV deduplication
                imp_key = f"{fp}|{cid}|{surf}"
                if self._check_dedup_locked(self._impression_dedup, imp_key, 30.0, ts):
                    dedup_skipped = True
                else:
                    uv_key = f"{fp}|{cid}|{stat_date}"
                    is_new_uv = not self._check_dedup_locked(self._uv_dedup, uv_key, 1800.0, ts)
            elif ev == "click":
                clk_key = f"{fp}|{cid}|{surf}"
                if self._check_dedup_locked(self._click_dedup, clk_key, 60.0, ts):
                    dedup_skipped = True

        if dedup_skipped:
            return {"ok": True, "campaign_id": cid, "event": ev, "deduplicated": True}

        deltas: Dict[str, int] = {}
        if ev == "impression":
            deltas["impressions"] = 1
            if is_new_uv:
                deltas["unique_visitors"] = 1
        elif ev == "audio_play":
            deltas["audio_plays"] = 1
            with self._lock:
                uv_key = f"{fp}|{cid}|{stat_date}"
                if not self._check_dedup_locked(self._uv_dedup, uv_key, 1800.0, ts):
                    deltas["unique_visitors"] = 1
        elif ev == "video_view":
            deltas["video_views"] = 1
        elif ev == "click":
            deltas["clicks"] = 1
        elif ev == "call":
            deltas["clicks"] = 1
            deltas["calls"] = 1
        elif ev == "copy":
            deltas["clicks"] = 1
            deltas["copies"] = 1
        elif ev == "navigate":
            deltas["clicks"] = 1
            deltas["navigations"] = 1
        elif ev == "issue_coupon":
            cp_dedup_key = f"{fp}|{cid}"
            with self._lock:
                cached_code = self._coupon_dedup.get(cp_dedup_key)
            existing_cp = self.db.get_ad_coupon(cached_code) if cached_code else None
            if existing_cp and existing_cp.get("status") == "issued" and (ts - float(existing_cp.get("issued_at") or 0)) < 3600.0:
                issued_coupon = existing_cp
            else:
                prefix = str((camp.get("creative") or {}).get("coupon_prefix") or "CP").upper()[:4]
                mmdd = datetime.datetime.fromtimestamp(ts).strftime("%m%d")
                code_str = f"{prefix}-{mmdd}-{secrets.randbelow(9000) + 1000}"
                issued_coupon = self.db.create_ad_coupon(
                    coupon_code=code_str,
                    campaign_id=cid,
                    unit_id=_clean_text(unit_id, 24),
                    platform=plat,
                    issued_at=ts,
                )
                with self._lock:
                    self._coupon_dedup[cp_dedup_key] = code_str
                    while len(self._coupon_dedup) > self.MAX_DEDUP_ENTRIES:
                        self._coupon_dedup.popitem(last=False)
                deltas["clicks"] = 1
                deltas["coupons_issued"] = 1
        elif ev == "submit_lead":
            lp = lead_payload or {}
            phone = _clean_text(lp.get("phone") or lp.get("contact_phone"), 32)
            if not re.search(r"\d{7,15}", phone):
                raise ValueError("请输入有效的联系手机号")
            saved_lead = self.db.create_ad_lead(
                campaign_id=cid,
                contact_phone=phone,
                contact_name=_clean_text(lp.get("name") or lp.get("contact_name"), 32),
                unit_id=_clean_text(lp.get("unit_id") or unit_id, 24),
                vehicle_type=_clean_text(lp.get("vehicle_type") or vehicle_type, 16),
                remark=_clean_text(lp.get("remark"), 80),
                platform=plat,
                created_at=ts,
            )
            deltas["clicks"] = 1
            deltas["leads_submitted"] = 1

        if deltas:
            self.db.increment_ad_daily_stats(
                campaign_id=cid,
                stat_date=stat_date,
                platform=plat,
                surface=surf,
                deltas=deltas,
            )

        out: Dict[str, Any] = {
            "ok": True,
            "campaign_id": cid,
            "event": ev,
            "deduplicated": False,
            "platform": plat,
        }
        if issued_coupon:
            out["coupon"] = issued_coupon
        if saved_lead:
            out["lead"] = saved_lead
        return out

    def redeem_coupon(self, coupon_code: str, note: str = "", partner_campaign_id: str = "") -> Dict[str, Any]:
        clean_code = _clean_text(coupon_code, 32).upper()
        if not clean_code:
            raise ValueError("请输入核销码")
        cp = self.db.get_ad_coupon(clean_code)
        if not cp:
            raise ValueError(f"未找到核销码：{clean_code}")
        if partner_campaign_id and cp["campaign_id"] != partner_campaign_id:
            raise ValueError("该核销码不属于当前商户推广计划")
        if cp.get("status") == "redeemed":
            return {
                "ok": True,
                "already_redeemed": True,
                "coupon": cp,
                "message": f"该核销码已于此前核销（{clean_code}）",
            }
        updated = self.db.mark_ad_coupon_redeemed(clean_code, note=_clean_text(note, 64))
        today_str = datetime.date.today().isoformat()
        self.db.increment_ad_daily_stats(
            campaign_id=cp["campaign_id"],
            stat_date=today_str,
            platform=cp.get("platform") or "store_pos",
            surface="offline_redeem",
            deltas={"coupons_redeemed": 1},
        )
        return {
            "ok": True,
            "already_redeemed": False,
            "coupon": updated,
            "message": f"✅ 核销成功：{clean_code}",
        }

    # ------------------------------------------------------------------
    # Settlement Calculation, Admin Overview, CSV & Partner Report
    # ------------------------------------------------------------------

    @staticmethod
    def compute_campaign_settlement(
        campaign: Dict[str, Any],
        agg_stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        billing = str(campaign.get("billing_model") or "CPS").upper()
        price = float(campaign.get("unit_price") or 0.0)
        imp = int(agg_stats.get("impressions") or 0)
        uv = int(agg_stats.get("unique_visitors") or 0)
        aud = int(agg_stats.get("audio_plays") or 0)
        vid = int(agg_stats.get("video_views") or 0)
        clk = int(agg_stats.get("clicks") or 0)
        calls = int(agg_stats.get("calls") or 0)
        copies = int(agg_stats.get("copies") or 0)
        navs = int(agg_stats.get("navigations") or 0)
        c_iss = int(agg_stats.get("coupons_issued") or 0)
        c_red = int(agg_stats.get("coupons_redeemed") or 0)
        leads = int(agg_stats.get("leads_submitted") or 0)
        active_days = int(agg_stats.get("active_days") or (1 if (imp + aud + clk) > 0 else 0))

        total_reach = imp + aud
        ctr_pct = round((clk / float(imp) * 100.0), 2) if imp > 0 else 0.0
        redeem_rate_pct = round((c_red / float(c_iss) * 100.0), 1) if c_iss > 0 else 0.0
        cpa_actions = leads + calls + copies

        if billing == "CPT":
            billable_units = max(1, active_days) if (active_days > 0 or campaign.get("enabled")) else 0
            amount = round(billable_units * price, 2)
            formula = f"在线履约 {billable_units} 天 × ¥{price:g}/天"
        elif billing == "CPM":
            billable_units = total_reach
            amount = round((total_reach / 1000.0) * price, 2)
            formula = f"有效触达 {total_reach} 次(画面{imp}+语音{aud}) ÷ 1000 × ¥{price:g}"
        elif billing == "CPC":
            billable_units = clk
            amount = round(clk * price, 2)
            formula = f"去重有效点击 {clk} 次 × ¥{price:g}/次"
        elif billing == "CPA":
            billable_units = cpa_actions
            amount = round(cpa_actions * price, 2)
            formula = f"有效线索/咨询 {cpa_actions} 次(留资{leads}+拨号{calls}+复制{copies}) × ¥{price:g}"
        elif billing == "CPS":
            billable_units = c_red
            amount = round(c_red * price, 2)
            formula = f"到店实锤核销 {c_red} 单(领券{c_iss}张) × ¥{price:g}/单"
        else:
            billable_units = 0
            amount = 0.0
            formula = "物业/社区免费公益通知"

        return {
            "impressions": imp,
            "unique_visitors": uv,
            "audio_plays": aud,
            "video_views": vid,
            "total_exposures": total_reach,
            "clicks": clk,
            "ctr_pct": ctr_pct,
            "calls": calls,
            "copies": copies,
            "navigations": navs,
            "coupons_issued": c_iss,
            "coupons_redeemed": c_red,
            "redeem_rate_pct": redeem_rate_pct,
            "leads_submitted": leads,
            "cpa_actions": cpa_actions,
            "active_days": active_days,
            "billable_units": billable_units,
            "estimated_amount": amount,
            "settlement_formula": formula,
            "by_platform": agg_stats.get("by_platform") or {},
        }

    def _attach_settlement_to_campaign(
        self, campaign: Dict[str, Any], stats_map: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        cid = campaign["campaign_id"]
        st = stats_map.get(cid, {})
        settlement = self.compute_campaign_settlement(campaign, st)
        return {**campaign, "stats": settlement}

    def build_admin_overview(self, days: int = 30) -> Dict[str, Any]:
        clean_days = max(1, min(365, int(days or 30)))
        settings = self.get_settings()
        campaigns_raw = self.db.list_ad_campaigns(only_enabled=False)
        stats_map = self.db.get_ad_stats_by_campaign(days=clean_days)
        campaigns = [self._attach_settlement_to_campaign(c, stats_map) for c in campaigns_raw]

        totals = {
            "campaigns_total": len(campaigns),
            "campaigns_active": sum(1 for c in campaigns if c.get("enabled")),
            "impressions": sum(c["stats"]["impressions"] for c in campaigns),
            "unique_visitors": sum(c["stats"]["unique_visitors"] for c in campaigns),
            "audio_plays": sum(c["stats"]["audio_plays"] for c in campaigns),
            "video_views": sum(c["stats"]["video_views"] for c in campaigns),
            "clicks": sum(c["stats"]["clicks"] for c in campaigns),
            "conversions": sum(
                c["stats"]["coupons_redeemed"] + c["stats"]["leads_submitted"] + c["stats"]["calls"]
                for c in campaigns
            ),
            "coupons_issued": sum(c["stats"]["coupons_issued"] for c in campaigns),
            "coupons_redeemed": sum(c["stats"]["coupons_redeemed"] for c in campaigns),
            "leads_submitted": sum(c["stats"]["leads_submitted"] for c in campaigns),
            "estimated_revenue": round(sum(c["stats"]["estimated_amount"] for c in campaigns), 2),
        }
        totals["ctr_pct"] = (
            round(totals["clicks"] / float(totals["impressions"]) * 100.0, 2)
            if totals["impressions"] > 0
            else 0.0
        )

        return {
            "ok": True,
            "workspace_id": self._ws_id(),
            "days": clean_days,
            "settings": settings,
            "summary": totals,
            "campaigns": campaigns,
            "recent_leads": self.db.list_ad_leads(limit=30),
            "recent_coupons": self.db.list_ad_coupons(limit=30),
            "billing_models": list(AD_BILLING_MODELS.values()),
            "categories": AD_CATEGORIES,
            "industry_presets": AD_INDUSTRY_PRESETS,
        }

    def export_reconciliation_csv(self, campaign_id: str = "", days: int = 30) -> str:
        clean_days = max(1, min(365, int(days or 30)))
        campaigns_raw = self.db.list_ad_campaigns(only_enabled=False)
        if campaign_id:
            campaigns_raw = [c for c in campaigns_raw if c["campaign_id"] == campaign_id]
        stats_map = self.db.get_ad_stats_by_campaign(days=clean_days)

        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM for direct Excel Chinese compatibility
        writer = csv.writer(buf)
        writer.writerow(
            [
                "计划ID",
                "合作商户",
                "推广标题",
                "场景分类",
                "计费模式",
                "合作单价(¥)",
                f"统计周期(近{clean_days}天)在线天数",
                "画面有效曝光(PV)",
                "去重独立车主(UV)",
                "语音冠名播报(次)",
                "微视频有效播放(次)",
                "去重有效点击(次)",
                "点击率(CTR%)",
                "拨号咨询(次)",
                "复制微信/口令(次)",
                "地图导航(次)",
                "领券数(张)",
                "到店核销数(单)",
                "留资预约单(条)",
                "应收结算金额(¥)",
                "对账核算口径说明",
            ]
        )
        for c in campaigns_raw:
            st = self.compute_campaign_settlement(c, stats_map.get(c["campaign_id"], {}))
            cat_lbl = AD_CATEGORY_MAP.get(c["category"], {}).get("label", c["category"])
            writer.writerow(
                [
                    c["campaign_id"],
                    c["advertiser_name"],
                    c["title"],
                    cat_lbl,
                    c["billing_model"],
                    c["unit_price"],
                    st["active_days"],
                    st["impressions"],
                    st["unique_visitors"],
                    st["audio_plays"],
                    st["video_views"],
                    st["clicks"],
                    st["ctr_pct"],
                    st["calls"],
                    st["copies"],
                    st["navigations"],
                    st["coupons_issued"],
                    st["coupons_redeemed"],
                    st["leads_submitted"],
                    f"{st['estimated_amount']:.2f}",
                    st["settlement_formula"],
                ]
            )
        return buf.getvalue()

    def get_partner_report_payload(
        self, campaign_id: str, provided_token: str, days: int = 30
    ) -> Dict[str, Any]:
        camp = self.db.get_ad_campaign(campaign_id)
        if not camp:
            raise LookupError("未找到对应的推广计划")
        expected = str(camp.get("partner_token") or "")
        if not expected or not provided_token or not hmac.compare_digest(expected, str(provided_token).strip()):
            raise PermissionError("商户对账访问凭证无效或已过期")

        clean_days = max(1, min(365, int(days or 30)))
        stats_map = self.db.get_ad_stats_by_campaign(days=clean_days)
        settlement = self.compute_campaign_settlement(camp, stats_map.get(campaign_id, {}))
        daily_rows = self.db.list_ad_daily_rows(campaign_id=campaign_id, days=clean_days)
        coupons = self.db.list_ad_coupons(campaign_id=campaign_id, limit=40)
        # Mask middle digits of resident phone numbers on read-only partner view if needed, or show masked lead summary
        leads_raw = self.db.list_ad_leads(campaign_id=campaign_id, limit=40)
        return {
            "ok": True,
            "days": clean_days,
            "campaign": {
                "campaign_id": camp["campaign_id"],
                "title": camp["title"],
                "subtitle": camp.get("subtitle", ""),
                "advertiser_name": camp.get("advertiser_name", ""),
                "category": camp.get("category", ""),
                "billing_model": camp.get("billing_model", ""),
                "unit_price": camp.get("unit_price", 0.0),
                "enabled": bool(camp.get("enabled")),
            },
            "settlement": settlement,
            "daily_rows": daily_rows,
            "coupons": coupons,
            "leads": leads_raw,
        }

    def render_partner_report_html(
        self, community_name: str, campaign_id: str, provided_token: str, days: int = 30
    ) -> str:
        """Render a standalone Apple-crafted mobile-friendly HTML reconciliation & coupon redemption page for merchants."""
        data = self.get_partner_report_payload(campaign_id, provided_token, days=days)
        camp = data["campaign"]
        st = data["settlement"]
        bm_info = AD_BILLING_MODELS.get(camp["billing_model"], AD_BILLING_MODELS["CPS"])
        esc = html.escape

        plat_badges = "".join(
            f'<span class="chip">{esc(k)}: 曝光 {v.get("impressions", 0)} / 点击 {v.get("clicks", 0)}</span>'
            for k, v in (st.get("by_platform") or {}).items()
        ) or '<span class="chip">等待首笔触达数据</span>'

        coupon_rows = "".join(
            f'<tr><td><code>{esc(cp["coupon_code"])}</code></td>'
            f'<td>{"✅ 已核销" if cp["status"] == "redeemed" else "⏳ 待到店"}</td>'
            f'<td>{esc(cp.get("unit_id") or "-")}</td></tr>'
            for cp in (data["coupons"] or [])[:15]
        ) or '<tr><td colspan="3" style="color:#86868b;text-align:center">暂无领券记录</td></tr>'

        lead_rows = "".join(
            f'<tr><td><strong>{esc(ld["contact_phone"])}</strong></td>'
            f'<td>{esc(ld.get("contact_name") or "业主")}</td>'
            f'<td>{esc(ld.get("unit_id") or "-")}</td></tr>'
            for ld in (data["leads"] or [])[:15]
        ) or '<tr><td colspan="3" style="color:#86868b;text-align:center">暂无预约线索</td></tr>'

        ws_id = self._ws_id()
        ws_qs = f"&ws={esc(ws_id)}" if ws_id and ws_id != "default" else ""

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"/>
<title>{esc(camp["advertiser_name"])} · 实时透明对账与核销台</title>
<style>
:root{{--bg:#f2f4f7;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--accent:#0071e3;--green:#117a27;--line:rgba(15,23,42,0.08)}}
*{{box-sizing:border-box}}
body{{margin:0;padding:16px 14px 40px;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif}}
.wrap{{max-width:680px;margin:0 auto;display:flex;flex-direction:column;gap:12px}}
.card{{background:var(--card);border:0.5px solid var(--line);border-radius:18px;padding:15px 16px;box-shadow:0 4px 18px rgba(15,23,42,0.05)}}
.eyebrow{{font-size:11.5px;font-weight:700;color:var(--accent)}}
h1{{margin:4px 0 6px;font-size:19px;letter-spacing:-0.02em}}
.sub{{font-size:12.5px;color:var(--muted);margin:0}}
.kpi-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}}
.kpi{{background:#f8fafc;border-radius:12px;padding:10px;border:0.5px solid var(--line)}}
.kpi span{{display:block;font-size:11px;color:var(--muted);font-weight:600}}
.kpi strong{{display:block;font-size:19px;font-weight:800;margin-top:3px;font-variant-numeric:tabular-nums}}
.bill-box{{margin-top:12px;padding:11px 13px;border-radius:12px;background:rgba(0,113,227,0.07);display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}
.bill-box em{{font-style:normal;font-size:21px;font-weight:800;color:var(--accent)}}
.chips{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}
.chip{{font-size:11.5px;padding:3px 9px;border-radius:999px;background:#f1f5f9;color:#334155;font-weight:600}}
.redeem-row{{display:flex;gap:8px;margin-top:8px}}
.redeem-row input{{flex:1;height:40px;padding:0 12px;border-radius:11px;border:1px solid rgba(15,23,42,0.16);font-size:15px;font-weight:700;text-transform:uppercase}}
.redeem-row button{{height:40px;padding:0 16px;border-radius:11px;border:none;background:var(--accent);color:#fff;font-size:14px;font-weight:700;cursor:pointer}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
th,td{{padding:7px 6px;border-bottom:0.5px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:600}}
</style>
</head>
<body>
<div class="wrap">
  <section class="card">
    <span class="eyebrow">{esc(community_name)} · 合作商户实时透明对账单（近 {data["days"]} 天）</span>
    <h1>{esc(camp["advertiser_name"])}</h1>
    <p class="sub">推广计划：{esc(camp["title"])} · 模式：{esc(bm_info["label"])}</p>
    <div class="kpi-grid">
      <div class="kpi"><span>有效曝光 PV</span><strong>{st["total_exposures"]}</strong></div>
      <div class="kpi"><span>独立车主 UV</span><strong>{st["unique_visitors"]}</strong></div>
      <div class="kpi"><span>有效点击 (CTR {st["ctr_pct"]}%)</span><strong>{st["clicks"]}</strong></div>
      <div class="kpi"><span>领券 / 核销</span><strong>{st["coupons_issued"]} / {st["coupons_redeemed"]}</strong></div>
      <div class="kpi"><span>电话 / 微信复制</span><strong>{st["calls"] + st["copies"]}</strong></div>
      <div class="kpi"><span>预约留资线索</span><strong>{st["leads_submitted"]}</strong></div>
    </div>
    <div class="bill-box">
      <div>
        <div style="font-size:12px;font-weight:700">核算口径：{esc(st["settlement_formula"])}</div>
        <div style="font-size:11px;color:#6e6e73">系统已自动剔除轮询刷新与60秒内重复连点</div>
      </div>
      <em>¥{st["estimated_amount"]:.2f}</em>
    </div>
    <div class="chips">{plat_badges}</div>
  </section>

  <section class="card">
    <strong>🎟️ 店员现场一键输码核销</strong>
    <p class="sub" style="margin-top:3px">输入车主手机出示的核销码（如 XC-1121-8392）立即完成到店核销记账：</p>
    <div class="redeem-row">
      <input id="codeInp" placeholder="输入核销码，如 XC-1121-8392" autocomplete="off"/>
      <button type="button" id="redeemBtn">确认核销</button>
    </div>
    <p id="redeemMsg" class="sub" style="margin-top:7px;font-weight:600"></p>
  </section>

  <section class="card">
    <strong>最近领券与核销记录</strong>
    <table><thead><tr><th>核销码</th><th>状态</th><th>楼栋单元</th></tr></thead><tbody>{coupon_rows}</tbody></table>
  </section>

  <section class="card">
    <strong>最近预约留资线索</strong>
    <table><thead><tr><th>联系电话</th><th>称呼</th><th>楼栋单元</th></tr></thead><tbody>{lead_rows}</tbody></table>
  </section>
</div>
<script>
document.getElementById("redeemBtn").addEventListener("click", async () => {{
  const code = document.getElementById("codeInp").value.trim();
  const msg = document.getElementById("redeemMsg");
  if (!code) return;
  const r = await fetch("/api/public/ad-partner-redeem?campaign_id={esc(camp["campaign_id"])}&token={esc(provided_token)}{ws_qs}", {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{coupon_code: code}})
  }});
  const d = await r.json();
  msg.style.color = d.ok ? "#117a27" : "#c81e1e";
  msg.textContent = d.message || d.error || "操作完成";
  if (d.ok && !d.already_redeemed) setTimeout(() => location.reload(), 900);
}});
</script>
</body>
</html>"""
