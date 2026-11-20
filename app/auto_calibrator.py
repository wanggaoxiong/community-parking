import base64
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.camera_naming import parse_camera_group_codes
from app.config import config_manager
from app.database import Database
from app.homography import (
    derive_vehicle_envelope_polygon,
    extract_spot_patch,
    subdivide_perspective_row,
)
from app.vision_pipeline import VisionPipeline, apply_camera_crop_roi


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
        allow_vlm: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        cam = self.db.get_camera(camera_id)
        if not cam:
            raise ValueError(f"未找到相机机位: {camera_id}")

        with self.vision.decode_guard():
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if full_img is None:
                raise ValueError("无法解码参考图字节流，请上传有效的 JPG/PNG/WebP 图片")

            h_raw, w_raw = full_img.shape[:2]
            if max(h_raw, w_raw) > 2560:
                sc = 2560.0 / float(max(h_raw, w_raw))
                resized = cv2.resize(
                    full_img,
                    (max(64, int(round(w_raw * sc))), max(64, int(round(h_raw * sc)))),
                    interpolation=cv2.INTER_AREA,
                )
                del full_img
                full_img = resized

            raw_h0, raw_w0 = full_img.shape[:2]
            prev_crop_cfg = dict(cam.get("crop_roi") or {})
            had_crop_enabled = bool(prev_crop_cfg.get("enabled"))
            # Always back up the uncropped privacy-redacted full frame; NEVER pre-slice a newly
            # uploaded reference photo with a previous photo's stale static crop_roi!
            full_frame_backup_uri, _, _ = self._build_privacy_safe_template(full_img, [])

        target_zone_id = zone_id or cam["zone_id"]
        existing_spots = self.db.list_spots(camera_id=camera_id)
        reclaimable_spots = self.db.list_reclaimable_spots_for_camera(camera_id)
        existing_ids_set = {s["spot_id"] for s in existing_spots}
        merged_existing = existing_spots + [
            s for s in reclaimable_spots if s["spot_id"] not in existing_ids_set
        ]
        custom_existing = [s for s in merged_existing if s.get("map_custom")]

        cfg = config_manager.get()
        vlm_cfg = cfg.get("vlm", {})
        car_len_m = float(cfg.get("standard_car_length_m", 5.2))
        base_u = str(vlm_cfg.get("base_url", "")).strip()
        vlm_ready = (
            bool(allow_vlm)
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
            vlm_spots, vlm_meta, vlm_err = self._calibrate_via_vlm(
                full_img, vlm_cfg, cam=cam, existing_spots=merged_existing
            )
            if vlm_spots:
                row_geom = str(vlm_meta.get("row_geometry") or "auto")
                raw_spots = self._refine_polygons_with_opencv_lines(
                    full_img, vlm_spots, row_geometry=row_geom
                )
                model_tag = vlm_cfg.get("model", "VLM")
                geom_label = "弯道/铺装自适应焊合" if row_geom == "curved_or_unlined" else "OpenCV 标线吸附"
                engine_used = f"Azure/VLM ({model_tag}) + {geom_label}"
                scene_summary = vlm_meta.get("scene_summary") or "多模态参考图透视自动标定"
                building_hint = vlm_meta.get("building_hint") or ""
            else:
                vlm_fallback_note = vlm_err or "VLM未返回有效车位"

        if not raw_spots:
            raw_spots, fb_summary = self._calibrate_opencv_fallback(
                full_img, cam=cam, existing_spots=existing_spots
            )
            scene_summary = fb_summary
            if vlm_fallback_note:
                engine_used = f"OpenCV_Auto (VLM回退: {vlm_fallback_note[:48]})"

        # Precompute 3D vehicle envelopes on uncropped frame so dynamic crop_roi never clips tall vans/roofs
        for sp in raw_spots:
            poly0 = sp["polygon"]
            st0 = sp.get("spot_type", "standard")
            if not (isinstance(sp.get("vehicle_polygon"), list) and len(sp["vehicle_polygon"]) >= 3):
                sp["vehicle_polygon"] = (
                    [list(p) for p in poly0]
                    if st0 == "elastic_strip"
                    else derive_vehicle_envelope_polygon(poly0, raw_w0, raw_h0)
                )

        # Automatically compute smart parking-strip crop_roi around newly detected ground + 3D
        # vehicle envelopes (with SIFT landmark padding) unless user explicitly disabled cropping!
        crop_cfg: Dict[str, Any] = dict(prev_crop_cfg)
        user_disabled_crop = bool(prev_crop_cfg.get("user_disabled", False))
        crop_updated = False
        crop_offset_x = 0
        crop_offset_y = 0
        if (not user_disabled_crop) and raw_spots:
            all_xs: List[float] = []
            all_ys: List[float] = []
            for sp in raw_spots:
                for pt in sp["polygon"]:
                    all_xs.append(float(pt[0]))
                    all_ys.append(float(pt[1]))
                for pt in sp.get("vehicle_polygon") or []:
                    all_xs.append(float(pt[0]))
                    all_ys.append(float(pt[1]))
            min_x, max_x = min(all_xs), max(all_xs)
            min_y, max_y = min(all_ys), max(all_ys)
            span_w = max(64.0, max_x - min_x)
            span_h = max(48.0, max_y - min_y)
            pad_x = max(raw_w0 * 0.06, span_w * 0.12)
            pad_top = max(raw_h0 * 0.055, span_h * 0.18)
            pad_bot = max(raw_h0 * 0.055, span_h * 0.16)
            cx0 = max(0, int(np.floor(min_x - pad_x)))
            cy0 = max(0, int(np.floor(min_y - pad_top)))
            cx1 = min(raw_w0, int(np.ceil(max_x + pad_x)))
            cy1 = min(raw_h0, int(np.ceil(max_y + pad_bot)))
            cw = cx1 - cx0
            ch = cy1 - cy0
            if cw >= 96 and ch >= 96 and (cw * ch) <= int(0.84 * raw_w0 * raw_h0):
                cropped_new = np.ascontiguousarray(full_img[cy0:cy1, cx0:cx1])
                del full_img
                full_img = cropped_new
                crop_offset_x, crop_offset_y = cx0, cy0
                for sp in raw_spots:
                    sp["polygon"] = [
                        [round(max(0.0, min(cw - 1.0, p[0] - cx0)), 1), round(max(0.0, min(ch - 1.0, p[1] - cy0)), 1)]
                        for p in sp["polygon"]
                    ]
                    sp["vehicle_polygon"] = [
                        [round(max(0.0, min(cw - 1.0, p[0] - cx0)), 1), round(max(0.0, min(ch - 1.0, p[1] - cy0)), 1)]
                        for p in (sp.get("vehicle_polygon") or [])
                    ]
                crop_cfg = {
                    "enabled": True,
                    "x": round(cx0 / float(raw_w0), 4),
                    "y": round(cy0 / float(raw_h0), 4),
                    "w": round(cw / float(raw_w0), 4),
                    "h": round(ch / float(raw_h0), 4),
                    "raw_width": raw_w0,
                    "raw_height": raw_h0,
                    "raw_size": [raw_w0, raw_h0],
                    "raw_aspect": round(float(raw_w0) / max(1.0, float(raw_h0)), 5),
                    "user_disabled": False,
                }
                crop_updated = True
            else:
                crop_cfg = {
                    "enabled": False,
                    "x": 0.0,
                    "y": 0.0,
                    "w": 1.0,
                    "h": 1.0,
                    "raw_width": raw_w0,
                    "raw_height": raw_h0,
                    "raw_size": [raw_w0, raw_h0],
                    "raw_aspect": round(float(raw_w0) / max(1.0, float(raw_h0)), 5),
                    "user_disabled": False,
                }
                crop_updated = True

        h_img, w_img = full_img.shape[:2]

        # Extract per-spot initial features & empty-asphalt reference signatures in RAM
        enriched_spots: List[Dict[str, Any]] = []

        primed_sigs: Dict[str, np.ndarray] = {}
        free_cnt = 0
        occ_cnt = 0

        for idx, sp in enumerate(raw_spots):
            bound_custom = custom_existing[idx] if idx < len(custom_existing) else None
            bound_sid = (
                str(bound_custom["spot_id"]).strip()
                if bound_custom and bound_custom.get("spot_id")
                else ""
            )
            spot_id = (
                bound_sid
                if bound_sid and not bound_sid.startswith("MAP_")
                else f"{camera_id}-S{idx+1:02d}"
            )
            spot_type = sp.get("spot_type", "standard")
            poly = sp["polygon"]
            total_m = float(sp.get("total_meters", 16.0 if spot_type == "elastic_strip" else 5.2))
            cap = max(1, int(total_m // car_len_m)) if spot_type == "elastic_strip" else 1
            vpoly = (
                sp.get("vehicle_polygon")
                if isinstance(sp.get("vehicle_polygon"), list) and len(sp.get("vehicle_polygon")) >= 3
                else (
                    [list(p) for p in poly]
                    if spot_type == "elastic_strip"
                    else derive_vehicle_envelope_polygon(poly, w_img, h_img)
                )
            )

            patch, mask = extract_spot_patch(full_img, poly, spot_type=spot_type)
            veh_patch, veh_mask = (
                extract_spot_patch(full_img, vpoly, spot_type=spot_type, out_size=(160, 160))
                if spot_type != "elastic_strip"
                else (None, None)
            )
            small_gray, comp_sig = self.vision._make_dual_signatures_32(patch, veh_patch)
            fast_obs = self.vision._analyze_patch_fast_opencv(
                patch,
                mask,
                spot_type=spot_type,
                total_meters=total_m,
                car_len_m=car_len_m,
                small_gray=small_gray,
                veh_patch_bgr=veh_patch,
                veh_valid_mask=veh_mask,
                enable_dual_layer=True,
            )
            del patch, mask
            if veh_patch is not None:
                del veh_patch
            if veh_mask is not None:
                del veh_mask

            st = str(sp.get("initial_status") or "").upper()
            if st not in ("FREE", "OCCUPIED", "BLOCKED"):
                st = fast_obs["status"]
            elif st == "FREE" and fast_obs["status"] == "OCCUPIED" and fast_obs["confidence"] >= 0.86:
                st = "OCCUPIED"
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

            primed_sigs[spot_id] = comp_sig
            resolved_label = (
                str(bound_custom["label"])
                if bound_custom and (bound_custom.get("group_code") or str(bound_custom.get("spot_id") or "").startswith("SP_"))
                else str(sp.get("label") or f"{idx+1:02d}")
            )
            enriched_entry: Dict[str, Any] = {
                "spot_id": spot_id,
                "label": resolved_label,
                "spot_type": spot_type,
                "compact_only": bool(
                    bound_custom["compact_only"]
                    if bound_custom and "compact_only" in bound_custom
                    else sp.get("compact_only", False)
                ),
                "total_meters": round(total_m, 1),
                "capacity": cap,
                "polygon": poly,
                "vehicle_polygon": vpoly,
                "vehicle_poly_custom": False,
                "initial_status": st,
                "status": st,
                "free_units": free_u,
                "occupied_units": occ_u,
                "confidence": round(conf, 3),
                "reason": f"参考图基准：{reason}" if not reason.startswith("参考图") else reason,
                "engine": engine_used,
                "ref_free_sig_b64": ref_free_sig_b64,
            }
            if bound_custom:
                if bound_sid:
                    enriched_entry["reclaim_spot_id"] = bound_sid
                enriched_entry["group_code"] = str(bound_custom.get("group_code") or "")
                enriched_entry["vehicle_model"] = str(bound_custom.get("vehicle_model") or "sedan")
            enriched_spots.append(enriched_entry)

        # Build privacy-desensitized visual backdrop + 320px ORB alignment grayscale map
        tpl_jpeg_data_uri, tpl_gray_b64, plates_redacted = self._build_privacy_safe_template(
            full_img, enriched_spots
        )

        # Zero-Disk privacy guarantee: destroy raw unredacted image immediately
        del full_img

        with self.vision._lock:
            self.vision.privacy_stats["frames_processed_in_ram"] += 1
            self.vision._sync_memory_stats_locked()

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
        derived_anchors = self.db.derive_default_anchor_points(
            [s["polygon"] for s in enriched_spots], w_img, h_img
        )
        if auto_save:
            # Persist spots FIRST so any DB error never leaves crop_roi/template out of sync with spots
            saved_spots = self.db.save_camera_spots(
                camera_id=camera_id,
                zone_id=target_zone_id,
                spots_payload=enriched_spots,
                image_width=w_img,
                image_height=h_img,
            )
            if full_frame_backup_uri:
                self.db.save_camera_full_frame_backup(camera_id, full_frame_backup_uri)
            if crop_updated:
                self.db.save_camera_crop_roi(camera_id, crop_cfg)
            self.db.save_camera_template(
                camera_id=camera_id,
                template_jpeg_b64=tpl_jpeg_data_uri,
                template_gray_b64=tpl_gray_b64,
                template_meta=template_meta,
                image_width=w_img,
                image_height=h_img,
                zone_id=target_zone_id,
            )
            if occ_cnt > 0 and tpl_jpeg_data_uri:
                self.db.save_camera_vehicle_template(
                    camera_id=camera_id,
                    vehicle_template_jpeg_b64=tpl_jpeg_data_uri,
                    vehicle_template_meta={
                        "uploaded_at": time.time(),
                        "engine": "auto_calibrate",
                        "resolution": [w_img, h_img],
                        "plates_redacted": plates_redacted,
                        "elapsed_ms": elapsed_ms,
                    },
                )
            rekeyed_sigs: Dict[str, np.ndarray] = {}
            for s_idx, s_saved in enumerate(saved_spots):
                old_key = enriched_spots[s_idx]["spot_id"] if s_idx < len(enriched_spots) else ""
                sig_arr = primed_sigs.get(s_saved["spot_id"])
                if sig_arr is None and old_key:
                    sig_arr = primed_sigs.get(old_key)
                if sig_arr is not None:
                    rekeyed_sigs[s_saved["spot_id"]] = sig_arr
            if rekeyed_sigs:
                primed_sigs = rekeyed_sigs
            # Synchronize A1~A4 anchor landmarks with the newly calibrated stall outer corners
            # so stale custom anchors from an older framing/crop never misalign subsequent frames!
            preserved_custom_anchors: Optional[List[Dict[str, Any]]] = None
            if cam.get("anchor_points_custom") and len(cam.get("anchor_points") or []) >= 4:
                old_w = max(64.0, float(cam.get("image_width") or raw_w0))
                old_h = max(64.0, float(cam.get("image_height") or raw_h0))
                old_raw_aspect = float(prev_crop_cfg.get("raw_aspect") or (old_w / old_h))
                new_raw_aspect = float(raw_w0) / max(1.0, float(raw_h0))
                if max(old_raw_aspect / max(0.05, new_raw_aspect), new_raw_aspect / max(0.05, old_raw_aspect)) <= 1.15:
                    orx = float(prev_crop_cfg.get("x", 0.0)) if had_crop_enabled else 0.0
                    ory = float(prev_crop_cfg.get("y", 0.0)) if had_crop_enabled else 0.0
                    orw = float(prev_crop_cfg.get("w", 1.0)) if had_crop_enabled else 1.0
                    orh = float(prev_crop_cfg.get("h", 1.0)) if had_crop_enabled else 1.0
                    mapped_pts: List[Dict[str, Any]] = []
                    diag_tol = 0.10 * float(np.hypot(w_img, h_img))
                    ok_all = True
                    for idx_a, apt in enumerate((cam.get("anchor_points") or [])[:4]):
                        raw_ax = (orx + (float(apt.get("x", 0.0)) / old_w) * orw) * raw_w0
                        raw_ay = (ory + (float(apt.get("y", 0.0)) / old_h) * orh) * raw_h0
                        nax = raw_ax - crop_offset_x
                        nay = raw_ay - crop_offset_y
                        if not (0.0 <= nax <= w_img - 1.0 and 0.0 <= nay <= h_img - 1.0):
                            ok_all = False
                            break
                        if idx_a < len(derived_anchors):
                            da = derived_anchors[idx_a]
                            if float(np.hypot(nax - da["x"], nay - da["y"])) > diag_tol:
                                ok_all = False
                                break
                        mapped_pts.append({**apt, "x": round(nax, 1), "y": round(nay, 1)})
                    if ok_all and len(mapped_pts) >= 4:
                        preserved_custom_anchors = mapped_pts
            self.db.save_camera_anchor_points(camera_id, preserved_custom_anchors)
            self.vision.clear_frame_cache(camera_id)
            self.vision.prime_reference_signatures(camera_id, primed_sigs)

        updated_cam_after = self.db.get_camera(camera_id, include_blobs=False) or {}
        return {
            "ok": True,
            "camera_id": camera_id,
            "zone_id": target_zone_id,
            "auto_saved": auto_save,
            "image_width": w_img,
            "image_height": h_img,
            "engine_used": engine_used,
            "vlm_fallback_error": vlm_fallback_note or None,
            "scene_summary": scene_summary,
            "building_hint": building_hint,
            "plates_redacted": plates_redacted,
            "elapsed_ms": elapsed_ms,
            "template_meta": template_meta,
            "template_jpeg_b64": tpl_jpeg_data_uri,
            "crop_roi": updated_cam_after.get("crop_roi") if auto_save else crop_cfg,
            "anchor_points": updated_cam_after.get("anchor_points") if auto_save else derived_anchors,
            "anchor_points_custom": bool(updated_cam_after.get("anchor_points_custom")) if auto_save else False,
            "camera": updated_cam_after if auto_save else None,
            "spots": saved_spots,
        }

    def refine_spots_against_image(
        self,
        camera_id: str,
        raw_image_bytes: Optional[bytes] = None,
        spots_override: Optional[List[Dict[str, Any]]] = None,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        """
        Sub-pixel white-line snapping (<35ms CPU, zero VLM calls) for existing or draft
        stall polygons against either an uploaded image or the camera's saved template.
        Also refreshes empty-stall baseline signatures when stalls are FREE.
        """
        t0 = time.perf_counter()
        cam_full = self.db.get_camera(camera_id, include_blobs=True)
        if not cam_full:
            raise ValueError(f"未找到相机机位: {camera_id}")

        cal_w = int(cam_full.get("image_width") or 1280)
        cal_h = int(cam_full.get("image_height") or 720)

        with self.vision.decode_guard():
            src_spots = (
                [dict(s) for s in spots_override]
                if spots_override is not None
                else self.db.list_spots(camera_id=camera_id, include_signatures=True)
            )
            if not src_spots:
                raise ValueError("当前机位尚无车位框可贴合")

            if raw_image_bytes:
                arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                del arr
                if img is not None:
                    img, _ = self.vision.align_frame_to_calibrated_canvas(
                        camera_id, img, spots_override=src_spots
                    )
            elif cam_full.get("template_jpeg_b64"):
                b64 = cam_full["template_jpeg_b64"]
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                raise ValueError("当前机位尚无参考底图，请先上传底图或参考图")

            if img is None:
                raise ValueError("底图解码失败")
            if img.shape[1] != cal_w or img.shape[0] != cal_h:
                img = cv2.resize(img, (cal_w, cal_h), interpolation=cv2.INTER_LINEAR)

            refined = self._refine_polygons_with_opencv_lines(img, src_spots)
            primed_sigs: Dict[str, np.ndarray] = {}
            for sp in refined:
                patch, _ = extract_spot_patch(img, sp["polygon"], spot_type=sp.get("spot_type", "standard"))
                small_gray = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (32, 32))
                if sp.get("status", "FREE") == "FREE" and sp.get("spot_type", "standard") != "elastic_strip":
                    sp["ref_free_sig_b64"] = base64.b64encode(small_gray.tobytes()).decode("ascii")
                if sp.get("spot_id"):
                    primed_sigs[sp["spot_id"]] = small_gray
                del patch
            del img

        saved = refined
        if auto_save:
            saved = self.db.save_camera_spots(
                camera_id=camera_id,
                zone_id=cam_full["zone_id"],
                spots_payload=refined,
                image_width=cal_w,
                image_height=cal_h,
                calibration_layer="ground",
            )
            self.vision.clear_frame_cache(camera_id)
            self.vision.prime_reference_signatures(camera_id, primed_sigs)

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "ok": True,
            "camera_id": camera_id,
            "elapsed_ms": elapsed_ms,
            "spots": saved,
        }

    def save_ground_reference_from_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        zone_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save a dedicated empty-ground reference frame for Layer-1 (ground polygon)
        calibration WITHOUT altering the parked-vehicle template or custom vehicle polygons.
        """
        t0 = time.perf_counter()
        cam_full = self.db.get_camera(camera_id, include_blobs=False)
        if not cam_full:
            raise ValueError(f"未找到相机机位: {camera_id}")

        with self.vision.decode_guard():
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if full_img is None:
                raise ValueError("无法解码上传的无车地面参考图")
            h_raw, w_raw = full_img.shape[:2]
            if max(h_raw, w_raw) > 2560:
                sc = 2560.0 / float(max(h_raw, w_raw))
                resized = cv2.resize(
                    full_img,
                    (max(64, int(round(w_raw * sc))), max(64, int(round(h_raw * sc)))),
                    interpolation=cv2.INTER_AREA,
                )
                del full_img
                full_img = resized

            raw_h0, raw_w0 = full_img.shape[:2]
            existing_w = int(cam_full.get("image_width") or 0)
            existing_h = int(cam_full.get("image_height") or 0)
            has_existing_spots = int(cam_full.get("calibrated_spot_count") or 0) > 0
            crop_cfg = dict(cam_full.get("crop_roi") or {})
            full_frame_backup_uri: Optional[str] = None
            save_crop_meta = False
            align_meta: Dict[str, Any] = {}

            if has_existing_spots and existing_w > 0 and existing_h > 0:
                aligned_img, crop_info = self.vision.align_frame_to_calibrated_canvas(camera_id, full_img)
                align_meta = dict(crop_info or {})
                if not crop_info.get("dynamic_adjusted") and (
                    crop_info.get("applied") or not crop_cfg.get("enabled")
                ):
                    full_frame_backup_uri, _, _ = self._build_privacy_safe_template(full_img, [])
                del full_img
                full_img = aligned_img
                cur_h, cur_w = full_img.shape[:2]
                if (cur_w, cur_h) != (existing_w, existing_h):
                    resized_match = cv2.resize(full_img, (existing_w, existing_h), interpolation=cv2.INTER_AREA)
                    del full_img
                    full_img = resized_match
            else:
                cropped_img, crop_info = apply_camera_crop_roi(
                    full_img,
                    crop_cfg,
                    cal_w=cam_full.get("image_width"),
                    cal_h=cam_full.get("image_height"),
                )
                align_meta = dict(crop_info or {})
                if crop_info.get("applied") or not crop_cfg.get("enabled"):
                    full_frame_backup_uri, _, _ = self._build_privacy_safe_template(full_img, [])
                if crop_info.get("applied"):
                    del full_img
                    full_img = cropped_img
                    crop_cfg["raw_width"] = raw_w0
                    crop_cfg["raw_height"] = raw_h0
                    crop_cfg["raw_size"] = [raw_w0, raw_h0]
                    crop_cfg["raw_aspect"] = round(float(raw_w0) / max(1.0, float(raw_h0)), 5)
                    save_crop_meta = True
                if max(full_img.shape[:2]) > 1600:
                    cur_h, cur_w = full_img.shape[:2]
                    sc2 = 1600.0 / float(max(cur_h, cur_w))
                    resized2 = cv2.resize(
                        full_img,
                        (max(64, int(round(cur_w * sc2))), max(64, int(round(cur_h * sc2)))),
                        interpolation=cv2.INTER_AREA,
                    )
                    del full_img
                    full_img = resized2

        h_img, w_img = full_img.shape[:2]
        spots = self.db.list_spots(camera_id=camera_id)
        tpl_jpeg_uri, tpl_gray_b64, plates_redacted = self._build_privacy_safe_template(full_img, spots)
        del full_img

        if full_frame_backup_uri:
            self.db.save_camera_full_frame_backup(camera_id, full_frame_backup_uri)
        if save_crop_meta:
            self.db.save_camera_crop_roi(camera_id, crop_cfg)

        with self.vision._lock:
            self.vision.privacy_stats["frames_processed_in_ram"] += 1
            self.vision._sync_memory_stats_locked()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        tpl_meta = {
            "calibrated_at": time.time(),
            "engine": "manual_ground_workspace",
            "spots_count": len(spots),
            "plates_redacted": plates_redacted,
            "resolution": [w_img, h_img],
            "elapsed_ms": elapsed_ms,
        }
        updated_cam = self.db.save_camera_template(
            camera_id=camera_id,
            template_jpeg_b64=tpl_jpeg_uri,
            template_gray_b64=tpl_gray_b64,
            template_meta=tpl_meta,
            image_width=w_img,
            image_height=h_img,
            zone_id=zone_id or cam_full.get("zone_id"),
        )
        return {
            "ok": True,
            "camera_id": camera_id,
            "has_template": True,
            "image_width": w_img,
            "image_height": h_img,
            "plates_redacted": plates_redacted,
            "elapsed_ms": elapsed_ms,
            "template_meta": tpl_meta,
            "template_jpeg_b64": tpl_jpeg_uri,
            "alignment": align_meta,
            "camera": updated_cam,
        }

    def save_vehicle_reference_from_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
    ) -> Dict[str, Any]:
        """
        Save a dedicated parked-vehicle reference frame for Layer-2 (3D vehicle envelope)
        calibration WITHOUT altering the empty-ground template or ground polygons.
        """
        t0 = time.perf_counter()
        cam_full = self.db.get_camera(camera_id, include_blobs=False)
        if not cam_full:
            raise ValueError(f"未找到相机机位: {camera_id}")

        with self.vision.decode_guard():
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if full_img is None:
                raise ValueError("无法解码上传的有车停入参考图")
            h_raw, w_raw = full_img.shape[:2]
            if max(h_raw, w_raw) > 2560:
                sc0 = 2560.0 / float(max(h_raw, w_raw))
                resized0 = cv2.resize(
                    full_img,
                    (max(64, int(round(w_raw * sc0))), max(64, int(round(h_raw * sc0)))),
                    interpolation=cv2.INTER_AREA,
                )
                del full_img
                full_img = resized0

            align_meta: Dict[str, Any] = {}
            if cam_full.get("has_template") and cam_full.get("image_width") and cam_full.get("image_height"):
                aligned_veh, v_align_info = self.vision.align_frame_to_calibrated_canvas(camera_id, full_img)
                align_meta = dict(v_align_info or {})
                del full_img
                full_img = aligned_veh
                target_w = int(cam_full["image_width"])
                target_h = int(cam_full["image_height"])
                h_raw, w_raw = full_img.shape[:2]
                if (w_raw, h_raw) != (target_w, target_h):
                    resized = cv2.resize(full_img, (target_w, target_h), interpolation=cv2.INTER_AREA)
                    del full_img
                    full_img = resized
            else:
                full_img, v_crop_info = apply_camera_crop_roi(
                    full_img,
                    cam_full.get("crop_roi"),
                    cal_w=cam_full.get("image_width"),
                    cal_h=cam_full.get("image_height"),
                )
                align_meta = dict(v_crop_info or {})
                h_raw, w_raw = full_img.shape[:2]
                if max(h_raw, w_raw) > 1600:
                    sc = 1600.0 / float(max(h_raw, w_raw))
                    resized = cv2.resize(
                        full_img,
                        (max(64, int(round(w_raw * sc))), max(64, int(round(h_raw * sc)))),
                        interpolation=cv2.INTER_AREA,
                    )
                    del full_img
                    full_img = resized

        h_img, w_img = full_img.shape[:2]
        spots = self.db.list_spots(camera_id=camera_id)
        veh_jpeg_uri, _, plates_redacted = self._build_privacy_safe_template(full_img, spots)
        del full_img

        with self.vision._lock:
            self.vision.privacy_stats["frames_processed_in_ram"] += 1
            self.vision._sync_memory_stats_locked()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        veh_meta = {
            "uploaded_at": time.time(),
            "resolution": [w_img, h_img],
            "plates_redacted": plates_redacted,
            "elapsed_ms": elapsed_ms,
        }
        if not cam_full.get("has_template"):
            self.db.update_camera(camera_id, {"image_width": w_img, "image_height": h_img})
        updated_cam = self.db.save_camera_vehicle_template(
            camera_id=camera_id,
            vehicle_template_jpeg_b64=veh_jpeg_uri,
            vehicle_template_meta=veh_meta,
        )
        return {
            "ok": True,
            "camera_id": camera_id,
            "has_vehicle_template": True,
            "image_width": w_img,
            "image_height": h_img,
            "plates_redacted": plates_redacted,
            "elapsed_ms": elapsed_ms,
            "vehicle_template_meta": veh_meta,
            "vehicle_template_jpeg_b64": veh_jpeg_uri,
            "alignment": align_meta,
            "camera": updated_cam,
        }

    # -------------------------------------------------------------------------
    # Per-Camera Parking-Strip ROI Cropping (Lossless Polygon Reprojection)
    # -------------------------------------------------------------------------
    @staticmethod
    def _decode_data_uri_bgr(data_uri_or_b64: str) -> Optional[np.ndarray]:
        if not data_uri_or_b64:
            return None
        b64 = data_uri_or_b64.split(",", 1)[1] if "," in data_uri_or_b64 else data_uri_or_b64
        try:
            raw = base64.b64decode(b64)
            return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

    def suggest_camera_crop_roi(
        self,
        camera_id: str,
        raw_image_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Automatically recommends a normalized [0..1] parking-area crop ROI on the
        uncropped camera frame so tall building/sky/tree regions are excluded while
        keeping comfortable context around every stall and 3D vehicle roof envelope.
        """
        cam_full = self.db.get_camera(camera_id, include_blobs=True)
        if not cam_full:
            raise ValueError(f"未找到相机机位: {camera_id}")

        spots = self.db.list_spots(camera_id=camera_id)
        old_roi = dict(cam_full.get("crop_roi") or {})
        cal_w = max(64.0, float(cam_full.get("image_width") or 1280))
        cal_h = max(64.0, float(cam_full.get("image_height") or 720))

        if spots and not raw_image_bytes:
            if old_roi.get("enabled"):
                ox = float(old_roi.get("x", 0.0))
                oy = float(old_roi.get("y", 0.0))
                ow = float(old_roi.get("w", 1.0))
                oh = float(old_roi.get("h", 1.0))
            else:
                ox, oy, ow, oh = 0.0, 0.0, 1.0, 1.0

            rxs: List[float] = []
            rys: List[float] = []
            for sp in spots:
                for poly_key in ("polygon", "vehicle_polygon"):
                    pts = sp.get(poly_key) or []
                    for pt in pts:
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            rxs.append(ox + (float(pt[0]) / cal_w) * ow)
                            rys.append(oy + (float(pt[1]) / cal_h) * oh)
            if rxs and rys:
                x0 = max(0.0, min(rxs) - 0.055)
                x1 = min(1.0, max(rxs) + 0.055)
                y0 = max(0.0, min(rys) - 0.115)
                y1 = min(1.0, max(rys) + 0.085)
                if (x1 - x0) < 0.24:
                    cx = 0.5 * (x0 + x1)
                    x0 = max(0.0, cx - 0.12)
                    x1 = min(1.0, x0 + 0.24)
                if (y1 - y0) < 0.20:
                    cy = 0.5 * (y0 + y1)
                    y0 = max(0.0, cy - 0.10)
                    y1 = min(1.0, y0 + 0.20)
                sug_box = {
                    "enabled": True,
                    "x": round(x0, 4),
                    "y": round(y0, 4),
                    "w": round(x1 - x0, 4),
                    "h": round(y1 - y0, 4),
                }
                return {
                    "ok": True,
                    "camera_id": camera_id,
                    "source": "calibrated_spots_bbox",
                    "crop_roi": sug_box,
                    "suggested_crop_roi": sug_box,
                }

        # Fallback: visual parking-band detector on full-frame or uploaded image
        img: Optional[np.ndarray] = None
        with self.vision.decode_guard():
            if raw_image_bytes:
                img = cv2.imdecode(np.frombuffer(raw_image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None and cam_full.get("full_frame_jpeg_b64"):
                img = self._decode_data_uri_bgr(cam_full["full_frame_jpeg_b64"])
            if img is None and cam_full.get("template_jpeg_b64"):
                img = self._decode_data_uri_bgr(cam_full["template_jpeg_b64"])

        if img is None:
            def_box = {"enabled": True, "x": 0.02, "y": 0.34, "w": 0.96, "h": 0.48}
            return {
                "ok": True,
                "camera_id": camera_id,
                "source": "default_band",
                "crop_roi": def_box,
                "suggested_crop_roi": def_box,
            }

        ih, iw = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        tophat = cv2.morphologyEx(
            gray,
            cv2.MORPH_TOPHAT,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, iw // 42), max(9, iw // 42))),
        )
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        edge_mag = 0.55 * gx + 0.45 * gy
        low_sat_white = np.where(hsv[:, :, 1] < 85, tophat.astype(np.float32), 0.0)
        energy_map = 0.52 * low_sat_white + 0.48 * np.minimum(edge_mag, 120.0)
        del img, gray, hsv, tophat, gx, gy, edge_mag, low_sat_white

        r_start = int(ih * 0.14)
        r_end = max(r_start + 16, int(ih * 0.94))
        row_prof = np.mean(energy_map[r_start:r_end, :], axis=1)
        win_h = max(12, int(round((r_end - r_start) * 0.38)))
        if len(row_prof) > win_h:
            csum = np.concatenate([[0.0], np.cumsum(row_prof)])
            sums = csum[win_h:] - csum[:-win_h]
            best_i = int(np.argmax(sums))
            y0_px = r_start + best_i
            y1_px = min(ih, y0_px + win_h)
        else:
            y0_px, y1_px = r_start, r_end

        y0 = max(0.0, float(y0_px) / ih - 0.06)
        y1 = min(1.0, float(y1_px) / ih + 0.06)
        col_prof = np.mean(energy_map[int(y0 * ih) : max(int(y0 * ih) + 4, int(y1 * ih)), :], axis=0)
        thr_c = float(np.percentile(col_prof, 35))
        active_cols = np.where(col_prof >= thr_c)[0]
        if len(active_cols) >= 8:
            x0 = max(0.0, float(active_cols[0]) / iw - 0.04)
            x1 = min(1.0, float(active_cols[-1]) / iw + 0.04)
        else:
            x0, x1 = 0.02, 0.98
        if (x1 - x0) < 0.35:
            x0, x1 = 0.02, 0.98
        del energy_map

        vis_box = {
            "enabled": True,
            "x": round(x0, 4),
            "y": round(y0, 4),
            "w": round(x1 - x0, 4),
            "h": round(y1 - y0, 4),
        }
        return {
            "ok": True,
            "camera_id": camera_id,
            "source": "visual_band_detector",
            "crop_roi": vis_box,
            "suggested_crop_roi": vis_box,
        }

    def configure_camera_crop_roi(
        self,
        camera_id: str,
        crop_roi_payload: Dict[str, Any],
        raw_image_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Applies, adjusts, or resets (`enabled=False`) a camera's persistent parking-area
        ROI crop (`crop_roi`), losslessly reprojecting all calibrated ground & 3D vehicle
        polygons and regenerating the cropped alignment & display templates so snapshots
        and calibration share the exact same coordinate frame.
        """
        t0 = time.perf_counter()
        cam_full = self.db.get_camera(camera_id, include_blobs=True)
        if not cam_full:
            raise ValueError(f"未找到相机机位: {camera_id}")

        old_roi = dict(cam_full.get("crop_roi") or {})
        old_enabled = bool(old_roi.get("enabled"))
        old_cal_w = max(64, int(cam_full.get("image_width") or 1280))
        old_cal_h = max(64, int(cam_full.get("image_height") or 720))

        enabled = bool(crop_roi_payload.get("enabled", True))
        nx = float(np.clip(float(crop_roi_payload.get("x", 0.0)), 0.0, 0.92))
        ny = float(np.clip(float(crop_roi_payload.get("y", 0.0)), 0.0, 0.92))
        nw = float(np.clip(float(crop_roi_payload.get("w", 1.0 - nx)), 0.08, 1.0 - nx))
        nh = float(np.clip(float(crop_roi_payload.get("h", 1.0 - ny)), 0.08, 1.0 - ny))
        if nx <= 0.002 and ny <= 0.002 and nw >= 0.996 and nh >= 0.996:
            enabled = False

        # Resolve uncropped full-frame image & canonical uncropped dimensions (raw_w, raw_h)
        raw_full_img: Optional[np.ndarray] = None
        raw_w, raw_h = old_cal_w, old_cal_h
        if isinstance(old_roi.get("raw_size"), (list, tuple)) and len(old_roi["raw_size"]) == 2:
            try:
                raw_w = max(64, int(old_roi["raw_size"][0]))
                raw_h = max(64, int(old_roi["raw_size"][1]))
            except Exception:
                pass

        with self.vision.decode_guard():
            if raw_image_bytes:
                arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
                decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                del arr
                if decoded is not None:
                    dh, dw = decoded.shape[:2]
                    if max(dh, dw) > 2560:
                        sc = 2560.0 / float(max(dh, dw))
                        decoded = cv2.resize(
                            decoded,
                            (max(64, int(round(dw * sc))), max(64, int(round(dh * sc)))),
                            interpolation=cv2.INTER_AREA,
                        )
                    raw_full_img = decoded
                    raw_h, raw_w = raw_full_img.shape[:2]
                    backup_uri, _, _ = self._build_privacy_safe_template(raw_full_img, [])
                    if backup_uri:
                        self.db.save_camera_full_frame_backup(camera_id, backup_uri)

            if raw_full_img is None and cam_full.get("full_frame_jpeg_b64"):
                decoded_ff = self._decode_data_uri_bgr(cam_full["full_frame_jpeg_b64"])
                if decoded_ff is not None:
                    if old_enabled and not isinstance(old_roi.get("raw_size"), (list, tuple)):
                        raw_h, raw_w = decoded_ff.shape[:2]
                    if (decoded_ff.shape[1], decoded_ff.shape[0]) != (raw_w, raw_h):
                        decoded_ff = cv2.resize(decoded_ff, (raw_w, raw_h), interpolation=cv2.INTER_LINEAR)
                    raw_full_img = decoded_ff

            if raw_full_img is None and not old_enabled and cam_full.get("template_jpeg_b64"):
                # First time enabling crop on an already-calibrated camera: back up uncropped template!
                self.db.save_camera_full_frame_backup(camera_id, cam_full["template_jpeg_b64"])
                decoded_tpl = self._decode_data_uri_bgr(cam_full["template_jpeg_b64"])
                if decoded_tpl is not None:
                    raw_w, raw_h = old_cal_w, old_cal_h
                    if (decoded_tpl.shape[1], decoded_tpl.shape[0]) != (raw_w, raw_h):
                        decoded_tpl = cv2.resize(decoded_tpl, (raw_w, raw_h), interpolation=cv2.INTER_LINEAR)
                    raw_full_img = decoded_tpl

        new_roi = {
            "enabled": enabled,
            "x": round(nx, 4) if enabled else 0.0,
            "y": round(ny, 4) if enabled else 0.0,
            "w": round(nw, 4) if enabled else 1.0,
            "h": round(nh, 4) if enabled else 1.0,
            "raw_width": raw_w,
            "raw_height": raw_h,
            "raw_size": [raw_w, raw_h],
            "raw_aspect": round(float(raw_w) / max(1.0, float(raw_h)), 5),
            "user_disabled": bool(crop_roi_payload.get("user_disabled", not enabled)),
            "updated_at": time.time(),
        }

        # Camera without any stored image yet: save ROI config for upcoming calibration/snapshots
        if raw_full_img is None:
            updated_cam = self.db.save_camera_crop_roi(camera_id, new_roi)
            return {
                "ok": True,
                "camera_id": camera_id,
                "crop_roi": new_roi,
                "image_width": old_cal_w,
                "image_height": old_cal_h,
                "spots": self.db.list_spots(camera_id=camera_id),
                "camera": updated_cam,
            }

        if enabled:
            x0 = int(np.clip(round(nx * raw_w), 0, raw_w - 32))
            y0 = int(np.clip(round(ny * raw_h), 0, raw_h - 32))
            x1 = int(np.clip(round((nx + nw) * raw_w), x0 + 32, raw_w))
            y1 = int(np.clip(round((ny + nh) * raw_h), y0 + 32, raw_h))
            eff_nx, eff_ny = x0 / float(raw_w), y0 / float(raw_h)
            eff_nw, eff_nh = (x1 - x0) / float(raw_w), (y1 - y0) / float(raw_h)
            new_roi["x"] = round(eff_nx, 5)
            new_roi["y"] = round(eff_ny, 5)
            new_roi["w"] = round(eff_nw, 5)
            new_roi["h"] = round(eff_nh, 5)
            active_img = raw_full_img[y0:y1, x0:x1].copy()
        else:
            eff_nx, eff_ny, eff_nw, eff_nh = 0.0, 0.0, 1.0, 1.0
            active_img = raw_full_img.copy()
        del raw_full_img

        new_cal_h, new_cal_w = active_img.shape[:2]

        # Reproject existing ground & 3D vehicle polygons losslessly via normalized raw space
        if old_enabled:
            ox = float(old_roi.get("x", 0.0))
            oy = float(old_roi.get("y", 0.0))
            ow = max(0.05, float(old_roi.get("w", 1.0)))
            oh = max(0.05, float(old_roi.get("h", 1.0)))
        else:
            ox, oy, ow, oh = 0.0, 0.0, 1.0, 1.0

        def map_pt(pt: List[float]) -> List[float]:
            rx_norm = ox + (float(pt[0]) / float(old_cal_w)) * ow
            ry_norm = oy + (float(pt[1]) / float(old_cal_h)) * oh
            px_new = ((rx_norm - eff_nx) / max(1e-6, eff_nw)) * float(new_cal_w)
            py_new = ((ry_norm - eff_ny) / max(1e-6, eff_nh)) * float(new_cal_h)
            return [
                round(float(np.clip(px_new, 0.0, new_cal_w - 1.0)), 1),
                round(float(np.clip(py_new, 0.0, new_cal_h - 1.0)), 1),
            ]

        src_spots = self.db.list_spots(camera_id=camera_id, include_signatures=True)
        reprojected_spots: List[Dict[str, Any]] = []
        primed_sigs: Dict[str, np.ndarray] = {}

        for sp in src_spots:
            sp_copy = dict(sp)
            poly_new = [map_pt(p) for p in (sp.get("polygon") or []) if len(p) >= 2]
            sp_copy["polygon"] = poly_new
            vpoly_old = sp.get("vehicle_polygon") or []
            if len(vpoly_old) >= 3:
                sp_copy["vehicle_polygon"] = [map_pt(p) for p in vpoly_old if len(p) >= 2]
            elif sp_copy.get("spot_type") != "elastic_strip" and len(poly_new) >= 3:
                sp_copy["vehicle_polygon"] = derive_vehicle_envelope_polygon(poly_new, new_cal_w, new_cal_h)

            if len(poly_new) >= 3:
                patch, _ = extract_spot_patch(active_img, poly_new, spot_type=sp_copy.get("spot_type", "standard"))
                small_gray = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (32, 32))
                if sp_copy.get("status", "FREE") == "FREE" and sp_copy.get("spot_type", "standard") != "elastic_strip":
                    sp_copy["ref_free_sig_b64"] = base64.b64encode(small_gray.tobytes()).decode("ascii")
                if sp_copy.get("spot_id"):
                    primed_sigs[sp_copy["spot_id"]] = small_gray
                del patch

            reprojected_spots.append(sp_copy)

        tpl_jpeg_uri, tpl_gray_b64, plates_redacted = self._build_privacy_safe_template(
            active_img, reprojected_spots
        )
        del active_img

        # Also crop/restore parked-vehicle template if present
        if cam_full.get("vehicle_template_jpeg_b64") and old_enabled != enabled:
            veh_img = self._decode_data_uri_bgr(cam_full["vehicle_template_jpeg_b64"])
            if veh_img is not None:
                if not old_enabled and enabled:
                    vh0, vw0 = veh_img.shape[:2]
                    vx0 = int(np.clip(round(eff_nx * vw0), 0, max(1, vw0 - 16)))
                    vy0 = int(np.clip(round(eff_ny * vh0), 0, max(1, vh0 - 16)))
                    vx1 = int(np.clip(round((eff_nx + eff_nw) * vw0), vx0 + 16, vw0))
                    vy1 = int(np.clip(round((eff_ny + eff_nh) * vh0), vy0 + 16, vh0))
                    veh_crop = veh_img[vy0:vy1, vx0:vx1].copy()
                    veh_uri, _, _ = self._build_privacy_safe_template(veh_crop, reprojected_spots)
                    if veh_uri:
                        self.db.save_camera_vehicle_template(
                            camera_id=camera_id,
                            vehicle_template_jpeg_b64=veh_uri,
                            vehicle_template_meta={
                                **(cam_full.get("vehicle_template_meta") or {}),
                                "resolution": [new_cal_w, new_cal_h],
                                "cropped_roi": enabled,
                            },
                        )
                    del veh_crop
                del veh_img

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        prev_meta = dict(cam_full.get("template_meta") or {})
        prev_meta.update(
            {
                "resolution": [new_cal_w, new_cal_h],
                "crop_roi_enabled": enabled,
                "spots_count": len(reprojected_spots),
                "plates_redacted": plates_redacted,
                "roi_updated_at": time.time(),
            }
        )
        self.db.save_camera_crop_roi(camera_id, new_roi)
        updated_cam = self.db.save_camera_template(
            camera_id=camera_id,
            template_jpeg_b64=tpl_jpeg_uri,
            template_gray_b64=tpl_gray_b64,
            template_meta=prev_meta,
            image_width=new_cal_w,
            image_height=new_cal_h,
            zone_id=cam_full.get("zone_id"),
        )
        saved_spots = reprojected_spots
        if reprojected_spots:
            saved_spots = self.db.save_camera_spots(
                camera_id=camera_id,
                zone_id=cam_full["zone_id"],
                spots_payload=reprojected_spots,
                image_width=new_cal_w,
                image_height=new_cal_h,
                calibration_layer="both",
            )
        if cam_full.get("anchor_points_custom") and cam_full.get("anchor_points"):
            reproj_anchors = []
            for a_item in cam_full["anchor_points"]:
                mx, my = map_pt([float(a_item["x"]), float(a_item["y"])])
                reproj_anchors.append({**a_item, "x": mx, "y": my})
            updated_cam = self.db.save_camera_anchor_points(camera_id, reproj_anchors) or updated_cam
        else:
            updated_cam = self.db.get_camera(camera_id, include_blobs=False) or updated_cam
        self.vision.clear_frame_cache(camera_id)
        if primed_sigs:
            self.vision.prime_reference_signatures(camera_id, primed_sigs)

        return {
            "ok": True,
            "camera_id": camera_id,
            "crop_roi": new_roi,
            "image_width": new_cal_w,
            "image_height": new_cal_h,
            "template_jpeg_b64": tpl_jpeg_uri,
            "template_meta": prev_meta,
            "spots": saved_spots,
            "elapsed_ms": elapsed_ms,
            "camera": updated_cam,
        }

    # -------------------------------------------------------------------------
    # Stage 1: Coordinate-Ruler Annotated VLM Spatial Calibration (2-Pass Zoom)
    # -------------------------------------------------------------------------
    @staticmethod
    def _draw_coordinate_ruler(img_bgr: np.ndarray, max_edge: float = 1360.0) -> np.ndarray:
        """
        Draws a zero-padding 0..1000 normalized coordinate ruler & subtle 50/100-unit
        grid directly inside the image edges so VLM normalized coordinates align 1:1.
        """
        h0, w0 = img_bgr.shape[:2]
        scale = min(1.0, float(max_edge) / max(h0, w0))
        if scale < 1.0:
            canvas = cv2.resize(img_bgr, (int(round(w0 * scale)), int(round(h0 * scale))))
        else:
            canvas = img_bgr.copy()

        h, w = canvas.shape[:2]
        overlay = canvas.copy()

        # Subtle 50-unit & 100-unit grid lines for sub-stall precision
        for k in range(50, 1000, 50):
            gx = int(round(k / 1000.0 * (w - 1)))
            gy = int(round(k / 1000.0 * (h - 1)))
            col = (0, 255, 255) if k % 100 == 0 else (180, 220, 120)
            cv2.line(overlay, (gx, 0), (gx, h - 1), col, 1, cv2.LINE_AA)
            cv2.line(overlay, (0, gy), (w - 1, gy), col, 1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.20, canvas, 0.80, 0, canvas)

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

    @staticmethod
    def _infer_camera_layout_hint(
        cam: Optional[Dict[str, Any]],
        existing_spots: Optional[List[Dict[str, Any]]],
    ) -> Tuple[str, int, List[str]]:
        """
        Extracts deduplicated (orientation, expected_count, group_codes) from camera
        group metadata (e.g. `10BH2` -> ('H', 2, ['10BH2']), `5LV7` -> ('V', 7, ['5LV7']))
        with fallback to existing custom/calibrated spots.
        """
        orientation = ""
        expected_count = 0
        group_codes: List[str] = []

        if isinstance(cam, dict):
            candidates = [
                str(cam.get("group_code") or "").strip(),
                re.sub(r"^CAM[_\-]+", "", str(cam.get("camera_id") or "").strip(), flags=re.IGNORECASE),
                str(cam.get("zone_id") or "").strip(),
                str(cam.get("name") or "").strip(),
            ]
            unique_groups: List[Dict[str, Any]] = []
            for cand in candidates:
                if not cand:
                    continue
                parsed_res = parse_camera_group_codes(cand)
                raw_groups = parsed_res.get("groups") or [] if isinstance(parsed_res, dict) else []
                seen_codes = set()
                deduped: List[Dict[str, Any]] = []
                for g in raw_groups:
                    gc = str(g.get("code") or "").strip().upper()
                    if gc and gc not in seen_codes:
                        seen_codes.add(gc)
                        deduped.append(g)
                if deduped:
                    unique_groups = deduped
                    group_codes = [str(g["code"]).upper() for g in deduped]
                    break

            if unique_groups:
                cnt_sum = sum(max(0, int(g.get("count") or 0)) for g in unique_groups)
                if cnt_sum >= 1:
                    expected_count = min(24, cnt_sum)
                h_votes = sum(
                    1
                    for g in unique_groups
                    if str(g.get("orient_code") or g.get("orientation") or "").upper() == "H"
                )
                v_votes = sum(
                    1
                    for g in unique_groups
                    if str(g.get("orient_code") or g.get("orientation") or "").upper() == "V"
                )
                if h_votes > v_votes:
                    orientation = "H"
                elif v_votes > 0:
                    orientation = "V"

        if expected_count < 1 and existing_spots:
            custom_cnt = sum(1 for s in existing_spots if s.get("map_custom"))
            cal_cnt = sum(
                1
                for s in existing_spots
                if str(s.get("last_engine") or "") not in ("", "map_editor")
            )
            if custom_cnt >= 1:
                expected_count = custom_cnt
            elif cal_cnt >= 1:
                expected_count = cal_cnt
            elif len(existing_spots) >= 1:
                expected_count = len(existing_spots)

        return orientation, expected_count, group_codes

    def _calibrate_via_vlm(
        self,
        full_img: np.ndarray,
        vlm_cfg: Dict[str, Any],
        cam: Optional[Dict[str, Any]] = None,
        existing_spots: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
        ruler_img = self._draw_coordinate_ruler(full_img)
        ok, buf = cv2.imencode(".jpg", ruler_img, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        if not ok:
            return [], {}, "参考图JPEG编码失败"
        b64_jpg = base64.b64encode(buf.tobytes()).decode("ascii")

        with self.vision._lock:
            self.vision.privacy_stats["slow_lane_vlm_calls"] += 1

        orient_hint, count_hint, group_codes = self._infer_camera_layout_hint(cam, existing_spots)
        prior_block = ""
        if orient_hint == "H":
            cnt_txt = f"参考组号 {'/'.join(group_codes)} 提示约 {count_hint} 个车位（若画面真实可见车位数明确不同则以实景为准）" if count_hint >= 1 else "车位沿路牙首尾相连顺停排布"
            prior_block = (
                f"【当前机位拓扑先验（重要）】：{cnt_txt}，且属于 **H 型路侧水平/顺停/异形透视车位**！"
                "每个车位的长边（约5.2m）顺着道路/路缘石延伸，短边（约2.2~2.5m）从后方绿化路牙探向沥青车道；"
                "切勿把植草砖小方格（约0.4m）误拆成一排细窄竖向车位！\n\n"
            )
        elif orient_hint == "V" and count_hint >= 1:
            prior_block = (
                f"【当前机位拓扑先验】：机位组号 {'/'.join(group_codes)} 对应并排垂直/斜列车位（参考规模约 {count_hint} 个车位，若实景清晰可见真实车位边界则以实景为准）。"
                "注意区分约2.5m×5.2m的完整汽车泊位与0.4m植草砖网格缝。\n\n"
            )

        prompt = (
            "你是高精度小区实景停车场空间视觉标定引擎。这张相机基准参考图四周与网格标有 0..1000 归一化坐标尺（X从左0到右1000，Y从上0到下1000）。\n"
            "请自动识别画面中【全部地面停车位】（支持高层窗台大俯角实拍、弧形弯道车位、红砖/植草砖无白线车位、路侧水平顺停异形位、直线白线车位及无划线顺停带），输出用于长期复用的空间参考模板。\n"
            + prior_block
            + "先判断车位排布几何类型 `row_geometry`：\n"
            "- 若为平直成排且地面有清晰白色车位分隔线，填 \"straight_lined\"；\n"
            "- 若沿弧形/弯道道路排布、扇形转角排布、路侧顺停异形梯形排布，或为红砖/植草砖/标线磨损地面，填 \"curved_or_unlined\"。\n\n"
            "核心空间定位规则（务必严格执行）：\n"
            "1. 按从左到右（或沿道路从近到远）顺序输出所有真实车位；若地面印有编号（01..07）则直接采用，否则命名为 01, 02...。\n"
            "2. 标准车位 `spot_type` 填 \"standard\"（不规则转角/异形植草砖泊位可填 \"standard\" 或 \"irregular\"），`polygon_1000` 按顺时针给出地面车位底面的 4 个角点：`[[后左x,后左y], [后右x,后右y], [前右x,前右y], [前左x,前左y]]`（靠绿化灌木/路牙/限位器一侧为后端，靠沥青行车道一侧为前端）。\n"
            "3. 【无车空地、异形植草砖与弯道/高层俯拍高精度铁律】：\n"
            "   - 先找出完整的【停车铺装带总轮廓】（后界为路缘石/绿篱根部，前界为铺装砖面与黑色沥青行车道的分界线，左右界为绿化岛/树池/道牙收口），再按单台轿车真实占地尺度（垂直位宽约2.5m×深5.2m；路侧顺停位长约5.2m×宽2.3m）切分泊位，严禁把植草砖/透水砖的细小网格误判为车位！\n"
            "   - 【无车地面（FREE）高精度贴边】：若地面有白色实线/虚线、T型/L型角标或黄色框线，`polygon_1000` 四个角点必须精确落在【白线中心线交点 / 挡车器后侧路牙交点】；若为红砖/植草砖空地，前端边严格贴合【砖面与黑色沥青车道分界线】，后端边严格贴合【后方路缘石/绿化带根部】！\n"
            "   - 当车位沿弧形弯道或斜向路侧排布时，每个车位的后端边与前端边 Y 坐标必须随路牙斜率/弧度自然升降，呈真实透视斜梯形！绝不可强行画成正矩形！\n"
            "   - 高层俯拍已停车辆时，车位框必须紧贴【每辆车轮胎接地的铺装地面底面】，一辆车对应一个车位，绝不可将框体画到绿色灌木丛上，也不可探入前方沥青行车道！\n"
            "4. 连续相邻车位必须严格共用同一条分隔线的两个端点（即第 k 位的 `[后右, 前右]` 等于第 k+1 位的 `[后左, 前左]`），严禁相邻车位框互相重叠交叉！\n"
            "5. 若存在不规则多边形车位填 \"irregular\"；若为无固定分格的超长路侧顺停带填 \"elastic_strip\" 并估算 `total_meters`。\n"
            "6. 判断当前参考图中每个车位的初始状态 `initial_status`：停有汽车填 \"OCCUPIED\"，空车位填 \"FREE\"，锥桶/地锁占道填 \"BLOCKED\"。\n"
            "仅输出严格 JSON（不要输出代码块以外的解释）：\n"
            "{\n"
            '  "scene_summary": "不超过28字中文场景总结",\n'
            '  "building_hint": "画面可见小区或楼栋文字，如 3栋",\n'
            '  "row_geometry": "straight_lined 或 curved_or_unlined",\n'
            '  "spots": [\n'
            "    {\n"
            '      "label": "01",\n'
            '      "spot_type": "standard",\n'
            '      "compact_only": false,\n'
            '      "total_meters": 5.2,\n'
            '      "polygon_1000": [[83,602],[158,605],[122,712],[26,706]],\n'
            '      "initial_status": "OCCUPIED",\n'
            '      "confidence": 0.96,\n'
            '      "reason": "银色轿车停放"\n'
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
                    px_poly = self._normalize_quad_preserve_orientation(px_poly)

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

            row_geom_raw = str(parsed.get("row_geometry") or "auto").strip().lower()
            meta = {
                "scene_summary": str(parsed.get("scene_summary", "")).strip(),
                "building_hint": str(parsed.get("building_hint", "")).strip(),
                "row_geometry": row_geom_raw if row_geom_raw in ("straight_lined", "curved_or_unlined") else "auto",
            }

            # Pass-2 Zoomed Band Refinement for tall/wide photos where stalls occupy < 50% of image height
            all_ys = [pt[1] for sp in converted for pt in sp["polygon"]]
            all_xs = [pt[0] for sp in converted for pt in sp["polygon"]]
            if (
                len(converted) >= 2
                and h_img >= 900
                and all_ys
                and (max(all_ys) - min(all_ys)) < (h_img * 0.50)
            ):
                zoom_spots = self._refine_tall_frame_via_zoom_vlm(
                    full_img, vlm_cfg, converted, meta.get("row_geometry", "auto")
                )
                if zoom_spots and len(zoom_spots) == len(converted):
                    converted = zoom_spots
                    meta["zoom_refined"] = True

            return converted, meta, None
        except Exception as parse_exc:
            return [], {}, f"标定JSON解析异常: {parse_exc}"

    def _refine_tall_frame_via_zoom_vlm(
        self,
        full_img: np.ndarray,
        vlm_cfg: Dict[str, Any],
        pass1_spots: List[Dict[str, Any]],
        row_geometry: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Second-pass high-magnification VLM zoom calibration on the cropped parking band
        for tall window photos (e.g. 1280x1707), boosting sub-stall corner resolution 3x.
        """
        h_img, w_img = full_img.shape[:2]
        xs = [pt[0] for sp in pass1_spots for pt in sp["polygon"]]
        ys = [pt[1] for sp in pass1_spots for pt in sp["polygon"]]
        span_y = max(60.0, max(ys) - min(ys))
        pad_y = max(48.0, span_y * 0.28)
        pad_x = max(36.0, w_img * 0.06)

        bx0 = max(0, int(round(min(xs) - pad_x)))
        bx1 = min(w_img, int(round(max(xs) + pad_x)))
        by0 = max(0, int(round(min(ys) - pad_y)))
        by1 = min(h_img, int(round(max(ys) + pad_y)))
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 160 or bh < 120:
            return None

        band_crop = full_img[by0:by1, bx0:bx1]
        ruler_zoom = self._draw_coordinate_ruler(band_crop, max_edge=1440.0)
        ok, buf = cv2.imencode(".jpg", ruler_zoom, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            return None
        b64_zoom = base64.b64encode(buf.tobytes()).decode("ascii")

        with self.vision._lock:
            self.vision.privacy_stats["slow_lane_vlm_calls"] += 1

        hint_items = []
        for sp in pass1_spots:
            p1000 = [
                [
                    int(round(np.clip((pt[0] - bx0) / float(bw) * 1000.0, 0, 1000))),
                    int(round(np.clip((pt[1] - by0) / float(bh) * 1000.0, 0, 1000))),
                ]
                for pt in sp["polygon"]
            ]
            hint_items.append(
                f'{sp["label"]}({sp.get("initial_status") or "FREE"}): 粗定位约 {p1000}'
            )

        zoom_prompt = (
            "这是车位区域的【局部高清放大特写图】，四周标有精确 0..1000 归一化坐标尺（50格细网格、100格主网格）。\n"
            f"第一阶段粗定位已识别出以下 {len(pass1_spots)} 个车位：\n"
            + "\n".join(hint_items)
            + "\n\n请基于高清特写地面细节，对这批车位进行【亚米级精修贴边】（保持车位数量与编号顺序一致）：\n"
            "1. 空车位（FREE）必须精确贴合地面白色分隔线、T字角标、限位器后缘，或红砖/植草砖与黑色沥青车道的分界线及砖缝列线！\n"
            "2. 有车车位（OCCUPIED）紧贴轮胎接地的铺装底面，绝不画到绿化灌木或行车道上。\n"
            "3. 相邻车位严格共用同一条分隔线端点 `[[后左x,后左y], [后右x,后右y], [前右x,前右y], [前左x,前左y]]`。\n"
            "仅输出严格 JSON：`{\"spots\": [{\"label\":\"01\",\"spot_type\":\"standard\",\"polygon_1000\":[[x,y],...],\"initial_status\":\"FREE或OCCUPIED\",\"confidence\":0.97,\"reason\":\"...\"}]}`"
        )

        content, err, _ = self.vision._post_vlm_completions(
            vlm_cfg,
            zoom_prompt,
            f"data:image/jpeg;base64,{b64_zoom}",
            max_tokens=3072,
            reasoning_effort="low",
            timeout_override=45.0,
        )
        if err or not content:
            return None

        try:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            parsed_z = json.loads(m.group(0) if m else content)
            z_list = parsed_z.get("spots") or []
            if len(z_list) != len(pass1_spots):
                return None
            refined_out: List[Dict[str, Any]] = []
            for idx, (orig_sp, z_item) in enumerate(zip(pass1_spots, z_list)):
                pts = z_item.get("polygon_1000") or z_item.get("polygon") or []
                if not isinstance(pts, list) or len(pts) < 3:
                    return None
                px_poly: List[List[float]] = []
                for p in pts:
                    if len(p) < 2:
                        continue
                    gx = bx0 + (float(p[0]) / 1000.0) * float(bw)
                    gy = by0 + (float(p[1]) / 1000.0) * float(bh)
                    px_poly.append(
                        [
                            round(float(np.clip(gx, 0, w_img - 1)), 1),
                            round(float(np.clip(gy, 0, h_img - 1)), 1),
                        ]
                    )
                if orig_sp.get("spot_type") in ("standard", "elastic_strip") and len(px_poly) == 4:
                    px_poly = self._normalize_quad_preserve_orientation(px_poly)
                merged = dict(orig_sp)
                merged["polygon"] = px_poly
                if z_item.get("initial_status") in ("FREE", "OCCUPIED", "BLOCKED"):
                    merged["initial_status"] = str(z_item["initial_status"]).upper()
                refined_out.append(merged)
            return refined_out
        except Exception:
            return None

    @classmethod
    def _normalize_quad_preserve_orientation(cls, pts: List[List[float]]) -> List[List[float]]:
        """
        Ensures a 4-point stall polygon is non-self-intersecting and clockwise in image
        space (`[rear_L, rear_R, front_R, front_L]`), without forcing Y-axis sorting
        that would twist stalls rotated along a curved road.
        """
        arr = np.array(pts[:4], dtype=np.float64)
        cx, cy = float(np.mean(arr[:, 0])), float(np.mean(arr[:, 1]))
        angles = np.arctan2(arr[:, 1] - cy, arr[:, 0] - cx)
        order = np.argsort(angles)  # clockwise in image coordinates (y points down)
        cw = arr[order]

        # Rotate cyclic start so edge (0 -> 1) matches the rear/upper edge closest to original pts[0]
        dists_to_p0 = [float(np.hypot(cw[k, 0] - arr[0, 0], cw[k, 1] - arr[0, 1])) for k in range(4)]
        start_k = int(np.argmin(dists_to_p0))
        cw = np.roll(cw, -start_k, axis=0)
        return [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in cw]

    @staticmethod
    def _weld_curved_or_unlined_row(
        spots: List[Dict[str, Any]], img_w: int, img_h: int
    ) -> List[Dict[str, Any]]:
        """
        Topology-safe shared-edge welder for curved roads, brick-paver stalls, or
        heavily-occupied bays where straight horizontal white-line snapping does not apply.
        Preserves each stall's curved Y elevation and fan rotation while eliminating
        overlaps or accidental hairline seams between adjacent stalls.
        """
        std_idxs = [
            i
            for i, s in enumerate(spots)
            if s.get("spot_type") == "standard" and len(s.get("polygon", [])) == 4
        ]
        if len(std_idxs) < 2:
            return spots

        std_idxs.sort(key=lambda idx: 0.25 * sum(float(p[0]) for p in spots[idx]["polygon"]))
        polys = {idx: [list(pt) for pt in spots[idx]["polygon"]] for idx in std_idxs}

        widths = [
            0.5
            * (
                float(np.hypot(polys[i][1][0] - polys[i][0][0], polys[i][1][1] - polys[i][0][1]))
                + float(np.hypot(polys[i][2][0] - polys[i][3][0], polys[i][2][1] - polys[i][3][1]))
            )
            for i in std_idxs
        ]
        med_w = max(24.0, float(np.median(widths)))
        weld_tol = med_w * 0.45

        for k in range(len(std_idxs) - 1):
            li = std_idxs[k]
            ri = std_idxs[k + 1]
            lp = polys[li]
            rp = polys[ri]
            d_top = float(np.hypot(lp[1][0] - rp[0][0], lp[1][1] - rp[0][1]))
            d_bot = float(np.hypot(lp[2][0] - rp[3][0], lp[2][1] - rp[3][1]))
            if d_top <= weld_tol and d_bot <= weld_tol:
                mt = [
                    round(float(np.clip(0.5 * (lp[1][0] + rp[0][0]), 0.0, img_w - 1.0)), 1),
                    round(float(np.clip(0.5 * (lp[1][1] + rp[0][1]), 0.0, img_h - 1.0)), 1),
                ]
                mb = [
                    round(float(np.clip(0.5 * (lp[2][0] + rp[3][0]), 0.0, img_w - 1.0)), 1),
                    round(float(np.clip(0.5 * (lp[2][1] + rp[3][1]), 0.0, img_h - 1.0)), 1),
                ]
                lp[1] = list(mt)
                rp[0] = list(mt)
                lp[2] = list(mb)
                rp[3] = list(mb)

        for idx in std_idxs:
            area = float(cv2.contourArea(np.array(polys[idx], dtype=np.float32)))
            if area >= 150.0:
                spots[idx]["polygon"] = polys[idx]
        return spots
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
    def _detect_row_white_geometry(
        img_bgr: np.ndarray, y_min: int, y_max: int
    ) -> Tuple[Optional[float], Optional[float], List[Tuple[float, float]]]:
        """
        Sub-pixel white parking-line geometry extractor within [y_min, y_max]:
          1. Finds both back horizontal white boundary (`back_y`, behind wheel-stoppers)
             and front horizontal white boundary (`front_y`, along driving lane).
          2. Traces painted slanted white divider ridges across ~22 horizontal scanlines
             in the clean middle stall band (avoiding top wheel-stoppers and bottom
             painted stall numbers `01..05`), supporting wide-angle perspective slopes
             up to |dx/dy| <= 2.2.
          3. Enforces vanishing-point perspective slope monotonicity across dividers so
             gray concrete curbs or frame borders are rejected.
        Returns `(back_y, front_y, list of (x_at_eff_top, x_at_eff_bot))`.
        """
        h, w = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

        y_lo = max(0, min(h - 1, int(y_min)))
        y_hi = max(y_lo + 1, min(h, int(y_max)))
        row_h0 = float(y_hi - y_lo)
        if row_h0 < 28:
            return None, None, []

        # 1. Detect front & back horizontal white painted lines (low saturation, high brightness)
        kh = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(13, h // 50)))
        th_h = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kh)
        white_h = (th_h > 28) & (hsv[:, :, 1] < 58) & (gray > 162)

        front_y: Optional[float] = None
        fb0 = max(0, int(round(y_hi - row_h0 * 0.24)))
        fb1 = min(h, int(round(y_hi + row_h0 * 0.22)))
        x_pad_f = int(w * 0.08)
        if fb1 > fb0 + 4 and (w - 2 * x_pad_f) > 20:
            prof_f = np.mean(white_h[fb0:fb1, x_pad_f : w - x_pad_f], axis=1)
            bf = int(np.argmax(prof_f))
            if float(prof_f[bf]) >= 0.25:
                front_y = float(fb0 + bf)

        back_y: Optional[float] = None
        tb0 = max(0, int(round(y_lo - row_h0 * 0.24)))
        tb1 = min(h, int(round(y_lo + row_h0 * 0.16)))
        x_pad_b = int(w * 0.14)
        if tb1 > tb0 + 4 and (w - 2 * x_pad_b) > 20:
            prof_b = np.mean(white_h[tb0:tb1, x_pad_b : w - x_pad_b], axis=1)
            bb = int(np.argmax(prof_b))
            if float(prof_b[bb]) >= 0.22:
                back_y = float(tb0 + bb)

        eff_top = back_y if back_y is not None else float(y_lo)
        eff_bot = front_y if front_y is not None else float(y_hi)
        row_h = max(28.0, eff_bot - eff_top)

        # 2. 1D White-Stripe Ridge Scanline Tracker (handles wide-angle slopes up to 2.2)
        kw = max(15, int(round(w * 0.022)) | 1)
        tophat_v = cv2.morphologyEx(
            gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1))
        )
        low_sat_bright = (hsv[:, :, 1] < 65) & (gray > 145)
        score_map = np.where(
            low_sat_bright,
            tophat_v.astype(np.float32) + (gray.astype(np.float32) - 142.0) * 0.30,
            0.0,
        )

        sy0 = max(2, int(round(eff_top + row_h * 0.10)))
        sy1 = min(h - 3, int(round(eff_bot - row_h * 0.18)))
        tracks: List[Tuple[float, float, float, float, int]] = []

        if sy1 - sy0 >= 24:
            step = max(2, (sy1 - sy0) // 22)
            ys = list(range(sy0, sy1, step))
            if len(ys) >= 8:
                scan_peaks: List[Tuple[float, List[Tuple[float, float]]]] = []
                max_stripe_w = max(28, int(w * 0.034))
                for y in ys:
                    prof = np.mean(score_map[max(0, y - 2) : min(h, y + 3), :], axis=0)
                    prof = cv2.GaussianBlur(prof.reshape(1, -1), (1, 5), 0).ravel()
                    thresh = max(24.0, float(np.percentile(prof, 84)))
                    xs = np.where(prof >= thresh)[0]
                    peaks_y: List[Tuple[float, float]] = []
                    if len(xs) > 0:
                        splits = np.where(np.diff(xs) > 4)[0] + 1
                        for grp in np.split(xs, splits):
                            if 1 <= len(grp) <= max_stripe_w:
                                weights = prof[grp]
                                cx = float(np.sum(grp * weights) / np.sum(weights))
                                # Ignore vertical image-frame screenshot border artifacts
                                if cx <= 9.0 or cx >= (w - 10.0):
                                    continue
                                peaks_y.append((cx, float(np.max(weights))))
                    scan_peaks.append((float(y), peaks_y))

                n_sc = len(scan_peaks)
                idx_starts = sorted({0, min(2, n_sc - 1), min(4, n_sc - 1)})
                idx_ends = sorted({max(0, n_sc - 1), max(0, n_sc - 4), max(0, int(n_sc * 0.62))})
                start_cands = [(scan_peaks[i][0], x) for i in idx_starts for x, _ in scan_peaks[i][1]]
                end_cands = [(scan_peaks[i][0], x) for i in idx_ends for x, _ in scan_peaks[i][1]]

                tol = max(4.0, w * 0.0055)
                for ya, xa in start_cands:
                    for yb, xb in end_cands:
                        if yb - ya < row_h * 0.26:
                            continue
                        slope = (xb - xa) / (yb - ya)
                        if abs(slope) > 2.25:
                            continue
                        inliers = []
                        total_w = 0.0
                        for y, pks in scan_peaks:
                            pred_x = xa + slope * (y - ya)
                            if pred_x < -4.0 or pred_x > w + 3.0:
                                continue
                            best = None
                            for px, st in pks:
                                err = abs(px - pred_x)
                                if err <= tol and (best is None or err < abs(best[0] - pred_x)):
                                    best = (px, st)
                            if best is not None:
                                inliers.append((y, best[0], best[1]))
                                total_w += best[1]
                        if len(inliers) >= max(6, int(n_sc * 0.45)):
                            iy = np.array([pt[0] for pt in inliers], dtype=np.float64)
                            ix = np.array([pt[1] for pt in inliers], dtype=np.float64)
                            iw = np.array([pt[2] for pt in inliers], dtype=np.float64)
                            s_fit, b_fit = np.polyfit(iy, ix, 1, w=iw)
                            x_mid = float(s_fit * (0.5 * (eff_top + eff_bot)) + b_fit)
                            if x_mid <= 10.0 or x_mid >= (w - 10.0):
                                continue
                            tracks.append(
                                (total_w * len(inliers), x_mid, float(s_fit), float(b_fit), len(inliers))
                            )

        # Non-maximum suppression across detected divider tracks
        tracks.sort(key=lambda t: t[0], reverse=True)
        kept: List[Tuple[float, float, float, float, int]] = []
        for tr in tracks:
            if all(abs(tr[1] - k[1]) > w * 0.052 for k in kept):
                kept.append(tr)
        kept.sort(key=lambda t: t[1])

        # Enforce vanishing-point perspective slope consistency (rejects non-converging curb edges)
        if len(kept) >= 3:
            strongest = sorted(kept, key=lambda t: t[0], reverse=True)[: min(4, len(kept))]
            ms = np.array([k[1] for k in strongest], dtype=np.float64)
            ss = np.array([k[2] for k in strongest], dtype=np.float64)
            if float(np.ptp(ms)) > w * 0.12:
                a_s, b_s = np.polyfit(ms, ss, 1)
                kept = [k for k in kept if abs(k[2] - (a_s * k[1] + b_s)) <= 0.24]

        if len(kept) >= 2:
            dividers_out = [
                (k[2] * eff_top + k[3], k[2] * eff_bot + k[3]) for k in kept
            ]
            return back_y, front_y, dividers_out

        # Fallback: HoughLinesP for ultra-thin synthetic lines
        r0 = max(0, int(eff_top) - int(h * 0.02))
        r1 = min(h, int(eff_bot) + int(h * 0.02))
        if r1 - r0 < 30:
            return back_y, front_y, []

        edges = cv2.Canny(tophat_v[r0:r1, :], 32, 110)
        min_len = max(36, int((r1 - r0) * 0.36))
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180.0, threshold=26, minLineLength=min_len, maxLineGap=22
        )
        raw_dividers: List[Tuple[float, float]] = []
        if lines is not None:
            for seg in np.asarray(lines).reshape(-1, 4):
                x1, y1, x2, y2 = float(seg[0]), float(seg[1] + r0), float(seg[2]), float(seg[3] + r0)
                dy = y2 - y1
                dx = x2 - x1
                if abs(dy) < min_len * 0.65:
                    continue
                slope_x_per_y = dx / dy
                if abs(slope_x_per_y) > 2.2:
                    continue
                x_top = x1 + (eff_top - y1) * slope_x_per_y
                x_bot = x1 + (eff_bot - y1) * slope_x_per_y
                x_mid = 0.5 * (x_top + x_bot)
                if 10.0 < x_mid < (w - 10.0):
                    raw_dividers.append((x_top, x_bot))

        if not raw_dividers:
            return back_y, front_y, []

        raw_dividers.sort(key=lambda d: 0.5 * (d[0] + d[1]))
        clustered: List[Tuple[float, float]] = []
        cluster_buf: List[Tuple[float, float]] = [raw_dividers[0]]
        merge_tol = w * 0.028
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
        return back_y, front_y, clustered

    @classmethod
    def _detect_white_stall_features(
        cls, img_bgr: np.ndarray, y_min: int, y_max: int
    ) -> Tuple[Optional[float], List[Tuple[float, float]]]:
        """
        Backward-compatible wrapper returning `(front_y, dividers_at_[y_min, y_max])`.
        """
        back_y, front_y, divs = cls._detect_row_white_geometry(img_bgr, y_min, y_max)
        if not divs:
            return front_y, []
        eff_top = back_y if back_y is not None else float(y_min)
        eff_bot = front_y if front_y is not None else float(y_max)
        denom = max(10.0, eff_bot - eff_top)
        reprojected = []
        for dt, db in divs:
            s = (db - dt) / denom
            reprojected.append((dt + (y_min - eff_top) * s, dt + (y_max - eff_top) * s))
        return front_y, reprojected

    @classmethod
    def _snap_edges_to_local_visual_cues(
        cls,
        img_bgr: np.ndarray,
        spots: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Local multi-cue perpendicular edge snapper (<12ms CPU) for empty ground,
        curved roadside bays, brick/grass pavers, and T-marker stalls:
          - Front & Rear edges (`0->1` rear curb, `3->2` front lane transition):
            snaps within ±10px along the outward normal to LAB chromatic + directional
            Sobel pavement/curb/asphalt transitions (plus horizontal white paint).
          - Left & Right dividers (`0->3`, `1->2`):
            snaps within ±8px along the divider normal to white paint ridges (`TOPHAT`)
            or dark paver-brick / concrete expansion seams (`BLACKHAT`).
          - Occupied stalls skip front/interior-occluded edges so car roofs/hoods never
            pull ground stall lines off-target.
        """
        h, w = img_bgr.shape[:2]
        if h < 48 or w < 48:
            return spots

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        lkx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3)
        lky = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3)
        chroma_grad = np.sqrt(np.sum(lkx * lkx + lky * lky, axis=2))

        k_paint = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(9, (w // 70) | 1), max(9, (w // 70) | 1)))
        tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k_paint).astype(np.float32)
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k_paint).astype(np.float32)
        white_ridge = np.where((hsv[:, :, 1] < 68) & (gray > 142), tophat * 1.45, 0.0)

        # Boundary step map (curb / brick-to-asphalt / shrub-to-paver)
        boundary_cue = 0.55 * chroma_grad + 0.30 * np.hypot(gx, gy) + 0.65 * white_ridge
        # Divider ridge map (white paint stripe OR dark expansion/brick seam)
        divider_cue = white_ridge + 0.42 * blackhat + 0.18 * np.abs(gx)

        def best_normal_offset(
            p_a: List[float],
            p_b: List[float],
            cue_map: np.ndarray,
            max_shift: int,
            min_ratio: float,
            min_peak: float,
        ) -> float:
            ax, ay = float(p_a[0]), float(p_a[1])
            bx, by = float(p_b[0]), float(p_b[1])
            edge_len = float(np.hypot(bx - ax, by - ay))
            if edge_len < 18.0:
                return 0.0
            tx, ty = (bx - ax) / edge_len, (by - ay) / edge_len
            nx_u, ny_u = -ty, tx

            ts = np.linspace(0.18, 0.82, 17, dtype=np.float32)
            base_xs = ax + ts * (bx - ax)
            base_ys = ay + ts * (by - ay)

            offsets = list(range(-max_shift, max_shift + 1))
            scores = np.zeros(len(offsets), dtype=np.float32)
            for oi, off in enumerate(offsets):
                sx = np.clip(np.round(base_xs + off * nx_u).astype(np.int32), 0, w - 1)
                sy = np.clip(np.round(base_ys + off * ny_u).astype(np.int32), 0, h - 1)
                # Slight distance regularization prefers clean nearby peak
                dist_pen = 1.0 - 0.016 * abs(off)
                scores[oi] = float(np.mean(cue_map[sy, sx])) * dist_pen

            best_idx = int(np.argmax(scores))
            peak_val = float(scores[best_idx])
            med_val = max(1e-3, float(np.median(scores)))
            if peak_val >= min_peak and (peak_val / med_val) >= min_ratio:
                return float(offsets[best_idx])
            return 0.0

        def shift_edge(poly: List[List[float]], i0: int, i1: int, shift: float) -> None:
            if abs(shift) < 0.5:
                return
            ax, ay = float(poly[i0][0]), float(poly[i0][1])
            bx, by = float(poly[i1][0]), float(poly[i1][1])
            el = float(np.hypot(bx - ax, by - ay))
            if el < 1e-3:
                return
            nx_u, ny_u = -(by - ay) / el, (bx - ax) / el
            poly[i0] = [
                round(float(np.clip(ax + shift * nx_u, 0.0, w - 1.0)), 1),
                round(float(np.clip(ay + shift * ny_u, 0.0, h - 1.0)), 1),
            ]
            poly[i1] = [
                round(float(np.clip(bx + shift * nx_u, 0.0, w - 1.0)), 1),
                round(float(np.clip(by + shift * ny_u, 0.0, h - 1.0)), 1),
            ]

        for sp in spots:
            if sp.get("spot_type") != "standard" or len(sp.get("polygon", [])) != 4:
                continue
            st = str(sp.get("initial_status") or sp.get("status") or "FREE").upper()
            poly = [list(pt) for pt in sp["polygon"]]

            # Rear curb edge (0 -> 1)
            s_rear = best_normal_offset(poly[0], poly[1], boundary_cue, max_shift=9, min_ratio=1.32, min_peak=26.0)
            shift_edge(poly, 0, 1, s_rear)

            if st == "FREE":
                # Front lane transition (3 -> 2) & both side dividers (0 -> 3, 1 -> 2)
                s_front = best_normal_offset(poly[3], poly[2], boundary_cue, max_shift=10, min_ratio=1.30, min_peak=24.0)
                shift_edge(poly, 3, 2, s_front)

                s_left = best_normal_offset(poly[0], poly[3], divider_cue, max_shift=8, min_ratio=1.38, min_peak=18.0)
                shift_edge(poly, 0, 3, s_left)

                s_right = best_normal_offset(poly[1], poly[2], divider_cue, max_shift=8, min_ratio=1.38, min_peak=18.0)
                shift_edge(poly, 1, 2, s_right)

            area_after = float(cv2.contourArea(np.array(poly, dtype=np.float32)))
            area_before = max(1.0, float(cv2.contourArea(np.array(sp["polygon"], dtype=np.float32))))
            if 0.72 <= (area_after / area_before) <= 1.32:
                sp["polygon"] = poly

        del gray, hsv, lab, gx, gy, lkx, lky, chroma_grad, tophat, blackhat, white_ridge, boundary_cue, divider_cue
        return spots

    def _refine_polygons_with_opencv_lines(
        self,
        img_bgr: np.ndarray,
        spots: List[Dict[str, Any]],
        row_geometry: str = "auto",
    ) -> List[Dict[str, Any]]:
        """
        Snap VLM-detected standard 4-point stall polygons onto painted white lines
        using vanishing-point perspective pencil geometry (`x_top = alpha * x_bot + beta`)
        when straight painted dividers exist (`>=2`), AND snap curb/brick/seam boundaries
        via `_snap_edges_to_local_visual_cues` before welding shared vertices watertight.
        """
        h, w = img_bgr.shape[:2]
        std_indices = [
            i
            for i, s in enumerate(spots)
            if s.get("spot_type") == "standard" and len(s.get("polygon", [])) == 4
        ]
        if len(std_indices) < 2:
            return spots

        # 1. If VLM identified a curved / brick-paver / unlined bay, snap local curb/brick seams & weld!
        if row_geometry == "curved_or_unlined":
            snapped = self._snap_edges_to_local_visual_cues(img_bgr, spots)
            return self._weld_curved_or_unlined_row(snapped, w, h)

        ys_top = [0.5 * (spots[i]["polygon"][0][1] + spots[i]["polygon"][1][1]) for i in std_indices]
        ys_bot = [0.5 * (spots[i]["polygon"][2][1] + spots[i]["polygon"][3][1]) for i in std_indices]
        med_top = float(np.median(ys_top))
        med_bot = float(np.median(ys_bot))
        row_h = max(20.0, med_bot - med_top)

        # 2. Geometric curvature detector: if front or rear Y elevations step/curve by >16% of stall depth,
        #    this is a curved or sloped roadside bay — snap local visual cues & weld without flattening Y!
        if (max(ys_bot) - min(ys_bot)) > row_h * 0.16 or (max(ys_top) - min(ys_top)) > row_h * 0.16:
            snapped = self._snap_edges_to_local_visual_cues(img_bgr, spots)
            return self._weld_curved_or_unlined_row(snapped, w, h)

        row_idxs = [
            idx
            for idx, yt, yb in zip(std_indices, ys_top, ys_bot)
            if abs(yt - med_top) < row_h * 0.38 and abs(yb - med_bot) < row_h * 0.38
        ]
        if len(row_idxs) < 2:
            snapped = self._snap_edges_to_local_visual_cues(img_bgr, spots)
            return self._weld_curved_or_unlined_row(snapped, w, h)

        row_idxs.sort(key=lambda idx: 0.25 * sum(p[0] for p in spots[idx]["polygon"]))

        # Preserve gentle linear slope across slanted roadside rows instead of flattening Y to horizontal!
        first_top_y = 0.5 * (spots[row_idxs[0]]["polygon"][0][1] + spots[row_idxs[0]]["polygon"][1][1])
        last_top_y = 0.5 * (spots[row_idxs[-1]]["polygon"][0][1] + spots[row_idxs[-1]]["polygon"][1][1])
        first_bot_y = 0.5 * (spots[row_idxs[0]]["polygon"][2][1] + spots[row_idxs[0]]["polygon"][3][1])
        last_bot_y = 0.5 * (spots[row_idxs[-1]]["polygon"][2][1] + spots[row_idxs[-1]]["polygon"][3][1])
        if abs(last_bot_y - first_bot_y) > max(4.5, row_h * 0.085) or abs(last_top_y - first_top_y) > max(
            4.5, row_h * 0.085
        ):
            snapped = self._snap_edges_to_local_visual_cues(img_bgr, spots)
            return self._weld_curved_or_unlined_row(snapped, w, h)

        # 3. Mask out OCCUPIED vehicle interiors & exterior road lane lines before searching for painted white stall stripes,
        #    preventing bright car roofs/windshields or distant road lane stripes from hijacking perspective fitting!
        masked_img = img_bgr.copy()
        min_row_x = min(min(p[0] for p in spots[i]["polygon"]) for i in row_idxs)
        max_row_x = max(max(p[0] for p in spots[i]["polygon"]) for i in row_idxs)
        left_cut = max(0, int(round(min_row_x - w * 0.045)))
        right_cut = min(w, int(round(max_row_x + w * 0.045)))
        if left_cut > 0:
            masked_img[:, :left_cut] = 60
        if right_cut < w:
            masked_img[:, right_cut:] = 60

        for idx in row_idxs:
            st_init = str(spots[idx].get("initial_status") or spots[idx].get("status") or "").upper()
            if st_init == "OCCUPIED":
                pts_arr = np.array(spots[idx]["polygon"], dtype=np.float32)
                c_pt = np.mean(pts_arr, axis=0, keepdims=True)
                shrunk = (c_pt + 0.68 * (pts_arr - c_pt)).astype(np.int32)
                cv2.fillPoly(masked_img, [shrunk], (70, 70, 70))

        back_y, front_y, dividers = self._detect_row_white_geometry(
            masked_img, int(round(med_top)), int(round(med_bot))
        )
        del masked_img

        # 4. Allow straight perspective divider snapping whenever >=2 painted dividers are found
        #    (even on open-front T-stalls where front_y/back_y have no continuous horizontal white line!);
        #    otherwise fall back to local curb/seam cue snapping + topology-safe welding.
        if len(dividers) < 2 or (front_y is None and back_y is None and len(dividers) < 3):
            snapped = self._snap_edges_to_local_visual_cues(img_bgr, spots)
            return self._weld_curved_or_unlined_row(snapped, w, h)

        eff_top = (
            round(back_y, 1)
            if (back_y is not None and abs(back_y - med_top) <= h * 0.065)
            else round(med_top, 1)
        )
        eff_bot = (
            round(front_y, 1)
            if (front_y is not None and abs(front_y - med_bot) <= h * 0.055)
            else round(med_bot, 1)
        )
        geom_top = back_y if back_y is not None else med_top
        geom_bot = front_y if front_y is not None else med_bot
        geom_denom = max(10.0, geom_bot - geom_top)

        # Project all detected dividers onto [eff_top, eff_bot]
        proj_dividers: List[Tuple[float, float]] = []
        for dt, db in dividers:
            s = (db - dt) / geom_denom
            proj_dividers.append((dt + (eff_top - geom_top) * s, dt + (eff_bot - geom_top) * s))

        # Fit exact camera perspective pencil x_top = alpha * x_bot + beta when >= 2 dividers exist
        pencil: Optional[Tuple[float, float]] = None
        if len(proj_dividers) >= 2:
            xb_arr = np.array([d[1] for d in proj_dividers], dtype=np.float64)
            xt_arr = np.array([d[0] for d in proj_dividers], dtype=np.float64)
            if float(np.ptp(xb_arr)) > w * 0.12:
                a_p, b_p = np.polyfit(xb_arr, xt_arr, 1)
                if 0.30 <= a_p <= 1.25:
                    pencil = (float(a_p), float(b_p))

        cand_polys = {idx: [list(pt) for pt in spots[idx]["polygon"]] for idx in row_idxs}
        for idx in row_idxs:
            if abs(cand_polys[idx][0][1] - med_top) <= h * 0.06:
                cand_polys[idx][0][1] = eff_top
            if abs(cand_polys[idx][1][1] - med_top) <= h * 0.06:
                cand_polys[idx][1][1] = eff_top
            if abs(cand_polys[idx][2][1] - med_bot) <= h * 0.055:
                cand_polys[idx][2][1] = eff_bot
            if abs(cand_polys[idx][3][1] - med_bot) <= h * 0.055:
                cand_polys[idx][3][1] = eff_bot

        # Match dividers primarily by foreground `x_bot` (where VLM is accurate & un-foreshortened),
        # allowing generous `x_top` correction so underestimated VLM perspective converges cleanly!
        bot_tol = w * 0.068
        top_tol = w * 0.185

        def snap_edge_to_divider(xt: float, xb: float) -> Tuple[float, float]:
            best_d: Optional[Tuple[float, float]] = None
            best_err = bot_tol
            for d_top, d_bot in proj_dividers:
                d_bot_clamp = float(np.clip(d_bot, 0.0, w - 1.0))
                err_bot = abs(d_bot_clamp - xb)
                err_top = abs(d_top - xt)
                score_err = 0.80 * err_bot + 0.20 * min(err_top, bot_tol)
                if err_bot <= bot_tol and err_top <= top_tol and score_err < best_err:
                    best_err = score_err
                    best_d = (d_top, d_bot_clamp)

            if best_d is not None:
                return (
                    round(float(np.clip(best_d[0], 0.0, w - 1.0)), 1),
                    round(float(np.clip(best_d[1], 0.0, w - 1.0)), 1),
                )
            # Even if white stripe was occluded by a parked car, apply camera's vanishing-point pencil to xt!
            if pencil is not None:
                proj_xt = pencil[0] * xb + pencil[1]
                if abs(proj_xt - xt) <= top_tol:
                    return (
                        round(float(np.clip(proj_xt, 0.0, w - 1.0)), 1),
                        round(float(np.clip(xb, 0.0, w - 1.0)), 1),
                    )
            return round(xt, 1), round(xb, 1)

        # Snap outer left & outer right stall boundaries
        first_p = cand_polys[row_idxs[0]]
        first_p[0][0], first_p[3][0] = snap_edge_to_divider(first_p[0][0], first_p[3][0])

        last_p = cand_polys[row_idxs[-1]]
        last_p[1][0], last_p[2][0] = snap_edge_to_divider(last_p[1][0], last_p[2][0])

        # Weld & snap shared internal dividers between adjacent stalls
        adj_tol = w * 0.065
        for k in range(len(row_idxs) - 1):
            lp = cand_polys[row_idxs[k]]
            rp = cand_polys[row_idxs[k + 1]]
            if abs(lp[1][0] - rp[0][0]) <= adj_tol or abs(lp[2][0] - rp[3][0]) <= adj_tol:
                yt = round(0.5 * (lp[1][1] + rp[0][1]), 1)
                yb = round(0.5 * (lp[2][1] + rp[3][1]), 1)
                xt = 0.5 * (lp[1][0] + rp[0][0])
                xb = 0.5 * (lp[2][0] + rp[3][0])
                s_xt, s_xb = snap_edge_to_divider(xt, xb)
                lp[1] = [s_xt, yt]
                lp[2] = [s_xb, yb]
                rp[0] = [s_xt, yt]
                rp[3] = [s_xb, yb]

        min_w = w * 0.032
        for idx in row_idxs:
            cp = cand_polys[idx]
            if (cp[1][0] - cp[0][0]) >= min_w and (cp[2][0] - cp[3][0]) >= min_w and (cp[2][1] - cp[1][1]) >= 20:
                spots[idx]["polygon"] = cp

        return spots

    def _calibrate_opencv_fallback(
        self,
        img_bgr: np.ndarray,
        cam: Optional[Dict[str, Any]] = None,
        existing_spots: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Pure-OpenCV automatic perspective stall detector when VLM is offline or disabled.
        Honors valid custom A1~A4 anchors + expected stall count first; otherwise finds low-saturation
        painted white lines and reconstructs perspective-consistent stall trapezoids without over-extrapolating.
        """
        h, w = img_bgr.shape[:2]
        orientation, expected_count, _ = self._infer_camera_layout_hint(cam, existing_spots)

        # 1. If valid custom A1~A4 outer bay landmarks exist in matching aspect space, subdivide & snap locally!
        if isinstance(cam, dict) and cam.get("anchor_points_custom") and len(cam.get("anchor_points") or []) >= 4:
            old_w = max(64.0, float(cam.get("image_width") or w))
            old_h = max(64.0, float(cam.get("image_height") or h))
            prev_crop = cam.get("crop_roi") or {}
            had_c = bool(isinstance(prev_crop, dict) and prev_crop.get("enabled"))
            raw_aspect_ref = float(prev_crop.get("raw_aspect") or (old_w / old_h))
            cur_aspect = float(w) / max(1.0, float(h))
            if max(raw_aspect_ref / max(0.05, cur_aspect), cur_aspect / max(0.05, raw_aspect_ref)) <= 1.18:
                rx = float(prev_crop.get("x", 0.0)) if had_c else 0.0
                ry = float(prev_crop.get("y", 0.0)) if had_c else 0.0
                rw_c = float(prev_crop.get("w", 1.0)) if had_c else 1.0
                rh_c = float(prev_crop.get("h", 1.0)) if had_c else 1.0
                outer_a: List[List[float]] = []
                for apt in (cam.get("anchor_points") or [])[:4]:
                    ax = (rx + (float(apt.get("x", 0.0)) / old_w) * rw_c) * w
                    ay = (ry + (float(apt.get("y", 0.0)) / old_h) * rh_c) * h
                    outer_a.append([round(float(np.clip(ax, 0.0, w - 1.0)), 1), round(float(np.clip(ay, 0.0, h - 1.0)), 1)])
                n_sub = expected_count if expected_count >= 1 else 5
                traps_a = subdivide_perspective_row(outer_a, count=n_sub, direction="along_01")
                seed_spots = [
                    {
                        "label": f"{i+1:02d}",
                        "spot_type": "standard",
                        "compact_only": False,
                        "total_meters": 5.2,
                        "polygon": p,
                    }
                    for i, p in enumerate(traps_a)
                ]
                snapped_a = self._snap_edges_to_local_visual_cues(img_bgr, seed_spots)
                welded_a = self._weld_curved_or_unlined_row(snapped_a, w, h)
                return welded_a, f"OpenCV 基于 A1~A4 锚点几何细分识别 {len(welded_a)} 个透视车位"

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

        # 2. Roadside horizontal/parallel stalls (e.g. H2 -> 2 end-to-end curb bays, never 8 grass-paver slices!)
        if orientation == "H":
            n_h = expected_count if expected_count >= 1 else 2
            y_lo, y_hi = int(h * 0.25), int(h * 0.90)
            roi_hsv = hsv[y_lo:y_hi, int(w * 0.12) : int(w * 0.88)]
            green_row = np.mean(
                (roi_hsv[:, :, 0] >= 32)
                & (roi_hsv[:, :, 0] <= 90)
                & (roi_hsv[:, :, 1] >= 48)
                & (roi_hsv[:, :, 2] >= 35),
                axis=1,
            )
            asphalt_row = np.mean(
                (roi_hsv[:, :, 1] < 48) & (roi_hsv[:, :, 2] >= 38) & (roi_hsv[:, :, 2] <= 145),
                axis=1,
            )
            band_h = max(1, y_hi - y_lo)
            top_candidates = np.where(green_row[: int(band_h * 0.62)] >= 0.28)[0]
            y_back_h = (
                int(y_lo + int(top_candidates[-1]) + max(4, int(h * 0.015)))
                if top_candidates.size > 0
                else int(h * 0.44)
            )
            bot_rel_start = max(int(band_h * 0.38), (y_back_h - y_lo) + int(h * 0.14))
            bot_candidates = np.where(asphalt_row[bot_rel_start:] >= 0.52)[0]
            y_front_h = (
                int(y_lo + bot_rel_start + int(bot_candidates[0]))
                if bot_candidates.size > 0
                else min(int(h * 0.72), y_back_h + int(h * 0.24))
            )
            if y_front_h - y_back_h < int(h * 0.14):
                y_back_h = int(h * 0.44)
                y_front_h = int(h * 0.68)
            outer_h = [
                [round(w * 0.08, 1), float(y_back_h)],
                [round(w * 0.92, 1), float(y_back_h)],
                [round(w * 0.95, 1), float(y_front_h)],
                [round(w * 0.05, 1), float(y_front_h)],
            ]
            traps_h = subdivide_perspective_row(outer_h, count=n_h, direction="along_01")
            seed_h = [
                {
                    "label": f"{i+1:02d}",
                    "spot_type": "standard",
                    "compact_only": False,
                    "total_meters": 5.2,
                    "polygon": p,
                }
                for i, p in enumerate(traps_h)
            ]
            snapped_h = self._snap_edges_to_local_visual_cues(img_bgr, seed_h)
            welded_h = self._weld_curved_or_unlined_row(snapped_h, w, h)
            return welded_h, f"OpenCV 路侧顺停带几何划分 ({len(welded_h)} 车位)"

        kh = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 48)))
        th_h = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kh)
        search_y0, search_y1 = int(h * 0.36), int(h * 0.85)
        white_band = (th_h[search_y0:search_y1, :] > 32) & (hsv[search_y0:search_y1, :, 1] < 72) & (
            gray[search_y0:search_y1, :] > 158
        )
        row_profile = np.mean(white_band, axis=1)
        best_rel = int(np.argmax(row_profile))

        if float(row_profile[best_rel]) >= 0.26:
            y_front = int(search_y0 + best_rel)
            y_back_est = max(int(h * 0.22), y_front - int(h * 0.19))
            back_y, front_y_det, dividers = self._detect_row_white_geometry(img_bgr, y_back_est, y_front)
            y_back = int(round(back_y)) if back_y is not None else y_back_est
            if front_y_det is not None:
                y_front = int(round(front_y_det))
            if len(dividers) >= 3 and (back_y is not None or front_y_det is not None):
                # Estimate linear perspective relation x_top = a * x_bot + b across detected dividers
                xb_arr = np.array([d[1] for d in dividers], dtype=np.float64)
                xt_arr = np.array([d[0] for d in dividers], dtype=np.float64)
                a_coef, b_coef = np.polyfit(xb_arr, xt_arr, 1) if len(dividers) >= 2 else (1.0, 0.0)

                diffs = np.diff(xb_arr)
                valid_pitches = [d for d in diffs if w * 0.09 <= d <= w * 0.34]
                if valid_pitches:
                    pitch_bot = float(np.median(valid_pitches))

                    # Interpolate missing occluded dividers; extrapolate edges ONLY when expected_count requires it
                    full_xb: List[float] = [float(xb_arr[0])]
                    for nxt in xb_arr[1:]:
                        start_v = full_xb[-1]
                        gap = nxt - start_v
                        if gap < w * 0.08:
                            continue
                        steps = max(1, int(round(gap / pitch_bot)))
                        for s in range(1, steps + 1):
                            full_xb.append(start_v + s * (gap / steps))

                    if (
                        expected_count >= 2
                        and len(full_xb) - 1 < expected_count
                        and full_xb[0] - pitch_bot >= w * 0.015
                    ):
                        full_xb.insert(0, full_xb[0] - pitch_bot)
                    if (
                        expected_count >= 2
                        and len(full_xb) - 1 < expected_count
                        and full_xb[-1] + pitch_bot <= w - 1 - w * 0.015
                    ):
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
                        if (poly[1][0] - poly[0][0]) >= w * 0.055 and (poly[2][0] - poly[3][0]) >= w * 0.055:
                            spots_out.append(
                                {
                                    "label": f"{len(spots_out)+1:02d}",
                                    "spot_type": "standard",
                                    "compact_only": False,
                                    "total_meters": 5.2,
                                    "polygon": poly,
                                }
                            )
                    if expected_count >= 2 and len(spots_out) > expected_count:
                        trim_start = (len(spots_out) - expected_count) // 2
                        spots_out = [
                            {**sp, "label": f"{idx+1:02d}"}
                            for idx, sp in enumerate(spots_out[trim_start : trim_start + expected_count])
                        ]
                    if len(spots_out) >= 2:
                        return spots_out, f"OpenCV 标线几何自动识别 {len(spots_out)} 个连排透视车位"

        # Generic perspective row fallback for unmarked synthetic test scenes
        fallback_cnt = expected_count if expected_count >= 1 else 5
        outer = [
            [round(w * 0.10, 1), round(h * 0.48, 1)],
            [round(w * 0.90, 1), round(h * 0.48, 1)],
            [round(w * 0.95, 1), round(h * 0.72, 1)],
            [round(w * 0.05, 1), round(h * 0.72, 1)],
        ]
        traps = subdivide_perspective_row(outer, count=fallback_cnt, direction="along_01")
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
            f"OpenCV 默认透视网格自动划分 ({fallback_cnt} 车位)",
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
                vpts = sp.get("vehicle_polygon")
                if isinstance(vpts, list) and len(vpts) >= 3:
                    cv2.fillPoly(occ_roi_mask, [np.array(vpts, dtype=np.int32)], 255)
            occ_roi_mask = cv2.dilate(occ_roi_mask, np.ones((25, 25), np.uint8), iterations=1)
            plate_mask = cv2.bitwise_and(plate_mask, occ_roi_mask)
        else:
            plate_mask[: int(h * 0.25), :] = 0

        close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
        plate_mask = cv2.morphologyEx(plate_mask, cv2.MORPH_CLOSE, close_k)

        contours, _ = cv2.findContours(plate_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        redacted = 0
        max_plate_w = max(w * 0.22, 140.0)
        max_plate_h = max(h * 0.12, 42.0)
        for cnt in contours:
            rx, ry, rw, rh = cv2.boundingRect(cnt)
            aspect = rw / max(1.0, float(rh))
            if (
                max(8.0, w * 0.012) <= rw <= max_plate_w
                and max(4.0, h * 0.006) <= rh <= max_plate_h
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
