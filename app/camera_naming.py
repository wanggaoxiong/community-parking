import math
import re
from typing import Any, Dict, List, Optional, Tuple


SIDE_ALIASES: Dict[str, Tuple[str, str, str]] = {
    "L": ("L", "left", "左侧"),
    "LEFT": ("L", "left", "左侧"),
    "左": ("L", "left", "左侧"),
    "左侧": ("L", "left", "左侧"),
    "左边": ("L", "left", "左侧"),
    "R": ("R", "right", "右侧"),
    "RIGHT": ("R", "right", "右侧"),
    "右": ("R", "right", "右侧"),
    "右侧": ("R", "right", "右侧"),
    "右边": ("R", "right", "右侧"),
    "F": ("F", "front", "前面"),
    "FRONT": ("F", "front", "前面"),
    "前": ("F", "front", "前面"),
    "前面": ("F", "front", "前面"),
    "前侧": ("F", "front", "前面"),
    "B": ("B", "backend", "后面"),
    "BACKEND": ("B", "backend", "后面"),
    "BACK": ("B", "backend", "后面"),
    "REAR": ("B", "backend", "后面"),
    "后": ("B", "backend", "后面"),
    "后面": ("B", "backend", "后面"),
    "后侧": ("B", "backend", "后面"),
    "S": ("S", "side", "侧面"),
    "SIDE": ("S", "side", "侧面"),
    "侧": ("S", "side", "侧面"),
    "侧面": ("S", "side", "侧面"),
    "旁": ("S", "side", "侧面"),
    "E": ("E", "east", "东侧"),
    "EAST": ("E", "east", "东侧"),
    "东": ("E", "east", "东侧"),
    "东侧": ("E", "east", "东侧"),
    "W": ("W", "west", "西侧"),
    "WEST": ("W", "west", "西侧"),
    "西": ("W", "west", "西侧"),
    "西侧": ("W", "west", "西侧"),
    "N": ("N", "north", "北侧"),
    "NORTH": ("N", "north", "北侧"),
    "北": ("N", "north", "北侧"),
    "北侧": ("N", "north", "北侧"),
}

ORIENT_ALIASES: Dict[str, Tuple[str, str, str]] = {
    "V": ("V", "vertical", "竖着"),
    "VERTICAL": ("V", "vertical", "竖着"),
    "VERT": ("V", "vertical", "竖着"),
    "竖": ("V", "vertical", "竖着"),
    "竖着": ("V", "vertical", "竖着"),
    "竖排": ("V", "vertical", "竖着"),
    "纵向": ("V", "vertical", "竖着"),
    "H": ("H", "horizontal", "横着"),
    "HORIZONTAL": ("H", "horizontal", "横着"),
    "HORIZ": ("H", "horizontal", "横着"),
    "横": ("H", "horizontal", "横着"),
    "横着": ("H", "horizontal", "横着"),
    "横排": ("H", "horizontal", "横着"),
    "横向": ("H", "horizontal", "横着"),
    "顺停": ("H", "horizontal", "横着"),
}

VEHICLE_MODEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "sedan": {
        "model": "sedan",
        "name": "标准竖停轿车位",
        "map_w": 1.80,
        "map_h": 3.35,
        "orientation": "V",
        "compact_only": False,
        "spot_type": "standard",
    },
    "suv": {
        "model": "suv",
        "name": "SUV宽体车位",
        "map_w": 2.05,
        "map_h": 3.65,
        "orientation": "V",
        "compact_only": False,
        "spot_type": "standard",
    },
    "parallel": {
        "model": "parallel",
        "name": "横向顺停车位",
        "map_w": 3.75,
        "map_h": 1.85,
        "orientation": "H",
        "compact_only": False,
        "spot_type": "standard",
    },
    "compact": {
        "model": "compact",
        "name": "紧凑微型车位",
        "map_w": 1.55,
        "map_h": 2.85,
        "orientation": "V",
        "compact_only": True,
        "spot_type": "irregular",
    },
    "angled": {
        "model": "angled",
        "name": "45°斜列式车位",
        "map_w": 1.85,
        "map_h": 3.40,
        "orientation": "V",
        "angle_offset": 45.0,
        "compact_only": False,
        "spot_type": "standard",
    },
}

_SIDE_KEYS_SORTED = sorted(SIDE_ALIASES.keys(), key=len, reverse=True)
_ORIENT_KEYS_SORTED = sorted(ORIENT_ALIASES.keys(), key=len, reverse=True)

_TOKEN_REGEX = re.compile(
    r"^\s*(?:第)?(?P<bldg>[0-9A-Za-z]+?)(?:#|号楼|号|栋|楼)?[\s_\-]*"
    r"(?P<side>" + "|".join(re.escape(k) for k in _SIDE_KEYS_SORTED) + r")[\s_\-]*"
    r"(?P<orient>" + "|".join(re.escape(k) for k in _ORIENT_KEYS_SORTED) + r")[\s_\-]*"
    r"(?P<count>\d{1,2})(?:个|位|车位)?\s*$",
    re.IGNORECASE,
)


def parse_single_group_token(token: str) -> Optional[Dict[str, Any]]:
    raw = str(token or "").strip()
    if not raw:
        return None
    m = _TOKEN_REGEX.match(raw)
    if not m:
        return None

    bldg_raw = m.group("bldg").strip().upper()
    side_raw = m.group("side").strip().upper()
    orient_raw = m.group("orient").strip().upper()
    count = max(1, min(60, int(m.group("count"))))

    side_info = SIDE_ALIASES.get(side_raw) or SIDE_ALIASES.get(m.group("side").strip())
    orient_info = ORIENT_ALIASES.get(orient_raw) or ORIENT_ALIASES.get(m.group("orient").strip())
    if not side_info or not orient_info:
        return None

    side_code, side_en, side_cn = side_info
    orient_code, orient_en, orient_cn = orient_info
    code = f"{bldg_raw}{side_code}{orient_code}{count}"
    building_cn = f"{bldg_raw}号楼"
    default_model = "parallel" if orient_code == "H" else "sedan"
    preset = VEHICLE_MODEL_PRESETS[default_model]

    return {
        "code": code,
        "building": bldg_raw,
        "building_cn": building_cn,
        "side_code": side_code,
        "side_en": side_en,
        "side_cn": side_cn,
        "orient_code": orient_code,
        "orient_en": orient_en,
        "orient_cn": orient_cn,
        "count": count,
        "vehicle_model": default_model,
        "map_w": preset["map_w"],
        "map_h": preset["map_h"],
        "description": f"{building_cn}{side_cn}{orient_cn}{count}个停车位",
    }


def parse_camera_group_codes(
    expr: str, mount_address: str = ""
) -> Dict[str, Any]:
    """
    Parse one or multiple stall-group codes captured by a single camera.

    Examples:
      - "5LV7" + "3号楼10楼固定机位拍摄"
        -> "5号楼左侧竖着7个停车位（3号楼10楼固定机位拍摄）"
      - "10BH2"
        -> "10号楼后面横着2个停车位"
      - "10BV7+10BH2" + "3号楼10楼固定机位拍摄"
        -> "10号楼后面竖着7个+横着2个停车位（3号楼10楼固定机位拍摄）"
    """
    cleaned_expr = str(expr or "").strip()
    mount_clean = str(mount_address or "").strip().strip("()（）")

    raw_segments = [
        s.strip()
        for s in re.split(r"[+,，/;&]+", cleaned_expr)
        if s and s.strip()
    ]
    groups: List[Dict[str, Any]] = []
    invalid_tokens: List[str] = []

    for seg in raw_segments:
        parsed_seg = parse_single_group_token(seg)
        if parsed_seg:
            groups.append(parsed_seg)
            continue
        sub_parts = [p.strip() for p in re.split(r"\s+", seg) if p and p.strip()]
        if len(sub_parts) > 1:
            for sp in sub_parts:
                parsed_sub = parse_single_group_token(sp)
                if parsed_sub:
                    groups.append(parsed_sub)
                else:
                    invalid_tokens.append(sp)
        else:
            invalid_tokens.append(seg)

    normalized_code = "+".join(g["code"] for g in groups)
    total_spots = sum(int(g["count"]) for g in groups)

    # Format human-readable standard camera name
    if groups:
        first_prefix = f"{groups[0]['building_cn']}{groups[0]['side_cn']}"
        same_bldg_side = len(groups) > 1 and all(
            f"{g['building_cn']}{g['side_cn']}" == first_prefix for g in groups
        )
        if same_bldg_side:
            segs = [f"{g['orient_cn']}{g['count']}个" for g in groups]
            base_name = f"{first_prefix}{'+'.join(segs)}停车位"
        else:
            base_name = "+".join(g["description"] for g in groups)
        standard_name = f"{base_name}（{mount_clean}）" if mount_clean else base_name
        suggested_cam_id = "CAM_" + "_".join(g["code"] for g in groups)
        bldg_num = groups[0]["building"]
        suggested_zone_id = f"Z_BLDG{bldg_num}_{groups[0]['side_code']}"
        suggested_zone_name = f"{groups[0]['building_cn']}{groups[0]['side_cn']}停车区"
    else:
        standard_name = f"未命名机位（{mount_clean}）" if mount_clean else ""
        suggested_cam_id = ""
        suggested_zone_id = ""
        suggested_zone_name = ""

    return {
        "ok": len(groups) > 0 and len(invalid_tokens) == 0,
        "normalized_code": normalized_code,
        "mount_address": mount_clean,
        "groups": groups,
        "total_spots": total_spots,
        "standard_name": standard_name,
        "suggested_camera_id": suggested_cam_id,
        "suggested_zone_id": suggested_zone_id,
        "suggested_zone_name": suggested_zone_name,
        "invalid_tokens": invalid_tokens,
    }


parse_camera_group_code = parse_camera_group_codes


def _world_metrics(aspect_ratio: float) -> Tuple[float, float]:
    aspect = max(0.45, min(2.6, float(aspect_ratio or 1.45)))
    world_w = 100.0 * aspect if aspect >= 1.0 else 100.0
    world_h = 100.0 if aspect >= 1.0 else 100.0 / aspect
    return world_w, world_h


def generate_spots_along_route(
    route_points: List[List[float]],
    count: int = 5,
    side: str = "right",
    orientation: str = "V",
    vehicle_model: str = "sedan",
    start_ratio: float = 0.15,
    spacing_gap: float = 0.35,
    road_offset: float = 3.1,
    aspect_ratio: float = 1.45,
    group_code: str = "",
    camera_id: str = "UNASSIGNED",
    zone_id: str = "Z_MAP_CUSTOM",
    label_prefix: str = "",
    anchor_point: Optional[List[float]] = None,
    end_point: Optional[List[float]] = None,
    placement_mode: str = "route_snap",
    reverse_direction: bool = False,
    angle_offset_deg: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Place `count` vehicle/stall models at a user-designated map location or along a route:
    - If `anchor_point` + `end_point` are provided (dragged span A -> B on map): places `count`
      stalls directly along segment A -> B with exact visual alignment.
    - If `anchor_point` is provided with `placement_mode == "exact"`: places stall #1 directly at
      `anchor_point` and marches along the nearest route tangent (or horizontal if none).
    - If `anchor_point` is provided with `placement_mode == "route_snap"`: projects `anchor_point`
      onto `route_points` to determine exact `start_s` (and auto-detects left/right when `side="auto"`),
      eliminating any need for percentage guessing.
    - Otherwise falls back to `start_ratio` along `route_points`.
    """
    has_route = bool(route_points and len(route_points) >= 2)
    if not has_route:
        route_points = [[18.0, 82.0], [82.0, 82.0]]

    count = max(1, min(60, int(count)))
    orient = "H" if str(orientation or "").upper().startswith("H") else "V"
    v_model = str(vehicle_model or ("parallel" if orient == "H" else "sedan")).lower()
    if v_model not in VEHICLE_MODEL_PRESETS:
        v_model = "parallel" if orient == "H" else "sedan"
    preset = VEHICLE_MODEL_PRESETS[v_model]

    stall_w = float(preset["map_w"])
    stall_h = float(preset["map_h"])

    world_w, world_h = _world_metrics(aspect_ratio)
    pts_w = [
        ((float(p[0]) / 100.0) * world_w, (float(p[1]) / 100.0) * world_h)
        for p in route_points
    ]

    seg_lens: List[float] = []
    cum_lens: List[float] = [0.0]
    for i in range(len(pts_w) - 1):
        d = math.hypot(pts_w[i + 1][0] - pts_w[i][0], pts_w[i + 1][1] - pts_w[i][1])
        seg_lens.append(d)
        cum_lens.append(cum_lens[-1] + d)

    total_len = max(0.01, cum_lens[-1])

    # Step distance between adjacent stalls
    if orient == "H":
        long_dim = max(stall_w, stall_h)
        short_dim = min(stall_w, stall_h)
        step_world = long_dim + max(0.25, float(spacing_gap))
        lateral_dist = max(1.8, float(road_offset) * 0.72 + short_dim * 0.5)
    else:
        narrow_dim = min(stall_w, stall_h)
        deep_dim = max(stall_w, stall_h)
        step_world = narrow_dim + max(0.22, float(spacing_gap))
        lateral_dist = max(2.2, float(road_offset) * 0.68 + deep_dim * 0.5)

    def project_onto_route(wx: float, wy: float) -> Tuple[float, float, float, float, float, float]:
        best_dist = float("inf")
        best_s = 0.0
        best_tx, best_ty = 1.0, 0.0
        best_px, best_py = wx, wy
        for idx_s, s_len in enumerate(seg_lens):
            if s_len < 1e-6:
                continue
            x0, y0 = pts_w[idx_s]
            x1, y1 = pts_w[idx_s + 1]
            vx, vy = x1 - x0, y1 - y0
            t_proj = max(0.0, min(1.0, ((wx - x0) * vx + (wy - y0) * vy) / (s_len * s_len)))
            px_w = x0 + vx * t_proj
            py_w = y0 + vy * t_proj
            dist = math.hypot(wx - px_w, wy - py_w)
            if dist < best_dist:
                best_dist = dist
                best_s = cum_lens[idx_s] + t_proj * s_len
                best_tx, best_ty = vx / s_len, vy / s_len
                best_px, best_py = px_w, py_w
        return best_s, best_tx, best_ty, best_px, best_py, best_dist

    valid_anchor = (
        isinstance(anchor_point, (list, tuple))
        and len(anchor_point) >= 2
        and anchor_point[0] is not None
        and anchor_point[1] is not None
    )
    valid_end = (
        isinstance(end_point, (list, tuple))
        and len(end_point) >= 2
        and end_point[0] is not None
        and end_point[1] is not None
    )

    clean_grp = str(group_code or "").strip().upper()
    prefix = str(label_prefix or clean_grp or "").strip()
    mode_norm = str(placement_mode or "route_snap").strip().lower()
    extra_deg = float(angle_offset_deg or 0.0)

    def build_spot_dict(idx_zero: int, cx_w: float, cy_w: float, tx: float, ty: float, side_sign_local: float) -> Dict[str, Any]:
        cx_clamped = max(1.2, min(world_w - 1.2, cx_w))
        cy_clamped = max(1.2, min(world_h - 1.2, cy_w))
        tangent_deg = math.degrees(math.atan2(ty, tx))
        if orient == "H":
            if v_model == "parallel":
                angle_deg = tangent_deg + extra_deg
            else:
                angle_deg = tangent_deg + 90.0 + extra_deg
        else:
            nx_local = ty * side_sign_local
            ny_local = -tx * side_sign_local
            face_road_deg = math.degrees(math.atan2(-nx_local, ny_local))
            preset_off = float(preset.get("angle_offset", 0.0))
            if v_model == "angled":
                angle_deg = face_road_deg + (35.0 if side_sign_local < 0 else -35.0) + extra_deg
            else:
                angle_deg = face_road_deg + preset_off + extra_deg

        norm_angle = round(((angle_deg + 180.0) % 360.0) - 180.0, 1)
        seq_str = f"{idx_zero + 1:02d}"
        lbl = f"{prefix}-{seq_str}" if prefix else seq_str
        sid_base = clean_grp or (camera_id if camera_id != "UNASSIGNED" else "PLAN")
        return {
            "spot_id": f"{sid_base}_S{seq_str}",
            "camera_id": camera_id,
            "zone_id": zone_id,
            "group_code": clean_grp,
            "orientation": orient,
            "vehicle_model": v_model,
            "label": lbl,
            "spot_type": preset["spot_type"],
            "compact_only": bool(preset["compact_only"]),
            "capacity": 1,
            "status": "UNKNOWN" if camera_id == "UNASSIGNED" else "FREE",
            "map_x": round((cx_clamped / world_w) * 100.0, 2),
            "map_y": round((cy_clamped / world_h) * 100.0, 2),
            "map_w": stall_w,
            "map_h": stall_h,
            "map_angle": norm_angle,
            "map_custom": 1,
        }

    # Mode A: Direct two-point span (A -> B dragged directly on floor plan or situational map)
    if valid_anchor and valid_end:
        ax_w = (float(anchor_point[0]) / 100.0) * world_w
        ay_w = (float(anchor_point[1]) / 100.0) * world_h
        bx_w = (float(end_point[0]) / 100.0) * world_w
        by_w = (float(end_point[1]) / 100.0) * world_h
        span_dx = bx_w - ax_w
        span_dy = by_w - ay_w
        span_dist = math.hypot(span_dx, span_dy)
        if span_dist >= 0.8:
            step_tx, step_ty = span_dx / span_dist, span_dy / span_dist
            if count == 1:
                vec_deg = math.degrees(math.atan2(step_ty, step_tx))
                rev_off = 180.0 if reverse_direction else 0.0
                preset_off = float(preset.get("angle_offset", 0.0))
                raw_single_deg = (
                    vec_deg + rev_off + extra_deg
                    if v_model == "parallel"
                    else vec_deg + 90.0 + preset_off + rev_off + extra_deg
                )
                spot0 = build_spot_dict(0, ax_w, ay_w, step_tx, step_ty, -1.0)
                spot0["map_angle"] = round(((raw_single_deg + 180.0) % 360.0) - 180.0, 1)
                return [spot0]

            default_span = step_world * max(1, count - 1)
            eff_step = (
                span_dist / max(1, count - 1)
                if span_dist >= default_span * 0.42
                else step_world
            )
            # Determine which side the nearest road is on so car noses face the lane naturally
            side_sign_span = -1.0
            if has_route:
                _, _, _, rpx, rpy, rdist = project_onto_route((ax_w + bx_w) * 0.5, (ay_w + by_w) * 0.5)
                if rdist < 28.0:
                    mid_x, mid_y = (ax_w + bx_w) * 0.5 - rpx, (ay_w + by_w) * 0.5 - rpy
                    if mid_x * step_ty - mid_y * step_tx > 0:
                        side_sign_span = 1.0
            orient_tx, orient_ty = (-step_tx, -step_ty) if reverse_direction else (step_tx, step_ty)
            if reverse_direction:
                side_sign_span = -side_sign_span
            return [
                build_spot_dict(
                    i,
                    ax_w + step_tx * (i * eff_step),
                    ay_w + step_ty * (i * eff_step),
                    orient_tx,
                    orient_ty,
                    side_sign_span,
                )
                for i in range(count)
            ]

    # Mode B: Exact designated anchor point (click anywhere on floor plan; borrows nearest road angle)
    if valid_anchor and (mode_norm == "exact" or not has_route):
        ax_w = (float(anchor_point[0]) / 100.0) * world_w
        ay_w = (float(anchor_point[1]) / 100.0) * world_h
        tx, ty = 1.0, 0.0
        side_sign_exact = -1.0
        if has_route:
            _, rtx, rty, rpx, rpy, rdist = project_onto_route(ax_w, ay_w)
            if rdist < 32.0:
                tx, ty = rtx, rty
                if (ax_w - rpx) * ty - (ay_w - rpy) * tx > 0:
                    side_sign_exact = 1.0
        if reverse_direction:
            tx, ty = -tx, -ty
            side_sign_exact = -side_sign_exact
        return [
            build_spot_dict(i, ax_w + tx * (i * step_world), ay_w + ty * (i * step_world), tx, ty, side_sign_exact)
            for i in range(count)
        ]

    # Mode C: Route-snapped placement (either via designated anchor_point projected onto route, or legacy start_ratio)
    side_lower = str(side or "right").strip().lower()
    if valid_anchor:
        ax_w = (float(anchor_point[0]) / 100.0) * world_w
        ay_w = (float(anchor_point[1]) / 100.0) * world_h
        proj_s, ptx, pty, ppx, ppy, _ = project_onto_route(ax_w, ay_w)
        start_s = max(0.3, min(total_len - 0.3, proj_s))
        if side_lower == "auto":
            # Left normal in Y-downward screen coords is (pty, -ptx)
            dot_left = (ax_w - ppx) * pty + (ay_w - ppy) * (-ptx)
            side_sign = 1.0 if dot_left >= 0 else -1.0
        else:
            side_sign = 1.0 if side_lower == "left" else -1.0
    else:
        start_s = max(0.5, min(total_len - 0.5, float(start_ratio) * total_len))
        side_sign = 1.0 if side_lower == "left" else -1.0

    dir_mult = -1.0 if reverse_direction else 1.0
    if dir_mult > 0:
        needed_span = step_world * max(0, count - 1)
        if not valid_anchor and start_s + needed_span > total_len - 0.4:
            start_s = max(0.4, total_len - needed_span - 0.4)
        if count > 1 and start_s + needed_span > total_len:
            if valid_anchor:
                # Clamp step slightly only if overflowing route tail from designated anchor
                avail = max(1.2, total_len - start_s - 0.2)
                if avail < needed_span * 0.5:
                    start_s = max(0.3, total_len - needed_span - 0.3)
                else:
                    step_world = max(1.1, avail / max(1, count - 1))
            else:
                step_world = max(1.1, (total_len - 0.8) / max(1, count - 1))
                start_s = 0.4

    def sample_at_s(s_val: float) -> Tuple[float, float, float, float]:
        s_clamped = max(0.0, min(total_len, s_val))
        seg_i = 0
        for idx_s in range(len(seg_lens)):
            if cum_lens[idx_s + 1] >= s_clamped or idx_s == len(seg_lens) - 1:
                seg_i = idx_s
                break
        s_len = max(1e-6, seg_lens[seg_i])
        t_local = max(0.0, min(1.0, (s_clamped - cum_lens[seg_i]) / s_len))
        x0, y0 = pts_w[seg_i]
        x1, y1 = pts_w[seg_i + 1]
        tx = (x1 - x0) / s_len
        ty = (y1 - y0) / s_len
        return x0 + (x1 - x0) * t_local, y0 + (y1 - y0) * t_local, tx, ty

    spots_out: List[Dict[str, Any]] = []
    for i in range(count):
        bx, by, tx, ty = sample_at_s(start_s + dir_mult * i * step_world)
        nx_vec = ty * side_sign
        ny_vec = -tx * side_sign
        cx_w = bx + nx_vec * lateral_dist
        cy_w = by + ny_vec * lateral_dist
        eff_tx = tx * dir_mult
        eff_ty = ty * dir_mult
        eff_side_sign = side_sign * dir_mult
        spots_out.append(build_spot_dict(i, cx_w, cy_w, eff_tx, eff_ty, eff_side_sign))
    return spots_out


def extract_route_guidance_path(
    routes: List[Dict[str, Any]],
    gates: List[Dict[str, Any]],
    preferred_gate_id: str,
    target_x: float,
    target_y: float,
    roads: Optional[List[Dict[str, Any]]] = None,
    strict_entry_gate: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Compute the optimal directed driving route polyline from entrance gate to the recommended stall/zone
    across the vehicular road network (supporting one-way & two-way roads/routes, excluding walkways).
    Supports both single-gate loops and multi-gate (front/back gate) communities:
    - preferred_gate_id="auto" (or comma-separated / missing): picks whichever connected vehicular gate
      has the shortest valid forward driving path to the already-chosen stall.
    - strict_entry_gate=True with a connected vehicular gate: starts guidance from the gate the driver entered.
    """
    import heapq

    corridors: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for src_idx, rt in enumerate(routes or []):
        pts = rt.get("points") or []
        if len(pts) < 2:
            continue
        rid = str(rt.get("route_id") or rt.get("road_id") or f"RT_{src_idx + 1}")
        is_ow = (
            bool(rt.get("one_way"))
            if "one_way" in rt
            else (str(rt.get("direction") or "").strip().lower() == "oneway")
        )
        corridors.append(
            {
                "route_id": rid,
                "road_id": str(rt.get("road_id") or rid),
                "name": str(rt.get("name") or "推荐行车路线"),
                "gate_id": str(rt.get("gate_id") or ""),
                "one_way": is_ow,
                "points": [[float(p[0]), float(p[1])] for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2],
            }
        )
        seen_ids.add(rid)
        if rt.get("road_id"):
            seen_ids.add(str(rt.get("road_id")))

    for rd_idx, rd in enumerate(roads or []):
        if not isinstance(rd, dict):
            continue
        rd_id = str(rd.get("road_id") or f"R_{rd_idx + 1}")
        rt_alias = str(rd.get("route_id") or f"RT_{rd_id}")
        if rd_id in seen_ids or rt_alias in seen_ids:
            continue
        pts = rd.get("points") or []
        if len(pts) < 2:
            continue
        is_ow = (
            bool(rd.get("one_way"))
            if "one_way" in rd
            else (str(rd.get("direction") or "").strip().lower() == "oneway")
        )
        corridors.append(
            {
                "route_id": rt_alias,
                "road_id": rd_id,
                "name": str(rd.get("name") or "内部车行道"),
                "gate_id": str(rd.get("gate_id") or ""),
                "one_way": is_ow,
                "points": [[float(p[0]), float(p[1])] for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2],
            }
        )

    corridors = [c for c in corridors if len(c["points"]) >= 2]
    if not corridors:
        return None

    pref_gid = str(preferred_gate_id or "").strip()
    is_auto_gate = (not pref_gid) or (pref_gid.lower() == "auto") or ("," in pref_gid)
    explicit_gate_obj = (
        next((g for g in (gates or []) if str(g.get("gate_id") or "") == pref_gid), None)
        if not is_auto_gate
        else None
    )
    if explicit_gate_obj is None:
        is_auto_gate = True
    gate_obj = explicit_gate_obj or (gates[0] if gates else None)
    gx = float(gate_obj.get("x", 47.0)) if gate_obj else 47.0
    gy = float(gate_obj.get("y", 96.0)) if gate_obj else 96.0

    # Multi-corridor directed road graph (supports branches & one-way loops, strictly forbidding reverse flow)
    nodes_xy: List[Tuple[float, float]] = []
    adj: List[List[Tuple[int, float, int]]] = []  # (to_node, dist, corridor_idx)
    node_corrs: List[set[int]] = []
    dir_segs: List[Tuple[int, int, int, bool]] = []  # (u, v, corridor_idx, one_way)
    ow_start_nodes: set[int] = set()
    tw_endpoint_nodes: set[int] = set()

    def add_node(px: float, py: float) -> int:
        for idx_n, (ex, ey) in enumerate(nodes_xy):
            if math.hypot(px - ex, py - ey) <= 1.25:
                return idx_n
        idx_new = len(nodes_xy)
        nodes_xy.append((px, py))
        adj.append([])
        node_corrs.append(set())
        return idx_new

    entry_Bonus_nodes: Dict[int, float] = {}
    for c_idx, corr in enumerate(corridors):
        pts = corr["points"]
        is_ow = bool(corr["one_way"])
        start_nid = add_node(pts[0][0], pts[0][1])
        node_corrs[start_nid].add(c_idx)
        if is_ow:
            ow_start_nodes.add(start_nid)
        else:
            tw_endpoint_nodes.add(start_nid)
        if not is_auto_gate and corr.get("gate_id") and corr.get("gate_id") == pref_gid:
            entry_Bonus_nodes[start_nid] = -20.0
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            seg_len = math.hypot(bx - ax, by - ay)
            if seg_len <= 1e-5:
                continue
            steps = max(1, min(20, int(math.ceil(seg_len / 2.6))))
            prev_n = add_node(ax, ay)
            node_corrs[prev_n].add(c_idx)
            for s in range(1, steps + 1):
                t = s / float(steps)
                cur_n = add_node(ax + (bx - ax) * t, ay + (by - ay) * t)
                node_corrs[cur_n].add(c_idx)
                if cur_n != prev_n:
                    w = math.hypot(
                        nodes_xy[cur_n][0] - nodes_xy[prev_n][0],
                        nodes_xy[cur_n][1] - nodes_xy[prev_n][1],
                    )
                    adj[prev_n].append((cur_n, w, c_idx))
                    if not is_ow:
                        adj[cur_n].append((prev_n, w, c_idx))
                    dir_segs.append((prev_n, cur_n, c_idx, is_ow))
                prev_n = cur_n
        end_nid = add_node(pts[-1][0], pts[-1][1])
        node_corrs[end_nid].add(c_idx)
        if is_ow:
            if end_nid != start_nid:
                d_loop = math.hypot(
                    nodes_xy[end_nid][0] - nodes_xy[start_nid][0],
                    nodes_xy[end_nid][1] - nodes_xy[start_nid][1],
                )
                if d_loop <= 4.0:
                    # Closed one-way loop: allow forward wrap end -> start only (never backward)
                    adj[end_nid].append((start_nid, d_loop, c_idx))
        else:
            tw_endpoint_nodes.add(end_nid)
            if not is_auto_gate and corr.get("gate_id") and corr.get("gate_id") == pref_gid:
                entry_Bonus_nodes[end_nid] = -20.0

    # Connect nearby inter-corridor junctions (<= 3.6 map units) ONLY across distinct corridors
    n_nodes = len(nodes_xy)
    out_deg = [len(adj[i]) for i in range(n_nodes)]
    in_deg = [0] * n_nodes
    for u_i in range(n_nodes):
        for v_i, _, _ in adj[u_i]:
            in_deg[v_i] += 1

    def _can_feed_branch(nid_check: int) -> bool:
        return (
            in_deg[nid_check] > 0
            or nid_check in ow_start_nodes
            or nid_check in tw_endpoint_nodes
        )

    for i in range(n_nodes):
        ix, iy = nodes_xy[i]
        for j in range(i + 1, n_nodes):
            if node_corrs[i] & node_corrs[j]:
                continue
            dj = math.hypot(ix - nodes_xy[j][0], iy - nodes_xy[j][1])
            if 0.01 < dj <= 3.6:
                if _can_feed_branch(i) and out_deg[j] > 0:
                    adj[i].append((j, dj * 1.05, -1))
                if _can_feed_branch(j) and out_deg[i] > 0:
                    adj[j].append((i, dj * 1.05, -1))

    # Recompute in_deg after inter-corridor junction links
    in_deg = [0] * n_nodes
    for u_i in range(n_nodes):
        for v_i, _, _ in adj[u_i]:
            in_deg[v_i] += 1

    if n_nodes >= 2 and dir_segs:
        # Project target stall onto closest road segment
        best_target_seg = None
        best_target_d = 1e9
        for u_i, v_i, c_idx, is_ow in dir_segs:
            ux, uy = nodes_xy[u_i]
            vx, vy = nodes_xy[v_i]
            dx, dy = vx - ux, vy - uy
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 <= 1e-9 else max(0.0, min(1.0, ((target_x - ux) * dx + (target_y - uy) * dy) / l2))
            px_t, py_t = ux + t * dx, uy + t * dy
            d_t = math.hypot(target_x - px_t, target_y - py_t)
            if d_t < best_target_d:
                best_target_d = d_t
                best_target_seg = (
                    u_i,
                    v_i,
                    c_idx,
                    is_ow,
                    px_t,
                    py_t,
                    math.hypot(px_t - ux, py_t - uy),
                    math.hypot(px_t - vx, py_t - vy),
                )

        if best_target_seg is not None:
            tu, tv, tc_idx, t_ow, proj_x, proj_y, t_du, t_dv = best_target_seg
            gate_list = [
                (
                    float(g.get("x", gx)),
                    float(g.get("y", gy)),
                    str(g.get("gate_id") or ""),
                    str(g.get("name") or ""),
                )
                for g in (gates or [])
                if isinstance(g, dict)
            ] or [(gx, gy, preferred_gate_id, "")]

            # Legitimate vehicular entrances:
            # - One-way corridor start nodes (especially open heads where in_deg == 0)
            # - Two-way corridor endpoints or gate-adjacent two-way segments
            # - Gate-adjacent nodes on closed one-way loops (where all ow_start_nodes have in_deg > 0)
            all_ow_closed = bool(ow_start_nodes) and all(in_deg[sn] > 0 for sn in ow_start_nodes)
            primary_entries: List[int] = []
            for nid in range(n_nodes):
                if len(adj[nid]) == 0:
                    continue
                nx, ny = nodes_xy[nid]
                d_any_gate = min(math.hypot(ggx - nx, ggy - ny) for ggx, ggy, _, _ in gate_list)
                has_tw = any(not corridors[ci]["one_way"] for ci in node_corrs[nid])
                if (
                    nid in ow_start_nodes
                    or nid in tw_endpoint_nodes
                    or (has_tw and d_any_gate <= 8.0)
                    or (all_ow_closed and d_any_gate <= 7.5)
                ):
                    primary_entries.append(nid)

            connected_gates: List[Tuple[float, float, str, str]] = [
                g_item
                for g_item in gate_list
                if g_item[2]
                and any(
                    math.hypot(g_item[0] - nodes_xy[eid][0], g_item[1] - nodes_xy[eid][1]) <= 12.0
                    for eid in primary_entries
                )
            ]

            def _solve_from_entries(entry_ids: List[int], fallback_mode: bool = False) -> Tuple[List[float], List[int], List[int]]:
                d_arr = [1e12] * n_nodes
                p_arr = [-1] * n_nodes
                p_corr = [-1] * n_nodes
                heap: List[Tuple[float, int]] = []
                pref_connected = (not is_auto_gate) and any(
                    math.hypot(gx - nodes_xy[eid][0], gy - nodes_xy[eid][1]) <= 12.0
                    for eid in entry_ids
                )
                for nid in entry_ids:
                    if len(adj[nid]) == 0:
                        continue
                    nx, ny = nodes_xy[nid]
                    d_pref = math.hypot(gx - nx, gy - ny)
                    d_any = min(math.hypot(ggx - nx, ggy - ny) for ggx, ggy, _, _ in gate_list)
                    if fallback_mode:
                        init_cost = d_any * 1.45 + (18.0 if in_deg[nid] > 0 else 0.0)
                    elif is_auto_gate:
                        # Multi-gate neutral entry cost: naturally selects whichever connected vehicular gate
                        # has the shortest valid forward driving route to the already-chosen stall
                        init_cost = d_any * 1.25
                    elif pref_connected:
                        init_cost = d_pref * 1.35
                    else:
                        # Preferred gate is pedestrian-only / disconnected from vehicular ramps; use actual road gate entrance
                        init_cost = d_any * 1.25 + d_pref * 0.08
                    if nid in ow_start_nodes and in_deg[nid] == 0:
                        init_cost -= 4.0
                    if not is_auto_gate:
                        init_cost += entry_Bonus_nodes.get(nid, 0.0)
                    if init_cost < d_arr[nid]:
                        d_arr[nid] = init_cost
                        heapq.heappush(heap, (init_cost, nid))

                while heap:
                    d_u, u_i = heapq.heappop(heap)
                    if d_u > d_arr[u_i] + 1e-6:
                        continue
                    for v_i, w_uv, c_idx in adj[u_i]:
                        nd = d_u + w_uv
                        if nd + 1e-6 < d_arr[v_i]:
                            d_arr[v_i] = nd
                            p_arr[v_i] = u_i
                            p_corr[v_i] = c_idx
                            heapq.heappush(heap, (nd, v_i))
                return d_arr, p_arr, p_corr

            strict_entries = (
                [
                    eid
                    for eid in primary_entries
                    if math.hypot(gx - nodes_xy[eid][0], gy - nodes_xy[eid][1]) <= 12.0
                ]
                if (strict_entry_gate and not is_auto_gate)
                else []
            )
            if strict_entries:
                dist_arr, prev_arr, prev_corr = _solve_from_entries(strict_entries, fallback_mode=False)
                cand_tu = dist_arr[tu] + t_du
                cand_tv = (dist_arr[tv] + t_dv) if not t_ow else 1e12
                if min(cand_tu, cand_tv) >= 1e11:
                    dist_arr, prev_arr, prev_corr = _solve_from_entries(primary_entries, fallback_mode=False)
                    cand_tu = dist_arr[tu] + t_du
                    cand_tv = (dist_arr[tv] + t_dv) if not t_ow else 1e12
            else:
                dist_arr, prev_arr, prev_corr = _solve_from_entries(primary_entries, fallback_mode=False)
                cand_tu = dist_arr[tu] + t_du
                cand_tv = (dist_arr[tv] + t_dv) if not t_ow else 1e12
            if min(cand_tu, cand_tv) >= 1e11:
                all_out_nodes = [nid for nid in range(n_nodes) if len(adj[nid]) > 0]
                dist_arr, prev_arr, prev_corr = _solve_from_entries(all_out_nodes, fallback_mode=True)
                cand_tu = dist_arr[tu] + t_du
                cand_tv = (dist_arr[tv] + t_dv) if not t_ow else 1e12

            best_end_n = tu if cand_tu <= cand_tv else tv
            best_total = min(cand_tu, cand_tv)
            if best_total < 1e11:
                chain: List[int] = []
                corr_counts: Dict[int, int] = {}
                cur = best_end_n
                while cur != -1:
                    chain.append(cur)
                    ci = prev_corr[cur]
                    if ci >= 0:
                        corr_counts[ci] = corr_counts.get(ci, 0) + 1
                    cur = prev_arr[cur]
                chain.reverse()
                dom_c_idx = tc_idx
                if corr_counts:
                    dom_c_idx = max(corr_counts.items(), key=lambda kv: kv[1])[0]
                chosen_corr = corridors[dom_c_idx]
                sliced: List[List[float]] = []
                for nid in chain:
                    pt = [round(nodes_xy[nid][0], 2), round(nodes_xy[nid][1], 2)]
                    if not sliced or math.hypot(pt[0] - sliced[-1][0], pt[1] - sliced[-1][1]) >= 0.45:
                        sliced.append(pt)
                proj_pt = [round(proj_x, 2), round(proj_y, 2)]
                if not sliced or math.hypot(proj_pt[0] - sliced[-1][0], proj_pt[1] - sliced[-1][1]) >= 0.35:
                    sliced.append(proj_pt)
                if len(sliced) == 1:
                    alt_pt = [round(target_x, 2), round(target_y, 2)]
                    if math.hypot(alt_pt[0] - sliced[0][0], alt_pt[1] - sliced[0][1]) >= 0.25:
                        sliced.append(alt_pt)
                    else:
                        sliced.append([round(nodes_xy[tv][0], 2), round(nodes_xy[tv][1], 2)])

                entry_gate_id = ""
                entry_gate_name = ""
                if sliced:
                    sx0, sy0 = sliced[0]
                    nearest_g = min(
                        gate_list,
                        key=lambda item: math.hypot(item[0] - sx0, item[1] - sy0),
                    )
                    if math.hypot(nearest_g[0] - sx0, nearest_g[1] - sy0) <= 15.0:
                        entry_gate_id = nearest_g[2]
                        entry_gate_name = nearest_g[3]

                return {
                    "route_id": chosen_corr["route_id"],
                    "road_id": chosen_corr["road_id"],
                    "route_name": chosen_corr["name"],
                    "one_way": chosen_corr["one_way"],
                    "entry_gate_id": entry_gate_id,
                    "entry_gate_name": entry_gate_name,
                    "connected_vehicular_gates": [
                        {"gate_id": g_c[2], "name": g_c[3]} for g_c in connected_gates
                    ],
                    "points": sliced,
                    "waypoints": sliced,
                    "target_point": [round(target_x, 2), round(target_y, 2)],
                }

    return None
