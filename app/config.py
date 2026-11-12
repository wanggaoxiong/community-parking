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
    "debounce_seconds": 8.0,
    "instant_confirm_mode": False,
    "soft_lock_ttl_seconds": 180,
    "offline_timeout_seconds": 900,
    "ssim_change_threshold": 0.035,
    "standard_car_length_m": 5.2,
    "http_port": int(os.environ.get("PORT", "8790")),
    "ftp_port": int(os.environ.get("FTP_PORT", "2121")),
    "ftp_user": "camera",
    "ftp_password": "parking2026",
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
                self._deep_merge(self._config, saved)
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
