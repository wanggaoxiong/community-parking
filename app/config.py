import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "parking.db"
RUNTIME_CONFIG_PATH = DATA_DIR / "runtime_config.json"

VLM_PRESETS: Dict[str, Dict[str, str]] = {
    "azure_foundry": {
        "label": "Azure AI Foundry (支持粘贴完整 Target URI 或 Endpoint)",
        "base_url": "https://<your-resource>.openai.azure.com",
        "model": "gpt-4o",
        "api_version": "2024-12-01-preview",
        "auth_mode": "auto",
    },
    "azure_ai_services": {
        "label": "Azure AI Foundry Serverless (services.ai.azure.com)",
        "base_url": "https://<your-resource>.services.ai.azure.com/models",
        "model": "Phi-4-multimodal-instruct",
        "api_version": "2024-05-01-preview",
        "auth_mode": "auto",
    },
    "openai": {
        "label": "OpenAI (gpt-4o / gpt-4.1-mini)",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_version": "",
        "auth_mode": "bearer",
    },
    "qwen": {
        "label": "阿里云百炼 Qwen-VL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-plus",
        "api_version": "",
        "auth_mode": "bearer",
    },
    "gemini": {
        "label": "Google Gemini Flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "api_version": "",
        "auth_mode": "bearer",
    },
    "ollama": {
        "label": "本地 Ollama (qwen2.5vl / llava)",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5vl:7b",
        "api_version": "",
        "auth_mode": "none",
    },
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "community_name": "阳光花园 · 邻里公益寻位",
    "debounce_seconds": 3.0,
    "instant_confirm_mode": False,
    "first_frame_vlm_anchor": False,
    "hybrid_vlm_confidence_threshold": 0.85,
    "dynamic_roi_enabled": True,
    "debounce_v2_5s_migrated": True,
    "debounce_v3_3s_migrated": True,
    "soft_lock_v2_40s_migrated": True,
    "vlm_hybrid_auto_migrated": True,
    "vlm_hybrid_v2_migrated": True,
    "hybrid_85_gate_migrated": True,
    "soft_lock_ttl_seconds": 40,
    "offline_timeout_seconds": 86400,
    "ssim_change_threshold": 0.035,
    "cache_budget_mb": 64,
    "vlm_cache_ttl_seconds": 900,
    "standard_car_length_m": 5.2,
    "http_port": int(os.environ.get("PORT", "8790")),
    "mobile_http_host": os.environ.get("MOBILE_HOST", "0.0.0.0"),
    "mobile_http_port": int(os.environ.get("MOBILE_PORT", "18790")),
    "voice_http_host": os.environ.get("VOICE_HOST", "0.0.0.0"),
    "voice_http_port": int(os.environ.get("VOICE_PORT", "18791")),
    "ftp_port": int(os.environ.get("FTP_PORT", "2121")),
    "ftp_user": "camera",
    "ftp_password": "parking2026",
    "voice_gateway": {
        "enabled": True,
        "access_token": os.environ.get("VOICE_GATEWAY_TOKEN", ""),
        "default_unit_id": os.environ.get("VOICE_DEFAULT_UNIT", "8-1"),
        "default_repeat": 2,
        "public_mobile_url": os.environ.get("PUBLIC_MOBILE_URL", ""),
        "public_voice_url": os.environ.get("PUBLIC_VOICE_URL", ""),
        "icloud_shortcut_url": os.environ.get("VOICE_ICLOUD_SHORTCUT_URL", ""),
        "arrival_place": os.environ.get("VOICE_ARRIVAL_PLACE", ""),
        "arrival_radius_m": int(os.environ.get("VOICE_ARRIVAL_RADIUS_M", "200")),
        "llm_qa_enabled": True,
        "rate_limit_per_minute": int(os.environ.get("VOICE_RATE_LIMIT_PER_MIN", "12")),
        "daily_llm_call_limit": int(os.environ.get("VOICE_DAILY_LLM_LIMIT", "300")),
        "max_question_chars": 300,
        "weixin_enabled": os.environ.get("OPENCLAW_WEIXIN_ENABLED", "").lower() in ("1", "true", "yes"),
        "weixin_api_base": os.environ.get("OPENCLAW_WEIXIN_BASE", "https://ilinkai.weixin.qq.com"),
        "weixin_bot_token": os.environ.get("OPENCLAW_WEIXIN_TOKEN", ""),
        "weixin_account_name": "",
    },
    "vlm": {
        "mode": "hybrid",  # hybrid | always_vlm | local_only
        "preset": "azure_foundry",
        "base_url": os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("VLM_BASE_URL", "")),
        "api_key": os.environ.get("AZURE_AI_API_KEY", os.environ.get("VLM_API_KEY", "")),
        "model": os.environ.get("AZURE_AI_MODEL", os.environ.get("VLM_MODEL", "gpt-4o")),
        "api_version": os.environ.get("AZURE_API_VERSION", "2024-12-01-preview"),
        "auth_mode": "auto",  # auto | api-key | bearer
        "timeout_seconds": 25,
    },
}


class ConfigManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: Dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
        self._load()

    def _load(self) -> None:
        if RUNTIME_CONFIG_PATH.exists():
            try:
                saved = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
                migrated = False
                if not saved.get("debounce_v2_5s_migrated"):
                    if float(saved.get("debounce_seconds", 8.0)) == 8.0:
                        saved["debounce_seconds"] = 5.0
                    saved["debounce_v2_5s_migrated"] = True
                    migrated = True
                if not saved.get("debounce_v3_3s_migrated"):
                    if float(saved.get("debounce_seconds", 5.0)) in (8.0, 5.0):
                        saved["debounce_seconds"] = 3.0
                    saved["debounce_v3_3s_migrated"] = True
                    migrated = True
                if not saved.get("soft_lock_v2_40s_migrated"):
                    if int(saved.get("soft_lock_ttl_seconds", 180)) == 180:
                        saved["soft_lock_ttl_seconds"] = 40
                    saved["soft_lock_v2_40s_migrated"] = True
                    migrated = True
                if not saved.get("vlm_hybrid_auto_migrated") or not saved.get("vlm_hybrid_v2_migrated"):
                    vlm_saved = saved.get("vlm")
                    if (
                        isinstance(vlm_saved, dict)
                        and vlm_saved.get("mode") == "local_only"
                        and str(vlm_saved.get("base_url") or "").strip()
                        and str(vlm_saved.get("api_key") or "").strip()
                    ):
                        vlm_saved["mode"] = "hybrid"
                    saved["vlm_hybrid_auto_migrated"] = True
                    saved["vlm_hybrid_v2_migrated"] = True
                    migrated = True
                if not saved.get("hybrid_85_gate_migrated"):
                    saved["first_frame_vlm_anchor"] = False
                    saved["hybrid_vlm_confidence_threshold"] = float(
                        saved.get("hybrid_vlm_confidence_threshold") or 0.85
                    )
                    saved["hybrid_85_gate_migrated"] = True
                    migrated = True
                self._deep_merge(self._config, saved)
                if migrated:
                    RUNTIME_CONFIG_PATH.write_text(
                        json.dumps(self._config, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception:
                pass

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]) -> None:
        for k, v in updates.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._config))

    def get_public(self) -> Dict[str, Any]:
        cfg = self.get()
        raw_key = cfg.get("vlm", {}).get("api_key", "")
        cfg["vlm"]["has_api_key"] = bool(raw_key)
        if raw_key:
            cfg["vlm"]["api_key_masked"] = (
                raw_key[:4] + "****" + raw_key[-4:] if len(raw_key) > 8 else "****"
            )
        else:
            cfg["vlm"]["api_key_masked"] = ""
        cfg["vlm"].pop("api_key", None)
        vg = cfg.get("voice_gateway")
        if isinstance(vg, dict):
            raw_vt = str(vg.get("access_token") or "")
            vg["has_access_token"] = bool(raw_vt)
            vg["access_token_masked"] = (
                raw_vt[:2] + "****" + raw_vt[-2:] if len(raw_vt) > 4 else ("****" if raw_vt else "")
            )
            vg.pop("access_token", None)
            raw_wx = str(vg.get("weixin_bot_token") or "")
            vg["has_weixin_bot_token"] = bool(raw_wx)
            vg.pop("weixin_bot_token", None)
        cfg["vlm_presets"] = VLM_PRESETS
        return cfg

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "vlm" in patch and isinstance(patch["vlm"], dict):
                vlm_patch = dict(patch["vlm"])
                # Preserve existing api_key if empty/omitted unless clear_api_key is True
                if vlm_patch.get("clear_api_key"):
                    vlm_patch["api_key"] = ""
                    vlm_patch.pop("clear_api_key", None)
                elif "api_key" in vlm_patch and vlm_patch["api_key"] == "":
                    vlm_patch.pop("api_key", None)
                patch = dict(patch)
                patch["vlm"] = vlm_patch
            if "voice_gateway" in patch and isinstance(patch["voice_gateway"], dict):
                vg_patch = dict(patch["voice_gateway"])
                if vg_patch.get("clear_access_token"):
                    vg_patch["access_token"] = ""
                    vg_patch.pop("clear_access_token", None)
                elif "access_token" in vg_patch and vg_patch["access_token"] == "":
                    vg_patch.pop("access_token", None)
                if vg_patch.get("clear_weixin_bot_token"):
                    vg_patch["weixin_bot_token"] = ""
                    vg_patch.pop("clear_weixin_bot_token", None)
                elif "weixin_bot_token" in vg_patch and vg_patch["weixin_bot_token"] == "":
                    vg_patch.pop("weixin_bot_token", None)
                patch = dict(patch)
                patch["voice_gateway"] = vg_patch
            self._deep_merge(self._config, patch)
            try:
                RUNTIME_CONFIG_PATH.write_text(
                    json.dumps(self._config, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            return self.get_public()


config_manager = ConfigManager()
