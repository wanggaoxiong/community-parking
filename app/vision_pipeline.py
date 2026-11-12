import base64
from collections import OrderedDict
from contextlib import contextmanager
import json
import re
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np
import requests

from app.config import config_manager
from app.database import Database
from app.homography import extract_spot_patch
from app.state_machine import SpotStateMachine

MAX_SIGNATURE_CACHE_ENTRIES = 512  # 512 * (32x32 uint8 = 1KB) = 512 KB hard cap
MAX_CONCURRENT_IMAGE_DECODES = 3   # Prevent concurrent high-res decodes from spiking RAM
MAX_DECODE_LONG_EDGE = 2560        # Downscale ultra-high-res frames immediately in RAM


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
    ) -> None:
        self.db = database
        self.fsm = fsm
        self._lock = threading.RLock()
        self.max_signature_entries = max(16, int(max_signature_entries))
        self.max_concurrent_decodes = max(1, int(max_concurrent_decodes))
        self._decode_semaphore = threading.BoundedSemaphore(self.max_concurrent_decodes)
        self._active_decodes = 0
        # Bounded LRU OrderedDict storing only 32x32 grayscale structural signatures (1KB each)
        self._prev_signatures: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self.privacy_stats: Dict[str, Any] = {
            "frames_processed_in_ram": 0,
            "frames_destroyed_in_ram": 0,
            "raw_images_written_to_disk": 0,
            "spots_skipped_by_framediff": 0,
            "fast_lane_inferences": 0,
            "slow_lane_vlm_calls": 0,
            "signature_cache_entries": 0,
            "signature_cache_max_entries": self.max_signature_entries,
            "signature_cache_kb": 0.0,
            "max_signature_cache_kb": round(self.max_signature_entries * 1.0, 1),
            "active_decodes": 0,
            "max_concurrent_decodes": self.max_concurrent_decodes,
        }

    def _sync_memory_stats_locked(self) -> None:
        total_bytes = sum(int(arr.nbytes) for arr in self._prev_signatures.values())
        self.privacy_stats["frames_destroyed_in_ram"] = self.privacy_stats["frames_processed_in_ram"]
        self.privacy_stats["signature_cache_entries"] = len(self._prev_signatures)
        self.privacy_stats["signature_cache_max_entries"] = self.max_signature_entries
        self.privacy_stats["signature_cache_kb"] = round(total_bytes / 1024.0, 1)
        self.privacy_stats["max_signature_cache_kb"] = round(self.max_signature_entries * 1.0, 1)
        self.privacy_stats["active_decodes"] = self._active_decodes
        self.privacy_stats["max_concurrent_decodes"] = self.max_concurrent_decodes

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
            self._sync_memory_stats_locked()

    def clear_frame_cache(self, camera_id: Optional[str] = None) -> None:
        with self._lock:
            if camera_id is None:
                self._prev_signatures.clear()
            else:
                prefix = f"{camera_id}::"
                for k in [k for k in self._prev_signatures if k.startswith(prefix)]:
                    self._prev_signatures.pop(k, None)
            self._sync_memory_stats_locked()

    def prime_reference_signatures(self, camera_id: str, spot_sigs: Dict[str, np.ndarray]) -> None:
        for spot_id, sig in spot_sigs.items():
            if isinstance(sig, np.ndarray) and sig.shape == (32, 32):
                self._put_signature_lru(f"{camera_id}::{spot_id}", sig)

    @staticmethod
    def _align_to_reference_template(
        full_img: np.ndarray,
        template_gray_b64: Optional[str],
        cal_w: int,
        cal_h: int,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Compensate minor wind/window-mount camera shake against the saved Reference Template
        using ORB keypoints + RANSAC Homography in low-res grayscale (<8ms CPU).
        """
        if not template_gray_b64:
            return full_img, {"active": False, "aligned": False, "shift_px": 0.0}

        try:
            ref_bytes = base64.b64decode(template_gray_b64)
            ref_small = cv2.imdecode(np.frombuffer(ref_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if ref_small is None:
                return full_img, {"active": False, "aligned": False, "shift_px": 0.0}

            rh, rw = ref_small.shape[:2]
            cur_small = cv2.resize(cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY), (rw, rh))

            orb = cv2.ORB_create(nfeatures=380)
            kp1, des1 = orb.detectAndCompute(cur_small, None)
            kp2, des2 = orb.detectAndCompute(ref_small, None)
            if des1 is None or des2 is None or len(kp1) < 20 or len(kp2) < 20:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

            matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = sorted(matcher.match(des1, des2), key=lambda m: m.distance)[:90]
            if len(matches) < 18:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

            src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
            H_small, inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 3.5)
            inlier_cnt = int(np.sum(inliers)) if inliers is not None else 0
            if H_small is None or inlier_cnt < 16:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

            det = float(np.linalg.det(H_small[:2, :2]))
            if not (0.86 <= det <= 1.16):
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

            corners = np.float32([[[0, 0]], [[rw, 0]], [[rw, rh]], [[0, rh]]])
            warped_corners = cv2.perspectiveTransform(corners, H_small)
            corner_disp = np.linalg.norm(warped_corners - corners, axis=2)
            mean_disp_small = float(np.mean(corner_disp))
            max_disp_small = float(np.max(corner_disp))

            # Reject wild false homographies (>8.5% frame jump) and skip sub-pixel noise (<1.2px)
            if max_disp_small > max(rw, rh) * 0.085:
                return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

            h_img, w_img = full_img.shape[:2]
            scale_back = w_img / max(1.0, float(rw))
            shift_full_px = round(mean_disp_small * scale_back, 2)
            if shift_full_px < 1.4:
                return full_img, {"active": True, "aligned": False, "shift_px": shift_full_px}

            S_down = np.array([[rw / w_img, 0, 0], [0, rh / h_img, 0], [0, 0, 1]], dtype=np.float64)
            S_up = np.array([[cal_w / rw, 0, 0], [0, cal_h / rh, 0], [0, 0, 1]], dtype=np.float64)
            H_full = S_up @ H_small @ S_down
            aligned = cv2.warpPerspective(
                full_img, H_full, (cal_w, cal_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )
            return aligned, {"active": True, "aligned": True, "shift_px": shift_full_px, "inliers": inlier_cnt}
        except Exception:
            return full_img, {"active": True, "aligned": False, "shift_px": 0.0}

    def process_snapshot_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        force_reanalyze: bool = False,
        override_instant_confirm: Optional[bool] = None,
        simulated_timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        cfg = config_manager.get()
        now = simulated_timestamp if simulated_timestamp is not None else time.time()

        cam = self.db.get_camera(camera_id, include_blobs=True)
        if not cam:
            raise ValueError(f"未找到相机配置: {camera_id}")

        spots = self.db.list_spots(camera_id=camera_id, include_signatures=True)
        extracted_patches: List[Tuple[Dict[str, Any], np.ndarray, np.ndarray]] = []

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
            cal_w = max(1, int(cam.get("image_width") or w_raw))
            cal_h = max(1, int(cam.get("image_height") or h_raw))

            full_img, tpl_align_info = self._align_to_reference_template(
                full_img, cam.get("template_gray_b64"), cal_w, cal_h
            )
            h_img, w_img = full_img.shape[:2]
            # Scale spot polygons proportionally if uploaded image resolution differs from calibration resolution
            sx = w_img / cal_w
            sy = h_img / cal_h

            for sp in spots:
                scaled_poly = [[p[0] * sx, p[1] * sy] for p in sp["polygon"]]
                patch, mask = extract_spot_patch(full_img, scaled_poly, spot_type=sp["spot_type"])
                extracted_patches.append((sp, patch, mask))

            # Immediately destroy raw full-frame image in RAM (Zero-Disk, Zero-Retention Privacy Guarantee)
            del full_img

        with self._lock:
            self.privacy_stats["frames_processed_in_ram"] += 1
            self._sync_memory_stats_locked()

        self.db.record_camera_heartbeat(camera_id, ts=now)

        debounce_sec = float(cfg.get("debounce_seconds", 8.0))
        instant_mode = (
            override_instant_confirm
            if override_instant_confirm is not None
            else bool(cfg.get("instant_confirm_mode", False))
        )
        diff_thresh = float(cfg.get("ssim_change_threshold", 0.035))
        vlm_cfg = cfg.get("vlm", {})
        vlm_mode = vlm_cfg.get("mode", "hybrid")
        car_len_m = float(cfg.get("standard_car_length_m", 5.2))

        spot_reports: List[Dict[str, Any]] = []

        try:
            while extracted_patches:
                sp, patch, mask = extracted_patches.pop(0)
                try:
                    sig_key = f"{camera_id}::{sp['spot_id']}"
                    small_gray = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (32, 32))

                    prev_sig = self._get_signature_lru(sig_key)
                    if prev_sig is not None:
                        diff_score = float(
                            np.mean(np.abs(small_gray.astype(np.float32) - prev_sig.astype(np.float32))) / 255.0
                        )
                    else:
                        diff_score = 1.0

                    # Note: If spot currently has a pending debounce check waiting for confirmation, NEVER skip via framediff!
                    has_pending = sp.get("pending_status") is not None
                    if (not force_reanalyze) and (not has_pending) and diff_score < diff_thresh:
                        with self._lock:
                            self.privacy_stats["spots_skipped_by_framediff"] += 1
                        self.db.update_spot_state(sp["spot_id"], {"last_seen_at": now})
                        spot_reports.append(
                            {
                                "spot_id": sp["spot_id"],
                                "label": sp["label"],
                                "spot_type": sp["spot_type"],
                                "diff_score": round(diff_score, 4),
                                "stage_used": "Stage1_FrameDiff_Skipped",
                                "observed_status": sp["status"],
                                "confidence": sp["confidence"],
                                "reason": "画面静止无变化（帧差拦截跳过推理）",
                                "fsm": {
                                    "transition": "SKIPPED_STATIC",
                                    "committed_status": sp["status"],
                                    "effective_status": sp["status"],
                                    "pending_status": None,
                                    "free_units": sp["free_units"],
                                    "effective_free_units": sp["free_units"] if sp["status"] == "FREE" else 0,
                                    "occupied_units": sp["occupied_units"],
                                    "note": "帧差 < 阈值，0算力跳过",
                                },
                            }
                        )
                        continue

                    self._put_signature_lru(sig_key, small_gray)
                    with self._lock:
                        self.privacy_stats["fast_lane_inferences"] += 1

                    # Stage 2: Fast-lane local OpenCV analysis (+ Reference Template empty-stall baseline if available)
                    ref_sig_arr: Optional[np.ndarray] = None
                    if sp.get("ref_free_sig_b64"):
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
                    )

                    final_obs = fast_res
                    stage_used = "Stage2_Fast_OpenCV"

                    # Stage 3: Decide whether to invoke Slow-lane Multimodal VLM (Azure AI Foundry / OpenAI-compat)
                    base_u = vlm_cfg.get("base_url", "").strip()
                    has_vlm_ready = bool(base_u) and (
                        bool(vlm_cfg.get("api_key"))
                        or "127.0.0.1" in base_u
                        or "localhost" in base_u
                    )
                    need_vlm = False
                    if vlm_mode == "always_vlm" and has_vlm_ready:
                        need_vlm = True
                    elif vlm_mode == "hybrid" and has_vlm_ready:
                        if (
                            fast_res["confidence"] < 0.78
                            or fast_res["status"] == "BLOCKED"
                            or sp["spot_type"] == "elastic_strip"
                        ):
                            need_vlm = True

                    if need_vlm:
                        vlm_res, vlm_err = self._call_slow_lane_vlm(patch, sp, vlm_cfg, car_len_m)
                        if vlm_res is not None:
                            final_obs = vlm_res
                            stage_used = f"Azure/VLM ({vlm_cfg.get('model', 'model')})"
                        else:
                            vlm_error_note = f" [VLM回退: {vlm_err}]" if vlm_err else " [VLM回退]"
                            stage_used = "Fast_OpenCV" + vlm_error_note

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
                    )

                    # Self-learn empty reference baseline when an uncalibrated/initially-occupied spot becomes FREE
                    if (
                        sp["spot_type"] != "elastic_strip"
                        and not sp.get("ref_free_sig_b64")
                        and fsm_res["committed_status"] == "FREE"
                        and final_obs["confidence"] >= 0.88
                    ):
                        self.db.update_spot_state(
                            sp["spot_id"],
                            {"ref_free_sig_b64": base64.b64encode(small_gray.tobytes()).decode("ascii")},
                        )

                    spot_reports.append(
                        {
                            "spot_id": sp["spot_id"],
                            "label": sp["label"],
                            "spot_type": sp["spot_type"],
                            "diff_score": round(diff_score, 4),
                            "stage_used": stage_used,
                            "observed_status": final_obs["status"],
                            "confidence": round(final_obs["confidence"], 3),
                            "reason": final_obs["reason"],
                            "metrics": fast_res.get("metrics", {}),
                            "fsm": fsm_res,
                        }
                    )
                finally:
                    del patch
                    del mask
        finally:
            extracted_patches.clear()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "camera_id": camera_id,
            "zone_id": cam["zone_id"],
            "processed_spots": len(spot_reports),
            "elapsed_ms": elapsed_ms,
            "raw_image_retained_on_disk": False,
            "instant_confirm_mode": instant_mode,
            "debounce_seconds": debounce_sec,
            "template_alignment": tpl_align_info,
            "reports": spot_reports,
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
    ) -> Dict[str, Any]:
        if spot_type == "elastic_strip":
            return self._analyze_elastic_strip_opencv(patch_bgr, total_meters, car_len_m)

        h, w = patch_bgr.shape[:2]
        # Inset by 15% on each side so painted parking stall lines & neighboring bumpers don't pollute features
        y0, y1 = int(h * 0.15), int(h * 0.85)
        x0, x1 = int(w * 0.15), int(w * 0.85)
        core_bgr = patch_bgr[y0:y1, x0:x1]
        core_mask = valid_mask[y0:y1, x0:x1] > 127
        if int(np.sum(core_mask)) < 100:
            core_bgr = patch_bgr
            core_mask = np.ones(patch_bgr.shape[:2], dtype=bool)

        smoothed = cv2.GaussianBlur(core_bgr, (5, 5), 0)
        gray = cv2.cvtColor(smoothed, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(smoothed, cv2.COLOR_BGR2HSV)

        # Cue 1: 2D multi-directional Sobel gradient energy (cars have rich 2D edges; road/straight shadow is 0D/1D)
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        grad_mag = np.sqrt(gx * gx + gy * gy)
        # Erode mask slightly to avoid black polygon boundary step-edge in irregular crops
        eroded_mask = cv2.erode(core_mask.astype(np.uint8) * 255, np.ones((7, 7), np.uint8)) > 127
        if int(np.sum(eroded_mask)) < 64:
            eroded_mask = core_mask

        valid_grad = grad_mag[eroded_mask]
        valid_gray = gray[eroded_mask]
        valid_sat = hsv[:, :, 1][eroded_mask]
        valid_hue = hsv[:, :, 0][eroded_mask]
        valid_val = hsv[:, :, 2][eroded_mask]

        edge_ratio = float(np.mean(valid_grad > 38.0))
        gx_mean = float(np.mean(gx[eroded_mask]))
        gy_mean = float(np.mean(gy[eroded_mask]))
        iso_energy = float(np.sqrt(gx_mean * gy_mean) / 42.0)

        # Cue 2: Dynamic range spread (dark windshield/tires vs bright roof/hood reflection)
        p92, p08 = np.percentile(valid_gray, [92, 8])
        intensity_spread = float((p92 - p08) / 255.0)

        # Cue 3: Saturation & vivid obstacle (orange cone / yellow parking lock) check
        high_sat_ratio = float(np.mean((valid_sat > 85) & (valid_val > 60)))
        cone_color_mask = (
            ((valid_hue <= 28) | (valid_hue >= 168))
            & (valid_sat > 125)
            & (valid_val > 90)
        )
        cone_ratio = float(np.mean(cone_color_mask))

        # Composite vehicle presence score in [0, 1.3]
        car_score = (
            0.40 * min(1.2, edge_ratio / 0.22)
            + 0.25 * min(1.2, iso_energy)
            + 0.23 * min(1.2, intensity_spread / 0.36)
            + 0.12 * min(1.2, high_sat_ratio / 0.20)
        )

        metrics: Dict[str, Any] = {
            "car_score": round(car_score, 3),
            "edge_ratio": round(edge_ratio, 3),
            "intensity_spread": round(intensity_spread, 3),
            "cone_ratio": round(cone_ratio, 3),
        }

        # Reference-Template Baseline Boost: compare zero-mean normalized 32x32 texture to known empty stall
        if ref_free_sig is not None:
            cur_32 = (
                small_gray
                if small_gray is not None
                else cv2.resize(cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY), (32, 32))
            )
            c_norm = cur_32.astype(np.float32) - float(np.mean(cur_32))
            r_norm = ref_free_sig.astype(np.float32) - float(np.mean(ref_free_sig))
            ref_delta = float(np.mean(np.abs(c_norm - r_norm)) / 255.0)
            metrics["ref_delta"] = round(ref_delta, 3)
            if ref_delta < 0.055 and cone_ratio < 0.025:
                car_score *= 0.68
            elif ref_delta > 0.145 and car_score >= 0.40:
                car_score = max(car_score, 0.58 + min(0.30, (ref_delta - 0.145) * 1.4))
            metrics["car_score"] = round(car_score, 3)

        # Detect small traffic cone / yellow lock occupying 2.5%~22% of empty pavement
        if 0.025 <= cone_ratio <= 0.24 and car_score < 0.72:
            return {
                "status": "BLOCKED",
                "free_units": 0,
                "occupied_units": 0,
                "confidence": 0.72,  # Deliberately <0.78 so hybrid mode asks VLM to confirm!
                "reason": "检测到疑似雪糕桶/地锁等高亮占道物",
                "metrics": metrics,
            }

        if car_score >= 0.52:
            conf = min(0.96, 0.62 + (car_score - 0.52) * 0.75)
            return {
                "status": "OCCUPIED",
                "free_units": 0,
                "occupied_units": 1,
                "confidence": conf,
                "reason": f"检测到停放车辆轮廓与车窗对比特征 (score={car_score:.2f})",
                "metrics": metrics,
            }
        else:
            conf = min(0.96, 0.62 + (0.52 - car_score) * 0.85)
            return {
                "status": "FREE",
                "free_units": 1,
                "occupied_units": 0,
                "confidence": conf,
                "reason": f"路面平坦无车辆特征 (score={car_score:.2f})",
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
        image_data_uri: str,
        max_tokens: int = 240,
        reasoning_effort: str = "low",
        timeout_override: Optional[float] = None,
    ) -> Tuple[Optional[str], Optional[str], str]:
        url, api_style, primary_headers, fallback_headers = self.resolve_vlm_request_target(vlm_cfg)
        if not vlm_cfg.get("base_url", "").strip():
            return None, "未填写 Endpoint / Target URI", url

        timeout = float(timeout_override or vlm_cfg.get("timeout_seconds", 30))
        model_name = str(vlm_cfg.get("model", "gpt-4o")).strip()

        if api_style == "responses":
            payload: Dict[str, Any] = {
                "model": model_name,
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max(max_tokens, 1024),
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt_text},
                            {
                                "type": "input_image",
                                "image_url": image_data_uri,
                                "detail": "auto",
                            },
                        ],
                    }
                ],
            }
        else:
            payload = {
                "model": model_name,
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {"type": "image_url", "image_url": {"url": image_data_uri}},
                        ],
                    }
                ],
            }

        try:
            resp = requests.post(url, headers=primary_headers, json=payload, timeout=timeout)
            if resp.status_code == 401 and fallback_headers != primary_headers:
                resp = requests.post(url, headers=fallback_headers, json=payload, timeout=timeout)
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
                    resp = requests.post(url, headers=active_headers, json=compat_payload, timeout=timeout)
                elif api_style == "chat_completions" and (
                    "max_tokens" in err_txt or "temperature" in err_txt
                ):
                    compat_payload = {
                        "model": model_name,
                        "max_completion_tokens": max(max_tokens, 1024),
                        "messages": payload["messages"],
                    }
                    resp = requests.post(url, headers=active_headers, json=compat_payload, timeout=timeout)

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
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        ok, buf = cv2.imencode(".jpg", patch_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
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
                "请观察画面中已停放的汽车及雪糕桶/电瓶车障碍物，判断剩余连续空白路段还能正常停入几辆家用轿车。"
                '仅输出严格 JSON：{"status":"FREE"或"OCCUPIED"或"BLOCKED", "free_units":整数, "occupied_units":整数, "confidence":0.0到1.0, "reason":"不超过20字中文说明"}'
            )
        else:
            prompt = (
                "这是小区单个地面停车位的局部裁切图。请判断该车位当前状态："
                "1. 若为空车位（仅有树影、水渍、标线但可正常停车），status 填 FREE；"
                "2. 若停有汽车，status 填 OCCUPIED；"
                "3. 若无汽车但被雪糕桶/锥桶、地锁、电瓶车/三轮车或杂物占道导致汽车无法停入，status 填 BLOCKED。"
                '仅输出严格 JSON：{"status":"FREE"|"OCCUPIED"|"BLOCKED", "confidence":0.0到1.0, "reason":"不超过18字中文说明"}'
            )

        content, err, _ = self._post_vlm_completions(
            vlm_cfg, prompt, f"data:image/jpeg;base64,{b64_jpg}", max_tokens=240
        )
        if err or not content:
            return None, err

        try:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            parsed = json.loads(match.group(0) if match else content)
            st = str(parsed.get("status", "FREE")).upper()
            if st not in ("FREE", "OCCUPIED", "BLOCKED"):
                st = "FREE"
            if spot["spot_type"] == "elastic_strip":
                free_u = max(0, int(parsed.get("free_units", 1 if st == "FREE" else 0)))
                occ_u = max(0, int(parsed.get("occupied_units", 0)))
                st = "FREE" if free_u > 0 else (st if st == "BLOCKED" else "OCCUPIED")
            else:
                free_u = 1 if st == "FREE" else 0
                occ_u = 1 if st == "OCCUPIED" else 0
            return (
                {
                    "status": st,
                    "free_units": free_u,
                    "occupied_units": occ_u,
                    "confidence": float(parsed.get("confidence", 0.93)),
                    "reason": str(parsed.get("reason", "Azure 多模态视觉判定")),
                },
                None,
            )
        except Exception as parse_exc:
            return None, f"JSON解析失败: {parse_exc}"
