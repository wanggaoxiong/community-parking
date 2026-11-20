"""Extensible Parking Spot Multi-Tag & EV/Rental Metadata Engine.

Provides a single source of truth for:
- Built-in & custom parking stall tags (`ev_charging`, `rentable`, `private`,
  `large_friendly`, `accessible`, `visitor`, plus custom operator tags)
- Normalization, alias mapping, and safe public viewer projection
- Preservation of operator tag overrides across camera recalibration
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple


SPOT_TAG_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "ev_charging",
        "label": "新能源充电",
        "short_badge": "⚡",
        "icon": "bolt",
        "color": "#10B981",
        "bg_soft": "rgba(16, 185, 129, 0.14)",
        "description": "配备充电桩或国标绿漆新能源车位，空间标定可自动识别并支持寻桩优先推荐",
        "auto_detect": True,
    },
    {
        "code": "rentable",
        "label": "对外招租",
        "short_badge": "租",
        "icon": "lease",
        "color": "#0071E3",
        "bg_soft": "rgba(0, 113, 227, 0.14)",
        "description": "支持包月长租或错峰共享，车主在平面图点选车位即可查看时段、租金与办理方式",
        "auto_detect": False,
    },
    {
        "code": "private",
        "label": "产权固定位",
        "short_badge": "专",
        "icon": "lock",
        "color": "#6366F1",
        "bg_soft": "rgba(99, 102, 241, 0.14)",
        "description": "业主专属固定车位；未同时开启「对外招租/共享」时不计入公共寻位净余空位",
        "auto_detect": False,
    },
    {
        "code": "large_friendly",
        "label": "宽体大车位",
        "short_badge": "宽",
        "icon": "wide",
        "color": "#0EA5E9",
        "bg_soft": "rgba(14, 165, 233, 0.14)",
        "description": "加宽好倒车泊位，中大型 SUV / MPV 优先推荐适配",
        "auto_detect": False,
    },
    {
        "code": "accessible",
        "label": "无障碍车位",
        "short_badge": "♿",
        "icon": "accessible",
        "color": "#8B5CF6",
        "bg_soft": "rgba(139, 92, 246, 0.14)",
        "description": "无障碍关爱宽幅车位，邻近无障碍电梯或单元坡道",
        "auto_detect": False,
    },
    {
        "code": "visitor",
        "label": "访客临停",
        "short_badge": "访",
        "icon": "visitor",
        "color": "#14B8A6",
        "bg_soft": "rgba(20, 184, 166, 0.14)",
        "description": "亲友来访或即停即走优先泊位",
        "auto_detect": False,
    },
]

SPOT_TAG_MAP: Dict[str, Dict[str, Any]] = {item["code"]: item for item in SPOT_TAG_CATALOG}
_CATALOG_ORDER: Dict[str, int] = {item["code"]: idx for idx, item in enumerate(SPOT_TAG_CATALOG)}

_TAG_ALIASES: Dict[str, str] = {
    "ev": "ev_charging",
    "ev_spot": "ev_charging",
    "charging": "ev_charging",
    "charger": "ev_charging",
    "new_energy": "ev_charging",
    "新能源": "ev_charging",
    "新能源车位": "ev_charging",
    "新能源充电": "ev_charging",
    "充电桩": "ev_charging",
    "充电车位": "ev_charging",
    "rent": "rentable",
    "lease": "rentable",
    "for_rent": "rentable",
    "shared": "rentable",
    "出租": "rentable",
    "对外出租": "rentable",
    "对外招租": "rentable",
    "车位招租": "rentable",
    "错峰共享": "rentable",
    "vip": "private",
    "fixed": "private",
    "reserved": "private",
    "产权": "private",
    "产权位": "private",
    "产权固定位": "private",
    "固定车位": "private",
    "私家车位": "private",
    "wide": "large_friendly",
    "suv_friendly": "large_friendly",
    "宽体": "large_friendly",
    "宽体大车位": "large_friendly",
    "大车位": "large_friendly",
    "无障碍": "accessible",
    "无障碍车位": "accessible",
    "访客": "visitor",
    "访客车位": "visitor",
    "访客临停": "visitor",
}


def normalize_spot_tags(raw_tags: Any, max_custom: int = 4) -> List[str]:
    """Normalize arbitrary tag input (list, JSON string, CSV) into ordered canonical tags."""
    if raw_tags is None:
        return []
    items: Iterable[Any]
    if isinstance(raw_tags, str):
        txt = raw_tags.strip()
        if not txt:
            return []
        if txt.startswith("["):
            try:
                parsed = json.loads(txt)
                items = parsed if isinstance(parsed, list) else [txt]
            except Exception:
                items = txt.split(",")
        else:
            items = [p for p in txt.replace("，", ",").split(",")]
    elif isinstance(raw_tags, (list, tuple, set)):
        items = raw_tags
    else:
        return []

    builtin_found: List[str] = []
    custom_found: List[str] = []
    seen = set()

    for raw in items:
        s = str(raw or "").strip()
        if not s:
            continue
        low = s.lower()
        canonical = _TAG_ALIASES.get(low) or _TAG_ALIASES.get(s) or (low if low in SPOT_TAG_MAP else "")
        if canonical:
            if canonical not in seen:
                seen.add(canonical)
                builtin_found.append(canonical)
            continue
        # Sanitize custom operator tag (e.g. "子母车位", "快充桩", "企业专享")
        clean_custom = "".join(ch for ch in s if ch.isprintable() and ch not in '<>"\'`\\').strip()[:16]
        if clean_custom and clean_custom not in seen and len(custom_found) < max_custom:
            seen.add(clean_custom)
            custom_found.append(clean_custom)

    builtin_found.sort(key=lambda code: _CATALOG_ORDER.get(code, 999))
    return builtin_found + custom_found


def parse_tags_json(raw_json: Optional[str]) -> List[str]:
    if not raw_json:
        return []
    return normalize_spot_tags(raw_json)


def parse_tag_meta_json(raw_json: Optional[str]) -> Dict[str, Any]:
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
        return normalize_spot_tag_meta(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _clean_short_str(val: Any, max_len: int) -> str:
    s = str(val or "").strip()
    s = "".join(ch for ch in s if ch.isprintable() and ch not in "\r\n\t")
    return s[:max_len].strip()


def normalize_spot_tag_meta(
    raw_meta: Optional[Dict[str, Any]],
    existing_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize structured EV / rental / operator metadata on a parking spot."""
    merged: Dict[str, Any] = {}
    if isinstance(existing_meta, dict):
        merged.update(existing_meta)
    if isinstance(raw_meta, dict):
        merged.update(raw_meta)

    out: Dict[str, Any] = {}
    charger_power = _clean_short_str(merged.get("charger_power"), 32)
    if charger_power:
        out["charger_power"] = charger_power

    if merged.get("ev_auto_detected"):
        out["ev_auto_detected"] = True
    if "ev_user_override" in merged and merged["ev_user_override"] is not None:
        out["ev_user_override"] = bool(merged["ev_user_override"])
    ev_reason = _clean_short_str(merged.get("ev_reason"), 48)
    if ev_reason:
        out["ev_reason"] = ev_reason

    rental_window = _clean_short_str(merged.get("rental_window"), 48)
    if rental_window:
        out["rental_window"] = rental_window

    rental_price = _clean_short_str(merged.get("rental_price"), 32)
    if rental_price:
        out["rental_price"] = rental_price

    rental_contact = _clean_short_str(merged.get("rental_contact"), 64)
    if rental_contact:
        out["rental_contact"] = rental_contact

    note = _clean_short_str(merged.get("note"), 80)
    if note:
        out["note"] = note

    if "shared_open" in merged and merged["shared_open"] is not None:
        out["shared_open"] = bool(merged["shared_open"])

    return out


def sanitize_spot_tag_meta_for_public(
    tags: List[str],
    tag_meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return only resident-safe public fields for :18790 and :18791."""
    if not isinstance(tag_meta, dict) or not tag_meta:
        return {}
    tag_set = set(tags or [])
    pub: Dict[str, Any] = {}
    if "ev_charging" in tag_set and tag_meta.get("charger_power"):
        pub["charger_power"] = str(tag_meta["charger_power"])
    if "rentable" in tag_set:
        for k in ("rental_window", "rental_price", "rental_contact"):
            if tag_meta.get(k):
                pub[k] = str(tag_meta[k])
    return pub


def is_spot_privately_reserved(
    tags: Iterable[str],
    tag_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when a spot is marked private/fixed AND not currently open for rent/sharing."""
    tag_set = set(tags or [])
    if "private" not in tag_set:
        return False
    if "rentable" in tag_set:
        return False
    if isinstance(tag_meta, dict) and bool(tag_meta.get("shared_open")):
        return False
    return True


def resolve_tag_filter_code(raw_tag: Optional[str]) -> str:
    """Normalize a single query/filter tag code or alias; returns '' for 'all'/empty."""
    s = str(raw_tag or "").strip()
    if not s or s.lower() in ("all", "any", "none", "全部", "不限"):
        return ""
    norm = normalize_spot_tags([s], max_custom=1)
    return norm[0] if norm else ""


def get_tag_display_info(tag_code: str) -> Dict[str, Any]:
    """Return display metadata (`code`, `label`, `short_badge`, `color`) for built-in or custom tag."""
    clean = resolve_tag_filter_code(tag_code)
    if not clean:
        return {"code": "", "label": "全部车位", "short_badge": "", "color": "#0071E3"}
    if clean in SPOT_TAG_MAP:
        return dict(SPOT_TAG_MAP[clean])
    return {
        "code": clean,
        "label": clean,
        "short_badge": "#",
        "icon": "tag",
        "color": "#0EA5E9",
        "bg_soft": "rgba(14, 165, 233, 0.14)",
        "description": f"自定义标签：{clean}",
        "auto_detect": False,
    }


_NATURAL_TEXT_TAG_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    (
        "ev_charging",
        (
            "新能源",
            "充电桩",
            "充电位",
            "充电车位",
            "充电空位",
            "电桩",
            "找桩",
            "寻桩",
            "有桩",
            "带桩",
            "快充",
            "慢充",
            "超充",
            "纯电",
            "绿牌",
            "插混",
            "电车",
            "充个电",
            "充下电",
            "叉電",
            "充電",
            "新能源車",
            "ev ",
            "ev?",
            "ev spot",
            "ev stall",
            "ev charg",
            "charger",
            "charging",
            "electric car",
            "electric vehicle",
        ),
    ),
    (
        "rentable",
        (
            "对外出租",
            "对外招租",
            "车位出租",
            "车位招租",
            "出租车位",
            "招租车位",
            "可租",
            "待租",
            "租车位",
            "月租",
            "包月",
            "长租",
            "短租",
            "错峰共享",
            "共享车位",
            "招租",
            "出租",
            "rent",
            "for rent",
            "lease",
        ),
    ),
    (
        "accessible",
        (
            "无障碍",
            "無障礙",
            "轮椅",
            "关爱车位",
            "残疾人",
            "accessible",
            "handicap",
            "wheelchair",
        ),
    ),
    (
        "large_friendly",
        (
            "宽体",
            "大车位",
            "加宽车位",
            "加宽位",
            "宽车位",
            "好倒车",
            "wide spot",
            "large spot",
        ),
    ),
    (
        "visitor",
        (
            "访客",
            "訪客",
            "来访",
            "亲友临停",
            "临时访客",
            "visitor",
            "guest spot",
        ),
    ),
    (
        "private",
        (
            "产权位",
            "产权车位",
            "固定车位",
            "固定位",
            "私家车位",
            "专属车位",
            "private spot",
            "reserved spot",
        ),
    ),
]


def match_spot_tag_from_text(
    text: str,
    extra_custom_tags: Iterable[str] = (),
) -> Optional[str]:
    """Extract a queried parking spot tag code from spoken/typed natural language."""
    s = str(text or "").strip()
    if not s:
        return None
    low = f" {s.lower()} "

    # Guard against matching "电动车/电瓶车" (two-wheel e-bike) as EV car charging
    scrubbed = (
        s.replace("两轮电动车", "")
        .replace("电瓶车", "")
        .replace("电动自行车", "")
    )
    scrub_low = f" {scrubbed.lower()} "

    # Check custom operator tags first (exact substring match, longest first)
    for ctag in sorted({str(t or "").strip() for t in extra_custom_tags if str(t or "").strip()}, key=len, reverse=True):
        if ctag.lower() in scrub_low:
            return ctag

    for code, keywords in _NATURAL_TEXT_TAG_PATTERNS:
        for kw in keywords:
            if kw.isascii():
                if kw in scrub_low:
                    return code
            else:
                if kw in scrubbed:
                    return code
    return None


def merge_spot_tags_on_recalibration(
    prev_tags_raw: Any,
    prev_meta_raw: Any,
    incoming_tags_raw: Any = None,
    incoming_meta_raw: Any = None,
    auto_is_ev: Optional[bool] = None,
    auto_ev_reason: str = "",
) -> Tuple[List[str], Dict[str, Any]]:
    """Preserve operator tags (e.g. rentable, private) across camera recalibration while merging EV auto-detection."""
    prev_tags = normalize_spot_tags(prev_tags_raw)
    prev_meta = (
        parse_tag_meta_json(prev_meta_raw)
        if isinstance(prev_meta_raw, str)
        else normalize_spot_tag_meta(prev_meta_raw if isinstance(prev_meta_raw, dict) else None)
    )
    inc_meta = (
        parse_tag_meta_json(incoming_meta_raw)
        if isinstance(incoming_meta_raw, str)
        else normalize_spot_tag_meta(incoming_meta_raw if isinstance(incoming_meta_raw, dict) else None)
    )

    if incoming_tags_raw is not None:
        # Explicit tags supplied by caller (e.g. calibration UI where user toggled ⚡ or preserved tags)
        tags = normalize_spot_tags(incoming_tags_raw)
        # Still preserve non-EV manual tags from previous record if caller only sent calibration-level tags
        for pt in prev_tags:
            if pt != "ev_charging" and pt not in tags:
                tags.append(pt)
        tags = normalize_spot_tags(tags)
    else:
        tags = list(prev_tags)

    merged_meta = normalize_spot_tag_meta(inc_meta, existing_meta=prev_meta)

    if auto_is_ev is True:
        # Respect explicit operator opt-out (`ev_user_override == False`)
        if merged_meta.get("ev_user_override") is not False:
            if "ev_charging" not in tags:
                tags = normalize_spot_tags(["ev_charging", *tags])
            merged_meta["ev_auto_detected"] = True
            if auto_ev_reason:
                merged_meta["ev_reason"] = _clean_short_str(auto_ev_reason, 48)
    elif auto_is_ev is False and incoming_tags_raw is None:
        # Only clear auto-detected EV if operator never manually pinned `ev_user_override=True`
        if merged_meta.get("ev_auto_detected") and not merged_meta.get("ev_user_override"):
            tags = [t for t in tags if t != "ev_charging"]
            merged_meta.pop("ev_auto_detected", None)
            merged_meta.pop("ev_reason", None)

    return normalize_spot_tags(tags), normalize_spot_tag_meta(merged_meta)
