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

    @staticmethod
    def compute_pessimistic_effective(
        committed_status: str,
        committed_free: int,
        spot_type: str,
        pending_status: Optional[str],
        pending_free: Optional[int],
    ) -> tuple[str, int]:
        """
        Pessimistic net-free rule ("宁少勿多，绝不虚报空位"):
        - FREE + pending(OCCUPIED / BLOCKED) immediately withholds free capacity.
        - OCCUPIED/BLOCKED + pending(FREE) keeps 0 free until debounce confirms.
        """
        if committed_status == "FREE":
            if spot_type == "elastic_strip":
                eff_free = int(committed_free)
                if pending_free is not None:
                    eff_free = min(eff_free, max(0, int(pending_free)))
                if pending_status == "BLOCKED":
                    return "BLOCKED", 0
                if eff_free <= 0 and pending_status == "OCCUPIED":
                    return "OCCUPIED", 0
                return ("FREE" if eff_free > 0 else "OCCUPIED"), eff_free
            if pending_status in ("OCCUPIED", "BLOCKED"):
                return pending_status, 0
            return "FREE", int(committed_free)
        return committed_status, 0

    @staticmethod
    def _normalize_obstacle_type(status: str, obstacle_type: Optional[str]) -> str:
        raw = str(obstacle_type or "").strip().lower()
        if status == "OCCUPIED":
            return "vehicle"
        if status == "BLOCKED":
            return raw if raw in ("immovable", "cone", "lock") else "immovable"
        if status == "FREE":
            if raw in ("animal", "pet", "cat", "dog", "person", "human"):
                return "pedestrian"
            if raw in ("bike", "bicycle", "ebike", "ball", "few_bricks"):
                return "movable"
            return raw if raw in ("none", "pedestrian", "movable") else "none"
        return "none"

    def apply_observation(
        self,
        spot: Dict[str, Any],
        observed_status: str,  # FREE | OCCUPIED | BLOCKED
        observed_free_units: int,
        observed_occupied_units: int,
        confidence: float,
        reason: str,
        engine: str,
        debounce_seconds: float = 3.0,
        instant_confirm: bool = False,
        now: Optional[float] = None,
        obstacle_type: str = "none",
        action_hint: str = "",
        persist_db: bool = True,
        motion_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        cur_status = spot["status"]
        cur_free = int(spot["free_units"])
        pending_status = spot.get("pending_status")
        pending_free = spot.get("pending_free_units")
        pending_since = spot.get("pending_since")
        if pending_since is not None and float(pending_since) > ts + 1.0:
            # Guard against simulated future timestamps wedging real-time debounce checks
            pending_since = ts
        norm_obs_type = self._normalize_obstacle_type(observed_status, obstacle_type)
        clean_hint = str(action_hint or "").strip()
        motion_label = str((motion_info or {}).get("intent_label") or "").strip()
        motion_suffix = f" · {motion_label}" if motion_label else ""

        def _commit_patch(fields: Dict[str, Any]) -> None:
            spot.update(fields)
            if persist_db:
                self.db.update_spot_state(spot["spot_id"], fields)

        is_same_as_current = (
            cur_status == observed_status
            and (spot["spot_type"] != "elastic_strip" or cur_free == observed_free_units)
        )

        # Case 1: Observation matches established state -> clear any transient pending state!
        # Also update obstacle_type & action_hint immediately (e.g. FREE empty -> FREE with movable ball/pedestrian).
        if is_same_as_current:
            had_pending = pending_status is not None
            _commit_patch(
                {
                    "pending_status": None,
                    "pending_free_units": None,
                    "pending_since": None,
                    "confidence": round(confidence, 3),
                    "last_reason": reason,
                    "last_engine": engine,
                    "last_seen_at": ts,
                    "obstacle_type": norm_obs_type,
                    "action_hint": clean_hint,
                }
            )
            return {
                "spot_id": spot["spot_id"],
                "transition": "REVERTED_TRANSIENT" if had_pending else "STABLE",
                "previous_status": cur_status,
                "committed_status": cur_status,
                "effective_status": cur_status,
                "pending_status": None,
                "free_units": cur_free,
                "effective_free_units": cur_free if cur_status == "FREE" else 0,
                "occupied_units": int(spot["occupied_units"]),
                "obstacle_type": norm_obs_type,
                "action_hint": clean_hint,
                "motion_intent": (motion_info or {}).get("intent_code", "NONE"),
                "note": (
                    f"复核恢复原状态（已过滤瞬态过路车干扰）{motion_suffix}"
                    if had_pending
                    else f"状态稳定维持{motion_suffix}"
                ),
            }

        # Case 2: Instant confirm mode OR debounce_seconds <= 0 OR recovering from UNKNOWN
        if instant_confirm or debounce_seconds <= 0.01 or cur_status == "UNKNOWN":
            _commit_patch(
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
                    "obstacle_type": norm_obs_type,
                    "action_hint": clean_hint,
                }
            )
            return {
                "spot_id": spot["spot_id"],
                "transition": "COMMITTED_IMMEDIATE",
                "previous_status": cur_status,
                "committed_status": observed_status,
                "effective_status": observed_status,
                "pending_status": None,
                "free_units": observed_free_units,
                "effective_free_units": observed_free_units if observed_status == "FREE" else 0,
                "occupied_units": observed_occupied_units,
                "obstacle_type": norm_obs_type,
                "action_hint": clean_hint,
                "note": f"状态直接更新: {cur_status} -> {observed_status}",
            }

        # Case 2b: Anchor-localized stationary position verification (`基于定位锚点的车位位置与无位移直确`)
        # When fixed ground anchors (A1~A4) localize the frame into canonical stall coordinates AND
        # spatial bay pose + cross-frame position check verify the vehicle is squarely parked inside
        # the stall (`PARKED_IN_SLOT`) or both bay & entrance mouth are clear (`CLEAR_EMPTY`) with zero
        # active movement, commit directly without stalling on artificial debounce review.
        if bool((motion_info or {}).get("position_verified_stationary")):
            pose_note = str((motion_info or {}).get("pose_label") or "定位框内无位移")
            _commit_patch(
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
                    "obstacle_type": norm_obs_type,
                    "action_hint": clean_hint,
                }
            )
            return {
                "spot_id": spot["spot_id"],
                "transition": "COMMITTED_POSITION_VERIFIED",
                "previous_status": cur_status,
                "committed_status": observed_status,
                "effective_status": observed_status,
                "pending_status": None,
                "free_units": observed_free_units,
                "effective_free_units": observed_free_units if observed_status == "FREE" else 0,
                "occupied_units": observed_occupied_units,
                "obstacle_type": norm_obs_type,
                "action_hint": clean_hint,
                "motion_intent": (motion_info or {}).get("intent_code", "NONE"),
                "localized_pose": (motion_info or {}).get("localized_pose", ""),
                "note": f"定位坐标确认无移动（{pose_note}）: {cur_status} -> {observed_status}{motion_suffix}",
            }

        # Case 3: Matches existing pending state -> check if debounce window elapsed!
        is_same_pending = (
            pending_status == observed_status
            and (spot["spot_type"] != "elastic_strip" or pending_free == observed_free_units)
        )
        if is_same_pending and pending_since is not None:
            elapsed = ts - float(pending_since)
            if elapsed >= debounce_seconds:
                _commit_patch(
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
                        "obstacle_type": norm_obs_type,
                        "action_hint": clean_hint,
                    }
                )
                return {
                    "spot_id": spot["spot_id"],
                    "transition": "COMMITTED_DEBOUNCED",
                    "previous_status": cur_status,
                    "committed_status": observed_status,
                    "effective_status": observed_status,
                    "pending_status": None,
                    "free_units": observed_free_units,
                    "effective_free_units": observed_free_units if observed_status == "FREE" else 0,
                    "occupied_units": observed_occupied_units,
                    "obstacle_type": norm_obs_type,
                    "action_hint": clean_hint,
                    "motion_intent": (motion_info or {}).get("intent_code", "NONE"),
                    "note": f"双帧防抖确认通过 ({elapsed:.1f}s >= {debounce_seconds:.1f}s): {cur_status} -> {observed_status}{motion_suffix}",
                }
            else:
                _commit_patch(
                    {
                        "pending_since": float(pending_since),
                        "last_seen_at": ts,
                        "last_reason": f"待复核({observed_status}): {reason}",
                        "last_engine": engine,
                        "action_hint": clean_hint or spot.get("action_hint", ""),
                    }
                )
                remain = max(0.0, debounce_seconds - elapsed)
                eff_st, eff_fu = self.compute_pessimistic_effective(
                    cur_status, cur_free, spot["spot_type"], observed_status, observed_free_units
                )
                return {
                    "spot_id": spot["spot_id"],
                    "transition": "WAITING_DEBOUNCE",
                    "previous_status": cur_status,
                    "committed_status": cur_status,
                    "effective_status": eff_st,
                    "pending_status": observed_status,
                    "free_units": cur_free,
                    "effective_free_units": eff_fu,
                    "occupied_units": int(spot["occupied_units"]),
                    "obstacle_type": norm_obs_type,
                    "action_hint": clean_hint,
                    "motion_intent": (motion_info or {}).get("intent_code", "NONE"),
                    "note": f"防抖观察中（已按悲观策略预扣/冻结空位，还需 {remain:.1f}s 复核确认{motion_suffix}）",
                }

        # Case 4: First frame showing a new state -> enter PENDING!
        _commit_patch(
            {
                "pending_status": observed_status,
                "pending_free_units": observed_free_units,
                "pending_since": ts,
                "confidence": round(confidence, 3),
                "last_reason": f"待复核({observed_status}): {reason}",
                "last_engine": engine,
                "last_seen_at": ts,
                "action_hint": clean_hint or spot.get("action_hint", ""),
            }
        )
        eff_st, eff_fu = self.compute_pessimistic_effective(
            cur_status, cur_free, spot["spot_type"], observed_status, observed_free_units
        )
        return {
            "spot_id": spot["spot_id"],
            "transition": "ENTERED_PENDING",
            "previous_status": cur_status,
            "committed_status": cur_status,
            "effective_status": eff_st,
            "pending_status": observed_status,
            "free_units": cur_free,
            "effective_free_units": eff_fu,
            "occupied_units": int(spot["occupied_units"]),
            "obstacle_type": norm_obs_type,
            "action_hint": clean_hint,
            "motion_intent": (motion_info or {}).get("intent_code", "NONE"),
            "note": f"首帧检测到变化({observed_status}: {reason})，进入 {debounce_seconds:.0f}s 防抖复核（净空位已立即防虚报调整{motion_suffix}）",
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
