import base64
import hashlib
import json
import mimetypes
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from app.auto_calibrator import ReferenceAutoCalibrator
from app.camera_ingestor import (
    CAMERA_PROTOCOL_SPECS,
    CAMERA_VENDOR_PRESETS,
    CameraIngestionScheduler,
    discover_lan_cameras,
    format_local_ts,
    list_camera_presets_payload,
    normalize_ingest_config,
    probe_onvif_camera,
    sanitize_ingest_config_for_public,
    test_camera_connection,
)
from app.camera_naming import (
    VEHICLE_MODEL_PRESETS,
    generate_spots_along_route,
    parse_camera_group_codes,
)
from app.config import BASE_DIR, config_manager
from app.database import Database, db as default_db
from app.homography import (
    derive_vehicle_envelope_polygon,
    subdivide_curved_row,
    subdivide_perspective_row,
)
from app.recommender import ParkingRecommender
from app.sitemap_generator import SiteMapGenerator
from app.state_machine import SpotStateMachine
from app.vision_pipeline import VisionPipeline

STATIC_DIR = BASE_DIR / "static"
MAX_HTTP_BODY_BYTES = 16 * 1024 * 1024  # 16 MB hard memory-safety limit


def _compute_frontend_version() -> str:
    h = hashlib.sha1()
    for name in ("index.html", "app.js", "styles.css"):
        fp = STATIC_DIR / name
        if fp.is_file():
            st = fp.stat()
            h.update(f"{name}:{st.st_size}:{st.st_mtime_ns};".encode("utf-8"))
    return h.hexdigest()[:10]


class PayloadTooLargeError(Exception):
    """Raised when an incoming HTTP payload exceeds MAX_HTTP_BODY_BYTES."""


class ParkingAppContext:
    def __init__(self, database: Optional[Database] = None) -> None:
        self.db = database or default_db
        self.fsm = SpotStateMachine(self.db)
        self.vision = VisionPipeline(self.db, self.fsm)
        self.calibrator = ReferenceAutoCalibrator(self.db, self.vision)
        self.sitemap_gen = SiteMapGenerator(self.db, self.vision)
        self.recommender = ParkingRecommender(self.db)
        self.vision.set_recommender(self.recommender)
        self.recent_events: list[Dict[str, Any]] = []
        self._sched_stop = threading.Event()
        self._sched_thread: Optional[threading.Thread] = None
        self.camera_ingestor = CameraIngestionScheduler(
            db=self.db,
            vision_pipeline=self.vision,
            event_callback=self.push_event,
        )

    @property
    def ingestor(self) -> CameraIngestionScheduler:
        return self.camera_ingestor

    def start_camera_ingestor(self, tick_seconds: float = 1.5) -> None:
        self.camera_ingestor.start(tick_seconds=tick_seconds)

    def stop_camera_ingestor(self) -> None:
        self.camera_ingestor.stop()

    def run_due_calib_checks(self, now_ts: Optional[float] = None) -> list[Dict[str, Any]]:
        """
        Executes scheduled / nightly calibration drift checks for any camera whose
        `calib_schedule.enabled` is True and `next_check_ts <= now_ts`.
        """
        cur_ts = float(now_ts if now_ts is not None else time.time())
        triggered: list[Dict[str, Any]] = []
        for cam in self.db.list_cameras():
            sched = cam.get("calib_schedule") or {}
            if not sched.get("enabled"):
                continue
            next_ts = float(sched.get("next_check_ts") or 0.0)
            if next_ts <= 0.0 or cur_ts < next_ts:
                continue
            cam_id = str(cam["camera_id"])
            try:
                res = self.vision.verify_camera_calibration(
                    camera_id=cam_id,
                    raw_image_bytes=None,
                    apply_polygon_shift=bool(sched.get("auto_compensate_shift", False)),
                    source="scheduled",
                )
                health = res.get("calib_health") or {}
                self.push_event(
                    {
                        "ts": cur_ts,
                        "source": f"自动标定核对 ({health.get('status', 'OK')})",
                        "camera_id": cam_id,
                        "elapsed_ms": res.get("elapsed_ms", 0.0),
                        "processed_spots": cam.get("spots_count", 0),
                    }
                )
                triggered.append(res)
            except Exception as exc:
                self.push_event(
                    {
                        "ts": cur_ts,
                        "source": "自动标定核对异常",
                        "camera_id": cam_id,
                        "error": str(exc),
                    }
                )
        return triggered

    def start_calib_scheduler(self, poll_seconds: float = 20.0) -> None:
        if self._sched_thread is not None and self._sched_thread.is_alive():
            return
        self._sched_stop.clear()

        def _loop() -> None:
            while not self._sched_stop.wait(poll_seconds):
                try:
                    self.run_due_calib_checks()
                except Exception:
                    pass

        self._sched_thread = threading.Thread(
            target=_loop, name="CalibrationDriftScheduler", daemon=True
        )
        self._sched_thread.start()

    def stop_calib_scheduler(self) -> None:
        self._sched_stop.set()
        th = self._sched_thread
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        self._sched_thread = None

    def push_event(self, event: Dict[str, Any]) -> None:
        self.recent_events.insert(0, event)
        if len(self.recent_events) > 30:
            del self.recent_events[30:]

    def handle_ftp_snapshot(self, camera_id: str, raw_bytes: bytes, virtual_path: str) -> None:
        try:
            res = self.vision.process_snapshot_bytes(camera_id, raw_bytes)
            now_ts = time.time()
            prev_rec = self.db.get_camera_ingest_record(camera_id) or {}
            prev_st = prev_rec.get("ingest_status") or {}
            self.db.update_camera_ingest_status(
                camera_id,
                {
                    "state": "push_active",
                    "last_pull_at": now_ts,
                    "last_ok_ts": now_ts,
                    "last_ok_at": format_local_ts(now_ts),
                    "frames_pulled": int(prev_st.get("frames_pulled") or 0) + 1,
                    "last_latency_ms": float(res.get("elapsed_ms") or 0.0),
                    "transport": f"FTP Push ({virtual_path})",
                    "consecutive_failures": 0,
                    "last_error": "",
                },
            )
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
            self.db.update_camera_ingest_status(
                camera_id,
                {
                    "state": "error",
                    "last_pull_at": time.time(),
                    "transport": f"FTP Push ({virtual_path})",
                    "last_error": str(exc)[:240],
                },
            )
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

        def _send_json(
            self,
            data: Any,
            status: int = 200,
            extra_headers: Optional[Dict[str, str]] = None,
        ) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                if extra_headers:
                    for hk, hv in extra_headers.items():
                        self.send_header(hk, hv)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_text(self, text: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body(self) -> bytes:
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                return b""
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise ValueError("Invalid Content-Length header") from exc
            if length < 0:
                raise ValueError("Negative Content-Length header")
            if length > MAX_HTTP_BODY_BYTES:
                self.close_connection = True
                raise PayloadTooLargeError(
                    f"上传内容大小 ({length / (1024 * 1024):.1f} MB) 超过内存保护上限 "
                    f"({MAX_HTTP_BODY_BYTES // (1024 * 1024)} MB)"
                )
            return self.rfile.read(length) if length > 0 else b""

        @staticmethod
        def _decode_base64_image(b64_str: str, missing_msg: str = "缺少图像数据 image_base64") -> bytes:
            b64 = str(b64_str or "").strip()
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            if not b64:
                raise ValueError(missing_msg)
            if len(b64) > (MAX_HTTP_BODY_BYTES * 4 // 3) + 1024:
                raise PayloadTooLargeError("Base64 图像尺寸超过 16MB 内存保护上限")
            raw = base64.b64decode(b64)
            if len(raw) > MAX_HTTP_BODY_BYTES:
                raise PayloadTooLargeError("解码后图像超过 16MB 内存保护上限")
            return raw

        def _read_json(self) -> Dict[str, Any]:
            raw = self._read_body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Parking-Client")
            self.end_headers()

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self._serve_static(parsed.path, head_only=True)

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
                    sel_uid = (qs.get("unit_id") or qs.get("unit") or [""])[0] or None
                    cfg = config_manager.get_public()
                    ctx.fsm.check_offline_timeouts(float(cfg.get("offline_timeout_seconds", 900)))
                    zones = ctx.recommender.get_zone_summaries(vehicle_type=vtype)
                    units_list = ctx.db.list_units()
                    cams_list = ctx.db.list_cameras()
                    smap_meta = ctx.db.get_site_map(include_backdrop=False)
                    spatial_gov = ctx.recommender.build_spatial_governance(
                        zone_summaries=zones,
                        units=units_list,
                        cameras=cams_list,
                        site_map=smap_meta,
                        selected_unit_id=sel_uid,
                    )
                    total_free = sum(z["effective_free"] for z in zones)
                    total_cap = sum(z["total_capacity"] for z in zones)
                    fe_ver = _compute_frontend_version()
                    client_ver = (self.headers.get("X-Parking-Client") or "").strip()
                    ua = self.headers.get("User-Agent") or ""
                    building_clusters = spatial_gov["building_clusters"]
                    extra_hdrs: Optional[Dict[str, str]] = None
                    if "Mozilla/" in ua and not client_ver:
                        # Protect already-open unrefreshed browser tabs still running older in-memory JS
                        # (which lacked getEffectiveSpotStatusClient in renderBuildingClusters) AND flush cache.
                        building_clusters = [
                            {
                                **bc,
                                "groups": [
                                    {**grp, "spots": []}
                                    for grp in (bc.get("groups") or [])
                                ],
                            }
                            for bc in (building_clusters or [])
                        ]
                        extra_hdrs = {"Clear-Site-Data": '"cache"'}
                    self._send_json(
                        {
                            "community_name": cfg["community_name"],
                            "frontend_version": fe_ver,
                            "total_capacity": total_cap,
                            "total_effective_free": total_free,
                            "units": units_list,
                            "zones": zones,
                            "cameras": cams_list,
                            "site_map": smap_meta,
                            "spot_groups": spatial_gov["spot_groups"],
                            "unbound_cameras": spatial_gov["unbound_cameras"],
                            "building_clusters": building_clusters,
                            "active_locks": ctx.recommender.list_active_locks(),
                            "privacy_stats": ctx.vision.get_privacy_stats(),
                            "camera_ingestor": {
                                "running": ctx.camera_ingestor.is_running,
                                "vendor_count": len(CAMERA_VENDOR_PRESETS),
                                "protocol_count": len(CAMERA_PROTOCOL_SPECS),
                            },
                            "recent_events": ctx.recent_events[:12],
                            "config": cfg,
                        },
                        extra_headers=extra_hdrs,
                    )
                    return

                if path == "/api/units":
                    smap_u = ctx.db.get_site_map(include_backdrop=False)
                    gates_u = (smap_u.get("topology") or {}).get("gates") or []
                    self._send_json(
                        {
                            "units": ctx.db.list_units(),
                            "gates": gates_u,
                        }
                    )
                    return

                if path == "/api/zones":
                    self._send_json(
                        {
                            "zones": ctx.db.list_zones(),
                        }
                    )
                    return

                if path == "/api/sitemap":
                    self._send_json(
                        {
                            "site_map": ctx.db.get_site_map(include_backdrop=True),
                            "zones": ctx.db.list_zones(),
                            "units": ctx.db.list_units(),
                        }
                    )
                    return

                if path == "/api/recommend":
                    unit_id = (qs.get("unit_id") or qs.get("unit") or ["3-1"])[0]
                    vtype = (qs.get("vehicle_type") or [""])[0]
                    if not vtype:
                        car_len = float((qs.get("car_length") or ["4.65"])[0] or 4.65)
                        vtype = "suv" if car_len >= 4.85 else ("compact" if car_len <= 4.1 else "sedan")
                    acq = (qs.get("acquire_lock") or ["1"])[0] not in ("0", "false", "False")
                    entry_gate_id = (qs.get("entry_gate_id") or qs.get("gate_id") or [""])[0] or None
                    res = ctx.recommender.recommend(
                        unit_id=unit_id,
                        vehicle_type=vtype,
                        driver_tag=(qs.get("driver_tag") or ["小区车主"])[0],
                        acquire_lock=acq,
                        entry_gate_id=entry_gate_id,
                    )
                    self._send_json(res)
                    return

                if path == "/api/recommend/voice":
                    # Direct Siri / iOS Shortcuts / CarPlay one-URL endpoint
                    unit_id = qs.get("unit_id", ["3-1"])[0]
                    vtype = qs.get("vehicle_type", ["sedan"])[0]
                    entry_gate_id = (qs.get("entry_gate_id") or qs.get("gate_id") or [""])[0] or None
                    res = ctx.recommender.recommend(
                        unit_id=unit_id,
                        vehicle_type=vtype,
                        driver_tag="车载语音快捷指令",
                        acquire_lock=True,
                        entry_gate_id=entry_gate_id,
                    )
                    if qs.get("format", ["text"])[0] == "json":
                        self._send_json(res)
                    else:
                        self._send_text(res["voice_text"])
                    return

                if path == "/api/cameras/presets":
                    self._send_json(list_camera_presets_payload())
                    return

                if path == "/api/cameras":
                    self._send_json(
                        {
                            "cameras": ctx.db.list_cameras(),
                            "vehicle_models": VEHICLE_MODEL_PRESETS,
                            "camera_presets": list_camera_presets_payload(),
                        }
                    )
                    return

                m_spots = re.match(r"^/api/cameras/([^/]+)/spots$", path)
                if m_spots:
                    cam_id = unquote(m_spots.group(1))
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

                m_ff = re.match(r"^/api/cameras/([^/]+)/full-frame$", path)
                if m_ff:
                    cam_id = unquote(m_ff.group(1))
                    ff_data = ctx.db.get_camera_full_frame(cam_id)
                    if not ff_data:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    if not ff_data.get("full_frame_jpeg_b64"):
                        cam_blobs = ctx.db.get_camera(cam_id, include_blobs=True)
                        if cam_blobs and cam_blobs.get("template_jpeg_b64"):
                            ff_data["full_frame_jpeg_b64"] = cam_blobs["template_jpeg_b64"]
                            ff_data["has_full_frame"] = True
                    self._send_json(ff_data)
                    return

                m_vtpl = re.match(r"^/api/cameras/([^/]+)/vehicle-template$", path)
                if m_vtpl:
                    cam_id = unquote(m_vtpl.group(1))
                    cam_blobs = ctx.db.get_camera(cam_id, include_blobs=True)
                    if not cam_blobs:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    self._send_json(
                        {
                            "camera_id": cam_id,
                            "has_vehicle_template": bool(cam_blobs.get("has_vehicle_template")),
                            "image_width": cam_blobs["image_width"],
                            "image_height": cam_blobs["image_height"],
                            "vehicle_template_meta": cam_blobs.get("vehicle_template_meta"),
                            "vehicle_template_jpeg_b64": cam_blobs.get("vehicle_template_jpeg_b64") or "",
                        }
                    )
                    return

                m_tpl = re.match(r"^/api/cameras/([^/]+)/template$", path)
                if m_tpl:
                    cam_id = unquote(m_tpl.group(1))
                    cached_tpl = ctx.vision.get_cached_camera_template(cam_id)
                    if not cached_tpl:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    cam_meta_only = ctx.db.get_camera(cam_id, include_blobs=False)
                    if cam_meta_only:
                        cached_tpl["has_vehicle_template"] = bool(cam_meta_only.get("has_vehicle_template"))
                        cached_tpl["vehicle_template_meta"] = cam_meta_only.get("vehicle_template_meta")
                        cached_tpl["anchor_points"] = cam_meta_only.get("anchor_points") or []
                        cached_tpl["anchor_points_custom"] = bool(cam_meta_only.get("anchor_points_custom"))
                    self._send_json(cached_tpl)
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
                    raw_eg = body.get("entry_gate_id")
                    res = ctx.recommender.recommend(
                        unit_id=str(body.get("unit_id", "3-1")),
                        vehicle_type=str(body.get("vehicle_type", "sedan")),
                        driver_tag=str(body.get("driver_tag", "小区车主")),
                        acquire_lock=bool(body.get("acquire_lock", True)),
                        entry_gate_id=str(raw_eg).strip() if raw_eg is not None else None,
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

                if path == "/api/calibration/subdivide-curve":
                    body = self._read_json()
                    quads = subdivide_curved_row(
                        front_curve=body["front_curve"],
                        back_curve=body["back_curve"],
                        count=int(body.get("count", 6)),
                        gap_ratio=float(body.get("gap_ratio", 0.0)),
                    )
                    self._send_json({"trapezoids": quads, "count": len(quads)})
                    return

                if path == "/api/calibration/derive-vehicle-polygons":
                    body = self._read_json()
                    img_w = max(64, int(body.get("image_width") or 1280))
                    img_h = max(64, int(body.get("image_height") or 720))
                    h_scale = float(body.get("height_scale", 0.32))
                    s_bias = float(body.get("slant_bias", 0.0))
                    in_spots = body.get("spots") or []
                    out_spots = []
                    vpolys = []
                    for item in in_spots:
                        gpoly = item.get("polygon") if isinstance(item, dict) else item
                        stype = item.get("spot_type", "standard") if isinstance(item, dict) else "standard"
                        if stype == "elastic_strip":
                            vp = [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in gpoly]
                        else:
                            vp = derive_vehicle_envelope_polygon(
                                gpoly, img_w, img_h, height_scale=h_scale, slant_bias=s_bias
                            )
                        vpolys.append(vp)
                        if isinstance(item, dict):
                            out_spots.append({**item, "vehicle_polygon": vp, "vehicle_poly_custom": False})
                    self._send_json(
                        {
                            "ok": True,
                            "vehicle_polygons": vpolys,
                            "spots": out_spots,
                            "count": len(vpolys),
                            "height_scale": h_scale,
                            "slant_bias": s_bias,
                        }
                    )
                    return

                if path == "/api/cameras/parse-code":
                    body = self._read_json()
                    parsed_code = parse_camera_group_codes(
                        expr=str(body.get("group_code", "")),
                        mount_address=str(body.get("mount_address", "")),
                    )
                    self._send_json({**parsed_code, "vehicle_models": VEHICLE_MODEL_PRESETS})
                    return

                if path == "/api/cameras/preview-url":
                    body = self._read_json()
                    cam_id_hint = str(body.get("camera_id") or "").strip()
                    existing_ic: Optional[Dict[str, Any]] = None
                    if cam_id_hint:
                        rec_hint = ctx.db.get_camera_ingest_record(cam_id_hint)
                        if rec_hint:
                            existing_ic = rec_hint.get("ingest_config")
                    raw_ic = body.get("ingest_config") if isinstance(body.get("ingest_config"), dict) else body
                    norm_ic = normalize_ingest_config(raw_ic, existing_ic)
                    self._send_json(
                        {
                            "ok": True,
                            "ingest_config": sanitize_ingest_config_for_public(
                                norm_ic, camera_id=cam_id_hint or "<camera_id>"
                            ),
                        }
                    )
                    return

                if path == "/api/cameras/discover":
                    body = self._read_json() if self.headers.get("Content-Length", "0") != "0" else {}
                    t_sec = max(0.4, min(5.0, float(body.get("timeout_sec", 1.8) or 1.8)))
                    raw_targets = body.get("target_hosts")
                    if isinstance(raw_targets, str):
                        target_hosts = [s.strip() for s in re.split(r"[,;\s]+", raw_targets) if s.strip()]
                    elif isinstance(raw_targets, list):
                        target_hosts = [str(s).strip() for s in raw_targets if str(s).strip()]
                    else:
                        target_hosts = None
                    devices = discover_lan_cameras(target_hosts=target_hosts, timeout_sec=t_sec)
                    self._send_json(
                        {
                            "ok": True,
                            "count": len(devices),
                            "devices": devices,
                        }
                    )
                    return

                if path == "/api/cameras/onvif-probe":
                    body = self._read_json()
                    cam_id_hint = str(body.get("camera_id") or "").strip()
                    existing_ic = None
                    if cam_id_hint:
                        rec_hint = ctx.db.get_camera_ingest_record(cam_id_hint)
                        if rec_hint:
                            existing_ic = rec_hint.get("ingest_config")
                    raw_ic = body.get("ingest_config") if isinstance(body.get("ingest_config"), dict) else body
                    merged_ic = normalize_ingest_config(raw_ic, existing_ic)
                    probe_res = probe_onvif_camera(
                        host=str(merged_ic.get("host") or body.get("host") or ""),
                        onvif_port=int(merged_ic.get("onvif_port") or body.get("onvif_port") or 80),
                        username=str(merged_ic.get("username") or ""),
                        password=str(merged_ic.get("password") or ""),
                        timeout_sec=max(1.5, min(10.0, float(body.get("timeout_sec", 4.5) or 4.5))),
                    )
                    self._send_json(probe_res, 200 if probe_res.get("ok") else 400)
                    return

                if path == "/api/cameras/test-connection":
                    body = self._read_json()
                    cam_id_hint = str(body.get("camera_id") or "").strip()
                    existing_ic = None
                    if cam_id_hint:
                        rec_hint = ctx.db.get_camera_ingest_record(cam_id_hint)
                        if rec_hint:
                            existing_ic = rec_hint.get("ingest_config")
                    raw_ic = body.get("ingest_config") if isinstance(body.get("ingest_config"), dict) else body
                    merged_ic = normalize_ingest_config(raw_ic, existing_ic)
                    test_res = test_camera_connection(merged_ic)
                    if cam_id_hint and ctx.db.get_camera(cam_id_hint):
                        now_ts = time.time()
                        prev_st = (existing_rec or {}).get("ingest_status") or {}
                        if test_res.get("ok"):
                            ctx.db.update_camera_ingest_status(
                                cam_id_hint,
                                {
                                    "state": "online",
                                    "last_pull_at": now_ts,
                                    "last_ok_ts": now_ts,
                                    "last_ok_at": format_local_ts(now_ts),
                                    "frames_pulled": int(prev_st.get("frames_pulled") or 0) + (1 if test_res.get("preview_jpeg_b64") else 0),
                                    "last_latency_ms": test_res.get("latency_ms", 0.0),
                                    "last_resolution": f"{test_res.get('width', 0)}x{test_res.get('height', 0)}" if test_res.get("width") else prev_st.get("last_resolution", ""),
                                    "transport": test_res.get("transport", ""),
                                    "consecutive_failures": 0,
                                    "last_error": "",
                                },
                            )
                            if body.get("save_as_reference", True) and test_res.get("preview_jpeg_b64"):
                                ctx.db.save_camera_latest_frame(
                                    cam_id_hint,
                                    str(test_res["preview_jpeg_b64"]),
                                    int(test_res.get("width") or 0),
                                    int(test_res.get("height") or 0),
                                )
                                test_res["saved_as_reference"] = True
                        else:
                            ctx.db.update_camera_ingest_status(
                                cam_id_hint,
                                {
                                    "state": "error",
                                    "last_pull_at": time.time(),
                                    "transport": test_res.get("transport", ""),
                                    "last_error": str(test_res.get("error") or "")[:240],
                                },
                            )
                    self._send_json(test_res, 200 if test_res.get("ok") else 400)
                    return

                if path == "/api/cameras":
                    body = self._read_json()
                    cam = ctx.db.upsert_camera(
                        camera_id=str(body.get("camera_id", "")).strip(),
                        name=str(body.get("name", "")).strip(),
                        zone_id=str(body.get("zone_id", "")).strip(),
                        mount_type=str(body.get("mount_type", "业主窗台Wi-Fi")),
                        image_width=int(body.get("image_width", 1280)),
                        image_height=int(body.get("image_height", 720)),
                        group_code=str(body.get("group_code", "")).strip(),
                        mount_address=str(body.get("mount_address", "")).strip(),
                        auto_name=bool(body.get("auto_name", False)),
                        scaffold_spots=bool(body.get("scaffold_spots", False)),
                        bind_group_codes=body.get("bind_group_codes"),
                        ingest_config=body.get("ingest_config") if isinstance(body.get("ingest_config"), dict) else None,
                        bind_spot_group=body.get("bind_spot_group"),
                        unbind_spot_groups=bool(body.get("unbind_spot_groups", False)),
                        group_name=body.get("group_name"),
                        near_unit_id=body.get("near_unit_id"),
                    )
                    ctx.vision.clear_frame_cache(cam["camera_id"])
                    ctx.camera_ingestor.notify_config_changed(cam["camera_id"])
                    self._send_json({"ok": True, "camera": cam, "spots": ctx.db.list_spots(camera_id=cam["camera_id"])})
                    return

                m_cam_icfg = re.match(r"^/api/cameras/([^/]+)/ingest-config$", path)
                if m_cam_icfg:
                    cam_id = unquote(m_cam_icfg.group(1))
                    body = self._read_json()
                    raw_ic = body.get("ingest_config") if isinstance(body.get("ingest_config"), dict) else body
                    updated_cam = ctx.db.save_camera_ingest_config(cam_id, raw_ic)
                    ctx.camera_ingestor.notify_config_changed(cam_id)
                    self._send_json({"ok": True, "camera": updated_cam})
                    return

                m_cam_pull = re.match(r"^/api/cameras/([^/]+)/pull-now$", path)
                if m_cam_pull:
                    cam_id = unquote(m_cam_pull.group(1))
                    body = self._read_json() if self.headers.get("Content-Length", "0") != "0" else {}
                    pull_res = ctx.camera_ingestor.pull_camera_once(
                        cam_id,
                        process_vision=bool(body.get("process_vision", True)),
                        save_reference_frame=bool(body.get("save_reference_frame", True)),
                    )
                    pull_res["camera"] = ctx.db.get_camera(cam_id)
                    pull_res["spots"] = ctx.db.list_spots(camera_id=cam_id)
                    self._send_json(pull_res, 200 if pull_res.get("ok") else 400)
                    return

                m_cam_bind = re.match(r"^/api/cameras/([^/]+)/bind$", path)
                if m_cam_bind:
                    cam_id = unquote(m_cam_bind.group(1))
                    body = self._read_json()
                    gc = str(body.get("group_code") or body.get("bind_spot_group") or "").strip()
                    if not gc or gc in {"__UNBIND__", "UNASSIGNED", "NONE"}:
                        res = ctx.db.unbind_camera(cam_id)
                        ctx.vision.clear_frame_cache(cam_id)
                        ctx.push_event(
                            {
                                "ts": time.time(),
                                "source": f"摄像头解除绑定 ({cam_id})",
                                "camera_id": cam_id,
                                "elapsed_ms": 0.0,
                                "processed_spots": len(res.get("detached_groups") or []),
                            }
                        )
                        self._send_json({"ok": True, **res})
                        return
                    res = ctx.db.bind_camera_to_spot_group(
                        camera_id=cam_id,
                        group_code=gc,
                        group_name=body.get("group_name"),
                        near_unit_id=body.get("near_unit_id"),
                        zone_id=body.get("zone_id"),
                    )
                    ctx.vision.clear_frame_cache(cam_id)
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": f"摄像头绑定平面图车位带 ({cam_id} ⇄ {gc})",
                            "camera_id": cam_id,
                            "elapsed_ms": 0.0,
                            "processed_spots": int(res.get("bound_spots") or 0),
                        }
                    )
                    self._send_json(
                        {
                            "ok": True,
                            **res,
                            "camera": ctx.db.get_camera(cam_id),
                            "spots": ctx.db.list_spots(camera_id=cam_id),
                        }
                    )
                    return

                m_cam_unbind = re.match(r"^/api/cameras/([^/]+)/unbind$", path)
                if m_cam_unbind:
                    cam_id = unquote(m_cam_unbind.group(1))
                    res = ctx.db.unbind_camera(cam_id)
                    ctx.vision.clear_frame_cache(cam_id)
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": f"摄像头解除绑定 ({cam_id})",
                            "camera_id": cam_id,
                            "elapsed_ms": 0.0,
                            "processed_spots": len(res.get("detached_groups") or []),
                        }
                    )
                    self._send_json({"ok": True, **res})
                    return

                m_cam_upd = re.match(r"^/api/cameras/([^/]+)/update$", path)
                if m_cam_upd:
                    cam_id = unquote(m_cam_upd.group(1))
                    body = self._read_json()
                    updated_cam = ctx.db.update_camera(cam_id, body)
                    ctx.vision.clear_frame_cache(cam_id)
                    ctx.camera_ingestor.notify_config_changed(cam_id)
                    if updated_cam["camera_id"] != cam_id:
                        ctx.vision.clear_frame_cache(updated_cam["camera_id"])
                    self._send_json(
                        {
                            "ok": True,
                            "camera": updated_cam,
                            "spots": ctx.db.list_spots(camera_id=updated_cam["camera_id"]),
                        }
                    )
                    return

                m_gtpl_post = re.match(r"^/api/cameras/([^/]+)/ground-template$", path)
                if m_gtpl_post:
                    cam_id = unquote(m_gtpl_post.group(1))
                    ctype = self.headers.get("Content-Type", "")
                    zone_override: Optional[str] = None
                    if "application/json" in ctype:
                        body = self._read_json()
                        zone_override = str(body.get("zone_id") or "").strip() or None
                        raw_img = self._decode_base64_image(
                            str(body.get("image_base64", "")), "缺少无车地面参考图 image_base64"
                        )
                        del body
                    else:
                        raw_img = self._read_body()
                        if not raw_img:
                            raise ValueError("缺少无车地面参考图二进制内容")
                    res = ctx.calibrator.save_ground_reference_from_bytes(
                        cam_id, raw_img, zone_id=zone_override
                    )
                    del raw_img
                    self._send_json(res)
                    return

                m_vtpl_post = re.match(r"^/api/cameras/([^/]+)/vehicle-template$", path)
                if m_vtpl_post:
                    cam_id = unquote(m_vtpl_post.group(1))
                    ctype = self.headers.get("Content-Type", "")
                    if "application/json" in ctype:
                        body = self._read_json()
                        raw_img = self._decode_base64_image(
                            str(body.get("image_base64", "")), "缺少有车停入参考图 image_base64"
                        )
                        del body
                    else:
                        raw_img = self._read_body()
                        if not raw_img:
                            raise ValueError("缺少有车停入参考图二进制内容")
                    res = ctx.calibrator.save_vehicle_reference_from_bytes(cam_id, raw_img)
                    del raw_img
                    self._send_json(res)
                    return

                m_spots = re.match(r"^/api/cameras/([^/]+)/spots$", path)
                if m_spots:
                    cam_id = unquote(m_spots.group(1))
                    body = self._read_json()
                    cam = ctx.db.get_camera(cam_id)
                    if not cam:
                        self._send_json({"error": f"Camera {cam_id} not found"}, 404)
                        return
                    cal_layer = str(body.get("calibration_layer") or "both").strip().lower()
                    target_zone = str(body.get("zone_id") or cam["zone_id"])
                    saved = ctx.db.save_camera_spots(
                        camera_id=cam_id,
                        zone_id=target_zone,
                        spots_payload=body.get("spots", []),
                        image_width=int(body.get("image_width") or cam["image_width"]),
                        image_height=int(body.get("image_height") or cam["image_height"]),
                        calibration_layer=cal_layer,
                    )
                    ctx.vision.clear_frame_cache(cam_id)
                    eval_res = None
                    auto_cropped = False
                    if body.get("image_base64") or body.get("evaluate_current"):
                        raw_eval: Optional[bytes] = None
                        if body.get("image_base64"):
                            raw_eval = self._decode_base64_image(str(body["image_base64"]))
                            # Persist layer-appropriate reference backdrop without touching the other layer!
                            if cal_layer == "vehicle":
                                ctx.calibrator.save_vehicle_reference_from_bytes(
                                    camera_id=cam_id, raw_image_bytes=raw_eval
                                )
                            else:
                                ctx.calibrator.save_ground_reference_from_bytes(
                                    camera_id=cam_id, raw_image_bytes=raw_eval, zone_id=target_zone
                                )
                                cam_latest = ctx.db.get_camera(cam_id) or cam
                                cur_roi = cam_latest.get("crop_roi") or {}
                                has_cal_spots = any(
                                    len(sp.get("polygon") or []) >= 3 for sp in (saved or [])
                                )
                                was_uncalibrated = int(cam.get("calibrated_spot_count") or 0) == 0
                                if (
                                    cal_layer == "both"
                                    and was_uncalibrated
                                    and has_cal_spots
                                    and not bool(cur_roi.get("enabled"))
                                    and not bool(cur_roi.get("user_disabled"))
                                ):
                                    sug = ctx.calibrator.suggest_camera_crop_roi(cam_id)
                                    sug_roi = (
                                        (sug or {}).get("suggested_crop_roi")
                                        or (sug or {}).get("crop_roi")
                                        or {}
                                    )
                                    if (
                                        sug.get("ok")
                                        and float(sug_roi.get("w", 1.0)) * float(sug_roi.get("h", 1.0))
                                        <= 0.84
                                    ):
                                        ctx.calibrator.configure_camera_crop_roi(
                                            camera_id=cam_id,
                                            crop_roi_payload={
                                                **sug_roi,
                                                "enabled": True,
                                                "user_disabled": False,
                                            },
                                            raw_image_bytes=raw_eval,
                                        )
                                        auto_cropped = True
                                        cam_cropped_blobs = ctx.db.get_camera(cam_id, include_blobs=True)
                                        if cam_cropped_blobs and cam_cropped_blobs.get("template_jpeg_b64"):
                                            raw_eval = self._decode_base64_image(
                                                str(cam_cropped_blobs["template_jpeg_b64"])
                                            )
                                        saved = ctx.db.list_spots(camera_id=cam_id)
                        else:
                            cam_blobs = ctx.db.get_camera(cam_id, include_blobs=True)
                            if cam_blobs:
                                pref_b64 = (
                                    cam_blobs.get("vehicle_template_jpeg_b64")
                                    if cal_layer == "vehicle" and cam_blobs.get("vehicle_template_jpeg_b64")
                                    else cam_blobs.get("template_jpeg_b64") or cam_blobs.get("vehicle_template_jpeg_b64")
                                )
                                if pref_b64:
                                    raw_eval = self._decode_base64_image(str(pref_b64))
                        if raw_eval:
                            eval_res = ctx.vision.benchmark_dual_layer_bytes(
                                camera_id=cam_id,
                                raw_image_bytes=raw_eval,
                                sync_db=True,
                                repeats=1,
                            )
                            del raw_eval
                            saved = ctx.db.list_spots(camera_id=cam_id)
                    cam_after = ctx.db.get_camera(cam_id) or cam
                    self._send_json(
                        {
                            "camera_id": cam_id,
                            "calibration_layer": cal_layer,
                            "image_width": cam_after.get("image_width"),
                            "image_height": cam_after.get("image_height"),
                            "crop_roi": cam_after.get("crop_roi"),
                            "auto_cropped": auto_cropped,
                            "spots": saved,
                            "evaluation": eval_res,
                        }
                    )
                    return

                m_sug_roi = re.match(r"^/api/cameras/([^/]+)/suggest-crop-roi$", path)
                if m_sug_roi:
                    cam_id = unquote(m_sug_roi.group(1))
                    body = self._read_json() if self.headers.get("Content-Length", "0") != "0" else {}
                    raw_opt: Optional[bytes] = None
                    if body.get("image_base64"):
                        raw_opt = self._decode_base64_image(str(body["image_base64"]))
                    res = ctx.calibrator.suggest_camera_crop_roi(cam_id, raw_image_bytes=raw_opt)
                    del raw_opt
                    self._send_json(res)
                    return

                m_crop_roi = re.match(r"^/api/cameras/([^/]+)/crop-roi$", path)
                if m_crop_roi:
                    cam_id = unquote(m_crop_roi.group(1))
                    body = self._read_json()
                    raw_opt_crop: Optional[bytes] = None
                    if body.get("image_base64"):
                        raw_opt_crop = self._decode_base64_image(str(body["image_base64"]))
                    roi_payload = body.get("crop_roi") if isinstance(body.get("crop_roi"), dict) else body
                    res = ctx.calibrator.configure_camera_crop_roi(
                        camera_id=cam_id,
                        crop_roi_payload=roi_payload,
                        raw_image_bytes=raw_opt_crop,
                    )
                    del raw_opt_crop
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": "车位区域固定裁剪 (ROI)" if res["crop_roi"].get("enabled") else "恢复原图全画幅标定",
                            "camera_id": cam_id,
                            "elapsed_ms": res.get("elapsed_ms", 0.0),
                            "processed_spots": len(res.get("spots") or []),
                        }
                    )
                    self._send_json(res)
                    return

                m_anchors = re.match(r"^/api/cameras/([^/]+)/anchors$", path)
                if m_anchors:
                    cam_id = unquote(m_anchors.group(1))
                    body = self._read_json()
                    anchors_list = (
                        None
                        if body.get("reset")
                        else (body.get("anchor_points") if isinstance(body.get("anchor_points"), list) else [])
                    )
                    updated_cam = ctx.db.save_camera_anchor_points(cam_id, anchors_list)
                    ctx.vision.clear_frame_cache(cam_id)
                    self._send_json(
                        {
                            "ok": True,
                            "camera_id": cam_id,
                            "camera": updated_cam,
                            "anchor_points": (updated_cam or {}).get("anchor_points") or [],
                            "anchor_points_custom": bool((updated_cam or {}).get("anchor_points_custom")),
                        }
                    )
                    return

                m_cal_sched = re.match(r"^/api/cameras/([^/]+)/calib-schedule$", path)
                if m_cal_sched:
                    cam_id = unquote(m_cal_sched.group(1))
                    body = self._read_json()
                    sched_payload = body.get("calib_schedule") if isinstance(body.get("calib_schedule"), dict) else body
                    updated_cam = ctx.db.save_camera_calib_schedule(cam_id, sched_payload)
                    self._send_json(
                        {
                            "ok": True,
                            "camera_id": cam_id,
                            "calib_schedule": updated_cam.get("calib_schedule"),
                            "camera": updated_cam,
                        }
                    )
                    return

                m_verify_cal = re.match(r"^/api/cameras/([^/]+)/verify-calibration$", path)
                if m_verify_cal:
                    cam_id = unquote(m_verify_cal.group(1))
                    body = self._read_json() if self.headers.get("Content-Length", "0") != "0" else {}
                    raw_ver: Optional[bytes] = None
                    if body.get("image_base64"):
                        raw_ver = self._decode_base64_image(str(body["image_base64"]))
                    res = ctx.vision.verify_camera_calibration(
                        camera_id=cam_id,
                        raw_image_bytes=raw_ver,
                        apply_polygon_shift=bool(
                            body.get("apply_polygon_shift") or body.get("apply_shift_compensation")
                        ),
                        source="manual",
                    )
                    del raw_ver
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": f"标定防偏移核对 ({(res.get('calib_health') or {}).get('status', 'OK')})",
                            "camera_id": cam_id,
                            "elapsed_ms": res.get("elapsed_ms", 0.0),
                            "processed_spots": (ctx.db.get_camera(cam_id) or {}).get("spots_count", 0),
                        }
                    )
                    self._send_json(res)
                    return

                m_bench = re.match(r"^/api/cameras/([^/]+)/benchmark-dual-layer$", path)
                if m_bench:
                    cam_id = unquote(m_bench.group(1))
                    body = self._read_json()
                    raw_bench: Optional[bytes] = None
                    if body.get("image_base64"):
                        raw_bench = self._decode_base64_image(str(body["image_base64"]))
                    else:
                        cam_blobs = ctx.db.get_camera(cam_id, include_blobs=True)
                        if cam_blobs and cam_blobs.get("template_jpeg_b64"):
                            raw_bench = self._decode_base64_image(str(cam_blobs["template_jpeg_b64"]))
                    if not raw_bench:
                        raise ValueError("请先加载当前机位参考画面或上传测试图片后再执行双层识别与算力对比")
                    sync_db_flag = bool(body.get("sync_db", True))
                    res = ctx.vision.benchmark_dual_layer_bytes(
                        camera_id=cam_id,
                        raw_image_bytes=raw_bench,
                        spots_override=body.get("spots"),
                        cal_width_override=int(body["image_width"]) if body.get("image_width") else None,
                        cal_height_override=int(body["image_height"]) if body.get("image_height") else None,
                        sync_db=sync_db_flag,
                        repeats=int(body.get("repeats", 5)),
                    )
                    del raw_bench
                    if sync_db_flag:
                        res["updated_spots"] = ctx.db.list_spots(camera_id=cam_id)
                        ctx.push_event(
                            {
                                "ts": time.time(),
                                "source": "地面+3D立体双层识别实测",
                                "camera_id": cam_id,
                                "elapsed_ms": res["compute"]["dual_layer_total_ms"],
                                "processed_spots": res["spot_count"],
                            }
                        )
                    self._send_json(res)
                    return

                m_refine = re.match(r"^/api/cameras/([^/]+)/refine-lines$", path)
                if m_refine:
                    cam_id = unquote(m_refine.group(1))
                    body = self._read_json()
                    raw_opt: Optional[bytes] = None
                    if body.get("image_base64"):
                        raw_opt = self._decode_base64_image(str(body["image_base64"]))
                    res = ctx.calibrator.refine_spots_against_image(
                        camera_id=cam_id,
                        raw_image_bytes=raw_opt,
                        spots_override=body.get("spots"),
                        auto_save=bool(body.get("auto_save", True)),
                    )
                    del raw_opt
                    self._send_json(res)
                    return

                m_autocal = re.match(r"^/api/cameras/([^/]+)/auto-calibrate$", path)
                if m_autocal:
                    cam_id = unquote(m_autocal.group(1))
                    ctype = self.headers.get("Content-Type", "")
                    zone_override: Optional[str] = qs.get("zone_id", [None])[0]  # type: ignore
                    auto_save = qs.get("auto_save", ["true"])[0].lower() != "false"

                    if "application/json" in ctype:
                        body = self._read_json()
                        raw_img = self._decode_base64_image(
                            str(body.get("image_base64", "")), "缺少参考图数据 image_base64"
                        )
                        if body.get("zone_id"):
                            zone_override = str(body["zone_id"])
                        if "auto_save" in body:
                            auto_save = bool(body["auto_save"])
                    else:
                        raw_img = self._read_body()
                        if not raw_img:
                            raise ValueError("缺少参考图二进制内容")

                    res = ctx.calibrator.auto_calibrate_from_bytes(
                        camera_id=cam_id,
                        raw_image_bytes=raw_img,
                        zone_id=zone_override,
                        auto_save=auto_save,
                    )
                    del raw_img
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": "AI 参考图自动标定模板",
                            "camera_id": cam_id,
                            "elapsed_ms": res["elapsed_ms"],
                            "processed_spots": len(res["spots"]),
                        }
                    )
                    self._send_json(res)
                    return

                m_snap = re.match(r"^/api/cameras/([^/]+)/snapshot$", path)
                if m_snap:
                    cam_id = unquote(m_snap.group(1))
                    cam_rec = ctx.db.get_camera_ingest_record(cam_id)
                    if cam_rec:
                        icfg = cam_rec.get("ingest_config") or {}
                        expected_tok = str(icfg.get("push_token") or "").strip()
                        if bool(icfg.get("require_push_token")) and expected_tok:
                            got_tok = (
                                self.headers.get("X-Camera-Token")
                                or (qs.get("token") or [""])[0]
                                or ""
                            ).strip()
                            if got_tok != expected_tok:
                                self._send_json(
                                    {
                                        "error": "摄像头推送鉴权失败：请携带正确的 X-Camera-Token 请求头或 ?token= 参数",
                                    },
                                    401,
                                )
                                return
                    raw_img, opts = self._extract_snapshot_request(qs)
                    sim_offset = float(opts.get("time_offset_seconds", 0.0))
                    sim_ts = time.time() + sim_offset
                    res = ctx.vision.process_snapshot_bytes(
                        camera_id=cam_id,
                        raw_image_bytes=raw_img,
                        force_reanalyze=bool(opts.get("force_reanalyze", False)),
                        override_instant_confirm=opts.get("instant_confirm"),
                        simulated_timestamp=sim_ts,
                        preview_spots=opts.get("preview_spots"),
                    )
                    del raw_img
                    if cam_rec:
                        now_ts = time.time()
                        prev_st = cam_rec.get("ingest_status") or {}
                        ctx.db.update_camera_ingest_status(
                            cam_id,
                            {
                                "state": "push_active",
                                "last_pull_at": now_ts,
                                "last_ok_ts": now_ts,
                                "last_ok_at": format_local_ts(now_ts),
                                "frames_pulled": int(prev_st.get("frames_pulled") or 0) + 1,
                                "last_latency_ms": float(res.get("elapsed_ms") or 0.0),
                                "transport": str(opts.get("source_label", "HTTP抓拍推送")),
                                "consecutive_failures": 0,
                                "last_error": "",
                            },
                        )
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

                if path == "/api/sitemap/auto-generate":
                    ctype = self.headers.get("Content-Type", "")
                    comm_name: Optional[str] = None
                    if "application/json" in ctype:
                        body = self._read_json()
                        raw_img = self._decode_base64_image(
                            str(body.get("image_base64", "")), "缺少小区平面图数据 image_base64"
                        )
                        if body.get("community_name"):
                            comm_name = str(body["community_name"]).strip()
                        del body
                    else:
                        raw_img = self._read_body()
                        if not raw_img:
                            raise ValueError("缺少小区平面图二进制内容")

                    res = ctx.sitemap_gen.generate_from_bytes(
                        raw_image_bytes=raw_img,
                        community_name=comm_name,
                        auto_save=True,
                    )
                    del raw_img
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": "小区平面图自动生成态势图",
                            "camera_id": "SITE_MAP",
                            "elapsed_ms": res["elapsed_ms"],
                            "processed_spots": len(res.get("zones", [])),
                        }
                    )
                    self._send_json(res)
                    return

                if path == "/api/sitemap/generate-route-spots":
                    body = self._read_json()
                    smap = ctx.db.get_site_map(include_backdrop=False)
                    aspect = float(body.get("aspect_ratio") or smap.get("aspect_ratio") or 1.45)
                    route_pts = body.get("route_points") or []
                    route_id_req = str(body.get("route_id") or body.get("road_id") or "").strip()
                    if not route_pts and route_id_req:
                        topo_cur = smap.get("topology") or {}
                        roads_list = topo_cur.get("roads") or []
                        routes_list = topo_cur.get("routes") or []
                        matched_rd = next(
                            (
                                r
                                for r in roads_list
                                if str(r.get("road_id") or "") == route_id_req
                                or str(r.get("route_id") or "") == route_id_req
                            ),
                            None,
                        )
                        if matched_rd:
                            route_pts = matched_rd.get("points") or []
                        else:
                            matched_rt = next(
                                (r for r in routes_list if str(r.get("route_id") or "") == route_id_req),
                                None,
                            )
                            if matched_rt:
                                route_pts = matched_rt.get("points") or []
                    group_code = str(body.get("group_code", "")).strip()
                    parsed = parse_camera_group_codes(group_code) if group_code else None
                    first_grp = parsed["groups"][0] if (parsed and parsed["groups"]) else None
                    count = int(body.get("count") or (first_grp["count"] if first_grp else 5))
                    orient = str(
                        body.get("orientation")
                        or (first_grp["orient_code"] if first_grp else "V")
                    )
                    v_model = str(
                        body.get("vehicle_model")
                        or (first_grp["vehicle_model"] if first_grp else ("parallel" if orient == "H" else "sedan"))
                    )
                    norm_grp = first_grp["code"] if first_grp else group_code.upper()
                    lbl_prefix = str(body.get("label_prefix") if "label_prefix" in body else norm_grp).rstrip("-")
                    offset_val = float(
                        body["road_offset"]
                        if "road_offset" in body
                        else body.get("offset", 3.1)
                    )
                    spots_gen = generate_spots_along_route(
                        route_points=route_pts,
                        count=count,
                        side=str(body.get("side", "right")),
                        orientation=orient,
                        vehicle_model=v_model,
                        start_ratio=float(body.get("start_ratio", 0.15)),
                        spacing_gap=float(body.get("spacing_gap", 0.35)),
                        road_offset=offset_val,
                        aspect_ratio=aspect,
                        group_code=norm_grp,
                        camera_id=str(body.get("camera_id", "UNASSIGNED")),
                        zone_id=str(body.get("zone_id", "")),
                        label_prefix=lbl_prefix,
                        anchor_point=body.get("anchor_point"),
                        end_point=body.get("end_point"),
                        placement_mode=str(body.get("placement_mode", "route_snap")),
                        reverse_direction=bool(body.get("reverse_direction", False)),
                        angle_offset_deg=float(body.get("angle_offset_deg", 0.0)),
                    )
                    should_save = bool(
                        body.get("auto_save", bool(route_id_req and "route_points" not in body))
                    )
                    if should_save and spots_gen:
                        ctx.db.apply_sitemap_layout(spots_payload=spots_gen)
                    self._send_json(
                        {
                            "ok": True,
                            "spots": spots_gen,
                            "created_count": len(spots_gen),
                            "saved": should_save,
                            "group_code": norm_grp,
                        }
                    )
                    return

                if path == "/api/units":
                    body = self._read_json()
                    res = ctx.db.upsert_unit(
                        unit_id=str(body.get("unit_id", "")).strip(),
                        name=str(body.get("name", "")).strip(),
                        default_gate=body.get("default_gate"),
                        map_x=float(body["map_x"]) if body.get("map_x") is not None else None,
                        map_y=float(body["map_y"]) if body.get("map_y") is not None else None,
                        map_w=float(body["map_w"]) if body.get("map_w") is not None else None,
                        map_h=float(body["map_h"]) if body.get("map_h") is not None else None,
                        door_x=float(body["door_x"]) if body.get("door_x") is not None else None,
                        door_y=float(body["door_y"]) if body.get("door_y") is not None else None,
                        door_custom=int(body["door_custom"]) if body.get("door_custom") is not None else None,
                        old_unit_id=body.get("old_unit_id"),
                    )
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/zones":
                    body = self._read_json()
                    res = ctx.db.upsert_zone(
                        zone_id=str(body.get("zone_id") or "").strip(),
                        name=str(body.get("name") or "").strip(),
                        description=body.get("description"),
                        gate_access=body.get("gate_access"),
                        dead_end_penalty=float(body["dead_end_penalty"])
                        if body.get("dead_end_penalty") is not None and str(body.get("dead_end_penalty")).strip() != ""
                        else None,
                        map_x=float(body["map_x"]) if body.get("map_x") is not None else None,
                        map_y=float(body["map_y"]) if body.get("map_y") is not None else None,
                        map_w=float(body["map_w"]) if body.get("map_w") is not None else None,
                        map_h=float(body["map_h"]) if body.get("map_h") is not None else None,
                        old_zone_id=body.get("old_zone_id"),
                    )
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/sitemap/bind-group":
                    body = self._read_json()
                    cam_id = str(body.get("camera_id") or "UNASSIGNED").strip()
                    gc = str(body.get("group_code") or "").strip()
                    res = ctx.db.bind_camera_to_spot_group(
                        camera_id=cam_id,
                        group_code=gc,
                        group_name=body.get("group_name"),
                        near_unit_id=body.get("near_unit_id"),
                        zone_id=body.get("zone_id"),
                    )
                    if cam_id and cam_id != "UNASSIGNED":
                        ctx.vision.clear_frame_cache(cam_id)
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": f"车位组绑定机位 ({gc} ⇄ {cam_id})",
                            "camera_id": cam_id,
                            "elapsed_ms": 0.0,
                            "processed_spots": int(res.get("bound_spots") or res.get("detached") or 0),
                        }
                    )
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/sitemap/place-camera":
                    body = self._read_json()
                    cam_id = str(body.get("camera_id") or "").strip()
                    res = ctx.db.place_camera_on_sitemap(
                        camera_id=cam_id,
                        map_x=body.get("map_x"),
                        map_y=body.get("map_y"),
                        end_map_x=body.get("end_map_x"),
                        end_map_y=body.get("end_map_y"),
                        map_angle=body.get("map_angle"),
                        orientation=str(body.get("orientation") or "auto"),
                        group_code=body.get("group_code"),
                        group_name=body.get("group_name"),
                        near_unit_id=body.get("near_unit_id"),
                        zone_id=body.get("zone_id"),
                    )
                    if cam_id and cam_id != "UNASSIGNED":
                        ctx.vision.clear_frame_cache(cam_id)
                    ctx.push_event(
                        {
                            "ts": time.time(),
                            "source": f"平面图摆位绑定 ({res.get('group_code')} ⇄ {cam_id})",
                            "camera_id": cam_id,
                            "elapsed_ms": 0.0,
                            "processed_spots": int(res.get("placed_spots") or 0),
                        }
                    )
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/sitemap/rename-group":
                    body = self._read_json()
                    res = ctx.db.rename_spot_group(
                        group_code=str(body.get("group_code") or "").strip(),
                        group_name=body.get("group_name"),
                        near_unit_id=body.get("near_unit_id"),
                        new_group_code=body.get("new_group_code"),
                        zone_id=body.get("zone_id"),
                        for_camera_id=body.get("for_camera_id"),
                    )
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/sitemap/delete-group":
                    body = self._read_json()
                    res = ctx.db.delete_spot_group(
                        group_code=str(body.get("group_code") or "").strip(),
                        camera_action=str(body.get("camera_action") or "keep").strip(),
                    )
                    for cid in res.get("deleted_camera_ids") or []:
                        ctx.vision.clear_frame_cache(cid)
                    self._send_json({"ok": True, **res})
                    return

                if path == "/api/sitemap/layout":
                    body = self._read_json()
                    topo_patch: Dict[str, Any] = {}
                    nested_topo = body.get("topology") if isinstance(body.get("topology"), dict) else {}
                    for k in ("gates", "roads", "walkways", "greens", "routes"):
                        if isinstance(body.get(k), list):
                            topo_patch[k] = body[k]
                        elif isinstance(nested_topo.get(k), list):
                            topo_patch[k] = nested_topo[k]
                    raw_span = (
                        body.get("estimated_span_meters")
                        or body.get("span_meters")
                        or (body.get("meta") or {}).get("estimated_span_meters")
                        if isinstance(body.get("meta"), dict)
                        else (body.get("estimated_span_meters") or body.get("span_meters"))
                    )
                    updated = ctx.db.apply_sitemap_layout(
                        zones_payload=body.get("zones"),
                        units_payload=body.get("units"),
                        spots_payload=body.get("spots"),
                        deleted_spot_ids=body.get("deleted_spot_ids"),
                        deleted_unit_ids=body.get("deleted_unit_ids"),
                        topology_patch=topo_patch or None,
                        span_meters=float(raw_span) if raw_span else None,
                        replace_units=False,
                        reset_spot_positions=bool(body.get("reset_spot_positions", False)),
                    )
                    self._send_json({"ok": True, **updated})
                    return

                if path == "/api/config":
                    body = self._read_json()
                    updated = config_manager.update(body)
                    if "vlm" in body:
                        ctx.vision.clear_vlm_cache()
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
            except PayloadTooLargeError as ple:
                self._send_json(
                    {"error": str(ple), "max_bytes": MAX_HTTP_BODY_BYTES},
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                m_zone_put = re.match(r"^/api/zones/([^/]+)$", path)
                if m_zone_put:
                    zid = unquote(m_zone_put.group(1))
                    body = self._read_json()
                    res = ctx.db.upsert_zone(
                        zone_id=str(body.get("zone_id") or zid).strip(),
                        name=str(body.get("name") or "").strip(),
                        description=body.get("description"),
                        gate_access=body.get("gate_access"),
                        dead_end_penalty=float(body["dead_end_penalty"])
                        if body.get("dead_end_penalty") is not None and str(body.get("dead_end_penalty")).strip() != ""
                        else None,
                        map_x=float(body["map_x"]) if body.get("map_x") is not None else None,
                        map_y=float(body["map_y"]) if body.get("map_y") is not None else None,
                        map_w=float(body["map_w"]) if body.get("map_w") is not None else None,
                        map_h=float(body["map_h"]) if body.get("map_h") is not None else None,
                        old_zone_id=zid,
                    )
                    self._send_json({"ok": True, **res})
                    return
                m_cam = re.match(r"^/api/cameras/([^/]+)$", path)
                if m_cam:
                    cam_id = unquote(m_cam.group(1))
                    body = self._read_json()
                    updated_cam = ctx.db.update_camera(cam_id, body)
                    ctx.vision.clear_frame_cache(cam_id)
                    if updated_cam["camera_id"] != cam_id:
                        ctx.vision.clear_frame_cache(updated_cam["camera_id"])
                    self._send_json(
                        {
                            "ok": True,
                            "camera": updated_cam,
                            "spots": ctx.db.list_spots(camera_id=updated_cam["camera_id"]),
                        }
                    )
                    return
                self._send_json({"error": f"Unknown PUT endpoint {path}"}, 404)
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/locks":
                    ctx.recommender.clear_locks()
                    self._send_json({"ok": True})
                    return
                m_unit_del = re.match(r"^/api/units/([^/]+)$", parsed.path)
                if m_unit_del:
                    uid_del = unquote(m_unit_del.group(1))
                    res = ctx.db.delete_unit(uid_del)
                    self._send_json({"ok": True, **res})
                    return
                m_zone_del = re.match(r"^/api/zones/([^/]+)$", parsed.path)
                if m_zone_del:
                    zid_del = unquote(m_zone_del.group(1))
                    reassign_to = qs.get("reassign_to", [""])[0]
                    res = ctx.db.delete_zone(zid_del, reassign_zone_id=reassign_to or None)
                    self._send_json({"ok": True, **res})
                    return
                m_grp_del = re.match(r"^/api/sitemap/groups/([^/]+)$", parsed.path)
                if m_grp_del:
                    gc_del = unquote(m_grp_del.group(1))
                    cam_act = qs.get("camera_action", ["keep"])[0]
                    res = ctx.db.delete_spot_group(gc_del, camera_action=cam_act)
                    for cid in res.get("deleted_camera_ids") or []:
                        ctx.vision.clear_frame_cache(cid)
                    self._send_json({"ok": True, **res})
                    return
                m_cam_del = re.match(r"^/api/cameras/([^/]+)$", parsed.path)
                if m_cam_del:
                    cam_id = unquote(m_cam_del.group(1))
                    spot_action = qs.get("spot_action", ["detach"])[0]
                    res = ctx.db.delete_camera(cam_id, spot_action=spot_action)
                    ctx.vision.clear_frame_cache(cam_id)
                    self._send_json({"ok": True, **res})
                    return
                self._send_json({"error": "Not found"}, 404)
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

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
                del raw
                img_bytes = self._decode_base64_image(str(payload.get("image_base64", "")))
                if "force_reanalyze" in payload:
                    opts["force_reanalyze"] = bool(payload["force_reanalyze"])
                if "instant_confirm" in payload and payload["instant_confirm"] is not None:
                    opts["instant_confirm"] = bool(payload["instant_confirm"])
                if "time_offset_seconds" in payload:
                    opts["time_offset_seconds"] = float(payload["time_offset_seconds"])
                if "source_label" in payload:
                    opts["source_label"] = payload["source_label"]
                if isinstance(payload.get("preview_spots"), list) and payload["preview_spots"]:
                    opts["preview_spots"] = payload["preview_spots"]
                del payload
                return img_bytes, opts

            # Raw binary JPEG/PNG upload (e.g. directly from ESP32-CAM HTTP POST)
            opts["source_label"] = "ESP32/IPC 二进制直传"
            return raw, opts

        def _serve_static(self, req_path: str, head_only: bool = False) -> None:
            clean_path = unquote(req_path)
            rel = "index.html" if clean_path in ("/", "") else clean_path.lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.is_file():
                self._send_json({"error": "Not found"}, 404)
                return
            mime, _ = mimetypes.guess_type(str(file_path))
            content = file_path.read_bytes()
            if rel == "index.html":
                fe_ver = _compute_frontend_version()
                html_txt = content.decode("utf-8")
                html_txt = re.sub(r'/styles\.css(?:\?v=[^"]*)?', f"/styles.css?v={fe_ver}", html_txt)
                html_txt = re.sub(r'/app\.js(?:\?v=[^"]*)?', f"/app.js?v={fe_ver}", html_txt)
                html_txt = re.sub(
                    r'<meta\s+name="parking-frontend-version"\s+content="[^"]*"\s*/?>',
                    f'<meta name="parking-frontend-version" content="{fe_ver}" />',
                    html_txt,
                )
                content = html_txt.encode("utf-8")
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", (mime or "application/octet-stream") + "; charset=utf-8" if (mime or "").startswith("text/") or str(file_path).endswith(".js") else (mime or "application/octet-stream"))
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                if not head_only:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ParkingRequestHandler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, ctx_ref: Optional[ParkingAppContext] = None):
        super().__init__(server_address, RequestHandlerClass)
        self._ctx_ref = ctx_ref

    def shutdown(self) -> None:
        if self._ctx_ref is not None:
            self._ctx_ref.stop_camera_ingestor()
            self._ctx_ref.stop_calib_scheduler()
        super().shutdown()

    def server_close(self) -> None:
        if self._ctx_ref is not None:
            self._ctx_ref.stop_camera_ingestor()
            self._ctx_ref.stop_calib_scheduler()
        super().server_close()


def create_http_server(
    host: str = "127.0.0.1", port: int = 8790, ctx: ParkingAppContext = app_ctx
) -> ThreadingHTTPServer:
    handler_cls = make_http_handler(ctx)
    srv = ReusableThreadingHTTPServer((host, port), handler_cls, ctx_ref=ctx)
    ctx.start_calib_scheduler()
    ctx.start_camera_ingestor()
    return srv
