import time
from typing import Any, Dict, Optional

from app.database import Database


class SpotStateMachine:
    """
    Four-state anti-flicker (dual-frame debounce) state machine:
      - FREE: Available for parking
      - OCCUPIED: Confirmed parked vehicle
      - BLOCKED: Occupied by cone / lock / e-bike / obstacle (not parkable)
      - UNKNOWN: Camera offline beyond heartbeat timeout

    Transient single-frame disturbances (e.g. a passing car occluding a free spot,
    or brief headlight glare) enter `pending_status` and only commit if a follow-up
    frame at `t >= pending_since + debounce_seconds` confirms the new state.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    def apply_observation(
        self,
        spot: Dict[str, Any],
        observed_status: str,  # FREE | OCCUPIED | BLOCKED
        observed_free_units: int,
        observed_occupied_units: int,
        confidence: float,
        reason: str,
        engine: str,
        debounce_seconds: float = 8.0,
        instant_confirm: bool = False,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        cur_status = spot["status"]
        cur_free = int(spot["free_units"])
        pending_status = spot.get("pending_status")
        pending_free = spot.get("pending_free_units")
        pending_since = spot.get("pending_since")

        is_same_as_current = (
            cur_status == observed_status
            and (spot["spot_type"] != "elastic_strip" or cur_free == observed_free_units)
        )

        # Case 1: Observation matches established state -> clear any transient pending state!
        if is_same_as_current:
            had_pending = pending_status is not None
            self.db.update_spot_state(
                spot["spot_id"],
                {
                    "pending_status": None,
                    "pending_free_units": None,
                    "pending_since": None,
                    "confidence": round(confidence, 3),
                    "last_reason": reason,
                    "last_engine": engine,
                    "last_seen_at": ts,
                },
            )
            return {
                "spot_id": spot["spot_id"],
                "transition": "REVERTED_TRANSIENT" if had_pending else "STABLE",
                "previous_status": cur_status,
                "committed_status": cur_status,
                "pending_status": None,
                "free_units": cur_free,
                "occupied_units": int(spot["occupied_units"]),
                "note": "复核恢复原状态（已过滤瞬态过路车干扰）" if had_pending else "状态稳定维持",
            }

        # Case 2: Instant confirm mode OR debounce_seconds <= 0 OR recovering from UNKNOWN
        if instant_confirm or debounce_seconds <= 0.01 or cur_status == "UNKNOWN":
            self.db.update_spot_state(
                spot["spot_id"],
                {
                    "status": observed_status,
                    "free_units": observed_free_units,
                    "occupied_units": observed_occupied_units,
                    "pending_status": None,
                    "pending_free_units": None,
                    "pending_since": None,
                    "confidence": round(confidence, 3),
                    "last_reason": reason,
                    "last_engine": engine,
                    "last_changed_at": ts,
                    "last_seen_at": ts,
                },
            )
            return {
                "spot_id": spot["spot_id"],
                "transition": "COMMITTED_IMMEDIATE",
                "previous_status": cur_status,
                "committed_status": observed_status,
                "pending_status": None,
                "free_units": observed_free_units,
                "occupied_units": observed_occupied_units,
                "note": f"状态直接更新: {cur_status} -> {observed_status}",
            }

        # Case 3: Matches existing pending state -> check if debounce window elapsed!
        is_same_pending = (
            pending_status == observed_status
            and (spot["spot_type"] != "elastic_strip" or pending_free == observed_free_units)
        )
        if is_same_pending and pending_since is not None:
            elapsed = ts - float(pending_since)
            if elapsed >= debounce_seconds:
                self.db.update_spot_state(
                    spot["spot_id"],
                    {
                        "status": observed_status,
                        "free_units": observed_free_units,
                        "occupied_units": observed_occupied_units,
                        "pending_status": None,
                        "pending_free_units": None,
                        "pending_since": None,
                        "confidence": round(confidence, 3),
                        "last_reason": reason,
                        "last_engine": engine,
                        "last_changed_at": ts,
                        "last_seen_at": ts,
                    },
                )
                return {
                    "spot_id": spot["spot_id"],
                    "transition": "COMMITTED_DEBOUNCED",
                    "previous_status": cur_status,
                    "committed_status": observed_status,
                    "pending_status": None,
                    "free_units": observed_free_units,
                    "occupied_units": observed_occupied_units,
                    "note": f"双帧防抖确认通过 ({elapsed:.1f}s >= {debounce_seconds:.1f}s): {cur_status} -> {observed_status}",
                }
            else:
                self.db.update_spot_state(
                    spot["spot_id"],
                    {
                        "last_seen_at": ts,
                        "last_reason": f"待复核({observed_status}): {reason}",
                        "last_engine": engine,
                    },
                )
                remain = max(0.0, debounce_seconds - elapsed)
                return {
                    "spot_id": spot["spot_id"],
                    "transition": "WAITING_DEBOUNCE",
                    "previous_status": cur_status,
                    "committed_status": cur_status,
                    "pending_status": observed_status,
                    "free_units": cur_free,
                    "occupied_units": int(spot["occupied_units"]),
                    "note": f"防抖观察中（还需 {remain:.1f}s 后复核帧确认）",
                }

        # Case 4: First frame showing a new state -> enter PENDING!
        self.db.update_spot_state(
            spot["spot_id"],
            {
                "pending_status": observed_status,
                "pending_free_units": observed_free_units,
                "pending_since": ts,
                "confidence": round(confidence, 3),
                "last_reason": f"待复核({observed_status}): {reason}",
                "last_engine": engine,
                "last_seen_at": ts,
            },
        )
        return {
            "spot_id": spot["spot_id"],
            "transition": "ENTERED_PENDING",
            "previous_status": cur_status,
            "committed_status": cur_status,
            "pending_status": observed_status,
            "free_units": cur_free,
            "occupied_units": int(spot["occupied_units"]),
            "note": f"首帧检测到变化({observed_status}: {reason})，进入 {debounce_seconds:.0f}s 防抖待确认态",
        }

    def check_offline_timeouts(
        self, offline_timeout_seconds: float = 900.0, now: Optional[float] = None
    ) -> int:
        ts = now if now is not None else time.time()
        marked = 0
        for sp in self.db.list_spots():
            last_seen = sp.get("last_seen_at")
            if last_seen is not None and (ts - float(last_seen)) > offline_timeout_seconds:
                if sp["status"] != "UNKNOWN":
                    self.db.update_spot_state(
                        sp["spot_id"],
                        {
                            "status": "UNKNOWN",
                            "free_units": 0,
                            "pending_status": None,
                            "pending_free_units": None,
                            "pending_since": None,
                            "last_reason": "相机超时离线熔断（暂停推荐）",
                            "last_changed_at": ts,
                        },
                    )
                    marked += 1
        return marked
