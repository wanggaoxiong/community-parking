import base64
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.config import config_manager
from app.database import Database
from app.homography import extract_spot_patch, subdivide_perspective_row
from app.vision_pipeline import VisionPipeline


class ReferenceAutoCalibrator:
    """
    Reference-Image Self-Calibration & Persistent Template Engine:
      1. Renders a zero-margin 0..1000 normalized coordinate ruler onto a RAM copy
         of the camera's reference photo.
      2. Uses Multimodal VLM (e.g. Azure AI Foundry gpt-6.1-sol / gpt-4o) to detect
         perspective stall trapezoids, irregular corner spots, unmarked elastic strips,
         ground-painted stall numbers, and initial occupancy states.
      3. Refines & welds stall divider vertices against OpenCV MORPH_TOPHAT painted
         white stall lines (with a pure-OpenCV perspective-row fallback when offline).
      4. Produces a privacy-desensitized Reference Template (blurring license plates),
         a 320px ORB grayscale alignment reference for camera-shake compensation, and
         32x32 empty-pavement baseline signatures for FREE stalls.
    """

    def __init__(self, database: Database, vision: VisionPipeline) -> None:
        self.db = database
        self.vision = vision

    def auto_calibrate_from_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        zone_id: Optional[str] = None,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
        full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if full_img is None:
            raise ValueError("无法解码参考图字节流，请上传有效的 JPG/PNG/WebP 图片")

        cam = self.db.get_camera(camera_id)
        if not cam:
            raise ValueError(f"未找到相机机位: {camera_id}")

        target_zone_id = zone_id or cam["zone_id"]
        h_img, w_img = full_img.shape[:2]

        cfg = config_manager.get()
        vlm_cfg = cfg.get("vlm", {})
        car_len_m = float(cfg.get("standard_car_length_m", 5.2))
        base_u = str(vlm_cfg.get("base_url", "")).strip()
        vlm_ready = (
            vlm_cfg.get("mode", "hybrid") != "local_only"
            and bool(base_u)
            and (
                bool(vlm_cfg.get("api_key"))
                or "127.0.0.1" in base_u
                or "localhost" in base_u
            )
        )

        engine_used = "OpenCV_Perspective_Auto"
        scene_summary = "基于地面标线透视几何自动标定"
        building_hint = ""
        vlm_fallback_note = ""
        raw_spots: List[Dict[str, Any]] = []

        if vlm_ready:
            vlm_spots, vlm_meta, vlm_err = self._calibrate_via_vlm(full_img, vlm_cfg)
            if vlm_spots:
                raw_spots = self._refine_polygons_with_opencv_lines(full_img, vlm_spots)
                model_tag = vlm_cfg.get("model", "VLM")
                engine_used = f"Azure/VLM ({model_tag}) + OpenCV 标线吸附"
                scene_summary = vlm_meta.get("scene_summary") or "多模态参考图透视自动标定"
                building_hint = vlm_meta.get("building_hint") or ""
            else:
                vlm_fallback_note = vlm_err or "VLM未返回有效车位"

        if not raw_spots:
            raw_spots, fb_summary = self._calibrate_opencv_fallback(full_img)
            scene_summary = fb_summary
            if vlm_fallback_note:
                engine_used = f"OpenCV_Auto (VLM回退: {vlm_fallback_note[:48]})"

        # Extract per-spot initial features & empty-asphalt reference signatures in RAM
        enriched_spots: List[Dict[str, Any]] = []
        primed_sigs: Dict[str, np.ndarray] = {}
        free_cnt = 0
        occ_cnt = 0

        for idx, sp in enumerate(raw_spots):
            spot_id = f"{camera_id}-S{idx+1:02d}"
            spot_type = sp.get("spot_type", "standard")
            poly = sp["polygon"]
            total_m = float(sp.get("total_meters", 16.0 if spot_type == "elastic_strip" else 5.2))
            cap = max(1, int(total_m // car_len_m)) if spot_type == "elastic_strip" else 1

            patch, mask = extract_spot_patch(full_img, poly, spot_type=spot_type)
            small_gray = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (32, 32))
            fast_obs = self.vision._analyze_patch_fast_opencv(
                patch,
                mask,
                spot_type=spot_type,
                total_meters=total_m,
                car_len_m=car_len_m,
                small_gray=small_gray,
            )

            st = str(sp.get("initial_status") or "").upper()
            if st not in ("FREE", "OCCUPIED", "BLOCKED"):
                st = fast_obs["status"]
            conf = float(sp.get("confidence") or fast_obs["confidence"])
            reason = str(sp.get("reason") or fast_obs["reason"])

            if spot_type == "elastic_strip":
                free_u = int(sp.get("free_units", fast_obs["free_units"] if st == "FREE" else 0))
                occ_u = int(sp.get("occupied_units", fast_obs["occupied_units"]))
                st = "FREE" if free_u > 0 else (st if st == "BLOCKED" else "OCCUPIED")
            else:
                free_u = 1 if st == "FREE" else 0
                occ_u = 1 if st == "OCCUPIED" else 0

            if st == "FREE":
                free_cnt += free_u
            elif st == "OCCUPIED":
                occ_cnt += max(1, occ_u)

            ref_free_sig_b64 = None
            if st == "FREE" and spot_type != "elastic_strip":
                ref_free_sig_b64 = base64.b64encode(small_gray.tobytes()).decode("ascii")

            primed_sigs[spot_id] = small_gray
            enriched_spots.append(
                {
                    "spot_id": spot_id,
                    "label": str(sp.get("label") or f"{idx+1:02d}"),
                    "spot_type": spot_type,
                    "compact_only": bool(sp.get("compact_only", False)),
                    "total_meters": round(total_m, 1),
                    "capacity": cap,
                    "polygon": poly,
                    "initial_status": st,
                    "status": st,
                    "free_units": free_u,
                    "occupied_units": occ_u,
                    "confidence": round(conf, 3),
                    "reason": f"参考图基准：{reason}" if not reason.startswith("参考图") else reason,
                    "engine": engine_used,
                    "ref_free_sig_b64": ref_free_sig_b64,
                }
            )

        # Build privacy-desensitized visual backdrop + 320px ORB alignment grayscale map
        tpl_jpeg_data_uri, tpl_gray_b64, plates_redacted = self._build_privacy_safe_template(
            full_img, enriched_spots
        )

        # Zero-Disk privacy guarantee: destroy raw unredacted image immediately
        del full_img
        del arr

        with self.vision._lock:
            self.vision.privacy_stats["frames_processed_in_ram"] += 1

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        template_meta = {
            "calibrated_at": time.time(),
            "engine": engine_used,
            "scene_summary": scene_summary,
            "building_hint": building_hint,
            "spots_count": len(enriched_spots),
            "initial_free": free_cnt,
            "initial_occupied": occ_cnt,
            "plates_redacted": plates_redacted,
            "resolution": [w_img, h_img],
            "elapsed_ms": elapsed_ms,
        }

        saved_spots = enriched_spots
        if auto_save:
            self.db.save_camera_template(
                camera_id=camera_id,
                template_jpeg_b64=tpl_jpeg_data_uri,
                template_gray_b64=tpl_gray_b64,
                template_meta=template_meta,
                image_width=w_img,
                image_height=h_img,
                zone_id=target_zone_id,
            )
            saved_spots = self.db.save_camera_spots(
                camera_id=camera_id,
                zone_id=target_zone_id,
                spots_payload=enriched_spots,
                image_width=w_img,
                image_height=h_img,
            )
            self.vision.clear_frame_cache(camera_id)
            self.vision.prime_reference_signatures(camera_id, primed_sigs)

        return {
            "ok": True,
            "camera_id": camera_id,
            "zone_id": target_zone_id,
            "auto_saved": auto_save,
            "image_width": w_img,
            "image_height": h_img,
            "engine_used": engine_used,
            "scene_summary": scene_summary,
            "building_hint": building_hint,
            "plates_redacted": plates_redacted,
            "elapsed_ms": elapsed_ms,
            "template_meta": template_meta,
            "template_jpeg_b64": tpl_jpeg_data_uri,
            "spots": saved_spots,
        }

    # -------------------------------------------------------------------------
    # Stage 1: Coordinate-Ruler Annotated VLM Spatial Calibration
    # -------------------------------------------------------------------------
    @staticmethod
    def _draw_coordinate_ruler(img_bgr: np.ndarray) -> np.ndarray:
        """
        Draws a zero-padding 0..1000 normalized coordinate ruler & subtle 100-unit
        grid directly inside the image edges so VLM normalized coordinates align 1:1.
        """
        h0, w0 = img_bgr.shape[:2]
        scale = min(1.0, 1024.0 / max(h0, w0))
        if scale < 1.0:
            canvas = cv2.resize(img_bgr, (int(round(w0 * scale)), int(round(h0 * scale))))
        else:
            canvas = img_bgr.copy()

        h, w = canvas.shape[:2]
        overlay = canvas.copy()

        # Subtle 100-unit grid lines
        for k in range(100, 1000, 100):
            gx = int(round(k / 1000.0 * (w - 1)))
            gy = int(round(k / 1000.0 * (h - 1)))
            cv2.line(overlay, (gx, 0), (gx, h - 1), (0, 255, 255), 1, cv2.LINE_AA)
            cv2.line(overlay, (0, gy), (w - 1, gy), (0, 255, 255), 1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.22, canvas, 0.78, 0, canvas)

        # Edge ticks every 50 units + dark-pill numeric labels every 100 units
        font = cv2.FONT_HERSHEY_SIMPLEX
        for k in range(0, 1001, 50):
            gx = int(round(k / 1000.0 * (w - 1)))
            gy = int(round(k / 1000.0 * (h - 1)))
            tick_len = 10 if k % 100 == 0 else 5
            cv2.line(canvas, (gx, 0), (gx, tick_len), (0, 255, 255), 2)
            cv2.line(canvas, (gx, h - 1), (gx, h - 1 - tick_len), (0, 255, 255), 2)
            cv2.line(canvas, (0, gy), (tick_len, gy), (0, 255, 255), 2)
            cv2.line(canvas, (w - 1, gy), (w - 1 - tick_len, gy), (0, 255, 255), 2)

            if 100 <= k <= 900 and k % 100 == 0:
                lbl = str(k)
                # Top & Bottom X ruler labels
                cv2.rectangle(canvas, (gx - 16, 12), (gx + 16, 26), (20, 20, 24), -1)
                cv2.putText(canvas, lbl, (gx - 14, 23), font, 0.36, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.rectangle(canvas, (gx - 16, h - 27), (gx + 16, h - 13), (20, 20, 24), -1)
                cv2.putText(canvas, lbl, (gx - 14, h - 16), font, 0.36, (0, 255, 255), 1, cv2.LINE_AA)
                # Left & Right Y ruler labels
                cv2.rectangle(canvas, (12, gy - 7), (42, gy + 7), (20, 20, 24), -1)
                cv2.putText(canvas, lbl, (14, gy + 4), font, 0.36, (255, 230, 80), 1, cv2.LINE_AA)

        return canvas

    def _calibrate_via_vlm(
        self, full_img: np.ndarray, vlm_cfg: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
        ruler_img = self._draw_coordinate_ruler(full_img)
        ok, buf = cv2.imencode(".jpg", ruler_img, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        if not ok:
            return [], {}, "参考图JPEG编码失败"
        b64_jpg = base64.b64encode(buf.tobytes()).decode("ascii")

        with self.vision._lock:
            self.vision.privacy_stats["slow_lane_vlm_calls"] += 1

        prompt = (
            "你是高精度停车场空间视觉标定引擎。这张相机基准参考图四周与网格标有 0..1000 归一化坐标尺（X从左0到右1000，Y从上0到下1000）。\n"
            "请自动识别画面中【全部地面停车位】（含已停有车辆的车位、空车位、画面左右边缘完整/半截划线车位、异形转角位、或无划线顺停路段），输出用于后续长期复用的空间参考模板。\n"
            "几何与语义要求：\n"
            "1. 按从左到右（或从近到远）顺序输出所有车位；若地面印有车位编号（如 01, 02...07），label 直接使用该编号，否则命名为 01, 02...。\n"
            "2. 标准划线车位 `spot_type` 填 \"standard\"，`polygon_1000` 必须沿【地面白色车位标线四边形】按顺时针给出 4 个角点：`[[后左x,后左y], [后右x,后右y], [前右x,前右y], [前左x,前左y]]`（Y较小一侧为靠马路牙子/限位器的车位后端，Y较大一侧为靠行车道白线的车位前端）。\n"
            "3. 严格贴合近大远小透视斜率：左侧车位分隔线与右侧车位分隔线向消失点倾斜，相邻连排车位必须共用同一条分隔线的上下端点！被车辆遮挡的车位边线请结合露出白线、限位器与相邻车位透视规律精准补齐。\n"
            "4. 若存在不规则多边形车位，`spot_type` 填 \"irregular\"（紧凑迷你位设 `compact_only`: true）；若为无划线长条路侧带，`spot_type` 填 \"elastic_strip\" 并估算 `total_meters`。\n"
            "5. 判断当前参考图中每个车位的初始状态 `initial_status`：停有汽车填 \"OCCUPIED\"，空车位填 \"FREE\"，锥桶/地锁/杂物占道填 \"BLOCKED\"。\n"
            "仅输出严格 JSON（不要输出代码块以外的解释）：\n"
            "{\n"
            '  "scene_summary": "不超过28字中文场景总结",\n'
            '  "building_hint": "画面可见小区或楼栋文字，如 3栋",\n'
            '  "spots": [\n'
            "    {\n"
            '      "label": "01",\n'
            '      "spot_type": "standard",\n'
            '      "compact_only": false,\n'
            '      "total_meters": 5.2,\n'
            '      "polygon_1000": [[83,478],[215,478],[139,650],[2,650]],\n'
            '      "initial_status": "OCCUPIED",\n'
            '      "confidence": 0.96,\n'
            '      "reason": "白色轿车停放"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        content, err, _ = self.vision._post_vlm_completions(
            vlm_cfg,
            prompt,
            f"data:image/jpeg;base64,{b64_jpg}",
            max_tokens=4096,
            reasoning_effort="medium",
            timeout_override=65.0,
        )
        if err or not content:
            return [], {}, err

        try:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            parsed = json.loads(match.group(0) if match else content)
            raw_list = parsed.get("spots") or []
            if not isinstance(raw_list, list) or len(raw_list) == 0:
                return [], {}, "模型返回的 spots 列表为空"

            h_img, w_img = full_img.shape[:2]
            converted: List[Dict[str, Any]] = []
            for idx, item in enumerate(raw_list):
                pts = item.get("polygon_1000") or item.get("polygon") or item.get("points") or []
                if not isinstance(pts, list) or len(pts) < 3:
                    continue

                # Convert 0..1000 normalized coords to image pixel coords
                max_coord = max(max(float(p[0]), float(p[1])) for p in pts if len(p) >= 2)
                is_norm_1000 = max_coord <= 1025.0 and not (w_img == 1024 and max_coord > 1002.0)
                px_poly: List[List[float]] = []
                for p in pts:
                    if len(p) < 2:
                        continue
                    if is_norm_1000:
                        x = float(np.clip(float(p[0]) / 1000.0 * w_img, 0, w_img - 1))
                        y = float(np.clip(float(p[1]) / 1000.0 * h_img, 0, h_img - 1))
                    else:
                        x = float(np.clip(float(p[0]), 0, w_img - 1))
                        y = float(np.clip(float(p[1]), 0, h_img - 1))
                    px_poly.append([round(x, 1), round(y, 1)])

                if len(px_poly) < 3:
                    continue
                spot_type = str(item.get("spot_type", "standard")).lower()
                if spot_type not in ("standard", "irregular", "elastic_strip"):
                    spot_type = "standard"

                if spot_type in ("standard", "elastic_strip") and len(px_poly) == 4:
                    px_poly = self._order_quad_tl_tr_br_bl(px_poly)

                area = float(cv2.contourArea(np.array(px_poly, dtype=np.float32)))
                if area < (w_img * h_img * 0.0015):
                    continue

                converted.append(
                    {
                        "label": str(item.get("label") or f"{idx+1:02d}").strip(),
                        "spot_type": spot_type,
                        "compact_only": bool(item.get("compact_only", False)),
                        "total_meters": float(
                            item.get("total_meters", 16.0 if spot_type == "elastic_strip" else 5.2)
                        ),
                        "polygon": px_poly,
                        "initial_status": str(item.get("initial_status", "")).upper(),
                        "confidence": float(item.get("confidence", 0.94)),
                        "reason": str(item.get("reason", "AI 参考图自动标定")),
                    }
                )

            if not converted:
                return [], {}, "未解析出有效面积的车位多边形"

            meta = {
                "scene_summary": str(parsed.get("scene_summary", "")).strip(),
                "building_hint": str(parsed.get("building_hint", "")).strip(),
            }
            return converted, meta, None
        except Exception as parse_exc:
            return [], {}, f"标定JSON解析异常: {parse_exc}"

    # -------------------------------------------------------------------------
    # Stage 2: OpenCV White-Line Snapping & Pure-OpenCV Perspective Fallback
    # -------------------------------------------------------------------------
    @staticmethod
    def _order_quad_tl_tr_br_bl(pts: List[List[float]]) -> List[List[float]]:
        arr = np.array(pts[:4], dtype=np.float32)
        idx_y = np.argsort(arr[:, 1])
        top2 = arr[idx_y[:2]]
        bot2 = arr[idx_y[2:]]
        tl, tr = top2[np.argsort(top2[:, 0])]
        bl, br = bot2[np.argsort(bot2[:, 0])]
        return [
            [round(float(tl[0]), 1), round(float(tl[1]), 1)],
            [round(float(tr[0]), 1), round(float(tr[1]), 1)],
            [round(float(br[0]), 1), round(float(br[1]), 1)],
            [round(float(bl[0]), 1), round(float(bl[1]), 1)],
        ]

    @staticmethod
    def _detect_white_stall_features(
        img_bgr: np.ndarray, y_min: int, y_max: int
    ) -> Tuple[Optional[float], List[Tuple[float, float]]]:
        """
        Detect horizontal front white boundary line and slanted white stall divider lines
        within [y_min, y_max] using morphological white top-hat filtering.
        Returns (snapped_front_y, list of (x_at_ymin, x_at_ymax) divider lines).
        """
        h, w = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # 1. Horizontal painted line response
        kh = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 48)))
        th_h = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kh)
        y_lo = max(0, y_min)
        y_hi = min(h, y_max)
        front_y: Optional[float] = None
        if y_hi - y_lo > 25:
            band_start = int(y_lo + (y_hi - y_lo) * 0.65)
            band_end = min(h, int(y_hi + h * 0.035))
            if band_end > band_start:
                row_strength = np.mean(th_h[band_start:band_end, :] > 34, axis=1)
                best_rel = int(np.argmax(row_strength))
                if float(row_strength[best_rel]) >= 0.28:
                    front_y = float(band_start + best_rel)

        # 2. Slanted vertical divider line segments
        kv = cv2.getStructuringElement(cv2.MORPH_RECT, (max(17, w // 42), 1))
        th_v = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kv)
        r0 = max(0, y_min - int(h * 0.02))
        r1 = min(h, y_max + int(h * 0.02))
        if r1 - r0 < 35:
            return front_y, []

        edges = cv2.Canny(th_v[r0:r1, :], 32, 110)
        min_len = max(42, int((r1 - r0) * 0.36))
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180.0, threshold=28, minLineLength=min_len, maxLineGap=22
        )
        raw_dividers: List[Tuple[float, float]] = []
        if lines is not None:
            for seg in lines[:, 0]:
                x1, y1, x2, y2 = float(seg[0]), float(seg[1] + r0), float(seg[2]), float(seg[3] + r0)
                dy = y2 - y1
                dx = x2 - x1
                if abs(dy) < min_len * 0.72:
                    continue
                slope_x_per_y = dx / dy
                if abs(slope_x_per_y) > 1.15:
                    continue
                x_top = x1 + (y_min - y1) * slope_x_per_y
                x_bot = x1 + (y_max - y1) * slope_x_per_y
                raw_dividers.append((x_top, x_bot))

        if not raw_dividers:
            return front_y, []

        # Cluster nearby parallel Hough edges (left/right edges of the same 8px white paint stripe)
        raw_dividers.sort(key=lambda d: 0.5 * (d[0] + d[1]))
        clustered: List[Tuple[float, float]] = []
        cluster_buf: List[Tuple[float, float]] = [raw_dividers[0]]
        merge_tol = w * 0.024
        for item in raw_dividers[1:]:
            prev_mid = 0.5 * (cluster_buf[-1][0] + cluster_buf[-1][1])
            cur_mid = 0.5 * (item[0] + item[1])
            if abs(cur_mid - prev_mid) <= merge_tol:
                cluster_buf.append(item)
            else:
                clustered.append(
                    (
                        float(np.median([c[0] for c in cluster_buf])),
                        float(np.median([c[1] for c in cluster_buf])),
                    )
                )
                cluster_buf = [item]
        clustered.append(
            (
                float(np.median([c[0] for c in cluster_buf])),
                float(np.median([c[1] for c in cluster_buf])),
            )
        )
        return front_y, clustered

    def _refine_polygons_with_opencv_lines(
        self, img_bgr: np.ndarray, spots: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Snap VLM-detected standard 4-point stall polygons onto nearby painted white
        lines and weld shared dividers between contiguous stalls without distorting layout.
        """
        h, w = img_bgr.shape[:2]
        std_indices = [
            i
            for i, s in enumerate(spots)
            if s.get("spot_type") == "standard" and len(s.get("polygon", [])) == 4
        ]
        if len(std_indices) < 2:
            return spots

        # Check if standard spots form a primary horizontal band
        ys_top = [0.5 * (spots[i]["polygon"][0][1] + spots[i]["polygon"][1][1]) for i in std_indices]
        ys_bot = [0.5 * (spots[i]["polygon"][2][1] + spots[i]["polygon"][3][1]) for i in std_indices]
        med_top = float(np.median(ys_top))
        med_bot = float(np.median(ys_bot))
        row_h = max(20.0, med_bot - med_top)

        row_idxs = [
            idx
            for idx, yt, yb in zip(std_indices, ys_top, ys_bot)
            if abs(yt - med_top) < row_h * 0.38 and abs(yb - med_bot) < row_h * 0.38
        ]
        if len(row_idxs) < 2:
            return spots

        row_idxs.sort(key=lambda idx: 0.25 * sum(p[0] for p in spots[idx]["polygon"]))
        front_y, dividers = self._detect_white_stall_features(
            img_bgr, int(round(med_top)), int(round(med_bot))
        )

        # Deep-copy polygons before candidate snapping
        cand_polys = {idx: [list(pt) for pt in spots[idx]["polygon"]] for idx in row_idxs}

        # 1. Snap front horizontal line if detected within ±3.2% of image height
        if front_y is not None and abs(front_y - med_bot) <= h * 0.032:
            for idx in row_idxs:
                if abs(cand_polys[idx][2][1] - front_y) <= h * 0.035:
                    cand_polys[idx][2][1] = round(front_y, 1)
                if abs(cand_polys[idx][3][1] - front_y) <= h * 0.035:
                    cand_polys[idx][3][1] = round(front_y, 1)

        snap_tol = w * 0.030

        def snap_edge_to_divider(xt: float, xb: float, yt: float, yb: float) -> Tuple[float, float]:
            best_d = None
            best_err = snap_tol
            for d_top, d_bot in dividers:
                # Re-project divider from [med_top, med_bot] to exact [yt, yb]
                denom = max(10.0, med_bot - med_top)
                slope = (d_bot - d_top) / denom
                proj_top = d_top + (yt - med_top) * slope
                proj_bot = d_top + (yb - med_top) * slope
                err = 0.5 * (abs(proj_top - xt) + abs(proj_bot - xb))
                if err < best_err and abs(proj_top - xt) <= snap_tol * 1.25 and abs(proj_bot - xb) <= snap_tol * 1.25:
                    best_err = err
                    best_d = (proj_top, proj_bot)
            if best_d is None:
                return xt, xb
            return (
                round(float(np.clip(best_d[0], 0, w - 1)), 1),
                round(float(np.clip(best_d[1], 0, w - 1)), 1),
            )

        # 2. Snap outer left edge of leftmost stall & outer right edge of rightmost stall
        first_p = cand_polys[row_idxs[0]]
        first_p[0][0], first_p[3][0] = snap_edge_to_divider(
            first_p[0][0], first_p[3][0], first_p[0][1], first_p[3][1]
        )
        last_p = cand_polys[row_idxs[-1]]
        last_p[1][0], last_p[2][0] = snap_edge_to_divider(
            last_p[1][0], last_p[2][0], last_p[1][1], last_p[2][1]
        )

        # 3. Weld & snap shared internal dividers between adjacent stalls
        adj_tol = w * 0.045
        for k in range(len(row_idxs) - 1):
            lp = cand_polys[row_idxs[k]]
            rp = cand_polys[row_idxs[k + 1]]
            if abs(lp[1][0] - rp[0][0]) <= adj_tol and abs(lp[2][0] - rp[3][0]) <= adj_tol:
                yt = round(0.5 * (lp[1][1] + rp[0][1]), 1)
                yb = round(0.5 * (lp[2][1] + rp[3][1]), 1)
                xt = 0.5 * (lp[1][0] + rp[0][0])
                xb = 0.5 * (lp[2][0] + rp[3][0])
                s_xt, s_xb = snap_edge_to_divider(xt, xb, yt, yb)
                lp[1] = [s_xt, yt]
                lp[2] = [s_xb, yb]
                rp[0] = [s_xt, yt]
                rp[3] = [s_xb, yb]

        # Validate every snapped polygon preserves positive width; apply only valid ones
        min_w = w * 0.032
        for idx in row_idxs:
            cp = cand_polys[idx]
            if (cp[1][0] - cp[0][0]) >= min_w and (cp[2][0] - cp[3][0]) >= min_w and (cp[2][1] - cp[1][1]) >= 20:
                spots[idx]["polygon"] = cp

        return spots

    def _calibrate_opencv_fallback(
        self, img_bgr: np.ndarray
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Pure-OpenCV automatic perspective stall detector when VLM is offline or disabled.
        Finds painted horizontal front boundary + slanted divider stripes and reconstructs
        perspective-consistent stall trapezoids.
        """
        h, w = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        kh = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 48)))
        th_h = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kh)
        search_y0, search_y1 = int(h * 0.42), int(h * 0.85)
        row_profile = np.mean(th_h[search_y0:search_y1, :] > 35, axis=1)
        best_rel = int(np.argmax(row_profile))

        if float(row_profile[best_rel]) >= 0.25:
            y_front = int(search_y0 + best_rel)
            y_back = max(int(h * 0.25), y_front - int(h * 0.175))
            _, dividers = self._detect_white_stall_features(img_bgr, y_back, y_front)
            if len(dividers) >= 3:
                # Estimate linear perspective relation x_top = a * x_bot + b across detected dividers
                xb_arr = np.array([d[1] for d in dividers], dtype=np.float64)
                xt_arr = np.array([d[0] for d in dividers], dtype=np.float64)
                a_coef, b_coef = np.polyfit(xb_arr, xt_arr, 1) if len(dividers) >= 2 else (1.0, 0.0)

                diffs = np.diff(xb_arr)
                valid_pitches = [d for d in diffs if w * 0.07 <= d <= w * 0.32]
                pitch_bot = float(np.median(valid_pitches)) if valid_pitches else (w / 6.5)

                # Interpolate missing occluded dividers & extrapolate to left/right frame boundaries
                full_xb: List[float] = [float(xb_arr[0])]
                for nxt in xb_arr[1:]:
                    start_v = full_xb[-1]
                    gap = nxt - start_v
                    steps = max(1, int(round(gap / pitch_bot)))
                    for s in range(1, steps + 1):
                        full_xb.append(start_v + s * (gap / steps))

                while full_xb[0] - pitch_bot >= -pitch_bot * 0.28:
                    full_xb.insert(0, full_xb[0] - pitch_bot)
                while full_xb[-1] + pitch_bot <= w - 1 + pitch_bot * 0.28:
                    full_xb.append(full_xb[-1] + pitch_bot)

                spots_out: List[Dict[str, Any]] = []
                for i in range(len(full_xb) - 1):
                    xb0, xb1 = full_xb[i], full_xb[i + 1]
                    xt0 = a_coef * xb0 + b_coef
                    xt1 = a_coef * xb1 + b_coef
                    poly = [
                        [round(float(np.clip(xt0, 0, w - 1)), 1), float(y_back)],
                        [round(float(np.clip(xt1, 0, w - 1)), 1), float(y_back)],
                        [round(float(np.clip(xb1, 0, w - 1)), 1), float(y_front)],
                        [round(float(np.clip(xb0, 0, w - 1)), 1), float(y_front)],
                    ]
                    if (poly[1][0] - poly[0][0]) >= w * 0.04 and (poly[2][0] - poly[3][0]) >= w * 0.04:
                        spots_out.append(
                            {
                                "label": f"{len(spots_out)+1:02d}",
                                "spot_type": "standard",
                                "compact_only": False,
                                "total_meters": 5.2,
                                "polygon": poly,
                            }
                        )
                if len(spots_out) >= 2:
                    return spots_out, f"OpenCV 标线几何自动识别 {len(spots_out)} 个连排透视车位"

        # Generic 5-stall perspective row fallback for unmarked synthetic test scenes
        outer = [
            [round(w * 0.10, 1), round(h * 0.48, 1)],
            [round(w * 0.90, 1), round(h * 0.48, 1)],
            [round(w * 0.95, 1), round(h * 0.72, 1)],
            [round(w * 0.05, 1), round(h * 0.72, 1)],
        ]
        traps = subdivide_perspective_row(outer, count=5, direction="along_01")
        return (
            [
                {
                    "label": f"{i+1:02d}",
                    "spot_type": "standard",
                    "compact_only": False,
                    "total_meters": 5.2,
                    "polygon": p,
                }
                for i, p in enumerate(traps)
            ],
            "OpenCV 默认透视网格自动划分 (5 车位)",
        )

    # -------------------------------------------------------------------------
    # Stage 3: Privacy-Desensitized Reference Template Builder
    # -------------------------------------------------------------------------
    @staticmethod
    def _build_privacy_safe_template(
        full_img: np.ndarray, spots: List[Dict[str, Any]]
    ) -> Tuple[str, str, int]:
        """
        Creates:
          1. `template_jpeg_b64` (`data:image/jpeg;base64,...`): max-960px backdrop with
             automatic mosaic/Gaussian blurring over license plates for privacy compliance.
          2. `template_gray_b64`: max-320px grayscale alignment image for camera-shake
             ORB homography registration on subsequent frames.
          3. `plates_redacted`: count of redacted license-plate candidates.
        """
        h, w = full_img.shape[:2]

        # 1. Build 320px grayscale alignment map BEFORE blurring (only tiny structural features)
        align_scale = min(1.0, 320.0 / max(h, w))
        align_gray = cv2.resize(
            cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY),
            (max(32, int(round(w * align_scale))), max(32, int(round(h * align_scale)))),
        )
        ok_g, buf_g = cv2.imencode(".jpg", align_gray, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        tpl_gray_b64 = base64.b64encode(buf_g.tobytes()).decode("ascii") if ok_g else ""

        # 2. Detect & blur license plates (Chinese blue & green NEV plates) in RAM
        safe_img = full_img.copy()
        hsv = cv2.cvtColor(safe_img, cv2.COLOR_BGR2HSV)
        gray_full = cv2.cvtColor(safe_img, cv2.COLOR_BGR2GRAY)
        sobel_x = np.abs(cv2.Sobel(gray_full, cv2.CV_32F, 1, 0, ksize=3))

        blue_plate = cv2.inRange(hsv, np.array([98, 95, 65]), np.array([132, 255, 255]))
        green_plate = cv2.inRange(hsv, np.array([68, 55, 140]), np.array([95, 255, 255]))
        plate_mask = cv2.bitwise_or(blue_plate, green_plate)

        # Restrict search to occupied stall regions (expanded slightly for 3D bumper projection)
        occ_roi_mask = np.zeros((h, w), dtype=np.uint8)
        occ_spots = [s for s in spots if s.get("initial_status") == "OCCUPIED" or s.get("status") == "OCCUPIED"]
        if occ_spots:
            for sp in occ_spots:
                pts_arr = np.array(sp["polygon"], dtype=np.int32)
                cv2.fillPoly(occ_roi_mask, [pts_arr], 255)
            occ_roi_mask = cv2.dilate(occ_roi_mask, np.ones((25, 25), np.uint8), iterations=1)
            plate_mask = cv2.bitwise_and(plate_mask, occ_roi_mask)
        else:
            plate_mask[: int(h * 0.25), :] = 0

        close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
        plate_mask = cv2.morphologyEx(plate_mask, cv2.MORPH_CLOSE, close_k)

        contours, _ = cv2.findContours(plate_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        redacted = 0
        for cnt in contours:
            rx, ry, rw, rh = cv2.boundingRect(cnt)
            aspect = rw / max(1.0, float(rh))
            if (
                w * 0.014 <= rw <= w * 0.14
                and h * 0.006 <= rh <= h * 0.055
                and 1.7 <= aspect <= 6.2
                and float(np.mean(sobel_x[ry : ry + rh, rx : rx + rw])) >= 22.0
            ):
                pad = 6
                x0, y0 = max(0, rx - pad), max(0, ry - pad)
                x1, y1 = min(w, rx + rw + pad), min(h, ry + rh + pad)
                roi = safe_img[y0:y1, x0:x1]
                if roi.size > 0:
                    small = cv2.resize(roi, (max(4, (x1 - x0) // 7), max(2, (y1 - y0) // 7)))
                    mosaic = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
                    safe_img[y0:y1, x0:x1] = cv2.GaussianBlur(mosaic, (11, 11), 0)
                    redacted += 1

        preview_scale = min(1.0, 960.0 / max(h, w))
        if preview_scale < 1.0:
            safe_img = cv2.resize(
                safe_img,
                (int(round(w * preview_scale)), int(round(h * preview_scale))),
                interpolation=cv2.INTER_AREA,
            )

        ok_j, buf_j = cv2.imencode(".jpg", safe_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        b64_preview = (
            "data:image/jpeg;base64," + base64.b64encode(buf_j.tobytes()).decode("ascii")
            if ok_j
            else ""
        )
        return b64_preview, tpl_gray_b64, redacted
