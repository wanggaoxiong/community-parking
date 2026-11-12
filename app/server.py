import base64
import json
import mimetypes
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from app.config import BASE_DIR, config_manager
from app.database import Database, db as default_db
from app.homography import subdivide_perspective_row
from app.recommender import ParkingRecommender
from app.state_machine import SpotStateMachine
from app.vision_pipeline import VisionPipeline

STATIC_DIR = BASE_DIR / "static"


class ParkingAppContext:
    def __init__(self, database: Optional[Database] = None) -> None:
        self.db = database or default_db
        self.fsm = SpotStateMachine(self.db)
        self.vision = VisionPipeline(self.db, self.fsm)
        self.recommender = ParkingRecommender(self.db)
        self.recent_events: list[Dict[str, Any]] = []

    def push_event(self, event: Dict[str, Any]) -> None:
        self.recent_events.insert(0, event)
        if len(self.recent_events) > 30:
            del self.recent_events[30:]

    def handle_ftp_snapshot(self, camera_id: str, raw_bytes: bytes, virtual_path: str) -> None:
        try:
            res = self.vision.process_snapshot_bytes(camera_id, raw_bytes)
            self.push_event(
                {
                    "ts": time.time(),
                    "source": f"FTP ({virtual_path})",
                    "camera_id": camera_id,
                    "elapsed_ms": res["elapsed_ms"],
                    "processed_spots": res["processed_spots"],
                }
            )
        except Exception as exc:
            self.push_event(
                {
                    "ts": time.time(),
                    "source": f"FTP ({virtual_path})",
                    "camera_id": camera_id,
                    "error": str(exc),
                }
            )


app_ctx = ParkingAppContext()


def make_http_handler(ctx: ParkingAppContext = app_ctx):
    class ParkingRequestHandler(BaseHTTPRequestHandler):
        server_version = "CommunityParking/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            return  # Keep console clean

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length > 0 else b""

        def _read_json(self) -> Dict[str, Any]:
            raw = self._read_body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            try:
                if path == "/api/health":
                    self._send_json({"ok": True, "ts": time.time()})
                    return

                if path == "/api/overview":
                    vtype = qs.get("vehicle_type", ["sedan"])[0]
                    cfg = config_manager.get_public()
                    ctx.fsm.check_offline_timeouts(float(cfg.get("offline_timeout_seconds", 900)))
                    zones = ctx.recommender.get_zone_summaries(vehicle_type=vtype)
                    total_free = sum(z["effective_free"] for z in zones)
                    total_cap = sum(z["total_capacity"] for z in zones)
                    self._send_json(
                        {
                            "community_name": cfg["community_name"],
                            "total_capacity": total_cap,
                            "total_effective_free": total_free,
                            "units": ctx.db.list_units(),
                            "zones": zones,
                            "cameras": ctx.db.list_cameras(),
                            "active_locks": ctx.recommender.list_active_locks(),
                            "privacy_stats": ctx.vision.privacy_stats,
                            "recent_events": ctx.recent_events[:12],
                            "config": cfg,
                        }
                    )
                    return

                if path == "/api/recommend/voice":
                    # Direct Siri / iOS Shortcuts / CarPlay one-URL endpoint
                    unit_id = qs.get("unit_id", ["3-1"])[0]
                    vtype = qs.get("vehicle_type", ["sedan"])[0]
                    res = ctx.recommender.recommend(
                        unit_id=unit_id, vehicle_type=vtype, driver_tag="车载语音快捷指令", acquire_lock=True
                    )
                    if qs.get("format", ["text"])[0] == "json":
                        self._send_json(res)
                    else:
                        self._send_text(res["voice_text"])
                    return

                if path == "/api/cameras":
                    self._send_json({"cameras": ctx.db.list_cameras()})
                    return

                m_spots = re.match(r"^/api/cameras/([^/]+)/spots$", path)
                if m_spots:
                    cam_id = m_spots.group(1)
                    cam = ctx.db.get_camera(cam_id)
                    if not cam:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    self._send_json(
                        {
                            "camera": cam,
                            "spots": ctx.db.list_spots(camera_id=cam_id),
                        }
                    )
                    return

                if path == "/api/config":
                    self._send_json(config_manager.get_public())
                    return

                # Serve static frontend files
                self._serve_static(path)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            try:
                if path == "/api/recommend":
                    body = self._read_json()
                    res = ctx.recommender.recommend(
                        unit_id=str(body.get("unit_id", "3-1")),
                        vehicle_type=str(body.get("vehicle_type", "sedan")),
                        driver_tag=str(body.get("driver_tag", "小区车主")),
                        acquire_lock=bool(body.get("acquire_lock", True)),
                    )
                    self._send_json(res)
                    return

                if path == "/api/calibration/subdivide":
                    body = self._read_json()
                    trapezoids = subdivide_perspective_row(
                        outer_quad=body["outer_quad"],
                        count=int(body.get("count", 5)),
                        direction=str(body.get("direction", "auto")),
                        foreshortening_gamma=float(body.get("foreshortening_gamma", 1.0)),
                        gap_ratio=float(body.get("gap_ratio", 0.02)),
                    )
                    self._send_json({"trapezoids": trapezoids, "count": len(trapezoids)})
                    return

                if path == "/api/cameras":
                    body = self._read_json()
                    cam = ctx.db.upsert_camera(
                        camera_id=str(body["camera_id"]).strip(),
                        name=str(body["name"]).strip(),
                        zone_id=str(body["zone_id"]).strip(),
                        mount_type=str(body.get("mount_type", "业主窗台Wi-Fi")),
                        image_width=int(body.get("image_width", 1280)),
                        image_height=int(body.get("image_height", 720)),
                    )
                    self._send_json({"camera": cam})
                    return

                m_spots = re.match(r"^/api/cameras/([^/]+)/spots$", path)
                if m_spots:
                    cam_id = m_spots.group(1)
                    body = self._read_json()
                    cam = ctx.db.get_camera(cam_id)
                    if not cam:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    saved = ctx.db.save_camera_spots(
                        camera_id=cam_id,
                        zone_id=str(body.get("zone_id") or cam["zone_id"]),
                        spots_payload=body.get("spots", []),
                        image_width=int(body.get("image_width") or cam["image_width"]),
                        image_height=int(body.get("image_height") or cam["image_height"]),
                    )
                    ctx.vision.clear_frame_cache(cam_id)
                    self._send_json({"camera_id": cam_id, "spots": saved})
                    return

                m_snap = re.match(r"^/api/cameras/([^/]+)/snapshot$", path)
                if m_snap:
                    cam_id = m_snap.group(1)
                    raw_img, opts = self._extract_snapshot_request(qs)
                    sim_offset = float(opts.get("time_offset_seconds", 0.0))
                    sim_ts = time.time() + sim_offset
                    res = ctx.vision.process_snapshot_bytes(
                        camera_id=cam_id,
                        raw_image_bytes=raw_img,
                        force_reanalyze=bool(opts.get("force_reanalyze", False)),
                        override_instant_confirm=opts.get("instant_confirm"),
                        simulated_timestamp=sim_ts,
                    )
                    del raw_img
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": str(opts.get("source_label", "HTTP抓拍上传")),
                            "camera_id": cam_id,
                            "elapsed_ms": res["elapsed_ms"],
                            "processed_spots": res["processed_spots"],
                        }
                    )
                    self._send_json(res)
                    return

                if path == "/api/config":
                    body = self._read_json()
                    updated = config_manager.update(body)
                    self._send_json(updated)
                    return

                if path == "/api/vlm/test":
                    body = self._read_json()
                    res = ctx.vision.test_vlm_connection(body.get("vlm") or body)
                    self._send_json(res, 200 if res.get("ok") else 400)
                    return

                if path == "/api/reset":
                    ctx.db.reset_seed_data()
                    ctx.vision.clear_frame_cache()
                    ctx.recommender.clear_locks()
                    ctx.recent_events.clear()
                    self._send_json({"ok": True, "message": "已恢复示例小区初始状态"})
                    return

                self._send_json({"error": f"Unknown endpoint {path}"}, 404)
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/locks":
                ctx.recommender.clear_locks()
                self._send_json({"ok": True})
                return
            self._send_json({"error": "Not found"}, 404)

        def _extract_snapshot_request(
            self, qs: Dict[str, list[str]]
        ) -> Tuple[bytes, Dict[str, Any]]:
            ctype = self.headers.get("Content-Type", "")
            raw = self._read_body()
            opts: Dict[str, Any] = {
                "force_reanalyze": qs.get("force_reanalyze", ["false"])[0].lower() == "true",
                "time_offset_seconds": float(qs.get("time_offset_seconds", ["0"])[0]),
            }
            if "instant_confirm" in qs:
                opts["instant_confirm"] = qs["instant_confirm"][0].lower() == "true"

            if "application/json" in ctype:
                payload = json.loads(raw.decode("utf-8"))
                b64 = str(payload.get("image_base64", ""))
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                if "force_reanalyze" in payload:
                    opts["force_reanalyze"] = bool(payload["force_reanalyze"])
                if "instant_confirm" in payload and payload["instant_confirm"] is not None:
                    opts["instant_confirm"] = bool(payload["instant_confirm"])
                if "time_offset_seconds" in payload:
                    opts["time_offset_seconds"] = float(payload["time_offset_seconds"])
                if "source_label" in payload:
                    opts["source_label"] = payload["source_label"]
                return img_bytes, opts

            # Raw binary JPEG/PNG upload (e.g. directly from ESP32-CAM HTTP POST)
            opts["source_label"] = "ESP32/IPC 二进制直传"
            return raw, opts

        def _serve_static(self, req_path: str) -> None:
            rel = "index.html" if req_path in ("/", "") else req_path.lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.is_file():
                self._send_json({"error": "Not found"}, 404)
                return
            mime, _ = mimetypes.guess_type(str(file_path))
            content = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", (mime or "application/octet-stream") + "; charset=utf-8" if (mime or "").startswith("text/") or str(file_path).endswith(".js") else (mime or "application/octet-stream"))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)

    return ParkingRequestHandler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def create_http_server(
    host: str = "127.0.0.1", port: int = 8790, ctx: ParkingAppContext = app_ctx
) -> ThreadingHTTPServer:
    handler_cls = make_http_handler(ctx)
    return ReusableThreadingHTTPServer((host, port), handler_cls)
