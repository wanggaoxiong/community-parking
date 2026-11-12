import base64
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests

from app.config import config_manager
from app.database import Database
from app.homography import extract_spot_patch
from app.state_machine import SpotStateMachine


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

    def __init__(self, database: Database, fsm: SpotStateMachine) -> None:
        self.db = database
        self.fsm = fsm
        self._lock = threading.RLock()
        # Only stores tiny 32x32 grayscale structural signatures (1KB, non-identifiable)
        self._prev_signatures: Dict[str, np.ndarray] = {}
        self.privacy_stats: Dict[str, int] = {
            "frames_processed_in_ram": 0,
            "raw_images_written_to_disk": 0,
            "spots_skipped_by_framediff": 0,
            "fast_lane_inferences": 0,
            "slow_lane_vlm_calls": 0,
        }

    def clear_frame_cache(self, camera_id: Optional[str] = None) -> None:
        with self._lock:
            if camera_id is None:
                self._prev_signatures.clear()
            else:
                prefix = f"{camera_id}::"
                for k in [k for k in self._prev_signatures if k.startswith(prefix)]:
                    self._prev_signatures.pop(k, None)

    def process_snapshot_bytes(
        self,
        camera_id: str,
        raw_image_bytes: bytes,
        force_reanalyze: bool = False,
        override_instant_confirm: Optional[bool] = None,
        simulated_timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
        full_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if full_img is None:
            raise ValueError("无法解码上传的图片字节流，请确认格式为 JPG/PNG/WebP")

        h_img, w_img = full_img.shape[:2]
        cfg = config_manager.get()
        now = simulated_timestamp if simulated_timestamp is not None else time.time()

        cam = self.db.get_camera(camera_id)
        if not cam:
            raise ValueError(f"未找到相机配置: {camera_id}")

        spots = self.db.list_spots(camera_id=camera_id)
        # Scale spot polygons proportionally if uploaded test image resolution differs from calibration resolution
        cal_w = max(1, int(cam.get("image_width") or w_img))
        cal_h = max(1, int(cam.get("image_height") or h_img))
        sx = w_img / cal_w
        sy = h_img / cal_h

        extracted_patches: List[Tuple[Dict[str, Any], np.ndarray, np.ndarray]] = []
        for sp in spots:
            scaled_poly = [[p[0] * sx, p[1] * sy] for p in sp["polygon"]]
            patch, mask = extract_spot_patch(full_img, scaled_poly, spot_type=sp["spot_type"])
            extracted_patches.append((sp, patch, mask))

        # Immediately destroy raw full-frame image in RAM (Zero-Disk, Zero-Retention Privacy Guarantee)
        del full_img
        del arr

        with self._lock:
            self.privacy_stats["frames_processed_in_ram"] += 1

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

        for sp, patch, mask in extracted_patches:
            sig_key = f"{camera_id}::{sp['spot_id']}"
            small_gray = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (32, 32))

            with self._lock:
                prev_sig = self._prev_signatures.get(sig_key)
                if prev_sig is not None:
                    diff_score = float(np.mean(np.abs(small_gray.astype(np.float32) - prev_sig.astype(np.float32))) / 255.0)
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
                            "pending_status": None,
                            "free_units": sp["free_units"],
                            "occupied_units": sp["occupied_units"],
                            "note": "帧差 < 阈值，0算力跳过",
                        },
                    }
                )
                continue

            with self._lock:
                self._prev_signatures[sig_key] = small_gray
                self.privacy_stats["fast_lane_inferences"] += 1

            # Stage 2: Fast-lane local OpenCV analysis
            fast_res = self._analyze_patch_fast_opencv(
                patch, mask, spot_type=sp["spot_type"], total_meters=float(sp["total_meters"]), car_len_m=car_len_m
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

            vlm_error_note = ""
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

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "camera_id": camera_id,
            "zone_id": cam["zone_id"],
            "processed_spots": len(spot_reports),
            "elapsed_ms": elapsed_ms,
            "raw_image_retained_on_disk": False,
            "instant_confirm_mode": instant_mode,
            "debounce_seconds": debounce_sec,
            "reports": spot_reports,
        }

    def _analyze_patch_fast_opencv(
        self,
        patch_bgr: np.ndarray,
        valid_mask: np.ndarray,
        spot_type: str = "standard",
        total_meters: float = 5.2,
        car_len_m: float = 5.2,
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

        metrics = {
            "car_score": round(car_score, 3),
            "edge_ratio": round(edge_ratio, 3),
            "intensity_spread": round(intensity_spread, 3),
            "cone_ratio": round(cone_ratio, 3),
        }

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
    def resolve_vlm_request_target(vlm_cfg: Dict[str, Any]) -> Tuple[str, Dict[str, str], Dict[str, str]]:
        """
        Smart URL & Auth resolver supporting Azure AI Foundry (Target URI, Azure OpenAI
        deployments, Azure AI Services /models, OpenAI v1) and standard OpenAI-compatible APIs.
        Returns (resolved_url, primary_headers, fallback_headers).
        """
        raw_url = str(vlm_cfg.get("base_url", "")).strip()
        model = str(vlm_cfg.get("model", "gpt-4o")).strip()
        api_ver = str(vlm_cfg.get("api_version", "") or "2024-12-01-preview").strip()
        api_key = str(vlm_cfg.get("api_key", "")).strip()
        auth_mode = str(vlm_cfg.get("auth_mode", "auto")).strip().lower()

        base = raw_url.rstrip("/")
        lower = base.lower()

        if "/chat/completions" in lower:
            resolved_url = raw_url.strip()
        elif (".openai.azure.com" in lower or ".cognitiveservices.azure.com" in lower) and "/v1" not in lower:
            if "/openai/deployments/" in lower:
                resolved_url = f"{base}/chat/completions?api-version={api_ver}"
            else:
                resolved_url = f"{base}/openai/deployments/{model}/chat/completions?api-version={api_ver}"
        elif ".services.ai.azure.com" in lower and "/v1" not in lower:
            if lower.endswith("/models"):
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

        return resolved_url, primary_h, fallback_h

    def _post_vlm_completions(
        self, vlm_cfg: Dict[str, Any], messages: List[Dict[str, Any]], max_tokens: int = 200
    ) -> Tuple[Optional[str], Optional[str], str]:
        url, primary_headers, fallback_headers = self.resolve_vlm_request_target(vlm_cfg)
        if not vlm_cfg.get("base_url", "").strip():
            return None, "未填写 Endpoint / Target URI", url

        timeout = float(vlm_cfg.get("timeout_seconds", 25))
        model_name = str(vlm_cfg.get("model", "gpt-4o")).strip()
        payload: Dict[str, Any] = {
            "model": model_name,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        try:
            resp = requests.post(url, headers=primary_headers, json=payload, timeout=timeout)
            if resp.status_code == 401 and fallback_headers != primary_headers:
                resp = requests.post(url, headers=fallback_headers, json=payload, timeout=timeout)
                active_headers = fallback_headers
            else:
                active_headers = primary_headers

            # Reasoning / newer Foundry models (o-series / gpt-5 / gpt-4.1) compatibility retry
            if resp.status_code == 400 and (
                "max_tokens" in resp.text or "temperature" in resp.text
            ):
                compat_payload = {
                    "model": model_name,
                    "max_completion_tokens": max(max_tokens, 512),
                    "messages": messages,
                }
                resp = requests.post(url, headers=active_headers, json=compat_payload, timeout=timeout)

            if not resp.ok:
                snippet = resp.text[:160].replace("\n", " ")
                return None, f"HTTP {resp.status_code}: {snippet}", url

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return str(content), None, url
        except Exception as exc:
            return None, str(exc)[:140], url

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

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": '连通性测试：这是一张灰色空车位示意图，请仅回复 JSON: {"status":"FREE","ok":true}',
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ]
        reply, err, resolved_url = self._post_vlm_completions(cfg, messages, max_tokens=80)
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

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_jpg}"},
                    },
                ],
            }
        ]

        content, err, _ = self._post_vlm_completions(vlm_cfg, messages, max_tokens=200)
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
