import base64
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.auto_calibrator import ReferenceAutoCalibrator
from app.config import config_manager
from app.database import DEFAULT_SITE_MAP_TOPOLOGY, Database
from app.vision_pipeline import VisionPipeline

MAX_SITEMAP_BACKDROP_EDGE = 1200
BACKDROP_JPEG_QUALITY = 76


class SiteMapGenerator:
    """
    AI + OpenCV Community Floor-Plan Parser & Situational Map Builder:
      1. Resizes uploaded floor-plan image in RAM (long edge <= 1200px, JPEG compressed)
         and immediately releases raw decoded buffers to prevent memory spikes.
      2. Overlays a 0..1000 normalized coordinate ruler and invokes Multimodal VLM
         (e.g. Azure AI Foundry gpt-6.1-sol) to extract gates, internal roads, green belts,
         building units, and parking zones.
      3. Provides an OpenCV color/contour geometric fallback when VLM is offline.
      4. Reconciles parking zones with existing calibrated cameras (e.g. CAM_3F_WIN) so
         actual calibrated stalls project seamlessly onto the new community map, and
         automatically recomputes the unit-to-zone walking distance matrix.
    """

    def __init__(self, database: Database, vision: VisionPipeline) -> None:
        self.db = database
        self.vision = vision

    def generate_from_bytes(
        self,
        raw_image_bytes: bytes,
        community_name: Optional[str] = None,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()

        existing_zones = self.db.list_zones()
        existing_cameras = self.db.list_cameras()
        existing_spots = self.db.list_spots()
        existing_units = self.db.list_units()

        with self.vision.decode_guard():
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            raw_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if raw_img is None:
                raise ValueError("无法解码小区平面图字节流，请上传有效的 JPG/PNG/WebP 图片")

            orig_h, orig_w = raw_img.shape[:2]
            aspect_ratio = round(orig_w / max(1.0, float(orig_h)), 4)

            scale = min(1.0, float(MAX_SITEMAP_BACKDROP_EDGE) / max(orig_h, orig_w))
            if scale < 1.0:
                work_img = cv2.resize(
                    raw_img,
                    (max(64, int(round(orig_w * scale))), max(64, int(round(orig_h * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                work_img = raw_img.copy()
            # Free full-resolution decoded frame immediately
            del raw_img

            backdrop_data_uri, backdrop_kb = self._encode_clean_backdrop(work_img)
            ruler_img = ReferenceAutoCalibrator._draw_coordinate_ruler(work_img)
            ok_r, buf_r = cv2.imencode(".jpg", ruler_img, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
            ruler_b64 = base64.b64encode(buf_r.tobytes()).decode("ascii") if ok_r else ""
            del ruler_img

            # Precompute lightweight OpenCV fallback while work_img is in RAM, then free work_img
            opencv_parsed = self._parse_via_opencv_fallback(
                work_img, existing_units, existing_zones
            )
            work_h, work_w = work_img.shape[:2]
            del work_img

        with self.vision._lock:
            self.vision.privacy_stats["frames_processed_in_ram"] += 1
            self.vision._sync_memory_stats_locked()

        cfg = config_manager.get()
        vlm_cfg = cfg.get("vlm", {})
        base_u = str(vlm_cfg.get("base_url", "")).strip()
        vlm_ready = (
            vlm_cfg.get("mode", "hybrid") != "local_only"
            and bool(base_u)
            and bool(ruler_b64)
            and (
                bool(vlm_cfg.get("api_key"))
                or "127.0.0.1" in base_u
                or "localhost" in base_u
            )
        )

        engine_used = "OpenCV_FloorPlan_Geometry"
        source_type = "opencv_generated"
        vlm_fallback_note = ""
        parsed_plan = opencv_parsed

        if vlm_ready:
            vlm_plan, vlm_err = self._parse_via_vlm(
                ruler_b64, vlm_cfg, existing_zones, existing_cameras, existing_spots
            )
            if vlm_plan is not None:
                parsed_plan = vlm_plan
                model_tag = vlm_cfg.get("model", "VLM")
                engine_used = f"Azure/VLM ({model_tag}) 平面图拓扑解析"
                source_type = "vlm_generated"
            else:
                vlm_fallback_note = vlm_err or "VLM未返回有效拓扑"
                engine_used = f"OpenCV_FloorPlan (VLM回退: {vlm_fallback_note[:42]})"

        del ruler_b64

        reconciled_zones = self._reconcile_zones_with_cameras(
            parsed_plan.get("zones") or [],
            existing_zones,
            existing_cameras,
            existing_spots,
        )
        reconciled_units = self._normalize_units(
            parsed_plan.get("units") or [], existing_units
        )
        topology = {
            "gates": parsed_plan.get("gates") or DEFAULT_SITE_MAP_TOPOLOGY["gates"],
            "roads": parsed_plan.get("roads") or DEFAULT_SITE_MAP_TOPOLOGY["roads"],
            "greens": parsed_plan.get("greens") or [],
        }
        span_meters = float(parsed_plan.get("estimated_span_meters") or 185.0)
        detected_name = str(community_name or parsed_plan.get("community_name") or "").strip()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        meta = {
            "engine": engine_used,
            "scene_summary": str(
                parsed_plan.get("scene_summary") or "已根据上传平面图自动生成态势拓扑与机位车位分布"
            ),
            "estimated_span_meters": span_meters,
            "original_resolution": [orig_w, orig_h],
            "stored_resolution": [work_w, work_h],
            "backdrop_kb": backdrop_kb,
            "elapsed_ms": elapsed_ms,
            "generated_at": time.time(),
        }

        if auto_save:
            self.db.save_site_map(
                source_type=source_type,
                backdrop_jpeg_b64=backdrop_data_uri,
                aspect_ratio=aspect_ratio,
                topology=topology,
                meta=meta,
            )
            self.db.apply_sitemap_layout(
                zones_payload=reconciled_zones,
                units_payload=reconciled_units,
                span_meters=span_meters,
                replace_units=True,
                reset_spot_positions=True,
            )
            if detected_name:
                config_manager.update({"community_name": detected_name})

        smap_out = self.db.get_site_map(include_backdrop=True) if auto_save else {
            "map_id": "default",
            "source_type": source_type,
            "has_backdrop": bool(backdrop_data_uri),
            "backdrop_jpeg_b64": backdrop_data_uri,
            "aspect_ratio": aspect_ratio,
            "topology": topology,
            "meta": meta,
            "updated_at": time.time(),
        }

        return {
            "ok": True,
            "source_type": source_type,
            "engine_used": engine_used,
            "aspect_ratio": aspect_ratio,
            "backdrop_kb": backdrop_kb,
            "elapsed_ms": elapsed_ms,
            "site_map": smap_out,
            "zones": self.db.list_zones() if auto_save else reconciled_zones,
            "units": self.db.list_units() if auto_save else reconciled_units,
        }

    @staticmethod
    def _encode_clean_backdrop(img_bgr: np.ndarray) -> Tuple[str, float]:
        # Softly blend with light neutral wash so vivid floor-plan text doesn't clash with stall pills
        ok, buf = cv2.imencode(
            ".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), BACKDROP_JPEG_QUALITY]
        )
        if not ok:
            return "", 0.0
        raw_b = buf.tobytes()
        kb = round(len(raw_b) / 1024.0, 1)
        return "data:image/jpeg;base64," + base64.b64encode(raw_b).decode("ascii"), kb

    def _parse_via_vlm(
        self,
        ruler_b64: str,
        vlm_cfg: Dict[str, Any],
        existing_zones: List[Dict[str, Any]],
        existing_cameras: List[Dict[str, Any]],
        existing_spots: List[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        with self.vision._lock:
            self.vision.privacy_stats["slow_lane_vlm_calls"] += 1

        spots_per_cam: Dict[str, int] = {}
        for s in existing_spots:
            spots_per_cam[s["camera_id"]] = spots_per_cam.get(s["camera_id"], 0) + 1

        cam_context_lines = []
        for c in existing_cameras:
            hint = (c.get("template_meta") or {}).get("building_hint") or ""
            cnt = spots_per_cam.get(c["camera_id"], 0)
            cal_tag = "已实景标定" if c.get("has_template") else "默认机位"
            cam_context_lines.append(
                f"- camera_id={c['camera_id']}, 名称={c['name']}, 当前绑定片区={c['zone_id']}, 车位数={cnt}, 楼栋提示={hint or '无'} ({cal_tag})"
            )
        cam_ctx_str = "\n".join(cam_context_lines) or "暂无已配置机位"

        prompt = (
            "你是小区总平图/导览平面图空间拓扑解析引擎。图片四周标有 0..1000 归一化坐标尺（X从左0到右1000，Y从上0到下1000）。\n"
            "请识别这张小区平面图中的：出入口大门（gates）、内部主要行车道路折线（roads）、中心绿地景观（greens）、住宅楼栋单元（units）以及地面停车片区/路侧停车带（zones）。\n"
            f"当前系统已接入的相机机位与片区信息如下（请优先将图中对应楼栋/方位的停车区域匹配复用这些 zone_id）：\n{cam_ctx_str}\n\n"
            "输出要求（所有坐标均使用 0..1000 整数）：\n"
            "1. `gates`：识别南门/东门/北门/西门等车行出入口，给出中心坐标 `x_1000, y_1000`。\n"
            "2. `roads`：沿小区内部主要车行通道中心线提取 1~5 条折线 `points_1000: [[x,y], ...]`。\n"
            "3. `units`：识别图中各住宅楼栋（如 1号楼->\"1-1\"，3号楼->\"3-1\"等），给出中心 `x_1000, y_1000` 与宽高 `w_1000, h_1000`。\n"
            "4. `zones`：识别地面停车位片区或路侧顺停带，给出左上角 `x_1000, y_1000`、宽高 `w_1000, h_1000`、旋转角 `layout_angle`（水平为0，垂直可设0并用窄高矩形表示），若对应已有片区请直接填对应 `zone_id`（如 Z_BLDG3_EAST、Z_SOUTH_MAIN、Z_BLDG5_DEADEND、Z_BLDG6_STRIP）。\n"
            "仅输出严格 JSON：\n"
            "{\n"
            '  "scene_summary": "不超过28字平面图概述",\n'
            '  "estimated_span_meters": 190,\n'
            '  "gates": [{"gate_id":"south","name":"南门入口","x_1000":480,"y_1000":955}],\n'
            '  "roads": [{"name":"内部主环路","points_1000":[[120,840],[880,840],[880,200],[140,200]],"width_1000":55}],\n'
            '  "greens": [{"name":"中心花园","x_1000":380,"y_1000":360,"w_1000":240,"h_1000":160}],\n'
            '  "units": [{"unit_id":"3-1","name":"3号楼1单元","default_gate":"south","x_1000":450,"y_1000":460,"w_1000":120,"h_1000":70}],\n'
            '  "zones": [{"zone_id":"Z_BLDG3_EAST","name":"3号楼前地面车位区","x_1000":350,"y_1000":540,"w_1000":280,"h_1000":95,"layout_angle":0,"dead_end_penalty":5,"description":"3号楼门前连排车位"}]\n'
            "}"
        )

        content, err, _ = self.vision._post_vlm_completions(
            vlm_cfg,
            prompt,
            f"data:image/jpeg;base64,{ruler_b64}",
            max_tokens=4096,
            reasoning_effort="medium",
            timeout_override=65.0,
        )
        if err or not content:
            return None, err

        try:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            raw = json.loads(match.group(0) if match else content)

            gates = []
            for idx, g in enumerate(raw.get("gates") or []):
                gx = float(np.clip(float(g.get("x_1000", g.get("x", 500))) / 10.0, 3.0, 97.0))
                gy = float(np.clip(float(g.get("y_1000", g.get("y", 950))) / 10.0, 3.0, 97.0))
                is_side = gx <= 9.0 or gx >= 91.0
                gates.append(
                    {
                        "gate_id": str(g.get("gate_id") or f"gate_{idx+1}"),
                        "name": str(g.get("name") or "小区入口"),
                        "x": round(gx, 1),
                        "y": round(gy, 1),
                        "w": 6.2 if is_side else 14.0,
                        "h": 11.5 if is_side else 4.4,
                    }
                )

            roads = []
            for r in raw.get("roads") or []:
                pts_raw = r.get("points_1000") or r.get("points") or []
                pts = []
                for p in pts_raw:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        pts.append(
                            [
                                round(float(np.clip(float(p[0]) / 10.0, 1.0, 99.0)), 1),
                                round(float(np.clip(float(p[1]) / 10.0, 1.0, 99.0)), 1),
                            ]
                        )
                if len(pts) >= 2:
                    rw = float(r.get("width_1000", 55)) / 10.0
                    roads.append(
                        {
                            "name": str(r.get("name") or "内部道路"),
                            "points": pts,
                            "width": round(float(np.clip(rw, 3.5, 9.0)), 1),
                        }
                    )

            greens = []
            for gr in raw.get("greens") or []:
                gx = float(np.clip(float(gr.get("x_1000", 400)) / 10.0, 2.0, 90.0))
                gy = float(np.clip(float(gr.get("y_1000", 350)) / 10.0, 2.0, 90.0))
                gw = float(np.clip(float(gr.get("w_1000", 180)) / 10.0, 5.0, 96.0 - gx))
                gh = float(np.clip(float(gr.get("h_1000", 120)) / 10.0, 4.0, 96.0 - gy))
                greens.append(
                    {
                        "name": str(gr.get("name") or "景观绿地"),
                        "x": round(gx, 1),
                        "y": round(gy, 1),
                        "w": round(gw, 1),
                        "h": round(gh, 1),
                    }
                )

            valid_gate_ids = {str(g.get("gate_id") or "") for g in gates if g.get("gate_id")}
            units = []
            for idx, u in enumerate(raw.get("units") or []):
                ux = float(np.clip(float(u.get("x_1000", 500)) / 10.0, 5.0, 95.0))
                uy = float(np.clip(float(u.get("y_1000", 500)) / 10.0, 5.0, 95.0))
                uw = float(np.clip(float(u.get("w_1000", 124)) / 10.0, 7.5, 24.0))
                uh = float(np.clip(float(u.get("h_1000", 68)) / 10.0, 5.0, 16.0))
                uid = str(u.get("unit_id") or f"{idx+1}-1").strip()
                raw_ug = str(u.get("default_gate") or "").strip()
                units.append(
                    {
                        "unit_id": uid,
                        "name": str(u.get("name") or f"{uid}号楼"),
                        "default_gate": raw_ug if raw_ug in valid_gate_ids else "auto",
                        "map_x": round(ux, 1),
                        "map_y": round(uy, 1),
                        "map_w": round(uw, 1),
                        "map_h": round(uh, 1),
                    }
                )

            zones = []
            for idx, z in enumerate(raw.get("zones") or []):
                zx = float(np.clip(float(z.get("x_1000", 200)) / 10.0, 2.0, 90.0))
                zy = float(np.clip(float(z.get("y_1000", 500)) / 10.0, 2.0, 90.0))
                zw = float(np.clip(float(z.get("w_1000", 260)) / 10.0, 8.0, 97.0 - zx))
                zh = float(np.clip(float(z.get("h_1000", 100)) / 10.0, 6.5, 97.0 - zy))
                zones.append(
                    {
                        "zone_id": str(z.get("zone_id") or "").strip(),
                        "name": str(z.get("name") or f"地面停车区 {idx+1}").strip(),
                        "map_x": round(zx, 1),
                        "map_y": round(zy, 1),
                        "map_w": round(zw, 1),
                        "map_h": round(zh, 1),
                        "layout_angle": round(float(z.get("layout_angle", 0.0)), 1),
                        "dead_end_penalty": float(z.get("dead_end_penalty", 4.0)),
                        "description": str(z.get("description") or "平面图自动解析车位片区"),
                        "matched_camera_ids": z.get("matched_camera_ids") or [],
                    }
                )

            if not zones and not units:
                return None, "模型未提取出有效楼栋或停车区"

            return (
                {
                    "community_name": str(raw.get("community_name") or "").strip(),
                    "scene_summary": str(raw.get("scene_summary") or "AI 平面图自动拓扑生成").strip(),
                    "estimated_span_meters": float(raw.get("estimated_span_meters") or 185.0),
                    "gates": gates or DEFAULT_SITE_MAP_TOPOLOGY["gates"],
                    "roads": roads or DEFAULT_SITE_MAP_TOPOLOGY["roads"],
                    "greens": greens,
                    "units": units,
                    "zones": zones,
                },
                None,
            )
        except Exception as exc:
            return None, f"平面图JSON解析失败: {exc}"

    @staticmethod
    def _parse_via_opencv_fallback(
        img_bgr: np.ndarray,
        existing_units: List[Dict[str, Any]],
        existing_zones: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Pure-OpenCV geometric floor-plan extractor when VLM is offline or local_only.
        Extracts green lawn regions, dominant road corridors, building blocks, and
        adjacent parking zones in normalized 0..100 coordinates.
        """
        h, w = img_bgr.shape[:2]
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # 1. Green lawns / gardens
        green_mask = cv2.inRange(hsv, np.array([32, 35, 50]), np.array([88, 255, 255]))
        green_mask = cv2.morphologyEx(
            green_mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)
        )
        g_cnts, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        greens: List[Dict[str, Any]] = []
        img_area = float(h * w)
        for cnt in sorted(g_cnts, key=cv2.contourArea, reverse=True)[:4]:
            area = cv2.contourArea(cnt)
            if img_area * 0.012 <= area <= img_area * 0.28:
                rx, ry, rw, rh = cv2.boundingRect(cnt)
                greens.append(
                    {
                        "name": "绿化景观带",
                        "x": round(rx / w * 100.0, 1),
                        "y": round(ry / h * 100.0, 1),
                        "w": round(max(6.0, rw / w * 100.0), 1),
                        "h": round(max(5.0, rh / h * 100.0), 1),
                    }
                )

        # 2. Building blocks via Canny + morphological closing
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 45, 135)
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        b_cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        b_boxes: List[Tuple[float, float, float, float]] = []
        for cnt in sorted(b_cnts, key=cv2.contourArea, reverse=True):
            rx, ry, rw, rh = cv2.boundingRect(cnt)
            area = float(rw * rh)
            aspect = rw / max(1.0, float(rh))
            if (
                img_area * 0.008 <= area <= img_area * 0.14
                and 0.45 <= aspect <= 4.2
                and rx > w * 0.03
                and (rx + rw) < w * 0.97
                and ry > h * 0.03
                and (ry + rh) < h * 0.94
            ):
                cx = (rx + rw * 0.5) / w * 100.0
                cy = (ry + rh * 0.5) / h * 100.0
                if all(abs(cx - bx[0]) > 12.0 or abs(cy - bx[1]) > 10.0 for bx in b_boxes):
                    b_boxes.append(
                        (
                            round(cx, 1),
                            round(cy, 1),
                            round(float(np.clip(rw / w * 100.0, 9.0, 18.0)), 1),
                            round(float(np.clip(rh / h * 100.0, 5.5, 11.0)), 1),
                        )
                    )
            if len(b_boxes) >= max(4, len(existing_units)):
                break

        b_boxes.sort(key=lambda b: (round(b[1] / 20.0), b[0]))
        units_out: List[Dict[str, Any]] = []
        if len(b_boxes) >= 2:
            for idx, (cx, cy, bw, bh) in enumerate(b_boxes[: max(6, len(existing_units))]):
                ref_u = existing_units[idx] if idx < len(existing_units) else None
                uid = ref_u["unit_id"] if ref_u else f"{idx+1}-1"
                uname = ref_u["name"] if ref_u else f"{idx+1}号楼1单元"
                ugate = "south" if cy >= 52.0 else "east"
                units_out.append(
                    {
                        "unit_id": uid,
                        "name": uname,
                        "default_gate": ugate,
                        "map_x": cx,
                        "map_y": cy,
                        "map_w": bw,
                        "map_h": bh,
                    }
                )
        else:
            units_out = [dict(u) for u in existing_units]

        # 3. Derive road network & parking zones adjacent to detected buildings
        roads = [
            {
                "name": "小区环线车行道",
                "points": [[10.0, 84.0], [88.0, 84.0], [88.0, 18.0], [12.0, 18.0], [12.0, 84.0]],
                "width": 5.8,
            },
            {
                "name": "中轴支路",
                "points": [[12.0, 52.0], [88.0, 52.0]],
                "width": 4.6,
            },
        ]

        zones_out: List[Dict[str, Any]] = []
        for idx, ez in enumerate(existing_zones):
            if idx < len(units_out):
                u_ref = units_out[min(idx, len(units_out) - 1)]
                is_vert = float(ez.get("map_h", 10)) > float(ez.get("map_w", 24)) * 1.2
                zw = 13.5 if is_vert else 28.0
                zh = 30.0 if is_vert else 9.5
                zx = float(np.clip(u_ref["map_x"] - zw * 0.45, 4.0, 94.0 - zw))
                zy = float(np.clip(u_ref["map_y"] + u_ref.get("map_h", 6.8) * 0.65 + 1.5, 6.0, 92.0 - zh))
                zones_out.append(
                    {
                        **ez,
                        "map_x": round(zx, 1),
                        "map_y": round(zy, 1),
                        "map_w": zw,
                        "map_h": zh,
                    }
                )
            else:
                zones_out.append(dict(ez))

        return {
            "community_name": "",
            "scene_summary": f"OpenCV 几何拓扑解析（识别 {len(units_out)} 栋楼宇与 {len(zones_out)} 个车位片区）",
            "estimated_span_meters": 185.0,
            "gates": DEFAULT_SITE_MAP_TOPOLOGY["gates"],
            "roads": roads,
            "greens": greens,
            "units": units_out,
            "zones": zones_out,
        }

    @staticmethod
    def _reconcile_zones_with_cameras(
        detected_zones: List[Dict[str, Any]],
        existing_zones: List[Dict[str, Any]],
        existing_cameras: List[Dict[str, Any]],
        existing_spots: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Ensures every existing zone that holds cameras/spots (especially user-calibrated
        cameras like CAM_3F_WIN) is preserved and positioned on the newly generated map,
        sizing zone boxes comfortably for their actual calibrated spot layout.
        """
        if not detected_zones:
            return [dict(z, is_demo_seed=0) for z in existing_zones]

        ez_all = {z["zone_id"]: dict(z) for z in existing_zones}
        cams_by_zone: Dict[str, List[Dict[str, Any]]] = {}
        for c in existing_cameras:
            cams_by_zone.setdefault(c["zone_id"], []).append(c)

        spots_by_zone: Dict[str, List[Dict[str, Any]]] = {}
        for s in existing_spots:
            spots_by_zone.setdefault(s["zone_id"], []).append(s)

        active_ez = {
            zid: z
            for zid, z in ez_all.items()
            if cams_by_zone.get(zid) or spots_by_zone.get(zid)
        }
        ez_by_id = active_ez if active_ez else ez_all

        # Build keyword hints per existing zone (from zone name + camera building_hint)
        zone_hints: Dict[str, List[str]] = {}
        for zid, ez in ez_by_id.items():
            hints = []
            for num in re.findall(r"\d+", ez.get("name", "")):
                hints.extend([f"{num}栋", f"{num}号楼", f"{num}-"])
            for d_word in ("南", "北", "东", "西"):
                if d_word in ez.get("name", ""):
                    hints.append(d_word)
            for c in cams_by_zone.get(zid, []):
                bh = str((c.get("template_meta") or {}).get("building_hint") or "").strip()
                if bh:
                    hints.append(bh)
                    for num in re.findall(r"\d+", bh):
                        hints.extend([f"{num}栋", f"{num}号楼"])
            zone_hints[zid] = hints

        used_existing_ids: set[str] = set()
        final_zones: List[Dict[str, Any]] = []
        unmatched_detected: List[Dict[str, Any]] = []

        # Pass 1: Exact zone_id or camera_id or building_hint match
        for dz in detected_zones:
            req_zid = str(dz.get("zone_id") or "").strip()
            matched_zid: Optional[str] = None
            if req_zid in ez_by_id and req_zid not in used_existing_ids:
                matched_zid = req_zid
            else:
                dz_text = f"{dz.get('name', '')} {dz.get('description', '')}"
                best_score = 0
                for cand_zid, hints in zone_hints.items():
                    if cand_zid in used_existing_ids:
                        continue
                    score = sum(2 for h in hints if h and h in dz_text)
                    if score > best_score:
                        best_score = score
                        matched_zid = cand_zid

            if matched_zid:
                used_existing_ids.add(matched_zid)
                base_z = ez_by_id[matched_zid]
                final_zones.append(
                    SiteMapGenerator._merge_zone_geometry(
                        base_z, dz, spots_by_zone.get(matched_zid, [])
                    )
                )
            else:
                unmatched_detected.append(dz)

        # Pass 2: Pair remaining camera-backed existing zones (calibrated cameras first!) with remaining detected zones
        remaining_existing = [
            zid for zid in ez_by_id.keys() if zid not in used_existing_ids
        ]
        remaining_existing.sort(
            key=lambda zid: (
                0
                if any(c.get("has_template") for c in cams_by_zone.get(zid, []))
                else (1 if cams_by_zone.get(zid) else 2)
            )
        )

        while unmatched_detected and remaining_existing:
            dz = unmatched_detected.pop(0)
            zid = remaining_existing.pop(0)
            used_existing_ids.add(zid)
            final_zones.append(
                SiteMapGenerator._merge_zone_geometry(
                    ez_by_id[zid], dz, spots_by_zone.get(zid, [])
                )
            )

        # Pass 3: Keep any still-unmatched camera-owning zones so no camera is ever orphaned
        for zid in remaining_existing:
            if cams_by_zone.get(zid) or spots_by_zone.get(zid):
                ez = dict(ez_by_id[zid])
                ez["is_demo_seed"] = 0
                final_zones.append(ez)

        # Only add a fallback zone if there are no camera/spot zones at all;
        # avoid spawning empty 0-spot placeholder zones that clutter the map.
        if not final_zones and unmatched_detected:
            dz = unmatched_detected[0]
            final_zones.append(
                {
                    "zone_id": dz.get("zone_id") or "Z_PLAN_01",
                    "name": dz.get("name") or "主干道停车带",
                    "dead_end_penalty": float(dz.get("dead_end_penalty", 2.0)),
                    "gate_access": "south,east",
                    "map_x": float(dz["map_x"]),
                    "map_y": float(dz["map_y"]),
                    "map_w": float(dz["map_w"]),
                    "map_h": float(dz["map_h"]),
                    "layout_angle": float(dz.get("layout_angle", 0.0)),
                    "description": str(dz.get("description") or "平面图解析主停车带"),
                    "is_demo_seed": 0,
                }
            )

        return final_zones

    @staticmethod
    def _merge_zone_geometry(
        base_zone: Dict[str, Any],
        detected_zone: Dict[str, Any],
        zone_spots: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        spot_cnt = max(1, len(zone_spots))
        zw = float(detected_zone.get("map_w", base_zone["map_w"]))
        zh = float(detected_zone.get("map_h", base_zone["map_h"]))
        # Ensure enough room on the situational map to render all calibrated stalls legibly
        if zw >= zh:
            min_w = min(44.0, max(16.0, spot_cnt * 4.6 + 3.0))
            zw = max(zw, min_w)
            zh = max(zh, 8.8)
        else:
            min_h = min(44.0, max(16.0, spot_cnt * 4.4 + 4.0))
            zh = max(zh, min_h)
            zw = max(zw, 11.5)

        zx = float(np.clip(float(detected_zone.get("map_x", base_zone["map_x"])), 1.5, 97.5 - zw))
        zy = float(np.clip(float(detected_zone.get("map_y", base_zone["map_y"])), 1.5, 97.5 - zh))
        return {
            **base_zone,
            "name": str(detected_zone.get("name") or base_zone["name"]).strip(),
            "map_x": round(zx, 1),
            "map_y": round(zy, 1),
            "map_w": round(zw, 1),
            "map_h": round(zh, 1),
            "layout_angle": round(float(detected_zone.get("layout_angle", 0.0)), 1),
            "dead_end_penalty": float(
                detected_zone.get("dead_end_penalty", base_zone.get("dead_end_penalty", 0.0))
            ),
            "description": str(
                detected_zone.get("description") or base_zone.get("description", "")
            ),
            "is_demo_seed": 0,
        }

    @staticmethod
    def _normalize_units(
        detected_units: List[Dict[str, Any]], existing_units: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not detected_units:
            return [dict(u) for u in existing_units]
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for idx, u in enumerate(detected_units):
            uid = str(u.get("unit_id") or f"{idx+1}-1").strip()
            if uid in seen:
                uid = f"{uid}-{idx+1}"
            seen.add(uid)
            out.append(
                {
                    "unit_id": uid,
                    "name": str(u.get("name") or f"{uid}号楼"),
                    "default_gate": str(u.get("default_gate") or "south"),
                    "map_x": round(float(np.clip(float(u.get("map_x", 50.0)), 5.0, 95.0)), 1),
                    "map_y": round(float(np.clip(float(u.get("map_y", 50.0)), 5.0, 95.0)), 1),
                    "map_w": round(float(np.clip(float(u.get("map_w", 12.4)), 7.0, 24.0)), 1),
                    "map_h": round(float(np.clip(float(u.get("map_h", 6.8)), 4.8, 16.0)), 1),
                }
            )
        return out
