import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import re
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import requests

from app.config import config_manager
from app.database import Database, compute_next_calib_check_ts
from app.homography import derive_vehicle_envelope_polygon, extract_spot_patch
from app.state_machine import SpotStateMachine

MAX_SIGNATURE_CACHE_ENTRIES = 4096   # Practical LRU pool for multi-camera 32x32 frame-diff signatures
MAX_TEMPLATE_CACHE_CAMERAS = 32      # Hot RAM cache for camera reference templates + ORB keypoints
MAX_LATEST_CHECK_FRAMES = 16         # Latest 320px pre-alignment grayscale frames (~55KB each) for drift check
MAX_VLM_CACHE_ENTRIES = 1024         # Perceptual VLM judgment cache (accelerates Frame-2 debounce & static polls)
MAX_MOTION_TRACK_ENTRIES = 1024      # Bounded per-spot longitudinal motion vector tracker (<64KB total)
MAX_DYNAMIC_ROI_CACHE_ENTRIES = 64   # Bounded dynamic ROI scene-anchor cache for wind/zoom recovery
DEFAULT_CACHE_BUDGET_MB = 64.0       # Balanced 64 MB RAM cache budget (fast hits without runaway growth)
MAX_CONCURRENT_IMAGE_DECODES = 4     # Bounded concurrent full-frame decodes
MAX_CONCURRENT_VLM_CALLS = 4         # Global RPM/TPM-safe concurrency ceiling for parallel VLM requests
MAX_DECODE_LONG_EDGE = 2560          # Downscale ultra-high-res frames immediately in RAM


def _to_rootsift_descriptors(des: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """
    Idempotently convert raw OpenCV SIFT descriptors (0..512) into RootSIFT
    (L1-normalized Hellinger kernel, values in 0..1) to boost matching precision
    under focal zoom, lighting shifts, and out-of-FOV crops without extra RAM.
    """
    if des is None:
        return None
    arr = np.asarray(des, dtype=np.float32)
    if arr.size == 0 or float(np.max(arr)) <= 1.05:
        return arr
    l1 = np.sum(np.abs(arr), axis=1, keepdims=True) + 1e-7
    return np.sqrt(arr / l1, dtype=np.float32)


def build_non_foliage_rigid_mask(bgr_img: np.ndarray, min_valid_ratio: float = 0.30) -> np.ndarray:
    """
    Build a uint8 (0 or 255) mask that suppresses green tree crowns, bushes, and swaying
    leaves (HSV H in [33..89], S >= 44, V >= 26) so camera shake alignment and dynamic ROI
    localization lock strictly onto rigid civil structures (curbs, bricks, walls, lanes).
    """
    if bgr_img is None or bgr_img.size == 0:
        return np.full((1, 1), 255, dtype=np.uint8)
    hsv = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    foliage = (
        (hsv[:, :, 0] >= 33)
        & (hsv[:, :, 0] <= 89)
        & (hsv[:, :, 1] >= 44)
        & (hsv[:, :, 2] >= 26)
    )
    foliage_u8 = cv2.dilate(foliage.astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
    rigid = np.where(foliage_u8 > 0, 0, 255).astype(np.uint8)
    if float(np.mean(rigid > 0)) < min_valid_ratio:
        rigid.fill(255)
    return rigid


def apply_camera_crop_roi(
    img_bgr: np.ndarray,
    crop_roi: Optional[Dict[str, Any]],
    cal_w: Optional[int] = None,
    cal_h: Optional[int] = None,
    already_cropped: bool = False,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Deterministically crops a raw camera frame to the camera's locked parking-strip ROI
    (`crop_roi = {"enabled": True, "x": 0..1, "y": 0..1, "w": 0..1, "h": 0..1}`).
    Includes a smart resolution/aspect-ratio guard so images already generated in the
    cropped calibration canvas space (e.g. stored cropped templates or synthetic canvas
    test frames) are NEVER double-cropped.
    """
    if already_cropped or not isinstance(crop_roi, dict) or not crop_roi.get("enabled"):
        return img_bgr, {"enabled": False, "applied": False}

    rx = float(crop_roi.get("x", 0.0))
    ry = float(crop_roi.get("y", 0.0))
    rw = float(crop_roi.get("w", 1.0))
    rh = float(crop_roi.get("h", 1.0))
    if rw >= 0.995 and rh >= 0.995 and rx <= 0.005 and ry <= 0.005:
        return img_bgr, {"enabled": False, "applied": False}

    h, w = img_bgr.shape[:2]
    if h < 32 or w < 32:
        return img_bgr, {"enabled": True, "applied": False, "reason": "image_too_small"}

    raw_size = crop_roi.get("raw_size")
    raw_aspect = float(crop_roi.get("raw_aspect") or 0.0)
    if isinstance(raw_size, (list, tuple)) and len(raw_size) >= 2:
        raw_w0 = max(32, int(raw_size[0]))
        raw_h0 = max(32, int(raw_size[1]))
        if raw_aspect <= 0.05:
            raw_aspect = float(raw_w0) / float(raw_h0)
        exp_x0 = max(0, min(raw_w0 - 16, int(round(rx * raw_w0))))
        exp_y0 = max(0, min(raw_h0 - 16, int(round(ry * raw_h0))))
        exp_w = max(16, min(raw_w0, int(round((rx + rw) * raw_w0))) - exp_x0)
        exp_h = max(16, min(raw_h0, int(round((ry + rh) * raw_h0))) - exp_y0)
        if (w, h) != (raw_w0, raw_h0) and abs(w - exp_w) <= 3 and abs(h - exp_h) <= 3:
            return img_bgr, {"enabled": True, "applied": False, "reason": "matches_cropped_size"}
        if (
            cal_w
            and cal_h
            and (w, h) == (int(cal_w), int(cal_h))
            and (int(cal_w), int(cal_h)) != (raw_w0, raw_h0)
        ):
            return img_bgr, {"enabled": True, "applied": False, "reason": "matches_cropped_canvas"}

    if raw_aspect > 0.05 and rh > 0.05:
        img_aspect = float(w) / float(h)
        expected_cropped_aspect = raw_aspect * (rw / rh)
        if (
            abs(expected_cropped_aspect - raw_aspect) > 0.12
            and abs(img_aspect - expected_cropped_aspect) < min(0.12, abs(img_aspect - raw_aspect) * 0.40)
        ):
            return img_bgr, {"enabled": True, "applied": False, "reason": "matches_cropped_aspect"}
        # Guard against applying a portrait crop_roi onto a landscape frame (or vice-versa / >1.22x aspect change)
        aspect_ratio_jump = max(img_aspect / raw_aspect, raw_aspect / img_aspect)
        if aspect_ratio_jump > 1.22:
            return img_bgr, {
                "enabled": True,
                "applied": False,
                "reason": "aspect_orientation_mismatch",
                "raw_frame_size": [w, h],
            }

    x0 = max(0, min(w - 16, int(round(rx * w))))
    y0 = max(0, min(h - 16, int(round(ry * h))))
    x1 = max(x0 + 16, min(w, int(round((rx + rw) * w))))
    y1 = max(y0 + 16, min(h, int(round((ry + rh) * h))))
    cropped = np.ascontiguousarray(img_bgr[y0:y1, x0:x1])
    return cropped, {
        "enabled": True,
        "applied": True,
        "pixel_box": [x0, y0, x1 - x0, y1 - y0],
        "raw_frame_size": [w, h],
    }


class VisionPipeline:
    """
    Privacy-first 3-stage parking occupancy vision funnel:
      0. Decode full camera frame strictly in RAM -> crop individual spots -> destroy raw image.
      1. Frame-diff filter (32x32 structural delta) to skip unchanged patches at 0 cost.
      2. Fast-lane local OpenCV analyzer (<15ms CPU): multi-directional gradient energy,
         interior dynamic-range spread, chromatic vehicle signature, cone/lock detector,
         and 24-column continuous gap meter for unmarked elastic strips.
      3. Slow-lane Multimodal VLM arbiter (OpenAI-compatible Vision API) for uncertain
         cases, obstacle semantics (`BLOCKED`), or elastic parallel parking strips.
    """

    def __init__(
        self,
        database: Database,
        fsm: SpotStateMachine,
        max_signature_entries: int = MAX_SIGNATURE_CACHE_ENTRIES,
        max_concurrent_decodes: int = MAX_CONCURRENT_IMAGE_DECODES,
        cache_budget_mb: Optional[float] = None,
    ) -> None:
        self.db = database
        self.fsm = fsm
        self._lock = threading.RLock()
        self.max_signature_entries = max(16, int(max_signature_entries))
        self.max_concurrent_decodes = max(1, int(max_concurrent_decodes))
        cfg_budget = float(config_manager.get().get("cache_budget_mb", DEFAULT_CACHE_BUDGET_MB))
        self.cache_budget_mb = max(4.0, float(cache_budget_mb if cache_budget_mb is not None else cfg_budget))
        self._decode_semaphore = threading.BoundedSemaphore(self.max_concurrent_decodes)
        self._vlm_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_VLM_CALLS)
        self._active_decodes = 0

        # Tier 1: Hot Camera Reference Template + Pre-computed ORB Features + Empty Stall Baselines
        self._template_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # Tier 2: Perceptual VLM Result Cache (keyed by camera_id::spot_id::model)
        self._vlm_result_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # Tier 3: 32x32 Grayscale Structural Frame-Diff Signatures
        self._prev_signatures: "OrderedDict[str, np.ndarray]" = OrderedDict()
        # Tier 4: Latest 320px pre-alignment grayscale check frame per camera for scheduled drift checks
        self._latest_check_frames: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # Tier 5: Per-spot longitudinal motion vector tracker, camera pose discontinuity tracker & Dynamic ROI cache
        self._motion_tracks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._camera_pose_tracks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._dynamic_roi_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._recommender: Any = None
        # Ephemeral RAM-only sandbox spots for benchmark testing on 0-spot cameras (never persisted to DB)
        self._sandbox_spots: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

        self.privacy_stats: Dict[str, Any] = {
            "frames_processed_in_ram": 0,
            "frames_destroyed_in_ram": 0,
            "raw_images_written_to_disk": 0,
            "spots_skipped_by_framediff": 0,
            "fast_lane_inferences": 0,
            "slow_lane_vlm_calls": 0,
            "first_frame_vlm_anchors": 0,
            "dynamic_roi_adjustments": 0,
            "vlm_cache_hits": 0,
            "template_cache_hits": 0,
            "template_cache_entries": 0,
            "vlm_cache_entries": 0,
            "signature_cache_entries": 0,
            "signature_cache_max_entries": self.max_signature_entries,
            "signature_cache_kb": 0.0,
            "max_signature_cache_kb": round(self.max_signature_entries * 1.0, 1),
            "cache_used_kb": 0.0,
            "cache_used_mb": 0.0,
            "cache_budget_mb": round(self.cache_budget_mb, 1),
            "active_decodes": 0,
            "max_concurrent_decodes": self.max_concurrent_decodes,
        }
        self.warm_caches()

    def set_recommender(self, recommender: Any) -> None:
        """Attach optional gate-entry soft-lock recommender for motion + incoming-car pre-judgment."""
        self._recommender = recommender

    def _compute_cache_bytes_locked(self) -> Tuple[int, int, int, int]:
        sig_bytes = sum(int(arr.nbytes) for arr in self._prev_signatures.values())
        chk_bytes = sum(int(item["gray"].nbytes) for item in self._latest_check_frames.values() if item.get("gray") is not None)
        tpl_bytes = sum(int(entry.get("byte_size", 0)) for entry in self._template_cache.values()) + chk_bytes
        vlm_bytes = sum(int(entry.get("byte_size", 0)) for entry in self._vlm_result_cache.values())
        smap_bytes = int(self.db.get_site_map_cache_bytes()) if hasattr(self.db, "get_site_map_cache_bytes") else 0
        return sig_bytes, tpl_bytes, vlm_bytes, smap_bytes

    def _enforce_cache_budget_locked(self) -> None:
        budget_bytes = int(self.cache_budget_mb * 1024 * 1024)
        while True:
            sig_b, tpl_b, vlm_b, smap_b = self._compute_cache_bytes_locked()
            if (sig_b + tpl_b + vlm_b + smap_b) <= budget_bytes:
                break
            if self._vlm_result_cache:
                self._vlm_result_cache.popitem(last=False)
            elif len(self._prev_signatures) > 32:
                self._prev_signatures.popitem(last=False)
            elif len(self._template_cache) > 1:
                self._template_cache.popitem(last=False)
            else:
                break

    def _sync_memory_stats_locked(self) -> None:
        sig_bytes, tpl_bytes, vlm_bytes, smap_bytes = self._compute_cache_bytes_locked()
        total_cache_bytes = sig_bytes + tpl_bytes + vlm_bytes + smap_bytes
        self.privacy_stats["frames_destroyed_in_ram"] = self.privacy_stats["frames_processed_in_ram"]
        self.privacy_stats["template_cache_entries"] = len(self._template_cache)
        self.privacy_stats["vlm_cache_entries"] = len(self._vlm_result_cache)
        self.privacy_stats["signature_cache_entries"] = len(self._prev_signatures)
        self.privacy_stats["signature_cache_max_entries"] = self.max_signature_entries
        self.privacy_stats["signature_cache_kb"] = round(sig_bytes / 1024.0, 1)
        self.privacy_stats["max_signature_cache_kb"] = round(self.max_signature_entries * 1.0, 1)
        self.privacy_stats["cache_used_kb"] = round(total_cache_bytes / 1024.0, 1)
        self.privacy_stats["cache_used_mb"] = round(total_cache_bytes / (1024.0 * 1024.0), 2)
        self.privacy_stats["cache_budget_mb"] = round(self.cache_budget_mb, 1)
        self.privacy_stats["active_decodes"] = self._active_decodes
        self.privacy_stats["max_concurrent_decodes"] = self.max_concurrent_decodes

    def warm_caches(self) -> None:
        """Pre-warm camera reference templates, ORB features, empty stall baselines, and site map into RAM."""
        try:
            self.db.get_site_map(include_backdrop=True)
            for cam_summary in self.db.list_cameras():
                if cam_summary.get("has_template"):
                    self._get_or_load_template_bundle(cam_summary["camera_id"])
            with self._lock:
                self._sync_memory_stats_locked()
        except Exception:
            pass

    @staticmethod
    def _anchor_to_cal_px(
        ap: Dict[str, Any], cal_w: int, cal_h: int
    ) -> Tuple[float, float]:
        """
        Convert an anchor dict `{x, y}` to canonical pixel coordinates `[0..cal_w, 0..cal_h]`.
        Transparently supports both normalized `[0..1]` and canonical pixel coordinates.
        """
        ax = float(ap.get("x", 0.0))
        ay = float(ap.get("y", 0.0))
        if 0.0 <= ax <= 1.05 and 0.0 <= ay <= 1.05 and (cal_w > 4 or cal_h > 4):
            return ax * float(cal_w), ay * float(cal_h)
        return ax, ay

    @classmethod
    def _build_car_and_foliage_rigid_mask_cal(
        cls,
        bgr_cal: np.ndarray,
        spots: List[Dict[str, Any]],
        anchor_points: Optional[List[Dict[str, Any]]] = None,
    ) -> np.ndarray:
        """
        Build a canonical `(cal_h, cal_w)` uint8 mask that suppresses BOTH swaying green foliage
        AND parked vehicle interiors (shrunk ground polygons + upper 3D cabin envelopes), while
        preserving rigid stall divider lines, curbs, and fixed positioning anchors (`A1..An`).
        """
        cal_h, cal_w = bgr_cal.shape[:2]
        non_foliage = build_non_foliage_rigid_mask(bgr_cal, min_valid_ratio=0.22)
        allow = np.full((cal_h, cal_w), 255, dtype=np.uint8)

        for sp in spots:
            poly = sp.get("polygon") or []
            if len(poly) >= 3:
                pts = np.asarray(poly, dtype=np.float32)
                ctr = np.mean(pts, axis=0)
                shrunk = (pts * 0.62 + ctr * 0.38).astype(np.int32)
                cv2.fillPoly(allow, [shrunk], 0)

            if sp.get("spot_type") != "elastic_strip":
                vpoly = sp.get("vehicle_polygon")
                if not isinstance(vpoly, list) or len(vpoly) < 3:
                    if len(poly) >= 3:
                        vpoly = derive_vehicle_envelope_polygon(poly, cal_w, cal_h)
                if isinstance(vpoly, list) and len(vpoly) >= 3:
                    vpts = np.asarray(vpoly, dtype=np.float32)
                    vctr = np.mean(vpts, axis=0)
                    vshrunk = (vpts * 0.56 + vctr * 0.44).astype(np.int32)
                    cv2.fillPoly(allow, [vshrunk], 0)

        # Boost fixed positioning anchor neighborhoods so rigid curb/corner landmarks always emit keypoints
        if anchor_points:
            rad = max(18, int(round(min(cal_w, cal_h) * 0.09)))
            for ap in anchor_points:
                try:
                    ax_px, ay_px = cls._anchor_to_cal_px(ap, cal_w, cal_h)
                    cv2.circle(allow, (int(round(ax_px)), int(round(ay_px))), rad, 255, -1)
                except Exception:
                    continue

        combined = cv2.bitwise_and(allow, non_foliage)
        if float(np.mean(combined > 0)) < 0.14:
            return non_foliage
        return combined

    def _get_or_load_template_bundle(self, camera_id: str) -> Optional[Dict[str, Any]]:
        cam_meta = self.db.get_camera(camera_id, include_blobs=False)
        if not cam_meta:
            return None
        cal_ts = float((cam_meta.get("template_meta") or {}).get("calibrated_at") or 0.0)
        anchors_sig = json.dumps(cam_meta.get("anchor_points") or [], sort_keys=True)
        with self._lock:
            cached = self._template_cache.get(camera_id)
            if (
                cached is not None
                and cached.get("calibrated_at") == cal_ts
                and cached.get("anchors_sig") == anchors_sig
                and cam_meta.get("has_template")
            ):
                self._template_cache.move_to_end(camera_id)
                self.privacy_stats["template_cache_hits"] += 1
                return {**cam_meta, **cached}

        cam_full = self.db.get_camera(camera_id, include_blobs=True)
        if not cam_full:
            return None

        spots_with_sigs = self.db.list_spots(camera_id=camera_id, include_signatures=True)
        spot_ref_sigs: Dict[str, np.ndarray] = {}
        for sp in spots_with_sigs:
            b64_sig = sp.get("ref_free_sig_b64")
            if b64_sig:
                try:
                    raw_sig = base64.b64decode(b64_sig)
                    if len(raw_sig) == 1024:
                        spot_ref_sigs[sp["spot_id"]] = np.frombuffer(raw_sig, dtype=np.uint8).reshape((32, 32)).copy()
                except Exception:
                    pass

        ref_small: Optional[np.ndarray] = None
        ref_rigid_mask: Optional[np.ndarray] = None
        ref_strip_edge: Optional[np.ndarray] = None
        ref_strip_lab: Optional[np.ndarray] = None
        orb_kp_pts: Optional[np.ndarray] = None
        orb_des: Optional[np.ndarray] = None
        sift_cal_pts: Optional[np.ndarray] = None
        sift_des: Optional[np.ndarray] = None
        sift_is_ground: Optional[np.ndarray] = None
        sift_collar_pts: Optional[np.ndarray] = None
        sift_collar_des: Optional[np.ndarray] = None
        tpl_gray_b64 = cam_full.get("template_gray_b64") or ""
        tpl_jpeg_b64 = cam_full.get("template_jpeg_b64") or ""
        ff_jpeg_b64 = cam_full.get("full_frame_jpeg_b64") or ""
        cal_w = max(32, int(cam_full.get("image_width") or 1280))
        cal_h = max(32, int(cam_full.get("image_height") or 720))
        anchor_pts = cam_full.get("anchor_points") or []
        crop_cfg = cam_full.get("crop_roi") or {}

        sift_pts_list: List[np.ndarray] = []
        sift_des_list: List[np.ndarray] = []
        sift_gnd_list: List[np.ndarray] = []
        sift_detector = (
            cv2.SIFT_create(nfeatures=3600, nOctaveLayers=5, contrastThreshold=0.016, edgeThreshold=14)
            if hasattr(cv2, "SIFT_create")
            else None
        )

        if tpl_gray_b64:
            try:
                ref_bytes = base64.b64decode(tpl_gray_b64)
                ref_small = cv2.imdecode(np.frombuffer(ref_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                if ref_small is not None:
                    rh_s, rw_s = ref_small.shape[:2]
                    if tpl_jpeg_b64:
                        try:
                            raw_jpg_b64 = tpl_jpeg_b64.split(",", 1)[1] if "," in tpl_jpeg_b64 else tpl_jpeg_b64
                            jpg_bytes = base64.b64decode(raw_jpg_b64)
                            tpl_bgr = cv2.imdecode(np.frombuffer(jpg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if tpl_bgr is not None:
                                tpl_cal_bgr = cv2.resize(tpl_bgr, (cal_w, cal_h), interpolation=cv2.INTER_AREA)
                                del tpl_bgr
                                cal_rigid_mask = self._build_car_and_foliage_rigid_mask_cal(
                                    tpl_cal_bgr, spots_with_sigs, anchor_pts
                                )
                                if sift_detector is not None:
                                    tpl_cal_gray = cv2.cvtColor(tpl_cal_bgr, cv2.COLOR_BGR2GRAY)
                                    skps, sdes = sift_detector.detectAndCompute(tpl_cal_gray, cal_rigid_mask)
                                    if skps and sdes is not None and len(skps) >= 12:
                                        order = np.argsort([-kp.response for kp in skps])[:1100]
                                        sift_pts_list.append(
                                            np.float32([skps[int(i)].pt for i in order])
                                        )
                                        sift_des_list.append(sdes[order].astype(np.float32))
                                        sift_gnd_list.append(np.ones(len(order), dtype=np.uint8))
                                    del tpl_cal_gray

                                tpl_bgr_s = cv2.resize(tpl_cal_bgr, (rw_s, rh_s), interpolation=cv2.INTER_AREA)
                                del tpl_cal_bgr
                                ref_rigid_mask = cv2.resize(
                                    cal_rigid_mask, (rw_s, rh_s), interpolation=cv2.INTER_NEAREST
                                )
                                th_160 = max(36, int(round(160.0 * rh_s / max(1, rw_s))))
                                strip_bgr = cv2.resize(tpl_bgr_s, (160, th_160), interpolation=cv2.INTER_AREA)
                                ref_strip_lab = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2LAB)
                                del strip_bgr
                                del tpl_bgr_s
                        except Exception:
                            ref_rigid_mask = None

                    th_160 = max(36, int(round(160.0 * rh_s / max(1, rw_s))))
                    ref_g160 = cv2.resize(ref_small, (160, th_160), interpolation=cv2.INTER_AREA)
                    gx160 = np.abs(cv2.Sobel(ref_g160, cv2.CV_32F, 1, 0, ksize=3))
                    gy160 = np.abs(cv2.Sobel(ref_g160, cv2.CV_32F, 0, 1, ksize=3))
                    edge160 = np.sqrt(gx160 * gx160 + gy160 * gy160)
                    if ref_rigid_mask is not None:
                        rm160 = cv2.resize(ref_rigid_mask, (160, th_160), interpolation=cv2.INTER_NEAREST) > 0
                        edge160 = np.where(rm160, edge160, edge160 * 0.15)
                    ref_strip_edge = np.clip(edge160, 0.0, 255.0).astype(np.uint8)

                    orb = cv2.ORB_create(nfeatures=420)
                    kps, des = orb.detectAndCompute(ref_small, ref_rigid_mask)
                    if (not kps or des is None or len(kps) < 20) and ref_rigid_mask is not None:
                        kps, des = orb.detectAndCompute(ref_small, None)
                    if kps and des is not None and len(kps) >= 20:
                        orb_kp_pts = np.float32([kp.pt for kp in kps])
                        orb_des = des
            except Exception:
                ref_small = None

        # Extract wide-FOV rigid SIFT keypoints from saved full-frame context (if crop_roi enabled)
        # mapped into canonical `(cal_w, cal_h)` coordinates, PLUS a high-density Stage-2 ground-collar
        # SIFT pool around the stall perimeter so ultra-wide zoom + 100% car / tall box-van occlusion still locks!
        if sift_detector is not None and ff_jpeg_b64 and isinstance(crop_cfg, dict) and crop_cfg.get("enabled"):
            try:
                raw_ff_b64 = ff_jpeg_b64.split(",", 1)[1] if "," in ff_jpeg_b64 else ff_jpeg_b64
                ff_bytes = base64.b64decode(raw_ff_b64)
                ff_bgr = cv2.imdecode(np.frombuffer(ff_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                if ff_bgr is not None:
                    fh, fw = ff_bgr.shape[:2]
                    rx = float(crop_cfg.get("x", 0.0))
                    ry = float(crop_cfg.get("y", 0.0))
                    rw = max(0.05, float(crop_cfg.get("w", 1.0)))
                    rh = max(0.05, float(crop_cfg.get("h", 1.0)))

                    ff_non_fol = build_non_foliage_rigid_mask(ff_bgr, min_valid_ratio=0.22)
                    ff_band = np.full((fh, fw), 255, dtype=np.uint8)
                    # Exclude sky / distant background horizon strip at top 5%
                    ff_band[: max(1, int(round(0.05 * fh))), :] = 0

                    # Mask parked cars & tall vehicle roofs inside the cropped stall region of full_frame
                    for sp in spots_with_sigs:
                        poly = sp.get("polygon") or []
                        if len(poly) >= 3:
                            pts_ff = np.asarray(
                                [
                                    [
                                        (rx + (float(p[0]) / cal_w) * rw) * fw,
                                        (ry + (float(p[1]) / cal_h) * rh) * fh,
                                    ]
                                    for p in poly
                                ],
                                dtype=np.float32,
                            )
                            ctr_ff = np.mean(pts_ff, axis=0)
                            cv2.fillPoly(ff_band, [(pts_ff * 0.78 + ctr_ff * 0.22).astype(np.int32)], 0)
                        if sp.get("spot_type") != "elastic_strip":
                            vp = sp.get("vehicle_polygon")
                            if not isinstance(vp, list) or len(vp) < 3:
                                if len(poly) >= 3:
                                    vp = derive_vehicle_envelope_polygon(poly, cal_w, cal_h)
                            if isinstance(vp, list) and len(vp) >= 3:
                                vpts_ff = np.asarray(
                                    [
                                        [
                                            (rx + (float(p[0]) / cal_w) * rw) * fw,
                                            (ry + (float(p[1]) / cal_h) * rh) * fh,
                                        ]
                                        for p in vp
                                    ],
                                    dtype=np.float32,
                                )
                                vctr_ff = np.mean(vpts_ff, axis=0)
                                cv2.fillPoly(ff_band, [(vpts_ff * 0.74 + vctr_ff * 0.26).astype(np.int32)], 0)

                    rad_ff = max(14, int(round(min(fw * rw, fh * rh) * 0.08)))
                    for ap in anchor_pts:
                        try:
                            ax_cal, ay_cal = self._anchor_to_cal_px(ap, cal_w, cal_h)
                            ax_f = int(round((rx + (ax_cal / float(cal_w)) * rw) * fw))
                            ay_f = int(round((ry + (ay_cal / float(cal_h)) * rh) * fh))
                            cv2.circle(ff_band, (ax_f, ay_f), rad_ff, 255, -1)
                        except Exception:
                            continue

                    ff_mask = cv2.bitwise_and(ff_band, ff_non_fol)
                    ff_gray = cv2.cvtColor(ff_bgr, cv2.COLOR_BGR2GRAY)

                    # Stage-2 Canonical Ground Collar SIFT pool:
                    # Warp `ff_bgr` directly into `[-0.18*cal_w..1.18*cal_w, -0.35*cal_h..1.35*cal_h]`
                    # so road curbs, stone bollards, walkways & building plinth refine ground alignment sub-pixel
                    # even when all stalls (including tall box-body medium vans) are occupied!
                    pad_cx = int(round(0.18 * cal_w))
                    pad_cy = int(round(0.35 * cal_h))
                    col_w = cal_w + 2 * pad_cx
                    col_h = cal_h + 2 * pad_cy
                    H_col_to_ff = np.array(
                        [
                            [(rw * fw) / float(cal_w), 0.0, rx * fw - pad_cx * (rw * fw) / float(cal_w)],
                            [0.0, (rh * fh) / float(cal_h), ry * fh - pad_cy * (rh * fh) / float(cal_h)],
                            [0.0, 0.0, 1.0],
                        ],
                        dtype=np.float64,
                    )
                    ff_collar = cv2.warpPerspective(
                        ff_bgr,
                        H_col_to_ff,
                        (col_w, col_h),
                        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_REPLICATE,
                    )
                    del ff_bgr
                    c_non_fol = build_non_foliage_rigid_mask(ff_collar, min_valid_ratio=0.22)
                    c_mask = c_non_fol.copy()
                    for sp in spots_with_sigs:
                        poly = sp.get("polygon") or []
                        if len(poly) >= 3:
                            pts_c = np.asarray(
                                [[float(p[0]) + pad_cx, float(p[1]) + pad_cy] for p in poly],
                                dtype=np.float32,
                            )
                            ctr_c = np.mean(pts_c, axis=0)
                            cv2.fillPoly(c_mask, [(pts_c * 0.88 + ctr_c * 0.12).astype(np.int32)], 0)
                        if sp.get("spot_type") != "elastic_strip":
                            vp = sp.get("vehicle_polygon")
                            if not isinstance(vp, list) or len(vp) < 3:
                                if len(poly) >= 3:
                                    vp = derive_vehicle_envelope_polygon(poly, cal_w, cal_h)
                            if isinstance(vp, list) and len(vp) >= 3:
                                vpts_c = np.asarray(
                                    [[float(p[0]) + pad_cx, float(p[1]) + pad_cy] for p in vp],
                                    dtype=np.float32,
                                )
                                vctr_c = np.mean(vpts_c, axis=0)
                                cv2.fillPoly(c_mask, [(vpts_c * 0.82 + vctr_c * 0.18).astype(np.int32)], 0)
                    rad_c = max(16, int(round(min(cal_w, cal_h) * 0.075)))
                    for ap in anchor_pts:
                        try:
                            ax_cal, ay_cal = self._anchor_to_cal_px(ap, cal_w, cal_h)
                            cv2.circle(
                                c_mask,
                                (int(round(ax_cal + pad_cx)), int(round(ay_cal + pad_cy))),
                                rad_c,
                                255,
                                -1,
                            )
                        except Exception:
                            continue
                    c_gray = cv2.cvtColor(ff_collar, cv2.COLOR_BGR2GRAY)
                    del ff_collar
                    sift_collar_det = cv2.SIFT_create(
                        nfeatures=2200, nOctaveLayers=5, contrastThreshold=0.015, edgeThreshold=14
                    )
                    ckps, cdes = sift_collar_det.detectAndCompute(c_gray, c_mask)
                    del c_gray
                    if ckps and cdes is not None and len(ckps) >= 16:
                        corder = np.argsort([-kp.response for kp in ckps])[:1500]
                        sift_collar_pts = np.float32(
                            [[ckps[int(i)].pt[0] - pad_cx, ckps[int(i)].pt[1] - pad_cy] for i in corder]
                        )
                        sift_collar_des = _to_rootsift_descriptors(cdes[corder].astype(np.float32))

                    # Stage-1 Full-Frame SIFT with balanced quota (950 ground-level + 650 upper building)
                    sift_ff = cv2.SIFT_create(
                        nfeatures=3600, nOctaveLayers=5, contrastThreshold=0.016, edgeThreshold=14
                    )
                    fkps, fdes = sift_ff.detectAndCompute(ff_gray, ff_mask)
                    del ff_gray
                    if fkps and fdes is not None and len(fkps) >= 12:
                        gnd_y_cut = (ry - 0.16 * rh) * float(fh)
                        gnd_idx = [i for i, kp in enumerate(fkps) if kp.pt[1] >= gnd_y_cut]
                        arch_idx = [i for i, kp in enumerate(fkps) if kp.pt[1] < gnd_y_cut]
                        gnd_sel = sorted(gnd_idx, key=lambda i: -fkps[i].response)[:950]
                        arch_sel = sorted(arch_idx, key=lambda i: -fkps[i].response)[:650]
                        forder = gnd_sel + arch_sel
                        mapped_cal_pts = np.float32(
                            [
                                [
                                    ((fkps[int(i)].pt[0] / float(fw)) - rx) / rw * cal_w,
                                    ((fkps[int(i)].pt[1] / float(fh)) - ry) / rh * cal_h,
                                ]
                                for i in forder
                            ]
                        )
                        sift_pts_list.append(mapped_cal_pts)
                        sift_des_list.append(fdes[forder].astype(np.float32))
                        sift_gnd_list.append(
                            np.array(
                                [1] * len(gnd_sel) + [0] * len(arch_sel),
                                dtype=np.uint8,
                            )
                        )
            except Exception:
                pass

        if sift_pts_list and sift_des_list:
            sift_cal_pts = np.vstack(sift_pts_list)
            sift_des = _to_rootsift_descriptors(np.vstack(sift_des_list))
            sift_is_ground = np.concatenate(sift_gnd_list) if sift_gnd_list else None

        if cam_full.get("has_template") or spot_ref_sigs:
            byte_sz = (
                len(tpl_jpeg_b64)
                + (int(ref_small.nbytes) if ref_small is not None else 0)
                + (int(ref_rigid_mask.nbytes) if ref_rigid_mask is not None else 0)
                + (int(ref_strip_edge.nbytes) if ref_strip_edge is not None else 0)
                + (int(ref_strip_lab.nbytes) if ref_strip_lab is not None else 0)
                + (int(orb_kp_pts.nbytes) if orb_kp_pts is not None else 0)
                + (int(orb_des.nbytes) if orb_des is not None else 0)
                + (int(sift_cal_pts.nbytes) if sift_cal_pts is not None else 0)
                + (int(sift_des.nbytes) if sift_des is not None else 0)
                + (int(sift_is_ground.nbytes) if sift_is_ground is not None else 0)
                + (int(sift_collar_pts.nbytes) if sift_collar_pts is not None else 0)
                + (int(sift_collar_des.nbytes) if sift_collar_des is not None else 0)
                + sum(int(s.nbytes) for s in spot_ref_sigs.values())
            )
            bundle = {
                "calibrated_at": cal_ts,
                "anchors_sig": anchors_sig,
                "template_jpeg_b64": tpl_jpeg_b64,
                "ref_small": ref_small,
                "ref_rigid_mask": ref_rigid_mask,
                "ref_strip_edge": ref_strip_edge,
                "ref_strip_lab": ref_strip_lab,
                "orb_kp_pts": orb_kp_pts,
                "orb_des": orb_des,
                "sift_cal_pts": sift_cal_pts,
                "sift_des": sift_des,
                "sift_is_ground": sift_is_ground,
                "sift_collar_pts": sift_collar_pts,
                "sift_collar_des": sift_collar_des,
                "spot_ref_sigs": spot_ref_sigs,
                "byte_size": byte_sz,
            }
            with self._lock:
                self._template_cache[camera_id] = bundle
                self._template_cache.move_to_end(camera_id)
                while len(self._template_cache) > MAX_TEMPLATE_CACHE_CAMERAS:
                    self._template_cache.popitem(last=False)
                self._enforce_cache_budget_locked()
                self._sync_memory_stats_locked()
            return {**cam_meta, **bundle}

        return {
            **cam_meta,
            "template_jpeg_b64": tpl_jpeg_b64,
            "ref_small": None,
            "ref_rigid_mask": None,
            "ref_strip_edge": None,
            "ref_strip_lab": None,
            "orb_kp_pts": None,
            "orb_des": None,
            "sift_cal_pts": None,
            "sift_des": None,
            "sift_is_ground": None,
            "sift_collar_pts": None,
            "sift_collar_des": None,
            "spot_ref_sigs": spot_ref_sigs,
        }

    def get_cached_camera_template(self, camera_id: str) -> Optional[Dict[str, Any]]:
        bundle = self._get_or_load_template_bundle(camera_id)
        if not bundle:
            return None
        return {
            "camera_id": camera_id,
            "has_template": bool(bundle.get("has_template")),
            "has_vehicle_template": bool(bundle.get("has_vehicle_template")),
            "has_full_frame": bool(bundle.get("has_full_frame")),
            "image_width": bundle["image_width"],
            "image_height": bundle["image_height"],
            "crop_roi": bundle.get("crop_roi"),
            "anchor_points": bundle.get("anchor_points") or [],
            "anchor_points_custom": bool(bundle.get("anchor_points_custom")),
            "calib_schedule": bundle.get("calib_schedule"),
            "calib_health": bundle.get("calib_health"),
            "template_meta": bundle.get("template_meta"),
            "vehicle_template_meta": bundle.get("vehicle_template_meta"),
            "template_jpeg_b64": bundle.get("template_jpeg_b64") or "",
        }

    def _lookup_vlm_cache(
        self,
        cache_key: str,
        patch_bgr_32: np.ndarray,
        ttl_seconds: float,
        now_wall: float,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._vlm_result_cache.get(cache_key)
            if entry is None:
                return None
            if (now_wall - float(entry["ts"])) > ttl_seconds:
                self._vlm_result_cache.pop(cache_key, None)
                self._sync_memory_stats_locked()
                return None
            cached_patch = entry["patch_bgr_32"]
            diff = float(np.mean(np.abs(patch_bgr_32.astype(np.float32) - cached_patch.astype(np.float32))) / 255.0)
            if diff > 0.020:
                return None
            self._vlm_result_cache.move_to_end(cache_key)
            self.privacy_stats["vlm_cache_hits"] += 1
            return dict(entry["obs"])

    def _store_vlm_cache(
        self,
        cache_key: str,
        patch_bgr_32: np.ndarray,
        obs: Dict[str, Any],
        now_wall: float,
    ) -> None:
        with self._lock:
            p_copy = patch_bgr_32.copy()
            obs_copy = dict(obs)
            self._vlm_result_cache[cache_key] = {
                "patch_bgr_32": p_copy,
                "obs": obs_copy,
                "ts": now_wall,
                "byte_size": int(p_copy.nbytes) + 384,
            }
            self._vlm_result_cache.move_to_end(cache_key)
            while len(self._vlm_result_cache) > MAX_VLM_CACHE_ENTRIES:
                self._vlm_result_cache.popitem(last=False)
            self._enforce_cache_budget_locked()
            self._sync_memory_stats_locked()

    def clear_vlm_cache(self) -> None:
        with self._lock:
            self._vlm_result_cache.clear()
            self._sync_memory_stats_locked()

    def get_privacy_stats(self) -> Dict[str, Any]:
        with self._lock:
            self._sync_memory_stats_locked()
            return dict(self.privacy_stats)

    @contextmanager
    def decode_guard(self, timeout: float = 15.0) -> Iterator[None]:
        acquired = self._decode_semaphore.acquire(timeout=timeout)
        if not acquired:
            raise RuntimeError("当前并发图像解码队列繁忙，请稍后重试（内存防爆保护已生效）")
        with self._lock:
            self._active_decodes += 1
            self._sync_memory_stats_locked()
        try:
            yield
        finally:
            with self._lock:
                self._active_decodes = max(0, self._active_decodes - 1)
                self._sync_memory_stats_locked()
            self._decode_semaphore.release()

    def _get_signature_lru(self, key: str) -> Optional[np.ndarray]:
        with self._lock:
            sig = self._prev_signatures.get(key)
            if sig is not None:
                self._prev_signatures.move_to_end(key)
            return sig

    def _put_signature_lru(self, key: str, sig: np.ndarray) -> None:
        with self._lock:
            compact_sig = np.asarray(sig, dtype=np.uint8)
            if compact_sig.shape != (32, 32):
                compact_sig = cv2.resize(compact_sig, (32, 32))
            else:
                compact_sig = compact_sig.copy()
            self._prev_signatures[key] = compact_sig
            self._prev_signatures.move_to_end(key)
            while len(self._prev_signatures) > self.max_signature_entries:
                self._prev_signatures.popitem(last=False)
            self._enforce_cache_budget_locked()
            self._sync_memory_stats_locked()

    def _cache_latest_check_frame(self, camera_id: str, frame_bgr: np.ndarray, ts: float) -> None:
        h, w = frame_bgr.shape[:2]
        if h < 16 or w < 16:
            return
        scale = min(1.0, 320.0 / float(max(w, h)))
        nw = max(32, int(round(w * scale)))
        nh = max(32, int(round(h * scale)))
        gray_small = cv2.resize(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (nw, nh), interpolation=cv2.INTER_AREA)
        with self._lock:
            self._latest_check_frames[camera_id] = {
                "gray": gray_small,
                "captured_at": float(ts),
                "frame_size": [w, h],
            }
            self._latest_check_frames.move_to_end(camera_id)
            while len(self._latest_check_frames) > MAX_LATEST_CHECK_FRAMES:
                self._latest_check_frames.popitem(last=False)
            self._enforce_cache_budget_locked()
            self._sync_memory_stats_locked()

    def clear_frame_cache(self, camera_id: Optional[str] = None) -> None:
        with self._lock:
            if camera_id is None:
                self._prev_signatures.clear()
                self._template_cache.clear()
                self._vlm_result_cache.clear()
                self._latest_check_frames.clear()
                self._motion_tracks.clear()
                self._camera_pose_tracks.clear()
                self._dynamic_roi_cache.clear()
            else:
                prefix = f"{camera_id}::"
                for k in [k for k in self._prev_signatures if k.startswith(prefix)]:
                    self._prev_signatures.pop(k, None)
                for k in [k for k in self._vlm_result_cache if k.startswith(prefix)]:
                    self._vlm_result_cache.pop(k, None)
                for k in [k for k in self._motion_tracks if k.startswith(prefix)]:
                    self._motion_tracks.pop(k, None)
                for k in [k for k in self._dynamic_roi_cache if k.startswith(prefix)]:
                    self._dynamic_roi_cache.pop(k, None)
                self._camera_pose_tracks.pop(camera_id, None)
                self._template_cache.pop(camera_id, None)
                self._latest_check_frames.pop(camera_id, None)
            self._sync_memory_stats_locked()

    def prime_reference_signatures(self, camera_id: str, spot_sigs: Dict[str, np.ndarray]) -> None:
        for spot_id, sig in spot_sigs.items():
            if isinstance(sig, np.ndarray) and sig.shape == (32, 32):
                self._put_signature_lru(f"{camera_id}::{spot_id}", sig)
        # Refresh hot template bundle in RAM
        self._get_or_load_template_bundle(camera_id)

    def _collect_gate_entry_context(self, zone_id: str, now_wall: float) -> Dict[str, Any]:
        """Query active gate-entry soft-locks to fuse incoming-vehicle traffic with stall motion."""
        zone_in_transit = 0
        community_in_transit = 0
        recent_units: List[str] = []
        if self._recommender is not None and hasattr(self._recommender, "list_active_locks"):
            try:
                locks = self._recommender.list_active_locks(now=now_wall)
                community_in_transit = len(locks)
                for lk in locks:
                    if str(lk.get("zone_id")) == str(zone_id):
                        zone_in_transit += 1
                        uname = str(lk.get("unit_name") or "").strip()
                        if uname and uname not in recent_units:
                            recent_units.append(uname)
            except Exception:
                pass
        return {
            "zone_in_transit": zone_in_transit,
            "community_in_transit": community_in_transit,
            "recent_units": recent_units[:3],
        }

    def _evaluate_spot_motion_and_gate(
        self,
        spot_key: str,
        prev_committed_status: str,
        pending_status: Optional[str],
        observed_status: str,
        obstacle_type: str,
        metrics: Dict[str, Any],
        gate_ctx: Dict[str, Any],
        now_ts: float,
        anchor_localized: bool = False,
        scene_discontinuity: bool = False,
        confidence: float = 0.0,
        vlm_verified: bool = False,
        vlm_parking_pose: str = "",
    ) -> Dict[str, Any]:
        """
        Track per-spot longitudinal & lateral foreground centroid velocity across frames
        (`occ_cy`: 0=deep stall curb, 1=front road mouth), evaluate anchor-localized stall pose
        (`PARKED_IN_SLOT` vs `CLEAR_EMPTY` vs `MANEUVERING_AT_BOUNDARY`), suppress bogus velocity
        across unrelated static photos / camera pose jumps, and fuse with gate-entry soft-locks.
        """
        occ_cy = float(metrics.get("occ_cy", 0.50))
        occ_cx = float(metrics.get("occ_cx", 0.50))
        rear_zone_occ = float(metrics.get("rear_zone_occ", 0.35))
        mouth_zone_occ = float(metrics.get("mouth_zone_occ", 0.0))
        veh_3d_occ = float(metrics.get("veh_3d_score", 0.0))
        neighbor_bleed = bool(metrics.get("neighbor_bleed_suspected", False))
        colored_paving = bool(metrics.get("colored_paving_filtered", False))
        has_fg = bool(
            observed_status != "FREE"
            or obstacle_type in ("pedestrian", "movable", "immovable")
            or float(metrics.get("car_score", 0.0)) >= 0.30
        )

        with self._lock:
            prev_track = self._motion_tracks.get(spot_key)
            self._motion_tracks[spot_key] = {
                "ts": now_ts,
                "occ_cy": occ_cy,
                "occ_cx": occ_cx,
                "has_fg": has_fg,
                "status": observed_status,
            }
            self._motion_tracks.move_to_end(spot_key)
            while len(self._motion_tracks) > MAX_MOTION_TRACK_ENTRIES:
                self._motion_tracks.popitem(last=False)

        zone_in_transit = int(gate_ctx.get("zone_in_transit", 0))
        community_in_transit = int(gate_ctx.get("community_in_transit", 0))
        unit_suffix = (
            f"（门岗刚登记驶向{gate_ctx['recent_units'][0]}）"
            if gate_ctx.get("recent_units")
            else ""
        )

        dy = 0.0
        dx = 0.0
        dt = 0.0
        direction = "idle"
        intent_code = "none"
        intent_label = ""

        # Suppress cross-frame centroid velocity across unrelated static test photos or camera angle jumps
        motion_dy_thr = 0.075 if anchor_localized else 0.040
        if scene_discontinuity:
            if prev_track is not None:
                dt = max(0.0, now_ts - float(prev_track["ts"]))
            direction = "stationary" if has_fg else "idle"
        elif prev_track is not None and 0.0 < (now_ts - float(prev_track["ts"])) <= 15.0:
            dt = max(0.01, now_ts - float(prev_track["ts"]))
            if has_fg and bool(prev_track.get("has_fg")):
                dy = occ_cy - float(prev_track["occ_cy"])
                dx = occ_cx - float(prev_track["occ_cx"])
                if abs(dx) >= 0.13 and abs(dx) > abs(dy) * 1.35 and observed_status == "FREE":
                    direction = "passing_by"
                elif dy <= -motion_dy_thr:
                    direction = "moving_in"   # Moving deeper toward rear curb / wheel stopper
                elif dy >= motion_dy_thr:
                    direction = "moving_out"  # Moving outward toward road aisle
                else:
                    direction = "stationary"
            elif has_fg and not bool(prev_track.get("has_fg")):
                direction = "entering_initial"
            elif (not has_fg) and bool(prev_track.get("has_fg")):
                direction = "clearing_initial"
        elif has_fg:
            direction = "entering_initial" if prev_committed_status == "FREE" else "stationary"

        # Anchor-localized vehicle pose evaluation inside canonical stall geometry:
        # Distinguishes a stationary parked car / fully clear stall from a car maneuvering at the front mouth.
        pose_norm = str(vlm_parking_pose or "").strip().lower()
        if observed_status == "OCCUPIED" or obstacle_type == "vehicle":
            if pose_norm == "maneuvering" or (
                not vlm_verified
                and (
                    (occ_cy >= 0.66 and rear_zone_occ < 0.12 and veh_3d_occ < 0.48)
                    or neighbor_bleed
                )
            ):
                localized_pose = "MANEUVERING_AT_BOUNDARY"
            else:
                localized_pose = "PARKED_IN_SLOT"
        elif observed_status == "BLOCKED":
            localized_pose = "FIXED_OBSTACLE"
        else:
            if pose_norm == "maneuvering" or (
                not vlm_verified
                and not colored_paving
                and mouth_zone_occ >= 0.24
                and prev_committed_status == "OCCUPIED"
            ):
                localized_pose = "MANEUVERING_AT_BOUNDARY"
            else:
                localized_pose = "CLEAR_EMPTY"

        if vlm_verified:
            high_conf_verified = confidence >= 0.82
        elif observed_status == "OCCUPIED":
            high_conf_verified = confidence >= 0.78 and not neighbor_bleed
        elif observed_status == "FREE":
            high_conf_verified = confidence >= 0.85 and not neighbor_bleed and obstacle_type == "none"
        else:
            high_conf_verified = confidence >= 0.82

        position_verified_stationary = bool(
            anchor_localized
            and localized_pose in ("PARKED_IN_SLOT", "CLEAR_EMPTY", "FIXED_OBSTACLE")
            and high_conf_verified
            and direction not in ("moving_in", "moving_out", "passing_by")
        )

        if observed_status == "OCCUPIED" or obstacle_type == "vehicle":
            if direction == "moving_in":
                if zone_in_transit > 0:
                    intent_code = "parking_in_gate_matched"
                    intent_label = f"车辆正向位内深处泊入 + 本片区有{zone_in_transit}辆进门在途车{unit_suffix} -> 预判正在停车入位"
                elif community_in_transit > 0:
                    intent_code = "parking_in_community_active"
                    intent_label = f"车辆正向位内推进 + 小区有{community_in_transit}辆刚进门车辆 -> 预判正在泊入车位"
                else:
                    intent_code = "parking_in"
                    intent_label = "检测到车身纵向朝车位深处推进 -> 预判正在倒车/前进泊入"
            elif direction == "moving_out":
                if prev_committed_status == "OCCUPIED":
                    intent_code = "exiting_stall"
                    intent_label = "原停放车辆朝通道外侧移动 -> 预判正在驶离出库（待完全驶出后释放空位）"
                elif zone_in_transit > 0:
                    intent_code = "maneuvering_adjust"
                    intent_label = f"车辆向外微调车头 + 本片区有{zone_in_transit}辆进门在途车 -> 预判揉库调姿泊车中"
                else:
                    intent_code = "reversing_or_exiting"
                    intent_label = "检测到车身朝车道方向移动 -> 预判正在揉库调整或准备驶出"
            elif position_verified_stationary:
                if prev_committed_status != "OCCUPIED":
                    intent_code = "anchor_parked_verified"
                    intent_label = "锚点定位车身已稳停于车位标定框内（无跨界位移，直接确认占用）"
                else:
                    intent_code = "anchor_parked_steady"
                    intent_label = ""
            elif direction == "entering_initial":
                if zone_in_transit > 0:
                    intent_code = "gate_arrival_entering"
                    intent_label = f"首帧捕获得车体进入 + 本片区有{zone_in_transit}辆进门在途车{unit_suffix} -> 高度预判前车到达入位"
                elif community_in_transit > 0:
                    intent_code = "community_arrival_entering"
                    intent_label = f"首帧检测到车辆入位 + 门岗{community_in_transit}辆在途车 -> 预判前车入位停靠"
                elif occ_cy >= 0.56 or localized_pose == "MANEUVERING_AT_BOUNDARY":
                    intent_code = "nose_tail_at_mouth"
                    intent_label = "车身目前位于车位前口，正在驶入或短暂停靠（已悲观锁位防误导）"
                else:
                    intent_code = "parked_in_place"
                    intent_label = "车辆已停入车位中后部，等待3秒二次确认锁死"
            elif direction == "stationary" and pending_status == "OCCUPIED":
                intent_code = "parked_steady"
                intent_label = "车身位置静止稳定，已停稳待满3秒落库"
        elif observed_status == "FREE" and prev_committed_status == "OCCUPIED":
            if position_verified_stationary:
                intent_code = "anchor_clear_verified"
                intent_label = "锚点定位核验车位地面至前口完全净空（无驶出残影，直接释放空位）"
            else:
                intent_code = "departed_verifying"
                intent_label = "原车位画面已清空 -> 预判车辆驶离，3秒复核无回揉后释放空位"
        elif direction == "passing_by":
            intent_code = "passing_traffic"
            intent_label = "横向位移显著，预判通道过路车辆/行人掠过"

        return {
            "direction": direction,
            "dy": round(dy, 3),
            "dx": round(dx, 3),
            "dt": round(dt, 2),
            "occ_cy": round(occ_cy, 3),
            "localized_pose": localized_pose,
            "position_verified_stationary": position_verified_stationary,
            "anchor_localized": anchor_localized,
            "scene_discontinuity": scene_discontinuity,
            "zone_in_transit": zone_in_transit,
            "community_in_transit": community_in_transit,
            "intent_code": intent_code,
            "intent_label": intent_label,
        }

    @staticmethod
    def _score_strip_window(
        cand_bgr: np.ndarray,
        ref_strip_edge: np.ndarray,
        ref_strip_lab: Optional[np.ndarray] = None,
    ) -> float:
        """
        Compute foliage-masked rigid structural + curb/pavement profile similarity [0..1]
        between a candidate cropped strip and the calibrated reference strip.
        """
        if cand_bgr is None or cand_bgr.size == 0 or ref_strip_edge is None:
            return 0.0
        th, tw = ref_strip_edge.shape[:2]
        c_small = cv2.resize(cand_bgr, (tw, th), interpolation=cv2.INTER_AREA)
        c_rigid = build_non_foliage_rigid_mask(c_small, min_valid_ratio=0.25) > 0
        c_gray = cv2.cvtColor(c_small, cv2.COLOR_BGR2GRAY)
        gx = np.abs(cv2.Sobel(c_gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(c_gray, cv2.CV_32F, 0, 1, ksize=3))
        c_edge = np.sqrt(gx * gx + gy * gy)
        c_edge = np.where(c_rigid, c_edge, c_edge * 0.15)
        c_edge_u8 = np.clip(c_edge, 0.0, 255.0).astype(np.float32)
        r_edge_f = ref_strip_edge.astype(np.float32)

        # Reject featureless empty asphalt road when calibrated strip contains rich stall/curb structure
        ref_act = float(np.mean(r_edge_f > 32.0))
        cand_act = float(np.mean(c_edge_u8 > 32.0))
        activity_gate = 1.0
        if ref_act >= 0.065 and cand_act < ref_act * 0.34:
            activity_gate = max(0.22, cand_act / max(0.01, ref_act))

        # 1. Full strip structural NCC
        c_zn = c_edge_u8 - float(np.mean(c_edge_u8))
        r_zn = r_edge_f - float(np.mean(r_edge_f))
        denom = float(np.linalg.norm(c_zn) * np.linalg.norm(r_zn)) + 1e-5
        edge_ncc = max(0.0, float(np.sum(c_zn * r_zn)) / denom)

        # 2. Horizontal row-energy profile NCC (captures rear curb/wall line + front lane line elevation!)
        c_row = np.mean(c_edge_u8, axis=1)
        r_row = np.mean(r_edge_f, axis=1)
        cr_zn = c_row - float(np.mean(c_row))
        rr_zn = r_row - float(np.mean(r_row))
        row_denom = float(np.linalg.norm(cr_zn) * np.linalg.norm(rr_zn)) + 1e-5
        row_ncc = max(0.0, float(np.sum(cr_zn * rr_zn)) / row_denom)

        # 3. Rigid border pavement chroma similarity (curb + grass-brick / concrete strip tone)
        chroma_sim = 0.55
        if ref_strip_lab is not None and ref_strip_lab.shape[:2] == (th, tw):
            c_lab = cv2.cvtColor(c_small, cv2.COLOR_BGR2LAB).astype(np.float32)
            r_lab = ref_strip_lab.astype(np.float32)
            # Compare bottom 28% front road/curb transition + top 22% rear border (least occluded by car roofs)
            b_rows = np.r_[0 : max(2, int(th * 0.22)), min(th - 2, int(th * 0.72)) : th]
            ab_diff = float(np.mean(np.abs(c_lab[b_rows, :, 1:3] - r_lab[b_rows, :, 1:3])))
            chroma_sim = float(np.clip(1.0 - ab_diff / 30.0, 0.0, 1.0))

        raw_score = 0.46 * edge_ncc + 0.34 * row_ncc + 0.20 * chroma_sim
        return float(raw_score * activity_gate)

    @staticmethod
    def _compute_constellation_bounds(
        spots: List[Dict[str, Any]], cal_w: int, cal_h: int
    ) -> Tuple[float, float, float, float]:
        xs: List[float] = []
        ys: List[float] = []
        for sp in spots:
            for pt in sp.get("polygon") or []:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    xs.append(float(pt[0]) / max(1.0, float(cal_w)))
                    ys.append(float(pt[1]) / max(1.0, float(cal_h)))
        if not xs or not ys:
            return 0.05, 0.15, 0.95, 0.90
        u0 = float(np.clip(min(xs), 0.0, 0.92))
        u1 = float(np.clip(max(xs), u0 + 0.08, 1.0))
        v0 = float(np.clip(min(ys), 0.0, 0.92))
        v1 = float(np.clip(max(ys), v0 + 0.08, 1.0))
        return u0, v0, u1, v1

    @staticmethod
    def _extract_canonical_stall_landmarks(
        spots: List[Dict[str, Any]],
        cal_w: int,
        cal_h: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract 6 canonical ground-plane landmarks (L1..L6) and true 4-corner outer quad
        [L1, L3, L4, L6] from calibrated stall polygons so VLM perspective rectification
        maps onto the real tilted/curved ground strip rather than an axis-aligned rectangle.
        """
        std = [
            s
            for s in spots
            if s.get("spot_type") != "elastic_strip" and len(s.get("polygon") or []) >= 3
        ]
        use = std if std else [s for s in spots if len(s.get("polygon") or []) >= 3]
        items: List[Tuple[Dict[str, Any], np.ndarray, np.ndarray]] = []
        for s in use:
            pts = np.asarray([[float(p[0]), float(p[1])] for p in s["polygon"]], dtype=np.float32)
            ctr = np.mean(pts, axis=0)
            items.append((s, pts, ctr))
        if not items:
            return None

        if len(items) >= 2:
            xs = [float(it[2][0]) for it in items]
            ys = [float(it[2][1]) for it in items]
            if (max(ys) - min(ys)) > 1.35 * max(1.0, (max(xs) - min(xs))):
                items.sort(key=lambda it: float(it[2][1]))
            else:
                items.sort(key=lambda it: float(it[2][0]))
            u_vec = items[-1][2] - items[0][2]
            nrm = float(np.linalg.norm(u_vec))
            u_row = (u_vec / nrm) if nrm > 1e-3 else np.array([1.0, 0.0], dtype=np.float32)
        else:
            u_row = np.array([1.0, 0.0], dtype=np.float32)

        v_row = np.array([-u_row[1], u_row[0]], dtype=np.float32)
        if v_row[1] < 0 or (abs(float(v_row[1])) < 1e-4 and v_row[0] < 0):
            v_row = -v_row

        def _corners4(pts: np.ndarray, ctr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
            rel = pts - ctr
            du = rel @ u_row
            dv = rel @ v_row
            order_v = np.argsort(dv)
            half = max(1, len(pts) // 2)
            rear_idx = order_v[:half]
            front_idx = order_v[half:]
            tl = pts[rear_idx[int(np.argmin(du[rear_idx]))]]
            tr = pts[rear_idx[int(np.argmax(du[rear_idx]))]]
            bl = pts[front_idx[int(np.argmin(du[front_idx]))]]
            br = pts[front_idx[int(np.argmax(du[front_idx]))]]
            return tl, tr, br, bl

        tl0, tr0, br0, bl0 = _corners4(items[0][1], items[0][2])
        tln, trn, brn, bln = _corners4(items[-1][1], items[-1][2])
        mid = items[len(items) // 2]
        tlm, trm, brm, blm = _corners4(mid[1], mid[2])

        l1 = tl0.astype(np.float32)
        l2 = (0.5 * (tlm + trm)).astype(np.float32)
        l3 = trn.astype(np.float32)
        l4 = brn.astype(np.float32)
        l5 = (0.5 * (blm + brm)).astype(np.float32)
        l6 = bl0.astype(np.float32)

        first_lbl = str(items[0][0].get("label", "首"))
        mid_lbl = str(mid[0].get("label", "中"))
        last_lbl = str(items[-1][0].get("label", "末"))
        landmarks = [
            ("L1", l1, f"首车位({first_lbl})左后接地角"),
            ("L2", l2, f"中间车位({mid_lbl})后沿接地中点"),
            ("L3", l3, f"末车位({last_lbl})右后接地角"),
            ("L4", l4, f"末车位({last_lbl})右前路缘角"),
            ("L5", l5, f"中间车位({mid_lbl})前沿路缘弧线中点"),
            ("L6", l6, f"首车位({first_lbl})左前路缘角"),
        ]
        outer_quad_cal = np.float32([l1, l3, l4, l6])
        return {
            "landmarks": landmarks,
            "outer_quad_cal": outer_quad_cal,
            "ordered_items": items,
            "is_horizontal": bool(abs(float(u_row[0])) >= abs(float(u_row[1]))),
        }

    def _locate_dynamic_roi_with_vlm(
        self,
        raw_img: np.ndarray,
        cam: Dict[str, Any],
        spots: List[Dict[str, Any]],
        static_crop: Dict[str, Any],
        vlm_cfg: Dict[str, Any],
        now_wall: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Use Dual-Image Multimodal VLM (Reference Calibration with L1..L6 ground landmarks +
        4-border 0..1000 ruler target frame with optional dark-frame shadow lift) to solve
        an exact ground-plane homography under heavy phone zoom, night lighting, or camera tilt.
        """
        h0, w0 = raw_img.shape[:2]
        thumb16 = cv2.resize(cv2.cvtColor(raw_img, cv2.COLOR_BGR2GRAY), (16, 16), interpolation=cv2.INTER_AREA)
        camera_id = str(cam["camera_id"])
        cache_prefix = f"{camera_id}::"
        with self._lock:
            for k, entry in list(self._dynamic_roi_cache.items()):
                if not k.startswith(cache_prefix):
                    continue
                if (now_wall - float(entry["ts"])) > 600.0:
                    self._dynamic_roi_cache.pop(k, None)
                    continue
                diff = float(np.mean(np.abs(thumb16.astype(np.float32) - entry["thumb16"].astype(np.float32))) / 255.0)
                if diff <= 0.045:
                    self._dynamic_roi_cache.move_to_end(k)
                    return dict(entry["result"])

        cal_w = max(32, int(cam.get("image_width") or cam.get("calibration_width") or 910))
        cal_h = max(32, int(cam.get("image_height") or cam.get("calibration_height") or 576))
        lm_bundle = self._extract_canonical_stall_landmarks(spots, cal_w, cal_h)

        # Prepare target inspection image (<=768px) with temporary night shadow lift + 4-border 0..1000 ruler ticks
        t_scale = min(1.0, 768.0 / float(max(h0, w0)))
        vis = cv2.resize(
            raw_img,
            (max(64, int(round(w0 * t_scale))), max(64, int(round(h0 * t_scale)))),
            interpolation=cv2.INTER_AREA,
        )
        if float(np.mean(cv2.cvtColor(vis, cv2.COLOR_BGR2GRAY))) < 95.0:
            ycrcb = cv2.cvtColor(vis, cv2.COLOR_BGR2YCrCb)
            clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
            y_eq = clahe.apply(ycrcb[:, :, 0])
            lut = np.array(
                [np.clip(((i / 255.0) ** 0.78) * 255.0, 0, 255) for i in range(256)],
                dtype=np.uint8,
            )
            ycrcb[:, :, 0] = cv2.addWeighted(lut[ycrcb[:, :, 0]], 0.65, y_eq, 0.35, 0)
            vis = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

        vh, vw = vis.shape[:2]
        for permille in range(50, 1000, 50):
            py = int(round(permille / 1000.0 * vh))
            px = int(round(permille / 1000.0 * vw))
            tick_len = 13 if permille % 100 == 0 else 7
            thick = 2 if permille % 100 == 0 else 1
            cv2.line(vis, (0, py), (tick_len, py), (0, 235, 255), thick)
            cv2.line(vis, (vw - tick_len, py), (vw - 1, py), (0, 235, 255), thick)
            cv2.line(vis, (px, 0), (px, tick_len), (0, 235, 255), thick)
            cv2.line(vis, (px, vh - tick_len), (px, vh - 1), (0, 235, 255), thick)
            if permille % 100 == 0:
                cv2.putText(vis, str(permille), (15, max(12, py + 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 235, 255), 1, cv2.LINE_AA)
                cv2.putText(vis, str(permille), (max(2, px - 14), 22), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 235, 255), 1, cv2.LINE_AA)
                cv2.putText(vis, str(permille), (max(2, px - 14), vh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 235, 255), 1, cv2.LINE_AA)

        ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        del vis
        if not ok:
            return None
        target_uri = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")

        # Build annotated canonical reference URI (<=640px) if template_jpeg_b64 is available
        ref_uri: Optional[str] = None
        tpl_b64 = str(cam.get("template_jpeg_b64") or "")
        if tpl_b64 and lm_bundle is not None:
            try:
                raw_b64 = tpl_b64.split(",", 1)[1] if "," in tpl_b64 else tpl_b64
                tpl_bgr = cv2.imdecode(np.frombuffer(base64.b64decode(raw_b64), dtype=np.uint8), cv2.IMREAD_COLOR)
                if tpl_bgr is not None:
                    ref_vis = cv2.resize(tpl_bgr, (cal_w, cal_h), interpolation=cv2.INTER_AREA)
                    del tpl_bgr
                    for s_it, pts_it, ctr_it in lm_bundle["ordered_items"]:
                        cv2.polylines(ref_vis, [pts_it.astype(np.int32).reshape(-1, 1, 2)], True, (255, 220, 0), 2, cv2.LINE_AA)
                        cv2.putText(
                            ref_vis,
                            str(s_it.get("label", "")),
                            (int(ctr_it[0]) - 12, int(ctr_it[1]) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (255, 255, 255),
                            2,
                            cv2.LINE_AA,
                        )
                    for code, pt_c, _ in lm_bundle["landmarks"]:
                        px_c, py_c = int(round(float(pt_c[0]))), int(round(float(pt_c[1])))
                        cv2.circle(ref_vis, (px_c, py_c), 8, (0, 0, 0), -1, cv2.LINE_AA)
                        cv2.circle(ref_vis, (px_c, py_c), 6, (0, 60, 255), -1, cv2.LINE_AA)
                        cv2.putText(
                            ref_vis,
                            code,
                            (max(4, min(cal_w - 36, px_c - 12)), max(18, py_c - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.58,
                            (0, 255, 255),
                            2,
                            cv2.LINE_AA,
                        )
                    r_scale = min(1.0, 640.0 / float(max(cal_w, cal_h)))
                    if r_scale < 0.999:
                        ref_vis = cv2.resize(
                            ref_vis,
                            (max(64, int(round(cal_w * r_scale))), max(64, int(round(cal_h * r_scale)))),
                            interpolation=cv2.INTER_AREA,
                        )
                    ok_r, ref_jpg = cv2.imencode(".jpg", ref_vis, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                    del ref_vis
                    if ok_r:
                        ref_uri = "data:image/jpeg;base64," + base64.b64encode(ref_jpg.tobytes()).decode("ascii")
            except Exception:
                ref_uri = None

        with self._lock:
            self.privacy_stats["slow_lane_vlm_calls"] += 1

        if ref_uri is not None and lm_bundle is not None:
            ordered_items = lm_bundle["ordered_items"]
            first_lbl = str(ordered_items[0][0].get("label", "01"))
            mid_lbl = str(ordered_items[len(ordered_items) // 2][0].get("label", ""))
            last_lbl = str(ordered_items[-1][0].get("label", ""))
            lm_desc = "；".join(f"{c}={d}" for c, _, d in lm_bundle["landmarks"])
            order_phrase = "从左到右" if lm_bundle["is_horizontal"] else "按编号顺序从首到尾"
            prompt = (
                f"你将看到同一小区停车排的两张图：\n"
                f"- 图1（参考标定图）：标出了共 {len(ordered_items)} 个固定地面车位（编号 {first_lbl}~{last_lbl}，青色框）以及 6 个关键地面基准锚点 {lm_desc}。\n"
                f"- 图2（当前手机/相机抓拍原图，四周边缘带 0..1000 千分比坐标刻度）：可能经过了手机放大变焦、视角偏移、倾斜或昼夜光照变化，且车位上可能停满不同车辆（含大型厢式车）。\n"
                f"请先根据地面路缘白线弧度、车位排列数量（{order_phrase}逐辆数清对应 {first_lbl}~{last_lbl} 号位上的车轮接地位置）、后沿绿篱/路牙根部线以及背景建筑立面柱位，在图2的 0..1000 坐标系中精准找出与图1中 L1~L6 一一对应的地面点坐标 [x, y]！\n"
                f"关键准则：\n"
                f"1. 必须是【轮胎接地的地面车位角点/路缘白线点】，切勿选在车顶、引擎盖或高顶厢式车顶部高度！\n"
                f"2. L6 是首车位({first_lbl})左前路缘点，L5 是中间车位({mid_lbl})前沿弧形路缘点，L4 是末车位({last_lbl})右前路缘点；若路缘呈弧形（L5 比两端 L6、L4 更靠下），务必如实反映各点真实高度，切勿把 L5 纵坐标错赋给 L6 或 L4！\n"
                f"3. 若手机放大拍摄导致首尾车位的某个边角贴近画面边缘或轻微超出画面边界，允许输出 -120..1120 范围内的外推千分比坐标，切勿生硬截断到 0 或 1000 造成整排车位压缩变形！\n"
                '仅输出严格 JSON：{"found":true, "landmarks":{"L1":[x,y],"L2":[x,y],"L3":[x,y],"L4":[x,y],"L5":[x,y],"L6":[x,y]}, "stall_quad":[[x_l1,y_l1],[x_l3,y_l3],[x_l4,y_l4],[x_l6,y_l6]], "confidence":0.90, "reason":"不超过25字对齐依据"}'
            )
            content, err, _ = self._post_vlm_completions(
                vlm_cfg, prompt, [ref_uri, target_uri], max_tokens=640
            )
        else:
            spot_labels = "、".join(str(s.get("label", "")) for s in spots[:10])
            sx0 = int(round(float(static_crop.get("x", 0.0)) * 1000))
            sy0 = int(round(float(static_crop.get("y", 0.0)) * 1000))
            sx1 = int(round((float(static_crop.get("x", 0.0)) + float(static_crop.get("w", 1.0))) * 1000))
            sy1 = int(round((float(static_crop.get("y", 0.0)) + float(static_crop.get("h", 1.0))) * 1000))
            prompt = (
                f"这是小区机位【{cam.get('name', camera_id)}】的完整抓拍画面（四周边缘标有 0..1000 千分比坐标刻度）。\n"
                f"该机位原本监控一排共 {len(spots)} 个固定地面停车位（编号：{spot_labels}），历史静态裁剪框约为 [{sx0},{sy0},{sx1},{sy1}]。\n"
                "受大风晃动、支架偏转旋转、树木摆动或手机放大变焦影响，车位排在画面中的位置、角度或大小可能发生了明显变化。\n"
                "请你忽略摆动的树冠枝叶与下方空白行车通道，精准定位这排地面停车位（从最左车位外侧地面边线到最右车位外侧地面边线、"
                "从车位后沿路牙/防撞石/绿篱根部地面线到车位前沿出入口地面线，包含停在位内的轮胎接地范围，但不要把上方整栋高楼或大片下方空马路框入）的 0..1000 归一化边界框与四角锚点 [后左TL, 后右TR, 前右BR, 前左BL]。\n"
                '仅输出严格 JSON：{"found":true, "stall_ground_box":[xmin,ymin,xmax,ymax], "stall_quad":[[xtl,ytl],[xtr,ytr],[xbr,ybr],[xbl,ybl]], "zoom_hint":1.0, "confidence":0.0到1.0, "reason":"不超过20字定位说明"}'
            )
            content, err, _ = self._post_vlm_completions(
                vlm_cfg, prompt, target_uri, max_tokens=512
            )

        if err or not content:
            return None

        try:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            parsed = json.loads(match.group(0) if match else content)
            if not parsed.get("found", True):
                return None

            homography_cal_to_raw_norm: Optional[List[List[float]]] = None
            quad_norm: Optional[List[List[float]]] = None
            landmark_inliers = 0
            raw_landmarks = parsed.get("landmarks")

            if isinstance(raw_landmarks, dict) and lm_bundle is not None:
                cal_pts_l: List[np.ndarray] = []
                raw_pts_l: List[List[float]] = []
                lm_norm_map: Dict[str, List[float]] = {}
                for code, pt_c, _ in lm_bundle["landmarks"]:
                    val = raw_landmarks.get(code)
                    if isinstance(val, (list, tuple)) and len(val) >= 2:
                        xn = float(np.clip(float(val[0]) / 1000.0, -0.18, 1.18))
                        yn = float(np.clip(float(val[1]) / 1000.0, -0.18, 1.18))
                        cal_pts_l.append(pt_c)
                        raw_pts_l.append([xn * w0, yn * h0])
                        lm_norm_map[code] = [xn, yn]
                if len(cal_pts_l) >= 4:
                    cal_arr_l = np.float32(cal_pts_l).reshape(-1, 1, 2)
                    raw_arr_l = np.float32(raw_pts_l).reshape(-1, 1, 2)
                    if len(cal_pts_l) >= 5:
                        H_lm, inl_lm = cv2.findHomography(
                            cal_arr_l,
                            raw_arr_l,
                            cv2.RANSAC,
                            max(12.0, min(w0, h0) * 0.045),
                        )
                        landmark_inliers = int(np.sum(inl_lm)) if inl_lm is not None else 0
                    else:
                        H_lm, _ = cv2.findHomography(cal_arr_l, raw_arr_l, 0)
                        landmark_inliers = 4
                    if (
                        H_lm is not None
                        and landmark_inliers >= 4
                        and np.all(np.isfinite(H_lm))
                        and abs(float(H_lm[2, 2])) > 1e-8
                    ):
                        H_lm = H_lm.astype(np.float64) / float(H_lm[2, 2])
                        cal_frame_q = np.float32([[[0, 0]], [[cal_w, 0]], [[cal_w, cal_h]], [[0, cal_h]]])
                        proj_frame_q = cv2.perspectiveTransform(cal_frame_q, H_lm).reshape(4, 2)
                        if (
                            np.all(np.isfinite(proj_frame_q))
                            and cv2.isContourConvex(proj_frame_q.astype(np.float32))
                            and float(cv2.contourArea(proj_frame_q.astype(np.float32), oriented=True)) > 100.0
                        ):
                            # Store normalized (cal_norm -> raw_norm) so resolution changes stay invariant
                            H_c2r_norm = (
                                np.diag([1.0 / float(w0), 1.0 / float(h0), 1.0])
                                @ H_lm
                                @ np.diag([float(cal_w), float(cal_h), 1.0])
                            )
                            homography_cal_to_raw_norm = [
                                [round(float(v), 7) for v in row] for row in H_c2r_norm
                            ]
                            outer_q_raw = cv2.perspectiveTransform(
                                lm_bundle["outer_quad_cal"].reshape(4, 1, 2), H_lm
                            ).reshape(4, 2)
                            quad_norm = [
                                [float(pt[0]) / float(w0), float(pt[1]) / float(h0)]
                                for pt in outer_q_raw
                            ]
                if quad_norm is None and all(k in lm_norm_map for k in ("L1", "L3", "L4", "L6")):
                    quad_norm = [lm_norm_map["L1"], lm_norm_map["L3"], lm_norm_map["L4"], lm_norm_map["L6"]]

            if quad_norm is None:
                raw_quad = parsed.get("stall_quad")
                if isinstance(raw_quad, (list, tuple)) and len(raw_quad) == 4:
                    try:
                        q_pts = [
                            [
                                float(np.clip(float(pt[0]) / 1000.0, -0.15, 1.15)),
                                float(np.clip(float(pt[1]) / 1000.0, -0.15, 1.15)),
                            ]
                            for pt in raw_quad
                            if isinstance(pt, (list, tuple)) and len(pt) >= 2
                        ]
                        if len(q_pts) == 4:
                            quad_norm = q_pts
                    except Exception:
                        quad_norm = None

            box = parsed.get("stall_ground_box") or parsed.get("roi_box")
            if isinstance(box, (list, tuple)) and len(box) >= 4:
                gx0 = float(np.clip(float(box[0]) / 1000.0, 0.0, 0.96))
                gy0 = float(np.clip(float(box[1]) / 1000.0, 0.0, 0.96))
                gx1 = float(np.clip(float(box[2]) / 1000.0, gx0 + 0.06, 1.0))
                gy1 = float(np.clip(float(box[3]) / 1000.0, gy0 + 0.04, 1.0))
            elif quad_norm is not None:
                gx0 = float(np.clip(min(p[0] for p in quad_norm), 0.0, 0.96))
                gy0 = float(np.clip(min(p[1] for p in quad_norm), 0.0, 0.96))
                gx1 = float(np.clip(max(p[0] for p in quad_norm), gx0 + 0.06, 1.0))
                gy1 = float(np.clip(max(p[1] for p in quad_norm), gy0 + 0.04, 1.0))
            else:
                return None

            res = {
                "gx0": gx0,
                "gy0": gy0,
                "gx1": gx1,
                "gy1": gy1,
                "quad_norm": quad_norm,
                "homography_cal_to_raw_norm": homography_cal_to_raw_norm,
                "landmark_inliers": landmark_inliers,
                "confidence": float(parsed.get("confidence", 0.88)),
                "reason": str(parsed.get("reason", "AI 多模态地面基准点对齐")),
            }
            cache_key = f"{camera_id}::{int(now_wall * 10)}"
            with self._lock:
                self._dynamic_roi_cache[cache_key] = {
                    "ts": now_wall,
                    "thumb16": thumb16,
                    "result": dict(res),
                }
                while len(self._dynamic_roi_cache) > MAX_DYNAMIC_ROI_CACHE_ENTRIES:
                    self._dynamic_roi_cache.popitem(last=False)
            return res
        except Exception:
            return None

    def _rectify_frame_via_sift_anchors(
        self,
        raw_img: np.ndarray,
        cam: Dict[str, Any],
        cal_w: int,
        cal_h: int,
        expected_rw: float = 1.0,
        expected_rh: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Perspective / similarity rectification using parked-car-masked + foliage-masked SIFT
        and fixed positioning anchors (`anchor_points`). Maps arbitrary shifted, rotated, tilted,
        or zoomed raw frames directly into canonical `(cal_w, cal_h)` calibration space so every
        stall polygon and 3D envelope aligns 1:1 with the reference calibration.
        """
        sift_cal_pts = cam.get("sift_cal_pts")
        sift_des = cam.get("sift_des")
        if (
            sift_cal_pts is None
            or sift_des is None
            or len(sift_cal_pts) < 12
            or not hasattr(cv2, "SIFT_create")
        ):
            return None

        h_raw, w_raw = raw_img.shape[:2]
        if h_raw < 48 or w_raw < 48:
            return None

        try:
            work_scale = min(1.0, 1080.0 / float(max(h_raw, w_raw)))
            work_w = max(48, int(round(w_raw * work_scale)))
            work_h = max(48, int(round(h_raw * work_scale)))
            work_bgr = (
                cv2.resize(raw_img, (work_w, work_h), interpolation=cv2.INTER_AREA)
                if work_scale < 0.999
                else raw_img
            )
            work_non_fol = build_non_foliage_rigid_mask(work_bgr, min_valid_ratio=0.22)
            work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
            if work_bgr is not raw_img:
                del work_bgr

            sift = cv2.SIFT_create(
                nfeatures=6500, nOctaveLayers=5, contrastThreshold=0.016, edgeThreshold=14
            )
            kp_raw, des_raw = sift.detectAndCompute(work_gray, work_non_fol)
            del work_gray
            if not kp_raw or des_raw is None or len(kp_raw) < 12:
                return None

            matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
            raw_des_root = _to_rootsift_descriptors(des_raw.astype(np.float32))
            inv_scale = 1.0 / work_scale
            reproj_thresh = max(5.5, min(w_raw, h_raw) * 0.0065)
            hom_method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)

            def _validate_H(H_cand: Optional[np.ndarray]) -> Optional[Tuple[np.ndarray, float, float, List[List[float]]]]:
                if H_cand is None or not np.all(np.isfinite(H_cand)):
                    return None
                if abs(float(H_cand[2, 2])) < 1e-8:
                    return None
                Hn = H_cand.astype(np.float64) / float(H_cand[2, 2])
                cal_corners = np.float32([[[0, 0]], [[cal_w, 0]], [[cal_w, cal_h]], [[0, cal_h]]])
                raw_corners = cv2.perspectiveTransform(cal_corners, Hn).reshape(4, 2)
                if not np.all(np.isfinite(raw_corners)):
                    return None
                signed_area = float(cv2.contourArea(raw_corners.astype(np.float32), oriented=True))
                if signed_area <= 64.0 or not cv2.isContourConvex(raw_corners.astype(np.float32)):
                    return None
                v_top = raw_corners[1] - raw_corners[0]
                v_bot = raw_corners[2] - raw_corners[3]
                v_left = raw_corners[3] - raw_corners[0]
                v_right = raw_corners[2] - raw_corners[1]
                w_top = float(np.linalg.norm(v_top))
                w_bot = float(np.linalg.norm(v_bot))
                h_left = float(np.linalg.norm(v_left))
                h_right = float(np.linalg.norm(v_right))
                if min(w_top, w_bot, h_left, h_right) < 20.0:
                    return None
                if max(w_top, w_bot) / max(1.0, min(w_top, w_bot)) > 1.95:
                    return None
                if max(h_left, h_right) / max(1.0, min(h_left, h_right)) > 1.95:
                    return None
                cos_tl = abs(float(np.dot(v_top, v_left)) / max(1.0, w_top * h_left))
                cos_br = abs(float(np.dot(v_bot, v_right)) / max(1.0, w_bot * h_right))
                if max(cos_tl, cos_br) > 0.28:
                    return None

                rot_deg = float(np.degrees(np.arctan2(v_top[1], v_top[0])))
                if abs(rot_deg) > 42.0:
                    return None

                exp_px_w = max(24.0, expected_rw * w_raw)
                zoom_ratio = (0.5 * (w_top + w_bot)) / exp_px_w
                if not (0.22 <= zoom_ratio <= 4.50):
                    return None

                cal_aspect = float(cal_w) / max(1.0, float(cal_h))
                mapped_aspect = (0.5 * (w_top + w_bot)) / max(1.0, 0.5 * (h_left + h_right))
                if not (0.52 <= (mapped_aspect / cal_aspect) <= 1.92):
                    return None

                ctr_raw = np.mean(raw_corners, axis=0)
                if not (-0.12 * w_raw <= ctr_raw[0] <= 1.12 * w_raw and -0.12 * h_raw <= ctr_raw[1] <= 1.12 * h_raw):
                    return None

                return Hn, rot_deg, zoom_ratio, raw_corners.tolist()

            def _solve_1to1_pool(
                pool_pts: np.ndarray,
                pool_des: np.ndarray,
            ) -> Optional[Tuple[np.ndarray, str, int, float, float, List[List[float]]]]:
                ref_des_root = _to_rootsift_descriptors(pool_des)
                if ref_des_root is None or raw_des_root is None or len(pool_pts) < 10:
                    return None
                knn_fwd = matcher.knnMatch(ref_des_root, raw_des_root, k=2)
                knn_bwd = matcher.knnMatch(raw_des_root, ref_des_root, k=1)
                bwd_best_ref: Dict[int, int] = {}
                for bpair in knn_bwd:
                    if bpair:
                        bwd_best_ref[bpair[0].queryIdx] = bpair[0].trainIdx

                # 1-to-1 deduplication by trainIdx (prevents out-of-FOV descriptors collapsing onto 1 raw keypoint under zoom!)
                best_by_train: Dict[int, Tuple[float, np.ndarray, List[float]]] = {}
                for pair in knn_fwd:
                    if len(pair) < 2:
                        continue
                    m, n = pair[0], pair[1]
                    if m.distance >= 0.80 * n.distance:
                        continue
                    q_idx = m.queryIdx
                    t_idx = m.trainIdx
                    cal_pt = pool_pts[q_idx]
                    bwd_ref = bwd_best_ref.get(t_idx, -1)
                    is_mutual = (
                        bwd_ref >= 0
                        and float(np.linalg.norm(pool_pts[bwd_ref] - cal_pt)) <= 8.0
                    )
                    if not is_mutual and m.distance >= 0.65 * n.distance:
                        continue
                    rx_pt = kp_raw[t_idx].pt[0] * inv_scale
                    ry_pt = kp_raw[t_idx].pt[1] * inv_scale
                    prev = best_by_train.get(t_idx)
                    if prev is None or m.distance < prev[0]:
                        best_by_train[t_idx] = (float(m.distance), cal_pt, [rx_pt, ry_pt])

                # Also deduplicate canonical 2px cells so src and dst are both strictly 1-to-1
                best_by_cell: Dict[Tuple[int, int], Tuple[float, np.ndarray, List[float]]] = {}
                for dist_v, cal_pt, dst_xy in best_by_train.values():
                    cell = (int(round(float(cal_pt[0]) * 0.5)), int(round(float(cal_pt[1]) * 0.5)))
                    prev_c = best_by_cell.get(cell)
                    if prev_c is None or dist_v < prev_c[0]:
                        best_by_cell[cell] = (dist_v, cal_pt, dst_xy)

                if len(best_by_cell) < 10:
                    return None

                u_src = np.float32([v[1] for v in best_by_cell.values()]).reshape(-1, 1, 2)
                u_dst = np.float32([v[2] for v in best_by_cell.values()]).reshape(-1, 1, 2)
                u_dst_xy = u_dst.reshape(-1, 2)

                M_sim, _ = cv2.estimateAffinePartial2D(
                    u_src, u_dst, method=cv2.RANSAC, ransacReprojThreshold=reproj_thresh, maxIters=3000, confidence=0.999
                )
                H_hom, _ = cv2.findHomography(
                    u_src, u_dst, hom_method, reproj_thresh, maxIters=4000, confidence=0.999
                )

                def _eval_inliers(H_cand: Optional[np.ndarray]) -> int:
                    if H_cand is None or not np.all(np.isfinite(H_cand)):
                        return 0
                    proj = cv2.perspectiveTransform(u_src, H_cand.astype(np.float64)).reshape(-1, 2)
                    if not np.all(np.isfinite(proj)):
                        return 0
                    errs = np.linalg.norm(proj - u_dst_xy, axis=1)
                    pts_in = u_src.reshape(-1, 2)[errs <= reproj_thresh]
                    if len(pts_in) < 10:
                        return 0
                    x_span = (float(np.max(pts_in[:, 0])) - float(np.min(pts_in[:, 0]))) / max(1.0, float(cal_w))
                    y_span = (float(np.max(pts_in[:, 1])) - float(np.min(pts_in[:, 1]))) / max(1.0, float(cal_h))
                    gx = np.floor(pts_in[:, 0] / max(16.0, float(cal_w) * 0.18)).astype(np.int32)
                    gy = np.floor(pts_in[:, 1] / max(16.0, float(cal_h) * 0.25)).astype(np.int32)
                    distinct_cells = len({(int(cx_i), int(cy_i)) for cx_i, cy_i in zip(gx, gy)})
                    if x_span < 0.22 or y_span < 0.11 or distinct_cells < 5:
                        return 0
                    return int(len(pts_in))

                sim_H_raw = np.vstack([M_sim, [0.0, 0.0, 1.0]]) if M_sim is not None else None
                sim_inl = _eval_inliers(sim_H_raw)
                hom_inl = _eval_inliers(H_hom)
                hom_val = _validate_H(H_hom) if hom_inl >= 10 else None
                sim_val = _validate_H(sim_H_raw) if sim_inl >= 10 else None

                if hom_val is not None and (
                    sim_val is None
                    or (hom_inl >= 20 and hom_inl >= int(sim_inl * 0.85))
                    or hom_inl >= max(13, int(sim_inl * 1.08))
                ):
                    return hom_val[0], "sift_anchor_homography_8dof", hom_inl, hom_val[1], hom_val[2], hom_val[3]
                if sim_val is not None:
                    return sim_val[0], "sift_anchor_similarity_4dof", sim_inl, sim_val[1], sim_val[2], sim_val[3]
                return None

            primary_sol = _solve_1to1_pool(sift_cal_pts, sift_des)
            sift_collar_pts = cam.get("sift_collar_pts")
            sift_collar_des = cam.get("sift_collar_des")
            # Under heavy phone zoom, upper-tower features leave the FOV; also evaluate the ground-collar SIFT pool!
            if (
                (primary_sol is None or primary_sol[2] < 18)
                and sift_collar_pts is not None
                and sift_collar_des is not None
                and len(sift_collar_pts) >= 12
            ):
                collar_sol = _solve_1to1_pool(sift_collar_pts, sift_collar_des)
                if collar_sol is not None and (primary_sol is None or collar_sol[2] > primary_sol[2]):
                    primary_sol = collar_sol

            if primary_sol is None:
                return None

            chosen_H, chosen_model, chosen_inliers, chosen_rot, chosen_zoom, chosen_corners = primary_sol

            # Stage 2: Native-resolution ground-collar local similarity refinement to eliminate
            # upper-wall parallax / ultra-wide lens distortion on the stall plane
            if (
                sift_collar_pts is not None
                and sift_collar_des is not None
                and len(sift_collar_pts) >= 12
            ):
                try:
                    pad_x = int(round(0.18 * cal_w))
                    pad_y = int(round(0.35 * cal_h))
                    collar_w = cal_w + 2 * pad_x
                    collar_h = cal_h + 2 * pad_y
                    T_shift = np.array(
                        [[1.0, 0.0, -pad_x], [0.0, 1.0, -pad_y], [0.0, 0.0, 1.0]], dtype=np.float64
                    )
                    raw_collar = cv2.warpPerspective(
                        raw_img,
                        chosen_H @ T_shift,
                        (collar_w, collar_h),
                        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_REPLICATE,
                    )
                    rc_non_fol = build_non_foliage_rigid_mask(raw_collar, min_valid_ratio=0.22)
                    rc_gray = cv2.cvtColor(raw_collar, cv2.COLOR_BGR2GRAY)
                    del raw_collar
                    sift_loc = cv2.SIFT_create(
                        nfeatures=2400, nOctaveLayers=5, contrastThreshold=0.014, edgeThreshold=14
                    )
                    rkps, rdes = sift_loc.detectAndCompute(rc_gray, rc_non_fol)
                    del rc_gray
                    if rkps and rdes is not None and len(rkps) >= 12:
                        rdes_root = _to_rootsift_descriptors(rdes.astype(np.float32))
                        cdes_root = _to_rootsift_descriptors(sift_collar_des)
                        knn_loc = matcher.knnMatch(cdes_root, rdes_root, k=2)
                        loc_by_train: Dict[int, Tuple[float, np.ndarray, List[float]]] = {}
                        for pair_l in knn_loc:
                            if len(pair_l) < 2:
                                continue
                            ml, nl = pair_l[0], pair_l[1]
                            if ml.distance < 0.78 * nl.distance:
                                s_pt = sift_collar_pts[ml.queryIdx]
                                d_pt = [rkps[ml.trainIdx].pt[0] - pad_x, rkps[ml.trainIdx].pt[1] - pad_y]
                                if float(np.hypot(d_pt[0] - s_pt[0], d_pt[1] - s_pt[1])) <= 42.0:
                                    prev_l = loc_by_train.get(ml.trainIdx)
                                    if prev_l is None or ml.distance < prev_l[0]:
                                        loc_by_train[ml.trainIdx] = (float(ml.distance), s_pt, d_pt)
                        if len(loc_by_train) >= 10:
                            loc_src = [v[1] for v in loc_by_train.values()]
                            loc_dst = [v[2] for v in loc_by_train.values()]
                            M_loc, loc_mask = cv2.estimateAffinePartial2D(
                                np.float32(loc_src).reshape(-1, 1, 2),
                                np.float32(loc_dst).reshape(-1, 1, 2),
                                method=cv2.RANSAC,
                                ransacReprojThreshold=4.5,
                            )
                            loc_inliers = int(np.sum(loc_mask)) if loc_mask is not None else 0
                            if M_loc is not None and loc_inliers >= 10:
                                loc_inlier_pts = np.float32(loc_src)[loc_mask.ravel() > 0]
                                loc_x_span = float(np.max(loc_inlier_pts[:, 0]) - np.min(loc_inlier_pts[:, 0]))
                                loc_scale = float(np.hypot(M_loc[0, 0], M_loc[1, 0]))
                                loc_rot = float(np.degrees(np.arctan2(M_loc[1, 0], M_loc[0, 0])))
                                loc_shift = float(np.hypot(M_loc[0, 2], M_loc[1, 2]))
                                if (
                                    loc_x_span >= 0.22 * float(cal_w)
                                    and 0.92 <= loc_scale <= 1.08
                                    and abs(loc_rot) <= 4.5
                                    and loc_shift <= 38.0
                                ):
                                    H_delta = np.vstack([M_loc, [0.0, 0.0, 1.0]])
                                    refined_valid = _validate_H(chosen_H @ H_delta)
                                    if refined_valid is not None:
                                        chosen_H, chosen_rot, chosen_zoom, chosen_corners = refined_valid
                                        chosen_inliers = max(chosen_inliers, loc_inliers)
                except Exception:
                    pass

            anchor_pts = cam.get("anchor_points") or []

            rectified_bgr = cv2.warpPerspective(
                raw_img,
                chosen_H,
                (cal_w, cal_h),
                flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )

            H_norm = np.diag([1.0 / float(w_raw), 1.0 / float(h_raw), 1.0]) @ chosen_H
            H_norm_list = [[round(float(v), 7) for v in row] for row in H_norm]

            anchor_matches: List[Dict[str, Any]] = []
            for ap in anchor_pts:
                try:
                    ax_c, ay_c = self._anchor_to_cal_px(ap, cal_w, cal_h)
                    pt_raw = cv2.perspectiveTransform(
                        np.float32([[[ax_c, ay_c]]]), chosen_H
                    )[0, 0]
                    anchor_matches.append(
                        {
                            "id": str(ap.get("id", "")),
                            "label": str(ap.get("label") or ap.get("name") or ap.get("id") or ""),
                            "cal_px": [round(float(ax_c), 1), round(float(ay_c), 1)],
                            "cal_xy": [
                                round(float(ax_c) / float(cal_w), 4),
                                round(float(ay_c) / float(cal_h), 4),
                            ],
                            "raw_xy": [
                                round(float(pt_raw[0]) / float(w_raw), 4),
                                round(float(pt_raw[1]) / float(h_raw), 4),
                            ],
                        }
                    )
                except Exception:
                    continue

            xs_n = [float(np.clip(c[0] / float(w_raw), 0.0, 1.0)) for c in chosen_corners]
            ys_n = [float(np.clip(c[1] / float(h_raw), 0.0, 1.0)) for c in chosen_corners]
            bx = min(xs_n)
            by = min(ys_n)
            bw = max(0.05, max(xs_n) - bx)
            bh = max(0.05, max(ys_n) - by)

            return {
                "rectified_bgr": rectified_bgr,
                "model": chosen_model,
                "inliers": chosen_inliers,
                "rotation_deg": round(chosen_rot, 2),
                "zoom_ratio": round(chosen_zoom, 3),
                "homography_norm": H_norm_list,
                "raw_quad_norm": [
                    [round(c[0] / float(w_raw), 4), round(c[1] / float(h_raw), 4)]
                    for c in chosen_corners
                ],
                "bbox_norm": (bx, by, bw, bh),
                "anchor_matches": anchor_matches,
            }
        except Exception:
            return None

    def _resolve_dynamic_crop_roi(
        self,
        raw_img: np.ndarray,
        cam: Dict[str, Any],
        spots: List[Dict[str, Any]],
        cal_w: int,
        cal_h: int,
        dynamic_enabled: bool,
        has_vlm_ready: bool,
        vlm_cfg: Dict[str, Any],
        now_wall: float,
    ) -> Tuple[np.ndarray, Dict[str, Any], Optional[Dict[str, Any]], Optional[Tuple[float, float, float, float]]]:
        """
        Dynamically anchor, perspective-rectify, and crop the parking-stall strip from an incoming frame:
        - Fast exit (<1.2ms) if static `crop_roi` already aligns cleanly with the rigid reference strip.
        - Tier 0 (Primary — Rigid SIFT + Fixed Anchor Landmarks Homography): Handles arbitrary camera
          translation, roll/tilt rotation, perspective shift, and focal zoom (0.3x..3.2x) with 1:1 canonical warp!
        - Tier A (<7ms CPU): Multi-scale foliage-masked rigid strip search for untextured / synthetic scenes.
        - Tier B (VLM fallback + RAM scene cache): AI Multimodal `0..1000` 4-corner quad / box grounding.
        """
        crop_cfg = cam.get("crop_roi")
        static_cropped, static_info = apply_camera_crop_roi(raw_img, crop_cfg, cal_w=cal_w, cal_h=cal_h)
        h_raw, w_raw = raw_img.shape[:2]

        # Even if camera was calibrated full-frame (`crop_roi` disabled) or rejected static crop due to aspect change,
        # compensate large camera move/tilt/zoom/orientation via SIFT+Anchors!
        if not static_info.get("applied") or not isinstance(crop_cfg, dict):
            aspect_mismatch = static_info.get("reason") == "aspect_orientation_mismatch"
            if dynamic_enabled and cam.get("sift_des") is not None:
                ref_strip_edge_ff = cam.get("ref_strip_edge")
                ref_strip_lab_ff = cam.get("ref_strip_lab")
                ff_score = (
                    self._score_strip_window(raw_img, ref_strip_edge_ff, ref_strip_lab_ff)
                    if (ref_strip_edge_ff is not None and not aspect_mismatch)
                    else 0.0
                    if aspect_mismatch
                    else 1.0
                )
                if ff_score < 0.72:
                    exp_rw_ff = (
                        float(crop_cfg.get("w", 1.0))
                        if (isinstance(crop_cfg, dict) and crop_cfg.get("enabled"))
                        else 1.0
                    )
                    exp_rh_ff = (
                        float(crop_cfg.get("h", 1.0))
                        if (isinstance(crop_cfg, dict) and crop_cfg.get("enabled"))
                        else 1.0
                    )
                    sift_ff = self._rectify_frame_via_sift_anchors(
                        raw_img, cam, cal_w, cal_h, expected_rw=exp_rw_ff, expected_rh=exp_rh_ff
                    )
                    if sift_ff is not None:
                        bx, by, bw, bh = sift_ff["bbox_norm"]
                        moved_ff = (
                            aspect_mismatch
                            or abs(sift_ff["rotation_deg"]) >= 0.75
                            or abs(sift_ff["zoom_ratio"] - 1.0) >= 0.025
                            or abs(bx) >= 0.02
                            or abs(by) >= 0.02
                        )
                        if moved_ff:
                            ff_reason = (
                                f"固定锚点+刚性特征单应性矫正 ({sift_ff['inliers']} 内点 · "
                                f"旋转 {sift_ff['rotation_deg']:+.1f}° · 焦距 {sift_ff['zoom_ratio']:.2f}x)"
                            )
                            rect_info = {
                                **static_info,
                                "enabled": True,
                                "applied": True,
                                "mode": sift_ff["model"],
                                "dynamic_adjusted": True,
                                "rectified_by_homography": True,
                                "rectified_homography_norm": sift_ff["homography_norm"],
                                "raw_quad_norm": sift_ff["raw_quad_norm"],
                                "anchor_matches": sift_ff["anchor_matches"],
                                "rotation_deg": sift_ff["rotation_deg"],
                                "zoom_ratio": sift_ff["zoom_ratio"],
                                "focal_zoom": sift_ff["zoom_ratio"],
                                "inliers": sift_ff["inliers"],
                                "sift_inliers": sift_ff["inliers"],
                                "reason": ff_reason,
                                "note": ff_reason,
                                "dynamic_box": [round(bx, 4), round(by, 4), round(bw, 4), round(bh, 4)],
                            }
                            return sift_ff["rectified_bgr"], rect_info, crop_cfg, None
            # If aspect ratio / orientation differed (e.g. rotated/portrait nighttime phone shot) and SIFT
            # could not lock due to illumination change, allow VLM landmark homography when enabled
            if not (dynamic_enabled and has_vlm_ready and spots and static_info.get("reason") == "aspect_orientation_mismatch"):
                return static_cropped, static_info, crop_cfg, None
            if not isinstance(crop_cfg, dict):
                crop_cfg = {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

        ref_strip_edge = cam.get("ref_strip_edge")
        ref_strip_lab = cam.get("ref_strip_lab")
        rx = float(crop_cfg.get("x", 0.0))
        ry = float(crop_cfg.get("y", 0.0))
        rw = float(crop_cfg.get("w", 1.0))
        rh = float(crop_cfg.get("h", 1.0))

        def _build_box_anchors(
            box_x: float,
            box_y: float,
            box_w: float,
            box_h: float,
            reproj_tf: Optional[Tuple[float, float, float, float]] = None,
        ) -> List[Dict[str, Any]]:
            out_ancs: List[Dict[str, Any]] = []
            for ap in (cam.get("anchor_points") or []):
                try:
                    ax_c, ay_c = self._anchor_to_cal_px(ap, cal_w, cal_h)
                    u_c = ax_c / float(cal_w)
                    v_c = ay_c / float(cal_h)
                    if reproj_tf is not None:
                        sx_r, sy_r, tx_r, ty_r = reproj_tf
                        u_eff = u_c * sx_r + tx_r
                        v_eff = v_c * sy_r + ty_r
                    else:
                        u_eff, v_eff = u_c, v_c
                    out_ancs.append(
                        {
                            "id": str(ap.get("id", "")),
                            "label": str(ap.get("label") or ap.get("name") or ap.get("id") or ""),
                            "cal_px": [round(u_eff * cal_w, 1), round(v_eff * cal_h, 1)],
                            "cal_xy": [round(u_eff, 4), round(v_eff, 4)],
                            "raw_xy": [
                                round(box_x + u_eff * box_w, 4),
                                round(box_y + v_eff * box_h, 4),
                            ],
                        }
                    )
                except Exception:
                    continue
            return out_ancs

        static_anchors = _build_box_anchors(rx, ry, rw, rh)
        if not dynamic_enabled or ref_strip_edge is None:
            static_info["mode"] = "static"
            if static_anchors:
                static_info["anchor_matches"] = static_anchors
            return static_cropped, static_info, crop_cfg, None

        static_score = self._score_strip_window(static_cropped, ref_strip_edge, ref_strip_lab)
        if static_score >= 0.76:
            static_info["mode"] = "static_verified"
            static_info["match_score"] = round(static_score, 3)
            if static_anchors:
                static_info["anchor_matches"] = static_anchors
            return static_cropped, static_info, crop_cfg, None

        # Tier 0: Rigid SIFT + Fixed Positioning Anchors Homography Rectification
        sift_rect = self._rectify_frame_via_sift_anchors(
            raw_img, cam, cal_w, cal_h, expected_rw=rw, expected_rh=rh
        )
        if sift_rect is not None:
            rect_score = self._score_strip_window(sift_rect["rectified_bgr"], ref_strip_edge, ref_strip_lab)
            bx_s, by_s, bw_s, bh_s = sift_rect["bbox_norm"]
            moved_sift = (
                abs(bx_s - rx) + abs(by_s - ry) + abs(bw_s - rw) + abs(bh_s - rh) >= 0.018
                or abs(sift_rect["rotation_deg"]) >= 0.55
                or abs(sift_rect["zoom_ratio"] - 1.0) >= 0.02
            )
            if not moved_sift and static_score >= 0.58:
                del sift_rect["rectified_bgr"]
                static_info["mode"] = "static_verified"
                static_info["match_score"] = round(max(static_score, rect_score), 3)
                static_info["anchor_matches"] = sift_rect.get("anchor_matches") or static_anchors
                return static_cropped, static_info, crop_cfg, None
            if sift_rect["inliers"] >= 8 or rect_score >= max(0.42, static_score + 0.03):
                del static_cropped
                with self._lock:
                    self.privacy_stats["dynamic_roi_adjustments"] += 1
                sift_reason = (
                    f"固定锚点+刚性特征单应性矫正 ({sift_rect['inliers']} 内点 · "
                    f"旋转 {sift_rect['rotation_deg']:+.1f}° · 焦距 {sift_rect['zoom_ratio']:.2f}x)"
                )
                eff_crop_sift = {
                    **crop_cfg,
                    "enabled": True,
                    "x": round(bx_s, 4),
                    "y": round(by_s, 4),
                    "w": round(bw_s, 4),
                    "h": round(bh_s, 4),
                    "dynamic_adjusted": True,
                    "dynamic_source": sift_rect["model"],
                    "rectified_by_homography": True,
                    "rotation_deg": sift_rect["rotation_deg"],
                    "zoom_ratio": sift_rect["zoom_ratio"],
                    "focal_zoom": sift_rect["zoom_ratio"],
                }
                dyn_sift_info = {
                    "enabled": True,
                    "applied": True,
                    "mode": sift_rect["model"],
                    "dynamic_source": sift_rect["model"],
                    "dynamic_adjusted": True,
                    "rectified_by_homography": True,
                    "rectified_homography_norm": sift_rect["homography_norm"],
                    "raw_quad_norm": sift_rect["raw_quad_norm"],
                    "anchor_matches": sift_rect["anchor_matches"],
                    "inliers": sift_rect["inliers"],
                    "sift_inliers": sift_rect["inliers"],
                    "rotation_deg": sift_rect["rotation_deg"],
                    "zoom_ratio": sift_rect["zoom_ratio"],
                    "focal_zoom": sift_rect["zoom_ratio"],
                    "static_box": [round(rx, 4), round(ry, 4), round(rw, 4), round(rh, 4)],
                    "dynamic_box": [round(bx_s, 4), round(by_s, 4), round(bw_s, 4), round(bh_s, 4)],
                    "static_score": round(static_score, 3),
                    "match_score": round(rect_score, 3),
                    "reason": sift_reason,
                    "note": sift_reason,
                }
                return sift_rect["rectified_bgr"], dyn_sift_info, eff_crop_sift, None
            del sift_rect["rectified_bgr"]

        if static_score >= 0.60:
            static_info["mode"] = "static_verified"
            static_info["match_score"] = round(static_score, 3)
            if static_anchors:
                static_info["anchor_matches"] = static_anchors
            return static_cropped, static_info, crop_cfg, None

        # Tier A: Multi-scale foliage-masked sliding search in downscaled raw frame (handles wind tilt, pitch shift & zoom)
        work_w = min(w_raw, 360)
        work_h = max(64, int(round(h_raw * (work_w / float(w_raw)))))
        work_bgr = cv2.resize(raw_img, (work_w, work_h), interpolation=cv2.INTER_AREA)
        work_rigid = build_non_foliage_rigid_mask(work_bgr, min_valid_ratio=0.25) > 0
        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
        wgx = np.abs(cv2.Sobel(work_gray, cv2.CV_32F, 1, 0, ksize=3))
        wgy = np.abs(cv2.Sobel(work_gray, cv2.CV_32F, 0, 1, ksize=3))
        work_edge = np.where(work_rigid, np.sqrt(wgx * wgx + wgy * wgy), 0.0)
        work_edge_u8 = np.clip(work_edge, 0.0, 255.0).astype(np.uint8)

        best_box = (rx, ry, rw, rh)
        best_score = static_score
        best_zoom = 1.0
        candidates: List[Tuple[float, float, float, float, float, float]] = []

        for zoom_s in (0.52, 0.64, 0.76, 0.88, 1.0, 1.14, 1.30, 1.50):
            cw_n = float(np.clip(rw * zoom_s, 0.14, 1.0))
            ch_n = float(np.clip(rh * zoom_s, 0.07, 0.94))
            tpl_w = max(24, min(work_w - 2, int(round(cw_n * work_w))))
            tpl_h = max(16, min(work_h - 2, int(round(ch_n * work_h))))
            sx0 = max(0, int(round(max(0.0, rx - 0.34) * work_w)))
            sx1 = min(work_w, int(round(min(1.0, rx + rw + 0.34) * work_w)))
            sy0 = max(0, int(round(max(0.0, ry - 0.45) * work_h)))
            sy1 = min(work_h, int(round(min(1.0, ry + rh + 0.45) * work_h)))
            if (sx1 - sx0) < tpl_w + 2 or (sy1 - sy0) < tpl_h + 2:
                sx0, sy0, sx1, sy1 = 0, 0, work_w, work_h
            if (sx1 - sx0) < tpl_w or (sy1 - sy0) < tpl_h:
                continue

            tpl_scaled = cv2.resize(ref_strip_edge, (tpl_w, tpl_h), interpolation=cv2.INTER_AREA)
            search_patch = work_edge_u8[sy0:sy1, sx0:sx1]
            res_map = cv2.matchTemplate(search_patch, tpl_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res_map)
            cand_x = float(np.clip((sx0 + max_loc[0]) / float(work_w), 0.0, 1.0 - cw_n))
            cand_y = float(np.clip((sy0 + max_loc[1]) / float(work_h), 0.0, 1.0 - ch_n))
            candidates.append((float(max_val), cand_x, cand_y, cw_n, ch_n, zoom_s))

        del work_bgr
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, cx_n, cy_n, cw_n, ch_n, z_s in candidates[:4]:
            px0 = max(0, min(w_raw - 16, int(round(cx_n * w_raw))))
            py0 = max(0, min(h_raw - 16, int(round(cy_n * h_raw))))
            px1 = max(px0 + 16, min(w_raw, int(round((cx_n + cw_n) * w_raw))))
            py1 = max(py0 + 16, min(h_raw, int(round((cy_n + ch_n) * h_raw))))
            cand_crop = raw_img[py0:py1, px0:px1]
            sc = self._score_strip_window(cand_crop, ref_strip_edge, ref_strip_lab)
            if sc > best_score + 0.045:
                best_score = sc
                best_box = (cx_n, cy_n, cw_n, ch_n)
                best_zoom = z_s

        reproj_transform: Optional[Tuple[float, float, float, float]] = None
        dyn_source = "opencv_multiscale"
        dyn_reason = ""

        # Tier B: Multimodal VLM dynamic stall-constellation grounding when rigid template correlation remains uncertain
        if has_vlm_ready and best_score < 0.66 and spots:
            vlm_loc = self._locate_dynamic_roi_with_vlm(
                raw_img, cam, spots, crop_cfg, vlm_cfg, now_wall
            )
            if vlm_loc is not None and vlm_loc.get("confidence", 0.0) >= 0.55:
                u0, v0, u1, v1 = self._compute_constellation_bounds(spots, cal_w, cal_h)
                quad_n = vlm_loc.get("quad_norm")
                H_c2r_norm = vlm_loc.get("homography_cal_to_raw_norm")
                lm_bundle_b = self._extract_canonical_stall_landmarks(spots, cal_w, cal_h)

                H_vlm: Optional[np.ndarray] = None
                if isinstance(H_c2r_norm, list) and len(H_c2r_norm) == 3:
                    try:
                        H_vlm = (
                            np.diag([float(w_raw), float(h_raw), 1.0])
                            @ np.asarray(H_c2r_norm, dtype=np.float64)
                            @ np.diag([1.0 / float(cal_w), 1.0 / float(cal_h), 1.0])
                        )
                    except Exception:
                        H_vlm = None

                if isinstance(quad_n, list) and len(quad_n) == 4:
                    raw_q_px = np.float32([[pt[0] * w_raw, pt[1] * h_raw] for pt in quad_n])
                    top_vec = raw_q_px[1] - raw_q_px[0]
                    q_rot = float(np.degrees(np.arctan2(top_vec[1], top_vec[0])))
                    bx_q = float(np.clip(min(pt[0] for pt in quad_n), 0.0, 0.94))
                    by_q = float(np.clip(min(pt[1] for pt in quad_n), 0.0, 0.94))
                    bw_q = float(np.clip(max(pt[0] for pt in quad_n) - bx_q, 0.06, 1.0 - bx_q))
                    bh_q = float(np.clip(max(pt[1] for pt in quad_n) - by_q, 0.05, 1.0 - by_q))
                    if lm_bundle_b is not None:
                        cal_q_px = lm_bundle_b["outer_quad_cal"]
                        ref_top_w = float(np.linalg.norm(cal_q_px[1] - cal_q_px[0])) / max(1.0, float(cal_w))
                    else:
                        cal_q_px = np.float32(
                            [[u0 * cal_w, v0 * cal_h], [u1 * cal_w, v0 * cal_h], [u1 * cal_w, v1 * cal_h], [u0 * cal_w, v1 * cal_h]]
                        )
                        ref_top_w = max(0.15, u1 - u0)
                    z_q = round(float(np.linalg.norm(top_vec)) / max(16.0, ref_top_w * rw * w_raw), 3)
                    quad_moved = (
                        H_vlm is not None
                        or abs(bx_q - rx) + abs(by_q - ry) + abs(bw_q - rw) + abs(bh_q - rh) >= 0.022
                        or abs(q_rot) >= 0.8
                        or abs(z_q - 1.0) >= 0.03
                    )
                    if (
                        quad_moved
                        and cv2.isContourConvex(raw_q_px)
                        and float(cv2.contourArea(raw_q_px, oriented=True)) > 100.0
                        and abs(q_rot) <= 45.0
                    ):
                        if H_vlm is None:
                            H_vlm = cv2.getPerspectiveTransform(cal_q_px, raw_q_px).astype(np.float64)
                        rect_vlm = cv2.warpPerspective(
                            raw_img,
                            H_vlm,
                            (cal_w, cal_h),
                            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_REPLICATE,
                        )
                        del static_cropped
                        with self._lock:
                            self.privacy_stats["dynamic_roi_adjustments"] += 1
                        H_vlm_norm = np.diag([1.0 / float(w_raw), 1.0 / float(h_raw), 1.0]) @ H_vlm
                        H_vlm_norm_list = [[round(float(v), 7) for v in row] for row in H_vlm_norm]
                        cal_frame_corners = cv2.perspectiveTransform(
                            np.float32([[[0, 0]], [[cal_w, 0]], [[cal_w, cal_h]], [[0, cal_h]]]),
                            H_vlm,
                        ).reshape(4, 2)
                        raw_frame_quad_norm = [
                            [round(float(c[0]) / float(w_raw), 4), round(float(c[1]) / float(h_raw), 4)]
                            for c in cal_frame_corners
                        ]
                        vlm_anchors: List[Dict[str, Any]] = []
                        for ap in (cam.get("anchor_points") or []):
                            try:
                                ax_c, ay_c = self._anchor_to_cal_px(ap, cal_w, cal_h)
                                pt_raw = cv2.perspectiveTransform(
                                    np.float32([[[ax_c, ay_c]]]), H_vlm
                                )[0, 0]
                                vlm_anchors.append(
                                    {
                                        "id": str(ap.get("id", "")),
                                        "label": str(ap.get("label") or ap.get("name") or ap.get("id") or ""),
                                        "cal_px": [round(float(ax_c), 1), round(float(ay_c), 1)],
                                        "cal_xy": [
                                            round(float(ax_c) / float(cal_w), 4),
                                            round(float(ay_c) / float(cal_h), 4),
                                        ],
                                        "raw_xy": [
                                            round(float(pt_raw[0]) / float(w_raw), 4),
                                            round(float(pt_raw[1]) / float(h_raw), 4),
                                        ],
                                    }
                                )
                            except Exception:
                                continue
                        vlm_mode_name = (
                            "vlm_landmark_homography"
                            if vlm_loc.get("landmark_inliers", 0) >= 4
                            else "vlm_quad_homography"
                        )
                        vlm_q_reason = str(vlm_loc.get("reason") or "AI 地面基准点透视单应性矫正")
                        eff_vlm_q = {
                            **crop_cfg,
                            "enabled": True,
                            "x": round(bx_q, 4),
                            "y": round(by_q, 4),
                            "w": round(bw_q, 4),
                            "h": round(bh_q, 4),
                            "dynamic_adjusted": True,
                            "dynamic_source": vlm_mode_name,
                            "rectified_by_homography": True,
                            "rotation_deg": round(q_rot, 2),
                            "zoom_ratio": z_q,
                            "focal_zoom": z_q,
                        }
                        vlm_q_info = {
                            "enabled": True,
                            "applied": True,
                            "mode": vlm_mode_name,
                            "dynamic_source": vlm_mode_name,
                            "dynamic_adjusted": True,
                            "rectified_by_homography": True,
                            "rectified_homography_norm": H_vlm_norm_list,
                            "raw_quad_norm": raw_frame_quad_norm,
                            "anchor_matches": vlm_anchors,
                            "inliers": int(vlm_loc.get("landmark_inliers", 4)),
                            "landmark_inliers": int(vlm_loc.get("landmark_inliers", 4)),
                            "rotation_deg": round(q_rot, 2),
                            "zoom_ratio": z_q,
                            "focal_zoom": z_q,
                            "static_box": [round(rx, 4), round(ry, 4), round(rw, 4), round(rh, 4)],
                            "dynamic_box": [round(bx_q, 4), round(by_q, 4), round(bw_q, 4), round(bh_q, 4)],
                            "static_score": round(static_score, 3),
                            "match_score": round(float(vlm_loc["confidence"]), 3),
                            "reason": vlm_q_reason,
                            "note": vlm_q_reason,
                        }
                        return rect_vlm, vlm_q_info, eff_vlm_q, None

                iw = (vlm_loc["gx1"] - vlm_loc["gx0"]) / max(0.15, u1 - u0)
                ih = (vlm_loc["gy1"] - vlm_loc["gy0"]) / max(0.15, v1 - v0)
                ix = vlm_loc["gx0"] - u0 * iw
                iy = vlm_loc["gy0"] - v0 * ih

                cx0 = float(np.clip(ix, 0.0, 0.94))
                cy0 = float(np.clip(iy, 0.0, 0.94))
                cx1 = float(np.clip(ix + iw, cx0 + 0.06, 1.0))
                cy1 = float(np.clip(iy + ih, cy0 + 0.05, 1.0))
                cw_c = cx1 - cx0
                ch_c = cy1 - cy0
                best_box = (cx0, cy0, cw_c, ch_c)
                best_zoom = round(cw_c / max(0.05, rw), 3)
                best_score = max(best_score, round(float(vlm_loc["confidence"]), 3))
                dyn_source = "vlm_constellation"
                dyn_reason = str(vlm_loc.get("reason") or "AI 多模态动态车位框定")
                # Exact affine constellation preservation even when ideal margin extends past raw image border
                reproj_transform = (
                    iw / max(1e-4, cw_c),
                    ih / max(1e-4, ch_c),
                    (ix - cx0) / max(1e-4, cw_c),
                    (iy - cy0) / max(1e-4, ch_c),
                )

        bx, by, bw, bh = best_box
        moved_dist = abs(bx - rx) + abs(by - ry) + abs(bw - rw) + abs(bh - rh)
        if moved_dist < 0.022 and reproj_transform is None:
            static_info["mode"] = "static_verified"
            static_info["match_score"] = round(best_score, 3)
            if static_anchors:
                static_info["anchor_matches"] = static_anchors
            return static_cropped, static_info, crop_cfg, None

        del static_cropped
        eff_crop_cfg = {
            **crop_cfg,
            "enabled": True,
            "x": round(bx, 4),
            "y": round(by, 4),
            "w": round(bw, 4),
            "h": round(bh, 4),
            "dynamic_adjusted": True,
            "dynamic_source": dyn_source,
        }
        dyn_cropped, dyn_info = apply_camera_crop_roi(raw_img, eff_crop_cfg, cal_w=cal_w, cal_h=cal_h)
        final_reason = (
            dyn_reason
            or f"检测到机位偏移/缩放，已自动重框定车位区 (Δx={bx - rx:+.2f}, Δy={by - ry:+.2f}, 缩放={best_zoom:.2f}x)"
        )
        dyn_anchors = _build_box_anchors(bx, by, bw, bh, reproj_tf=reproj_transform)
        dyn_info.update(
            {
                "mode": dyn_source,
                "dynamic_source": dyn_source,
                "dynamic_adjusted": True,
                "anchor_matches": dyn_anchors,
                "static_box": [round(rx, 4), round(ry, 4), round(rw, 4), round(rh, 4)],
                "dynamic_box": [round(bx, 4), round(by, 4), round(bw, 4), round(bh, 4)],
                "zoom_ratio": round(best_zoom, 3),
                "focal_zoom": round(best_zoom, 3),
                "match_score": round(best_score, 3),
                "static_score": round(static_score, 3),
                "reason": final_reason,
                "note": final_reason,
            }
        )
        with self._lock:
            self.privacy_stats["dynamic_roi_adjustments"] += 1
        return dyn_cropped, dyn_info, eff_crop_cfg, reproj_transform

    def align_frame_to_calibrated_canvas(
        self,
        camera_id: str,
        raw_img: np.ndarray,
        spots_override: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Align a secondary reference upload (empty-ground template, parked-vehicle 3D template,
        or line-refinement image) into the camera's existing `(cal_w, cal_h)` calibration canvas
        using SIFT + `A1–A4` homography / dynamic crop / ORB alignment rather than blind static slicing.
        """
        cam = self._get_or_load_template_bundle(camera_id) or self.db.get_camera(camera_id, include_blobs=False)
        if not cam:
            return raw_img, {"applied": False, "aligned": False}

        spots = spots_override if spots_override is not None else self.db.list_spots(camera_id=camera_id)
        cal_w = max(1, int(cam.get("image_width") or raw_img.shape[1]))
        cal_h = max(1, int(cam.get("image_height") or raw_img.shape[0]))
        has_calibrated_ref = bool(spots) and (bool(cam.get("has_template")) or cam.get("sift_des") is not None)
        if not has_calibrated_ref:
            cropped, cinfo = apply_camera_crop_roi(raw_img, cam.get("crop_roi"), cal_w=cal_w, cal_h=cal_h)
            return cropped, cinfo

        cfg = config_manager.get()
        vlm_cfg = cfg.get("vlm", {})
        base_u = str(vlm_cfg.get("base_url") or "").strip()
        has_vlm_ready = bool(base_u) and (
            bool(vlm_cfg.get("api_key")) or "127.0.0.1" in base_u or "localhost" in base_u
        )
        aligned_img, crop_info, _, _ = self._resolve_dynamic_crop_roi(
            raw_img=raw_img,
            cam=cam,
            spots=spots,
            cal_w=cal_w,
            cal_h=cal_h,
            dynamic_enabled=True,
            has_vlm_ready=has_vlm_ready,
            vlm_cfg=vlm_cfg,
            now_wall=time.time(),
        )
        if not crop_info.get("rectified_by_homography"):
            aligned_img, orb_info = self._align_to_reference_template(
                aligned_img,
                cam.get("template_gray_b64"),
                cal_w,
                cal_h,
                cached_ref_small=cam.get("ref_small"),
                cached_kp_pts=cam.get("orb_kp_pts"),
                cached_des=cam.get("orb_des"),
                cached_rigid_mask=cam.get("orb_rigid_mask"),
            )
            if orb_info.get("aligned"):
                crop_info = {**crop_info, "orb_aligned": True, "shift_px": orb_info.get("shift_px", 0.0)}
        return aligned_img, crop_info

    @staticmethod
    def _align_to_reference_template(
        full_img: np.ndarray,
        template_gray_b64: Optional[str],
        cal_w: int,
        cal_h: int,
        cached_ref_small: Optional[np.ndarray] = None,
        cached_kp_pts: Optional[np.ndarray] = None,
        cached_des: Optional[np.ndarray] = None,
        cached_rigid_mask: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Compensate wind sway, camera mount displacement, and focal zoom against the saved
        Reference Template using foliage-masked ORB keypoints + RANSAC Similarity/Homography.
        Suppresses swaying green tree crowns so gale-force leaf motion cannot skew alignment.
        """
        if cached_ref_small is None and not template_gray_b64:
            return full_img, {"active": False, "aligned": False, "shift_px": 0.0}

        try:
            ref_small = cached_ref_small
            if ref_small is None and template_gray_b64:
                ref_bytes = base64.b64decode(template_gray_b64)
                ref_small = cv2.imdecode(np.frombuffer(ref_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if ref_small is None:
                return full_img, {"active": False, "aligned": False, "shift_px": 0.0}

            rh, rw = ref_small.shape[:2]
            cur_bgr_small = cv2.resize(full_img, (rw, rh), interpolation=cv2.INTER_AREA)
            cur_rigid_mask = build_non_foliage_rigid_mask(cur_bgr_small)
            cur_small = cv2.cvtColor(cur_bgr_small, cv2.COLOR_BGR2GRAY)
            del cur_bgr_small

            orb = cv2.ORB_create(nfeatures=420)
            kp1, des1 = orb.detectAndCompute(cur_small, cur_rigid_mask)
            if not kp1 or des1 is None or len(kp1) < 20:
                kp1, des1 = orb.detectAndCompute(cur_small, None)

            if cached_kp_pts is not None and cached_des is not None:
                kp2_pts = cached_kp_pts
                des2 = cached_des
            else:
                kp2, des2 = orb.detectAndCompute(ref_small, cached_rigid_mask)
                if not kp2 or des2 is None or len(kp2) < 20:
                    kp2, des2 = orb.detectAndCompute(ref_small, None)
                kp2_pts = np.float32([kp.pt for kp in kp2]) if kp2 else None

            if des1 is None or des2 is None or kp2_pts is None or len(kp1) < 18 or len(kp2_pts) < 18:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0, "foliage_masked": True}

            matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = sorted(matcher.match(des1, des2), key=lambda m: m.distance)[:100]
            if len(matches) < 16:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0, "foliage_masked": True}

            src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2_pts[m.trainIdx] for m in matches]).reshape(-1, 1, 2)

            # Prefer 4-DOF similarity transform (translation + rotation + uniform focal zoom) for wind & zoom resilience,
            # upgrading to full planar Homography only when perspective inliers are strong and well-conditioned.
            M_sim, sim_inliers = cv2.estimateAffinePartial2D(
                src_pts, dst_pts, method=cv2.RANSAC, ransacReprojThreshold=3.8
            )
            sim_cnt = int(np.sum(sim_inliers)) if sim_inliers is not None else 0
            H_cand: Optional[np.ndarray] = None
            inlier_cnt = 0
            model_used = "similarity_4dof"

            if M_sim is not None and sim_cnt >= 14:
                H_cand = np.vstack([M_sim, [0.0, 0.0, 1.0]]).astype(np.float64)
                inlier_cnt = sim_cnt

            H_hom, hom_inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 3.5)
            hom_cnt = int(np.sum(hom_inliers)) if hom_inliers is not None else 0
            if H_hom is not None and hom_cnt >= max(18, sim_cnt + 3):
                hom_det = float(np.linalg.det(H_hom[:2, :2]))
                persp_norm = abs(float(H_hom[2, 0])) * rw + abs(float(H_hom[2, 1])) * rh
                if 0.62 <= hom_det <= 1.65 and persp_norm < 0.14:
                    H_cand = H_hom
                    inlier_cnt = hom_cnt
                    model_used = "homography_8dof"

            if H_cand is None or inlier_cnt < 14:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0, "foliage_masked": True}

            det = float(np.linalg.det(H_cand[:2, :2]))
            # Support focal zoom between ~0.72x and ~1.40x (det in [0.52, 1.95])
            if not (0.52 <= det <= 1.95):
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0, "foliage_masked": True}

            corners = np.float32([[[0, 0]], [[rw, 0]], [[rw, rh]], [[0, rh]]])
            warped_corners = cv2.perspectiveTransform(corners, H_cand)
            corner_disp = np.linalg.norm(warped_corners - corners, axis=2)
            mean_disp_small = float(np.mean(corner_disp))
            max_disp_small = float(np.max(corner_disp))

            max_allowed_ratio = 0.28 if inlier_cnt >= 18 else 0.10
            if max_disp_small > max(rw, rh) * max_allowed_ratio:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0, "foliage_masked": True}

            zoom_scale = round(float(np.sqrt(max(0.01, det))), 3)
            h_img, w_img = full_img.shape[:2]
            scale_back = w_img / max(1.0, float(rw))
            shift_full_px = round(mean_disp_small * scale_back, 2)
            if shift_full_px < 1.4 and abs(zoom_scale - 1.0) < 0.015:
                return full_img, {
                    "active": True,
                    "aligned": False,
                    "shift_px": shift_full_px,
                    "zoom_scale": zoom_scale,
                    "foliage_masked": True,
                }

            S_down = np.array([[rw / w_img, 0, 0], [0, rh / h_img, 0], [0, 0, 1]], dtype=np.float64)
            S_up = np.array([[cal_w / rw, 0, 0], [0, cal_h / rh, 0], [0, 0, 1]], dtype=np.float64)
            H_full = S_up @ H_cand @ S_down
            aligned = cv2.warpPerspective(
                full_img, H_full, (cal_w, cal_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )
            try:
                H_full_inv = np.linalg.inv(H_full)
            except Exception:
                H_full_inv = None
            return aligned, {
                "active": True,
                "aligned": True,
                "model": model_used,
                "shift_px": shift_full_px,
                "scale": zoom_scale,
                "zoom_scale": zoom_scale,
                "inliers": inlier_cnt,
                "foliage_masked": True,
                "H_full_inv": H_full_inv,
            }
        except Exception:
            return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

    @staticmethod
    def _snap_scaled_polygons_to_white_lines(
        img_bgr: np.ndarray,
        spots: List[Dict[str, Any]],
        scaled_polys: List[List[List[float]]],
    ) -> List[List[List[float]]]:
        """
        Rigid-only perspective-preserving micro-shift compensation on incoming snapshots:
        Never warps individual stall vertices (preventing parked-car occlusion or border edges
        from distorting calibrated polygons). Only applies a small uniform `(dx, dy)` shift
        when >= 2 visible ground dividers match the calibrated dividers' perspective slopes.
        """
        h, w = img_bgr.shape[:2]
        std_idxs = [
            i
            for i, sp in enumerate(spots)
            if sp.get("spot_type") == "standard" and len(scaled_polys[i]) == 4
        ]
        if len(std_idxs) < 2:
            return scaled_polys

        ys_top = [0.5 * (scaled_polys[i][0][1] + scaled_polys[i][1][1]) for i in std_idxs]
        ys_bot = [0.5 * (scaled_polys[i][2][1] + scaled_polys[i][3][1]) for i in std_idxs]
        med_top = float(np.median(ys_top))
        med_bot = float(np.median(ys_bot))
        row_h = max(20.0, med_bot - med_top)
        row_idxs = [
            idx
            for idx, yt, yb in zip(std_idxs, ys_top, ys_bot)
            if abs(yt - med_top) < row_h * 0.40 and abs(yb - med_bot) < row_h * 0.40
        ]
        if len(row_idxs) < 2:
            return scaled_polys

        from app.auto_calibrator import ReferenceAutoCalibrator

        front_y, dividers = ReferenceAutoCalibrator._detect_white_stall_features(
            img_bgr, int(round(med_top)), int(round(med_bot))
        )
        if not dividers and front_y is None:
            return scaled_polys

        dy = 0.0
        if front_y is not None and abs(front_y - med_bot) <= h * 0.025:
            dy = float(front_y - med_bot)

        # Collect calibrated divider edges `(xt_cal, xb_cal)` and compare with detected dividers of matching slope
        cal_edges: List[Tuple[float, float]] = []
        row_sorted = sorted(row_idxs, key=lambda idx: 0.25 * sum(p[0] for p in scaled_polys[idx]))
        cal_edges.append((scaled_polys[row_sorted[0]][0][0], scaled_polys[row_sorted[0]][3][0]))
        for idx in row_sorted:
            cal_edges.append((scaled_polys[idx][1][0], scaled_polys[idx][2][0]))

        dx_votes: List[float] = []
        denom = max(15.0, med_bot - med_top)
        for xt_c, xb_c in cal_edges:
            s_cal = (xb_c - xt_c) / denom
            mid_c = 0.5 * (xt_c + xb_c)
            for dt, db in dividers:
                s_det = (db - dt) / denom
                mid_d = 0.5 * (dt + db)
                if abs(s_det - s_cal) <= 0.14 and abs(mid_d - mid_c) <= w * 0.026:
                    dx_votes.append(mid_d - mid_c)
                    break

        dx = float(np.median(dx_votes)) if len(dx_votes) >= 2 else 0.0
        if abs(dx) < 1.0 and abs(dy) < 1.0:
            return scaled_polys

        out_polys = [[list(pt) for pt in poly] for poly in scaled_polys]
        for idx in row_idxs:
            out_polys[idx] = [
                [
                    round(float(np.clip(pt[0] + dx, 0.0, w - 1.0)), 1),
                    round(float(np.clip(pt[1] + dy, 0.0, h - 1.0)), 1),
                ]
                for pt in out_polys[idx]
            ]
        return out_polys

    @staticmethod
    def _draw_dashed_polyline(
        canvas: np.ndarray,
        pts: np.ndarray,
        color: Tuple[int, int, int],
        thickness: int = 2,
        dash_len: int = 8,
        gap_len: int = 5,
    ) -> None:
        n = len(pts)
        if n < 2:
            return
        for i in range(n):
            p1 = pts[i].astype(np.float32)
            p2 = pts[(i + 1) % n].astype(np.float32)
            seg = p2 - p1
            dist = float(np.linalg.norm(seg))
            if dist < 1.0:
                continue
            unit = seg / dist
            pos = 0.0
            while pos < dist:
                end_pos = min(dist, pos + dash_len)
                a = tuple(np.round(p1 + unit * pos).astype(int))
                b = tuple(np.round(p1 + unit * end_pos).astype(int))
                cv2.line(canvas, a, b, color, thickness, lineType=cv2.LINE_AA)
                pos += dash_len + gap_len

    @staticmethod
    def _make_dual_signatures_32(
        ground_patch_bgr: np.ndarray,
        veh_patch_bgr: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Returns `(ground_gray_32, composite_diff_sig_32)`:
          - `ground_gray_32` (`32x32 uint8`, 1 KB): ground-only signature compatible with `ref_free_sig_b64`
          - `composite_diff_sig_32` (`32x32 uint8`, 1 KB): top 12 rows encode 3D vehicle cabin envelope +
            bottom 20 rows encode ground stall core, so Stage-1 frame-diff catches both ground & 3D changes
            with ZERO extra RAM overhead.
        """
        ground_gray_32 = cv2.resize(cv2.cvtColor(ground_patch_bgr, cv2.COLOR_BGR2GRAY), (32, 32))
        if veh_patch_bgr is None or veh_patch_bgr.size == 0:
            return ground_gray_32, ground_gray_32
        vh = veh_patch_bgr.shape[0]
        upper_veh = cv2.cvtColor(veh_patch_bgr[: max(16, int(vh * 0.62)), :], cv2.COLOR_BGR2GRAY)
        veh_top_12 = cv2.resize(upper_veh, (32, 12))
        ground_mid_20 = cv2.resize(ground_gray_32[6:30, :], (32, 20))
        composite_32 = np.vstack([veh_top_12, ground_mid_20]).astype(np.uint8)
        return ground_gray_32, composite_32

    @classmethod
    def _extract_vlm_context_crop(
        cls,
        img_bgr: np.ndarray,
        poly: List[List[float]],
        label: str,
        vehicle_poly: Optional[List[List[float]]] = None,
    ) -> np.ndarray:
        """
        Extracts a neighbor-aware unwarped perspective crop around `poly` + `vehicle_poly`,
        drawing BOTH:
          - Solid Cyan (`#00FFFF`): Ground Footprint (`地面标定层` - where tires contact pavement)
          - Dashed Gold (`#FFD60A`): 3D Vehicle Envelope (`车辆立体层` - where car roof/windshield
            projects under camera perspective)
        so VLM accurately fuses ground + 3D vehicle silhouette without misjudging oblique roofs as neighbors.
        """
        h_img, w_img = img_bgr.shape[:2]
        all_pts = list(poly) + (list(vehicle_poly) if vehicle_poly else [])
        xs = [float(p[0]) for p in all_pts]
        ys = [float(p[1]) for p in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        sw = max(24.0, max_x - min_x)
        sh = max(24.0, max_y - min_y)

        cx0 = max(0, int(round(min_x - sw * 0.45)))
        cx1 = min(w_img, int(round(max_x + sw * 0.45)))
        cy0 = max(0, int(round(min_y - sh * 0.30)))
        cy1 = min(h_img, int(round(max_y + sh * 0.18)))

        sub = img_bgr[cy0:cy1, cx0:cx1].copy()
        sh_sub, sw_sub = sub.shape[:2]
        scale = min(1.0, 448.0 / max(1, sh_sub, sw_sub))
        if scale < 1.0:
            sub = cv2.resize(
                sub,
                (max(32, int(round(sw_sub * scale))), max(32, int(round(sh_sub * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        local_pts = np.array(
            [[int(round((p[0] - cx0) * scale)), int(round((p[1] - cy0) * scale))] for p in poly],
            dtype=np.int32,
        )
        if vehicle_poly and len(vehicle_poly) >= 3:
            local_vpts = np.array(
                [[int(round((p[0] - cx0) * scale)), int(round((p[1] - cy0) * scale))] for p in vehicle_poly],
                dtype=np.int32,
            )
            # Connect ground-to-3D extrusion pillars faintly so 3D prism relationship is unmistakable
            if len(local_pts) == len(local_vpts):
                for gp, vp in zip(local_pts, local_vpts):
                    cv2.line(sub, tuple(gp), tuple(vp), (90, 200, 255), 1, lineType=cv2.LINE_AA)
            cls._draw_dashed_polyline(sub, local_vpts, color=(0, 214, 255), thickness=2, dash_len=8, gap_len=5)

        # Solid cyan ground footprint outline
        cv2.polylines(sub, [local_pts], isClosed=True, color=(255, 255, 0), thickness=2, lineType=cv2.LINE_AA)
        tag = f"#{label}"
        tx = int(np.clip(np.mean(local_pts[:, 0]) - 18, 4, max(4, sub.shape[1] - 54)))
        ty = int(np.clip(np.max(local_pts[:, 1]) + 14, 16, sub.shape[0] - 5))
        cv2.rectangle(sub, (tx - 3, ty - 12), (tx + 42, ty + 3), (18, 18, 22), -1)
        cv2.putText(
            sub, tag, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 0), 1, cv2.LINE_AA
        )
        return sub

    def process_snapshot_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        force_reanalyze: bool = False,
        override_instant_confirm: Optional[bool] = None,
        simulated_timestamp: Optional[float] = None,
        preview_spots: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        cfg = config_manager.get()
        self.cache_budget_mb = max(4.0, float(cfg.get("cache_budget_mb", self.cache_budget_mb)))
        now = simulated_timestamp if simulated_timestamp is not None else time.time()
        wall_now = time.time()

        tpl_bundle = self._get_or_load_template_bundle(camera_id)
        if not tpl_bundle:
            raise ValueError(f"未找到相机配置: {camera_id}")
        cam = tpl_bundle
        cached_spot_ref_sigs: Dict[str, np.ndarray] = cam.get("spot_ref_sigs") or {}

        sandbox_mode = bool(isinstance(preview_spots, list) and len(preview_spots) > 0)
        if sandbox_mode:
            spots = []
            with self._lock:
                for idx, raw_sp in enumerate(preview_spots[:24]):
                    if not isinstance(raw_sp, dict):
                        continue
                    poly = raw_sp.get("polygon")
                    if not isinstance(poly, list) or len(poly) < 3:
                        continue
                    sp_id = str(raw_sp.get("spot_id") or f"{camera_id}_SANDBOX_{idx + 1:02d}")
                    sb_key = f"{camera_id}::{sp_id}"
                    existing_sb = self._sandbox_spots.get(sb_key)
                    if existing_sb is None:
                        existing_sb = {
                            "spot_id": sp_id,
                            "camera_id": camera_id,
                            "zone_id": cam["zone_id"],
                            "label": str(raw_sp.get("label") or f"沙盒-{idx + 1:02d}"),
                            "spot_type": str(raw_sp.get("spot_type") or "standard"),
                            "polygon": [[float(pt[0]), float(pt[1])] for pt in poly],
                            "vehicle_polygon": raw_sp.get("vehicle_polygon"),
                            "total_meters": float(raw_sp.get("total_meters") or 5.2),
                            "status": "FREE",
                            "free_units": 1,
                            "occupied_units": 0,
                            "pending_status": None,
                            "pending_free_units": None,
                            "pending_since": None,
                            "confidence": 0.9,
                            "last_reason": "沙盒初始状态",
                            "last_engine": "sandbox_init",
                            "obstacle_type": "none",
                            "action_hint": "",
                            "ref_free_sig_b64": None,
                        }
                        self._sandbox_spots[sb_key] = existing_sb
                        while len(self._sandbox_spots) > 64:
                            self._sandbox_spots.popitem(last=False)
                    else:
                        existing_sb["polygon"] = [[float(pt[0]), float(pt[1])] for pt in poly]
                        existing_sb["label"] = str(raw_sp.get("label") or existing_sb["label"])
                        if raw_sp.get("vehicle_polygon"):
                            existing_sb["vehicle_polygon"] = raw_sp.get("vehicle_polygon")
                        self._sandbox_spots.move_to_end(sb_key)
                    spots.append(existing_sb)
        else:
            spots = self.db.list_spots(camera_id=camera_id, include_signatures=not bool(cached_spot_ref_sigs))
        vlm_cfg = cfg.get("vlm", {})
        vlm_mode = vlm_cfg.get("mode", "hybrid")
        base_u = str(vlm_cfg.get("base_url", "")).strip()
        has_vlm_ready = (
            vlm_mode != "local_only"
            and bool(base_u)
            and (
                bool(vlm_cfg.get("api_key"))
                or "127.0.0.1" in base_u
                or "localhost" in base_u
            )
        )

        extracted_patches: List[
            Tuple[
                Dict[str, Any],
                np.ndarray,
                np.ndarray,
                Optional[np.ndarray],
                Optional[np.ndarray],
                Optional[np.ndarray],
                List[List[float]],
                List[List[float]],
            ]
        ] = []

        with self.decode_guard():
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if full_img is None:
                raise ValueError("无法解码上传的图片字节流，请确认格式为 JPG/PNG/WebP")

            h_raw, w_raw = full_img.shape[:2]
            if max(h_raw, w_raw) > MAX_DECODE_LONG_EDGE:
                cap_scale = MAX_DECODE_LONG_EDGE / float(max(h_raw, w_raw))
                resized = cv2.resize(
                    full_img,
                    (max(64, int(round(w_raw * cap_scale))), max(64, int(round(h_raw * cap_scale)))),
                    interpolation=cv2.INTER_AREA,
                )
                del full_img
                full_img = resized

            h_img, w_img = full_img.shape[:2]
            amb_v_sample = np.max(full_img[::8, ::8], axis=2)
            amb_p50 = float(np.percentile(amb_v_sample, 50)) if amb_v_sample.size else 128.0
            amb_p90 = float(np.percentile(amb_v_sample, 90)) if amb_v_sample.size else 180.0
            is_low_light_frame = bool(amb_p50 < 56.0 and amb_p90 < 138.0)
            cal_w = max(1, int(cam.get("image_width") or w_raw))
            cal_h = max(1, int(cam.get("image_height") or h_raw))

            dynamic_roi_enabled = bool(cfg.get("dynamic_roi_enabled", True)) and (
                not sandbox_mode or bool(cam.get("has_template"))
            )
            full_img, crop_info, eff_crop_roi, reproj_tf = self._resolve_dynamic_crop_roi(
                full_img,
                cam,
                spots,
                cal_w=cal_w,
                cal_h=cal_h,
                dynamic_enabled=dynamic_roi_enabled,
                has_vlm_ready=has_vlm_ready,
                vlm_cfg=vlm_cfg,
                now_wall=wall_now,
            )
            h_img, w_img = full_img.shape[:2]

            if not sandbox_mode:
                self._cache_latest_check_frame(camera_id, full_img, wall_now)

            if crop_info.get("rectified_by_homography"):
                tpl_align_info = {
                    "active": True,
                    "aligned": True,
                    "model": str(crop_info.get("mode") or "sift_anchor_homography_8dof"),
                    "shift_px": 0.0,
                    "scale": float(crop_info.get("zoom_ratio") or 1.0),
                    "zoom_scale": float(crop_info.get("zoom_ratio") or 1.0),
                    "rotation_deg": float(crop_info.get("rotation_deg") or 0.0),
                    "inliers": int(crop_info.get("inliers") or 4),
                    "foliage_masked": True,
                    "crop_roi": crop_info,
                }
                if sandbox_mode:
                    tpl_align_info["note"] = "内存沙盒自检模式（已启用固定锚点单应性矫正，不修改数据库）"
                reproj_tf = None
            elif sandbox_mode:
                tpl_align_info = {
                    "enabled": False,
                    "aligned": False,
                    "crop_roi": crop_info,
                    "note": "内存沙盒自检模式（不修改数据库与参考模板）",
                }
            else:
                pre_align_w, pre_align_h = w_img, h_img
                full_img, tpl_align_info = self._align_to_reference_template(
                    full_img,
                    cam.get("template_gray_b64"),
                    cal_w,
                    cal_h,
                    cached_ref_small=cam.get("ref_small"),
                    cached_kp_pts=cam.get("orb_kp_pts"),
                    cached_des=cam.get("orb_des"),
                    cached_rigid_mask=cam.get("ref_rigid_mask"),
                )
                if tpl_align_info.get("aligned") and tpl_align_info.get("H_full_inv") is not None:
                    try:
                        H_inv = np.asarray(tpl_align_info.pop("H_full_inv"), dtype=np.float64)
                        active_box = (
                            crop_info.get("dynamic_box")
                            or crop_info.get("static_box")
                            or (
                                [
                                    float(eff_crop_roi.get("x", 0.0)),
                                    float(eff_crop_roi.get("y", 0.0)),
                                    float(eff_crop_roi.get("w", 1.0)),
                                    float(eff_crop_roi.get("h", 1.0)),
                                ]
                                if isinstance(eff_crop_roi, dict) and eff_crop_roi.get("enabled")
                                else [0.0, 0.0, 1.0, 1.0]
                            )
                        )
                        bx_a, by_a, bw_a, bh_a = [float(v) for v in active_box]
                        M_box = np.array(
                            [
                                [bw_a / max(1.0, float(pre_align_w)), 0.0, bx_a],
                                [0.0, bh_a / max(1.0, float(pre_align_h)), by_a],
                                [0.0, 0.0, 1.0],
                            ],
                            dtype=np.float64,
                        )
                        H_comb = M_box @ H_inv
                        if abs(float(H_comb[2, 2])) > 1e-8:
                            H_comb = H_comb / float(H_comb[2, 2])
                            crop_info["rectified_by_homography"] = True
                            crop_info["rectified_homography_norm"] = [
                                [round(float(v), 7) for v in row] for row in H_comb
                            ]
                    except Exception:
                        tpl_align_info.pop("H_full_inv", None)
                else:
                    tpl_align_info.pop("H_full_inv", None)
                tpl_align_info["crop_roi"] = crop_info
                if tpl_align_info.get("aligned"):
                    reproj_tf = None
            h_img, w_img = full_img.shape[:2]
            # Scale spot polygons proportionally (with constellation clamping compensation if needed)
            sx = w_img / cal_w
            sy = h_img / cal_h

            def _map_cal_pt(pt_xy: List[float]) -> List[float]:
                ux = float(pt_xy[0]) / float(cal_w)
                vy = float(pt_xy[1]) / float(cal_h)
                if reproj_tf is not None:
                    ux = reproj_tf[2] + ux * reproj_tf[0]
                    vy = reproj_tf[3] + vy * reproj_tf[1]
                return [
                    float(np.clip(ux * w_img, 0.0, w_img - 1.0)),
                    float(np.clip(vy * h_img, 0.0, h_img - 1.0)),
                ]

            raw_scaled_polys = [
                [_map_cal_pt(p) for p in sp["polygon"]] for sp in spots
            ]
            # Never snap polygons onto white cars when reference template / fixed anchors already lock canonical geometry!
            anchor_locked_coords = bool(
                sandbox_mode
                or cam.get("has_template")
                or crop_info.get("rectified_by_homography")
                or crop_info.get("anchor_matches")
                or tpl_align_info.get("aligned")
            )
            snapped_polys = (
                raw_scaled_polys
                if anchor_locked_coords
                else self._snap_scaled_polygons_to_white_lines(
                    full_img, spots, raw_scaled_polys
                )
            )

            for sp, raw_s_poly, s_poly in zip(spots, raw_scaled_polys, snapped_polys):
                patch, mask = extract_spot_patch(full_img, s_poly, spot_type=sp["spot_type"])
                dx_snap = (s_poly[0][0] - raw_s_poly[0][0]) if (s_poly and raw_s_poly) else 0.0
                dy_snap = (s_poly[0][1] - raw_s_poly[0][1]) if (s_poly and raw_s_poly) else 0.0

                veh_patch: Optional[np.ndarray] = None
                veh_mask: Optional[np.ndarray] = None
                if sp["spot_type"] != "elastic_strip":
                    cal_vpoly = sp.get("vehicle_polygon")
                    if not isinstance(cal_vpoly, list) or len(cal_vpoly) < 3:
                        cal_vpoly = derive_vehicle_envelope_polygon(sp["polygon"], cal_w, cal_h)
                    s_vpoly = [
                        [
                            float(np.clip(_map_cal_pt(vp)[0] + dx_snap, 0.0, w_img - 1.0)),
                            float(np.clip(_map_cal_pt(vp)[1] + dy_snap, 0.0, h_img - 1.0)),
                        ]
                        for vp in cal_vpoly
                    ]
                    veh_patch, veh_mask = extract_spot_patch(
                        full_img, s_vpoly, spot_type=sp["spot_type"], out_size=(160, 160)
                    )
                    eff_cal_vpoly = [
                        [round(pt[0] / sx, 1), round(pt[1] / sy, 1)] for pt in s_vpoly
                    ]
                else:
                    s_vpoly = None
                    eff_cal_vpoly = [[round(pt[0] / sx, 1), round(pt[1] / sy, 1)] for pt in s_poly]

                ctx_crop = (
                    self._extract_vlm_context_crop(
                        full_img, s_poly, sp["label"], vehicle_poly=s_vpoly
                    )
                    if (has_vlm_ready and sp["spot_type"] != "elastic_strip")
                    else None
                )
                eff_cal_poly = [
                    [round(pt[0] / sx, 1), round(pt[1] / sy, 1)] for pt in s_poly
                ]
                extracted_patches.append(
                    (sp, patch, mask, veh_patch, veh_mask, ctx_crop, eff_cal_poly, eff_cal_vpoly)
                )

            # Immediately destroy raw full-frame image in RAM (Zero-Disk, Zero-Retention Privacy Guarantee)
            del full_img

        with self._lock:
            self.privacy_stats["frames_processed_in_ram"] += 1
            self._sync_memory_stats_locked()

        self.db.record_camera_heartbeat(camera_id, ts=now)

        debounce_sec = float(cfg.get("debounce_seconds", 3.0))
        first_frame_vlm_anchor = bool(cfg.get("first_frame_vlm_anchor", False))
        hybrid_conf_thr = float(
            np.clip(float(cfg.get("hybrid_vlm_confidence_threshold", 0.85)), 0.50, 0.99)
        )
        instant_mode = (
            override_instant_confirm
            if override_instant_confirm is not None
            else bool(cfg.get("instant_confirm_mode", False))
        )
        diff_thresh = float(cfg.get("ssim_change_threshold", 0.035))
        car_len_m = float(cfg.get("standard_car_length_m", 5.2))
        gate_ctx = self._collect_gate_entry_context(cam["zone_id"], now)

        spot_reports: List[Optional[Dict[str, Any]]] = []
        pending_slots: List[Dict[str, Any]] = []
        vlm_jobs: List[Dict[str, Any]] = []
        vlm_attempted_spots = 0
        vlm_succeeded_spots = 0
        vlm_fallback_error: Optional[str] = None

        try:
            while extracted_patches:
                sp, patch, mask, veh_patch, veh_mask, ctx_crop, eff_cal_poly, eff_cal_vpoly = (
                    extracted_patches.pop(0)
                )
                keep_vlm_input = False
                try:
                    sig_key = f"{camera_id}::{sp['spot_id']}"
                    small_gray, composite_sig = self._make_dual_signatures_32(patch, veh_patch)

                    prev_sig = self._get_signature_lru(sig_key)
                    if prev_sig is not None:
                        diff_score = float(
                            np.mean(
                                np.abs(composite_sig.astype(np.float32) - prev_sig.astype(np.float32))
                            )
                            / 255.0
                        )
                    else:
                        diff_score = 1.0

                    # Note: If spot currently has a pending debounce check waiting for confirmation, NEVER skip via framediff!
                    has_pending = sp.get("pending_status") is not None
                    if (not force_reanalyze) and (not has_pending) and diff_score < diff_thresh:
                        with self._lock:
                            self.privacy_stats["spots_skipped_by_framediff"] += 1
                        if sandbox_mode:
                            sp["last_seen_at"] = now
                        else:
                            self.db.update_spot_state(sp["spot_id"], {"last_seen_at": now})
                        spot_reports.append(
                            {
                                "spot_id": sp["spot_id"],
                                "label": sp["label"],
                                "spot_type": sp["spot_type"],
                                "effective_polygon": eff_cal_poly,
                                "effective_vehicle_polygon": eff_cal_vpoly,
                                "diff_score": round(diff_score, 4),
                                "stage_used": "Stage1_FrameDiff_Skipped",
                                "observed_status": sp["status"],
                                "obstacle_type": sp.get("obstacle_type", "none"),
                                "action_hint": sp.get("action_hint", ""),
                                "confidence": sp["confidence"],
                                "reason": "画面静止无变化（双层帧差拦截跳过推理）",
                                "motion": {
                                    "state": None,
                                    "localized_pose": "PARKED_IN_SLOT" if sp["status"] == "OCCUPIED" else "CLEAR_EMPTY",
                                    "position_verified_stationary": True,
                                },
                                "fsm": {
                                    "transition": "SKIPPED_STATIC",
                                    "committed_status": sp["status"],
                                    "effective_status": sp["status"],
                                    "pending_status": None,
                                    "free_units": sp["free_units"],
                                    "effective_free_units": sp["free_units"] if sp["status"] == "FREE" else 0,
                                    "occupied_units": sp["occupied_units"],
                                    "obstacle_type": sp.get("obstacle_type", "none"),
                                    "action_hint": sp.get("action_hint", ""),
                                    "note": "帧差 < 阈值，0算力跳过",
                                },
                            }
                        )
                        continue

                    self._put_signature_lru(sig_key, composite_sig)
                    with self._lock:
                        self.privacy_stats["fast_lane_inferences"] += 1

                    # Stage 2: Fast-lane Dual-Layer OpenCV analysis (Ground Footprint + 3D Vehicle Envelope)
                    ref_sig_arr: Optional[np.ndarray] = cached_spot_ref_sigs.get(sp["spot_id"])
                    if ref_sig_arr is None and sp.get("ref_free_sig_b64"):
                        try:
                            raw_sig = base64.b64decode(sp["ref_free_sig_b64"])
                            if len(raw_sig) == 1024:
                                ref_sig_arr = np.frombuffer(raw_sig, dtype=np.uint8).reshape((32, 32))
                        except Exception:
                            ref_sig_arr = None

                    fast_res = self._analyze_patch_fast_opencv(
                        patch,
                        mask,
                        spot_type=sp["spot_type"],
                        total_meters=float(sp["total_meters"]),
                        car_len_m=car_len_m,
                        ref_free_sig=ref_sig_arr,
                        small_gray=small_gray,
                        veh_patch_bgr=veh_patch,
                        veh_valid_mask=veh_mask,
                        enable_dual_layer=True,
                    )

                    final_obs = fast_res
                    stage_used = "Stage2_DualLayer_OpenCV" if veh_patch is not None else "Stage2_Fast_OpenCV"

                    # Stage 3: Invoke Slow-lane Multimodal VLM (First-Frame Semantic Anchor + Perceptual Patch Cache)
                    need_vlm = False
                    first_frame_triggered = False
                    pending_st = sp.get("pending_status")
                    is_first_state_change = (
                        (pending_st is None and fast_res["status"] != sp["status"])
                        or (pending_st is not None and fast_res["status"] != pending_st)
                    )
                    not_vlm_anchored_yet = not str(sp.get("last_engine") or "").startswith("Azure/VLM")
                    is_unanchored_non_free = (
                        fast_res["status"] != "FREE"
                        and not_vlm_anchored_yet
                        and (pending_st is not None or force_reanalyze)
                    )
                    model_tag = str(vlm_cfg.get("model", "model")).strip()
                    vlm_cache_key = f"{camera_id}::{sp['spot_id']}::{model_tag}::dual5"
                    vlm_ttl = float(cfg.get("vlm_cache_ttl_seconds", 900.0))
                    patch_for_cache = veh_patch if veh_patch is not None else patch
                    patch_bgr_32: Optional[np.ndarray] = None
                    cached_vlm: Optional[Dict[str, Any]] = None

                    if is_low_light_frame:
                        fast_res["confidence"] = min(float(fast_res.get("confidence", 0.78)), 0.78)
                        fast_res["needs_vlm"] = True
                        if isinstance(fast_res.get("metrics"), dict):
                            fast_res["metrics"]["low_light_scene"] = True

                    if has_vlm_ready and vlm_mode in ("always_vlm", "hybrid") and vlm_ttl > 0:
                        patch_bgr_32 = cv2.resize(patch_for_cache, (32, 32), interpolation=cv2.INTER_AREA)
                        cached_vlm = self._lookup_vlm_cache(vlm_cache_key, patch_bgr_32, vlm_ttl, wall_now)

                    if vlm_mode == "always_vlm" and has_vlm_ready:
                        need_vlm = True
                    elif vlm_mode == "hybrid" and has_vlm_ready:
                        if cached_vlm is not None:
                            need_vlm = True
                        elif first_frame_vlm_anchor and (is_first_state_change or is_unanchored_non_free):
                            need_vlm = True
                            first_frame_triggered = True
                        elif (
                            fast_res["confidence"] < hybrid_conf_thr
                            or fast_res["status"] == "BLOCKED"
                            or fast_res.get("obstacle_type") in ("movable", "pedestrian", "immovable")
                            or bool(fast_res.get("needs_vlm"))
                            or (
                                bool((fast_res.get("metrics") or {}).get("chromatic_anomaly"))
                                and fast_res["confidence"] < hybrid_conf_thr
                            )
                            or sp["spot_type"] == "elastic_strip"
                        ):
                            need_vlm = True

                    slot_idx = len(spot_reports)
                    spot_reports.append(None)
                    slot_info: Dict[str, Any] = {
                        "slot_idx": slot_idx,
                        "sp": sp,
                        "eff_cal_poly": eff_cal_poly,
                        "eff_cal_vpoly": eff_cal_vpoly,
                        "diff_score": diff_score,
                        "small_gray": small_gray,
                        "fast_res": fast_res,
                        "final_obs": final_obs,
                        "stage_used": stage_used,
                        "first_frame_triggered": first_frame_triggered,
                    }
                    pending_slots.append(slot_info)

                    if need_vlm:
                        if patch_bgr_32 is None:
                            patch_bgr_32 = cv2.resize(patch_for_cache, (32, 32), interpolation=cv2.INTER_AREA)
                        if cached_vlm is not None:
                            slot_info["final_obs"] = cached_vlm
                            slot_info["stage_used"] = f"Azure/VLM ({model_tag}) [快取命中]"
                        else:
                            if first_frame_triggered:
                                with self._lock:
                                    self.privacy_stats["first_frame_vlm_anchors"] += 1
                            vlm_input_img = ctx_crop if ctx_crop is not None else patch.copy()
                            keep_vlm_input = ctx_crop is not None
                            vlm_jobs.append(
                                {
                                    "slot_info": slot_info,
                                    "vlm_input_img": vlm_input_img,
                                    "has_context_outline": (ctx_crop is not None),
                                    "has_dual_layer_outline": (ctx_crop is not None and veh_patch is not None),
                                    "model_tag": model_tag,
                                    "vlm_cache_key": vlm_cache_key,
                                    "vlm_ttl": vlm_ttl,
                                    "patch_bgr_32": patch_bgr_32,
                                }
                            )
                finally:
                    del patch
                    del mask
                    if veh_patch is not None:
                        del veh_patch
                    if veh_mask is not None:
                        del veh_mask
                    if ctx_crop is not None and not keep_vlm_input:
                        del ctx_crop

            if vlm_jobs:
                vlm_attempted_spots = len(vlm_jobs)

                def _exec_vlm_job(job: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
                    try:
                        raw_ret = self._call_slow_lane_vlm(
                            job["vlm_input_img"],
                            job["slot_info"]["sp"],
                            vlm_cfg,
                            car_len_m,
                            has_context_outline=job["has_context_outline"],
                            has_dual_layer_outline=job["has_dual_layer_outline"],
                        )
                        if isinstance(raw_ret, tuple) and len(raw_ret) == 2:
                            return raw_ret
                        if isinstance(raw_ret, dict) and raw_ret:
                            return raw_ret, None
                        return None, None
                    finally:
                        job["vlm_input_img"] = None

                if len(vlm_jobs) == 1:
                    vlm_outcomes = [_exec_vlm_job(vlm_jobs[0])]
                else:
                    # Adaptive RPM/TPM-safe parallelism: 4 workers up to 6 stalls, 3 up to 12, 2 above 12
                    n_jobs = len(vlm_jobs)
                    max_vlm_workers = 4 if n_jobs <= 6 else (3 if n_jobs <= 12 else 2)
                    with ThreadPoolExecutor(max_workers=min(max_vlm_workers, n_jobs)) as pool:
                        vlm_outcomes = list(pool.map(_exec_vlm_job, vlm_jobs))

                for job, (vlm_res, vlm_err) in zip(vlm_jobs, vlm_outcomes):
                    s_info = job["slot_info"]
                    if vlm_res is not None:
                        vlm_succeeded_spots += 1
                        vlm_res = self._reconcile_vlm_massive_immovable_guard(
                            vlm_res,
                            s_info.get("fast_res"),
                            spot_type=str(s_info["sp"].get("spot_type") or "standard"),
                        )
                        s_info["final_obs"] = vlm_res
                        anchor_tag = " · 首帧语义锚定" if s_info.get("first_frame_triggered") else ""
                        s_info["stage_used"] = f"Azure/VLM ({job['model_tag']}{anchor_tag})"
                        if job["vlm_ttl"] > 0:
                            self._store_vlm_cache(
                                job["vlm_cache_key"], job["patch_bgr_32"], vlm_res, wall_now
                            )
                    else:
                        if vlm_err and not vlm_fallback_error:
                            vlm_fallback_error = str(vlm_err)
                        vlm_error_note = f" [VLM回退: {vlm_err}]" if vlm_err else " [VLM回退]"
                        s_info["stage_used"] = "DualLayer_OpenCV" + vlm_error_note

            anchor_localized = bool(
                not sandbox_mode
                and cam.get("has_template")
                and (
                    crop_info.get("rectified_by_homography")
                    or crop_info.get("anchor_matches")
                    or tpl_align_info.get("aligned")
                    or tpl_align_info.get("active")
                )
            )
            cur_rot_deg = float(crop_info.get("rotation_deg") or tpl_align_info.get("rotation_deg") or 0.0)
            cur_zoom = float(crop_info.get("zoom_ratio") or tpl_align_info.get("zoom_scale") or 1.0)
            simultaneous_flips = sum(
                1
                for s_it in pending_slots
                if str(s_it["final_obs"].get("status")) != str(s_it["sp"].get("status") or "FREE")
            )
            with self._lock:
                prev_cam_pose = self._camera_pose_tracks.get(camera_id)
                if not sandbox_mode:
                    self._camera_pose_tracks[camera_id] = {
                        "ts": now,
                        "rot_deg": cur_rot_deg,
                        "zoom": cur_zoom,
                    }
                    self._camera_pose_tracks.move_to_end(camera_id)
                    while len(self._camera_pose_tracks) > 64:
                        self._camera_pose_tracks.popitem(last=False)
            cam_dt = (now - float(prev_cam_pose["ts"])) if prev_cam_pose is not None else 999.0
            scene_discontinuity = bool(
                prev_cam_pose is None
                or cam_dt <= 0.0
                or cam_dt >= 15.0
                or abs(cur_rot_deg - float(prev_cam_pose.get("rot_deg", 0.0))) >= 1.2
                or abs(cur_zoom - float(prev_cam_pose.get("zoom", 1.0))) >= 0.04
                or simultaneous_flips >= 2
            )

            for s_info in pending_slots:
                sp = s_info["sp"]
                final_obs = s_info["final_obs"]
                stage_used = s_info["stage_used"]
                fast_res = s_info["fast_res"]
                small_gray = s_info["small_gray"]

                motion_info = self._evaluate_spot_motion_and_gate(
                    spot_key=f"{camera_id}::{sp['spot_id']}",
                    prev_committed_status=str(sp.get("status") or "FREE"),
                    pending_status=sp.get("pending_status"),
                    observed_status=str(final_obs["status"]),
                    obstacle_type=str(final_obs.get("obstacle_type") or "none"),
                    metrics=fast_res.get("metrics") or {},
                    gate_ctx=gate_ctx,
                    now_ts=now,
                    anchor_localized=anchor_localized,
                    scene_discontinuity=scene_discontinuity,
                    confidence=float(final_obs.get("confidence") or 0.0),
                    vlm_verified=str(stage_used).startswith("Azure/VLM"),
                    vlm_parking_pose=str(final_obs.get("parking_pose") or ""),
                )

                fsm_res = self.fsm.apply_observation(
                    spot=sp,
                    observed_status=final_obs["status"],
                    observed_free_units=final_obs["free_units"],
                    observed_occupied_units=final_obs["occupied_units"],
                    confidence=final_obs["confidence"],
                    reason=final_obs["reason"],
                    engine=stage_used,
                    debounce_seconds=debounce_sec,
                    instant_confirm=instant_mode,
                    now=now,
                    obstacle_type=str(final_obs.get("obstacle_type") or "none"),
                    action_hint=str(final_obs.get("action_hint") or ""),
                    persist_db=not sandbox_mode,
                    motion_info=motion_info,
                )

                # Self-learn empty reference baseline ONLY when spot is cleanly empty (no movable item / pedestrian)
                if (
                    sp["spot_type"] != "elastic_strip"
                    and not sp.get("ref_free_sig_b64")
                    and fsm_res["committed_status"] == "FREE"
                    and final_obs.get("obstacle_type", "none") == "none"
                    and final_obs["confidence"] >= 0.88
                ):
                    ref_sig_b64 = base64.b64encode(small_gray.tobytes()).decode("ascii")
                    if sandbox_mode:
                        sp["ref_free_sig_b64"] = ref_sig_b64
                    else:
                        self.db.update_spot_state(
                            sp["spot_id"],
                            {"ref_free_sig_b64": ref_sig_b64},
                        )

                spot_reports[s_info["slot_idx"]] = {
                    "spot_id": sp["spot_id"],
                    "label": sp["label"],
                    "spot_type": sp["spot_type"],
                    "effective_polygon": s_info["eff_cal_poly"],
                    "effective_vehicle_polygon": s_info["eff_cal_vpoly"],
                    "diff_score": round(s_info["diff_score"], 4),
                    "stage_used": stage_used,
                    "observed_status": final_obs["status"],
                    "obstacle_type": final_obs.get("obstacle_type", "none"),
                    "action_hint": final_obs.get("action_hint", ""),
                    "confidence": round(final_obs["confidence"], 3),
                    "reason": final_obs["reason"],
                    "metrics": fast_res.get("metrics", {}),
                    "motion": motion_info,
                    "fsm": fsm_res,
                }
        finally:
            extracted_patches.clear()
            vlm_jobs.clear()

        final_spot_reports = [r for r in spot_reports if r is not None]

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "camera_id": camera_id,
            "zone_id": cam["zone_id"],
            "processed_spots": len(final_spot_reports),
            "sandbox_preview": sandbox_mode,
            "elapsed_ms": elapsed_ms,
            "raw_image_retained_on_disk": False,
            "instant_confirm_mode": instant_mode,
            "debounce_seconds": debounce_sec,
            "hybrid_vlm_confidence_threshold": hybrid_conf_thr,
            "low_light_scene": is_low_light_frame,
            "vlm_attempted_spots": vlm_attempted_spots,
            "vlm_succeeded_spots": vlm_succeeded_spots,
            "vlm_fallback_error": vlm_fallback_error,
            "effective_crop_roi": eff_crop_roi,
            "gate_context": gate_ctx,
            "template_alignment": tpl_align_info,
            "reports": final_spot_reports,
        }

    def _analyze_patch_fast_opencv(
        self,
        patch_bgr: np.ndarray,
        valid_mask: np.ndarray,
        spot_type: str = "standard",
        total_meters: float = 5.2,
        car_len_m: float = 5.2,
        ref_free_sig: Optional[np.ndarray] = None,
        small_gray: Optional[np.ndarray] = None,
        veh_patch_bgr: Optional[np.ndarray] = None,
        veh_valid_mask: Optional[np.ndarray] = None,
        enable_dual_layer: bool = True,
    ) -> Dict[str, Any]:
        if spot_type == "elastic_strip":
            return self._analyze_elastic_strip_opencv(patch_bgr, total_meters, car_len_m)

        h, w = patch_bgr.shape[:2]
        smoothed_full = cv2.GaussianBlur(patch_bgr, (5, 5), 0)
        gray_full = cv2.cvtColor(smoothed_full, cv2.COLOR_BGR2GRAY)
        hsv_full = cv2.cvtColor(smoothed_full, cv2.COLOR_BGR2HSV)
        gx_full = np.abs(cv2.Sobel(gray_full, cv2.CV_32F, 1, 0, ksize=3))
        gy_full = np.abs(cv2.Sobel(gray_full, cv2.CV_32F, 0, 1, ksize=3))
        grad_full = np.sqrt(gx_full * gx_full + gy_full * gy_full)

        # 1. Inner Center Core [x: 28%..72%, y: 32%..72%]:
        # Excludes rear wheel-stopper / curb (`防撞石/防撞条`, y<32%), front ground-painted number 01..07 (y>72%),
        # and adjacent 3D vehicle fender/headlight perspective bleed (x<28% or x>72%).
        cy0, cy1 = int(h * 0.32), int(h * 0.72)
        cx0, cx1 = int(w * 0.28), int(w * 0.72)
        center_mask = valid_mask[cy0:cy1, cx0:cx1] > 127
        if int(np.sum(center_mask)) < 80:
            cy0, cy1 = int(h * 0.24), int(h * 0.82)
            cx0, cx1 = int(w * 0.16), int(w * 0.84)
            center_mask = np.ones((cy1 - cy0, cx1 - cx0), dtype=bool)

        # Detect colored / terracotta permeable brick pavement (`透水红砖/彩砖地面`) up front so brick seams
        # and brick-vs-asphalt color delta are never misclassified as vehicles or stacked brick piles.
        lab_full = cv2.cvtColor(smoothed_full, cv2.COLOR_BGR2LAB).astype(np.float32)
        pave_ring = np.zeros((h, w), dtype=bool)
        pave_ring[int(h * 0.90) :, :] = True
        pave_ring[:, : int(w * 0.11)] = True
        pave_ring[:, int(w * 0.89) :] = True
        pave_valid = (
            pave_ring
            & (valid_mask > 127)
            & ~((hsv_full[:, :, 1] < 35) & (hsv_full[:, :, 2] > 185))
            & (gray_full > 42)
        )
        if int(np.sum(pave_valid)) < 24:
            py0, py1 = int(h * 0.78), int(h * 0.98)
            px0, px1 = int(w * 0.16), int(w * 0.84)
            pave_valid = np.zeros((h, w), dtype=bool)
            pave_valid[py0:py1, px0:px1] = (valid_mask[py0:py1, px0:px1] > 127) & (
                gray_full[py0:py1, px0:px1] > 42
            )
        if int(np.sum(pave_valid)) >= 24:
            pave_l = float(np.median(lab_full[:, :, 0][pave_valid]))
            pave_a = float(np.median(lab_full[:, :, 1][pave_valid]))
            pave_b = float(np.median(lab_full[:, :, 2][pave_valid]))
        else:
            pave_l, pave_a, pave_b = 128.0, 128.0, 128.0

        # Check horizontal underbody clearance shadow bar across lower half [y: 0.56..0.90, x: 0.18..0.82]
        shy0, shy1 = int(h * 0.56), int(h * 0.90)
        shx0, shx1 = int(w * 0.18), int(w * 0.82)
        sh_dark_thresh = max(38.0, pave_l * 0.62)
        sh_dark_strip = (lab_full[shy0:shy1, shx0:shx1, 0] < sh_dark_thresh) & (
            valid_mask[shy0:shy1, shx0:shx1] > 127
        )
        sh_col_w = max(1, sh_dark_strip.shape[1] // 8)
        underbody_shadow_cols = 0
        for c_idx in range(8):
            sc = sh_dark_strip[:, c_idx * sh_col_w : (c_idx + 1) * sh_col_w]
            if sc.size > 0 and float(np.mean(sc)) >= 0.10:
                underbody_shadow_cols += 1
        has_chassis_shadow_bar = underbody_shadow_cols >= 5

        c_grad = grad_full[cy0:cy1, cx0:cx1][center_mask]
        c_gx = gx_full[cy0:cy1, cx0:cx1][center_mask]
        c_gy = gy_full[cy0:cy1, cx0:cx1][center_mask]
        c_gray = gray_full[cy0:cy1, cx0:cx1][center_mask]
        c_sat = hsv_full[cy0:cy1, cx0:cx1, 1][center_mask]
        c_val = hsv_full[cy0:cy1, cx0:cx1, 2][center_mask]
        cp92, cp08 = np.percentile(c_gray, [92, 8])
        center_spread = float((cp92 - cp08) / 255.0)

        warm_paver_full = (
            ((hsv_full[:, :, 0] <= 24) | (hsv_full[:, :, 0] >= 164))
            & (hsv_full[:, :, 1] >= 28)
            & (hsv_full[:, :, 1] <= 178)
            & (hsv_full[:, :, 2] >= 45)
            & (hsv_full[:, :, 2] <= 248)
            & (valid_mask > 127)
        )
        apy0, apy1 = int(h * 0.74), int(h * 0.92)
        apx0, apx1 = int(w * 0.16), int(w * 0.84)
        ap_valid = (valid_mask[apy0:apy1, apx0:apx1] > 127) & ~(
            (hsv_full[apy0:apy1, apx0:apx1, 1] < 35) & (hsv_full[apy0:apy1, apx0:apx1, 2] > 185)
        )
        apron_warm_ratio = (
            float(np.mean(warm_paver_full[apy0:apy1, apx0:apx1][ap_valid]))
            if int(np.sum(ap_valid)) >= 24
            else 0.0
        )
        lat_strip = np.zeros((h, w), dtype=bool)
        lat_strip[int(h * 0.34) : int(h * 0.88), int(w * 0.08) : int(w * 0.24)] = True
        lat_strip[int(h * 0.34) : int(h * 0.88), int(w * 0.76) : int(w * 0.92)] = True
        lat_valid = (
            lat_strip
            & (valid_mask > 127)
            & ~((hsv_full[:, :, 1] < 35) & (hsv_full[:, :, 2] > 185))
        )
        lateral_warm_ratio = (
            float(np.mean(warm_paver_full[lat_valid]))
            if int(np.sum(lat_valid)) >= 24
            else 0.0
        )
        core_warm_ratio = float(np.mean(warm_paver_full[cy0:cy1, cx0:cx1][center_mask]))
        ev_green_paver_full = (
            (hsv_full[:, :, 0] >= 75)
            & (hsv_full[:, :, 0] <= 99)
            & (hsv_full[:, :, 1] >= 80)
            & (hsv_full[:, :, 2] >= 85)
            & (valid_mask > 127)
        )
        apron_ev_green_ratio = (
            float(np.mean(ev_green_paver_full[apy0:apy1, apx0:apx1][ap_valid]))
            if int(np.sum(ap_valid)) >= 24
            else 0.0
        )
        core_ev_green_ratio = float(np.mean(ev_green_paver_full[cy0:cy1, cx0:cx1][center_mask]))
        ev_green_paving_filtered = (
            apron_ev_green_ratio >= 0.25
            and core_ev_green_ratio >= 0.35
            and not has_chassis_shadow_bar
            and center_spread < 0.32
        )
        colored_paving_filtered = (
            (
                apron_warm_ratio >= 0.14
                and lateral_warm_ratio >= 0.12
                and core_warm_ratio >= 0.18
                and not has_chassis_shadow_bar
                and center_spread < 0.38
            )
            or ev_green_paving_filtered
        )
        if colored_paving_filtered:
            paver_src_full = ev_green_paver_full if ev_green_paving_filtered else warm_paver_full
            floor_brick_mask = paver_src_full[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80)]
            if int(np.sum(floor_brick_mask)) >= 24:
                sub_lab = lab_full[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80)]
                pave_l = float(np.median(sub_lab[:, :, 0][floor_brick_mask]))
                pave_a = float(np.median(sub_lab[:, :, 1][floor_brick_mask]))
                pave_b = float(np.median(sub_lab[:, :, 2][floor_brick_mask]))
            else:
                floor_core = (valid_mask[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80)] > 127) & ~(
                    (hsv_full[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80), 1] < 35)
                    & (hsv_full[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80), 2] > 185)
                )
                if int(np.sum(floor_core)) >= 32:
                    sub_lab = lab_full[int(h * 0.34) : int(h * 0.86), int(w * 0.20) : int(w * 0.80)]
                    pave_l = float(np.median(sub_lab[:, :, 0][floor_core]))
                    pave_a = float(np.median(sub_lab[:, :, 1][floor_core]))
                    pave_b = float(np.median(sub_lab[:, :, 2][floor_core]))

        chroma_dist_full = np.sqrt(
            (lab_full[:, :, 1] - pave_a) ** 2 + (lab_full[:, :, 2] - pave_b) ** 2
        )
        if colored_paving_filtered:
            # Zero out colored floor paint/brick AND low-saturation gray shadows / white lines (`S < 45`)
            # so empty colored stalls never trigger chromatic anomalies, while vehicle bodies stand out!
            brick_or_neutral_mask = (
                warm_paver_full
                | (ev_green_paver_full if ev_green_paving_filtered else False)
                | (hsv_full[:, :, 1] < 45)
                | (hsv_full[:, :, 2] < 34)
            )
            chroma_dist_full = np.where(brick_or_neutral_mask, 0.0, chroma_dist_full)

        valid_vals_all = hsv_full[:, :, 2][valid_mask > 127]
        if valid_vals_all.size >= 32:
            patch_p50_v = float(np.percentile(valid_vals_all, 50))
            patch_p92_v = float(np.percentile(valid_vals_all, 92))
        else:
            patch_p50_v, patch_p92_v = 128.0, 180.0
        low_light_patch = bool(patch_p50_v < 46.0 and patch_p92_v < 86.0)

        edge_grad_thr = 48.0 if colored_paving_filtered else 38.0
        center_edge_ratio = float(np.mean(c_grad > edge_grad_thr))
        center_iso = float(np.sqrt(float(np.mean(c_gx)) * float(np.mean(c_gy))) / (48.0 if colored_paving_filtered else 42.0))
        sat_thr = 155 if colored_paving_filtered else 85
        center_high_sat = float(np.mean((c_sat > sat_thr) & (c_val > 60)))

        # Rear-half Ground Contact Core [x: 24%..76%, y: 31%..62%]:
        # Starts in front of rear wheel-stops (`防撞石/防撞条`, y<31%) while still catching
        # deeper-parked car tires/bumpers under oblique perspective.
        ry0, ry1 = int(h * 0.31), int(h * 0.62)
        rx0, rx1 = int(w * 0.24), int(w * 0.76)
        rear_mask = valid_mask[ry0:ry1, rx0:rx1] > 127
        if int(np.sum(rear_mask)) >= 60:
            r_grad = grad_full[ry0:ry1, rx0:rx1][rear_mask]
            r_gray = gray_full[ry0:ry1, rx0:rx1][rear_mask]
            r_sat = hsv_full[ry0:ry1, rx0:rx1, 1][rear_mask]
            r_val = hsv_full[ry0:ry1, rx0:rx1, 2][rear_mask]
            rear_edge_r = float(np.mean(r_grad > edge_grad_thr))
            rp92, rp08 = np.percentile(r_gray, [92, 8])
            rear_spread = float((rp92 - rp08) / 255.0)
            rear_sat = float(np.mean((r_sat > sat_thr) & (r_val > 60)))
            ground_rear_score = (
                0.46 * min(1.25, rear_edge_r / 0.20)
                + 0.34 * min(1.25, rear_spread / 0.34)
                + 0.20 * min(1.25, rear_sat / 0.18)
            )
        else:
            rear_edge_r = center_edge_ratio
            ground_rear_score = 0.0

        # Side border strips to detect one-sided 3D bumper bleed from a neighboring stall
        left_grad = grad_full[cy0:cy1, int(w * 0.08) : int(w * 0.28)]
        right_grad = grad_full[cy0:cy1, int(w * 0.72) : int(w * 0.92)]
        left_edge_r = float(np.mean(left_grad > edge_grad_thr)) if left_grad.size else 0.0
        right_edge_r = float(np.mean(right_grad > edge_grad_thr)) if right_grad.size else 0.0
        max_side_edge = max(left_edge_r, right_edge_r)

        # Horizontal 8-column span check across [x: 20%..80%, y: 33%..70%] (in front of rear wheel-stop):
        span_g = gray_full[int(h * 0.33) : int(h * 0.70), int(w * 0.20) : int(w * 0.80)]
        span_gr = grad_full[int(h * 0.33) : int(h * 0.70), int(w * 0.20) : int(w * 0.80)]
        col_w = max(1, span_g.shape[1] // 8)
        active_cols = 0
        for c_idx in range(8):
            cg_col = span_g[:, c_idx * col_w : (c_idx + 1) * col_w]
            cgr_col = span_gr[:, c_idx * col_w : (c_idx + 1) * col_w]
            if cg_col.size == 0:
                continue
            col_spread = float((np.percentile(cg_col, 90) - np.percentile(cg_col, 10)) / 255.0)
            col_edge = float(np.mean(cgr_col > edge_grad_thr))
            if col_edge > 0.14 or col_spread > (0.27 if colored_paving_filtered else 0.24):
                active_cols += 1

        # 2. Mid-stall Anomaly Zone [x: 18%..84%, y: 33%..75%] for localized items (basketball, bicycle, cone, pedestrian)
        # Excludes rear wheel-stop zone (`y < 33%`) and adds explicit horizontal wheel-stop bar filter (`rear_wheelstop_filtered`).
        my0, my1 = int(h * 0.33), int(h * 0.75)
        mx0, mx1 = int(w * 0.18), int(w * 0.84)
        m_mask = cv2.erode((valid_mask[my0:my1, mx0:mx1] > 127).astype(np.uint8) * 255, np.ones((5, 5), np.uint8)) > 127
        if int(np.sum(m_mask)) < 80:
            m_mask = np.ones((my1 - my0, mx1 - mx0), dtype=bool)

        m_hue = hsv_full[my0:my1, mx0:mx1, 0]
        m_sat = hsv_full[my0:my1, mx0:mx1, 1]
        m_val = hsv_full[my0:my1, mx0:mx1, 2]
        m_grad = grad_full[my0:my1, mx0:mx1]
        mh_rows = m_hue.shape[0]
        rear_wheelstop_filtered = False

        # Detect horizontal rear wheel-stop curb / reflective bar (`车辆后段防撞石/防撞条/止退器`) in rear 6%..35% zone
        wsy0, wsy1 = int(h * 0.06), int(h * 0.35)
        wsx0, wsx1 = int(w * 0.16), int(w * 0.84)
        ws_valid = (valid_mask[wsy0:wsy1, wsx0:wsx1] > 127) & ~(
            (hsv_full[wsy0:wsy1, wsx0:wsx1, 1] < 35) & (hsv_full[wsy0:wsy1, wsx0:wsx1, 2] > 195)
        )
        if int(np.sum(ws_valid)) >= 40 and (center_edge_ratio < 0.26 or colored_paving_filtered):
            ws_sat = hsv_full[wsy0:wsy1, wsx0:wsx1, 1]
            ws_val = hsv_full[wsy0:wsy1, wsx0:wsx1, 2]
            ws_sat_thr = 192 if colored_paving_filtered else 120
            ws_act = (
                ((ws_sat > ws_sat_thr) & (ws_val > 190))
                | (ws_val < 52)
                | (
                    not colored_paving_filtered
                    and (grad_full[wsy0:wsy1, wsx0:wsx1] > 52.0)
                )
            ) & ws_valid
            ys_wsz, xs_wsz = np.where(ws_act)
            if ys_wsz.size >= 14:
                wsz_w = float(int(np.max(xs_wsz)) - int(np.min(xs_wsz)) + 1)
                wsz_h = float(max(1, int(np.max(ys_wsz)) - int(np.min(ys_wsz)) + 1))
                if (wsz_w / wsz_h) >= 2.2 and wsz_h <= (wsy1 - wsy0) * 0.72:
                    rear_wheelstop_filtered = True

        m_chroma_dist = chroma_dist_full[my0:my1, mx0:mx1]
        if colored_paving_filtered:
            # On terracotta/orange brick paving, sunlit floor bricks naturally have H<=24 & V>190;
            # require fluorescent cone/lock saturation (>192) or distinct CIE-Lab chroma shift (>22) from floor.
            vivid_orange_map = (
                (((m_hue <= 32) | (m_hue >= 168)) & (m_val > 190) & ((m_sat > 192) | (m_chroma_dist > 22.0)))
                & m_mask
            )
        else:
            vivid_orange_map = (
                (((m_hue <= 32) | (m_hue >= 168)) & (m_sat > 118) & (m_val > 190))
                & m_mask
            )
        ys_vo, xs_vo = np.where(vivid_orange_map)
        if ys_vo.size >= 8:
            vo_y85 = float(np.percentile(ys_vo, 85))
            vo_w = float(int(np.max(xs_vo)) - int(np.min(xs_vo)) + 1)
            vo_h = float(max(1, int(np.max(ys_vo)) - int(np.min(ys_vo)) + 1))
            if vo_y85 <= mh_rows * 0.32 and (vo_w / vo_h) >= 2.2:
                # Yellow-black / reflective rear rubber wheel-stop bar (`防撞条/止退器`) at rear of empty stall
                vivid_orange_map = np.zeros_like(vivid_orange_map, dtype=bool)
                rear_wheelstop_filtered = True
        cone_ratio = float(np.mean(vivid_orange_map[m_mask]))

        # Natural green foliage / hedge mask (OpenCV HSV H in [34..88]):
        foliage_mid_map = (m_hue >= 34) & (m_hue <= 88) & (m_sat >= 48) & (m_val >= 28) & m_mask
        upper_band = np.zeros_like(m_mask, dtype=bool)
        upper_band[: max(1, int(mh_rows * 0.42)), :] = True
        upper_foliage_map = foliage_mid_map & upper_band
        lower_foliage_ratio = (
            float(np.mean(foliage_mid_map[~upper_band & m_mask]))
            if int(np.sum(~upper_band & m_mask)) > 20
            else 0.0
        )
        ground_foliage_ratio = float(np.mean(foliage_mid_map[m_mask]))
        foliage_px_count = int(np.sum(foliage_mid_map))
        foliage_smooth_ratio = (
            float(np.mean(m_grad[foliage_mid_map] < 44.0))
            if foliage_px_count >= 24
            else 0.0
        )
        # Smooth painted green vehicle body (e.g. dark forest-green sedan/SUV on brick or asphalt)
        # vs jagged overhanging tree leaves (which either stay in upper rear band or have dense leaf micro-edges):
        painted_green_vehicle_body = (
            not ev_green_paving_filtered
            and ground_foliage_ratio >= 0.18
            and lower_foliage_ratio >= 0.15
            and foliage_smooth_ratio >= 0.55
            and min(left_edge_r, right_edge_r) >= 0.032
        )
        mask_out_rear_foliage = upper_foliage_map if lower_foliage_ratio < 0.12 else np.zeros_like(m_mask, dtype=bool)

        # Exclude flat white ground paint, colored/EV floor paint (when `colored_paving_filtered`), and rear hedge leaves
        obj_grad_thr = 56.0 if colored_paving_filtered else 46.0
        obj_sat_thr = 115 if colored_paving_filtered else 95
        warm_floor_mid = (
            (ev_green_paver_full[my0:my1, mx0:mx1] if ev_green_paving_filtered else warm_paver_full[my0:my1, mx0:mx1])
            if colored_paving_filtered
            else np.zeros_like(m_mask, dtype=bool)
        )
        non_paint_obj_map = (
            ((m_grad > obj_grad_thr) | (((m_sat > obj_sat_thr) & (m_val > 60)) & ~warm_floor_mid))
            & ~((m_sat < 35) & (m_val > 185))
            & ~mask_out_rear_foliage
            & m_mask
        )
        # Filter horizontal concrete/rubber rear wheel-stop bar sitting at the very top of `m_mask` when rest of stall is empty
        rear_ws_band = np.zeros_like(m_mask, dtype=bool)
        rear_ws_band[: max(1, int(mh_rows * 0.28)), :] = True
        front_72_mask = (~rear_ws_band) & m_mask
        if int(np.sum(front_72_mask)) > 30 and float(np.mean(non_paint_obj_map[front_72_mask])) < 0.042:
            ys_rw, xs_rw = np.where(non_paint_obj_map & rear_ws_band)
            if ys_rw.size >= 8:
                rw_w = float(int(np.max(xs_rw)) - int(np.min(xs_rw)) + 1)
                rw_h = float(max(1, int(np.max(ys_rw)) - int(np.min(ys_rw)) + 1))
                if (rw_w / rw_h) >= 2.2:
                    non_paint_obj_map = non_paint_obj_map & (~rear_ws_band)
                    rear_wheelstop_filtered = True
        obj_blob_ratio = float(np.mean(non_paint_obj_map[m_mask]))

        # Ground-layer vehicle score driven by INNER CENTER CORE so neighbor bumper bleed cannot trigger false OCCUPIED
        ground_car_score = (
            0.44 * min(1.25, center_edge_ratio / 0.22)
            + 0.24 * min(1.25, center_iso)
            + 0.22 * min(1.25, center_spread / 0.36)
            + 0.10 * min(1.25, center_high_sat / 0.20)
        )

        # CIE-Lab Underbody / Chassis vs. Pavement Chromatic & Shadow Contrast (`chroma_dist_full` computed above):
        foliage_full = (
            (hsv_full[:, :, 0] >= 34)
            & (hsv_full[:, :, 0] <= 88)
            & (hsv_full[:, :, 1] >= 48)
            & (hsv_full[:, :, 2] >= 28)
        )

        suppress_foliage_chroma = (not painted_green_vehicle_body) and (
            (lower_foliage_ratio < 0.14)
            or (ground_foliage_ratio >= 0.35 and not has_chassis_shadow_bar)
        )
        uby0, uby1 = int(h * 0.34), int(h * 0.88)
        ubx0, ubx1 = int(w * 0.20), int(w * 0.80)
        ub_mask = valid_mask[uby0:uby1, ubx0:ubx1] > 127
        if suppress_foliage_chroma:
            ub_mask = ub_mask & (~foliage_full[uby0:uby1, ubx0:ubx1])
        if int(np.sum(ub_mask)) >= 40:
            ub_chroma_vals = chroma_dist_full[uby0:uby1, ubx0:ubx1][ub_mask]
            ub_l_vals = lab_full[uby0:uby1, ubx0:ubx1, 0][ub_mask]
            underbody_chroma_delta = float(np.percentile(ub_chroma_vals, 78))
            underbody_shadow_ratio = float(np.mean(ub_l_vals < sh_dark_thresh))
        else:
            underbody_chroma_delta = 0.0
            underbody_shadow_ratio = 0.0

        cspan_ch = chroma_dist_full[uby0:uby1, int(w * 0.20) : int(w * 0.80)]
        cspan_fol = foliage_full[uby0:uby1, int(w * 0.20) : int(w * 0.80)]
        ccol_w = max(1, cspan_ch.shape[1] // 8)
        chroma_active_cols = 0
        for c_idx in range(8):
            c_ch = cspan_ch[:, c_idx * ccol_w : (c_idx + 1) * ccol_w]
            c_fl = cspan_fol[:, c_idx * ccol_w : (c_idx + 1) * ccol_w]
            if c_ch.size == 0:
                continue
            if suppress_foliage_chroma:
                non_fol_ch = c_ch[~c_fl] if int(np.sum(~c_fl)) >= 8 else np.zeros((1,), dtype=np.float32)
            else:
                non_fol_ch = c_ch
            if float(np.mean(non_fol_ch > 13.5)) >= 0.30:
                chroma_active_cols += 1

        allow_foliage_chroma = (
            ground_foliage_ratio < 0.35
            or has_chassis_shadow_bar
            or painted_green_vehicle_body
        )
        chromatic_car_detected = (
            underbody_chroma_delta >= 15.0
            and chroma_active_cols >= (5 if painted_green_vehicle_body else 6)
            and allow_foliage_chroma
        )
        chromatic_anomaly = (
            not chromatic_car_detected
            and underbody_chroma_delta >= (16.0 if colored_paving_filtered else 12.5)
            and (2 if colored_paving_filtered else 1) <= chroma_active_cols <= 5
            and allow_foliage_chroma
        )
        if chromatic_car_detected:
            chroma_boost = 0.58 + min(0.30, (underbody_chroma_delta - 15.0) * 0.015) + min(0.08, underbody_shadow_ratio * 0.20)
            ground_car_score = max(ground_car_score, chroma_boost)
            active_cols = max(active_cols, chroma_active_cols)

        # Terracotta / clay brick & masonry pile detector (distinguish 1~4 loose bricks vs >=5 stacked pile)
        # Guard: if the stall floor itself is terracotta/colored permeable brick paving (`colored_paving_filtered`),
        # flat floor bricks are NOT stacked masonry debris (`brick_ratio = 0.0`).
        if colored_paving_filtered:
            brick_ratio = 0.0
        else:
            terracotta_map = (
                (((m_hue <= 17) | (m_hue >= 168)) & (m_sat >= 52) & (m_sat <= 185) & (m_val >= 42) & (m_val <= 205))
                & m_mask
            )
            brick_ratio = float(np.mean(terracotta_map[m_mask]))

        # Foreground longitudinal/lateral centroid (`occ_cy`: 0=rear curb, 1=front road mouth) for motion vectoring
        fgy0, fgy1 = int(h * 0.08), int(h * 0.92)
        fgx0, fgx1 = int(w * 0.14), int(w * 0.86)
        fg_valid = (valid_mask[fgy0:fgy1, fgx0:fgx1] > 127) & ~(
            foliage_full[fgy0:fgy1, fgx0:fgx1] if lower_foliage_ratio < 0.14 else False
        )
        fg_paint = (hsv_full[fgy0:fgy1, fgx0:fgx1, 1] < 35) & (hsv_full[fgy0:fgy1, fgx0:fgx1, 2] > 185)
        fg_paver_suppress = (
            (warm_paver_full[fgy0:fgy1, fgx0:fgx1] & (grad_full[fgy0:fgy1, fgx0:fgx1] < 58.0))
            if colored_paving_filtered
            else False
        )
        fg_act = (
            (
                (grad_full[fgy0:fgy1, fgx0:fgx1] > 38.0)
                | (chroma_dist_full[fgy0:fgy1, fgx0:fgx1] > 14.0)
                | ((hsv_full[fgy0:fgy1, fgx0:fgx1, 1] > sat_thr) & (hsv_full[fgy0:fgy1, fgx0:fgx1, 2] > 55))
            )
            & fg_valid
            & ~fg_paint
            & ~fg_paver_suppress
        )
        if rear_wheelstop_filtered:
            ws_cut = max(0, int(h * 0.22) - fgy0)
            if ws_cut > 0:
                fg_act[:ws_cut, :] = False
        fg_ys, fg_xs = np.where(fg_act)
        if fg_ys.size >= 20:
            occ_cy = float(np.clip((fgy0 + float(np.mean(fg_ys))) / float(max(1, h)), 0.0, 1.0))
            occ_cx = float(np.clip((fgx0 + float(np.mean(fg_xs))) / float(max(1, w)), 0.0, 1.0))
        else:
            occ_cy = 0.50
            occ_cx = 0.50

        # Longitudinal 3-zone vehicle presence density (`rear` / `mid` / `mouth`) for anchor-localized pose checks
        zx0, zx1 = int(w * 0.20), int(w * 0.80)

        def _zone_occ_density(zy0_f: float, zy1_f: float) -> float:
            zy0_i, zy1_i = int(h * zy0_f), int(h * zy1_f)
            if zy1_i <= zy0_i or zx1 <= zx0:
                return 0.0
            zv = (valid_mask[zy0_i:zy1_i, zx0:zx1] > 127) & ~(
                (hsv_full[zy0_i:zy1_i, zx0:zx1, 1] < 35) & (hsv_full[zy0_i:zy1_i, zx0:zx1, 2] > 185)
            )
            if int(np.sum(zv)) < 16:
                return 0.0
            z_paver = (
                (warm_paver_full[zy0_i:zy1_i, zx0:zx1] & (grad_full[zy0_i:zy1_i, zx0:zx1] < 58.0))
                if colored_paving_filtered
                else False
            )
            z_hit = (
                (
                    (grad_full[zy0_i:zy1_i, zx0:zx1] > 32.0)
                    | (chroma_dist_full[zy0_i:zy1_i, zx0:zx1] > 14.0)
                    | (hsv_full[zy0_i:zy1_i, zx0:zx1, 2] < 86)
                )
                & zv
                & ~z_paver
            )
            return float(np.mean(z_hit[zv]))

        rear_zone_occ = _zone_occ_density(0.22, 0.50)
        mid_zone_occ = _zone_occ_density(0.42, 0.72)
        mouth_zone_occ = _zone_occ_density(0.72, 0.92)

        # Reference-Template Baseline Delta on ground core (computed early for symmetric white/van check)
        ref_delta_val: Optional[float] = None
        ref_mean_shift: float = 0.0
        if ref_free_sig is not None:
            cur_32 = (
                small_gray
                if small_gray is not None
                else cv2.resize(cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY), (32, 32))
            )
            c_sub = cur_32[8:23, 9:23].astype(np.float32)
            r_sub = ref_free_sig[8:23, 9:23].astype(np.float32)
            ref_mean_shift = float(abs(float(np.mean(c_sub)) - float(np.mean(r_sub))) / 255.0)
            ref_delta_val = float(np.mean(np.abs((c_sub - np.mean(c_sub)) - (r_sub - np.mean(r_sub)))) / 255.0)

        # Smooth white cargo-box wall (`白色厢式货车` flat metal body filling interior + dark lower chassis/tire bar):
        # Painted stall lines occupy <15% of the interior core; when >52% of the core is bright achromatic white
        # (`S < 35, V > 205`) above a dark undercarriage shadow bar (`V < 58`), treat as a cargo vehicle body
        # rather than masking it out as road paint!
        white_cargo_sheet_ratio = float(np.mean(((m_sat < 35) & (m_val > 205))[m_mask]))
        my0_d, my1_d = int(h * 0.76), int(h * 0.96)
        mx0_d, mx1_d = int(w * 0.18), int(w * 0.82)
        m_valid_d = valid_mask[my0_d:my1_d, mx0_d:mx1_d] > 127
        mouth_dark_bar_ratio = (
            float(np.mean((hsv_full[my0_d:my1_d, mx0_d:mx1_d, 2] < 58)[m_valid_d]))
            if int(np.sum(m_valid_d)) >= 16
            else 0.0
        )
        smooth_white_cargo_body = (
            (not colored_paving_filtered)
            and white_cargo_sheet_ratio >= 0.52
            and mouth_dark_bar_ratio >= 0.20
            and (ref_free_sig is None or ref_mean_shift >= 0.20)
        )
        if smooth_white_cargo_body and ref_delta_val is not None:
            ref_delta_val = max(ref_delta_val, 0.65 * ref_mean_shift)

        symmetric_achromatic_car = (
            active_cols >= 7
            and min(left_edge_r, right_edge_r) >= 0.15
            and min(left_edge_r, right_edge_r) >= 0.55 * max(1e-6, max_side_edge)
            and 0.36 <= occ_cx <= 0.64
            and center_spread >= 0.52
            and mid_zone_occ >= 0.72
            and (ref_delta_val is None or ref_delta_val >= 0.14)
        )
        bilateral_smooth_car = (
            min(left_edge_r, right_edge_r) >= 0.11
            and max_side_edge >= 0.20
            and min(left_edge_r, right_edge_r) >= 0.42 * max(1e-6, max_side_edge)
            and 0.32 <= occ_cx <= 0.68
            and (
                (chromatic_car_detected and chroma_active_cols >= 5)
                or painted_green_vehicle_body
                or (mid_zone_occ >= 0.34 and active_cols >= 5)
            )
        )
        symmetric_full_car = (
            (
                chromatic_car_detected
                and has_chassis_shadow_bar
                and chroma_active_cols >= 7
                and min(left_edge_r, right_edge_r) >= 0.16
            )
            or (
                chromatic_car_detected
                and chroma_active_cols >= 6
                and 0.30 <= occ_cx <= 0.70
            )
            or symmetric_achromatic_car
            or bilateral_smooth_car
        )
        neighbor_bleed_suspected = (
            not symmetric_full_car
            and max_side_edge >= 0.22
            and center_edge_ratio < 0.14
            and rear_edge_r < 0.18
            and max_side_edge > max(0.05, center_edge_ratio * 2.0)
        )
        if neighbor_bleed_suspected:
            ground_car_score *= 0.55

        if ref_delta_val is not None:
            if ref_delta_val < 0.050 and cone_ratio < 0.012 and obj_blob_ratio < 0.06:
                ground_car_score *= 0.68
            elif ref_delta_val > 0.155 and (ground_car_score >= 0.44 or symmetric_achromatic_car) and not neighbor_bleed_suspected:
                ground_car_score = max(ground_car_score, 0.58 + min(0.28, (ref_delta_val - 0.155) * 1.3))

        # Flat 2D Ground Stencil / Painted EV or Accessible Icon Filter (`地面喷涂新能源小车/轮椅/数字标线`):
        # When >52% of the stall core is exposed bare pavement matching the empty front apron,
        # >94% of the core is explained solely by bare pavement + smooth green/cyan/blue/white
        # road paint (with 2px anti-aliasing), and there is virtually zero 3D dark chassis/tire
        # shadow or orange/terracotta/lock object, suppress stencil-induced ground edges so empty
        # EV bays (even with a rear charging pedestal in the upper 3D prism) stay cleanly FREE!
        ground_stencil_filtered = False
        if (
            not colored_paving_filtered
            and not has_chassis_shadow_bar
            and cone_ratio < 0.008
            and brick_ratio < 0.008
        ):
            aps_y0, aps_y1 = int(h * 0.72), int(h * 0.90)
            aps_x0, aps_x1 = int(w * 0.22), int(w * 0.78)
            aps_hsv = hsv_full[aps_y0:aps_y1, aps_x0:aps_x1]
            aps_lab = lab_full[aps_y0:aps_y1, aps_x0:aps_x1]
            aps_valid = (valid_mask[aps_y0:aps_y1, aps_x0:aps_x1] > 127) & ~(
                (aps_hsv[:, :, 1] < 42) & (aps_hsv[:, :, 2] > 175)
            )
            st_y0, st_y1 = int(h * 0.28), int(h * 0.74)
            st_x0, st_x1 = int(w * 0.22), int(w * 0.78)
            st_valid = valid_mask[st_y0:st_y1, st_x0:st_x1] > 127
            if int(np.sum(aps_valid)) >= 40 and int(np.sum(st_valid)) >= 120:
                bg_l_med = float(np.median(aps_lab[:, :, 0][aps_valid]))
                bg_a_med = float(np.median(aps_lab[:, :, 1][aps_valid]))
                bg_b_med = float(np.median(aps_lab[:, :, 2][aps_valid]))
                st_lab = lab_full[st_y0:st_y1, st_x0:st_x1]
                st_hsv = hsv_full[st_y0:st_y1, st_x0:st_x1]
                d_bg_lab = np.sqrt(
                    (st_lab[:, :, 0] - bg_l_med) ** 2
                    + (st_lab[:, :, 1] - bg_a_med) ** 2
                    + (st_lab[:, :, 2] - bg_b_med) ** 2
                )
                bare_pave_mask = d_bg_lab < 15.0
                bare_pave_ratio = float(np.mean(bare_pave_mask[st_valid]))
                colored_stencil_px = (
                    (st_hsv[:, :, 0] >= 35)
                    & (st_hsv[:, :, 0] <= 118)
                    & (st_hsv[:, :, 1] >= 42)
                    & (st_hsv[:, :, 2] >= 78)
                )
                white_stencil_px = (st_hsv[:, :, 1] < 42) & (st_hsv[:, :, 2] > 175)
                flat_stencil_mask = colored_stencil_px | white_stencil_px
                stencil_ratio = float(np.mean(flat_stencil_mask[st_valid]))
                col_stencil_cnt = int(np.sum(colored_stencil_px & st_valid))
                stencil_v_std = (
                    float(np.std(st_hsv[:, :, 2][colored_stencil_px & st_valid]))
                    if col_stencil_cnt >= 20
                    else 0.0
                )
                dark_3d_ratio = float(
                    np.mean((st_lab[:, :, 0] < max(36.0, bg_l_med * 0.64))[st_valid])
                )
                dil_stencil = (
                    cv2.dilate(flat_stencil_mask.astype(np.uint8) * 255, np.ones((5, 5), np.uint8)) > 0
                )
                explained_flat_ratio = float(np.mean((bare_pave_mask | dil_stencil)[st_valid]))
                if (
                    bare_pave_ratio >= 0.52
                    and 0.025 <= stencil_ratio <= 0.40
                    and stencil_v_std <= 34.0
                    and explained_flat_ratio >= 0.94
                    and dark_3d_ratio < 0.035
                ):
                    ground_stencil_filtered = True
                    ground_car_score = 0.04
                    ground_rear_score = 0.04
                    active_cols = 0
                    center_edge_ratio = 0.02
                    rear_edge_r = 0.02
                    obj_blob_ratio = 0.0
                    rear_zone_occ = 0.0
                    mid_zone_occ = 0.0
                    mouth_zone_occ = 0.0
                    chromatic_car_detected = False
                    chromatic_anomaly = False
                    painted_green_vehicle_body = False
                    symmetric_achromatic_car = False
                    bilateral_smooth_car = False
                    symmetric_full_car = False
                    smooth_white_cargo_body = False

        # 3. 3D Vehicle-Occupied Envelope Layer (`车辆立体轮廓层`) + Ground-Anchored Fusion
        car_score = ground_car_score
        eff_edge_ratio = center_edge_ratio
        eff_active_cols = active_cols
        veh_3d_score = 0.0
        veh_cabin_edge = 0.0
        veh_active_cols = 0
        canopy_foliage_ratio = 0.0
        tree_canopy_filtered = False
        dual_layer_fused = False
        curb_box_contact = False

        if enable_dual_layer and veh_patch_bgr is not None and veh_patch_bgr.size > 0:
            vh, vw = veh_patch_bgr.shape[:2]
            v_smooth = cv2.GaussianBlur(veh_patch_bgr, (5, 5), 0)
            v_gray = cv2.cvtColor(v_smooth, cv2.COLOR_BGR2GRAY)
            v_hsv = cv2.cvtColor(v_smooth, cv2.COLOR_BGR2HSV)
            v_gx = np.abs(cv2.Sobel(v_gray, cv2.CV_32F, 1, 0, ksize=3))
            v_gy = np.abs(cv2.Sobel(v_gray, cv2.CV_32F, 0, 1, ksize=3))
            v_grad = np.sqrt(v_gx * v_gx + v_gy * v_gy)

            vy0, vy1 = int(vh * 0.10), int(vh * 0.62)
            vx0, vx1 = int(vw * 0.22), int(vw * 0.78)
            if veh_valid_mask is not None and veh_valid_mask.shape[:2] == (vh, vw):
                vmask_core = veh_valid_mask[vy0:vy1, vx0:vx1] > 127
            else:
                vmask_core = np.ones((vy1 - vy0, vx1 - vx0), dtype=bool)
            if int(np.sum(vmask_core)) < 60:
                vmask_core = np.ones((vy1 - vy0, vx1 - vx0), dtype=bool)

            vc_grad = v_grad[vy0:vy1, vx0:vx1][vmask_core]
            vc_gx = v_gx[vy0:vy1, vx0:vx1][vmask_core]
            vc_gy = v_gy[vy0:vy1, vx0:vx1][vmask_core]
            vc_gray = v_gray[vy0:vy1, vx0:vx1][vmask_core]
            vc_hue = v_hsv[vy0:vy1, vx0:vx1, 0][vmask_core]
            vc_sat = v_hsv[vy0:vy1, vx0:vx1, 1][vmask_core]
            vc_val = v_hsv[vy0:vy1, vx0:vx1, 2][vmask_core]

            vc_foliage = (vc_hue >= 34) & (vc_hue <= 88) & (vc_sat >= 45) & (vc_val >= 28)
            canopy_foliage_ratio = float(np.mean(vc_foliage))

            veh_cabin_edge = float(np.mean(vc_grad > 36.0))
            veh_cabin_iso = float(np.sqrt(float(np.mean(vc_gx)) * float(np.mean(vc_gy))) / 40.0)
            vp92, vp08 = np.percentile(vc_gray, [92, 8])
            veh_cabin_spread = float((vp92 - vp08) / 255.0)
            veh_cabin_sat = float(np.mean((vc_sat > 80) & (vc_val > 55) & ~vc_foliage))
            veh_horiz_seam = float(np.mean((vc_gy > 34.0) & (vc_gy > vc_gx * 1.12)))

            v_left = v_grad[vy0:vy1, int(vw * 0.06) : int(vw * 0.22)]
            v_right = v_grad[vy0:vy1, int(vw * 0.78) : int(vw * 0.94)]
            v_side_edge = max(
                float(np.mean(v_left > 36.0)) if v_left.size else 0.0,
                float(np.mean(v_right > 36.0)) if v_right.size else 0.0,
            )

            vspan_g = v_gray[int(vh * 0.12) : int(vh * 0.64), int(vw * 0.18) : int(vw * 0.82)]
            vspan_gr = v_grad[int(vh * 0.12) : int(vh * 0.64), int(vw * 0.18) : int(vw * 0.82)]
            vcol_w = max(1, vspan_g.shape[1] // 8)
            for c_idx in range(8):
                vg_c = vspan_g[:, c_idx * vcol_w : (c_idx + 1) * vcol_w]
                vgr_c = vspan_gr[:, c_idx * vcol_w : (c_idx + 1) * vcol_w]
                if vg_c.size == 0:
                    continue
                vc_sp = float((np.percentile(vg_c, 90) - np.percentile(vg_c, 10)) / 255.0)
                vc_ed = float(np.mean(vgr_c > 36.0))
                if vc_ed > 0.14 or vc_sp > 0.25:
                    veh_active_cols += 1

            veh_3d_score = (
                0.42 * min(1.28, veh_cabin_edge / 0.21)
                + 0.24 * min(1.28, veh_cabin_iso)
                + 0.22 * min(1.28, veh_cabin_spread / 0.36)
                + 0.12 * min(1.28, 0.5 * (veh_horiz_seam / 0.10) + 0.5 * (veh_cabin_sat / 0.22))
            )
            if v_side_edge >= 0.24 and veh_cabin_edge < 0.14 and v_side_edge > veh_cabin_edge * 1.9:
                veh_3d_score *= 0.50

            # Ground-Contact Physical Gate:
            # Prevents background curbs/wheel-stoppers/hedges behind an EMPTY stall from triggering
            # false positives, while catching deeper/oblique parked cars whose tires/chassis enter
            # the ground stall interior and whose 3D cabin/roof fills `veh_patch_bgr` (>=6/8 cols).
            matches_empty_ref = ref_delta_val is not None and ref_delta_val < 0.055
            ground_has_contact = (
                (not neighbor_bleed_suspected)
                and (not matches_empty_ref)
                and ground_car_score >= 0.26
                and (center_edge_ratio >= 0.080 or active_cols >= 4)
            )
            curb_box_contact = (
                ground_has_contact
                and (not colored_paving_filtered)
                and active_cols >= 5
                and veh_active_cols >= 5
                and ground_car_score >= 0.72
                and veh_3d_score >= 0.70
                and veh_cabin_edge >= 0.16
            )
            if ground_has_contact and (
                (veh_active_cols >= 6 and veh_cabin_edge >= 0.18) or curb_box_contact
            ):
                fused_candidate = 0.42 * ground_car_score + 0.58 * veh_3d_score
                car_score = max(ground_car_score, fused_candidate)
                eff_edge_ratio = max(
                    center_edge_ratio,
                    0.68 * veh_cabin_edge + 0.32 * max(center_edge_ratio, rear_edge_r),
                )
                eff_active_cols = max(active_cols, veh_active_cols)
                dual_layer_fused = True
            elif canopy_foliage_ratio >= 0.18 and not ground_has_contact and not painted_green_vehicle_body:
                # Overhanging tree crown / lush hedge inside upper 3D envelope with empty ground brick:
                # attenuate foliage-induced 3D score & soften dappled tree-shadow ground edges.
                tree_canopy_filtered = True
                veh_3d_score *= max(0.25, 1.0 - canopy_foliage_ratio)
                if cone_ratio < 0.010 and lower_foliage_ratio < 0.12:
                    dappled_shadow_free_map = (
                        ((m_grad > 68.0) | ((m_sat > 95) & (m_val > 60)))
                        & ~((m_sat < 35) & (m_val > 185))
                        & ~foliage_mid_map
                        & m_mask
                    )
                    obj_blob_ratio = float(np.mean(dappled_shadow_free_map[m_mask]))
                    if active_cols <= 4 and center_high_sat < 0.08:
                        ground_car_score *= 0.72
                        car_score = ground_car_score

        strong_chromatic_body = (
            chromatic_car_detected
            and chroma_active_cols >= 6
            and underbody_chroma_delta >= 18.0
            and mid_zone_occ >= 0.60
            and obj_blob_ratio >= 0.25
            and not neighbor_bleed_suspected
        )
        if (chromatic_car_detected or symmetric_achromatic_car or smooth_white_cargo_body) and not neighbor_bleed_suspected:
            eff_edge_ratio = max(
                eff_edge_ratio,
                0.24 if chromatic_car_detected else (0.18 if smooth_white_cargo_body else 0.22),
            )
            eff_active_cols = max(
                eff_active_cols,
                chroma_active_cols if chromatic_car_detected else (7 if smooth_white_cargo_body else active_cols),
            )
            if smooth_white_cargo_body:
                veh_active_cols = max(veh_active_cols, 7)
                veh_3d_score = max(veh_3d_score, 0.76)
                ground_car_score = max(ground_car_score, 0.78)
                mid_zone_occ = max(mid_zone_occ, 0.72)
                curb_box_contact = True
                dual_layer_fused = True
            elif strong_chromatic_body:
                ground_car_score = max(ground_car_score, 0.84)
            car_score = max(
                car_score,
                ground_car_score,
                0.86 if strong_chromatic_body else (
                    0.78 if smooth_white_cargo_body else (0.58 if symmetric_achromatic_car else 0.0)
                ),
            )

        metrics: Dict[str, Any] = {
            "car_score": round(car_score, 3),
            "ground_car_score": round(ground_car_score, 3),
            "ground_rear_score": round(ground_rear_score, 3),
            "veh_3d_score": round(veh_3d_score, 3),
            "dual_layer_fused": dual_layer_fused,
            "curb_box_contact": bool(curb_box_contact),
            "tree_canopy_filtered": tree_canopy_filtered,
            "canopy_foliage_ratio": round(canopy_foliage_ratio, 3),
            "ground_foliage_ratio": round(ground_foliage_ratio, 3),
            "edge_ratio": round(center_edge_ratio, 3),
            "eff_edge_ratio": round(eff_edge_ratio, 3),
            "veh_cabin_edge": round(veh_cabin_edge, 3),
            "side_edge_ratio": round(max_side_edge, 3),
            "active_cols": active_cols,
            "veh_active_cols": veh_active_cols,
            "chroma_active_cols": chroma_active_cols,
            "underbody_chroma_delta": round(underbody_chroma_delta, 2),
            "underbody_shadow_ratio": round(underbody_shadow_ratio, 3),
            "chromatic_anomaly": bool(chromatic_anomaly),
            "colored_paving_filtered": bool(colored_paving_filtered),
            "ground_stencil_filtered": bool(ground_stencil_filtered),
            "painted_green_vehicle_body": bool(painted_green_vehicle_body),
            "low_light_scene": bool(low_light_patch),
            "rear_wheelstop_filtered": bool(rear_wheelstop_filtered),
            "neighbor_bleed_suspected": bool(neighbor_bleed_suspected),
            "brick_ratio": round(brick_ratio, 3),
            "occ_cy": round(occ_cy, 3),
            "occ_cx": round(occ_cx, 3),
            "rear_zone_occ": round(rear_zone_occ, 3),
            "mid_zone_occ": round(mid_zone_occ, 3),
            "mouth_zone_occ": round(mouth_zone_occ, 3),
            "intensity_spread": round(center_spread, 3),
            "cone_ratio": round(cone_ratio, 3),
            "obj_blob_ratio": round(obj_blob_ratio, 3),
        }
        if ref_delta_val is not None:
            metrics["ref_delta"] = round(ref_delta_val, 3)

        # Case A0: Clay brick / masonry grading before generic orange cone check
        # Few loose bricks (small footprint, <=3 cols) -> FREE + movable; large stacked brick pile -> BLOCKED + immovable
        if brick_ratio >= 0.012 and cone_ratio < 0.012 and car_score < 0.68 and eff_active_cols < 6:
            if brick_ratio < 0.052 and eff_active_cols <= 3:
                return {
                    "status": "FREE",
                    "obstacle_type": "movable",
                    "action_hint": "地面有少量零散砖块，随手移至路牙旁即可停入",
                    "free_units": 1,
                    "occupied_units": 0,
                    "confidence": 0.68,
                    "needs_vlm": True,
                    "reason": f"检测到少量零散砖石可移物 (占比={brick_ratio:.1%}，待多模态确认)",
                    "metrics": metrics,
                }
            if brick_ratio >= 0.052:
                return {
                    "status": "BLOCKED",
                    "obstacle_type": "immovable",
                    "action_hint": "车位内有成堆砌砖/建材长期占道，暂无法直接停入",
                    "free_units": 0,
                    "occupied_units": 0,
                    "confidence": 0.70,
                    "needs_vlm": True,
                    "reason": f"检测到成堆砌砖/石块占道障碍 (占比={brick_ratio:.1%}，待多模态确认)",
                    "metrics": metrics,
                }

        # Case A: Vivid orange/red/safety-yellow localized object (basketball vs traffic cone / fixed parking lock)
        if 0.010 <= cone_ratio <= 0.25 and car_score < 0.72 and eff_active_cols < 6:
            # Check if blob has bright white reflective collar (traffic cone) or yellow-black iron stripe (parking lock)
            dilated_orange = cv2.dilate(vivid_orange_map.astype(np.uint8) * 255, np.ones((7, 7), np.uint8)) > 0
            n_dilated = int(np.sum(dilated_orange))
            has_white_collar = (
                bool(np.mean((m_val[dilated_orange] > 232) & (m_sat[dilated_orange] < 38)) >= 0.06)
                if n_dilated > 16
                else False
            )
            safety_yellow_px = (m_hue >= 18) & (m_hue <= 32) & (m_sat > 125) & (m_val > 115) & m_mask
            has_yellow_black_lock = (
                bool(
                    float(np.mean(safety_yellow_px[m_mask])) >= 0.008
                    and np.mean(m_val[dilated_orange] < 46) >= 0.12
                )
                if n_dilated > 16
                else False
            )

            ys_o, xs_o = np.where(vivid_orange_map)
            if ys_o.size >= 10:
                o_h = float(int(np.max(ys_o)) - int(np.min(ys_o)) + 1)
                o_w = float(max(1, int(np.max(xs_o)) - int(np.min(xs_o)) + 1))
                is_tall_cone_shape = (o_h / o_w) >= 1.32 and cone_ratio >= 0.020
            else:
                is_tall_cone_shape = False

            if (
                cone_ratio < 0.036
                and not has_white_collar
                and not has_yellow_black_lock
                and not is_tall_cone_shape
            ):
                return {
                    "status": "FREE",
                    "obstacle_type": "movable",
                    "action_hint": "地面有球类或小型可移物品，移至路边即可停入",
                    "free_units": 1,
                    "occupied_units": 0,
                    "confidence": 0.68,  # <0.78 -> escalates to Azure VLM in hybrid mode!
                    "needs_vlm": True,
                    "reason": "检测到小型可移物品（建议 AI 复核并提示移开）",
                    "metrics": metrics,
                }
            return {
                "status": "BLOCKED",
                "obstacle_type": "immovable",
                "action_hint": "检测到锥桶/固定地锁占道，暂无法直接停入",
                "free_units": 0,
                "occupied_units": 0,
                "confidence": 0.70,  # <0.78 -> escalates to Azure VLM in hybrid mode!
                "needs_vlm": True,
                "reason": "检测到雪糕桶/黄黑地锁等固定占道障碍",
                "metrics": metrics,
            }

        # Case B: Full vehicle or box truck / curb-hugging van occupying stall
        standard_full_vehicle = (
            car_score >= 0.56
            and eff_edge_ratio >= 0.22
            and eff_active_cols >= 6
            and (not curb_box_contact)
        )
        box_or_curb_vehicle = (
            (not neighbor_bleed_suspected)
            and (
                not colored_paving_filtered
                or chromatic_car_detected
                or painted_green_vehicle_body
                or (mid_zone_occ >= 0.55 and core_warm_ratio < 0.38 and veh_3d_score >= 0.72)
            )
            and (
                (
                    car_score >= (0.66 if (painted_green_vehicle_body or chromatic_car_detected) else 0.72)
                    and max(eff_active_cols, veh_active_cols) >= 5
                    and (eff_edge_ratio >= 0.14 or veh_3d_score >= 0.70)
                    and (obj_blob_ratio >= 0.16 or veh_3d_score >= 0.70 or mid_zone_occ >= 0.45)
                )
                or (
                    car_score >= 0.85
                    and max(eff_active_cols, veh_active_cols) >= 4
                    and eff_edge_ratio >= 0.15
                    and obj_blob_ratio >= 0.22
                    and veh_3d_score >= 0.68
                )
            )
        )
        if standard_full_vehicle or box_or_curb_vehicle:
            if standard_full_vehicle:
                conf = min(0.96, 0.66 + (car_score - 0.56) * 0.74)
            else:
                conf = min(0.92, max(0.80, 0.66 + max(0.0, car_score - 0.56) * 0.68))
            if low_light_patch:
                conf = min(conf, 0.78)
            escalate_box_vlm = bool(low_light_patch or conf < 0.85)

            if box_or_curb_vehicle and not standard_full_vehicle:
                if escalate_box_vlm:
                    reason_txt = (
                        f"检测到厢式货车/大型车体或贴边停放车辆，交由 AI 核验 (地面={ground_car_score:.2f}, 3D={veh_3d_score:.2f})"
                    )
                else:
                    reason_txt = (
                        f"检测到大型车体/贴边停放车辆 (地面={ground_car_score:.2f}, 3D={veh_3d_score:.2f})"
                    )
            elif dual_layer_fused:
                reason_txt = (
                    f"地面+3D立体双层融合确认停放车辆 (地面={ground_car_score:.2f}, 3D车体={veh_3d_score:.2f}, 综合={car_score:.2f})"
                )
            elif chromatic_car_detected and center_edge_ratio < 0.22:
                reason_txt = (
                    f"车身底部与地面显著色差确认停放车辆 (Lab色差={underbody_chroma_delta:.1f}, 跨列={chroma_active_cols}/8)"
                )
            else:
                reason_txt = f"检测到停放车辆轮廓与车窗对比特征 (score={car_score:.2f})"
            return {
                "status": "OCCUPIED",
                "obstacle_type": "vehicle",
                "action_hint": "",
                "free_units": 0,
                "occupied_units": 1,
                "confidence": conf,
                "needs_vlm": escalate_box_vlm,
                "reason": reason_txt,
                "metrics": metrics,
            }

        # Case B2: Massive non-vehicle bulk / heavy container / large obstacle — physically impossible to move by hand!
        # Never allow high-mass / large-area objects to fall into Case C ("movable")!
        is_massive_immovable = (
            (not neighbor_bleed_suspected)
            and (not colored_paving_filtered)
            and (
                car_score >= 0.62
                or obj_blob_ratio >= 0.24
                or (eff_active_cols >= 5 and (car_score >= 0.48 or obj_blob_ratio >= 0.14))
                or (veh_3d_score >= 0.72 and ground_car_score >= 0.38)
            )
        )
        if is_massive_immovable:
            return {
                "status": "BLOCKED",
                "obstacle_type": "immovable",
                "action_hint": "车位内有大体量箱体或重物占道，无法人工挪移",
                "free_units": 0,
                "occupied_units": 0,
                "confidence": 0.68,
                "needs_vlm": True,
                "reason": f"检测到大体量重物/箱体占道，无法人工移动（待 AI 复核, score={car_score:.2f}）",
                "metrics": metrics,
            }

        # Case C: Genuinely small/light movable object (bicycle, e-bike, pedestrian, pet, ball, few bricks) -> ask VLM!
        if (
            chromatic_anomaly
            or (1 <= eff_active_cols <= 4 and (car_score >= 0.34 or obj_blob_ratio >= 0.08))
            or (eff_active_cols == 5 and car_score < 0.48 and obj_blob_ratio < 0.14)
            or (0.11 <= center_edge_ratio < 0.24 and obj_blob_ratio < 0.24)
            or (0.08 <= obj_blob_ratio < 0.24)
        ) and not neighbor_bleed_suspected and not (
            tree_canopy_filtered and active_cols <= 4 and obj_blob_ratio < 0.08 and cone_ratio < 0.008 and not chromatic_anomaly
        ):
            return {
                "status": "FREE",
                "obstacle_type": "movable",
                "action_hint": "车位内有单车/轻便物或临时行人，移开或示意让行即可停入",
                "free_units": 1,
                "occupied_units": 0,
                "confidence": 0.66,  # <0.78 -> escalates to Azure VLM in hybrid mode!
                "needs_vlm": True,
                "reason": f"检测到非机动车/临时轻物特征，可清理停放 (score={car_score:.2f})",
                "metrics": metrics,
            }

        # Case D: Empty stall (if neighbor bleed exists on side or low-light night scene, ask VLM in hybrid mode)
        conf = 0.72 if neighbor_bleed_suspected else min(0.96, 0.66 + (0.52 - min(0.52, car_score)) * 0.75)
        if low_light_patch:
            conf = min(conf, 0.78)
        return {
            "status": "FREE",
            "obstacle_type": "none",
            "action_hint": "",
            "free_units": 1,
            "occupied_units": 0,
            "confidence": conf,
            "needs_vlm": bool(neighbor_bleed_suspected or low_light_patch),
            "reason": (
                f"车位中心路面空置（已排除邻车边缘透视干扰, score={car_score:.2f}）"
                if neighbor_bleed_suspected
                else f"双层核验路面空置无车辆 (地面={ground_car_score:.2f}, 3D={veh_3d_score:.2f})"
                if (enable_dual_layer and veh_patch_bgr is not None)
                else f"路面平坦无车辆特征 (score={car_score:.2f})"
            ),
            "metrics": metrics,
        }

    def _analyze_elastic_strip_opencv(
        self, strip_bgr: np.ndarray, total_meters: float, car_len_m: float
    ) -> Dict[str, Any]:
        """
        Scan a rectified (672, 160) unmarked roadside strip across 24 longitudinal slices,
        measuring contiguous empty segments in real physical meters.
        """
        num_bins = 24
        h, w = strip_bgr.shape[:2]
        y0, y1 = int(h * 0.15), int(h * 0.85)
        core = cv2.GaussianBlur(strip_bgr[y0:y1, :], (5, 5), 0)
        gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        grad = np.sqrt(gx * gx + gy * gy)

        bin_w = w // num_bins
        raw_occ: List[float] = []
        for b in range(num_bins):
            bx0, bx1 = b * bin_w, (b + 1) * bin_w
            g_slice = grad[:, bx0:bx1]
            v_slice = gray[:, bx0:bx1]
            s_slice = hsv[:, bx0:bx1, 1]
            edge_r = float(np.mean(g_slice > 38.0))
            spread = float((np.percentile(v_slice, 90) - np.percentile(v_slice, 10)) / 255.0)
            sat_r = float(np.mean(s_slice > 85))
            score = 0.50 * (edge_r / 0.22) + 0.35 * (spread / 0.36) + 0.15 * (sat_r / 0.20)
            raw_occ.append(score)

        # Smooth across adjacent bins
        smoothed_occ = [
            float(np.mean(raw_occ[max(0, i - 1) : min(num_bins, i + 2)]))
            for i in range(num_bins)
        ]
        is_occupied_bin = [s >= 0.52 for s in smoothed_occ]

        meters_per_bin = total_meters / num_bins
        free_cars = 0
        occupied_bins = sum(1 for x in is_occupied_bin if x)
        cur_empty_bins = 0
        for occ in is_occupied_bin:
            if not occ:
                cur_empty_bins += 1
            else:
                seg_m = cur_empty_bins * meters_per_bin
                free_cars += int((seg_m + 0.35) // car_len_m)
                cur_empty_bins = 0
        if cur_empty_bins > 0:
            seg_m = cur_empty_bins * meters_per_bin
            free_cars += int((seg_m + 0.35) // car_len_m)

        occupied_cars = max(0, int(round((occupied_bins * meters_per_bin) / car_len_m)))
        status = "FREE" if free_cars >= 1 else "OCCUPIED"
        return {
            "status": status,
            "free_units": free_cars,
            "occupied_units": occupied_cars,
            "confidence": 0.74,  # Triggers VLM in hybrid mode when VLM key is configured
            "reason": f"弹性带总长{total_meters:.1f}m：测算已停约{occupied_cars}辆，剩余连续空档可停 {free_cars} 辆",
            "metrics": {
                "occupied_bins": occupied_bins,
                "total_bins": num_bins,
                "free_cars": free_cars,
            },
        }

    @staticmethod
    def resolve_vlm_request_target(
        vlm_cfg: Dict[str, Any],
    ) -> Tuple[str, str, Dict[str, str], Dict[str, str]]:
        """
        Smart URL, API-protocol & Auth resolver supporting:
          1) Azure AI Foundry / OpenAI Responses API (`/openai/responses?...` or `/v1/responses`)
          2) Azure AI Foundry Chat Completions Target URI (`/deployments/.../chat/completions?...`)
          3) Azure OpenAI / Cognitive Services resource root (`*.openai.azure.com`, `*.cognitiveservices.azure.com`)
          4) Azure AI Services `/models` (`*.services.ai.azure.com`) and standard OpenAI `/v1`.

        Returns (resolved_url, api_style, primary_headers, fallback_headers)
        where api_style is "responses" or "chat_completions".
        """
        raw_url = str(vlm_cfg.get("base_url", "")).strip()
        model = str(vlm_cfg.get("model", "gpt-4o")).strip()
        api_ver = str(vlm_cfg.get("api_version", "") or "2025-04-01-preview").strip()
        api_key = str(vlm_cfg.get("api_key", "")).strip()
        auth_mode = str(vlm_cfg.get("auth_mode", "auto")).strip().lower()

        base = raw_url.rstrip("/")
        lower = raw_url.lower()
        api_style = "chat_completions"

        if "/responses" in lower:
            api_style = "responses"
            if "?" in raw_url or "/v1/responses" in lower or ".azure.com" not in lower:
                resolved_url = raw_url
            else:
                resolved_url = f"{base}?api-version={api_ver}"
        elif "/chat/completions" in lower:
            if "?" in raw_url or "/v1/" in lower or ".azure.com" not in lower:
                resolved_url = raw_url
            else:
                resolved_url = f"{base}?api-version={api_ver}"
        elif (".openai.azure.com" in lower or ".cognitiveservices.azure.com" in lower) and "/v1" not in lower:
            if "?" in raw_url:
                path_part, query_part = raw_url.split("?", 1)
                path_part = path_part.rstrip("/")
                resolved_url = f"{path_part}/openai/deployments/{model}/chat/completions?{query_part}"
            elif "/openai/deployments/" in lower:
                resolved_url = f"{base}/chat/completions?api-version={api_ver}"
            else:
                resolved_url = f"{base}/openai/deployments/{model}/chat/completions?api-version={api_ver}"
        elif ".services.ai.azure.com" in lower and "/v1" not in lower:
            if base.lower().endswith("/models"):
                resolved_url = f"{base}/chat/completions?api-version={api_ver}"
            else:
                resolved_url = f"{base}/models/chat/completions?api-version={api_ver}"
        else:
            resolved_url = f"{base}/chat/completions"

        primary_h = {"Content-Type": "application/json"}
        fallback_h = {"Content-Type": "application/json"}
        if api_key:
            is_azure_key_host = any(
                h in lower
                for h in (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
            )
            use_api_key_first = auth_mode == "api-key" or (
                auth_mode == "auto" and is_azure_key_host and not api_key.startswith("eyJ")
            )
            if use_api_key_first:
                primary_h["api-key"] = api_key
                fallback_h["Authorization"] = f"Bearer {api_key}"
            else:
                primary_h["Authorization"] = f"Bearer {api_key}"
                fallback_h["api-key"] = api_key

        return resolved_url, api_style, primary_h, fallback_h

    @staticmethod
    def _extract_responses_api_text(data: Dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        chunks: List[str] = []
        for item in data.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    txt = part.get("text", "")
                    if txt:
                        chunks.append(str(txt))
        if chunks:
            return "\n".join(chunks).strip()
        if "choices" in data and data["choices"]:
            return str(data["choices"][0]["message"]["content"]).strip()
        return ""

    def _post_vlm_completions(
        self,
        vlm_cfg: Dict[str, Any],
        prompt_text: str,
        image_data_uri: Union[str, Sequence[str]],
        max_tokens: int = 240,
        reasoning_effort: str = "low",
        timeout_override: Optional[float] = None,
    ) -> Tuple[Optional[str], Optional[str], str]:
        url, api_style, primary_headers, fallback_headers = self.resolve_vlm_request_target(vlm_cfg)
        if not vlm_cfg.get("base_url", "").strip():
            return None, "未填写 Endpoint / Target URI", url

        timeout = float(timeout_override or vlm_cfg.get("timeout_seconds", 30))
        model_name = str(vlm_cfg.get("model", "gpt-4o")).strip()
        uris = [image_data_uri] if isinstance(image_data_uri, str) else [str(u) for u in image_data_uri if u]

        if api_style == "responses":
            content_blocks: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt_text}]
            for u in uris:
                content_blocks.append({"type": "input_image", "image_url": u, "detail": "auto"})
            payload: Dict[str, Any] = {
                "model": model_name,
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max(max_tokens, 1024),
                "input": [
                    {
                        "role": "user",
                        "content": content_blocks,
                    }
                ],
            }
        else:
            content_blocks = [{"type": "text", "text": prompt_text}]
            for u in uris:
                content_blocks.append({"type": "image_url", "image_url": {"url": u}})
            payload = {
                "model": model_name,
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": content_blocks,
                    }
                ],
            }

        is_local_mock = "127.0.0.1" in url or "localhost" in url
        vlm_sem = getattr(self, "_vlm_semaphore", None)

        def _parse_retry_after_sec(resp_obj: requests.Response, attempt_idx: int) -> float:
            hdrs_map = getattr(resp_obj, "headers", None) or {}
            ms_hdr = hdrs_map.get("x-ms-retry-after-ms") or hdrs_map.get("X-Ms-Retry-After-Ms")
            if ms_hdr:
                try:
                    return float(np.clip(float(ms_hdr) / 1000.0, 0.6, 8.0))
                except (TypeError, ValueError):
                    pass
            sec_hdr = hdrs_map.get("Retry-After") or hdrs_map.get("retry-after")
            if sec_hdr:
                try:
                    return float(np.clip(float(sec_hdr), 0.6, 8.0))
                except (TypeError, ValueError):
                    pass
            jitter = 0.08 * float((threading.get_ident() % 7) + 1)
            return min(6.0, 0.9 * (2 ** attempt_idx) + jitter)

        def _req_post(hdrs: Dict[str, str], body_json: Dict[str, Any]) -> requests.Response:
            max_attempts = 1 if is_local_mock else 4
            last_exc: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    if vlm_sem is not None:
                        with vlm_sem:
                            r = requests.post(url, headers=hdrs, json=body_json, timeout=timeout)
                    else:
                        r = requests.post(url, headers=hdrs, json=body_json, timeout=timeout)
                    if r.status_code in (429, 502, 503, 504) and attempt + 1 < max_attempts:
                        wait_s = (
                            _parse_retry_after_sec(r, attempt)
                            if r.status_code == 429
                            else 0.8 * (attempt + 1)
                        )
                        time.sleep(wait_s)
                        continue
                    return r
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.Timeout,
                ) as net_exc:
                    last_exc = net_exc
                    if attempt + 1 < max_attempts:
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    raise
            assert last_exc is not None
            raise last_exc

        try:
            resp = _req_post(primary_headers, payload)
            if resp.status_code == 401 and fallback_headers != primary_headers:
                resp = _req_post(fallback_headers, payload)
                active_headers = fallback_headers
            else:
                active_headers = primary_headers

            # Compatibility retry:
            # - For Responses API if a non-reasoning model rejects `reasoning`
            # - For Chat Completions if a reasoning model rejects `max_tokens` or `temperature`
            if resp.status_code == 400:
                err_txt = resp.text
                if api_style == "responses" and "reasoning" in err_txt:
                    compat_payload = dict(payload)
                    compat_payload.pop("reasoning", None)
                    resp = _req_post(active_headers, compat_payload)
                elif api_style == "chat_completions" and (
                    "max_tokens" in err_txt or "temperature" in err_txt
                ):
                    compat_payload = {
                        "model": model_name,
                        "max_completion_tokens": max(max_tokens, 1024),
                        "messages": payload["messages"],
                    }
                    resp = _req_post(active_headers, compat_payload)

            if not resp.ok:
                snippet = resp.text[:200].replace("\n", " ")
                return None, f"HTTP {resp.status_code}: {snippet}", url

            data = resp.json()
            if api_style == "responses":
                content = self._extract_responses_api_text(data)
            else:
                content = str(data["choices"][0]["message"]["content"])
            if not content:
                return None, "模型返回成功但文本内容为空", url
            return content, None, url
        except Exception as exc:
            return None, str(exc)[:160], url

    def test_vlm_connection(self, override_vlm_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a tiny synthetic 64x64 parking spot image to verify Azure Foundry / VLM connectivity."""
        t0 = time.perf_counter()
        cfg = config_manager.get().get("vlm", {})
        if override_vlm_cfg:
            merged = dict(cfg)
            for k, v in override_vlm_cfg.items():
                if k == "api_key" and not v:
                    continue
                merged[k] = v
            cfg = merged

        sample = np.full((64, 64, 3), 82, dtype=np.uint8)
        cv2.rectangle(sample, (6, 6), (58, 58), (220, 220, 220), 2)
        ok, buf = cv2.imencode(".jpg", sample, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        b64 = base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""

        prompt_text = '连通性测试：这是一张灰色空车位示意图，请仅回复 JSON: {"status":"FREE","ok":true}'
        reply, err, resolved_url = self._post_vlm_completions(
            cfg, prompt_text, f"data:image/jpeg;base64,{b64}", max_tokens=120
        )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if err:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "resolved_url": resolved_url,
                "error": err,
            }
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "resolved_url": resolved_url,
            "reply": reply,
            "model": cfg.get("model", ""),
        }

    def _call_slow_lane_vlm(
        self,
        patch_bgr: np.ndarray,
        spot: Dict[str, Any],
        vlm_cfg: Dict[str, Any],
        car_len_m: float,
        has_context_outline: bool = False,
        has_dual_layer_outline: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        ok, buf = cv2.imencode(".jpg", patch_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return None, "JPEG编码失败"
        b64_jpg = base64.b64encode(buf.tobytes()).decode("ascii")

        with self._lock:
            self.privacy_stats["slow_lane_vlm_calls"] += 1

        if spot["spot_type"] == "elastic_strip":
            total_m = float(spot["total_meters"])
            prompt = (
                f"这是一段小区路侧无划线弹性顺停区域的俯拍裁切图，物理总长度约 {total_m:.1f} 米"
                f"（每辆普通家用轿车顺停约需 {car_len_m:.1f} 米）。"
                "请观察画面中已停放的汽车、可移开轻物（自行车/球/纸箱）及固定障碍物（锥桶/地锁），判断剩余连续可停路段能正常停入几辆家用轿车。"
                '仅输出严格 JSON：{"status":"FREE"|"OCCUPIED"|"BLOCKED", "obstacle_type":"none"|"pedestrian"|"movable"|"immovable"|"vehicle", "free_units":整数, "occupied_units":整数, "confidence":0.0到1.0, "reason":"不超过20字中文说明", "action_hint":"如有行人或可移轻物请给出不超过24字处理建议，否则填\\"\\""}'
            )
        else:
            if has_dual_layer_outline:
                ctx_desc = (
                    f"这是小区地面车位【{spot['label']}】的双层空间标定透视截图：\n"
                    f"- 青色实线框（标有 #{spot['label']}）：目标车位的【地面标定层】（无车时地面砖/标线范围）；\n"
                    "- 黄色虚线框及连接线：目标车位对应的【3D 车辆立体包络层】（有车停入后，因斜向俯拍视角，车身/车窗/车顶向上延伸投影的立体区域）。\n"
                )
            elif has_context_outline:
                ctx_desc = (
                    f"这是小区地面车位【{spot['label']}】带左右邻位环境的透视截图，青色线框（标有 #{spot['label']}）标出了目标车位的地面范围。\n"
                )
            else:
                ctx_desc = f"这是小区地面停车位【{spot['label']}】的局部图。\n"

            prompt = (
                ctx_desc
                + "请结合【地面标定层】、【3D 车辆立体层】以及【车身底部与地面颜色/阴影对比】精准判断目标车位当前状态与物业停车指引：\n"
                "1. 【双层立体与车底色差判定（含厢式货车/面包车/轻卡，极其重要）】：\n"
                "   - 在斜拍视角下，停在本车位内的机动车（包括家用轿车、SUV、MPV，以及【白色/蓝色方正货厢的厢式货车/箱式货车、轻卡、面包车、依维柯、工程车】），只要其轮胎/底盘或主体货厢落在本车位青色地面框内（即便靠外侧路牙/绿化带边缘贴边停放），一律必须判为 status=\"OCCUPIED\", obstacle_type=\"vehicle\"！\n"
                "   - 严禁因厢式货车货厢大面积纯白平整、形似方箱而误判为可移动轻物！\n"
                "2. 【严禁将透水红砖/彩砖地面与车位后段防撞石/防撞条误判为占用或障碍（核心铁律）】：\n"
                "   - 小区车位常铺设红色/橙红/灰红透水砖或植草砖（晴天阳光直射或雨后湿润反光时表面会呈现明亮橙红色，与灰色柏油马路颜色截然不同），且车位后沿靠近路牙/绿篱处常装有【水泥防撞石、黄黑橡胶防撞条/止退器（车轮挡）】用于防止倒车出界！\n"
                "   - 【严禁把橙红透水砖空地面误判为红色汽车】：真正的汽车必有立体车顶/深色挡风玻璃/A柱B柱车窗框/后视镜/车轮轮胎与车底浓黑阴影；若青色地面框内只是平坦的橙红/砖红色地面砖铺装（即使反光发亮）而无车窗玻璃和立体车厢，必须判为 status=\"FREE\", obstacle_type=\"none\", parking_pose=\"empty_clear\", action_hint=\"\"！\n"
                "   - 透水红砖/彩砖铺装地面、砖缝纹理、地面喷涂车位号、地面喷涂的新能源小汽车/充电插头/闪电/无障碍轮椅平面图标与绿色边框线、以及车位后段的固定【立柱/壁挂充电桩、充电枪线、防撞石 / 黄黑防撞条 / 止退器 / 路缘石】均属于正常空车位基础设施，绝不是停放车辆也不是障碍物！\n"
                "   - 同样，若汽车完全停在隔壁车位线外（注意不要把左右相邻车位的白色/深色车身侧面误算进本位青色框），或摇晃树枝/绿叶/灌木仅伸入上方黄色 3D 框、地面仅有斑驳树影/落叶而青色地面框无本位车轮/底盘接触，也必须判为 status=\"FREE\", obstacle_type=\"none\", parking_pose=\"empty_clear\"。\n"
                "   - 同时请判断车辆定位姿态 `parking_pose`：若车辆已正常停在车位标定框内部静止停稳，填 \"parked_stationary\"；若车身斜跨在车位前口与马路之间正在倒车入库/驶出揉库，填 \"maneuvering\"；若车位空置无车，填 \"empty_clear\"。\n"
                "3. 【临时有人或猫狗小动物（动态活体让行）】：若车位内有行人驻足/路过，或有猫、狗等宠物/小动物停留，活体会在车辆慢速驶近时自行避让，车位可用！必须判为 status=\"FREE\", obstacle_type=\"pedestrian\"，并在 action_hint 提示（如“临时有人，驶近请减速示意让行”或“车位内有猫狗小动物，请慢速鸣笛观察避让后停入”）。\n"
                "4. 【可移动轻便物 & 少量零散砖头（仅限单人随手可拎走的小体积轻物）】：仅当车位内只有篮球/皮球、自行车、未锁死轻便两轮电动车/滑板车、空纸箱、小塑料桶，或者【车位中部仅有少量零散砖头（1~4块散砖）】等车主单人徒手几秒即可挪至路边的小型轻物时，才判为 status=\"FREE\", obstacle_type=\"movable\"，并在 action_hint 给出具体指引。\n"
                "5. 【大体量不可移动重物 & 成堆砌砖/锥桶/地锁霸位（严禁判为 movable）】：任何体积庞大、沉重、单人无法徒手挪开的物体（如大型货箱/集装箱体、大型机械设备、大号环卫垃圾箱、重型货运三轮车、水泥石墩、【成堆码放/堆砌的砖头（≥5块砖堆或建筑垃圾）】），以及反光锥桶/雪糕桶、车位中央竖起T型/三角形金属地锁，一律必须判为 status=\"BLOCKED\", obstacle_type=\"immovable\"！\n"
                "仅输出严格 JSON：\n"
                '{"status":"FREE"|"OCCUPIED"|"BLOCKED", "obstacle_type":"none"|"pedestrian"|"movable"|"immovable"|"vehicle", "parking_pose":"parked_stationary"|"maneuvering"|"empty_clear", "confidence":0.0到1.0, "reason":"不超过18字识别结论", "action_hint":"不超过28字处理建议（纯净空位或已停汽车填\\"\\"）"}'
            )

        content, err, _ = self._post_vlm_completions(
            vlm_cfg, prompt, f"data:image/jpeg;base64,{b64_jpg}", max_tokens=340
        )
        if err or not content:
            return None, err

        try:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            parsed = json.loads(match.group(0) if match else content)
            st = str(parsed.get("status", "FREE")).upper()
            if st not in ("FREE", "OCCUPIED", "BLOCKED"):
                st = "FREE"

            raw_obs = str(parsed.get("obstacle_type") or "").strip().lower()
            action_hint = str(parsed.get("action_hint") or "").strip()
            raw_pose = str(parsed.get("parking_pose") or "").strip().lower()
            if raw_pose not in ("parked_stationary", "maneuvering", "empty_clear"):
                raw_pose = "parked_stationary" if st == "OCCUPIED" else "empty_clear"

            if raw_obs in ("truck", "box_truck", "van", "cargo_van", "light_truck", "car", "suv", "mpv", "commercial_vehicle"):
                raw_obs = "vehicle"
                st = "OCCUPIED"
                action_hint = ""
            elif raw_obs in ("animal", "pet", "cat", "dog", "person", "human"):
                raw_obs = "pedestrian"
            elif raw_obs in ("wheel_stop", "wheel_stopper", "curb", "curb_stone", "bumper_block", "pavement", "brick_floor"):
                raw_obs = "none"
                st = "FREE"
                action_hint = ""
            elif raw_obs in ("ball", "basketball", "bike", "bicycle", "ebike", "scooter", "few_bricks", "loose_bricks"):
                raw_obs = "movable"
            elif raw_obs in (
                "brick_pile", "stacked_bricks", "debris", "large_box", "container",
                "heavy_object", "machinery", "dumpster", "tricycle_locked",
            ):
                raw_obs = "immovable"
                st = "BLOCKED"

            # Enforce semantic invariant: movable items & pedestrians/animals are ALWAYS parkable (FREE), never BLOCKED!
            if raw_obs in ("movable", "pedestrian"):
                st = "FREE"
            elif st == "OCCUPIED":
                raw_obs = "vehicle"
            elif st == "BLOCKED":
                raw_obs = raw_obs if raw_obs in ("immovable", "cone", "lock") else "immovable"
            elif st == "FREE" and raw_obs not in ("none", "pedestrian", "movable"):
                raw_obs = "none"

            if raw_obs == "movable" and not action_hint:
                action_hint = "车位内有轻便物品，下车移至路边即可停入"
            elif raw_obs == "pedestrian" and not action_hint:
                action_hint = "车位内临时有人，慢速靠近示意会自动让开"

            if spot["spot_type"] == "elastic_strip":
                free_u = max(0, int(parsed.get("free_units", 1 if st == "FREE" else 0)))
                occ_u = max(0, int(parsed.get("occupied_units", 0)))
                st = "FREE" if free_u > 0 else (st if st == "BLOCKED" else "OCCUPIED")
            else:
                free_u = 1 if st == "FREE" else 0
                occ_u = 1 if st == "OCCUPIED" else 0
            out_vlm = {
                "status": st,
                "obstacle_type": raw_obs,
                "parking_pose": "empty_clear" if st == "FREE" and raw_pose == "parked_stationary" else raw_pose,
                "action_hint": action_hint,
                "free_units": free_u,
                "occupied_units": occ_u,
                "confidence": float(parsed.get("confidence", 0.93)),
                "reason": str(parsed.get("reason", "Azure 多模态视觉判定")),
            }
            return (
                self._reconcile_vlm_massive_immovable_guard(
                    out_vlm, None, spot_type=str(spot.get("spot_type") or "standard")
                ),
                None,
            )
        except Exception as parse_exc:
            return None, f"JSON解析失败: {parse_exc}"

    def _reconcile_vlm_massive_immovable_guard(
        self,
        vlm_res: Dict[str, Any],
        fast_res: Optional[Dict[str, Any]] = None,
        spot_type: str = "standard",
    ) -> Dict[str, Any]:
        """
        Safety guardrail preventing massive commercial box trucks (`厢式货车`) or huge immovable
        containers from ever being exposed as `FREE` + `movable` ("可挪轻物").
        Applies ONLY when VLM itself concluded an in-stall object is present (`obstacle_type == "movable"`),
        never disturbing empty-ground (`none`), neighbor-bleed (`none`), or small movable items (ball/bike).
        """
        obs = str(vlm_res.get("obstacle_type") or "none").strip().lower()
        if obs != "movable" or spot_type == "elastic_strip":
            return vlm_res

        txt = f"{vlm_res.get('reason') or ''} {vlm_res.get('action_hint') or ''}"
        has_negation = any(neg in txt for neg in ("无货车", "非货车", "不是货车", "无车"))
        vehicle_terms = (
            "厢式货车",
            "箱式货车",
            "货车",
            "轻卡",
            "卡车",
            "货厢",
            "货箱",
            "车厢",
            "面包车",
            "依维柯",
            "工程车",
            "商用车",
        )
        massive_immovable_terms = (
            "集装箱",
            "活动板房",
            "大型铁皮",
            "大型垃圾箱",
            "水泥墩",
            "石墩",
            "成堆砖",
            "建筑垃圾堆",
        )

        m = (fast_res or {}).get("metrics") or {}
        massive_physical_vehicle = bool(
            fast_res
            and fast_res.get("status") == "OCCUPIED"
            and (
                bool(m.get("curb_box_contact"))
                or (
                    int(m.get("active_cols") or 0) >= 6
                    and int(m.get("veh_active_cols") or 0) >= 6
                    and float(m.get("car_score") or 0.0) >= 0.78
                    and float(m.get("mid_zone_occ") or 0.0) >= 0.65
                )
            )
        )

        if (not has_negation and any(k in txt for k in vehicle_terms)) or massive_physical_vehicle:
            patched = dict(vlm_res)
            patched["status"] = "OCCUPIED"
            patched["obstacle_type"] = "vehicle"
            patched["parking_pose"] = "parked_stationary"
            patched["action_hint"] = ""
            patched["free_units"] = 0
            patched["occupied_units"] = 1
            patched["confidence"] = max(float(vlm_res.get("confidence") or 0.85), 0.90)
            base_r = str(vlm_res.get("reason") or "大型厢式车辆停放").strip()
            patched["reason"] = f"{base_r[:18]} [大型车辆纠偏]"
            return patched

        if not has_negation and any(k in txt for k in massive_immovable_terms):
            patched = dict(vlm_res)
            patched["status"] = "BLOCKED"
            patched["obstacle_type"] = "immovable"
            patched["action_hint"] = "车位内有大型不可移动障碍物，暂无法停入"
            patched["free_units"] = 0
            patched["occupied_units"] = 0
            patched["confidence"] = max(float(vlm_res.get("confidence") or 0.85), 0.88)
            return patched

        return vlm_res

    def benchmark_dual_layer_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        spots_override: Optional[List[Dict[str, Any]]] = None,
        cal_width_override: Optional[int] = None,
        cal_height_override: Optional[int] = None,
        sync_db: bool = False,
        repeats: int = 5,
    ) -> Dict[str, Any]:
        """
        Side-by-side live benchmark comparing:
          - Mode A: Ground-Only Single-Layer (`地面单层标定`)
          - Mode B: Ground + 3D Vehicle-Occupied Dual-Layer (`地面 + 车辆立体双层融合`)
        Measures exact CPU milliseconds (averaged over `repeats`), transient/persistent RAM KB,
        Stage-1 static frame-diff cost, cloud VLM escalation rate, and per-spot recognition diffs.
        """
        cfg = config_manager.get()
        car_len_m = float(cfg.get("standard_car_length_m", 5.2))
        tpl_bundle = self._get_or_load_template_bundle(camera_id)
        if not tpl_bundle:
            raise ValueError(f"未找到相机配置: {camera_id}")
        cam = tpl_bundle
        cached_spot_ref_sigs: Dict[str, np.ndarray] = cam.get("spot_ref_sigs") or {}

        db_spots = self.db.list_spots(camera_id=camera_id, include_signatures=not bool(cached_spot_ref_sigs))
        db_map = {s["spot_id"]: s for s in db_spots}

        if spots_override is not None:
            spots: List[Dict[str, Any]] = []
            for idx, item in enumerate(spots_override):
                sid = str(item.get("spot_id") or f"{camera_id}_{idx + 1:02d}")
                base_sp = dict(db_map.get(sid) or {})
                poly = item.get("polygon") or base_sp.get("polygon") or []
                if len(poly) < 3:
                    continue
                spots.append(
                    {
                        **base_sp,
                        "spot_id": sid,
                        "label": str(item.get("label") or base_sp.get("label") or f"{idx + 1:02d}"),
                        "spot_type": str(item.get("spot_type") or base_sp.get("spot_type") or "standard"),
                        "total_meters": float(item.get("total_meters") or base_sp.get("total_meters") or 5.2),
                        "polygon": poly,
                        "vehicle_polygon": item.get("vehicle_polygon") or base_sp.get("vehicle_polygon"),
                    }
                )
        else:
            spots = db_spots

        if not spots:
            raise ValueError("当前机位尚无已标定车位，请先生成或绘制车位后再实测")

        n_rounds = max(1, min(12, int(repeats)))

        with self.decode_guard():
            t_dec0 = time.perf_counter()
            arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
            full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            del arr
            if full_img is None:
                raise ValueError("无法解码测试图片字节流")

            h_raw, w_raw = full_img.shape[:2]
            if max(h_raw, w_raw) > MAX_DECODE_LONG_EDGE:
                cap_scale = MAX_DECODE_LONG_EDGE / float(max(h_raw, w_raw))
                resized = cv2.resize(
                    full_img,
                    (max(64, int(round(w_raw * cap_scale))), max(64, int(round(h_raw * cap_scale)))),
                    interpolation=cv2.INTER_AREA,
                )
                del full_img
                full_img = resized

            cal_w = max(1, int(cal_width_override or cam.get("image_width") or w_raw))
            cal_h = max(1, int(cal_height_override or cam.get("image_height") or h_raw))
            vlm_cfg_bench = cfg.get("vlm", {})
            has_vlm_bench = bool(vlm_cfg_bench.get("enabled") and vlm_cfg_bench.get("base_url", "").strip())
            full_img, crop_info_b, _, _ = self._resolve_dynamic_crop_roi(
                raw_img=full_img,
                cam=cam,
                spots=spots,
                cal_w=cal_w,
                cal_h=cal_h,
                dynamic_enabled=bool(cfg.get("dynamic_roi_enabled", True)),
                has_vlm_ready=has_vlm_bench,
                vlm_cfg=vlm_cfg_bench,
                now_wall=time.time(),
            )
            if not crop_info_b.get("rectified_by_homography"):
                full_img, _ = self._align_to_reference_template(
                    full_img,
                    cam.get("template_gray_b64"),
                    cal_w,
                    cal_h,
                    cached_ref_small=cam.get("ref_small"),
                    cached_kp_pts=cam.get("orb_kp_pts"),
                    cached_des=cam.get("orb_des"),
                )
            decode_align_ms = (time.perf_counter() - t_dec0) * 1000.0

            h_img, w_img = full_img.shape[:2]
            sx = w_img / cal_w
            sy = h_img / cal_h
            raw_scaled_polys = [[[p[0] * sx, p[1] * sy] for p in sp["polygon"]] for sp in spots]
            snapped_polys = self._snap_scaled_polygons_to_white_lines(full_img, spots, raw_scaled_polys)

            scaled_vpolys: List[Optional[List[List[float]]]] = []
            for sp, raw_s_poly, s_poly in zip(spots, raw_scaled_polys, snapped_polys):
                if sp["spot_type"] == "elastic_strip":
                    scaled_vpolys.append(None)
                    continue
                dx_snap = (s_poly[0][0] - raw_s_poly[0][0]) if (s_poly and raw_s_poly) else 0.0
                dy_snap = (s_poly[0][1] - raw_s_poly[0][1]) if (s_poly and raw_s_poly) else 0.0
                cal_vp = sp.get("vehicle_polygon")
                if not isinstance(cal_vp, list) or len(cal_vp) < 3:
                    cal_vp = derive_vehicle_envelope_polygon(sp["polygon"], cal_w, cal_h)
                scaled_vpolys.append(
                    [
                        [
                            float(np.clip(vp[0] * sx + dx_snap, 0.0, w_img - 1.0)),
                            float(np.clip(vp[1] * sy + dy_snap, 0.0, h_img - 1.0)),
                        ]
                        for vp in cal_vp
                    ]
                )

            ref_sigs: List[Optional[np.ndarray]] = []
            for sp in spots:
                rs = cached_spot_ref_sigs.get(sp["spot_id"])
                if rs is None and sp.get("ref_free_sig_b64"):
                    try:
                        raw_sig = base64.b64decode(sp["ref_free_sig_b64"])
                        if len(raw_sig) == 1024:
                            rs = np.frombuffer(raw_sig, dtype=np.uint8).reshape((32, 32))
                    except Exception:
                        rs = None
                ref_sigs.append(rs)

            # Benchmark A: Single-Layer Ground-Only
            single_results: List[Dict[str, Any]] = []
            single_transient_bytes = 0
            t_s0 = time.perf_counter()
            for r_i in range(n_rounds):
                if r_i == 0:
                    single_results.clear()
                for sp, s_poly, r_sig in zip(spots, snapped_polys, ref_sigs):
                    g_patch, g_mask = extract_spot_patch(full_img, s_poly, spot_type=sp["spot_type"])
                    g_32 = cv2.resize(cv2.cvtColor(g_patch, cv2.COLOR_BGR2GRAY), (32, 32))
                    res = self._analyze_patch_fast_opencv(
                        g_patch,
                        g_mask,
                        spot_type=sp["spot_type"],
                        total_meters=float(sp["total_meters"]),
                        car_len_m=car_len_m,
                        ref_free_sig=r_sig,
                        small_gray=g_32,
                        enable_dual_layer=False,
                    )
                    if r_i == 0:
                        single_transient_bytes = max(
                            single_transient_bytes, int(g_patch.nbytes + g_mask.nbytes)
                        )
                        single_results.append(res)
                    del g_patch, g_mask, g_32
            single_ms_total = ((time.perf_counter() - t_s0) * 1000.0) / n_rounds

            # Benchmark B: Dual-Layer Ground + 3D Vehicle Envelope
            dual_results: List[Dict[str, Any]] = []
            dual_sigs: List[np.ndarray] = []
            dual_transient_bytes = 0
            t_d0 = time.perf_counter()
            for r_i in range(n_rounds):
                if r_i == 0:
                    dual_results.clear()
                    dual_sigs.clear()
                for sp, s_poly, s_vpoly, r_sig in zip(spots, snapped_polys, scaled_vpolys, ref_sigs):
                    g_patch, g_mask = extract_spot_patch(full_img, s_poly, spot_type=sp["spot_type"])
                    if s_vpoly is not None:
                        v_patch, v_mask = extract_spot_patch(
                            full_img, s_vpoly, spot_type=sp["spot_type"], out_size=(160, 160)
                        )
                    else:
                        v_patch, v_mask = None, None
                    g_32, comp_32 = self._make_dual_signatures_32(g_patch, v_patch)
                    res = self._analyze_patch_fast_opencv(
                        g_patch,
                        g_mask,
                        spot_type=sp["spot_type"],
                        total_meters=float(sp["total_meters"]),
                        car_len_m=car_len_m,
                        ref_free_sig=r_sig,
                        small_gray=g_32,
                        veh_patch_bgr=v_patch,
                        veh_valid_mask=v_mask,
                        enable_dual_layer=True,
                    )
                    if r_i == 0:
                        v_bytes = int(v_patch.nbytes + v_mask.nbytes) if (v_patch is not None and v_mask is not None) else 0
                        dual_transient_bytes = max(
                            dual_transient_bytes, int(g_patch.nbytes + g_mask.nbytes) + v_bytes
                        )
                        dual_results.append(res)
                        dual_sigs.append(comp_32)
                    del g_patch, g_mask, g_32
                    if v_patch is not None:
                        del v_patch
                    if v_mask is not None:
                        del v_mask
            dual_ms_total = ((time.perf_counter() - t_d0) * 1000.0) / n_rounds

            # Benchmark C: Stage-1 Static Frame-Diff Gate cost (when parking frame is unchanged)
            t_f0 = time.perf_counter()
            for _ in range(n_rounds):
                for s_poly, s_vpoly, prev_comp in zip(snapped_polys, scaled_vpolys, dual_sigs):
                    g_patch, g_mask = extract_spot_patch(full_img, s_poly, out_size=(96, 96))
                    v_patch = (
                        extract_spot_patch(full_img, s_vpoly, out_size=(96, 96))[0]
                        if s_vpoly is not None
                        else None
                    )
                    _, comp_now = self._make_dual_signatures_32(g_patch, v_patch)
                    _ = float(np.mean(np.abs(comp_now.astype(np.float32) - prev_comp.astype(np.float32))) / 255.0)
                    del g_patch, g_mask
                    if v_patch is not None:
                        del v_patch
            static_diff_ms_total = ((time.perf_counter() - t_f0) * 1000.0) / n_rounds

            del full_img

        def _needs_vlm(obs: Dict[str, Any], stype: str) -> bool:
            return (
                obs["confidence"] < 0.78
                or obs["status"] == "BLOCKED"
                or obs.get("obstacle_type") in ("movable", "pedestrian", "immovable")
                or bool(obs.get("needs_vlm"))
                or stype == "elastic_strip"
            )

        spot_comparisons: List[Dict[str, Any]] = []
        changed_count = 0
        single_vlm_cnt = 0
        dual_vlm_cnt = 0
        now_ts = time.time()

        for sp, s_obs, d_obs, comp_sig in zip(spots, single_results, dual_results, dual_sigs):
            s_vlm = _needs_vlm(s_obs, sp["spot_type"])
            d_vlm = _needs_vlm(d_obs, sp["spot_type"])
            if s_vlm:
                single_vlm_cnt += 1
            if d_vlm:
                dual_vlm_cnt += 1
            changed = s_obs["status"] != d_obs["status"]
            if changed:
                changed_count += 1

            if sync_db and sp["spot_id"] in db_map:
                self._put_signature_lru(f"{camera_id}::{sp['spot_id']}", comp_sig)
                self.fsm.apply_observation(
                    spot=db_map[sp["spot_id"]],
                    observed_status=d_obs["status"],
                    observed_free_units=d_obs["free_units"],
                    observed_occupied_units=d_obs["occupied_units"],
                    confidence=d_obs["confidence"],
                    reason=d_obs["reason"],
                    engine="Stage2_DualLayer_OpenCV",
                    debounce_seconds=0.0,
                    instant_confirm=True,
                    now=now_ts,
                    obstacle_type=str(d_obs.get("obstacle_type") or "none"),
                    action_hint=str(d_obs.get("action_hint") or ""),
                )

            dm = d_obs.get("metrics", {})
            sm = s_obs.get("metrics", {})
            spot_comparisons.append(
                {
                    "spot_id": sp["spot_id"],
                    "label": sp["label"],
                    "single_status": s_obs["status"],
                    "single_confidence": round(float(s_obs["confidence"]), 3),
                    "single_score": sm.get("car_score", 0.0),
                    "single_needs_vlm": s_vlm,
                    "dual_status": d_obs["status"],
                    "dual_confidence": round(float(d_obs["confidence"]), 3),
                    "ground_score": dm.get("ground_car_score", sm.get("car_score", 0.0)),
                    "veh_3d_score": dm.get("veh_3d_score", 0.0),
                    "fused_score": dm.get("car_score", 0.0),
                    "dual_layer_fused": bool(dm.get("dual_layer_fused", False)),
                    "dual_needs_vlm": d_vlm,
                    "changed": changed,
                    "reason": d_obs["reason"],
                }
            )

        if sync_db:
            self.db.record_camera_heartbeat(camera_id, ts=now_ts)

        n_spots = max(1, len(spots))
        single_per_spot = single_ms_total / n_spots
        dual_per_spot = dual_ms_total / n_spots
        extra_per_spot = max(0.0, dual_per_spot - single_per_spot)
        overhead_pct = ((dual_ms_total - single_ms_total) / max(0.1, single_ms_total)) * 100.0

        return {
            "ok": True,
            "camera_id": camera_id,
            "spot_count": len(spots),
            "repeats": n_rounds,
            "synced_db": bool(sync_db),
            "compute": {
                "decode_align_ms": round(decode_align_ms, 2),
                "single_layer_total_ms": round(single_ms_total, 2),
                "single_layer_per_spot_ms": round(single_per_spot, 2),
                "dual_layer_total_ms": round(dual_ms_total, 2),
                "dual_layer_per_spot_ms": round(dual_per_spot, 2),
                "extra_ms_per_spot": round(extra_per_spot, 2),
                "extra_ms_total": round(max(0.0, dual_ms_total - single_ms_total), 2),
                "cpu_overhead_pct": round(overhead_pct, 1),
                "static_framediff_total_ms": round(static_diff_ms_total, 2),
                "single_vlm_escalations": single_vlm_cnt,
                "dual_vlm_escalations": dual_vlm_cnt,
                "single_transient_patch_kb": round(single_transient_bytes / 1024.0, 1),
                "dual_transient_patch_kb": round(dual_transient_bytes / 1024.0, 1),
                "persistent_sig_kb_per_spot": 1.0,
            },
            "summary": {
                "single_occupied": sum(1 for r in spot_comparisons if r["single_status"] == "OCCUPIED"),
                "single_free": sum(1 for r in spot_comparisons if r["single_status"] == "FREE"),
                "dual_occupied": sum(1 for r in spot_comparisons if r["dual_status"] == "OCCUPIED"),
                "dual_free": sum(1 for r in spot_comparisons if r["dual_status"] == "FREE"),
                "changed_spots": changed_count,
            },
            "spots": spot_comparisons,
        }

    def verify_camera_calibration(
        self,
        camera_id: str,
        raw_image_bytes: Optional[bytes] = None,
        apply_polygon_shift: bool = False,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """
        Verify camera mounting stability & calibration accuracy against the stored reference
        template while masking out inner vehicle stall cores (so parked/departing cars or
        night streetlights do not masquerade as camera movement).
        Supports day/night illumination invariance via static-landmark ORB + Sobel edge
        phase-correlation fallback, and optionally shifts polygons when persistent minor
        camera drift is confirmed.
        """
        t0 = time.perf_counter()
        now_ts = time.time()
        cam_full = self.db.get_camera(camera_id, include_blobs=True)
        if not cam_full:
            raise ValueError(f"未找到机位配置: {camera_id}")

        sched = dict(cam_full.get("calib_schedule") or {})
        next_check_at = compute_next_calib_check_ts(sched, now_ts)
        cal_w = max(1, int(cam_full.get("image_width") or 1280))
        cal_h = max(1, int(cam_full.get("image_height") or 720))

        tpl_gray_b64 = cam_full.get("template_gray_b64") or ""
        if not tpl_gray_b64:
            health = {
                "status": "no_template",
                "checked_at": now_ts,
                "last_verified_ts": now_ts,
                "next_check_at": next_check_at,
                "source": source,
                "shift_px": 0.0,
                "drift_px": 0.0,
                "dx_px": 0.0,
                "dy_px": 0.0,
                "rot_deg": 0.0,
                "inliers": 0,
                "compensated": False,
                "polygons_shifted": False,
                "elapsed_ms": 0.0,
                "message": "尚未保存基准参考图，请先上传空场/参考底图完成标定",
            }
            self.db.save_camera_calib_health(camera_id, health)
            return {**health, "ok": True, "camera_id": camera_id, "health": health, "calib_health": health}

        ref_bytes = base64.b64decode(tpl_gray_b64)
        ref_small = cv2.imdecode(np.frombuffer(ref_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if ref_small is None:
            raise ValueError("基准灰度特征图损坏，请重新保存标定底图")

        rh, rw = ref_small.shape[:2]
        check_gray: Optional[np.ndarray] = None
        frame_source = "baseline_self_check"

        if raw_image_bytes:
            with self.decode_guard():
                arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                del arr
                if img is None:
                    raise ValueError("无法解码标定核对图像")
                img, _ = apply_camera_crop_roi(img, cam_full.get("crop_roi"), cal_w=cal_w, cal_h=cal_h)
                check_gray = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (rw, rh), interpolation=cv2.INTER_AREA)
                self._cache_latest_check_frame(camera_id, img, now_ts)
                del img
            frame_source = "uploaded_frame"
        else:
            with self._lock:
                cached_chk = self._latest_check_frames.get(camera_id)
            if cached_chk is not None and cached_chk.get("gray") is not None:
                check_gray = cv2.resize(cached_chk["gray"], (rw, rh), interpolation=cv2.INTER_AREA)
                frame_source = "latest_snapshot"
            elif cam_full.get("vehicle_template_jpeg_b64"):
                try:
                    vb = base64.b64decode(cam_full["vehicle_template_jpeg_b64"])
                    v_img = cv2.imdecode(np.frombuffer(vb, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if v_img is not None:
                        check_gray = cv2.resize(
                            cv2.cvtColor(v_img, cv2.COLOR_BGR2GRAY), (rw, rh), interpolation=cv2.INTER_AREA
                        )
                        frame_source = "vehicle_template_cross_check"
                        del v_img
                except Exception:
                    check_gray = None

        spots = self.db.list_spots(camera_id=camera_id, include_signatures=False)

        # Build static-structure mask: mask out inner 92% of stalls where cars park/depart,
        # preserving painted outer stall lines, curbs, lanes, walls, pillars, and facades.
        static_mask = np.full((rh, rw), 255, dtype=np.uint8)
        sx_s = float(rw) / float(cal_w)
        sy_s = float(rh) / float(cal_h)
        for sp in spots:
            poly = sp.get("polygon") or []
            if len(poly) < 3:
                continue
            pts = np.array([[p[0] * sx_s, p[1] * sy_s] for p in poly], dtype=np.float32)
            ctr = np.mean(pts, axis=0, keepdims=True)
            shrunk = np.round(ctr + (pts - ctr) * 0.92).astype(np.int32)
            cv2.fillPoly(static_mask, [shrunk], 0)
            vpoly = sp.get("vehicle_polygon") or []
            if len(vpoly) >= 3:
                vpts = np.array([[p[0] * sx_s, p[1] * sy_s] for p in vpoly], dtype=np.float32)
                vctr = np.mean(vpts, axis=0, keepdims=True)
                vshrunk = np.round(vctr + (vpts - vctr) * 0.88).astype(np.int32)
                cv2.fillPoly(static_mask, [vshrunk], 0)

        if float(np.mean(static_mask > 0)) < 0.22:
            static_mask.fill(255)

        orb = cv2.ORB_create(nfeatures=450)
        kp_ref, des_ref = orb.detectAndCompute(ref_small, static_mask)
        if not kp_ref or len(kp_ref) < 16:
            kp_ref, des_ref = orb.detectAndCompute(ref_small, None)

        if check_gray is None:
            n_feat = len(kp_ref) if kp_ref else 0
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            health = {
                "status": "healthy",
                "checked_at": now_ts,
                "last_verified_ts": now_ts,
                "next_check_at": next_check_at,
                "source": source,
                "frame_source": frame_source,
                "shift_px": 0.0,
                "drift_px": 0.0,
                "dx_px": 0.0,
                "dy_px": 0.0,
                "rot_deg": 0.0,
                "inliers": n_feat,
                "compensated": False,
                "polygons_shifted": False,
                "spot_count": len(spots),
                "elapsed_ms": elapsed_ms,
                "message": f"基准静态特征完好（{n_feat} 个静态锚点，{len(spots)} 个车位），后续抓拍将自动比对机位偏移",
            }
            self.db.save_camera_calib_health(camera_id, health)
            return {**health, "ok": True, "camera_id": camera_id, "health": health, "calib_health": health}

        # Compute illumination-invariant static Sobel edge maps for both global phase correlation
        # and candidate alignment verification (prevents periodic window/facade aliasing in ORB).
        mask_f = static_mask.astype(np.float32) / 255.0
        ref_edge = cv2.magnitude(
            cv2.Sobel(ref_small, cv2.CV_32F, 1, 0, ksize=3),
            cv2.Sobel(ref_small, cv2.CV_32F, 0, 1, ksize=3),
        ) * mask_f
        cur_edge = cv2.magnitude(
            cv2.Sobel(check_gray, cv2.CV_32F, 1, 0, ksize=3),
            cv2.Sobel(check_gray, cv2.CV_32F, 0, 1, ksize=3),
        ) * mask_f

        def _edge_alignment_sim(dx_s: float, dy_s: float, deg_s: float = 0.0) -> float:
            M_chk = cv2.getRotationMatrix2D((rw * 0.5, rh * 0.5), -deg_s, 1.0)
            M_chk[0, 2] -= dx_s
            M_chk[1, 2] -= dy_s
            warped_cur = cv2.warpAffine(cur_edge, M_chk, (rw, rh), flags=cv2.INTER_LINEAR, borderValue=0.0)
            warped_m = cv2.warpAffine(mask_f, M_chk, (rw, rh), flags=cv2.INTER_NEAREST, borderValue=0.0)
            valid = (mask_f > 0.5) & (warped_m > 0.5)
            if int(np.count_nonzero(valid)) < max(64, int(rw * rh * 0.12)):
                return -1.0
            a = ref_edge[valid]
            b = warped_cur[valid]
            denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-6
            return float(np.dot(a, b) / denom)

        hann = cv2.createHanningWindow((rw, rh), cv2.CV_32F)
        (pc_dx, pc_dy), pc_resp = cv2.phaseCorrelate(ref_edge, cur_edge, hann)
        pc_inliers = int(round(max(0.0, min(1.0, float(pc_resp))) * 40.0))

        orb_dx, orb_dy, orb_rot, orb_inliers = 0.0, 0.0, 0.0, 0
        orb_ok = False
        kp_cur, des_cur = orb.detectAndCompute(check_gray, static_mask)
        if not kp_cur or len(kp_cur) < 16:
            kp_cur, des_cur = orb.detectAndCompute(check_gray, None)

        if (
            des_cur is not None
            and des_ref is not None
            and kp_cur
            and kp_ref
            and len(kp_cur) >= 12
            and len(kp_ref) >= 12
        ):
            bf_knn = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
            raw_knn = bf_knn.knnMatch(des_cur, des_ref, k=2)
            max_disp = 0.24 * float(max(rw, rh))
            good = []
            for pair in raw_knn:
                if not pair:
                    continue
                m = pair[0]
                if m.distance >= 58:
                    continue
                if len(pair) > 1 and m.distance > 0.82 * pair[1].distance:
                    continue
                p_c = kp_cur[m.queryIdx].pt
                p_r = kp_ref[m.trainIdx].pt
                if float(np.hypot(p_c[0] - p_r[0], p_c[1] - p_r[1])) <= max_disp:
                    good.append(m)
            if len(good) >= 8:
                src_pts = np.float32([kp_cur[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp_ref[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                M_aff, inl_mask = cv2.estimateAffinePartial2D(
                    src_pts, dst_pts, method=cv2.RANSAC, ransacReprojThreshold=2.8
                )
                if M_aff is not None and inl_mask is not None and int(inl_mask.sum()) >= 6:
                    sc_est = float(np.hypot(M_aff[0, 0], M_aff[1, 0]))
                    rot_est = float(np.degrees(np.arctan2(M_aff[1, 0], M_aff[0, 0])))
                    if 0.90 <= sc_est <= 1.10 and abs(rot_est) <= 12.0:
                        orb_inliers = int(inl_mask.sum())
                        cx, cy = rw * 0.5, rh * 0.5
                        mapped_c = M_aff @ np.array([cx, cy, 1.0], dtype=np.float64)
                        orb_dx = float(cx - mapped_c[0])
                        orb_dy = float(cy - mapped_c[1])
                        orb_rot = rot_est
                        orb_ok = True

        sim_zero = _edge_alignment_sim(0.0, 0.0, 0.0)
        sim_pc = _edge_alignment_sim(float(pc_dx), float(pc_dy), 0.0)
        sim_orb = _edge_alignment_sim(orb_dx, orb_dy, orb_rot) if orb_ok else -1.0

        if orb_ok and sim_orb >= sim_pc - 0.015 and sim_orb >= sim_zero + 0.01:
            dx_small, dy_small, rot_deg = orb_dx, orb_dy, orb_rot
            inliers = orb_inliers
            method_used = "orb_ransac"
        elif sim_pc > sim_zero + 0.015:
            dx_small, dy_small, rot_deg = float(pc_dx), float(pc_dy), 0.0
            inliers = max(pc_inliers, orb_inliers)
            method_used = "sobel_phase_correlate"
        else:
            dx_small, dy_small, rot_deg = 0.0, 0.0, 0.0
            inliers = max(orb_inliers, pc_inliers, 12)
            method_used = "orb_ransac" if orb_ok else "sobel_phase_correlate"

        dx_px = round(dx_small * (float(cal_w) / float(rw)), 2)
        dy_px = round(dy_small * (float(cal_h) / float(rh)), 2)
        shift_px = round(float(np.hypot(dx_px, dy_px)), 2)
        rot_deg = round(rot_deg, 2)

        max_auto = float(sched.get("max_auto_shift_px", 26.0))
        auto_comp = bool(
            sched["auto_compensate_shift"]
            if "auto_compensate_shift" in sched
            else sched.get("auto_compensate", True)
        )
        polygons_shifted = False

        if shift_px < 2.2 and abs(rot_deg) < 0.8:
            status = "healthy"
            msg = f"机位稳固准确（偏差 {shift_px:.1f}px，旋转 {rot_deg:+.1f}°，匹配点 {inliers}）"
        elif shift_px <= max_auto and abs(rot_deg) <= 3.5:
            if apply_polygon_shift and len(spots) > 0 and shift_px >= 1.0:
                self.db.shift_camera_spot_polygons(camera_id, dx_px, dy_px, cal_w, cal_h)
                self.clear_frame_cache(camera_id)
                polygons_shifted = True
                status = "compensated"
                msg = f"已按实测偏移 (Δx={dx_px:+.1f}px, Δy={dy_px:+.1f}px) 自动平移校准全部 {len(spots)} 个车位框"
            elif auto_comp:
                status = "minor_drift"
                msg = (
                    f"检测到轻微偏移 {shift_px:.1f}px (Δx={dx_px:+.1f}px, Δy={dy_px:+.1f}px)，"
                    "抓拍实时 ORB 防抖对齐已自动补偿，也可点击一键平移车位框"
                )
            else:
                status = "minor_drift"
                msg = f"检测到轻微偏移 {shift_px:.1f}px (Δx={dx_px:+.1f}px, Δy={dy_px:+.1f}px)"
        else:
            status = "drifted"
            msg = (
                f"⚠️ 检测到机位明显移动（偏移 {shift_px:.1f}px / 旋转 {rot_deg:+.1f}°），"
                "超出自动补偿范围，请上传最新空场图重新核对标定！"
            )

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        health = {
            "status": status,
            "checked_at": now_ts,
            "last_verified_ts": now_ts,
            "next_check_at": next_check_at,
            "source": source,
            "frame_source": frame_source,
            "method": method_used,
            "shift_px": shift_px,
            "drift_px": shift_px,
            "dx_px": dx_px,
            "dy_px": dy_px,
            "rot_deg": rot_deg,
            "inliers": inliers,
            "compensated": polygons_shifted,
            "polygons_shifted": polygons_shifted,
            "spot_count": len(spots),
            "elapsed_ms": elapsed_ms,
            "message": msg,
        }
        self.db.save_camera_calib_health(camera_id, health)
        return {**health, "ok": True, "camera_id": camera_id, "health": health, "calib_health": health}


