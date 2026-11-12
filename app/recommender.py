import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.config import config_manager
from app.database import Database


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
        all_spots = self.db.list_spots()
        spots_by_zone: Dict[str, List[Dict[str, Any]]] = {}
        for sp in all_spots:
            spots_by_zone.setdefault(sp["zone_id"], []).append(sp)

        summaries = []
        for z in zones:
            zid = z["zone_id"]
            z_spots = spots_by_zone.get(zid, [])
            raw_free = 0
            compact_free = 0
            occupied = 0
            blocked = 0
            unknown = 0
            total_capacity = 0

            for sp in z_spots:
                total_capacity += int(sp["capacity"])
                st = sp["status"]
                if st == "FREE":
                    fu = int(sp["free_units"])
                    if sp["compact_only"]:
                        compact_free += fu
                        if vehicle_type != "suv":
                            raw_free += fu
                    else:
                        raw_free += fu
                    occupied += int(sp["occupied_units"])
                elif st == "OCCUPIED":
                    occupied += max(1, int(sp["occupied_units"]))
                elif st == "BLOCKED":
                    blocked += 1
                else:
                    unknown += int(sp["capacity"])

            soft_locked = lock_counts.get(zid, 0)
            effective_free = max(0, raw_free - soft_locked)

            summaries.append(
                {
                    **z,
                    "total_capacity": total_capacity,
                    "raw_free": raw_free,
                    "compact_free": compact_free,
                    "soft_locked": soft_locked,
                    "effective_free": effective_free,
                    "occupied": occupied,
                    "blocked": blocked,
                    "unknown": unknown,
                    "spots": [
                        {
                            "spot_id": s["spot_id"],
                            "label": s["label"],
                            "spot_type": s["spot_type"],
                            "compact_only": s["compact_only"],
                            "status": s["status"],
                            "pending_status": s["pending_status"],
                            "free_units": s["free_units"],
                            "occupied_units": s["occupied_units"],
                            "total_meters": s["total_meters"],
                            "last_reason": s["last_reason"],
                        }
                        for s in z_spots
                    ],
                }
            )
        return summaries

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
