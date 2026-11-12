import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.config import config_manager
from app.database import Database


MAX_SOFT_LOCKS = 200


class ParkingRecommender:
    """
    Zone-based parking recommendation engine with In-Transit Soft Locks (TTL=180s).

    Cost Formula (lower is better):
        Cost(z, u) = walk_meters(z, u)
                   + dead_end_penalty(z)
                   + compact_suv_penalty(z, vehicle_type)
                   - 14.0 * min(effective_free_spots(z), 3)
    """

    def __init__(self, database: Database) -> None:
        self.db = database
        self._lock = threading.RLock()
        # List of active soft locks: {lock_id, zone_id, driver_tag, unit_id, expires_at}
        self._soft_locks: List[Dict[str, Any]] = []

    def _purge_expired(self, now: float) -> None:
        self._soft_locks = [lk for lk in self._soft_locks if lk["expires_at"] > now]

    def list_active_locks(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        ts = now if now is not None else time.time()
        with self._lock:
            self._purge_expired(ts)
            return [
                {
                    **lk,
                    "remaining_seconds": max(0, int(round(lk["expires_at"] - ts))),
                }
                for lk in self._soft_locks
            ]

    def clear_locks(self) -> None:
        with self._lock:
            self._soft_locks.clear()

    def get_zone_summaries(
        self, vehicle_type: str = "sedan", now: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        ts = now if now is not None else time.time()
        with self._lock:
            self._purge_expired(ts)
            lock_counts: Dict[str, int] = {}
            for lk in self._soft_locks:
                lock_counts[lk["zone_id"]] = lock_counts.get(lk["zone_id"], 0) + 1

        zones = self.db.list_zones()
        cameras = self.db.list_cameras()
        all_spots = self.db.list_spots()

        cams_by_zone: Dict[str, List[Dict[str, Any]]] = {}
        cams_by_id: Dict[str, Dict[str, Any]] = {}
        for cam in cameras:
            cams_by_id[cam["camera_id"]] = cam
            cams_by_zone.setdefault(cam["zone_id"], []).append(cam)

        spots_by_zone: Dict[str, List[Dict[str, Any]]] = {}
        for sp in all_spots:
            spots_by_zone.setdefault(sp["zone_id"], []).append(sp)

        any_real_calibrated = any(
            bool(c.get("has_template")) for c in cameras
        ) or any(s.get("last_engine") != "seed" for s in all_spots)

        summaries = []
        for z in zones:
            zid = z["zone_id"]
            z_spots = spots_by_zone.get(zid, [])
            z_cams = cams_by_zone.get(zid, [])

            raw_free = 0
            compact_free = 0
            occupied = 0
            blocked = 0
            pending_confirming = 0
            unknown = 0
            total_capacity = 0
            calibrated_spot_count = 0

            enriched_zone_spots: List[Dict[str, Any]] = []
            for sp in z_spots:
                cap = int(sp["capacity"])
                total_capacity += cap
                is_spot_calibrated = sp.get("last_engine") != "seed" or bool(
                    cams_by_id.get(sp["camera_id"], {}).get("has_template")
                )
                if is_spot_calibrated:
                    calibrated_spot_count += 1

                st = sp["status"]
                pend = sp.get("pending_status")
                pend_free = sp.get("pending_free_units")

                # Pessimistic net-availability rule ("宁少勿多，绝不虚报空位"):
                # - If a FREE spot detects OCCUPIED/BLOCKED (even in Frame-1 pending debounce),
                #   immediately withhold it from raw_free / effective_free so net free is accurate!
                # - If an OCCUPIED/BLOCKED spot detects FREE in Frame-1, wait for debounce before releasing.
                effective_status = st
                eff_free_u = 0
                eff_occ_u = int(sp["occupied_units"])

                if st == "FREE":
                    if sp["spot_type"] == "elastic_strip":
                        eff_free_u = int(sp["free_units"])
                        if pend_free is not None:
                            if int(pend_free) < eff_free_u:
                                pending_confirming += eff_free_u - int(pend_free)
                            eff_free_u = min(eff_free_u, int(pend_free))
                        if pend == "BLOCKED":
                            pending_confirming += max(1, eff_free_u)
                            eff_free_u = 0
                            effective_status = "BLOCKED"
                            blocked += 1
                        elif eff_free_u <= 0 and pend == "OCCUPIED":
                            effective_status = "OCCUPIED"
                        raw_free += eff_free_u
                        eff_occ_u = max(eff_occ_u, cap - eff_free_u) if eff_free_u < cap else eff_occ_u
                        occupied += eff_occ_u
                    else:
                        if pend == "OCCUPIED":
                            effective_status = "OCCUPIED"
                            eff_free_u = 0
                            eff_occ_u = 1
                            occupied += 1
                            pending_confirming += 1
                        elif pend == "BLOCKED":
                            effective_status = "BLOCKED"
                            eff_free_u = 0
                            eff_occ_u = 0
                            blocked += 1
                            pending_confirming += 1
                        else:
                            eff_free_u = int(sp["free_units"])
                            if sp["compact_only"]:
                                compact_free += eff_free_u
                                if vehicle_type != "suv":
                                    raw_free += eff_free_u
                            else:
                                raw_free += eff_free_u
                            occupied += eff_occ_u
                elif st == "OCCUPIED":
                    eff_occ_u = max(1, int(sp["occupied_units"]))
                    occupied += eff_occ_u
                    if pend in ("FREE", "BLOCKED"):
                        pending_confirming += 1
                elif st == "BLOCKED":
                    blocked += 1
                    if pend in ("FREE", "OCCUPIED"):
                        pending_confirming += 1
                else:
                    unknown += cap

                enriched_zone_spots.append(
                    {
                        "spot_id": sp["spot_id"],
                        "camera_id": sp["camera_id"],
                        "label": sp["label"],
                        "spot_type": sp["spot_type"],
                        "compact_only": sp["compact_only"],
                        "capacity": cap,
                        "status": st,
                        "effective_status": effective_status,
                        "pending_status": pend,
                        "free_units": eff_free_u,
                        "committed_free_units": sp["free_units"],
                        "occupied_units": eff_occ_u,
                        "total_meters": sp["total_meters"],
                        "last_reason": sp["last_reason"],
                        "is_calibrated": is_spot_calibrated,
                        "polygon": sp["polygon"],
                    }
                )

            # Compute normalized 2D layout positions (0..1) from actual calibrated polygons
            self._attach_spatial_layout(enriched_zone_spots, cams_by_id)

            soft_locked = lock_counts.get(zid, 0)
            effective_free = max(0, raw_free - soft_locked)
            is_calibrated_zone = calibrated_spot_count > 0 or any(
                bool(c.get("has_template")) for c in z_cams
            )

            summaries.append(
                {
                    **z,
                    "is_calibrated": is_calibrated_zone,
                    "any_system_calibrated": any_real_calibrated,
                    "cameras": [
                        {
                            "camera_id": c["camera_id"],
                            "name": c["name"],
                            "has_template": bool(c.get("has_template")),
                            "building_hint": (c.get("template_meta") or {}).get("building_hint", ""),
                        }
                        for c in z_cams
                    ],
                    "total_capacity": total_capacity,
                    "raw_free": raw_free,
                    "compact_free": compact_free,
                    "soft_locked": soft_locked,
                    "effective_free": effective_free,
                    "occupied": occupied,
                    "blocked": blocked,
                    "pending_confirming": pending_confirming,
                    "unknown": unknown,
                    "spots": enriched_zone_spots,
                }
            )
        return summaries

    @staticmethod
    def _attach_spatial_layout(
        zone_spots: List[Dict[str, Any]], cams_by_id: Dict[str, Dict[str, Any]]
    ) -> None:
        """
        Projects each camera's calibrated stall polygons into normalized local
        row/slot coordinates (`row_idx`, `col_idx`, `rows_total`, `cols_in_row`,
        `rel_x`, `rel_y`) so the community situational map reflects actual camera
        spot counts, row arrangements, and stall order.
        """
        if not zone_spots:
            return

        by_cam: Dict[str, List[Dict[str, Any]]] = {}
        for sp in zone_spots:
            by_cam.setdefault(sp["camera_id"], []).append(sp)

        cam_groups = list(by_cam.items())
        global_row_offset = 0
        staged_rows: List[List[Dict[str, Any]]] = []

        for cam_id, c_spots in cam_groups:
            cam = cams_by_id.get(cam_id) or {}
            img_w = max(1.0, float(cam.get("image_width") or 1280))
            img_h = max(1.0, float(cam.get("image_height") or 720))

            pts = []
            for sp in c_spots:
                poly = sp.get("polygon") or []
                if poly:
                    cx = sum(float(p[0]) for p in poly) / len(poly) / img_w
                    cy = sum(float(p[1]) for p in poly) / len(poly) / img_h
                else:
                    cx, cy = 0.5, 0.5
                pts.append((cx, cy, sp))

            if len(pts) <= 1:
                staged_rows.append([pts[0][2]])
                continue

            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            span_x = max(xs) - min(xs)
            span_y = max(ys) - min(ys)

            # Determine if spots are arranged primarily left-to-right or front-to-back,
            # and detect multi-row courtyards (e.g. front row + back row).
            if span_x >= span_y * 0.75:
                # Primary axis = X, check if Y splits into 2+ distinct rows
                pts_by_y = sorted(pts, key=lambda t: t[1])
                rows_local: List[List[Any]] = [[pts_by_y[0]]]
                for item in pts_by_y[1:]:
                    prev_mean_y = sum(x[1] for x in rows_local[-1]) / len(rows_local[-1])
                    if abs(item[1] - prev_mean_y) > 0.22 and len(pts) >= 4:
                        rows_local.append([item])
                    else:
                        rows_local[-1].append(item)
                for r in rows_local:
                    r.sort(key=lambda t: t[0])
                    staged_rows.append([t[2] for t in r])
            else:
                # Vertical perspective row receding away from camera -> order from near/top to far/bottom
                pts.sort(key=lambda t: t[1])
                staged_rows.append([t[2] for t in pts])

        rows_total = max(1, len(staged_rows))
        for r_idx, row_spots in enumerate(staged_rows):
            cols_n = max(1, len(row_spots))
            for c_idx, sp in enumerate(row_spots):
                sp["layout"] = {
                    "row_idx": global_row_offset + r_idx,
                    "col_idx": c_idx,
                    "rows_total": rows_total,
                    "cols_in_row": cols_n,
                    "rel_x": round((c_idx + 0.5) / cols_n, 4),
                    "rel_y": round((r_idx + 0.5) / rows_total, 4),
                }
            # Remove bulky polygon vertices from overview payload once layout is computed
            for sp in row_spots:
                sp.pop("polygon", None)

    def recommend(
        self,
        unit_id: str,
        vehicle_type: str = "sedan",  # sedan | suv
        driver_tag: str = "业主车辆",
        acquire_lock: bool = True,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        cfg = config_manager.get()
        ttl = float(cfg.get("soft_lock_ttl_seconds", 180))

        units = {u["unit_id"]: u for u in self.db.list_units()}
        unit_info = units.get(unit_id) or next(iter(units.values()))
        dists = self.db.get_unit_distances(unit_info["unit_id"])
        zone_summaries = self.get_zone_summaries(vehicle_type=vehicle_type, now=ts)

        candidates = []
        for zs in zone_summaries:
            eff = zs["effective_free"]
            if eff <= 0:
                continue
            walk_m = dists.get(zs["zone_id"], 100.0)
            dead_end = float(zs["dead_end_penalty"]) if eff == 1 else float(zs["dead_end_penalty"]) * 0.35
            abundance_bonus = 14.0 * min(eff, 3)
            score = round(walk_m + dead_end - abundance_bonus, 1)
            candidates.append(
                {
                    "zone_id": zs["zone_id"],
                    "zone_name": zs["name"],
                    "walk_meters": int(round(walk_m)),
                    "raw_free": zs["raw_free"],
                    "soft_locked": zs["soft_locked"],
                    "effective_free": eff,
                    "compact_free": zs["compact_free"],
                    "dead_end_penalty": round(dead_end, 1),
                    "score": score,
                    "description": zs["description"],
                }
            )

        candidates.sort(key=lambda c: c["score"])

        if not candidates:
            total_raw = sum(z["raw_free"] for z in zone_summaries)
            if total_raw > 0:
                voice = f"小区仅剩的 {total_raw} 个空位当前均有前车正在驶往，建议在主干道临时等候或前往地下访客区。"
            else:
                voice = "抱歉，当前小区地面监测车位已全部停满，建议直接前往周边备选停车场。"
            return {
                "has_recommendation": False,
                "unit": unit_info,
                "vehicle_type": vehicle_type,
                "primary": None,
                "backup": None,
                "voice_text": voice,
                "lock_created": None,
                "all_candidates": [],
            }

        primary = candidates[0]
        backup = candidates[1] if len(candidates) > 1 else None

        lock_created = None
        if acquire_lock and ttl > 0:
            lock_created = {
                "lock_id": uuid.uuid4().hex[:8],
                "zone_id": primary["zone_id"],
                "zone_name": primary["zone_name"],
                "driver_tag": driver_tag,
                "unit_id": unit_info["unit_id"],
                "created_at": ts,
                "expires_at": ts + ttl,
            }
            with self._lock:
                self._purge_expired(ts)
                if len(self._soft_locks) >= MAX_SOFT_LOCKS:
                    del self._soft_locks[: len(self._soft_locks) - MAX_SOFT_LOCKS + 1]
                self._soft_locks.append(lock_created)
            primary["effective_free_after_lock"] = max(0, primary["effective_free"] - 1)

        compact_note = ""
        if vehicle_type == "sedan" and primary["compact_free"] > 0 and primary["effective_free"] == primary["compact_free"]:
            compact_note = "，含转角紧凑小车位，请注意观察"

        voice = (
            f"欢迎回家！为您推荐 {primary['zone_name']}，"
            f"当前净余 {primary['effective_free']} 个空位，步行至您单元门约 {primary['walk_meters']} 米{compact_note}。"
        )
        if backup:
            voice += f"备选 {backup['zone_name']}，余 {backup['effective_free']} 位，步行 {backup['walk_meters']} 米。"

        return {
            "has_recommendation": True,
            "unit": unit_info,
            "vehicle_type": vehicle_type,
            "primary": primary,
            "backup": backup,
            "voice_text": voice,
            "lock_created": lock_created,
            "all_candidates": candidates,
        }
