from __future__ import annotations

import hmac
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import DATA_DIR, config_manager
from app.database import Database

WORKSPACES_DIR = DATA_DIR / "workspaces"
REGISTRY_FILE = WORKSPACES_DIR / "registry.json"

SCENE_PRESETS: Dict[str, Dict[str, Any]] = {
    "community": {
        "label": "🏘️ 住宅小区",
        "role_title": "业主",
        "dest_label": "单元门",
        "greeting_template": (
            "尊贵的{site_name}{role_title}，欢迎回家！目前小区剩余停车位 {total_free} 个，"
            "优先为您推荐 {primary_zone}，当前剩余 {primary_free} 个停车位，"
            "步行到您单元门 {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": (
            "尊贵的{site_name}{role_title}，欢迎回家！目前小区监测车位已停满，建议您前往周边备选停车场。"
        ),
        "starter_units": [
            ("1-1", "1号楼1单元", "south", 32.0, 68.0),
            ("2-1", "2号楼1单元", "south", 66.0, 68.0),
        ],
        "starter_zone": ("Z_MAIN", "主干道地面停车带", "小区内部主干道地面车位"),
    },
    "office": {
        "label": "🏢 办公园区",
        "role_title": "同仁与访客",
        "dest_label": "楼座大堂",
        "greeting_template": (
            "欢迎莅临{site_name}！目前园区剩余停车位 {total_free} 个，"
            "优先为您推荐 {primary_zone}，当前剩余 {primary_free} 个停车位，"
            "步行到{unit_name} {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": (
            "欢迎莅临{site_name}！目前园区车位已停满，请前往访客备选停车区。"
        ),
        "starter_units": [
            ("A-1", "A座办公大堂", "south", 34.0, 64.0),
            ("B-1", "B座研发大堂", "east", 68.0, 48.0),
        ],
        "starter_zone": ("Z_OFFICE_A", "A座地面访客停车区", "靠近A座大堂地面标准位"),
    },
    "hospital": {
        "label": "🏥 医院院区",
        "role_title": "患者及家属",
        "dest_label": "门诊/住院楼",
        "greeting_template": (
            "欢迎来到{site_name}，祝您安康！目前院区剩余停车位 {total_free} 个，"
            "优先为您推荐 {primary_zone}，当前剩余 {primary_free} 个停车位，"
            "步行到{unit_name} {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": (
            "{site_name}提示您：目前院内车位已满，即停即走通道正常开放，长时间停放请前往周边车场。"
        ),
        "starter_units": [
            ("1-1", "门诊医技楼大厅", "south", 36.0, 66.0),
            ("2-1", "住院部1号楼入口", "east", 68.0, 42.0),
        ],
        "starter_zone": ("Z_CLINIC", "门诊楼前地面停车区", "靠近门诊大厅无障碍通道"),
    },
    "school": {
        "label": "🏫 学校园区",
        "role_title": "家长与师生",
        "dest_label": "校门/教学楼",
        "greeting_template": (
            "欢迎来到{site_name}！目前校区剩余停车位 {total_free} 个，"
            "优先为您推荐 {primary_zone}，当前剩余 {primary_free} 个停车位，"
            "步行到{unit_name} {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": (
            "{site_name}提示您：目前校区车位已满，接送车辆请沿外侧通道即停即走。"
        ),
        "starter_units": [
            ("1-1", "东校门接送等候区", "east", 74.0, 52.0),
            ("2-1", "综合行政楼入口", "south", 42.0, 66.0),
        ],
        "starter_zone": ("Z_CAMPUS_EAST", "东门访客与接送停车带", "学校东门外侧缓冲停车带"),
    },
    "venue": {
        "label": "🏛️ 场馆景区",
        "role_title": "贵宾与游客",
        "dest_label": "场馆检票口",
        "greeting_template": (
            "尊贵的{site_name}{role_title}，欢迎光临！目前场馆剩余停车位 {total_free} 个，"
            "优先为您推荐 {primary_zone}，当前剩余 {primary_free} 个停车位，"
            "步行到{unit_name} {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": (
            "欢迎光临{site_name}！目前主场馆车位已满，请前往应急生态停车场。"
        ),
        "starter_units": [
            ("1-1", "1号主展馆检票口", "south", 38.0, 64.0),
            ("2-1", "2号会议中心入口", "east", 70.0, 48.0),
        ],
        "starter_zone": ("Z_VENUE_P1", "P1贵宾地面停车场", "距离1号主展馆最近地面车位"),
    },
}

BROADCAST_STYLE_TEMPLATES: Dict[str, Dict[str, str]] = {
    "vip_community": {
        "label": "尊享业主欢迎版（默认）",
        "greeting_template": SCENE_PRESETS["community"]["greeting_template"],
        "full_template": SCENE_PRESETS["community"]["full_template"],
    },
    "office_welcome": {
        "label": "商务办公园区版",
        "greeting_template": SCENE_PRESETS["office"]["greeting_template"],
        "full_template": SCENE_PRESETS["office"]["full_template"],
    },
    "classic": {
        "label": "经典亲切回家版",
        "greeting_template": (
            "欢迎回家！目前小区剩余停车位 {total_free} 个，优先为您推荐 {primary_zone}，"
            "当前剩余 {primary_free} 个停车位，步行到您单元门 {walk_m} 米{compact_note}。"
            "{handling_note}{backup_clause}"
        ),
        "full_template": "抱歉，目前小区地面车位已停满，建议前往周边备选停车场。",
    },
    "concise": {
        "label": "极简老司机速报版",
        "greeting_template": (
            "目前剩余 {total_free} 个停车位，优先推荐 {primary_zone}，"
            "当前剩余 {primary_free} 个停车位，步行到您单元门 {walk_m} 米{compact_note}。{handling_note}{backup_clause}"
        ),
        "full_template": "{site_name}车位已满，请前往周边备选停车场。",
    },
}


def extract_short_site_name(full_name: str) -> str:
    raw = str(full_name or "").strip()
    if not raw:
        return "本小区"
    part = re.split(r"\s*[·.．｜|—\-]\s*", raw)[0].strip()
    return part or raw


def sanitize_workspace_slug(raw_slug: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw_slug or "").strip().lower()).strip("-_")
    if not s:
        return ""
    return s[:40]


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render_workspace_broadcast_text(
    profile: Optional[Dict[str, Any]],
    *,
    has_recommendation: bool,
    total_free: int,
    total_raw_free: int,
    total_spots: int,
    unit_name: str,
    unit_id: str,
    primary: Optional[Dict[str, Any]] = None,
    backup: Optional[Dict[str, Any]] = None,
    compact_note: str = "",
    handling_note: str = "",
) -> str:
    prof = profile or {}
    cfg_comm_name = str(config_manager.get().get("community_name") or "阳光花园 · 邻里公益寻位")
    full_name = str(prof.get("name") or cfg_comm_name).strip()
    short_name = str(prof.get("short_name") or "").strip() or extract_short_site_name(full_name)
    scene_type = str(prof.get("scene_type") or "community").strip().lower()
    preset = SCENE_PRESETS.get(scene_type, SCENE_PRESETS["community"])
    role_title = str(prof.get("role_title") or preset["role_title"]).strip()

    if not has_recommendation or not primary:
        if total_raw_free > 0:
            return (
                f"{short_name}仅剩的 {total_raw_free} 个空位当前均有前车正在驶往，"
                "建议在主干道临时等候或前往备选停车区。"
            )
        full_tpl = str(prof.get("full_template") or preset["full_template"]).strip()
        vars_full = _SafeFormatDict(
            site_name=short_name,
            community_name=full_name,
            role_title=role_title,
            total_free=total_free,
            total_spots=total_spots,
            unit_name=unit_name,
            unit_id=unit_id,
        )
        try:
            rendered = full_tpl.format_map(vars_full).strip()
            if rendered:
                return rendered
        except Exception:
            pass
        return f"抱歉，当前{short_name}地面监测车位已全部停满，建议直接前往周边备选停车场。"

    backup_clause = ""
    b_zone = ""
    b_free = 0
    b_walk = 0
    if backup:
        b_zone = str(backup.get("zone_name") or "")
        b_free = int(backup.get("effective_free") or 0)
        b_walk = int(backup.get("walk_meters") or 0)
        backup_clause = f"备选 {b_zone}，剩余 {b_free} 个，步行 {b_walk} 米。"

    tpl = str(prof.get("greeting_template") or preset["greeting_template"]).strip()
    vars_ok = _SafeFormatDict(
        site_name=short_name,
        community_name=full_name,
        role_title=role_title,
        total_free=total_free,
        total_spots=total_spots,
        unit_name=unit_name,
        unit_id=unit_id,
        primary_zone=primary.get("zone_name", ""),
        primary_free=primary.get("effective_free", 0),
        walk_m=primary.get("walk_meters", 0),
        compact_note=compact_note,
        handling_note=handling_note,
        backup_clause=backup_clause,
        backup_zone=b_zone,
        backup_free=b_free,
        backup_walk_m=b_walk,
    )
    try:
        rendered_ok = tpl.format_map(vars_ok).strip()
        if rendered_ok:
            return rendered_ok
    except Exception:
        pass
    return (
        f"尊贵的{short_name}{role_title}，欢迎回家！目前小区剩余停车位 {total_free} 个，"
        f"优先为您推荐 {primary['zone_name']}，当前剩余 {primary['effective_free']} 个停车位，"
        f"步行到您单元门 {primary['walk_meters']} 米{compact_note}。"
        f"{handling_note}{backup_clause}"
    )


_GREETING_LEAD_RE = re.compile(
    r"^(?:"
    r"(?:尊贵的[^！!。，,]+[，,]?\s*)?(?:欢迎(?:回家|光临|莅临|来到)[^！!。]*[！!。]\s*)"
    r"|(?:早安|午安|晚安|您好)[！!，,。]\s*"
    r")+",
)


def strip_greeting_for_repeat(single_text: str) -> str:
    """
    Strip leading welcome/greeting salutations so a 2nd spoken pass only
    repeats the concrete parking guidance without repeating '欢迎回家' etc.
    """
    clean = str(single_text or "").strip()
    if not clean:
        return ""
    stripped = _GREETING_LEAD_RE.sub("", clean, count=1).strip()
    return stripped or clean


def render_workspace_repeat_core_text(
    profile: Optional[Dict[str, Any]],
    *,
    has_recommendation: bool,
    total_raw_free: int,
    unit_name: str,
    primary: Optional[Dict[str, Any]] = None,
    backup: Optional[Dict[str, Any]] = None,
    compact_note: str = "",
) -> str:
    """
    Build a concise, natural 2nd-pass speech string without greeting/salutation:
    '优先推荐 XX停车带，剩余 X 个停车位，备选 YY停车带，剩余 Y 个。'
    """
    prof = profile or {}
    cfg_comm_name = str(config_manager.get().get("community_name") or "阳光花园 · 邻里公益寻位")
    full_name = str(prof.get("name") or cfg_comm_name).strip()
    short_name = str(prof.get("short_name") or "").strip() or extract_short_site_name(full_name)

    if not has_recommendation or not primary:
        if total_raw_free > 0:
            return f"剩余 {total_raw_free} 个车位均有前车正在驶往，建议稍候或前往备选区。"
        return f"目前{short_name}车位已停满，建议前往周边备选停车场。"

    p_zone = str(primary.get("zone_name") or "")
    p_free = int(primary.get("effective_free") or 0)
    if backup and str(backup.get("zone_name") or ""):
        return f"优先推荐 {p_zone}，剩余 {p_free} 个停车位，备选 {backup['zone_name']}，剩余 {int(backup.get('effective_free') or 0)} 个。"
    return f"优先推荐 {p_zone}，剩余 {p_free} 个停车位。"


def render_workspace_display_card_text(
    profile: Optional[Dict[str, Any]],
    *,
    has_recommendation: bool,
    total_free: int,
    total_raw_free: int,
    unit_name: str,
    primary: Optional[Dict[str, Any]] = None,
    backup: Optional[Dict[str, Any]] = None,
    handling_note: str = "",
) -> str:
    """
    Build a clean, single-copy visual popup summary (NEVER duplicated even when repeat=2).
    """
    prof = profile or {}
    cfg_comm_name = str(config_manager.get().get("community_name") or "阳光花园 · 邻里公益寻位")
    full_name = str(prof.get("name") or cfg_comm_name).strip()
    short_name = str(prof.get("short_name") or "").strip() or extract_short_site_name(full_name)

    if not has_recommendation or not primary:
        if total_raw_free > 0:
            return f"📍 {short_name} · {unit_name}\n剩余 {total_raw_free} 位均有前车驶往，请稍候或转备选区"
        return f"📍 {short_name} · {unit_name}\n院内监测车位已停满，请前往周边备选车场"

    p_zone = str(primary.get("zone_name") or "")
    p_free = int(primary.get("effective_free") or 0)
    p_walk = int(primary.get("walk_meters") or 0)
    lines = [
        f"📍 {short_name} · {unit_name}（总余 {total_free} 位）",
        f"推荐：{p_zone}（余 {p_free} 位 · 步行 {p_walk}m）",
    ]
    if backup and str(backup.get("zone_name") or ""):
        lines.append(
            f"备选：{backup['zone_name']}（余 {int(backup.get('effective_free') or 0)} 位 · 步行 {int(backup.get('walk_meters') or 0)}m）"
        )
    if handling_note:
        lines.append(handling_note.rstrip("。"))
    return "\n".join(lines)


class WorkspaceRegistry:
    """
    Multi-site / Multi-community Workspace Registry.
    - `default` workspace seamlessly wraps the root `ParkingAppContext` (`data/parking.db`).
    - Additional workspaces live in `data/workspaces/<workspace_id>/parking.db` with full
      isolation of floor-plan site maps, units, zones, cameras, custom greeting templates,
      and viewer/admin identity passcodes.
    """

    def __init__(
        self,
        root_ctx: Any,
        context_factory: Callable[..., Any],
        workspaces_dir: Optional[Path] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._root_ctx = root_ctx
        self._context_factory = context_factory
        self._base_dir = workspaces_dir or WORKSPACES_DIR
        self._registry_path = self._base_dir / "registry.json"
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._contexts: Dict[str, Any] = {"default": root_ctx}
        self._load_or_init()

    def _default_profile_template(self) -> Dict[str, Any]:
        cfg = config_manager.get()
        vg = cfg.get("voice_gateway", {})
        comm_name = str(cfg.get("community_name") or "阳光花园 · 邻里公益寻位").strip()
        return {
            "workspace_id": "default",
            "name": comm_name,
            "short_name": extract_short_site_name(comm_name),
            "scene_type": "community",
            "role_title": "业主",
            "greeting_template": SCENE_PRESETS["community"]["greeting_template"],
            "full_template": SCENE_PRESETS["community"]["full_template"],
            "preferred_voice_lang": "",
            "arrival_place": str(vg.get("arrival_place") or ""),
            "arrival_radius_m": int(vg.get("arrival_radius_m") or 200),
            "access_token": str(vg.get("access_token") or ""),
            "admin_token": "",
            "default_unit_id": str(vg.get("default_unit_id") or "8-1"),
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    def _load_or_init(self) -> None:
        with self._lock:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            def_prof = self._default_profile_template()
            loaded: Dict[str, Dict[str, Any]] = {}
            if self._registry_path.is_file():
                try:
                    raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("workspaces"), dict):
                        for wid, p in raw["workspaces"].items():
                            if isinstance(p, dict):
                                loaded[str(wid)] = p
                except Exception:
                    loaded = {}
            if "default" in loaded:
                merged_def = {**def_prof, **loaded["default"], "workspace_id": "default"}
                # Keep default workspace name synced with runtime_config community_name if not explicitly diverged
                cfg_name = str(config_manager.get().get("community_name") or "").strip()
                if cfg_name and merged_def.get("name") != cfg_name:
                    merged_def["name"] = cfg_name
                    if not loaded["default"].get("custom_short_name"):
                        merged_def["short_name"] = extract_short_site_name(cfg_name)
                loaded["default"] = merged_def
            else:
                loaded["default"] = def_prof
            self._profiles = loaded
            self._root_ctx.workspace_id = "default"
            self._root_ctx.workspace_registry = self
            self._save_locked()

    def _save_locked(self) -> None:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "workspaces": self._profiles}
            self._registry_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _mask_secret(sec: str) -> str:
        s = str(sec or "")
        if not s:
            return ""
        return (s[:2] + "****" + s[-2:]) if len(s) > 4 else "****"

    def serialize_public_profile(self, ws_id: str, include_templates: bool = True) -> Dict[str, Any]:
        with self._lock:
            prof = self._profiles.get(ws_id) or self._profiles["default"]
            scene_type = str(prof.get("scene_type") or "community")
            preset = SCENE_PRESETS.get(scene_type, SCENE_PRESETS["community"])
            raw_tok = str(prof.get("access_token") or "").strip()
            if ws_id == "default" and not raw_tok:
                raw_tok = str(config_manager.get().get("voice_gateway", {}).get("access_token") or "").strip()
            raw_adm = str(prof.get("admin_token") or "").strip()
            out: Dict[str, Any] = {
                "workspace_id": prof["workspace_id"],
                "name": prof.get("name") or "邻里公益寻位",
                "short_name": prof.get("short_name") or extract_short_site_name(prof.get("name") or ""),
                "scene_type": scene_type,
                "scene_label": preset["label"],
                "dest_label": preset["dest_label"],
                "role_title": prof.get("role_title") or preset["role_title"],
                "preferred_voice_lang": prof.get("preferred_voice_lang") or "",
                "arrival_place": prof.get("arrival_place") or "",
                "arrival_radius_m": int(prof.get("arrival_radius_m") or 200),
                "default_unit_id": prof.get("default_unit_id") or "",
                "has_access_token": bool(raw_tok),
                "access_token_masked": self._mask_secret(raw_tok),
                "has_admin_token": bool(raw_adm),
                "admin_token_masked": self._mask_secret(raw_adm),
                "is_default": prof["workspace_id"] == "default",
            }
            if include_templates:
                out["greeting_template"] = prof.get("greeting_template") or preset["greeting_template"]
                out["full_template"] = prof.get("full_template") or preset["full_template"]
            return out

    def get_raw_profile(self, ws_id: str) -> Dict[str, Any]:
        with self._lock:
            prof = dict(self._profiles.get(ws_id) or self._profiles["default"])
            if prof["workspace_id"] == "default":
                cfg = config_manager.get()
                vg = cfg.get("voice_gateway", {})
                if cfg.get("community_name"):
                    prof["name"] = cfg["community_name"]
                if not prof.get("access_token") and vg.get("access_token"):
                    prof["access_token"] = vg["access_token"]
                if not prof.get("arrival_place") and vg.get("arrival_place"):
                    prof["arrival_place"] = vg["arrival_place"]
            return prof

    def list_workspaces(self, scoped_admin_ws: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            ids = list(self._profiles.keys())
        if scoped_admin_ws and scoped_admin_ws in self._profiles:
            ids = [scoped_admin_ws]
        items: List[Dict[str, Any]] = []
        for wid in ids:
            pub = self.serialize_public_profile(wid, include_templates=True)
            ctx = self.get_context(wid)
            zones = ctx.recommender.get_zone_summaries()
            pub["total_capacity"] = sum(int(z.get("total_capacity") or 0) for z in zones)
            pub["total_effective_free"] = sum(int(z.get("effective_free") or 0) for z in zones)
            pub["units_count"] = len(ctx.db.list_units())
            pub["cameras_count"] = len(ctx.db.list_cameras())
            items.append(pub)
        return items

    def exists(self, ws_id: str) -> bool:
        with self._lock:
            return ws_id in self._profiles

    def get_context(self, ws_id: Optional[str] = None) -> Any:
        target = str(ws_id or "default").strip() or "default"
        with self._lock:
            if target not in self._profiles:
                target = "default"
            if target in self._contexts:
                return self._contexts[target]
            ws_dir = self._base_dir / target
            ws_dir.mkdir(parents=True, exist_ok=True)
            db_file = ws_dir / "parking.db"
            ws_db = Database(str(db_file), seed_default=False)
            new_ctx = self._context_factory(
                database=ws_db,
                workspace_id=target,
                workspace_registry=self,
            )
            if self._root_ctx.camera_ingestor.is_running:
                new_ctx.start_camera_ingestor()
            self._contexts[target] = new_ctx
            return new_ctx

    def verify_viewer_token(self, ws_id: str, provided_token: str) -> bool:
        prof = self.get_raw_profile(ws_id)
        expected = str(prof.get("access_token") or "").strip()
        if not expected:
            return True
        cand = str(provided_token or "").strip()
        if not cand:
            return False
        return hmac.compare_digest(expected.encode("utf-8"), cand.encode("utf-8"))

    def verify_admin_token(self, ws_id: str, provided_admin_token: str) -> bool:
        prof = self.get_raw_profile(ws_id)
        expected = str(prof.get("admin_token") or "").strip()
        if not expected:
            return True
        cand = str(provided_admin_token or "").strip()
        if not cand:
            return False
        return hmac.compare_digest(expected.encode("utf-8"), cand.encode("utf-8"))

    def create_workspace(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_id = str(payload.get("workspace_id") or payload.get("slug") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("请输入工作空间（小区/园区）名称")
        ws_id = sanitize_workspace_slug(raw_id)
        if not ws_id:
            ws_id = f"ws-{int(time.time()) % 1000000}"
        if ws_id == "default":
            raise ValueError("workspace_id 不能为保留标识 default")

        scene_type = str(payload.get("scene_type") or "community").strip().lower()
        if scene_type not in SCENE_PRESETS:
            scene_type = "community"
        preset = SCENE_PRESETS[scene_type]
        short_name = str(payload.get("short_name") or "").strip() or extract_short_site_name(name)
        role_title = str(payload.get("role_title") or preset["role_title"]).strip()
        greeting_tpl = str(payload.get("greeting_template") or preset["greeting_template"]).strip()
        full_tpl = str(payload.get("full_template") or preset["full_template"]).strip()
        template_mode = str(payload.get("template_mode") or "blank").strip().lower()

        with self._lock:
            if ws_id in self._profiles:
                raise ValueError(f"工作空间标识 '{ws_id}' 已存在，请换一个英文标识")
            ws_dir = self._base_dir / ws_id
            ws_dir.mkdir(parents=True, exist_ok=True)
            db_file = ws_dir / "parking.db"
            seed_demo = template_mode == "demo"
            ws_db = Database(str(db_file), seed_default=seed_demo)
            if not seed_demo:
                self._seed_clean_starter_workspace(ws_db, preset)

            now = time.time()
            starter_units = ws_db.list_units()
            def_uid = str(payload.get("default_unit_id") or (starter_units[0]["unit_id"] if starter_units else "1-1"))
            prof = {
                "workspace_id": ws_id,
                "name": name,
                "short_name": short_name,
                "custom_short_name": bool(payload.get("short_name")),
                "scene_type": scene_type,
                "role_title": role_title,
                "greeting_template": greeting_tpl,
                "full_template": full_tpl,
                "preferred_voice_lang": str(payload.get("preferred_voice_lang") or "").strip(),
                "arrival_place": str(payload.get("arrival_place") or "").strip(),
                "arrival_radius_m": int(payload.get("arrival_radius_m") or 200),
                "access_token": str(payload.get("access_token") or "").strip(),
                "admin_token": str(payload.get("admin_token") or "").strip(),
                "default_unit_id": def_uid,
                "created_at": now,
                "updated_at": now,
            }
            self._profiles[ws_id] = prof
            new_ctx = self._context_factory(
                database=ws_db,
                workspace_id=ws_id,
                workspace_registry=self,
            )
            if self._root_ctx.camera_ingestor.is_running:
                new_ctx.start_camera_ingestor()
            self._contexts[ws_id] = new_ctx
            self._save_locked()
            return self.serialize_public_profile(ws_id, include_templates=True)

    @staticmethod
    def _seed_clean_starter_workspace(ws_db: Database, preset: Dict[str, Any]) -> None:
        z_id, z_name, z_desc = preset["starter_zone"]
        ws_db.upsert_zone(
            zone_id=z_id,
            name=z_name,
            description=z_desc,
            gate_access="south,east",
            dead_end_penalty=0.0,
            map_x=28.0,
            map_y=46.0,
            map_w=36.0,
            map_h=10.0,
        )
        for uid, uname, ugate, ux, uy in preset["starter_units"]:
            ws_db.upsert_unit(
                unit_id=uid,
                name=uname,
                default_gate=ugate,
                map_x=ux,
                map_y=uy,
            )

    def update_workspace(self, ws_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        target = str(ws_id or "default").strip() or "default"
        with self._lock:
            if target not in self._profiles:
                raise ValueError(f"工作空间 '{target}' 不存在")
            prof = dict(self._profiles[target])
            if "name" in patch and str(patch["name"]).strip():
                prof["name"] = str(patch["name"]).strip()
                if "short_name" not in patch and not prof.get("custom_short_name"):
                    prof["short_name"] = extract_short_site_name(prof["name"])
            if "short_name" in patch:
                sn = str(patch["short_name"]).strip()
                prof["short_name"] = sn or extract_short_site_name(prof["name"])
                prof["custom_short_name"] = bool(sn)
            if "scene_type" in patch and str(patch["scene_type"]).strip() in SCENE_PRESETS:
                new_sc = str(patch["scene_type"]).strip()
                prof["scene_type"] = new_sc
                if patch.get("apply_scene_preset"):
                    sc_p = SCENE_PRESETS[new_sc]
                    prof["role_title"] = sc_p["role_title"]
                    prof["greeting_template"] = sc_p["greeting_template"]
                    prof["full_template"] = sc_p["full_template"]
            for field in (
                "role_title",
                "greeting_template",
                "full_template",
                "preferred_voice_lang",
                "arrival_place",
                "default_unit_id",
            ):
                if field in patch and patch[field] is not None:
                    prof[field] = str(patch[field]).strip()
            if "arrival_radius_m" in patch and patch["arrival_radius_m"] is not None:
                prof["arrival_radius_m"] = max(50, min(2000, int(patch["arrival_radius_m"])))

            if patch.get("clear_access_token"):
                prof["access_token"] = ""
            elif "access_token" in patch and str(patch["access_token"]).strip():
                prof["access_token"] = str(patch["access_token"]).strip()

            if patch.get("clear_admin_token"):
                prof["admin_token"] = ""
            elif "admin_token" in patch and str(patch["admin_token"]).strip():
                prof["admin_token"] = str(patch["admin_token"]).strip()

            prof["updated_at"] = time.time()
            self._profiles[target] = prof
            self._save_locked()

            if target == "default":
                cfg_patch: Dict[str, Any] = {"community_name": prof["name"]}
                vg_patch: Dict[str, Any] = {
                    "arrival_place": prof.get("arrival_place", ""),
                    "arrival_radius_m": int(prof.get("arrival_radius_m") or 200),
                }
                if patch.get("clear_access_token"):
                    vg_patch["clear_access_token"] = True
                elif "access_token" in patch and str(patch["access_token"]).strip():
                    vg_patch["access_token"] = prof["access_token"]
                if prof.get("default_unit_id"):
                    vg_patch["default_unit_id"] = prof["default_unit_id"]
                cfg_patch["voice_gateway"] = vg_patch
                config_manager.update(cfg_patch)

            return self.serialize_public_profile(target, include_templates=True)

    def delete_workspace(self, ws_id: str) -> Dict[str, Any]:
        target = str(ws_id or "").strip()
        if not target or target == "default":
            raise ValueError("默认主工作空间不支持删除")
        with self._lock:
            if target not in self._profiles:
                raise ValueError(f"工作空间 '{target}' 不存在")
            ctx = self._contexts.pop(target, None)
            if ctx is not None:
                try:
                    ctx.stop_camera_ingestor()
                    ctx.voice_gateway.stop_background_workers()
                    ctx.db.checkpoint_wal("TRUNCATE")
                except Exception:
                    pass
            self._profiles.pop(target, None)
            self._save_locked()
            ws_dir = self._base_dir / target
            if ws_dir.is_dir():
                shutil.rmtree(ws_dir, ignore_errors=True)
            return {"ok": True, "deleted_workspace_id": target}

    def start_all_ingestors(self, tick_seconds: float = 1.5) -> None:
        with self._lock:
            ctxs = list(self._contexts.values())
        for c in ctxs:
            c.start_camera_ingestor(tick_seconds=tick_seconds)

    def stop_all_and_checkpoint(self) -> None:
        with self._lock:
            ctxs = list(self._contexts.values())
        for c in ctxs:
            try:
                c.stop_camera_ingestor()
                c.voice_gateway.stop_background_workers()
                c.db.checkpoint_wal("TRUNCATE")
            except Exception:
                pass
