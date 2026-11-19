import math
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.camera_naming import VEHICLE_MODEL_PRESETS, extract_route_guidance_path
from app.config import config_manager
from app.database import (
    Database,
    build_road_walk_graph,
    compute_walk_between_map_points,
    extract_building_info,
    natural_unit_sort_key,
)
from app.workspaces import (
    render_workspace_broadcast_text,
    render_workspace_display_card_text,
    render_workspace_repeat_core_text,
)


MAX_SOFT_LOCKS = 200


class ParkingRecommender:
    """
    Zone-based parking recommendation engine with In-Transit Soft Locks (TTL=40s).

    Cost Formula (lower is better):
        Cost(z, u) = walk_meters(z, u)
                   + dead_end_penalty(z)
                   + compact_suv_penalty(z, vehicle_type)
                   - 14.0 * min(effective_free_spots(z), 3)
    """

    def __init__(
        self,
        database: Database,
        profile_getter: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.db = database
        self._profile_getter = profile_getter
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
        smap = self.db.get_site_map(include_backdrop=False)
        aspect = max(0.35, min(3.0, float(smap.get("aspect_ratio") or 1.0)))
        vw = 100.0 * aspect if aspect >= 1.0 else 100.0
        vh = 100.0 if aspect >= 1.0 else 100.0 / aspect

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
        has_drawn_floorplan_spots = any(
            int(s.get("map_custom") or 0) == 1 for s in all_spots
        )

        summaries = []
        for z in zones:
            zid = z["zone_id"]
            z_spots = spots_by_zone.get(zid, [])
            z_cams = cams_by_zone.get(zid, [])

            raw_free = 0
            clean_free = 0
            movable_free = 0
            pedestrian_free = 0
            compact_free = 0
            occupied = 0
            blocked = 0
            pending_confirming = 0
            unknown = 0
            total_capacity = 0
            calibrated_spot_count = 0
            actionable_hints: List[Dict[str, str]] = []

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
                obs_type = str(sp.get("obstacle_type") or "none")
                act_hint = str(sp.get("action_hint") or "")

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
                        clean_free += eff_free_u
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
                            counted = False
                            if sp["compact_only"]:
                                compact_free += eff_free_u
                                if vehicle_type != "suv":
                                    raw_free += eff_free_u
                                    counted = True
                            else:
                                raw_free += eff_free_u
                                counted = True
                            if counted and eff_free_u > 0:
                                if obs_type == "movable":
                                    movable_free += eff_free_u
                                    actionable_hints.append(
                                        {
                                            "label": sp["label"],
                                            "obstacle_type": "movable",
                                            "hint": act_hint or sp["last_reason"] or "有可移轻物，移开即可停入",
                                        }
                                    )
                                elif obs_type == "pedestrian":
                                    pedestrian_free += eff_free_u
                                    actionable_hints.append(
                                        {
                                            "label": sp["label"],
                                            "obstacle_type": "pedestrian",
                                            "hint": act_hint or "临时有人驻留，车辆靠近会自动让开",
                                        }
                                    )
                                else:
                                    clean_free += eff_free_u
                            occupied += eff_occ_u
                elif st == "OCCUPIED":
                    eff_occ_u = max(1, int(sp["occupied_units"]))
                    occupied += eff_occ_u
                    if pend in ("FREE", "BLOCKED"):
                        pending_confirming += 1
                elif st == "BLOCKED":
                    blocked += 1
                    if act_hint:
                        actionable_hints.append(
                            {
                                "label": sp["label"],
                                "obstacle_type": "immovable",
                                "hint": act_hint,
                            }
                        )
                    if pend in ("FREE", "OCCUPIED"):
                        pending_confirming += 1
                else:
                    unknown += cap

                cam_obj = cams_by_id.get(sp["camera_id"])
                cam_display_name = (
                    cam_obj["name"]
                    if cam_obj
                    else ("待绑定机位" if sp["camera_id"] == "UNASSIGNED" else sp["camera_id"])
                )
                enriched_zone_spots.append(
                    {
                        "spot_id": sp["spot_id"],
                        "camera_id": sp["camera_id"],
                        "camera_name": cam_display_name,
                        "zone_id": zid,
                        "zone_name": z["name"],
                        "label": sp["label"],
                        "group_code": sp.get("group_code", ""),
                        "group_name": sp.get("group_name", ""),
                        "near_unit_id": sp.get("near_unit_id", ""),
                        "near_unit_custom": bool(sp.get("near_unit_custom")),
                        "vehicle_model": sp.get("vehicle_model", "sedan"),
                        "spot_type": sp["spot_type"],
                        "compact_only": sp["compact_only"],
                        "capacity": cap,
                        "status": st,
                        "effective_status": effective_status,
                        "pending_status": pend,
                        "obstacle_type": obs_type,
                        "action_hint": act_hint,
                        "free_units": eff_free_u,
                        "committed_free_units": sp["free_units"],
                        "occupied_units": eff_occ_u,
                        "total_meters": sp["total_meters"],
                        "last_reason": sp["last_reason"],
                        "is_calibrated": is_spot_calibrated,
                        "map_x": sp.get("map_x"),
                        "map_y": sp.get("map_y"),
                        "map_w": sp.get("map_w"),
                        "map_h": sp.get("map_h"),
                        "map_angle": sp.get("map_angle"),
                        "map_custom": bool(sp.get("map_custom")),
                        "polygon": sp["polygon"],
                    }
                )

            # Compute normalized 2D layout positions & individual world-map stall coordinates
            self._attach_spatial_layout(
                enriched_zone_spots,
                cams_by_id,
                z,
                vw,
                vh,
                suppress_unplaced=has_drawn_floorplan_spots,
            )

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
                            "group_code": c.get("group_code", ""),
                            "group_name": c.get("group_name", ""),
                            "mount_address": c.get("mount_address", ""),
                            "parsed_groups": c.get("parsed_groups", []),
                            "has_template": bool(c.get("has_template")),
                            "has_vehicle_template": bool(c.get("has_vehicle_template")),
                            "building_hint": (c.get("template_meta") or {}).get("building_hint", ""),
                        }
                        for c in z_cams
                    ],
                    "total_capacity": total_capacity,
                    "raw_free": raw_free,
                    "clean_free": clean_free,
                    "movable_free": movable_free,
                    "pedestrian_free": pedestrian_free,
                    "compact_free": compact_free,
                    "soft_locked": soft_locked,
                    "effective_free": effective_free,
                    "occupied": occupied,
                    "blocked": blocked,
                    "pending_confirming": pending_confirming,
                    "unknown": unknown,
                    "actionable_hints": actionable_hints,
                    "spots": enriched_zone_spots,
                }
            )
        return summaries

    @staticmethod
    def _attach_spatial_layout(
        zone_spots: List[Dict[str, Any]],
        cams_by_id: Dict[str, Dict[str, Any]],
        zone_dict: Optional[Dict[str, Any]] = None,
        vw: float = 100.0,
        vh: float = 100.0,
        suppress_unplaced: bool = False,
    ) -> None:
        """
        Projects each camera's calibrated stall polygons into normalized local
        row/slot coordinates AND computes realistic individual stall coordinates
        (`map_x`, `map_y`, `map_w`, `map_h`, `map_angle`) on the community site map
        without requiring giant zone wrapper boxes.
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
                pts.sort(key=lambda t: t[1])
                staged_rows.append([t[2] for t in pts])

        z_info = zone_dict or {}
        zx = float(z_info.get("map_x") or 40.0)
        zy = float(z_info.get("map_y") or 50.0)
        zw = float(z_info.get("map_w") or 18.0)
        zh = float(z_info.get("map_h") or 8.0)
        raw_angle = float(z_info.get("layout_angle") or 0.0)
        if zh > zw * 1.3 and abs(raw_angle) < 1.0:
            base_angle = 90.0
        else:
            base_angle = raw_angle

        zcx_iso = (zx + zw * 0.5) * vw / 100.0
        zcy_iso = (zy + zh * 0.5) * vh / 100.0
        rad = math.radians(base_angle)
        tx, ty = math.cos(rad), math.sin(rad)
        nx, ny = -ty, tx

        rows_total = max(1, len(staged_rows))
        for r_idx, row_spots in enumerate(staged_rows):
            cols_n = max(1, len(row_spots))
            # Precompute default isotropic slot widths along row tangent
            slot_dims: List[tuple[float, float, float]] = []
            for sp in row_spots:
                if sp.get("spot_type") == "elastic_strip":
                    cap = max(1, int(sp.get("capacity") or 2))
                    def_w = round(max(4.8, min(12.0, cap * 2.65)), 2)
                    def_h = 2.05
                    gap = 0.55
                else:
                    v_preset = VEHICLE_MODEL_PRESETS.get(str(sp.get("vehicle_model") or "sedan"))
                    def_w = float(v_preset["map_w"]) if v_preset else 1.75
                    def_h = float(v_preset["map_h"]) if v_preset else 3.25
                    gap = 0.28
                slot_dims.append((def_w, def_h, gap))

            total_row_span = sum(d[0] for d in slot_dims) + sum(
                d[2] for d in slot_dims[:-1]
            )
            cursor_t = -total_row_span * 0.5
            row_n_off = (r_idx - (rows_total - 1) * 0.5) * 4.35

            for c_idx, sp in enumerate(row_spots):
                def_w, def_h, gap = slot_dims[c_idx]
                t_center = cursor_t + def_w * 0.5
                cursor_t += def_w + gap

                sp["layout"] = {
                    "row_idx": global_row_offset + r_idx,
                    "col_idx": c_idx,
                    "rows_total": rows_total,
                    "cols_in_row": cols_n,
                    "rel_x": round((c_idx + 0.5) / cols_n, 4),
                    "rel_y": round((r_idx + 0.5) / rows_total, 4),
                }

                if sp.get("map_x") is not None and sp.get("map_y") is not None:
                    sp["map_placed"] = True
                    sp["map_x"] = round(float(sp["map_x"]), 2)
                    sp["map_y"] = round(float(sp["map_y"]), 2)
                    sp["map_w"] = round(float(sp["map_w"] or def_w), 2)
                    sp["map_h"] = round(float(sp["map_h"] or def_h), 2)
                    sp["map_angle"] = round(
                        float(sp["map_angle"]) if sp.get("map_angle") is not None else base_angle,
                        1,
                    )
                elif suppress_unplaced:
                    # User has already drawn floor-plan stall groups: never synthesize phantom
                    # diagonal stalls on roads for unbound cameras; surface camera for 1-to-1 binding instead.
                    sp["map_placed"] = False
                    sp["map_x"] = None
                    sp["map_y"] = None
                    sp["map_w"] = def_w
                    sp["map_h"] = def_h
                    sp["map_angle"] = round(base_angle, 1)
                else:
                    sp["map_placed"] = True
                    sx = max(2.0, min(vw - 2.0, zcx_iso + t_center * tx + row_n_off * nx))
                    sy = max(2.0, min(vh - 2.0, zcy_iso + t_center * ty + row_n_off * ny))
                    sp["map_x"] = round(sx * 100.0 / vw, 2)
                    sp["map_y"] = round(sy * 100.0 / vh, 2)
                    sp["map_w"] = def_w
                    sp["map_h"] = def_h
                    sp["map_angle"] = round(base_angle, 1)

                sp.pop("polygon", None)

    def build_spatial_governance(
        self,
        zone_summaries: List[Dict[str, Any]],
        units: Optional[List[Dict[str, Any]]] = None,
        cameras: Optional[List[Dict[str, Any]]] = None,
        site_map: Optional[Dict[str, Any]] = None,
        selected_unit_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Aggregates named stall groups (`spot_groups`), unbound calibrated cameras (`unbound_cameras`),
        and building-proximity clusters (`building_clusters`) with exact `🚪 单元门` road-walking paths.
        """
        units_list = units if units is not None else self.db.list_units()
        cams_list = cameras if cameras is not None else self.db.list_cameras()
        smap = site_map if site_map is not None else self.db.get_site_map(include_backdrop=False)
        span_m = float(
            (smap.get("meta") or {}).get("estimated_span_meters")
            or smap.get("estimated_span_meters")
            or 185.0
        )
        road_graph = build_road_walk_graph(smap, span_m)

        units_by_id = {u["unit_id"]: u for u in units_list}
        cams_by_id = {c["camera_id"]: c for c in cams_list}
        sel_uid = (
            selected_unit_id
            if selected_unit_id and selected_unit_id in units_by_id
            else (units_list[0]["unit_id"] if units_list else "")
        )

        grouped_spots: Dict[str, List[Dict[str, Any]]] = {}
        unplaced_by_cam: Dict[str, List[Dict[str, Any]]] = {}
        bound_cam_ids: set[str] = set()

        for zs in zone_summaries:
            for sp in zs.get("spots", []):
                cam_id = str(sp.get("camera_id") or "UNASSIGNED")
                if not sp.get("map_placed", True) or sp.get("map_x") is None or sp.get("map_y") is None:
                    if cam_id != "UNASSIGNED":
                        unplaced_by_cam.setdefault(cam_id, []).append(sp)
                    continue
                if cam_id != "UNASSIGNED":
                    bound_cam_ids.add(cam_id)
                fallback_gc = cam_id if cam_id.upper().startswith("CAM_") else f"CAM_{cam_id}"
                gc = str(sp.get("group_code") or "").strip() or fallback_gc
                grouped_spots.setdefault(gc, []).append(sp)

        active_locks = self.list_active_locks(now=now)
        group_lock_counts: Dict[str, int] = {}
        for lk in active_locks:
            lk_gc = str(lk.get("group_code") or "").strip()
            if lk_gc:
                group_lock_counts[lk_gc] = group_lock_counts.get(lk_gc, 0) + 1

        spot_groups: List[Dict[str, Any]] = []
        for gc, g_spots in grouped_spots.items():
            cx = round(sum(float(s["map_x"]) for s in g_spots) / len(g_spots), 2)
            cy = round(sum(float(s["map_y"]) for s in g_spots) / len(g_spots), 2)
            real_cams = [str(s["camera_id"]) for s in g_spots if str(s.get("camera_id") or "UNASSIGNED") != "UNASSIGNED"]
            cam_id = real_cams[0] if real_cams else "UNASSIGNED"
            cam_obj = cams_by_id.get(cam_id)
            cam_name = cam_obj["name"] if cam_obj else ("待绑定机位" if cam_id == "UNASSIGNED" else cam_id)

            near_uid = next(
                (str(s.get("near_unit_id") or "") for s in g_spots if str(s.get("near_unit_id") or "") in units_by_id),
                sel_uid,
            )
            near_u = units_by_id.get(near_uid)
            b_info = (
                extract_building_info(near_u["unit_id"], near_u["name"])
                if near_u
                else {"building_id": "OTHER", "building_name": "公共区域", "unit_no": "1"}
            )
            gname = next((str(s.get("group_name") or "").strip() for s in g_spots if str(s.get("group_name") or "").strip()), "") or gc

            total_cap = sum(int(s.get("capacity") or 1) for s in g_spots)
            raw_free_grp = 0
            clean_free = 0
            movable_free = 0
            ped_free = 0
            occ_cnt = 0
            blk_cnt = 0
            unk_cnt = 0
            pend_cnt = 0
            for s in g_spots:
                est = s.get("effective_status") or s.get("status")
                fu = int(s.get("free_units") or 0)
                if est == "FREE":
                    raw_free_grp += fu
                    obs = str(s.get("obstacle_type") or "none")
                    if obs == "movable":
                        movable_free += fu
                    elif obs == "pedestrian":
                        ped_free += fu
                    else:
                        clean_free += fu
                elif est == "OCCUPIED":
                    occ_cnt += max(1, int(s.get("occupied_units") or 1))
                elif est == "BLOCKED":
                    blk_cnt += 1
                else:
                    unk_cnt += int(s.get("capacity") or 1)
                if s.get("pending_status"):
                    pend_cnt += 1

            grp_soft_locked = group_lock_counts.get(gc, 0)
            eff_free = max(0, raw_free_grp - grp_soft_locked)

            door_distances: Dict[str, int] = {}
            selected_walk: Optional[Dict[str, Any]] = None
            target_walk_uid = sel_uid or near_uid
            for u in units_list:
                dx = float(u.get("door_x") if u.get("door_x") is not None else u["map_x"])
                dy = float(u.get("door_y") if u.get("door_y") is not None else u["map_y"])
                # Direction: parked spot group (cx, cy) -> walking home to unit door (dx, dy)
                w_res = compute_walk_between_map_points(cx, cy, dx, dy, road_graph=road_graph)
                door_distances[u["unit_id"]] = int(w_res["walk_meters"])
                if u["unit_id"] == target_walk_uid:
                    selected_walk = {
                        "unit_id": u["unit_id"],
                        "unit_name": u["name"],
                        "door_x": dx,
                        "door_y": dy,
                        "target_x": cx,
                        "target_y": cy,
                        "walk_meters": int(w_res["walk_meters"]),
                        "mode": w_res["mode"],
                        "path_points": w_res["path_points"],
                    }

            spot_groups.append(
                {
                    "group_code": gc,
                    "group_name": gname,
                    "zone_id": g_spots[0]["zone_id"],
                    "zone_name": g_spots[0]["zone_name"],
                    "camera_id": cam_id,
                    "camera_name": cam_name,
                    "is_bound": cam_id != "UNASSIGNED",
                    "is_calibrated": any(bool(s.get("is_calibrated")) for s in g_spots),
                    "map_placed": True,
                    "near_unit_id": near_uid,
                    "near_unit_name": near_u["name"] if near_u else "",
                    "near_unit_custom": any(bool(s.get("near_unit_custom")) for s in g_spots),
                    "building_id": b_info["building_id"],
                    "building_name": b_info["building_name"],
                    "center_x": cx,
                    "center_y": cy,
                    "spot_count": len(g_spots),
                    "total_capacity": total_cap,
                    "raw_free": raw_free_grp,
                    "soft_locked": grp_soft_locked,
                    "effective_free": eff_free,
                    "clean_free": clean_free,
                    "movable_free": movable_free,
                    "pedestrian_free": ped_free,
                    "occupied": occ_cnt,
                    "blocked": blk_cnt,
                    "unknown": unk_cnt,
                    "pending_confirming": pend_cnt,
                    "door_distances": door_distances,
                    "selected_door_walk": selected_walk,
                    "spots": g_spots,
                }
            )

        spot_groups.sort(
            key=lambda g: (
                natural_unit_sort_key(g["near_unit_id"] or "999-9"),
                0 if g["is_bound"] else 1,
                g["group_code"],
            )
        )

        # Build unbound_cameras list for 1-click 1-to-1 floor-plan group binding
        unassigned_groups = [g for g in spot_groups if not g["is_bound"]]
        unbound_cameras: List[Dict[str, Any]] = []
        for cam in cams_list:
            cid = cam["camera_id"]
            if cid == "UNASSIGNED":
                continue
            unplaced_list = unplaced_by_cam.get(cid, [])
            if cid in bound_cam_ids and not unplaced_list:
                continue
            unplaced_cnt = len(unplaced_list) or int(cam.get("spots_count") or 0)
            cam_text = f"{cid} {cam.get('group_code', '')} {cam.get('name', '')} {cam.get('mount_address', '')}".upper()
            bldg_m = re.search(r"(\d+)\s*(?:号楼|栋|#|L)", cam_text)
            cam_bldg = bldg_m.group(1) if bldg_m else ""

            candidate_pool = list(unassigned_groups)
            for sg in spot_groups:
                if sg["camera_id"] == cid and not sg.get("is_calibrated") and sg not in candidate_pool:
                    candidate_pool.append(sg)

            scored_cands = []
            for ug in candidate_pool:
                ugc = ug["group_code"].upper()
                sc = 0
                if ug["camera_id"] == cid:
                    sc += 150
                if ugc and ugc in cam_text:
                    sc += 100
                if unplaced_cnt > 0 and ug["spot_count"] == unplaced_cnt:
                    sc += 40
                elif unplaced_cnt > 0 and abs(ug["spot_count"] - unplaced_cnt) <= 1:
                    sc += 18
                if cam_bldg and ug["building_id"] == cam_bldg:
                    sc += 25
                scored_cands.append((sc, ug))
            scored_cands.sort(key=lambda item: (-item[0], item[1]["group_code"]))
            best_ug = scored_cands[0][1] if scored_cands and scored_cands[0][0] > 0 else (scored_cands[0][1] if scored_cands else None)

            unbound_cameras.append(
                {
                    "camera_id": cid,
                    "camera_name": cam["name"],
                    "zone_id": cam["zone_id"],
                    "has_template": bool(cam.get("has_template")),
                    "unplaced_spot_count": unplaced_cnt,
                    "effective_free": sum(int(s.get("free_units") or 0) for s in unplaced_list if s.get("effective_status") == "FREE"),
                    "occupied": sum(1 for s in unplaced_list if s.get("effective_status") == "OCCUPIED"),
                    "blocked": sum(1 for s in unplaced_list if s.get("effective_status") == "BLOCKED"),
                    "suggested_group_code": best_ug["group_code"] if best_ug else "",
                    "suggested_group_name": best_ug["group_name"] if best_ug else "",
                    "candidate_groups": [
                        {
                            "group_code": ug["group_code"],
                            "group_name": ug["group_name"],
                            "spot_count": ug["spot_count"],
                            "building_name": ug["building_name"],
                            "match_score": sc,
                        }
                        for sc, ug in scored_cands[:12]
                    ],
                }
            )

        # Build building_clusters (grouping e.g. 9-1 & 9-2 under 9号楼 with nearby stall groups)
        clusters_map: Dict[str, Dict[str, Any]] = {}
        for u in units_list:
            bid = u.get("building_id") or extract_building_info(u["unit_id"], u["name"])["building_id"]
            bname = u.get("building_name") or extract_building_info(u["unit_id"], u["name"])["building_name"]
            if bid not in clusters_map:
                clusters_map[bid] = {
                    "building_id": bid,
                    "building_name": bname,
                    "units": [],
                    "groups": [],
                    "unbound_cameras": [],
                }
            clusters_map[bid]["units"].append(
                {
                    "unit_id": u["unit_id"],
                    "name": u["name"],
                    "unit_no": u.get("unit_no") or extract_building_info(u["unit_id"], u["name"])["unit_no"],
                    "default_gate": u.get("default_gate", "south"),
                    "map_x": u["map_x"],
                    "map_y": u["map_y"],
                    "door_x": u.get("door_x", u["map_x"]),
                    "door_y": u.get("door_y", u["map_y"]),
                    "door_custom": int(u.get("door_custom") or 0),
                }
            )

        for grp in spot_groups:
            bid = grp["building_id"]
            if bid not in clusters_map:
                clusters_map[bid] = {
                    "building_id": bid,
                    "building_name": grp["building_name"],
                    "units": [],
                    "groups": [],
                    "unbound_cameras": [],
                }
            cl = clusters_map[bid]
            door_badges = [
                {
                    "unit_id": cu["unit_id"],
                    "unit_no": cu["unit_no"],
                    "label": f"{cu['unit_no']}单元门",
                    "walk_meters": grp["door_distances"].get(cu["unit_id"], 0),
                }
                for cu in cl["units"]
            ]
            cl["groups"].append({**grp, "unit_door_badges": door_badges})

        for ucam in unbound_cameras:
            cam_text = f"{ucam['camera_id']} {ucam['camera_name']}"
            bm = re.search(r"(\d+)\s*(?:号楼|栋|#|L)", cam_text, re.IGNORECASE)
            bid = bm.group(1) if (bm and bm.group(1) in clusters_map) else (next(iter(clusters_map.keys()), "OTHER"))
            if bid in clusters_map:
                clusters_map[bid]["unbound_cameras"].append(ucam)

        building_clusters: List[Dict[str, Any]] = []
        for bid, cl in clusters_map.items():
            grps = cl["groups"]
            building_clusters.append(
                {
                    **cl,
                    "unit_count": len(cl["units"]),
                    "group_count": len(grps),
                    "total_capacity": sum(g["total_capacity"] for g in grps),
                    "bound_capacity": sum(g["total_capacity"] for g in grps if g["is_bound"]),
                    "unassigned_capacity": sum(g["total_capacity"] for g in grps if not g["is_bound"]),
                    "effective_free": sum(g["effective_free"] for g in grps),
                    "occupied": sum(g["occupied"] for g in grps),
                    "blocked": sum(g["blocked"] for g in grps),
                    "unknown": sum(g["unknown"] for g in grps),
                }
            )
        building_clusters.sort(
            key=lambda c: natural_unit_sort_key(c["units"][0]["unit_id"] if c["units"] else f"{c['building_id']}-1")
        )

        return {
            "selected_unit_id": sel_uid,
            "spot_groups": spot_groups,
            "unbound_cameras": unbound_cameras,
            "building_clusters": building_clusters,
        }

    def recommend(
        self,
        unit_id: str,
        vehicle_type: str = "sedan",  # sedan | suv
        driver_tag: str = "业主车辆",
        acquire_lock: bool = True,
        entry_gate_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        cfg = config_manager.get()
        ttl = float(cfg.get("soft_lock_ttl_seconds", 40))

        units = {u["unit_id"]: u for u in self.db.list_units()}
        unit_info = units.get(unit_id) or next(iter(units.values()))
        dists = self.db.get_unit_distances(unit_info["unit_id"])
        zone_summaries = self.get_zone_summaries(vehicle_type=vehicle_type, now=ts)
        smap = self.db.get_site_map(include_backdrop=False)
        topo = smap.get("topology") or {}
        span_m = float(
            (smap.get("meta") or {}).get("estimated_span_meters")
            or smap.get("estimated_span_meters")
            or 185.0
        )
        road_graph = build_road_walk_graph(smap, span_m)
        u_door_x = float(unit_info.get("door_x") if unit_info.get("door_x") is not None else unit_info["map_x"])
        u_door_y = float(unit_info.get("door_y") if unit_info.get("door_y") is not None else unit_info["map_y"])

        # 核心选位准则：
        # 1) 无论车辆从哪个小区大门驶入（单门或前后多门），选位始终按「车位步行至楼栋单元门最近」排序，不受车行距离影响；
        # 2) 同一片区（zone_id）下若绘制/绑定了多条独立命名停车带（spot_group / group_code），按每条停车带独立核算净余空位、步行距离与备选播报，绝不跨带虚增空位数。
        active_locks = self.list_active_locks(now=ts)
        group_lock_counts: Dict[str, int] = {}
        unscoped_zone_locks: Dict[str, int] = {}
        for lk in active_locks:
            lk_gc = str(lk.get("group_code") or "").strip()
            lk_zid = str(lk.get("zone_id") or "").strip()
            if lk_gc:
                group_lock_counts[lk_gc] = group_lock_counts.get(lk_gc, 0) + 1
            elif lk_zid:
                unscoped_zone_locks[lk_zid] = unscoped_zone_locks.get(lk_zid, 0) + 1

        candidates = []
        best_walk_by_cand: Dict[str, Dict[str, Any]] = {}
        for zs in zone_summaries:
            placed_spots = [sp for sp in zs.get("spots", []) if sp.get("map_placed", True)]
            has_explicit_groups = any(str(sp.get("group_code") or "").strip() for sp in placed_spots)

            if has_explicit_groups:
                strip_buckets: Dict[str, List[Dict[str, Any]]] = {}
                for sp in placed_spots:
                    cid = str(sp.get("camera_id") or "UNASSIGNED")
                    fallback_gc = cid if cid.upper().startswith("CAM_") else f"CAM_{cid}"
                    gc = str(sp.get("group_code") or "").strip() or fallback_gc
                    strip_buckets.setdefault(gc, []).append(sp)

                rem_unscoped = unscoped_zone_locks.get(zs["zone_id"], 0)
                for gc, g_spots in strip_buckets.items():
                    b_raw_free = 0
                    b_clean_free = 0
                    b_movable_free = 0
                    b_ped_free = 0
                    b_compact_free = 0
                    b_free_hints: List[Dict[str, Any]] = []
                    eligible_free_spots: List[Dict[str, Any]] = []

                    for sp in g_spots:
                        if sp.get("effective_status") != "FREE":
                            continue
                        fu = int(sp.get("free_units") or 0)
                        if fu <= 0:
                            continue
                        is_compact = bool(sp.get("compact_only")) and sp.get("spot_type") != "elastic_strip"
                        if is_compact:
                            b_compact_free += fu
                            counted = vehicle_type != "suv"
                        else:
                            counted = True
                        if not counted:
                            continue
                        b_raw_free += fu
                        if sp.get("map_x") is not None and sp.get("map_y") is not None:
                            eligible_free_spots.append(sp)
                        obs = str(sp.get("obstacle_type") or "none")
                        if obs == "movable":
                            b_movable_free += fu
                            b_free_hints.append(
                                {
                                    "label": sp["label"],
                                    "obstacle_type": "movable",
                                    "hint": sp.get("action_hint")
                                    or sp.get("last_reason")
                                    or "有可移轻物，移开即可停入",
                                }
                            )
                        elif obs == "pedestrian":
                            b_ped_free += fu
                            b_free_hints.append(
                                {
                                    "label": sp["label"],
                                    "obstacle_type": "pedestrian",
                                    "hint": sp.get("action_hint") or "临时有人驻留，车辆靠近会自动让开",
                                }
                            )
                        else:
                            b_clean_free += fu

                    b_soft_locked = group_lock_counts.get(gc, 0)
                    if rem_unscoped > 0 and b_raw_free > b_soft_locked:
                        absorb = min(rem_unscoped, b_raw_free - b_soft_locked)
                        b_soft_locked += absorb
                        rem_unscoped -= absorb

                    eff = max(0, b_raw_free - b_soft_locked)
                    if eff <= 0:
                        continue

                    walk_m = float(dists.get(zs["zone_id"], 100.0))
                    best_sp_walk: Optional[Dict[str, Any]] = None
                    best_sp_obj: Optional[Dict[str, Any]] = None
                    for sp in eligible_free_spots:
                        w_res = compute_walk_between_map_points(
                            float(sp["map_x"]),
                            float(sp["map_y"]),
                            u_door_x,
                            u_door_y,
                            road_graph=road_graph,
                        )
                        if best_sp_walk is None or w_res["walk_meters"] < best_sp_walk["walk_meters"]:
                            best_sp_walk = w_res
                            best_sp_obj = sp

                    cand_key = f"{zs['zone_id']}::{gc}"
                    if best_sp_walk is not None and best_sp_obj is not None:
                        walk_m = float(best_sp_walk["walk_meters"])
                        best_walk_by_cand[cand_key] = {
                            "spot": best_sp_obj,
                            "walk": best_sp_walk,
                        }

                    dead_end = float(zs["dead_end_penalty"]) if eff == 1 else float(zs["dead_end_penalty"]) * 0.35
                    clearable_penalty = 0.0
                    if b_clean_free <= 0:
                        if b_ped_free > 0:
                            clearable_penalty = 3.0
                        elif b_movable_free > 0:
                            clearable_penalty = 12.0
                    abundance_bonus = 14.0 * min(eff, 3)
                    score = round(walk_m + dead_end + clearable_penalty - abundance_bonus, 1)

                    cam_fallback = next(
                        (
                            str(s.get("camera_name") or "").strip()
                            for s in g_spots
                            if str(s.get("camera_name") or "").strip()
                            not in ("", "待绑定机位", "UNASSIGNED")
                        ),
                        "",
                    )
                    gname = (
                        next(
                            (
                                str(s.get("group_name") or "").strip()
                                for s in g_spots
                                if str(s.get("group_name") or "").strip()
                            ),
                            "",
                        )
                        or cam_fallback
                        or (f"{zs['name']}({gc})" if len(strip_buckets) > 1 else zs["name"])
                    )
                    rec_slabel = str(best_sp_obj.get("label") or "") if best_sp_obj is not None else ""
                    candidates.append(
                        {
                            "zone_id": zs["zone_id"],
                            "zone_name": gname or zs["name"],
                            "raw_zone_name": zs["name"],
                            "group_code": gc,
                            "group_name": gname,
                            "spot_label": rec_slabel,
                            "walk_meters": int(round(walk_m)),
                            "raw_free": b_raw_free,
                            "clean_free": b_clean_free,
                            "movable_free": b_movable_free,
                            "pedestrian_free": b_ped_free,
                            "soft_locked": b_soft_locked,
                            "effective_free": eff,
                            "compact_free": b_compact_free,
                            "dead_end_penalty": round(dead_end, 1),
                            "score": score,
                            "description": zs["description"],
                            "actionable_hints": b_free_hints,
                        }
                    )
                continue

            eff = zs["effective_free"]
            if eff <= 0:
                continue
            walk_m = float(dists.get(zs["zone_id"], 100.0))
            placed_free_spots = [
                sp
                for sp in zs.get("spots", [])
                if sp.get("effective_status") == "FREE"
                and int(sp.get("free_units") or 0) > 0
                and (
                    vehicle_type != "suv"
                    or not bool(sp.get("compact_only"))
                    or sp.get("spot_type") == "elastic_strip"
                )
                and sp.get("map_placed", True)
                and sp.get("map_x") is not None
                and sp.get("map_y") is not None
            ]
            best_sp_walk = None
            best_sp_obj = None
            for sp in placed_free_spots:
                w_res = compute_walk_between_map_points(
                    float(sp["map_x"]),
                    float(sp["map_y"]),
                    u_door_x,
                    u_door_y,
                    road_graph=road_graph,
                )
                if best_sp_walk is None or w_res["walk_meters"] < best_sp_walk["walk_meters"]:
                    best_sp_walk = w_res
                    best_sp_obj = sp
            rec_gname = ""
            rec_gcode = ""
            rec_slabel = ""
            if best_sp_obj is not None:
                rec_gcode = str(best_sp_obj.get("group_code") or "")
                rec_gname = str(best_sp_obj.get("group_name") or "")
                rec_slabel = str(best_sp_obj.get("label") or "")
            if best_sp_walk is not None and best_sp_obj is not None:
                walk_m = float(best_sp_walk["walk_meters"])
                best_walk_by_cand[f"{zs['zone_id']}::{rec_gcode}"] = {
                    "spot": best_sp_obj,
                    "walk": best_sp_walk,
                }
            dead_end = float(zs["dead_end_penalty"]) if eff == 1 else float(zs["dead_end_penalty"]) * 0.35
            # Slight convenience preference for pure empty stalls over stalls requiring stepping out to move an item
            clearable_penalty = 0.0
            if zs.get("clean_free", 0) <= 0:
                if zs.get("pedestrian_free", 0) > 0:
                    clearable_penalty = 3.0
                elif zs.get("movable_free", 0) > 0:
                    clearable_penalty = 12.0
            abundance_bonus = 14.0 * min(eff, 3)
            score = round(walk_m + dead_end + clearable_penalty - abundance_bonus, 1)
            free_hints = [
                h for h in zs.get("actionable_hints", [])
                if h.get("obstacle_type") in ("movable", "pedestrian")
            ]
            display_zone_name = rec_gname or zs["name"]
            candidates.append(
                {
                    "zone_id": zs["zone_id"],
                    "zone_name": display_zone_name,
                    "raw_zone_name": zs["name"],
                    "group_code": rec_gcode,
                    "group_name": rec_gname,
                    "spot_label": rec_slabel,
                    "walk_meters": int(round(walk_m)),
                    "raw_free": zs["raw_free"],
                    "clean_free": zs.get("clean_free", 0),
                    "movable_free": zs.get("movable_free", 0),
                    "pedestrian_free": zs.get("pedestrian_free", 0),
                    "soft_locked": zs["soft_locked"],
                    "effective_free": eff,
                    "compact_free": zs["compact_free"],
                    "dead_end_penalty": round(dead_end, 1),
                    "score": score,
                    "description": zs["description"],
                    "actionable_hints": free_hints,
                }
            )

        candidates.sort(key=lambda c: c["score"])
        ws_profile = self._profile_getter() if self._profile_getter else None
        total_raw = sum(int(z["raw_free"]) for z in zone_summaries)
        total_eff = sum(int(z["effective_free"]) for z in zone_summaries)
        total_cap = sum(int(z["total_capacity"]) for z in zone_summaries)

        u_label = str(unit_info.get("name") or unit_info["unit_id"])
        if not candidates:
            voice = render_workspace_broadcast_text(
                ws_profile,
                has_recommendation=False,
                total_free=total_eff,
                total_raw_free=total_raw,
                total_spots=total_cap,
                unit_name=u_label,
                unit_id=str(unit_info["unit_id"]),
            )
            repeat_core = render_workspace_repeat_core_text(
                ws_profile,
                has_recommendation=False,
                total_raw_free=total_raw,
                unit_name=u_label,
            )
            display_card = render_workspace_display_card_text(
                ws_profile,
                has_recommendation=False,
                total_free=total_eff,
                total_raw_free=total_raw,
                unit_name=u_label,
            )
            return {
                "has_recommendation": False,
                "unit": unit_info,
                "vehicle_type": vehicle_type,
                "primary": None,
                "backup": None,
                "voice_text": voice,
                "repeat_voice_text": repeat_core,
                "display_text": display_card,
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
                "group_code": primary.get("group_code", ""),
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

        handling_note = ""
        p_hints = primary.get("actionable_hints") or []
        if p_hints:
            seen_hints: List[str] = []
            for h in p_hints:
                htxt = str(h.get("hint") or "").strip()
                if htxt and htxt not in seen_hints:
                    seen_hints.append(htxt)
                if len(seen_hints) >= 2:
                    break
            if seen_hints:
                if primary.get("clean_free", 0) == 0 or len(p_hints) == primary["effective_free"]:
                    handling_note = f"提示：空位处{'；'.join(seen_hints)}。"
                else:
                    handling_note = f"另有空位{seen_hints[0]}。"

        voice = render_workspace_broadcast_text(
            ws_profile,
            has_recommendation=True,
            total_free=total_eff,
            total_raw_free=total_raw,
            total_spots=total_cap,
            unit_name=u_label,
            unit_id=str(unit_info["unit_id"]),
            primary=primary,
            backup=backup,
            compact_note=compact_note,
            handling_note=handling_note,
        )
        repeat_core = render_workspace_repeat_core_text(
            ws_profile,
            has_recommendation=True,
            total_raw_free=total_raw,
            unit_name=u_label,
            primary=primary,
            backup=backup,
            compact_note=compact_note,
        )
        display_card = render_workspace_display_card_text(
            ws_profile,
            has_recommendation=True,
            total_free=total_eff,
            total_raw_free=total_raw,
            unit_name=u_label,
            primary=primary,
            backup=backup,
            handling_note=handling_note,
        )

        # Extract planned driving route polyline & exact unit-door walking path to the recommended stall
        primary_zs = next((z for z in zone_summaries if z["zone_id"] == primary["zone_id"]), None)
        target_x = float(primary_zs["map_x"] + primary_zs["map_w"] * 0.5) if primary_zs else 50.0
        target_y = float(primary_zs["map_y"] + primary_zs["map_h"] * 0.5) if primary_zs else 50.0
        door_walk_path: Optional[Dict[str, Any]] = None

        z_best = best_walk_by_cand.get(f"{primary['zone_id']}::{primary.get('group_code', '')}")
        if z_best is not None:
            sp_best = z_best["spot"]
            w_best = z_best["walk"]
            target_x = float(sp_best["map_x"])
            target_y = float(sp_best["map_y"])
            door_walk_path = {
                "unit_id": unit_info["unit_id"],
                "unit_name": unit_info["name"],
                "door_x": u_door_x,
                "door_y": u_door_y,
                "target_x": target_x,
                "target_y": target_y,
                "spot_label": sp_best.get("label", ""),
                "group_code": sp_best.get("group_code", ""),
                "group_name": sp_best.get("group_name", ""),
                "walk_meters": int(w_best["walk_meters"]),
                "mode": w_best["mode"],
                "path_points": w_best["path_points"],
            }
        else:
            w_fallback = compute_walk_between_map_points(
                target_x, target_y, u_door_x, u_door_y, road_graph=road_graph
            )
            door_walk_path = {
                "unit_id": unit_info["unit_id"],
                "unit_name": unit_info["name"],
                "door_x": u_door_x,
                "door_y": u_door_y,
                "target_x": round(target_x, 2),
                "target_y": round(target_y, 2),
                "spot_label": "",
                "group_code": "",
                "group_name": "",
                "walk_meters": int(primary["walk_meters"]),
                "mode": w_fallback["mode"],
                "path_points": w_fallback["path_points"],
            }

        req_gate = str(entry_gate_id or "").strip()
        strict_gate = bool(req_gate and req_gate.lower() != "auto")
        if req_gate:
            route_pref_gate = req_gate
        else:
            u_def_gate = str(unit_info.get("default_gate") or "").strip()
            valid_gate_ids = {
                str(g.get("gate_id") or "")
                for g in (topo.get("gates") or [])
                if isinstance(g, dict) and g.get("gate_id")
            }
            route_pref_gate = u_def_gate if (u_def_gate and u_def_gate in valid_gate_ids) else "auto"

        recommended_route = extract_route_guidance_path(
            routes=topo.get("routes") or [],
            gates=topo.get("gates") or [],
            preferred_gate_id=route_pref_gate,
            target_x=target_x,
            target_y=target_y,
            roads=topo.get("roads") or [],
            strict_entry_gate=strict_gate,
        )

        if recommended_route:
            conn_gates = recommended_route.get("connected_vehicular_gates") or []
            eg_name = str(recommended_route.get("entry_gate_name") or "").strip()
            if eg_name and len(conn_gates) >= 2:
                if strict_gate:
                    voice += f"已按{eg_name}进场规划顺向路线。"
                else:
                    voice += f"建议从{eg_name}驶入。"

        return {
            "has_recommendation": True,
            "unit": unit_info,
            "vehicle_type": vehicle_type,
            "entry_gate_id": req_gate or "auto",
            "primary": primary,
            "backup": backup,
            "recommended_route": recommended_route,
            "door_walk_path": door_walk_path,
            "voice_text": voice,
            "repeat_voice_text": repeat_core,
            "display_text": display_card,
            "lock_created": lock_created,
            "all_candidates": candidates,
        }
