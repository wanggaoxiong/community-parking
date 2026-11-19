import datetime
import heapq
import json
import math
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import DB_PATH


DEFAULT_CALIB_SCHEDULE: Dict[str, Any] = {
    "enabled": True,
    "mode": "nightly",  # "nightly" | "interval"
    "interval_hours": 12.0,
    "nightly_time": "02:30",
    "night_time": "02:30",
    "auto_compensate": True,
    "auto_compensate_shift": True,
    "max_auto_shift_px": 26.0,
}


def compute_next_calib_check_ts(
    schedule: Optional[Dict[str, Any]], now_ts: Optional[float] = None
) -> Optional[float]:
    cfg = {**DEFAULT_CALIB_SCHEDULE, **(schedule or {})}
    if not cfg.get("enabled", True):
        return None
    base_ts = float(now_ts if now_ts is not None else time.time())
    mode = str(cfg.get("mode") or "nightly").strip().lower()
    if mode == "interval":
        hours = max(0.05, min(168.0, float(cfg.get("interval_hours") or 12.0)))
        return round(base_ts + hours * 3600.0, 2)

    raw_hm = str(cfg.get("night_time") or cfg.get("nightly_time") or "02:30").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw_hm)
    hh = max(0, min(23, int(m.group(1)))) if m else 2
    mm = max(0, min(59, int(m.group(2)))) if m else 30
    now_dt = datetime.datetime.fromtimestamp(base_ts)
    target_dt = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target_dt.timestamp() <= base_ts + 5.0:
        target_dt = target_dt + datetime.timedelta(days=1)
    return round(target_dt.timestamp(), 2)


def natural_unit_sort_key(unit_id: str) -> Tuple[int, int, int, str]:
    raw = str(unit_id or "").strip()
    m = re.match(r"^([A-Za-z]*)(\d+)(?:[-#单元栋楼_]+(\d+))?$", raw)
    if m:
        prefix = m.group(1).upper()
        b_num = int(m.group(2))
        u_num = int(m.group(3)) if m.group(3) is not None else 1
        p_rank = 0 if not prefix else 1
        return (p_rank, b_num, u_num, prefix + raw)
    return (2, 999999, 999999, raw)


def extract_building_info(unit_id: str, unit_name: str = "") -> Dict[str, Any]:
    raw = str(unit_id or "").strip()
    m = re.match(r"^([A-Za-z]*\d+)(?:[-#](\d+))?$", raw)
    if m:
        b_id = m.group(1).upper()
        u_no = int(m.group(2)) if m.group(2) is not None else 1
        b_name = f"{b_id}号楼" if b_id.isdigit() else f"{b_id}栋"
        num_part = int(re.sub(r"\D", "", b_id) or "9999")
        b_sort = (0 if b_id.isdigit() else 1, num_part, b_id)
        return {
            "building_id": b_id,
            "building_name": b_name,
            "unit_no": u_no,
            "sort_key": b_sort,
        }
    m_cn = re.search(r"([A-Za-z]*\d+)\s*[号栋楼]", str(unit_name or ""))
    if m_cn:
        b_id = m_cn.group(1).upper()
        b_name = f"{b_id}号楼" if b_id.isdigit() else f"{b_id}栋"
        num_part = int(re.sub(r"\D", "", b_id) or "9999")
        return {
            "building_id": b_id,
            "building_name": b_name,
            "unit_no": 1,
            "sort_key": (0 if b_id.isdigit() else 1, num_part, b_id),
        }
    clean = raw or "OTHER"
    return {
        "building_id": clean,
        "building_name": str(unit_name or clean),
        "unit_no": 1,
        "sort_key": (2, 9999, clean),
    }


def format_unit_display_name(unit_id: str, custom_name: str = "") -> str:
    uid = str(unit_id or "").strip()
    cname = str(custom_name or "").strip()
    m = re.match(r"^([A-Za-z]*\d+)-(\d+)$", uid)
    if m:
        b_part, u_part = m.group(1).upper(), int(m.group(2))
        b_disp = f"{b_part}号楼" if b_part.isdigit() else f"{b_part}栋"
        std_name = f"{b_disp}{u_part}单元"
        if not cname or cname in (uid, f"{uid}栋", f"{uid}号楼", f"{uid}单元"):
            return std_name
        return cname
    m_single = re.match(r"^([A-Za-z]*\d+)$", uid)
    if m_single:
        b_part = m_single.group(1).upper()
        std_name = f"{b_part}号楼" if b_part.isdigit() else f"{b_part}栋"
        if not cname or cname == uid:
            return std_name
        return cname
    return cname or uid


def normalize_unit_id_and_name(raw_id: str, raw_name: str = "") -> Tuple[str, str]:
    s_id = str(raw_id or "").strip()
    s_name = str(raw_name or "").strip()
    probe = s_id or s_name
    if not probe:
        raise ValueError("单元编号不能为空（示例：9-2 或 9号楼2单元）")

    # Parse patterns like "9号楼2单元", "9栋2单元", "9#2", "9-2栋", "9-2"
    m_full = re.match(
        r"^([A-Za-z]*\d+)\s*(?:号楼|栋|楼|#|-|_)\s*(\d+)\s*(?:单元|栋|号楼)?$",
        probe,
    )
    if m_full:
        uid = f"{m_full.group(1).upper()}-{int(m_full.group(2))}"
        return uid, format_unit_display_name(uid, s_name)

    m_bldg_only = re.match(r"^([A-Za-z]*\d+)\s*(?:号楼|栋|楼)?$", probe)
    if m_bldg_only:
        b_code = m_bldg_only.group(1).upper()
        # Check if raw_name carries unit number e.g. "2单元" or "9号楼2单元"
        m_u = re.search(r"(\d+)\s*单元", s_name)
        u_num = int(m_u.group(1)) if m_u else 1
        uid = f"{b_code}-{u_num}"
        return uid, format_unit_display_name(uid, s_name)

    uid = re.sub(r"\s+", "", s_id or s_name)[:32]
    return uid, format_unit_display_name(uid, s_name)


def _clean_polyline_points(pts_raw: Any) -> List[Tuple[float, float]]:
    if not isinstance(pts_raw, list) or len(pts_raw) < 2:
        return []
    pts: List[Tuple[float, float]] = []
    for p in pts_raw:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            pts.append((float(p[0]), float(p[1])))
        elif isinstance(p, dict) and "x" in p and "y" in p:
            pts.append((float(p["x"]), float(p["y"])))
    return pts if len(pts) >= 2 else []


def _extract_road_polylines(topology: Optional[Dict[str, Any]]) -> List[List[Tuple[float, float]]]:
    if not isinstance(topology, dict):
        return []
    polylines: List[List[Tuple[float, float]]] = []
    for r in topology.get("roads") or []:
        pts = _clean_polyline_points(r.get("points") if isinstance(r, dict) else r)
        if pts:
            polylines.append(pts)
    if not polylines:
        for rt in topology.get("routes") or []:
            pts = _clean_polyline_points(rt.get("points") if isinstance(rt, dict) else None)
            if pts:
                polylines.append(pts)
    return polylines


def _extract_walkable_polylines(
    topology: Optional[Dict[str, Any]],
) -> List[Tuple[str, List[Tuple[float, float]]]]:
    """Extract all walkable polylines (roads, vehicle routes, and walkways).

    Pedestrians always traverse roads and vehicle routes bidirectionally, even when
    a road/route is configured as ``one_way=True`` for vehicles.
    """
    if not isinstance(topology, dict):
        return []
    out: List[Tuple[str, List[Tuple[float, float]]]] = []
    seen_road_keys = set()
    for r in topology.get("roads") or []:
        pts = _clean_polyline_points(r.get("points") if isinstance(r, dict) else r)
        if pts:
            key = tuple((round(px, 2), round(py, 2)) for px, py in pts)
            if key not in seen_road_keys:
                seen_road_keys.add(key)
                out.append(("road", pts))
    for rt in topology.get("routes") or []:
        pts = _clean_polyline_points(rt.get("points") if isinstance(rt, dict) else None)
        if pts:
            key = tuple((round(px, 2), round(py, 2)) for px, py in pts)
            if key not in seen_road_keys:
                seen_road_keys.add(key)
                out.append(("road", pts))
    for w in topology.get("walkways") or []:
        pts = _clean_polyline_points(w.get("points") if isinstance(w, dict) else w)
        if pts:
            out.append(("walkway", pts))
    return out


def _closest_point_on_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> Tuple[float, float, float]:
    vx, vy = bx - ax, by - ay
    seg_len2 = vx * vx + vy * vy
    if seg_len2 <= 1e-9:
        return ax, ay, math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / seg_len2))
    cx = ax + t * vx
    cy = ay + t * vy
    return cx, cy, math.hypot(px - cx, py - cy)


def _segment_intersection_params(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> Optional[Tuple[float, float]]:
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denom = rx * sy - ry * sx
    if abs(denom) <= 1e-9:
        return None
    qpx, qpy = cx - ax, cy - ay
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if 0.001 < t < 0.999 and 0.001 < u < 0.999:
        return (t, u)
    return None


def _polyline_mean_dist(
    pts_a: List[Tuple[float, float]], pts_b: List[Tuple[float, float]]
) -> float:
    if len(pts_a) < 2 or len(pts_b) < 2:
        return 1e9
    dists: List[float] = []
    for px, py in pts_a:
        best = 1e9
        for i in range(len(pts_b) - 1):
            _, _, d = _closest_point_on_segment(
                px, py, pts_b[i][0], pts_b[i][1], pts_b[i + 1][0], pts_b[i + 1][1]
            )
            if d < best:
                best = d
        dists.append(best)
    return sum(dists) / len(dists) if dists else 1e9


def normalize_site_map_topology(
    topology: Optional[Dict[str, Any]],
    sync_routes_from_roads: bool = False,
) -> Dict[str, Any]:
    topo = dict(topology or {})
    gates = list(topo.get("gates") or [])
    greens = list(topo.get("greens") or [])
    raw_roads = topo.get("roads") if isinstance(topo.get("roads"), list) else []
    raw_routes = topo.get("routes") if isinstance(topo.get("routes"), list) else []
    raw_walkways = topo.get("walkways") if isinstance(topo.get("walkways"), list) else []

    norm_routes: List[Dict[str, Any]] = []
    for idx, rt in enumerate(raw_routes):
        if not isinstance(rt, dict):
            continue
        pts = _clean_polyline_points(rt.get("points"))
        if not pts:
            continue
        is_ow = (
            bool(rt.get("one_way"))
            if "one_way" in rt
            else (str(rt.get("direction") or "").strip().lower() == "oneway")
        )
        rt_copy = dict(rt)
        rt_copy["route_id"] = str(rt.get("route_id") or f"RT_{idx + 1:02d}")
        rt_copy["name"] = str(rt.get("name") or f"行车路线 #{idx + 1}")
        rt_copy["one_way"] = is_ow
        rt_copy["direction"] = "oneway" if is_ow else "twoway"
        rt_copy["points"] = [[round(p[0], 2), round(p[1], 2)] for p in pts]
        norm_routes.append(rt_copy)

    matched_route_indices: set[int] = set()
    norm_roads: List[Dict[str, Any]] = []
    for idx, rd in enumerate(raw_roads):
        pts = _clean_polyline_points(rd.get("points") if isinstance(rd, dict) else rd)
        if not pts:
            continue
        rd_dict = dict(rd) if isinstance(rd, dict) else {}
        has_explicit_dir = ("one_way" in rd_dict) or ("direction" in rd_dict)
        is_ow = (
            bool(rd_dict.get("one_way"))
            if "one_way" in rd_dict
            else (str(rd_dict.get("direction") or "").strip().lower() == "oneway")
        )
        best_rt_i: Optional[int] = None
        best_rt_dist = 6.5
        for rt_i, rt_obj in enumerate(norm_routes):
            rt_pts = [(p[0], p[1]) for p in rt_obj["points"]]
            md = 0.5 * (_polyline_mean_dist(pts, rt_pts) + _polyline_mean_dist(rt_pts, pts))
            if md < best_rt_dist:
                best_rt_dist = md
                best_rt_i = rt_i
        if best_rt_i is not None:
            matched_route_indices.add(best_rt_i)
            matched_rt = norm_routes[best_rt_i]
            if not has_explicit_dir:
                is_ow = bool(matched_rt["one_way"])
                rt_pts = matched_rt["points"]
                d_same = math.hypot(pts[0][0] - rt_pts[0][0], pts[0][1] - rt_pts[0][1]) + math.hypot(
                    pts[-1][0] - rt_pts[-1][0], pts[-1][1] - rt_pts[-1][1]
                )
                d_rev = math.hypot(pts[0][0] - rt_pts[-1][0], pts[0][1] - rt_pts[-1][1]) + math.hypot(
                    pts[-1][0] - rt_pts[0][0], pts[-1][1] - rt_pts[0][1]
                )
                if d_rev + 2.0 < d_same:
                    pts = list(reversed(pts))
            if not rd_dict.get("gate_id") and matched_rt.get("gate_id"):
                rd_dict["gate_id"] = matched_rt["gate_id"]
            if not rd_dict.get("route_id") and matched_rt.get("route_id"):
                rd_dict["route_id"] = matched_rt["route_id"]

        rid = str(rd_dict.get("road_id") or f"R_{idx + 1:02d}")
        norm_roads.append(
            {
                **rd_dict,
                "road_id": rid,
                "name": str(rd_dict.get("name") or f"车行道 #{idx + 1}"),
                "width": max(2.2, min(14.0, float(rd_dict.get("width") or 4.8))),
                "one_way": is_ow,
                "direction": "oneway" if is_ow else "twoway",
                "points": [[round(p[0], 2), round(p[1], 2)] for p in pts],
            }
        )

    norm_walkways: List[Dict[str, Any]] = []
    for idx, wk in enumerate(raw_walkways):
        pts = _clean_polyline_points(wk.get("points") if isinstance(wk, dict) else wk)
        if not pts:
            continue
        wk_dict = dict(wk) if isinstance(wk, dict) else {}
        wid = str(wk_dict.get("walkway_id") or f"W_{idx + 1:02d}")
        norm_walkways.append(
            {
                **wk_dict,
                "walkway_id": wid,
                "name": str(wk_dict.get("name") or f"人行步道 #{idx + 1}"),
                "width": max(1.0, min(6.0, float(wk_dict.get("width") or 1.8))),
                "points": [[round(p[0], 2), round(p[1], 2)] for p in pts],
            }
        )

    if sync_routes_from_roads:
        final_routes = [
            {
                "route_id": str(r.get("route_id") or f"RT_{r['road_id']}"),
                "road_id": r["road_id"],
                "name": r["name"],
                "gate_id": str(r.get("gate_id") or ""),
                "one_way": bool(r["one_way"]),
                "direction": r["direction"],
                "points": r["points"],
            }
            for r in norm_roads
        ]
    else:
        # Preserve non-overlapping standalone routes (e.g. legacy API routes), and replace
        # overlapping duplicates with synced road entries so roads + routes stay consistent
        final_routes = [
            rt for rt_i, rt in enumerate(norm_routes) if rt_i not in matched_route_indices
        ]
        existing_rids = {str(rt.get("route_id") or "") for rt in final_routes}
        for r in norm_roads:
            cand_rt_id = str(r.get("route_id") or f"RT_{r['road_id']}")
            if cand_rt_id not in existing_rids:
                final_routes.append(
                    {
                        "route_id": cand_rt_id,
                        "road_id": r["road_id"],
                        "name": r["name"],
                        "gate_id": str(r.get("gate_id") or ""),
                        "one_way": bool(r["one_way"]),
                        "direction": r["direction"],
                        "points": r["points"],
                    }
                )
                existing_rids.add(cand_rt_id)

    topo["gates"] = gates
    topo["greens"] = greens
    topo["roads"] = norm_roads
    topo["walkways"] = norm_walkways
    topo["routes"] = final_routes
    return topo


def resolve_unit_door_xy(
    unit_row: Dict[str, Any], topology: Optional[Dict[str, Any]] = None
) -> Tuple[float, float]:
    ux = float(unit_row.get("map_x") if unit_row.get("map_x") is not None else 50.0)
    uy = float(unit_row.get("map_y") if unit_row.get("map_y") is not None else 50.0)
    uw = max(4.0, float(unit_row.get("map_w") or 8.0))
    uh = max(3.0, float(unit_row.get("map_h") or 4.2))
    if (
        int(unit_row.get("door_custom") or 0) == 1
        and unit_row.get("door_x") is not None
        and unit_row.get("door_y") is not None
    ):
        return (
            round(max(1.0, min(99.0, float(unit_row["door_x"]))), 2),
            round(max(1.0, min(99.0, float(unit_row["door_y"]))), 2),
        )

    walkables = _extract_walkable_polylines(topology)
    best_pt: Optional[Tuple[float, float]] = None
    best_d = 1e9
    for _, poly in walkables:
        for i in range(len(poly) - 1):
            cx, cy, d = _closest_point_on_segment(
                ux, uy, poly[i][0], poly[i][1], poly[i + 1][0], poly[i + 1][1]
            )
            if d < best_d:
                best_d = d
                best_pt = (cx, cy)

    if best_pt is not None and best_d > 0.25:
        vx = best_pt[0] - ux
        vy = best_pt[1] - uy
        half_w = uw * 0.52
        half_h = uh * 0.56
        scale_x = half_w / max(abs(vx), 1e-4)
        scale_y = half_h / max(abs(vy), 1e-4)
        edge_t = min(scale_x, scale_y, 0.85)
        dx = round(max(1.0, min(99.0, ux + vx * edge_t)), 2)
        dy = round(max(1.0, min(99.0, uy + vy * edge_t)), 2)
        return dx, dy

    return (
        round(max(1.0, min(99.0, ux)), 2),
        round(max(1.0, min(99.0, uy + uh * 0.55)), 2),
    )


def build_road_walk_graph(
    smap: Optional[Dict[str, Any]] = None,
    span_meters: Optional[float] = None,
) -> Dict[str, Any]:
    smap = smap or {}
    if span_meters is None:
        span_meters = float(
            (smap.get("meta") or {}).get("estimated_span_meters")
            or smap.get("estimated_span_meters")
            or 185.0
        )
    span_m = max(60.0, min(600.0, float(span_meters)))
    aspect = max(0.35, min(3.0, float(smap.get("aspect_ratio") or 1.0)))
    vw = 100.0 * aspect if aspect >= 1.0 else 100.0
    vh = 100.0 if aspect >= 1.0 else 100.0 / aspect
    m_per_iso = span_m / max(vw, vh)

    def to_m(px: float, py: float) -> Tuple[float, float]:
        return (px * vw / 100.0 * m_per_iso, py * vh / 100.0 * m_per_iso)

    nodes_map: List[Tuple[float, float]] = []
    nodes_m: List[Tuple[float, float]] = []
    adj: List[List[Tuple[int, float, str]]] = []
    edges: List[Tuple[int, int, str]] = []

    walkables = _extract_walkable_polylines(smap.get("topology"))
    if walkables:
        raw_segs: List[Tuple[float, float, float, float, str]] = []
        for kind, poly in walkables:
            for i in range(len(poly) - 1):
                ax, ay = poly[i]
                bx, by = poly[i + 1]
                if math.hypot(bx - ax, by - ay) > 1e-4:
                    raw_segs.append((ax, ay, bx, by, kind))

        split_params: List[List[float]] = [[0.0, 1.0] for _ in raw_segs]
        n_segs = len(raw_segs)
        for i in range(n_segs):
            ax, ay, bx, by, _ = raw_segs[i]
            amx, amy = to_m(ax, ay)
            bmx, bmy = to_m(bx, by)
            for j in range(i + 1, n_segs):
                cx, cy, dx, dy, _ = raw_segs[j]
                cross = _segment_intersection_params(ax, ay, bx, by, cx, cy, dx, dy)
                if cross is not None:
                    split_params[i].append(cross[0])
                    split_params[j].append(cross[1])
            # Also project every polyline vertex onto nearby interior segments (2F T-junctions)
            vx_m, vy_m = bmx - amx, bmy - amy
            l2_m = vx_m * vx_m + vy_m * vy_m
            if l2_m > 1e-6:
                for j in range(n_segs):
                    if i == j:
                        continue
                    cx, cy, dx, dy, _ = raw_segs[j]
                    for qx, qy in ((cx, cy), (dx, dy)):
                        qmx, qmy = to_m(qx, qy)
                        t_proj = ((qmx - amx) * vx_m + (qmy - amy) * vy_m) / l2_m
                        if 0.02 < t_proj < 0.98:
                            pmx = amx + t_proj * vx_m
                            pmy = amy + t_proj * vy_m
                            if math.hypot(qmx - pmx, qmy - pmy) <= 4.2:
                                split_params[i].append(t_proj)

        def add_node(px: float, py: float) -> int:
            pmx, pmy = to_m(px, py)
            for idx_n, (emx, emy) in enumerate(nodes_m):
                if abs(pmx - emx) <= 1.35 and abs(pmy - emy) <= 1.35:
                    if math.hypot(pmx - emx, pmy - emy) <= 1.35:
                        return idx_n
            idx_new = len(nodes_map)
            nodes_map.append((px, py))
            nodes_m.append((pmx, pmy))
            adj.append([])
            return idx_new

        def add_edge(u_i: int, v_i: int, w_m: float, kind: str) -> None:
            if u_i == v_i:
                return
            adj[u_i].append((v_i, w_m, kind))
            adj[v_i].append((u_i, w_m, kind))
            edges.append((u_i, v_i, kind))

        for seg_idx, (ax, ay, bx, by, kind) in enumerate(raw_segs):
            ts_sorted = sorted(set(round(t_val, 5) for t_val in split_params[seg_idx]))
            for k in range(len(ts_sorted) - 1):
                t0, t1 = ts_sorted[k], ts_sorted[k + 1]
                if t1 - t0 <= 1e-5:
                    continue
                sx0, sy0 = ax + (bx - ax) * t0, ay + (by - ay) * t0
                sx1, sy1 = ax + (bx - ax) * t1, ay + (by - ay) * t1
                sm0 = to_m(sx0, sy0)
                sm1 = to_m(sx1, sy1)
                sub_m = math.hypot(sm1[0] - sm0[0], sm1[1] - sm0[1])
                steps = max(1, min(20, int(math.ceil(sub_m / 3.2))))
                prev_idx = add_node(sx0, sy0)
                for s in range(1, steps + 1):
                    ts = s / float(steps)
                    nx = sx0 + (sx1 - sx0) * ts
                    ny = sy0 + (sy1 - sy0) * ts
                    cur_idx = add_node(nx, ny)
                    w_step = math.hypot(
                        nodes_m[cur_idx][0] - nodes_m[prev_idx][0],
                        nodes_m[cur_idx][1] - nodes_m[prev_idx][1],
                    )
                    add_edge(prev_idx, cur_idx, w_step, kind)
                    prev_idx = cur_idx

        n_total = len(nodes_m)
        for i in range(n_total):
            ix, iy = nodes_m[i]
            for j in range(i + 1, n_total):
                jx, jy = nodes_m[j]
                if abs(ix - jx) <= 4.2 and abs(iy - jy) <= 4.2:
                    dj = math.hypot(ix - jx, iy - jy)
                    if dj <= 4.2:
                        adj[i].append((j, dj * 1.03, "junction"))
                        adj[j].append((i, dj * 1.03, "junction"))

    return {
        "vw": vw,
        "vh": vh,
        "m_per_iso": m_per_iso,
        "nodes_map": nodes_map,
        "nodes_m": nodes_m,
        "adj": adj,
        "edges": edges,
    }


def compute_walk_between_map_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    smap: Optional[Dict[str, Any]] = None,
    span_meters: Optional[float] = None,
    road_graph: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    g = road_graph if road_graph is not None else build_road_walk_graph(smap=smap, span_meters=span_meters)
    vw = float(g["vw"])
    vh = float(g["vh"])
    m_per_iso = float(g["m_per_iso"])
    mx0 = x0 * vw / 100.0 * m_per_iso
    my0 = y0 * vh / 100.0 * m_per_iso
    mx1 = x1 * vw / 100.0 * m_per_iso
    my1 = y1 * vh / 100.0 * m_per_iso
    d_direct = math.hypot(mx1 - mx0, my1 - my0)
    d_manhattan = abs(mx1 - mx0) + abs(my1 - my0)

    if d_direct <= 8.5:
        walk_short = round(max(3.0, d_direct * 1.06), 1)
        return {
            "walk_meters": walk_short,
            "direct_meters": round(d_direct, 1),
            "mode": "direct_courtyard",
            "path_points": [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)]],
        }

    nodes_map = g["nodes_map"]
    nodes_m = g["nodes_m"]
    adj = g["adj"]
    edges = g.get("edges") or []
    n_total = len(nodes_m)
    if n_total >= 2 and edges:
        def project_to_edges(qx: float, qy: float, qmx: float, qmy: float) -> Tuple[int, int, str, float, float, float, float, float, float, float]:
            best_e = None
            best_d = 1e12
            for e_idx, (u_i, v_i, kind) in enumerate(edges):
                umx, umy = nodes_m[u_i]
                vmx, vmy = nodes_m[v_i]
                vx, vy = vmx - umx, vmy - umy
                l2 = vx * vx + vy * vy
                t = 0.0 if l2 <= 1e-9 else max(0.0, min(1.0, ((qmx - umx) * vx + (qmy - umy) * vy) / l2))
                pmx, pmy = umx + t * vx, umy + t * vy
                d = math.hypot(qmx - pmx, qmy - pmy)
                if d < best_d:
                    best_d = d
                    ux_map, uy_map = nodes_map[u_i]
                    vx_map, vy_map = nodes_map[v_i]
                    px_map = ux_map + t * (vx_map - ux_map)
                    py_map = uy_map + t * (vy_map - uy_map)
                    du = math.hypot(pmx - umx, pmy - umy)
                    dv = math.hypot(pmx - vmx, pmy - vmy)
                    best_e = (e_idx, u_i, v_i, kind, best_d, px_map, py_map, pmx, pmy, du, dv)
            assert best_e is not None
            return best_e  # type: ignore[return-value]

        se_idx, su, sv, skind, d_acc_s, sx_map, sy_map, smx, smy, s_du, s_dv = project_to_edges(x0, y0, mx0, my0)
        ee_idx, eu, ev, ekind, d_acc_e, ex_map, ey_map, emx, emy, e_du, e_dv = project_to_edges(x1, y1, mx1, my1)

        if se_idx == ee_idx:
            d_same_seg = math.hypot(emx - smx, emy - smy)
            total_same = d_acc_s + d_same_seg + d_acc_e
            if (d_acc_s + d_acc_e) <= max(42.0, d_direct * 1.05):
                pts_same: List[List[float]] = [[round(x0, 2), round(y0, 2)]]
                for px_c, py_c in ((sx_map, sy_map), (ex_map, ey_map), (x1, y1)):
                    rpt = [round(px_c, 2), round(py_c, 2)]
                    if math.hypot(rpt[0] - pts_same[-1][0], rpt[1] - pts_same[-1][1]) >= 0.45:
                        pts_same.append(rpt)
                pts_same[-1] = [round(x1, 2), round(y1, 2)]
                return {
                    "walk_meters": round(max(4.0, total_same), 1),
                    "direct_meters": round(d_direct, 1),
                    "mode": "walkway_network" if skind == "walkway" else "road_network",
                    "path_points": pts_same,
                }

        dist_arr = [1e12] * n_total
        prev_arr = [-1] * n_total
        prev_kind = [""] * n_total
        pq: List[Tuple[float, int]] = []
        for start_n, d_extra in ((su, s_du), (sv, s_dv)):
            init_d = d_acc_s + d_extra
            if init_d < dist_arr[start_n]:
                dist_arr[start_n] = init_d
                prev_kind[start_n] = skind
                heapq.heappush(pq, (init_d, start_n))

        while pq:
            d_u, u_i = heapq.heappop(pq)
            if d_u > dist_arr[u_i] + 1e-6:
                continue
            if d_u > dist_arr[eu] + e_du + d_acc_e and d_u > dist_arr[ev] + e_dv + d_acc_e:
                break
            for v_tuple in adj[u_i]:
                v_i, w_uv = v_tuple[0], v_tuple[1]
                e_kind = v_tuple[2] if len(v_tuple) > 2 else "road"
                nd = d_u + w_uv
                if nd + 1e-6 < dist_arr[v_i]:
                    dist_arr[v_i] = nd
                    prev_arr[v_i] = u_i
                    prev_kind[v_i] = e_kind
                    heapq.heappush(pq, (nd, v_i))

        cand_u = dist_arr[eu] + e_du + d_acc_e
        cand_v = dist_arr[ev] + e_dv + d_acc_e
        if cand_u <= cand_v:
            best_end_n = eu
            total_road_walk = cand_u
        else:
            best_end_n = ev
            total_road_walk = cand_v

        if (
            total_road_walk < 1e11
            and (d_acc_s + d_acc_e) <= max(42.0, d_direct * 1.05)
            and total_road_walk <= max(d_direct * 4.8, d_direct + 95.0)
        ):
            chain: List[int] = []
            used_walkway = skind == "walkway" or ekind == "walkway"
            cur = best_end_n
            while cur != -1:
                chain.append(cur)
                if prev_kind[cur] == "walkway":
                    used_walkway = True
                cur = prev_arr[cur]
            chain.reverse()
            pts_out: List[List[float]] = [[round(x0, 2), round(y0, 2)]]
            if d_acc_s >= 0.45:
                s_proj_pt = [round(sx_map, 2), round(sy_map, 2)]
                if math.hypot(s_proj_pt[0] - pts_out[-1][0], s_proj_pt[1] - pts_out[-1][1]) >= 0.45:
                    pts_out.append(s_proj_pt)
            for nid in chain:
                rx, ry = round(nodes_map[nid][0], 2), round(nodes_map[nid][1], 2)
                if math.hypot(rx - pts_out[-1][0], ry - pts_out[-1][1]) >= 0.55:
                    pts_out.append([rx, ry])
            if d_acc_e >= 0.45:
                e_proj_pt = [round(ex_map, 2), round(ey_map, 2)]
                if math.hypot(e_proj_pt[0] - pts_out[-1][0], e_proj_pt[1] - pts_out[-1][1]) >= 0.45:
                    pts_out.append(e_proj_pt)
            end_pt = [round(x1, 2), round(y1, 2)]
            if math.hypot(end_pt[0] - pts_out[-1][0], end_pt[1] - pts_out[-1][1]) >= 0.45:
                pts_out.append(end_pt)
            else:
                pts_out[-1] = end_pt
            return {
                "walk_meters": round(max(4.0, total_road_walk), 1),
                "direct_meters": round(d_direct, 1),
                "mode": "walkway_network" if used_walkway else "road_network",
                "path_points": pts_out,
            }

    blended_m = (0.68 * d_direct + 0.32 * d_manhattan) * 1.12
    return {
        "walk_meters": round(max(4.0, blended_m), 1),
        "direct_meters": round(d_direct, 1),
        "mode": "direct_blended",
        "path_points": [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)]],
    }


def _auto_directional_group_name(
    gx: float, gy: float, unit_row: Optional[Dict[str, Any]], group_code: str
) -> str:
    code_disp = str(group_code or "").strip() or "车位组"
    if not unit_row:
        return f"公共车位组 · {code_disp}"
    b_info = extract_building_info(unit_row.get("unit_id", ""), unit_row.get("name", ""))
    b_name = b_info["building_name"]
    ux = float(unit_row.get("map_x") or 50.0)
    uy = float(unit_row.get("map_y") or 50.0)
    dx = gx - ux
    dy = gy - uy  # +y is South on SVG map
    if math.hypot(dx, dy) < 3.0:
        dir_txt = "楼前"
    else:
        ang_deg = math.degrees(math.atan2(-dy, dx))  # 0=E, 90=N, -90=S
        sectors = [
            (-22.5, 22.5, "东侧"),
            (22.5, 67.5, "东北侧"),
            (67.5, 112.5, "北侧"),
            (112.5, 157.5, "西北侧"),
            (-67.5, -22.5, "东南侧"),
            (-112.5, -67.5, "南侧"),
            (-157.5, -112.5, "西南侧"),
        ]
        dir_txt = "西侧"
        for lo, hi, label in sectors:
            if lo <= ang_deg < hi:
                dir_txt = label
                break
    return f"{b_name}{dir_txt} · {code_disp}"


DEFAULT_SITE_MAP_TOPOLOGY: Dict[str, Any] = {
    "gates": [
        {"gate_id": "south", "name": "南门入口", "x": 47.0, "y": 96.5, "w": 14.0, "h": 4.2},
        {"gate_id": "east", "name": "东门", "x": 96.2, "y": 52.0, "w": 5.5, "h": 12.0},
    ],
    "roads": [
        {
            "road_id": "R_MAIN_RING",
            "name": "小区内部主环路",
            "points": [[8.0, 83.0], [88.0, 83.0], [88.0, 22.0], [12.0, 22.0]],
            "width": 6.2,
            "one_way": False,
            "direction": "twoway",
        }
    ],
    "walkways": [],
    "greens": [
        {"name": "中心景观花园", "x": 42.0, "y": 34.0, "w": 20.0, "h": 11.0}
    ],
    "routes": [
        {
            "route_id": "RT_MAIN_LOOP",
            "name": "南门至东门主行车路线",
            "gate_id": "south",
            "one_way": False,
            "direction": "twoway",
            "points": [[47.0, 95.0], [47.0, 83.0], [88.0, 83.0], [88.0, 22.0], [15.0, 22.0]],
        }
    ],
}


class Database:
    def __init__(self, db_path: str = str(DB_PATH), seed_default: bool = True) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._site_map_cache: Optional[Dict[str, Any]] = None
        self._init_schema()
        if seed_default:
            self._seed_if_empty()
        self.reconcile_spot_groups_and_doors()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS units (
                    unit_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    default_gate TEXT NOT NULL DEFAULT 'south',
                    map_x REAL NOT NULL DEFAULT 50,
                    map_y REAL NOT NULL DEFAULT 50
                );

                CREATE TABLE IF NOT EXISTS zones (
                    zone_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    dead_end_penalty REAL NOT NULL DEFAULT 0,
                    gate_access TEXT NOT NULL DEFAULT 'south,east',
                    map_x REAL NOT NULL DEFAULT 50,
                    map_y REAL NOT NULL DEFAULT 50,
                    map_w REAL NOT NULL DEFAULT 18,
                    map_h REAL NOT NULL DEFAULT 10,
                    description TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS unit_zone_dist (
                    unit_id TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    walk_meters REAL NOT NULL,
                    PRIMARY KEY (unit_id, zone_id)
                );

                CREATE TABLE IF NOT EXISTS cameras (
                    camera_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    mount_type TEXT NOT NULL DEFAULT 'window_ipc',
                    image_width INTEGER NOT NULL DEFAULT 1280,
                    image_height INTEGER NOT NULL DEFAULT 720,
                    last_heartbeat REAL,
                    frames_received INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS spots (
                    spot_id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    spot_type TEXT NOT NULL DEFAULT 'standard', -- standard | irregular | elastic_strip
                    compact_only INTEGER NOT NULL DEFAULT 0,
                    total_meters REAL NOT NULL DEFAULT 5.2,
                    capacity INTEGER NOT NULL DEFAULT 1,
                    polygon_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'FREE', -- FREE | OCCUPIED | BLOCKED | UNKNOWN
                    free_units INTEGER NOT NULL DEFAULT 1,
                    occupied_units INTEGER NOT NULL DEFAULT 0,
                    pending_status TEXT,
                    pending_free_units INTEGER,
                    pending_since REAL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    last_reason TEXT NOT NULL DEFAULT '初始化空闲',
                    last_engine TEXT NOT NULL DEFAULT 'seed',
                    last_changed_at REAL NOT NULL,
                    last_seen_at REAL,
                    ref_free_sig_b64 TEXT,
                    obstacle_type TEXT NOT NULL DEFAULT 'none',
                    action_hint TEXT NOT NULL DEFAULT '',
                    map_x REAL,
                    map_y REAL,
                    map_w REAL,
                    map_h REAL,
                    map_angle REAL,
                    map_custom INTEGER NOT NULL DEFAULT 0,
                    vehicle_polygon_json TEXT,
                    vehicle_poly_custom INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS site_map (
                    map_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL DEFAULT 'seed',
                    backdrop_jpeg_b64 TEXT,
                    aspect_ratio REAL NOT NULL DEFAULT 1.0,
                    topology_json TEXT NOT NULL DEFAULT '{}',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS im_user_bindings (
                    binding_key TEXT PRIMARY KEY,
                    channel TEXT NOT NULL DEFAULT 'siri',
                    external_uid TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    unit_id TEXT NOT NULL DEFAULT '8-1',
                    repeat_count INTEGER NOT NULL DEFAULT 2,
                    vehicle_type TEXT NOT NULL DEFAULT 'standard',
                    updated_at REAL NOT NULL
                );
                """
            )
            self._ensure_migrations(conn)

    def checkpoint_wal(self, mode: str = "TRUNCATE") -> Dict[str, Any]:
        """Flush SQLite WAL pages to disk cleanly before container / Spot VM shutdown."""
        clean_mode = str(mode or "TRUNCATE").strip().upper()
        if clean_mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            clean_mode = "TRUNCATE"
        with self._lock, self._connect() as conn:
            row = conn.execute(f"PRAGMA wal_checkpoint({clean_mode})").fetchone()
            if row is not None and len(row) >= 3:
                return {
                    "ok": int(row[0]) == 0,
                    "mode": clean_mode,
                    "busy": int(row[0]),
                    "log": int(row[1]),
                    "checkpointed": int(row[2]),
                }
            return {"ok": True, "mode": clean_mode, "busy": 0, "log": 0, "checkpointed": 0}

    def get_im_user_binding(
        self, channel: str, external_uid: str
    ) -> Optional[Dict[str, Any]]:
        ch = str(channel or "siri").strip().lower()[:24] or "siri"
        uid = str(external_uid or "").strip()[:128]
        if not uid:
            return None
        bkey = f"{ch}:{uid}"
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM im_user_bindings WHERE binding_key = ?", (bkey,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_im_user_binding(
        self,
        channel: str,
        external_uid: str,
        unit_id: str,
        repeat_count: int = 2,
        vehicle_type: str = "standard",
        display_name: str = "",
    ) -> Dict[str, Any]:
        ch = str(channel or "siri").strip().lower()[:24] or "siri"
        uid = str(external_uid or "").strip()[:128]
        if not uid:
            raise ValueError("external_uid 不能为空")
        uid_clean = str(unit_id or "8-1").strip()[:24] or "8-1"
        rep = 1 if int(repeat_count or 2) <= 1 else 2
        vtype = "compact" if str(vehicle_type or "").strip().lower() == "compact" else "standard"
        dname = str(display_name or "").strip()[:64]
        bkey = f"{ch}:{uid}"
        now = time.time()
        with self._lock, self._connect() as conn:
            prev = conn.execute(
                "SELECT display_name FROM im_user_bindings WHERE binding_key = ?", (bkey,)
            ).fetchone()
            if not dname and prev and prev["display_name"]:
                dname = str(prev["display_name"])
            conn.execute(
                """
                INSERT INTO im_user_bindings (
                    binding_key, channel, external_uid, display_name,
                    unit_id, repeat_count, vehicle_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(binding_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    unit_id = excluded.unit_id,
                    repeat_count = excluded.repeat_count,
                    vehicle_type = excluded.vehicle_type,
                    updated_at = excluded.updated_at
                """,
                (bkey, ch, uid, dname, uid_clean, rep, vtype, now),
            )
            row = conn.execute(
                "SELECT * FROM im_user_bindings WHERE binding_key = ?", (bkey,)
            ).fetchone()
            return dict(row) if row else {
                "binding_key": bkey,
                "channel": ch,
                "external_uid": uid,
                "display_name": dname,
                "unit_id": uid_clean,
                "repeat_count": rep,
                "vehicle_type": vtype,
                "updated_at": now,
            }

    def list_im_user_bindings(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if channel:
                rows = conn.execute(
                    "SELECT * FROM im_user_bindings WHERE channel = ? ORDER BY updated_at DESC",
                    (str(channel).strip().lower(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM im_user_bindings ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _ensure_migrations(conn: sqlite3.Connection) -> None:
        cam_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cameras)").fetchall()}
        for col_name, col_def in (
            ("template_jpeg_b64", "TEXT"),
            ("template_gray_b64", "TEXT"),
            ("template_meta_json", "TEXT"),
            ("group_code", "TEXT NOT NULL DEFAULT ''"),
            ("mount_address", "TEXT NOT NULL DEFAULT ''"),
            ("vehicle_template_jpeg_b64", "TEXT"),
            ("vehicle_template_meta_json", "TEXT"),
            ("crop_roi_json", "TEXT"),
            ("full_frame_jpeg_b64", "TEXT"),
            ("calib_schedule_json", "TEXT"),
            ("calib_health_json", "TEXT"),
            ("anchor_points_json", "TEXT"),
            ("group_name", "TEXT NOT NULL DEFAULT ''"),
            ("ingest_config_json", "TEXT"),
            ("ingest_status_json", "TEXT"),
        ):
            if col_name not in cam_cols:
                conn.execute(f"ALTER TABLE cameras ADD COLUMN {col_name} {col_def}")

        spot_cols = {r["name"] for r in conn.execute("PRAGMA table_info(spots)").fetchall()}
        for col_name, col_def in (
            ("ref_free_sig_b64", "TEXT"),
            ("obstacle_type", "TEXT NOT NULL DEFAULT 'none'"),
            ("action_hint", "TEXT NOT NULL DEFAULT ''"),
            ("map_x", "REAL"),
            ("map_y", "REAL"),
            ("map_w", "REAL"),
            ("map_h", "REAL"),
            ("map_angle", "REAL"),
            ("map_custom", "INTEGER NOT NULL DEFAULT 0"),
            ("vehicle_polygon_json", "TEXT"),
            ("vehicle_poly_custom", "INTEGER NOT NULL DEFAULT 0"),
            ("group_code", "TEXT NOT NULL DEFAULT ''"),
            ("vehicle_model", "TEXT NOT NULL DEFAULT 'sedan'"),
            ("group_name", "TEXT NOT NULL DEFAULT ''"),
            ("near_unit_id", "TEXT NOT NULL DEFAULT ''"),
            ("near_unit_custom", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col_name not in spot_cols:
                conn.execute(f"ALTER TABLE spots ADD COLUMN {col_name} {col_def}")

        zone_cols = {r["name"] for r in conn.execute("PRAGMA table_info(zones)").fetchall()}
        for col_name, col_def in (
            ("layout_angle", "REAL NOT NULL DEFAULT 0"),
            ("is_demo_seed", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if col_name not in zone_cols:
                conn.execute(f"ALTER TABLE zones ADD COLUMN {col_name} {col_def}")

        unit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(units)").fetchall()}
        for col_name, col_def in (
            ("map_w", "REAL NOT NULL DEFAULT 12.4"),
            ("map_h", "REAL NOT NULL DEFAULT 6.8"),
            ("door_x", "REAL"),
            ("door_y", "REAL"),
            ("door_custom", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col_name not in unit_cols:
                conn.execute(f"ALTER TABLE units ADD COLUMN {col_name} {col_def}")

    def _seed_if_empty(self) -> None:
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0]
            if cnt > 0:
                return

            now = time.time()
            units = [
                ("1-1", "1号楼1单元（近南门）", "south", 24, 78),
                ("2-1", "2号楼1单元", "south", 52, 78),
                ("3-1", "3号楼1单元（中心区）", "south", 30, 46),
                ("3-2", "3号楼2单元（中心区）", "east", 48, 46),
                ("5-1", "5号楼1单元（北侧里端）", "east", 30, 18),
                ("6-1", "6号楼1单元（近东门）", "east", 74, 28),
            ]
            conn.executemany(
                "INSERT INTO units (unit_id, name, default_gate, map_x, map_y) VALUES (?, ?, ?, ?, ?)",
                units,
            )

            zones = [
                ("Z_SOUTH_MAIN", "1号楼南侧主干道顺停区", 0.0, "south,east", 16, 86, 36, 9, "宽敞主干道，进南门即达，随时掉头"),
                ("Z_BLDG3_EAST", "3号楼东侧窗台俯拍区", 5.0, "south,east", 34, 56, 32, 10, "4楼志愿者窗台俯拍覆盖，连排标准位"),
                ("Z_BLDG5_DEADEND", "5号楼北侧断头路尽头", 42.0, "east,south", 18, 28, 28, 9, "通道尽头狭窄，若仅剩孤位倒车较难"),
                ("Z_BLDG6_STRIP", "6号楼西侧无划线弹性带", 8.0, "east,south", 68, 40, 14, 34, "18米老旧路侧无划线带，支持弹性测算"),
            ]
            conn.executemany(
                """
                INSERT INTO zones (
                    zone_id, name, dead_end_penalty, gate_access, map_x, map_y, map_w, map_h, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                zones,
            )

            # Walking distance matrix (meters)
            dists = [
                ("1-1", "Z_SOUTH_MAIN", 22), ("1-1", "Z_BLDG3_EAST", 68), ("1-1", "Z_BLDG5_DEADEND", 135), ("1-1", "Z_BLDG6_STRIP", 115),
                ("2-1", "Z_SOUTH_MAIN", 45), ("2-1", "Z_BLDG3_EAST", 52), ("2-1", "Z_BLDG5_DEADEND", 130), ("2-1", "Z_BLDG6_STRIP", 75),
                ("3-1", "Z_SOUTH_MAIN", 65), ("3-1", "Z_BLDG3_EAST", 25), ("3-1", "Z_BLDG5_DEADEND", 72),  ("3-1", "Z_BLDG6_STRIP", 85),
                ("3-2", "Z_SOUTH_MAIN", 75), ("3-2", "Z_BLDG3_EAST", 18), ("3-2", "Z_BLDG5_DEADEND", 78),  ("3-2", "Z_BLDG6_STRIP", 55),
                ("5-1", "Z_SOUTH_MAIN", 140),("5-1", "Z_BLDG3_EAST", 62), ("5-1", "Z_BLDG5_DEADEND", 20),  ("5-1", "Z_BLDG6_STRIP", 78),
                ("6-1", "Z_SOUTH_MAIN", 120),("6-1", "Z_BLDG3_EAST", 58), ("6-1", "Z_BLDG5_DEADEND", 85),  ("6-1", "Z_BLDG6_STRIP", 24),
            ]
            conn.executemany("INSERT INTO unit_zone_dist VALUES (?, ?, ?)", dists)

            cameras = [
                ("CAM_3F_WIN", "3号楼402业主窗台俯拍机", "Z_BLDG3_EAST", "业主窗台Wi-Fi", 1280, 720, now, 1),
                ("CAM_SOUTH_01", "南门主干道普联枪机", "Z_SOUTH_MAIN", "路灯抱箍Wi-Fi", 1280, 720, now, 1),
                ("CAM_NORTH_4G", "5号楼断头路4G太阳能枪机", "Z_BLDG5_DEADEND", "4G太阳能补盲", 1280, 720, now, 1),
                ("CAM_EAST_STRIP", "6号楼201窗台路侧俯拍机", "Z_BLDG6_STRIP", "业主窗台Wi-Fi", 1280, 720, now, 1),
            ]
            conn.executemany(
                """
                INSERT INTO cameras (
                    camera_id, name, zone_id, mount_type, image_width, image_height, last_heartbeat, frames_received
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cameras,
            )

            # Seed realistic spots across the 4 cameras/zones (including perspective trapezoids, irregular corner, elastic strip)
            from app.homography import subdivide_perspective_row

            row_traps = subdivide_perspective_row(
                [[140, 420], [1120, 390], [1030, 610], [210, 650]], count=5, direction="along_01"
            )
            seed_spots = []
            statuses_3 = [
                ("FREE", 1, 0, "空闲车位"),
                ("OCCUPIED", 0, 1, "深色轿车停放"),
                ("FREE", 1, 0, "空闲车位"),
                ("BLOCKED", 0, 0, "雪糕桶异常霸位"),
                ("FREE", 1, 0, "空闲车位"),
            ]
            for idx, poly in enumerate(row_traps):
                st, fu, ou, rsn = statuses_3[idx]
                seed_spots.append(
                    (
                        f"B3-{idx+1:02d}", "CAM_3F_WIN", "Z_BLDG3_EAST", f"3东-{idx+1:02d}",
                        "standard", 0, 5.2, 1, json.dumps(poly), st, fu, ou,
                        None, None, None, 0.94, rsn, "seed", now, now
                    )
                )
            # Add 1 irregular corner compact spot in CAM_3F_WIN
            seed_spots.append(
                (
                    "B3-06-IRR", "CAM_3F_WIN", "Z_BLDG3_EAST", "3东-转角小车位",
                    "irregular", 1, 4.3, 1,
                    json.dumps([[1045, 610], [1210, 560], [1245, 675], [1095, 695], [1035, 660]]),
                    "FREE", 1, 0, None, None, None, 0.91, "转角异形小车位空闲", "seed", now, now
                )
            )

            # South main road (4 spots: 2 free, 2 occupied)
            south_traps = subdivide_perspective_row(
                [[120, 380], [1150, 380], [1180, 620], [90, 620]], count=4, direction="along_01"
            )
            for idx, poly in enumerate(south_traps):
                is_free = idx in (1, 3)
                seed_spots.append(
                    (
                        f"S1-{idx+1:02d}", "CAM_SOUTH_01", "Z_SOUTH_MAIN", f"南主-{idx+1:02d}",
                        "standard", 0, 5.2, 1, json.dumps(poly),
                        "FREE" if is_free else "OCCUPIED",
                        1 if is_free else 0, 0 if is_free else 1,
                        None, None, None, 0.95, "空闲" if is_free else "已停车辆", "seed", now, now
                    )
                )

            # Bldg 5 Dead-end (3 spots: ONLY 1 isolated spot left -> triggers dead-end penalty!)
            north_traps = subdivide_perspective_row(
                [[180, 360], [1060, 360], [1100, 610], [140, 610]], count=3, direction="along_01"
            )
            for idx, poly in enumerate(north_traps):
                is_free = idx == 2
                seed_spots.append(
                    (
                        f"N5-{idx+1:02d}", "CAM_NORTH_4G", "Z_BLDG5_DEADEND", f"5北-{idx+1:02d}",
                        "standard", 0, 5.2, 1, json.dumps(poly),
                        "FREE" if is_free else "OCCUPIED",
                        1 if is_free else 0, 0 if is_free else 1,
                        None, None, None, 0.93, "尽头孤立空位" if is_free else "已停车辆", "seed", now, now
                    )
                )

            # Bldg 6 Unmarked Elastic Strip (18.0 meters total: currently 1 car parked, 2 sedans can still fit!)
            seed_spots.append(
                (
                    "E6-STRIP-01", "CAM_EAST_STRIP", "Z_BLDG6_STRIP", "6西-18米无划线顺停带",
                    "elastic_strip", 0, 18.0, 3,
                    json.dumps([[110, 350], [1160, 330], [1190, 560], [85, 590]]),
                    "FREE", 2, 1, None, None, None, 0.90, "总长18m，已停1辆，连续空档尚可停2辆轿车", "seed", now, now
                )
            )

            conn.executemany(
                """
                INSERT INTO spots (
                    spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                    polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                    pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                seed_spots,
            )

    def list_units(self) -> List[Dict[str, Any]]:
        smap = self.get_site_map(include_backdrop=False)
        topo = smap.get("topology") or {}
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM units").fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                uid = str(d.get("unit_id") or "").strip()
                d["name"] = format_unit_display_name(uid, str(d.get("name") or ""))
                dx, dy = resolve_unit_door_xy(d, topo)
                d["door_x"] = dx
                d["door_y"] = dy
                d["door_custom"] = int(d.get("door_custom") or 0)
                b_info = extract_building_info(uid, d["name"])
                d["building_id"] = b_info["building_id"]
                d["building_name"] = b_info["building_name"]
                d["unit_no"] = b_info["unit_no"]
                out.append(d)
            out.sort(key=lambda item: natural_unit_sort_key(item["unit_id"]))
            return out

    def upsert_unit(
        self,
        unit_id: str,
        name: str = "",
        default_gate: Optional[str] = None,
        map_x: Optional[float] = None,
        map_y: Optional[float] = None,
        map_w: Optional[float] = None,
        map_h: Optional[float] = None,
        door_x: Optional[float] = None,
        door_y: Optional[float] = None,
        door_custom: Optional[int] = None,
        old_unit_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        uid, clean_name = normalize_unit_id_and_name(unit_id, name)
        old_uid = str(old_unit_id or "").strip()
        smap = self.get_site_map(include_backdrop=False)
        topo = smap.get("topology") or {}

        with self._lock, self._connect() as conn:
            all_rows = {
                r["unit_id"]: dict(r)
                for r in conn.execute("SELECT * FROM units").fetchall()
            }
            cur_u = all_rows.get(uid) or (all_rows.get(old_uid) if old_uid else None)

            if map_x is not None and map_y is not None:
                ux = round(max(2.0, min(98.0, float(map_x))), 2)
                uy = round(max(2.0, min(98.0, float(map_y))), 2)
            elif cur_u is not None:
                ux = round(float(cur_u.get("map_x") or 50.0), 2)
                uy = round(float(cur_u.get("map_y") or 50.0), 2)
            else:
                # Smart auto-placement beside same-building sibling (e.g. 9-2 next to 9-1)
                target_bldg = extract_building_info(uid, clean_name)["building_id"]
                sibling = None
                for ex_id, ex_u in all_rows.items():
                    if extract_building_info(ex_id, ex_u.get("name", ""))["building_id"] == target_bldg:
                        sibling = ex_u
                        break
                if sibling is not None:
                    base_x = float(sibling.get("map_x") or 50.0)
                    base_y = float(sibling.get("map_y") or 50.0)
                    ux = round(max(4.0, min(95.0, base_x + 6.2)), 2)
                    uy = round(max(4.0, min(95.0, base_y)), 2)
                else:
                    ux, uy = 50.0, 50.0
                # Nudge slightly if colliding with another unit center
                for _ in range(8):
                    if any(
                        ex_id != uid
                        and math.hypot(float(ex_u.get("map_x") or 50.0) - ux, float(ex_u.get("map_y") or 50.0) - uy) < 3.2
                        for ex_id, ex_u in all_rows.items()
                    ):
                        ux = round(max(4.0, min(95.0, ux + 4.2)), 2)
                    else:
                        break

            uw = round(
                max(
                    4.0,
                    min(
                        28.0,
                        float(
                            map_w
                            if map_w is not None
                            else (cur_u.get("map_w") if cur_u else 8.0)
                        ),
                    ),
                ),
                2,
            )
            uh = round(
                max(
                    3.0,
                    min(
                        20.0,
                        float(
                            map_h
                            if map_h is not None
                            else (cur_u.get("map_h") if cur_u else 4.2)
                        ),
                    ),
                ),
                2,
            )
            if default_gate:
                ugate = str(default_gate).strip()
            elif cur_u and cur_u.get("default_gate"):
                ugate = str(cur_u["default_gate"])
            else:
                # Inherit sibling gate or first topology gate
                gates_list = topo.get("gates") or []
                ugate = str(gates_list[0]["gate_id"]) if gates_list else "south"
                target_bldg = extract_building_info(uid, clean_name)["building_id"]
                for ex_id, ex_u in all_rows.items():
                    if extract_building_info(ex_id, ex_u.get("name", ""))["building_id"] == target_bldg:
                        ugate = str(ex_u.get("default_gate") or ugate)
                        break

            if door_x is not None and door_y is not None:
                dx = round(max(1.0, min(99.0, float(door_x))), 2)
                dy = round(max(1.0, min(99.0, float(door_y))), 2)
                d_cust = 1 if door_custom is None else int(bool(door_custom))
            elif (
                cur_u is not None
                and int(cur_u.get("door_custom") or 0) == 1
                and door_custom != 0
                and cur_u.get("door_x") is not None
                and cur_u.get("door_y") is not None
            ):
                shift_x = ux - float(cur_u.get("map_x") or ux)
                shift_y = uy - float(cur_u.get("map_y") or uy)
                dx = round(max(1.0, min(99.0, float(cur_u["door_x"]) + shift_x)), 2)
                dy = round(max(1.0, min(99.0, float(cur_u["door_y"]) + shift_y)), 2)
                d_cust = 1
            else:
                dx, dy = resolve_unit_door_xy(
                    {"map_x": ux, "map_y": uy, "map_w": uw, "map_h": uh, "door_custom": 0},
                    topo,
                )
                d_cust = 0

            if old_uid and old_uid != uid and old_uid in all_rows:
                conn.execute("DELETE FROM units WHERE unit_id = ?", (old_uid,))
                conn.execute("DELETE FROM unit_zone_dist WHERE unit_id = ?", (old_uid,))
                conn.execute(
                    "UPDATE spots SET near_unit_id = ? WHERE near_unit_id = ?",
                    (uid, old_uid),
                )

            conn.execute(
                """
                INSERT INTO units (
                    unit_id, name, default_gate, map_x, map_y, map_w, map_h, door_x, door_y, door_custom
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id) DO UPDATE SET
                    name = excluded.name,
                    default_gate = excluded.default_gate,
                    map_x = excluded.map_x,
                    map_y = excluded.map_y,
                    map_w = excluded.map_w,
                    map_h = excluded.map_h,
                    door_x = excluded.door_x,
                    door_y = excluded.door_y,
                    door_custom = excluded.door_custom
                """,
                (uid, clean_name, ugate, ux, uy, uw, uh, dx, dy, d_cust),
            )

        self.reconcile_spot_groups_and_doors()
        units_now = self.list_units()
        saved_u = next((u for u in units_now if u["unit_id"] == uid), None)
        return {"unit": saved_u, "units": units_now}

    def delete_unit(self, unit_id: str) -> Dict[str, Any]:
        uid = str(unit_id or "").strip()
        if not uid:
            raise ValueError("请指定要删除的单元编号")
        with self._lock, self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM units").fetchone()[0])
            exists = conn.execute(
                "SELECT 1 FROM units WHERE unit_id = ?", (uid,)
            ).fetchone()
            if not exists:
                raise ValueError(f"单元 {uid} 不存在")
            if total <= 1:
                raise ValueError("至少需要保留 1 个目的地单元")
            conn.execute("DELETE FROM units WHERE unit_id = ?", (uid,))
            conn.execute("DELETE FROM unit_zone_dist WHERE unit_id = ?", (uid,))
            conn.execute(
                "UPDATE spots SET near_unit_id = '', near_unit_custom = 0 WHERE near_unit_id = ?",
                (uid,),
            )
        self.reconcile_spot_groups_and_doors()
        return {"deleted_unit_id": uid, "units": self.list_units()}

    def list_zones(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT z.*,
                       (SELECT COUNT(*) FROM cameras c WHERE c.zone_id = z.zone_id) AS camera_count,
                       (SELECT COUNT(*) FROM spots s WHERE s.zone_id = z.zone_id) AS spot_count
                FROM zones z
                ORDER BY z.zone_id
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_zone(
        self,
        zone_id: str = "",
        name: str = "",
        description: Optional[str] = None,
        gate_access: Optional[str] = None,
        dead_end_penalty: Optional[float] = None,
        map_x: Optional[float] = None,
        map_y: Optional[float] = None,
        map_w: Optional[float] = None,
        map_h: Optional[float] = None,
        old_zone_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        nm = str(name or "").strip()
        if not nm:
            raise ValueError("片区名称不能为空")

        raw_old = str(old_zone_id or "").strip()
        raw_zid = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fa5]+", "_", str(zone_id or "").strip()).strip("_")

        with self._lock, self._connect() as conn:
            cur_row = None
            lookup_id = raw_old or raw_zid
            if lookup_id:
                cur_row = conn.execute(
                    "SELECT * FROM zones WHERE zone_id = ?", (lookup_id,)
                ).fetchone()

            if not raw_zid:
                b_match = re.search(r"(\d+)\s*[号#栋]", nm)
                base_prefix = f"Z_BLDG{b_match.group(1)}" if b_match else "Z_AREA"
                seq = 1
                cand = f"{base_prefix}_{seq:02d}"
                while conn.execute("SELECT 1 FROM zones WHERE zone_id = ?", (cand,)).fetchone():
                    seq += 1
                    cand = f"{base_prefix}_{seq:02d}"
                raw_zid = cand

            if raw_old and raw_old != raw_zid:
                if not cur_row:
                    raise ValueError(f"原片区不存在: {raw_old}")
                conflict = conn.execute(
                    "SELECT 1 FROM zones WHERE zone_id = ?", (raw_zid,)
                ).fetchone()
                if conflict:
                    raise ValueError(f"目标片区编码已存在: {raw_zid}")

            cur = dict(cur_row) if cur_row else {}
            final_desc = (
                str(description).strip()
                if description is not None
                else str(cur.get("description") or "支持人工校准与自定义微调的小区停车片区")
            )
            final_gates = (
                str(gate_access).strip() or "south,east"
                if gate_access is not None
                else str(cur.get("gate_access") or "south,east")
            )
            final_penalty = (
                max(0.0, min(100.0, float(dead_end_penalty)))
                if dead_end_penalty is not None
                else float(cur.get("dead_end_penalty") or 0.0)
            )
            final_mx = (
                max(2.0, min(96.0, float(map_x)))
                if map_x is not None
                else float(cur.get("map_x") if cur.get("map_x") is not None else 44.0)
            )
            final_my = (
                max(2.0, min(96.0, float(map_y)))
                if map_y is not None
                else float(cur.get("map_y") if cur.get("map_y") is not None else 44.0)
            )
            final_mw = (
                max(4.0, min(60.0, float(map_w)))
                if map_w is not None
                else float(cur.get("map_w") if cur.get("map_w") is not None else 14.0)
            )
            final_mh = (
                max(3.0, min(50.0, float(map_h)))
                if map_h is not None
                else float(cur.get("map_h") if cur.get("map_h") is not None else 7.0)
            )

            if raw_old and raw_old != raw_zid:
                conn.execute(
                    """
                    UPDATE zones
                    SET zone_id = ?, name = ?, dead_end_penalty = ?, gate_access = ?,
                        map_x = ?, map_y = ?, map_w = ?, map_h = ?, description = ?
                    WHERE zone_id = ?
                    """,
                    (
                        raw_zid,
                        nm,
                        final_penalty,
                        final_gates,
                        final_mx,
                        final_my,
                        final_mw,
                        final_mh,
                        final_desc,
                        raw_old,
                    ),
                )
                conn.execute("UPDATE cameras SET zone_id = ? WHERE zone_id = ?", (raw_zid, raw_old))
                conn.execute("UPDATE spots SET zone_id = ? WHERE zone_id = ?", (raw_zid, raw_old))
                conn.execute("UPDATE unit_zone_dist SET zone_id = ? WHERE zone_id = ?", (raw_zid, raw_old))
            else:
                conn.execute(
                    """
                    INSERT INTO zones (
                        zone_id, name, dead_end_penalty, gate_access, map_x, map_y, map_w, map_h, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(zone_id) DO UPDATE SET
                        name = excluded.name,
                        dead_end_penalty = excluded.dead_end_penalty,
                        gate_access = excluded.gate_access,
                        map_x = excluded.map_x,
                        map_y = excluded.map_y,
                        map_w = excluded.map_w,
                        map_h = excluded.map_h,
                        description = excluded.description
                    """,
                    (
                        raw_zid,
                        nm,
                        final_penalty,
                        final_gates,
                        final_mx,
                        final_my,
                        final_mw,
                        final_mh,
                        final_desc,
                    ),
                )

        self.recompute_unit_zone_distances()
        zones_now = self.list_zones()
        saved = next((z for z in zones_now if z["zone_id"] == raw_zid), None)
        return {"zone": saved, "zones": zones_now}

    def delete_zone(
        self, zone_id: str, reassign_zone_id: Optional[str] = None
    ) -> Dict[str, Any]:
        zid = str(zone_id or "").strip()
        if not zid:
            raise ValueError("zone_id 不能为空")
        with self._lock, self._connect() as conn:
            all_zones = [dict(r) for r in conn.execute("SELECT * FROM zones ORDER BY zone_id").fetchall()]
            target_cur = next((z for z in all_zones if z["zone_id"] == zid), None)
            if not target_cur:
                raise ValueError(f"片区不存在: {zid}")
            remaining = [z for z in all_zones if z["zone_id"] != zid]
            if not remaining:
                raise ValueError("系统至少需要保留 1 个小区停车片区，无法删除最后一个片区")

            explicit_target = str(reassign_zone_id or "").strip()
            fallback_zone = next(
                (z for z in remaining if z["zone_id"] == explicit_target),
                None,
            )
            if not fallback_zone:
                tx = float(target_cur.get("map_x") or 50.0)
                ty = float(target_cur.get("map_y") or 50.0)
                fallback_zone = min(
                    remaining,
                    key=lambda z: math.hypot(
                        tx - float(z.get("map_x") or 50.0),
                        ty - float(z.get("map_y") or 50.0),
                    ),
                )
            dest_zid = str(fallback_zone["zone_id"])

            mig_cams = conn.execute(
                "UPDATE cameras SET zone_id = ? WHERE zone_id = ?", (dest_zid, zid)
            ).rowcount
            mig_spots = conn.execute(
                "UPDATE spots SET zone_id = ? WHERE zone_id = ?", (dest_zid, zid)
            ).rowcount
            conn.execute("DELETE FROM unit_zone_dist WHERE zone_id = ?", (zid,))
            conn.execute("DELETE FROM zones WHERE zone_id = ?", (zid,))

        self.recompute_unit_zone_distances()
        return {
            "deleted": True,
            "deleted_zone_id": zid,
            "reassigned_zone_id": dest_zid,
            "reassigned_to": dest_zid,
            "reassigned_zone_name": str(fallback_zone.get("name") or dest_zid),
            "migrated_cameras": int(mig_cams or 0),
            "migrated_spots": int(mig_spots or 0),
            "zones": self.list_zones(),
        }

    def get_unit_distances(self, unit_id: str) -> Dict[str, float]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT zone_id, walk_meters FROM unit_zone_dist WHERE unit_id = ?",
                (unit_id,),
            ).fetchall()
            return {r["zone_id"]: float(r["walk_meters"]) for r in rows}

    @staticmethod
    def derive_default_anchor_points(
        spot_polygons: List[List[List[float]]], cal_w: int, cal_h: int
    ) -> List[Dict[str, Any]]:
        w = max(64.0, float(cal_w or 1280))
        h = max(64.0, float(cal_h or 720))
        valid_polys = [
            [[float(p[0]), float(p[1])] for p in poly if isinstance(p, (list, tuple)) and len(p) >= 2]
            for poly in (spot_polygons or [])
            if isinstance(poly, list) and len(poly) >= 3
        ]
        valid_polys = [p for p in valid_polys if len(p) >= 3]
        if valid_polys:
            valid_polys.sort(key=lambda poly: sum(pt[0] for pt in poly) / len(poly))

            def _quad_corners(poly: List[List[float]]):
                by_y = sorted(poly, key=lambda pt: pt[1])
                mid = max(2, len(by_y) // 2)
                top_pts = by_y[:mid]
                bot_pts = by_y[-mid:]
                tl = min(top_pts, key=lambda pt: pt[0])
                tr = max(top_pts, key=lambda pt: pt[0])
                br = max(bot_pts, key=lambda pt: pt[0])
                bl = min(bot_pts, key=lambda pt: pt[0])
                return tl, tr, br, bl

            tl_l, _, _, bl_l = _quad_corners(valid_polys[0])
            _, tr_r, br_r, _ = _quad_corners(valid_polys[-1])
            return [
                {"id": "A1", "label": "左后沿基点", "x": round(tl_l[0], 1), "y": round(tl_l[1], 1), "auto": True},
                {"id": "A2", "label": "右后沿基点", "x": round(tr_r[0], 1), "y": round(tr_r[1], 1), "auto": True},
                {"id": "A3", "label": "右前路缘点", "x": round(br_r[0], 1), "y": round(br_r[1], 1), "auto": True},
                {"id": "A4", "label": "左前路缘点", "x": round(bl_l[0], 1), "y": round(bl_l[1], 1), "auto": True},
            ]
        return [
            {"id": "A1", "label": "左后沿基点", "x": round(w * 0.08, 1), "y": round(h * 0.16, 1), "auto": True},
            {"id": "A2", "label": "右后沿基点", "x": round(w * 0.92, 1), "y": round(h * 0.16, 1), "auto": True},
            {"id": "A3", "label": "右前路缘点", "x": round(w * 0.92, 1), "y": round(h * 0.84, 1), "auto": True},
            {"id": "A4", "label": "左前路缘点", "x": round(w * 0.08, 1), "y": round(h * 0.84, 1), "auto": True},
        ]

    @staticmethod
    def _format_camera_row(row: sqlite3.Row, include_blobs: bool = False) -> Dict[str, Any]:
        from app.camera_ingestor import sanitize_ingest_config_for_public
        from app.camera_naming import parse_camera_group_codes

        d = dict(row)
        has_tpl = bool(d.get("template_jpeg_b64"))
        has_veh_tpl = bool(d.get("vehicle_template_jpeg_b64"))
        has_full_frame = bool(d.get("full_frame_jpeg_b64"))
        meta_raw = d.pop("template_meta_json", None)
        veh_meta_raw = d.pop("vehicle_template_meta_json", None)
        crop_raw = d.pop("crop_roi_json", None)
        sched_raw = d.pop("calib_schedule_json", None)
        health_raw = d.pop("calib_health_json", None)
        anchors_raw = d.pop("anchor_points_json", None)
        ingest_cfg_raw = d.pop("ingest_config_json", None)
        ingest_st_raw = d.pop("ingest_status_json", None)

        d["has_template"] = has_tpl
        d["has_vehicle_template"] = has_veh_tpl
        d["has_full_frame"] = has_full_frame
        d["template_meta"] = json.loads(meta_raw) if meta_raw else None
        d["vehicle_template_meta"] = json.loads(veh_meta_raw) if veh_meta_raw else None

        custom_anchors: List[Dict[str, Any]] = []
        if anchors_raw:
            try:
                parsed_anchors = json.loads(anchors_raw)
                if isinstance(parsed_anchors, list):
                    for idx, item in enumerate(parsed_anchors[:12]):
                        if isinstance(item, dict) and "x" in item and "y" in item:
                            custom_anchors.append(
                                {
                                    "id": str(item.get("id") or f"A{idx + 1}"),
                                    "label": str(item.get("label") or f"锚点{idx + 1}"),
                                    "x": round(float(item["x"]), 1),
                                    "y": round(float(item["y"]), 1),
                                    "auto": False,
                                }
                            )
            except Exception:
                custom_anchors = []
        d["anchor_points"] = custom_anchors
        d["anchor_points_custom"] = len(custom_anchors) >= 3

        crop_cfg: Dict[str, Any] = {"enabled": False, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        if crop_raw:
            try:
                parsed_crop = json.loads(crop_raw)
                if isinstance(parsed_crop, dict):
                    crop_cfg.update(parsed_crop)
            except Exception:
                pass
        raw_sz = crop_cfg.get("raw_size")
        if isinstance(raw_sz, (list, tuple)) and len(raw_sz) >= 2:
            try:
                rw_i = max(1, int(raw_sz[0]))
                rh_i = max(1, int(raw_sz[1]))
                crop_cfg.setdefault("raw_width", rw_i)
                crop_cfg.setdefault("raw_height", rh_i)
                crop_cfg.setdefault("raw_aspect", round(float(rw_i) / float(rh_i), 5))
            except Exception:
                pass
        elif crop_cfg.get("raw_width") and crop_cfg.get("raw_height"):
            try:
                rw_i = max(1, int(crop_cfg["raw_width"]))
                rh_i = max(1, int(crop_cfg["raw_height"]))
                crop_cfg["raw_size"] = [rw_i, rh_i]
                crop_cfg.setdefault("raw_aspect", round(float(rw_i) / float(rh_i), 5))
            except Exception:
                pass
        d["crop_roi"] = crop_cfg

        sched_cfg: Dict[str, Any] = dict(DEFAULT_CALIB_SCHEDULE)
        if sched_raw:
            try:
                parsed_sched = json.loads(sched_raw)
                if isinstance(parsed_sched, dict):
                    sched_cfg.update(parsed_sched)
            except Exception:
                pass
        hm_norm = str(sched_cfg.get("night_time") or sched_cfg.get("nightly_time") or "02:30")
        sched_cfg["nightly_time"] = hm_norm
        sched_cfg["night_time"] = hm_norm
        ac_norm = bool(
            sched_cfg["auto_compensate_shift"]
            if "auto_compensate_shift" in sched_cfg
            else sched_cfg.get("auto_compensate", True)
        )
        sched_cfg["auto_compensate"] = ac_norm
        sched_cfg["auto_compensate_shift"] = ac_norm

        health_cfg: Optional[Dict[str, Any]] = None
        if health_raw:
            try:
                parsed_h = json.loads(health_raw)
                if isinstance(parsed_h, dict):
                    health_cfg = parsed_h
                    if "drift_px" not in health_cfg and "shift_px" in health_cfg:
                        health_cfg["drift_px"] = health_cfg["shift_px"]
                    if "last_verified_ts" not in health_cfg and "checked_at" in health_cfg:
                        health_cfg["last_verified_ts"] = health_cfg["checked_at"]
            except Exception:
                pass
        if sched_cfg.get("enabled") and not sched_cfg.get("next_check_ts"):
            sched_cfg["next_check_ts"] = (
                (health_cfg or {}).get("next_check_at")
                or compute_next_calib_check_ts(sched_cfg)
            )
        elif not sched_cfg.get("enabled"):
            sched_cfg["next_check_ts"] = None
        d["calib_schedule"] = sched_cfg
        d["calib_health"] = health_cfg

        d["group_code"] = str(d.get("group_code") or "")
        d["mount_address"] = str(d.get("mount_address") or "")
        parsed_info = parse_camera_group_codes(d["group_code"], d["mount_address"])
        d["parsed_groups"] = parsed_info["groups"]
        d["spot_groups"] = parsed_info["groups"]
        d["standard_name_preview"] = parsed_info["standard_name"]

        parsed_ingest_cfg: Dict[str, Any] = {}
        if ingest_cfg_raw:
            try:
                loaded_ic = json.loads(ingest_cfg_raw)
                if isinstance(loaded_ic, dict):
                    parsed_ingest_cfg = loaded_ic
            except Exception:
                parsed_ingest_cfg = {}
        d["ingest_config"] = sanitize_ingest_config_for_public(
            parsed_ingest_cfg,
            camera_id=str(d.get("camera_id") or ""),
        )
        parsed_ingest_st: Dict[str, Any] = {"state": "idle"}
        if ingest_st_raw:
            try:
                loaded_ist = json.loads(ingest_st_raw)
                if isinstance(loaded_ist, dict):
                    parsed_ingest_st.update(loaded_ist)
            except Exception:
                pass
        d["ingest_status"] = parsed_ingest_st

        if not include_blobs:
            d.pop("template_jpeg_b64", None)
            d.pop("template_gray_b64", None)
            d.pop("vehicle_template_jpeg_b64", None)
            d.pop("full_frame_jpeg_b64", None)
        return d

    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM cameras ORDER BY camera_id").fetchall()
            cnt_rows = conn.execute(
                """
                SELECT camera_id,
                       COUNT(*) AS total_cnt,
                       SUM(CASE WHEN COALESCE(last_engine, '') != 'map_editor' THEN 1 ELSE 0 END) AS calibrated_cnt,
                       SUM(CASE WHEN map_x IS NOT NULL AND map_y IS NOT NULL THEN 1 ELSE 0 END) AS placed_cnt
                FROM spots
                GROUP BY camera_id
                """
            ).fetchall()
            counts = {
                r["camera_id"]: (
                    int(r["total_cnt"] or 0),
                    int(r["calibrated_cnt"] or 0),
                    int(r["placed_cnt"] or 0),
                )
                for r in cnt_rows
            }
            bound_grp_rows = conn.execute(
                """
                SELECT camera_id, group_code
                FROM spots
                WHERE camera_id != 'UNASSIGNED'
                  AND map_x IS NOT NULL AND map_y IS NOT NULL
                  AND COALESCE(group_code, '') != ''
                GROUP BY camera_id, group_code
                ORDER BY group_code
                """
            ).fetchall()
            bound_grps_by_cam: Dict[str, List[str]] = {}
            for gr in bound_grp_rows:
                gc_val = str(gr["group_code"] or "").strip()
                if gc_val:
                    bound_grps_by_cam.setdefault(str(gr["camera_id"]), []).append(gc_val)
            poly_rows = conn.execute(
                """
                SELECT camera_id, polygon_json
                FROM spots
                WHERE COALESCE(last_engine, '') != 'map_editor'
                ORDER BY spot_id
                """
            ).fetchall()
            polys_by_cam: Dict[str, List[List[List[float]]]] = {}
            for pr in poly_rows:
                try:
                    polys_by_cam.setdefault(pr["camera_id"], []).append(json.loads(pr["polygon_json"]))
                except Exception:
                    pass
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = self._format_camera_row(r, include_blobs=False)
                tot_c, cal_c, plc_c = counts.get(d["camera_id"], (0, 0, 0))
                d["spot_count"] = tot_c
                d["calibrated_spot_count"] = cal_c
                d["map_placed_spot_count"] = plc_c
                d["bound_group_codes"] = bound_grps_by_cam.get(d["camera_id"], [])
                if not d.get("anchor_points_custom"):
                    d["anchor_points"] = self.derive_default_anchor_points(
                        polys_by_cam.get(d["camera_id"], []),
                        int(d.get("image_width") or 1280),
                        int(d.get("image_height") or 720),
                    )
                out.append(d)
            return out

    def get_camera(self, camera_id: str, include_blobs: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            if not row:
                return None
            d = self._format_camera_row(row, include_blobs=include_blobs)
            cnt_row = conn.execute(
                """
                SELECT COUNT(*) AS total_cnt,
                       SUM(CASE WHEN COALESCE(last_engine, '') != 'map_editor' THEN 1 ELSE 0 END) AS calibrated_cnt,
                       SUM(CASE WHEN map_x IS NOT NULL AND map_y IS NOT NULL THEN 1 ELSE 0 END) AS placed_cnt
                FROM spots
                WHERE camera_id = ?
                """,
                (camera_id,),
            ).fetchone()
            d["spot_count"] = int(cnt_row["total_cnt"] or 0) if cnt_row else 0
            d["calibrated_spot_count"] = int(cnt_row["calibrated_cnt"] or 0) if cnt_row else 0
            d["map_placed_spot_count"] = int(cnt_row["placed_cnt"] or 0) if cnt_row else 0
            bg_rows = conn.execute(
                """
                SELECT DISTINCT group_code
                FROM spots
                WHERE camera_id = ?
                  AND map_x IS NOT NULL AND map_y IS NOT NULL
                  AND COALESCE(group_code, '') != ''
                ORDER BY group_code
                """,
                (camera_id,),
            ).fetchall()
            d["bound_group_codes"] = [
                str(r["group_code"]).strip() for r in bg_rows if str(r["group_code"] or "").strip()
            ]
            if not d.get("anchor_points_custom"):
                poly_rows = conn.execute(
                    """
                    SELECT polygon_json FROM spots
                    WHERE camera_id = ? AND COALESCE(last_engine, '') != 'map_editor'
                    ORDER BY spot_id
                    """,
                    (camera_id,),
                ).fetchall()
                spot_polys: List[List[List[float]]] = []
                for pr in poly_rows:
                    try:
                        spot_polys.append(json.loads(pr["polygon_json"]))
                    except Exception:
                        pass
                d["anchor_points"] = self.derive_default_anchor_points(
                    spot_polys,
                    int(d.get("image_width") or 1280),
                    int(d.get("image_height") or 720),
                )
            return d

    def save_camera_anchor_points(
        self,
        camera_id: str,
        anchor_points: Optional[List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        cur_cam = self.get_camera(camera_id, include_blobs=False)
        if not cur_cam:
            raise ValueError(f"未找到相机机位: {camera_id}")
        w = max(64.0, float(cur_cam.get("image_width") or 1280))
        h = max(64.0, float(cur_cam.get("image_height") or 720))
        cleaned: List[Dict[str, Any]] = []
        default_labels = ["左后沿基点", "右后沿基点", "右前路缘点", "左前路缘点"]
        for idx, pt in enumerate((anchor_points or [])[:12]):
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                continue
            cleaned.append(
                {
                    "id": str(pt.get("id") or f"A{idx + 1}")[:12],
                    "label": str(
                        pt.get("label")
                        or (default_labels[idx] if idx < len(default_labels) else f"固定锚点{idx + 1}")
                    )[:24],
                    "x": round(max(0.0, min(w - 1.0, float(pt["x"]))), 1),
                    "y": round(max(0.0, min(h - 1.0, float(pt["y"]))), 1),
                }
            )
        serialized = json.dumps(cleaned, ensure_ascii=False) if len(cleaned) >= 3 else None
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET anchor_points_json = ? WHERE camera_id = ?",
                (serialized, camera_id),
            )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.get_camera(camera_id, include_blobs=False)

    def save_camera_template(
        self,
        camera_id: str,
        template_jpeg_b64: str,
        template_gray_b64: str,
        template_meta: Dict[str, Any],
        image_width: int,
        image_height: int,
        zone_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            if zone_id:
                conn.execute(
                    """
                    UPDATE cameras
                    SET zone_id = ?, image_width = ?, image_height = ?,
                        template_jpeg_b64 = ?, template_gray_b64 = ?, template_meta_json = ?,
                        last_heartbeat = ?
                    WHERE camera_id = ?
                    """,
                    (
                        zone_id,
                        image_width,
                        image_height,
                        template_jpeg_b64,
                        template_gray_b64,
                        json.dumps(template_meta, ensure_ascii=False),
                        now,
                        camera_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE cameras
                    SET image_width = ?, image_height = ?,
                        template_jpeg_b64 = ?, template_gray_b64 = ?, template_meta_json = ?,
                        last_heartbeat = ?
                    WHERE camera_id = ?
                    """,
                    (
                        image_width,
                        image_height,
                        template_jpeg_b64,
                        template_gray_b64,
                        json.dumps(template_meta, ensure_ascii=False),
                        now,
                        camera_id,
                    ),
                )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.get_camera(camera_id, include_blobs=False)

    def save_camera_vehicle_template(
        self,
        camera_id: str,
        vehicle_template_jpeg_b64: str,
        vehicle_template_meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        meta_json = json.dumps(vehicle_template_meta or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE cameras
                SET vehicle_template_jpeg_b64 = ?, vehicle_template_meta_json = ?, last_heartbeat = ?
                WHERE camera_id = ?
                """,
                (vehicle_template_jpeg_b64, meta_json, now, camera_id),
            )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.get_camera(camera_id, include_blobs=False)

    def get_camera_template(self, camera_id: str) -> Optional[Dict[str, Any]]:
        cam = self.get_camera(camera_id, include_blobs=True)
        if not cam:
            return None
        return {
            "camera_id": cam["camera_id"],
            "has_template": bool(cam.get("has_template")),
            "has_full_frame": bool(cam.get("has_full_frame")),
            "image_width": int(cam.get("image_width") or 1280),
            "image_height": int(cam.get("image_height") or 720),
            "crop_roi": cam.get("crop_roi"),
            "calib_schedule": cam.get("calib_schedule"),
            "calib_health": cam.get("calib_health"),
            "template_jpeg_b64": cam.get("template_jpeg_b64") or "",
            "template_meta": cam.get("template_meta") or {},
        }

    def get_camera_full_frame(self, camera_id: str) -> Optional[Dict[str, Any]]:
        cam = self.get_camera(camera_id, include_blobs=True)
        if not cam:
            return None
        return {
            "camera_id": cam["camera_id"],
            "has_full_frame": bool(cam.get("full_frame_jpeg_b64")),
            "full_frame_jpeg_b64": cam.get("full_frame_jpeg_b64") or cam.get("template_jpeg_b64") or "",
            "crop_roi": cam.get("crop_roi"),
        }

    def save_camera_full_frame_backup(
        self, camera_id: str, full_frame_jpeg_b64: str
    ) -> None:
        if not full_frame_jpeg_b64:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET full_frame_jpeg_b64 = ? WHERE camera_id = ?",
                (full_frame_jpeg_b64, camera_id),
            )

    def save_camera_latest_frame(
        self,
        camera_id: str,
        full_frame_jpeg_b64: str,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None,
    ) -> None:
        if not full_frame_jpeg_b64:
            return
        with self._lock, self._connect() as conn:
            cal_row = conn.execute(
                """
                SELECT COUNT(*) AS cal_cnt
                FROM spots
                WHERE camera_id = ? AND COALESCE(last_engine, '') != 'map_editor'
                """,
                (camera_id,),
            ).fetchone()
            cal_cnt = int(cal_row["cal_cnt"] or 0) if cal_row else 0
            if cal_cnt == 0 and image_width and image_height and int(image_width) > 0 and int(image_height) > 0:
                conn.execute(
                    """
                    UPDATE cameras
                    SET full_frame_jpeg_b64 = ?, image_width = ?, image_height = ?
                    WHERE camera_id = ?
                    """,
                    (full_frame_jpeg_b64, int(image_width), int(image_height), camera_id),
                )
            else:
                conn.execute(
                    "UPDATE cameras SET full_frame_jpeg_b64 = ? WHERE camera_id = ?",
                    (full_frame_jpeg_b64, camera_id),
                )

    def get_camera_ingest_record(self, camera_id: str) -> Optional[Dict[str, Any]]:
        from app.camera_ingestor import normalize_ingest_config

        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT camera_id, name, zone_id, mount_type, group_code, group_name,
                       ingest_config_json, ingest_status_json,
                       CASE WHEN COALESCE(full_frame_jpeg_b64, '') != ''
                              OR COALESCE(template_jpeg_b64, '') != ''
                            THEN 1 ELSE 0 END AS has_ref_frame
                FROM cameras
                WHERE camera_id = ?
                """,
                (camera_id,),
            ).fetchone()
            if not row:
                return None
            raw_cfg: Dict[str, Any] = {}
            if row["ingest_config_json"]:
                try:
                    loaded = json.loads(row["ingest_config_json"])
                    if isinstance(loaded, dict):
                        raw_cfg = loaded
                except Exception:
                    raw_cfg = {}
            raw_st: Dict[str, Any] = {"state": "idle"}
            if row["ingest_status_json"]:
                try:
                    loaded_st = json.loads(row["ingest_status_json"])
                    if isinstance(loaded_st, dict):
                        raw_st.update(loaded_st)
                except Exception:
                    pass
            return {
                "camera_id": str(row["camera_id"]),
                "name": str(row["name"] or ""),
                "zone_id": str(row["zone_id"] or ""),
                "mount_type": str(row["mount_type"] or ""),
                "group_code": str(row["group_code"] or ""),
                "group_name": str(row["group_name"] or ""),
                "ingest_config": normalize_ingest_config(raw_cfg),
                "ingest_status": raw_st,
                "has_reference_frame": bool(row["has_ref_frame"]),
            }

    def list_active_pull_camera_records(self) -> List[Dict[str, Any]]:
        from app.camera_ingestor import normalize_ingest_config

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT camera_id, name, zone_id, mount_type,
                       ingest_config_json, ingest_status_json,
                       CASE WHEN COALESCE(full_frame_jpeg_b64, '') != ''
                              OR COALESCE(template_jpeg_b64, '') != ''
                            THEN 1 ELSE 0 END AS has_ref_frame
                FROM cameras
                WHERE COALESCE(ingest_config_json, '') != ''
                ORDER BY camera_id
                """
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                try:
                    raw_cfg = json.loads(r["ingest_config_json"] or "{}")
                    if not isinstance(raw_cfg, dict):
                        continue
                except Exception:
                    continue
                cfg = normalize_ingest_config(raw_cfg)
                if not cfg.get("enabled"):
                    continue
                from app.camera_ingestor import resolve_slot_ingest_config

                _, _, act_slot_cfg = resolve_slot_ingest_config(cfg)
                if act_slot_cfg.get("mode") != "active_pull" and cfg.get("mode") != "active_pull":
                    continue
                raw_st: Dict[str, Any] = {"state": "idle"}
                if r["ingest_status_json"]:
                    try:
                        loaded_st = json.loads(r["ingest_status_json"])
                        if isinstance(loaded_st, dict):
                            raw_st.update(loaded_st)
                    except Exception:
                        pass
                out.append(
                    {
                        "camera_id": str(r["camera_id"]),
                        "name": str(r["name"] or ""),
                        "zone_id": str(r["zone_id"] or ""),
                        "mount_type": str(r["mount_type"] or ""),
                        "ingest_config": cfg,
                        "ingest_status": raw_st,
                        "has_reference_frame": bool(r["has_ref_frame"]),
                    }
                )
            return out

    def save_camera_ingest_config(
        self, camera_id: str, ingest_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        from app.camera_ingestor import normalize_ingest_config

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT camera_id, ingest_config_json FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"机位 {camera_id} 不存在")
            existing_cfg: Dict[str, Any] = {}
            if row["ingest_config_json"]:
                try:
                    loaded = json.loads(row["ingest_config_json"])
                    if isinstance(loaded, dict):
                        existing_cfg = loaded
                except Exception:
                    existing_cfg = {}
            merged = normalize_ingest_config(ingest_payload or {}, existing_cfg)
            conn.execute(
                "UPDATE cameras SET ingest_config_json = ? WHERE camera_id = ?",
                (json.dumps(merged, ensure_ascii=False), camera_id),
            )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.get_camera(camera_id)  # type: ignore

    def update_camera_ingest_status(
        self, camera_id: str, status_payload: Dict[str, Any]
    ) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT ingest_status_json FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
            if not row:
                return
            merged: Dict[str, Any] = {"state": "idle"}
            if row["ingest_status_json"]:
                try:
                    loaded = json.loads(row["ingest_status_json"])
                    if isinstance(loaded, dict):
                        merged.update(loaded)
                except Exception:
                    pass
            merged.update(status_payload or {})
            conn.execute(
                "UPDATE cameras SET ingest_status_json = ? WHERE camera_id = ?",
                (json.dumps(merged, ensure_ascii=False), camera_id),
            )

    def save_camera_crop_roi(
        self,
        camera_id: str,
        crop_roi: Dict[str, Any],
        image_width: Optional[int] = None,
        image_height: Optional[int] = None,
        template_jpeg_b64: Optional[str] = None,
        template_gray_b64: Optional[str] = None,
        template_meta: Optional[Dict[str, Any]] = None,
        vehicle_template_jpeg_b64: Optional[str] = None,
        full_frame_jpeg_b64: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        clean_roi = {
            "enabled": bool(crop_roi.get("enabled", False)),
            "x": round(max(0.0, min(0.95, float(crop_roi.get("x", 0.0)))), 4),
            "y": round(max(0.0, min(0.95, float(crop_roi.get("y", 0.0)))), 4),
            "w": round(max(0.05, min(1.0, float(crop_roi.get("w", 1.0)))), 4),
            "h": round(max(0.05, min(1.0, float(crop_roi.get("h", 1.0)))), 4),
        }
        if clean_roi["x"] + clean_roi["w"] > 1.0:
            clean_roi["w"] = round(max(0.05, 1.0 - clean_roi["x"]), 4)
        if clean_roi["y"] + clean_roi["h"] > 1.0:
            clean_roi["h"] = round(max(0.05, 1.0 - clean_roi["y"]), 4)
        if crop_roi.get("raw_aspect"):
            clean_roi["raw_aspect"] = round(float(crop_roi["raw_aspect"]), 5)
        raw_w_val = int(crop_roi.get("raw_width") or 0)
        raw_h_val = int(crop_roi.get("raw_height") or 0)
        if isinstance(crop_roi.get("raw_size"), (list, tuple)) and len(crop_roi["raw_size"]) >= 2:
            raw_w_val = raw_w_val or int(crop_roi["raw_size"][0] or 0)
            raw_h_val = raw_h_val or int(crop_roi["raw_size"][1] or 0)
        if raw_w_val > 0 and raw_h_val > 0:
            clean_roi["raw_size"] = [raw_w_val, raw_h_val]
            clean_roi["raw_width"] = raw_w_val
            clean_roi["raw_height"] = raw_h_val
            clean_roi.setdefault("raw_aspect", round(float(raw_w_val) / max(1.0, float(raw_h_val)), 5))
        if "user_disabled" in crop_roi:
            clean_roi["user_disabled"] = bool(crop_roi.get("user_disabled"))

        if clean_roi["w"] >= 0.995 and clean_roi["h"] >= 0.995 and clean_roi["x"] <= 0.005 and clean_roi["y"] <= 0.005:
            clean_roi["enabled"] = False

        updates = ["crop_roi_json = ?"]
        params: List[Any] = [json.dumps(clean_roi, ensure_ascii=False)]
        if image_width and image_height:
            updates.extend(["image_width = ?", "image_height = ?"])
            params.extend([int(image_width), int(image_height)])
        if template_jpeg_b64 is not None:
            updates.append("template_jpeg_b64 = ?")
            params.append(template_jpeg_b64)
        if template_gray_b64 is not None:
            updates.append("template_gray_b64 = ?")
            params.append(template_gray_b64)
        if template_meta is not None:
            updates.append("template_meta_json = ?")
            params.append(json.dumps(template_meta, ensure_ascii=False))
        if vehicle_template_jpeg_b64 is not None:
            updates.append("vehicle_template_jpeg_b64 = ?")
            params.append(vehicle_template_jpeg_b64)
        if full_frame_jpeg_b64 is not None:
            updates.append("full_frame_jpeg_b64 = ?")
            params.append(full_frame_jpeg_b64)
        params.append(camera_id)

        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE cameras SET {', '.join(updates)} WHERE camera_id = ?",
                tuple(params),
            )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.get_camera(camera_id, include_blobs=False)

    def _snapshot_active_slot_calibration_conn(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
        force: bool = False,
    ) -> None:
        """Persist current camera slot's calibration & reference templates into ingest_config_json."""
        cam_row = conn.execute(
            """
            SELECT ingest_config_json, image_width, image_height, crop_roi_json, anchor_points_json,
                   template_jpeg_b64, template_gray_b64, template_meta_json,
                   vehicle_template_jpeg_b64, vehicle_template_meta_json, full_frame_jpeg_b64
            FROM cameras
            WHERE camera_id = ?
            """,
            (camera_id,),
        ).fetchone()
        if not cam_row or not cam_row["ingest_config_json"]:
            return
        try:
            cfg = json.loads(cam_row["ingest_config_json"])
            if not isinstance(cfg, dict):
                return
        except Exception:
            return
        standbys = cfg.get("standby_slots") or []
        profiles = cfg.get("calibration_profiles")
        if not force and not standbys and not profiles:
            return
        if not isinstance(profiles, dict):
            profiles = {}
        try:
            cur_slot = max(0, int(cfg.get("active_slot_index") or 0))
        except (TypeError, ValueError):
            cur_slot = 0

        spot_rows = conn.execute(
            """
            SELECT spot_id, label, polygon_json, vehicle_polygon_json, vehicle_poly_custom, ref_free_sig_b64
            FROM spots
            WHERE camera_id = ?
            ORDER BY spot_id
            """,
            (camera_id,),
        ).fetchall()
        spot_polys: List[Dict[str, Any]] = [
            {
                "spot_id": str(sr["spot_id"]),
                "label": str(sr["label"]),
                "polygon_json": sr["polygon_json"],
                "vehicle_polygon_json": sr["vehicle_polygon_json"],
                "vehicle_poly_custom": int(sr["vehicle_poly_custom"] or 0),
                "ref_free_sig_b64": sr["ref_free_sig_b64"],
            }
            for sr in spot_rows
        ]
        profiles[str(cur_slot)] = {
            "saved_at": time.time(),
            "image_width": int(cam_row["image_width"] or 1280),
            "image_height": int(cam_row["image_height"] or 720),
            "crop_roi_json": cam_row["crop_roi_json"],
            "anchor_points_json": cam_row["anchor_points_json"],
            "template_jpeg_b64": cam_row["template_jpeg_b64"],
            "template_gray_b64": cam_row["template_gray_b64"],
            "template_meta_json": cam_row["template_meta_json"],
            "vehicle_template_jpeg_b64": cam_row["vehicle_template_jpeg_b64"],
            "vehicle_template_meta_json": cam_row["vehicle_template_meta_json"],
            "full_frame_jpeg_b64": cam_row["full_frame_jpeg_b64"],
            "spots": spot_polys,
        }
        cfg["calibration_profiles"] = profiles
        conn.execute(
            "UPDATE cameras SET ingest_config_json = ? WHERE camera_id = ?",
            (json.dumps(cfg, ensure_ascii=False), camera_id),
        )

    def switch_camera_active_slot(
        self,
        camera_id: str,
        target_slot_index: int,
        reason: str = "手动切换主备镜头",
    ) -> Dict[str, Any]:
        """Switch the active ingestion camera slot (0=Primary, 1..3=Standby) and hot-swap per-slot calibration."""
        from app.camera_ingestor import (
            format_local_ts,
            normalize_ingest_config,
            resolve_slot_ingest_config,
        )

        now_ts = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT camera_id, ingest_config_json, ingest_status_json FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"未找到摄像头: {camera_id}")
            raw_cfg: Dict[str, Any] = {}
            if row["ingest_config_json"]:
                try:
                    loaded = json.loads(row["ingest_config_json"])
                    if isinstance(loaded, dict):
                        raw_cfg = loaded
                except Exception:
                    raw_cfg = {}
            cfg = normalize_ingest_config(raw_cfg)
            standbys = cfg.get("standby_slots") or []
            max_slot = len(standbys)
            target_idx = int(target_slot_index)
            if target_idx < 0 or target_idx > max_slot:
                raise ValueError(f"目标槽位 #{target_idx} 超出已配置主备范围 (0..{max_slot})")

            prev_idx = int(cfg.get("active_slot_index") or 0)
            # Snapshot current active slot's calibration before switching
            self._snapshot_active_slot_calibration_conn(conn, camera_id, force=True)

            # Re-read updated cfg with latest calibration_profiles
            refreshed = conn.execute(
                "SELECT ingest_config_json FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            if refreshed and refreshed["ingest_config_json"]:
                try:
                    cfg = json.loads(refreshed["ingest_config_json"])
                except Exception:
                    pass

            profiles = cfg.get("calibration_profiles")
            if not isinstance(profiles, dict):
                profiles = {}
            target_prof = profiles.get(str(target_idx))
            restored_profile = False
            if isinstance(target_prof, dict):
                restored_profile = True
                conn.execute(
                    """
                    UPDATE cameras
                    SET image_width = ?,
                        image_height = ?,
                        crop_roi_json = ?,
                        anchor_points_json = ?,
                        template_jpeg_b64 = ?,
                        template_gray_b64 = ?,
                        template_meta_json = ?,
                        vehicle_template_jpeg_b64 = ?,
                        vehicle_template_meta_json = ?,
                        full_frame_jpeg_b64 = ?
                    WHERE camera_id = ?
                    """,
                    (
                        int(target_prof.get("image_width") or 1280),
                        int(target_prof.get("image_height") or 720),
                        target_prof.get("crop_roi_json"),
                        target_prof.get("anchor_points_json"),
                        target_prof.get("template_jpeg_b64"),
                        target_prof.get("template_gray_b64"),
                        target_prof.get("template_meta_json"),
                        target_prof.get("vehicle_template_jpeg_b64"),
                        target_prof.get("vehicle_template_meta_json"),
                        target_prof.get("full_frame_jpeg_b64"),
                        camera_id,
                    ),
                )
                saved_spots = target_prof.get("spots")
                if isinstance(saved_spots, list) and saved_spots:
                    cur_spots = [
                        dict(sr)
                        for sr in conn.execute(
                            "SELECT spot_id, label FROM spots WHERE camera_id = ? ORDER BY spot_id",
                            (camera_id,),
                        ).fetchall()
                    ]
                    by_id = {str(s["spot_id"]): s for s in saved_spots if isinstance(s, dict) and s.get("spot_id")}
                    by_label = {str(s["label"]): s for s in saved_spots if isinstance(s, dict) and s.get("label")}
                    for idx_sp, cur_sp in enumerate(cur_spots):
                        sid = str(cur_sp["spot_id"])
                        slbl = str(cur_sp["label"])
                        hit = by_id.get(sid) or by_label.get(slbl)
                        if hit is None and idx_sp < len(saved_spots) and isinstance(saved_spots[idx_sp], dict):
                            hit = saved_spots[idx_sp]
                        if hit and hit.get("polygon_json"):
                            conn.execute(
                                """
                                UPDATE spots
                                SET polygon_json = ?,
                                    vehicle_polygon_json = COALESCE(?, vehicle_polygon_json),
                                    vehicle_poly_custom = ?,
                                    ref_free_sig_b64 = ?
                                WHERE spot_id = ?
                                """,
                                (
                                    hit["polygon_json"],
                                    hit.get("vehicle_polygon_json"),
                                    int(hit.get("vehicle_poly_custom") or 0),
                                    hit.get("ref_free_sig_b64"),
                                    sid,
                                ),
                            )

            cfg["active_slot_index"] = target_idx
            _, target_label, _ = resolve_slot_ingest_config(cfg, target_idx)
            conn.execute(
                "UPDATE cameras SET ingest_config_json = ? WHERE camera_id = ?",
                (json.dumps(cfg, ensure_ascii=False), camera_id),
            )

            st_merged: Dict[str, Any] = {"state": "idle"}
            if row["ingest_status_json"]:
                try:
                    loaded_st = json.loads(row["ingest_status_json"])
                    if isinstance(loaded_st, dict):
                        st_merged.update(loaded_st)
                except Exception:
                    pass
            failover_cnt = int(st_merged.get("failover_count") or 0)
            if target_idx != prev_idx:
                failover_cnt += 1
            st_merged.update(
                {
                    "active_slot_index": target_idx,
                    "active_slot_label": target_label,
                    "standby_count": max_slot,
                    "failover_active": bool(target_idx > 0),
                    "consecutive_failures": 0,
                    "last_failover_at": format_local_ts(now_ts),
                    "last_failover_ts": now_ts,
                    "last_failover_reason": str(reason or "切换主备镜头")[:200],
                    "failover_count": failover_cnt,
                }
            )
            conn.execute(
                "UPDATE cameras SET ingest_status_json = ? WHERE camera_id = ?",
                (json.dumps(st_merged, ensure_ascii=False), camera_id),
            )

        cam_info = self.get_camera(camera_id, include_blobs=False) or {"camera_id": camera_id}
        cam_info["switched_from_slot"] = prev_idx
        cam_info["active_slot_index"] = target_idx
        cam_info["active_slot_label"] = target_label
        cam_info["restored_calibration_profile"] = restored_profile
        return cam_info

    def save_camera_calib_schedule(
        self, camera_id: str, schedule_payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        cur_cam = self.get_camera(camera_id, include_blobs=False)
        if not cur_cam:
            raise ValueError(f"未找到相机机位: {camera_id}")
        merged = {**DEFAULT_CALIB_SCHEDULE, **(cur_cam.get("calib_schedule") or {})}
        if "enabled" in schedule_payload:
            merged["enabled"] = bool(schedule_payload["enabled"])
        if "mode" in schedule_payload:
            mode = str(schedule_payload["mode"]).strip().lower()
            if mode in ("nightly", "interval"):
                merged["mode"] = mode
        if "interval_hours" in schedule_payload:
            merged["interval_hours"] = round(
                max(0.05, min(168.0, float(schedule_payload["interval_hours"]))), 3
            )
        raw_time_val = schedule_payload.get("night_time") or schedule_payload.get("nightly_time")
        if raw_time_val is not None:
            raw_t = str(raw_time_val).strip()
            if re.match(r"^\d{1,2}:\d{2}$", raw_t):
                hh, mm = raw_t.split(":")
                norm_t = f"{max(0, min(23, int(hh))):02d}:{max(0, min(59, int(mm))):02d}"
                merged["nightly_time"] = norm_t
                merged["night_time"] = norm_t
        if "auto_compensate_shift" in schedule_payload or "auto_compensate" in schedule_payload:
            ac_flag = bool(
                schedule_payload["auto_compensate_shift"]
                if "auto_compensate_shift" in schedule_payload
                else schedule_payload["auto_compensate"]
            )
            merged["auto_compensate"] = ac_flag
            merged["auto_compensate_shift"] = ac_flag
        if "max_auto_shift_px" in schedule_payload:
            merged["max_auto_shift_px"] = round(
                max(4.0, min(80.0, float(schedule_payload["max_auto_shift_px"]))), 1
            )

        now = time.time()
        next_ts = compute_next_calib_check_ts(merged, now)
        merged["next_check_ts"] = next_ts
        health = dict(cur_cam.get("calib_health") or {})
        health["next_check_at"] = next_ts
        if not health.get("status"):
            health["status"] = "scheduled" if merged["enabled"] else "disabled"
            health["message"] = (
                "已启用定时/夜间自动标定偏移核对"
                if merged["enabled"]
                else "自动标定偏移核对已关闭"
            )

        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET calib_schedule_json = ?, calib_health_json = ? WHERE camera_id = ?",
                (
                    json.dumps(merged, ensure_ascii=False),
                    json.dumps(health, ensure_ascii=False),
                    camera_id,
                ),
            )
        return self.get_camera(camera_id, include_blobs=False)

    def save_camera_calib_health(
        self, camera_id: str, health_payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        cur_cam = self.get_camera(camera_id, include_blobs=False)
        if not cur_cam:
            return None
        prev_health = dict(cur_cam.get("calib_health") or {})
        hist = list(prev_health.get("history") or [])
        entry_summary = {
            "checked_at": health_payload.get("checked_at", health_payload.get("last_checked_at", time.time())),
            "status": health_payload.get("status", "healthy"),
            "shift_px": health_payload.get("shift_px", 0.0),
            "drift_px": health_payload.get("drift_px", health_payload.get("shift_px", 0.0)),
            "dx_px": health_payload.get("dx_px", 0.0),
            "dy_px": health_payload.get("dy_px", 0.0),
            "source": health_payload.get("source", "manual"),
        }
        hist.insert(0, entry_summary)
        del hist[8:]
        merged = {**prev_health, **health_payload, "history": hist}
        sched_cur = dict(cur_cam.get("calib_schedule") or DEFAULT_CALIB_SCHEDULE)
        if health_payload.get("next_check_at") is not None:
            sched_cur["next_check_ts"] = health_payload["next_check_at"]
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET calib_health_json = ?, calib_schedule_json = ? WHERE camera_id = ?",
                (
                    json.dumps(merged, ensure_ascii=False),
                    json.dumps(sched_cur, ensure_ascii=False),
                    camera_id,
                ),
            )
        return self.get_camera(camera_id, include_blobs=False)

    def shift_camera_spot_polygons(
        self,
        camera_id: str,
        dx_px: float,
        dy_px: float,
        cal_w: Optional[int] = None,
        cal_h: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        from app.homography import derive_vehicle_envelope_polygon

        with self._lock, self._connect() as conn:
            cam_row = conn.execute(
                "SELECT image_width, image_height FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
            if not cam_row:
                return []
            cw = max(64, int(cal_w or cam_row["image_width"] or 1280))
            ch = max(64, int(cal_h or cam_row["image_height"] or 720))
            rows = conn.execute(
                "SELECT spot_id, polygon_json, vehicle_polygon_json, vehicle_poly_custom FROM spots WHERE camera_id = ?",
                (camera_id,),
            ).fetchall()
            for r in rows:
                gpoly = json.loads(r["polygon_json"])
                shifted_g = [
                    [
                        round(max(0.0, min(float(cw - 1), float(pt[0]) + dx_px)), 1),
                        round(max(0.0, min(float(ch - 1), float(pt[1]) + dy_px)), 1),
                    ]
                    for pt in gpoly
                ]
                raw_vp = r["vehicle_polygon_json"]
                is_cust = int(r["vehicle_poly_custom"] or 0)
                if raw_vp and is_cust:
                    try:
                        vp = json.loads(raw_vp)
                        shifted_v = [
                            [
                                round(max(0.0, min(float(cw - 1), float(pt[0]) + dx_px)), 1),
                                round(max(0.0, min(float(ch - 1), float(pt[1]) + dy_px)), 1),
                            ]
                            for pt in vp
                        ]
                    except Exception:
                        shifted_v = derive_vehicle_envelope_polygon(shifted_g, img_w=cw, img_h=ch)
                else:
                    shifted_v = derive_vehicle_envelope_polygon(shifted_g, img_w=cw, img_h=ch)
                conn.execute(
                    "UPDATE spots SET polygon_json = ?, vehicle_polygon_json = ? WHERE spot_id = ?",
                    (json.dumps(shifted_g), json.dumps(shifted_v), r["spot_id"]),
                )
        return self.list_spots(camera_id=camera_id)

    def get_camera_vehicle_template(self, camera_id: str) -> Optional[Dict[str, Any]]:
        cam = self.get_camera(camera_id, include_blobs=True)
        if not cam:
            return None
        return {
            "camera_id": cam["camera_id"],
            "has_vehicle_template": bool(cam.get("has_vehicle_template")),
            "vehicle_template_jpeg_b64": cam.get("vehicle_template_jpeg_b64") or "",
            "vehicle_template_meta": cam.get("vehicle_template_meta") or {},
        }

    def _ensure_zone_exists_locked(
        self, conn: sqlite3.Connection, zone_id: str, zone_name_hint: str = ""
    ) -> str:
        zid = str(zone_id or "").strip() or "Z_MAP_CUSTOM"
        row = conn.execute("SELECT zone_id FROM zones WHERE zone_id = ?", (zid,)).fetchone()
        if not row:
            zname = zone_name_hint.strip() if zone_name_hint else f"{zid} 停车片区"
            conn.execute(
                """
                INSERT INTO zones (
                    zone_id, name, dead_end_penalty, gate_access,
                    map_x, map_y, map_w, map_h, description, layout_angle, is_demo_seed
                ) VALUES (?, ?, 0.0, 'south,east', 42.0, 50.0, 14.0, 7.0, '机位关联片区', 0.0, 0)
                """,
                (zid, zname),
            )
        return zid

    def _scaffold_or_bind_camera_groups_locked(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
        zone_id: str,
        parsed_groups: List[Dict[str, Any]],
        bind_group_codes: Optional[List[str]] = None,
        scaffold_spots: bool = False,
        image_width: int = 1280,
        image_height: int = 720,
    ) -> int:
        from app.homography import derive_vehicle_envelope_polygon

        now = time.time()
        codes_to_bind = [
            str(c).strip().upper()
            for c in (bind_group_codes if bind_group_codes is not None else [g["code"] for g in parsed_groups])
            if str(c).strip()
        ]
        bound_count = 0
        for gcode in codes_to_bind:
            cur = conn.execute(
                """
                UPDATE spots
                SET camera_id = ?, zone_id = ?,
                    status = CASE WHEN status = 'UNKNOWN' THEN 'FREE' ELSE status END,
                    free_units = CASE WHEN status = 'UNKNOWN' THEN capacity ELSE free_units END,
                    last_reason = CASE WHEN status = 'UNKNOWN' THEN '已绑定机位，待实景标定/抓拍' ELSE last_reason END
                WHERE UPPER(group_code) = ? AND (camera_id = 'UNASSIGNED' OR camera_id LIKE 'CAM_MAP_%')
                """,
                (camera_id, zone_id, gcode),
            )
            bound_count += int(cur.rowcount or 0)

        if scaffold_spots and parsed_groups:
            existing_rows = conn.execute(
                "SELECT spot_id, group_code FROM spots WHERE camera_id = ?", (camera_id,)
            ).fetchall()
            existing_by_grp: Dict[str, int] = {}
            for r in existing_rows:
                gc = str(r["group_code"] or "").upper()
                existing_by_grp[gc] = existing_by_grp.get(gc, 0) + 1

            total_groups = len(parsed_groups)
            for g_idx, grp in enumerate(parsed_groups):
                gcode = grp["code"]
                if existing_by_grp.get(gcode, 0) > 0:
                    continue
                cnt = int(grp["count"])
                orient = grp["orient_code"]
                v_model = grp["vehicle_model"]
                mw = float(grp["map_w"])
                mh = float(grp["map_h"])
                # Lay out initial calibration polygons cleanly per group band
                band_y0 = int(image_height * (0.50 if g_idx == 0 else max(0.18, 0.50 - 0.26 * g_idx)))
                band_y1 = min(int(image_height * 0.88), band_y0 + int(image_height * 0.22))
                margin_x = int(image_width * 0.08)
                usable_w = max(160, image_width - 2 * margin_x)
                slot_px = max(36, usable_w // max(1, cnt))

                for i in range(cnt):
                    sid = f"{camera_id}_{gcode}_{i + 1:02d}"
                    lbl = f"{gcode}-{i + 1:02d}" if total_groups > 1 else f"{i + 1:02d}"
                    x0 = margin_x + i * slot_px + 3
                    x1 = margin_x + (i + 1) * slot_px - 3
                    gpoly = [[x0, band_y0], [x1, band_y0], [x1, band_y1], [x0, band_y1]]
                    vpoly = derive_vehicle_envelope_polygon(gpoly, img_w=image_width, img_h=image_height)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO spots (
                            spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                            polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                            pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                            obstacle_type, action_hint, map_w, map_h, map_angle, map_custom,
                            vehicle_polygon_json, vehicle_poly_custom, group_code, vehicle_model
                        ) VALUES (
                            ?, ?, ?, ?, 'standard', 0, 5.2, 1,
                            ?, 'FREE', 1, 0, NULL, NULL,
                            NULL, 1.0, '按机位编码自动生成初始车位', 'scaffold', ?, ?,
                            'none', '', ?, ?, ?, 0,
                            ?, 0, ?, ?
                        )
                        """,
                        (
                            sid,
                            camera_id,
                            zone_id,
                            lbl,
                            json.dumps(gpoly),
                            now,
                            now,
                            mw,
                            mh,
                            0.0 if orient == "V" else 90.0,
                            json.dumps(vpoly),
                            gcode,
                            v_model,
                        ),
                    )
                    bound_count += 1
        return bound_count

    def upsert_camera(
        self,
        camera_id: str,
        name: str,
        zone_id: str,
        mount_type: str = "业主窗台Wi-Fi",
        image_width: int = 1280,
        image_height: int = 720,
        group_code: str = "",
        mount_address: str = "",
        auto_name: bool = False,
        scaffold_spots: bool = False,
        bind_group_codes: Optional[List[str]] = None,
        ingest_config: Optional[Dict[str, Any]] = None,
        bind_spot_group: Optional[str] = None,
        unbind_spot_groups: bool = False,
        group_name: Optional[str] = None,
        near_unit_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.camera_ingestor import normalize_ingest_config
        from app.camera_naming import parse_camera_group_codes

        effective_raw_code = str(bind_spot_group if bind_spot_group is not None else group_code or "").strip()
        if effective_raw_code in {"__UNBIND__", "NONE", "UNASSIGNED"}:
            effective_raw_code = ""
        parsed = parse_camera_group_codes(effective_raw_code, mount_address)
        norm_code = parsed["normalized_code"] if parsed["groups"] else effective_raw_code.upper()
        clean_addr = parsed["mount_address"]
        cam_id_clean = str(camera_id or parsed["suggested_camera_id"] or "").strip()
        if not cam_id_clean:
            raise ValueError("camera_id 不能为空")
        final_name = str(name or "").strip()
        if (not final_name or auto_name) and parsed["standard_name"]:
            final_name = parsed["standard_name"]
        if not final_name:
            final_name = cam_id_clean

        target_zone = str(zone_id or parsed["suggested_zone_id"] or "Z_MAP_CUSTOM").strip()

        with self._lock, self._connect() as conn:
            self._ensure_zone_exists_locked(conn, target_zone, parsed["suggested_zone_name"])
            existing = conn.execute(
                "SELECT camera_id, ingest_config_json FROM cameras WHERE camera_id = ?",
                (cam_id_clean,),
            ).fetchone()
            merged_ingest_json: Optional[str] = None
            if isinstance(ingest_config, dict):
                prev_ic: Dict[str, Any] = {}
                if existing and existing["ingest_config_json"]:
                    try:
                        loaded_ic = json.loads(existing["ingest_config_json"])
                        if isinstance(loaded_ic, dict):
                            prev_ic = loaded_ic
                    except Exception:
                        prev_ic = {}
                merged_ingest_json = json.dumps(
                    normalize_ingest_config(ingest_config, prev_ic),
                    ensure_ascii=False,
                )
            elif not existing:
                merged_ingest_json = json.dumps(
                    normalize_ingest_config({}),
                    ensure_ascii=False,
                )

            if existing:
                if merged_ingest_json is not None:
                    conn.execute(
                        """
                        UPDATE cameras
                        SET name = ?, zone_id = ?, mount_type = ?, image_width = ?, image_height = ?,
                            group_code = ?, mount_address = ?, ingest_config_json = ?
                        WHERE camera_id = ?
                        """,
                        (
                            final_name,
                            target_zone,
                            mount_type,
                            image_width,
                            image_height,
                            norm_code,
                            clean_addr,
                            merged_ingest_json,
                            cam_id_clean,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE cameras
                        SET name = ?, zone_id = ?, mount_type = ?, image_width = ?, image_height = ?,
                            group_code = ?, mount_address = ?
                        WHERE camera_id = ?
                        """,
                        (
                            final_name,
                            target_zone,
                            mount_type,
                            image_width,
                            image_height,
                            norm_code,
                            clean_addr,
                            cam_id_clean,
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO cameras (
                        camera_id, name, zone_id, mount_type, image_width, image_height,
                        last_heartbeat, frames_received, group_code, mount_address, ingest_config_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        cam_id_clean,
                        final_name,
                        target_zone,
                        mount_type,
                        image_width,
                        image_height,
                        time.time(),
                        norm_code,
                        clean_addr,
                        merged_ingest_json,
                    ),
                )

            explicit_bind = str(bind_spot_group or "").strip()
            if unbind_spot_groups or explicit_bind == "__UNBIND__":
                self._unbind_camera_all_groups_conn(conn, cam_id_clean)
            elif explicit_bind and explicit_bind not in {"NONE", "UNASSIGNED"}:
                _, grp_rows = self._resolve_group_spots_conn(conn, explicit_bind)
                if grp_rows:
                    self._bind_camera_to_group_conn(
                        conn,
                        camera_id=cam_id_clean,
                        group_code=explicit_bind,
                        group_name=group_name,
                        near_unit_id=near_unit_id,
                        zone_id=target_zone,
                    )
                elif norm_code or bind_group_codes or scaffold_spots:
                    self._scaffold_or_bind_camera_groups_locked(
                        conn,
                        camera_id=cam_id_clean,
                        zone_id=target_zone,
                        parsed_groups=parsed["groups"],
                        bind_group_codes=bind_group_codes,
                        scaffold_spots=scaffold_spots,
                        image_width=image_width,
                        image_height=image_height,
                    )
            elif norm_code or bind_group_codes or scaffold_spots:
                self._scaffold_or_bind_camera_groups_locked(
                    conn,
                    camera_id=cam_id_clean,
                    zone_id=target_zone,
                    parsed_groups=parsed["groups"],
                    bind_group_codes=bind_group_codes,
                    scaffold_spots=scaffold_spots,
                    image_width=image_width,
                    image_height=image_height,
                )
            self._snapshot_active_slot_calibration_conn(conn, cam_id_clean)

        if explicit_bind or unbind_spot_groups:
            self.reconcile_spot_groups_and_doors()
        else:
            self.recompute_unit_zone_distances()
        return self.get_camera(cam_id_clean)  # type: ignore

    def update_camera(self, camera_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.camera_ingestor import normalize_ingest_config
        from app.camera_naming import parse_camera_group_codes

        explicit_bind_raw = payload.get("bind_spot_group")
        unbind_flag = bool(payload.get("unbind_spot_groups")) or str(explicit_bind_raw or "").strip() == "__UNBIND__"

        with self._lock, self._connect() as conn:
            cur_row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            if not cur_row:
                raise ValueError(f"机位 {camera_id} 不存在")
            cur = dict(cur_row)

            new_cam_id = str(payload.get("new_camera_id") or payload.get("camera_id") or camera_id).strip()
            if not new_cam_id:
                raise ValueError("机位编号不能为空")
            if new_cam_id != camera_id:
                dup = conn.execute(
                    "SELECT camera_id FROM cameras WHERE camera_id = ?", (new_cam_id,)
                ).fetchone()
                if dup:
                    raise ValueError(f"目标机位编号 {new_cam_id} 已存在，请更换其他编号")

            if unbind_flag:
                raw_code = ""
            elif explicit_bind_raw is not None and str(explicit_bind_raw).strip() not in {"", "NONE", "UNASSIGNED", "__UNBIND__"}:
                raw_code = str(explicit_bind_raw).strip()
            else:
                raw_code = str(
                    payload["group_code"] if "group_code" in payload else (cur.get("group_code") or "")
                ).strip()
            raw_addr = str(
                payload["mount_address"] if "mount_address" in payload else (cur.get("mount_address") or "")
            ).strip()
            parsed = parse_camera_group_codes(raw_code, raw_addr)
            norm_code = parsed["normalized_code"] if parsed["groups"] else raw_code.upper()
            clean_addr = parsed["mount_address"]

            auto_name = bool(
                payload.get(
                    "auto_name",
                    ("group_code" in payload or "mount_address" in payload) and "name" not in payload,
                )
            )
            req_name = str(payload.get("name") if "name" in payload else cur["name"]).strip()
            if (auto_name or not req_name) and parsed["standard_name"]:
                final_name = parsed["standard_name"]
            else:
                final_name = req_name or cur["name"]

            target_zone = str(payload.get("zone_id") or cur["zone_id"]).strip()
            self._ensure_zone_exists_locked(conn, target_zone, parsed["suggested_zone_name"])
            mount_type = str(payload.get("mount_type") or cur["mount_type"]).strip()
            img_w = int(payload.get("image_width") or cur["image_width"] or 1280)
            img_h = int(payload.get("image_height") or cur["image_height"] or 720)

            merged_ingest_json: Optional[str] = None
            if isinstance(payload.get("ingest_config"), dict):
                prev_ic: Dict[str, Any] = {}
                if cur.get("ingest_config_json"):
                    try:
                        loaded_ic = json.loads(cur["ingest_config_json"])
                        if isinstance(loaded_ic, dict):
                            prev_ic = loaded_ic
                    except Exception:
                        prev_ic = {}
                merged_ingest_json = json.dumps(
                    normalize_ingest_config(payload["ingest_config"], prev_ic),
                    ensure_ascii=False,
                )

            if merged_ingest_json is not None:
                conn.execute(
                    """
                    UPDATE cameras
                    SET camera_id = ?, name = ?, zone_id = ?, mount_type = ?,
                        image_width = ?, image_height = ?, group_code = ?, mount_address = ?,
                        ingest_config_json = ?
                    WHERE camera_id = ?
                    """,
                    (
                        new_cam_id,
                        final_name,
                        target_zone,
                        mount_type,
                        img_w,
                        img_h,
                        norm_code,
                        clean_addr,
                        merged_ingest_json,
                        camera_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE cameras
                    SET camera_id = ?, name = ?, zone_id = ?, mount_type = ?,
                        image_width = ?, image_height = ?, group_code = ?, mount_address = ?
                    WHERE camera_id = ?
                    """,
                    (
                        new_cam_id,
                        final_name,
                        target_zone,
                        mount_type,
                        img_w,
                        img_h,
                        norm_code,
                        clean_addr,
                        camera_id,
                    ),
                )

            if new_cam_id != camera_id or target_zone != cur["zone_id"]:
                conn.execute(
                    "UPDATE spots SET camera_id = ?, zone_id = ? WHERE camera_id = ?",
                    (new_cam_id, target_zone, camera_id),
                )

            explicit_bind_clean = str(explicit_bind_raw or "").strip()
            if unbind_flag:
                self._unbind_camera_all_groups_conn(conn, new_cam_id)
            elif explicit_bind_clean and explicit_bind_clean not in {"NONE", "UNASSIGNED", "__UNBIND__"}:
                _, grp_rows = self._resolve_group_spots_conn(conn, explicit_bind_clean)
                if grp_rows:
                    # Detach previous floor-plan group if switching to a different single group
                    old_bound_rows = conn.execute(
                        """
                        SELECT DISTINCT group_code
                        FROM spots
                        WHERE camera_id = ? AND map_x IS NOT NULL AND map_y IS NOT NULL
                          AND COALESCE(group_code, '') != ''
                        """,
                        (new_cam_id,),
                    ).fetchall()
                    for obr in old_bound_rows:
                        ogc = str(obr["group_code"] or "").strip()
                        if ogc and ogc.upper() != explicit_bind_clean.upper():
                            self._detach_group_from_camera_conn(conn, ogc, time.time())
                    self._bind_camera_to_group_conn(
                        conn,
                        camera_id=new_cam_id,
                        group_code=explicit_bind_clean,
                        group_name=payload.get("group_name"),
                        near_unit_id=payload.get("near_unit_id"),
                        zone_id=target_zone,
                    )
                else:
                    self._scaffold_or_bind_camera_groups_locked(
                        conn,
                        camera_id=new_cam_id,
                        zone_id=target_zone,
                        parsed_groups=parsed["groups"],
                        bind_group_codes=payload.get("bind_group_codes"),
                        scaffold_spots=bool(payload.get("scaffold_spots", False)),
                        image_width=img_w,
                        image_height=img_h,
                    )
            elif payload.get("bind_group_codes") is not None or payload.get("scaffold_spots") or norm_code:
                self._scaffold_or_bind_camera_groups_locked(
                    conn,
                    camera_id=new_cam_id,
                    zone_id=target_zone,
                    parsed_groups=parsed["groups"],
                    bind_group_codes=payload.get("bind_group_codes"),
                    scaffold_spots=bool(payload.get("scaffold_spots", False)),
                    image_width=img_w,
                    image_height=img_h,
                )
            self._snapshot_active_slot_calibration_conn(conn, new_cam_id)

        if explicit_bind_raw is not None or unbind_flag:
            self.reconcile_spot_groups_and_doors()
        else:
            self.recompute_unit_zone_distances()
        return self.get_camera(new_cam_id)  # type: ignore

    def delete_camera(self, camera_id: str, spot_action: str = "detach") -> Dict[str, Any]:
        action = str(spot_action or "detach").strip().lower()
        with self._lock, self._connect() as conn:
            cur_row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            if not cur_row:
                raise ValueError(f"机位 {camera_id} 不存在")
            spot_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS cnt FROM spots WHERE camera_id = ?", (camera_id,)
                ).fetchone()["cnt"]
            )
            detached = 0
            deleted_spots = 0
            if action == "delete":
                conn.execute("DELETE FROM spots WHERE camera_id = ?", (camera_id,))
                deleted_spots = spot_count
            else:
                # Detach spots onto floor plan as unbound stalls so floor-plan layout is preserved
                conn.execute(
                    """
                    UPDATE spots
                    SET camera_id = 'UNASSIGNED',
                        status = 'UNKNOWN',
                        free_units = 0,
                        occupied_units = 0,
                        pending_status = NULL,
                        pending_free_units = NULL,
                        pending_since = NULL,
                        last_reason = '机位已移除，保留平面图车位待后续绑定新机位'
                    WHERE camera_id = ?
                    """,
                    (camera_id,),
                )
                detached = spot_count
            conn.execute("DELETE FROM cameras WHERE camera_id = ?", (camera_id,))
        return {
            "deleted": True,
            "deleted_camera_id": camera_id,
            "spot_action": action,
            "detached_spots": detached,
            "deleted_spots": deleted_spots,
        }

    def record_camera_heartbeat(
        self, camera_id: str, width: Optional[int] = None, height: Optional[int] = None, ts: Optional[float] = None
    ) -> None:
        now = ts if ts is not None else time.time()
        with self._lock, self._connect() as conn:
            if width and height:
                conn.execute(
                    """
                    UPDATE cameras
                    SET last_heartbeat = ?, frames_received = frames_received + 1,
                        image_width = ?, image_height = ?
                    WHERE camera_id = ?
                    """,
                    (now, width, height, camera_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE cameras
                    SET last_heartbeat = ?, frames_received = frames_received + 1
                    WHERE camera_id = ?
                    """,
                    (now, camera_id),
                )

    @staticmethod
    def _natural_sort_tokens(text: str) -> Tuple[Any, ...]:
        parts = re.split(r"(\d+)", str(text or ""))
        return tuple(int(p) if p.isdigit() else p.lower() for p in parts)

    @classmethod
    def _spot_sort_key(cls, item: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            str(item.get("camera_id") or ""),
            cls._natural_sort_tokens(str(item.get("group_code") or "")),
            cls._natural_sort_tokens(str(item.get("label") or "")),
            cls._natural_sort_tokens(str(item.get("spot_id") or "")),
        )

    @staticmethod
    def _build_placeholder_polygons(
        count: int, img_w: int = 1280, img_h: int = 720
    ) -> List[List[List[int]]]:
        if count <= 0:
            return []
        w = max(320, int(img_w or 1280))
        h = max(240, int(img_h or 720))
        max_per_row = 8
        rows_cnt = max(1, math.ceil(count / max_per_row))
        cols_cnt = max(1, math.ceil(count / rows_cnt))
        pad_x = max(24, int(round(w * 0.05)))
        usable_w = max(160, w - 2 * pad_x)
        slot_w = usable_w / cols_cnt
        gap_x = max(6, min(18, int(round(slot_w * 0.10))))
        box_w = max(36, int(round(slot_w - gap_x)))

        top_y = int(round(h * (0.28 if rows_cnt == 1 else 0.18)))
        bot_y = int(round(h * (0.62 if rows_cnt == 1 else 0.80)))
        usable_h = max(100, bot_y - top_y)
        slot_h = usable_h / rows_cnt
        gap_y = 0 if rows_cnt == 1 else max(12, int(round(slot_h * 0.12)))
        box_h = max(70, int(round(slot_h - gap_y)))

        polys: List[List[List[int]]] = []
        for idx in range(count):
            r_idx = idx // cols_cnt
            c_idx = idx % cols_cnt
            x0 = int(round(pad_x + c_idx * slot_w + (slot_w - box_w) * 0.5))
            x1 = min(w - 8, x0 + box_w)
            y0 = int(round(top_y + r_idx * slot_h))
            y1 = min(h - 8, y0 + box_h)
            polys.append([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        return polys

    @classmethod
    def _normalize_camera_map_placeholders_conn(
        cls, conn: sqlite3.Connection, camera_id: str, force: bool = False
    ) -> bool:
        if not camera_id or camera_id == "UNASSIGNED":
            return False
        cam_row = conn.execute(
            "SELECT image_width, image_height FROM cameras WHERE camera_id = ?",
            (camera_id,),
        ).fetchone()
        if not cam_row:
            return False
        cw = max(320, int(cam_row["image_width"] or 1280))
        ch = max(240, int(cam_row["image_height"] or 720))
        sp_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT spot_id, camera_id, group_code, label, polygon_json, last_engine FROM spots WHERE camera_id = ?",
                (camera_id,),
            ).fetchall()
        ]
        if not sp_rows or any(str(r.get("last_engine") or "") != "map_editor" for r in sp_rows):
            return False
        sp_rows.sort(key=cls._spot_sort_key)

        needs_fix = force
        if not needs_fix:
            seen_polys = set()
            for r in sp_rows:
                raw_p = str(r.get("polygon_json") or "")
                if raw_p in seen_polys:
                    needs_fix = True
                    break
                seen_polys.add(raw_p)
                try:
                    pts = json.loads(raw_p)
                except Exception:
                    needs_fix = True
                    break
                if not isinstance(pts, list) or len(pts) < 3:
                    needs_fix = True
                    break
                for pt in pts:
                    if pt[0] < 0 or pt[0] > cw - 4 or pt[1] < 0 or pt[1] > ch - 4:
                        needs_fix = True
                        break
                if needs_fix:
                    break

        if not needs_fix:
            return False

        clean_polys = cls._build_placeholder_polygons(len(sp_rows), cw, ch)
        for r, poly in zip(sp_rows, clean_polys):
            conn.execute(
                "UPDATE spots SET polygon_json = ?, vehicle_polygon_json = NULL, vehicle_poly_custom = 0 WHERE spot_id = ?",
                (json.dumps(poly), r["spot_id"]),
            )
        return True

    def list_spots(
        self,
        camera_id: Optional[str] = None,
        zone_id: Optional[str] = None,
        include_signatures: bool = False,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM spots WHERE 1=1"
        params: List[Any] = []
        if camera_id:
            query += " AND camera_id = ?"
            params.append(camera_id)
        if zone_id:
            query += " AND zone_id = ?"
            params.append(zone_id)
        query += " ORDER BY spot_id"

        with self._lock, self._connect() as conn:
            if camera_id and camera_id != "UNASSIGNED":
                self._normalize_camera_map_placeholders_conn(conn, camera_id, force=False)
            else:
                cam_ids_to_check = [
                    r["camera_id"]
                    for r in conn.execute(
                        "SELECT DISTINCT camera_id FROM spots WHERE last_engine = 'map_editor' AND camera_id != 'UNASSIGNED'"
                    ).fetchall()
                ]
                for cid_chk in cam_ids_to_check:
                    self._normalize_camera_map_placeholders_conn(conn, cid_chk, force=False)

            cam_dims = {
                r["camera_id"]: (int(r["image_width"] or 1280), int(r["image_height"] or 720))
                for r in conn.execute("SELECT camera_id, image_width, image_height FROM cameras").fetchall()
            }
            rows = conn.execute(query, params).fetchall()
            result = []
            from app.homography import derive_vehicle_envelope_polygon

            for r in rows:
                d = dict(r)
                d["polygon"] = json.loads(d.pop("polygon_json"))
                raw_vpoly = d.pop("vehicle_polygon_json", None)
                if raw_vpoly:
                    try:
                        parsed_vp = json.loads(raw_vpoly)
                    except Exception:
                        parsed_vp = None
                else:
                    parsed_vp = None
                if parsed_vp and len(parsed_vp) >= 3:
                    d["vehicle_polygon"] = parsed_vp
                else:
                    cw, ch = cam_dims.get(d["camera_id"], (1280, 720))
                    d["vehicle_polygon"] = derive_vehicle_envelope_polygon(
                        d["polygon"], img_w=cw, img_h=ch
                    )
                d["vehicle_poly_custom"] = bool(d.get("vehicle_poly_custom"))
                d["compact_only"] = bool(d["compact_only"])
                d["group_code"] = str(d.get("group_code") or "")
                d["group_name"] = str(d.get("group_name") or "")
                d["near_unit_id"] = str(d.get("near_unit_id") or "")
                d["near_unit_custom"] = int(d.get("near_unit_custom") or 0)
                d["vehicle_model"] = str(d.get("vehicle_model") or "sedan")
                d["has_ref_signature"] = bool(d.get("ref_free_sig_b64"))
                if not include_signatures:
                    d.pop("ref_free_sig_b64", None)
                result.append(d)
            result.sort(key=self._spot_sort_key)
            return result

    @staticmethod
    def _extract_camera_group_tokens(camera_id: str) -> List[str]:
        raw = str(camera_id or "").strip().upper()
        if not raw or raw == "UNASSIGNED":
            return []
        ignore = {
            "CAM", "MAP", "WIN", "POLE", "GATE", "NORTH", "SOUTH",
            "EAST", "WEST", "4G", "5G", "WIFI", "HD", "IPC", "UNASSIGNED",
        }
        tokens: List[str] = []
        for part in re.split(r"[_\-\s]+", raw):
            p = part.strip()
            if not p or p in ignore or p.isdigit() or re.fullmatch(r"\d{1,2}F", p):
                continue
            if any(ch.isdigit() for ch in p) and any(ch.isalpha() for ch in p) and 2 <= len(p) <= 12:
                if p not in tokens:
                    tokens.append(p)
        return tokens

    def _mint_unique_spot_id_conn(
        self,
        conn: sqlite3.Connection,
        preferred_id: str,
        fallback_prefix: str,
        reserved_ids: set[str],
    ) -> str:
        cand = str(preferred_id or "").strip()
        if cand and cand not in reserved_ids:
            exists = conn.execute("SELECT 1 FROM spots WHERE spot_id = ?", (cand,)).fetchone()
            if not exists:
                reserved_ids.add(cand)
                return cand
        for k in range(1, 1000):
            alt = f"{fallback_prefix}{k:02d}"
            if alt not in reserved_ids:
                exists = conn.execute("SELECT 1 FROM spots WHERE spot_id = ?", (alt,)).fetchone()
                if not exists:
                    reserved_ids.add(alt)
                    return alt
        ts_tag = int(time.time() * 1000) % 1000000
        for k in range(1, 1000):
            alt = f"{fallback_prefix}{ts_tag}_{k}"
            if alt not in reserved_ids:
                exists = conn.execute("SELECT 1 FROM spots WHERE spot_id = ?", (alt,)).fetchone()
                if not exists:
                    reserved_ids.add(alt)
                    return alt
        raise RuntimeError(f"无法生成唯一车位 ID: {fallback_prefix}")

    def _rename_spot_id_to_map_placeholder_conn(
        self,
        conn: sqlite3.Connection,
        spot_id: str,
        group_code: str,
        zone_id: str,
        idx_hint: int,
        now: float,
        reserved_ids: Optional[set[str]] = None,
    ) -> str:
        sid = str(spot_id or "").strip()
        if not sid or sid.startswith("MAP_") or sid.startswith("SP_"):
            return sid
        res_set = reserved_ids if reserved_ids is not None else set()
        raw_tag = re.sub(r"[^A-Za-z0-9]+", "", str(group_code or zone_id or "Z")).upper()[:12] or "Z"
        pref = f"MAP_{raw_tag}_{idx_hint:02d}_{int(now * 1000) % 100000}"
        new_sid = self._mint_unique_spot_id_conn(conn, pref, f"MAP_{raw_tag}_", res_set)
        conn.execute("UPDATE spots SET spot_id = ? WHERE spot_id = ?", (new_sid, sid))
        return new_sid

    def _list_reclaimable_unassigned_rows_conn(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
        zone_id: str,
        active_prev_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        cam_id = str(camera_id or "").strip()
        if not cam_id or cam_id == "UNASSIGNED":
            return []
        active_gcs = {
            str(r.get("group_code") or "").strip().upper()
            for r in active_prev_rows
            if str(r.get("group_code") or "").strip()
        }
        cam_meta = conn.execute(
            "SELECT group_code FROM cameras WHERE camera_id = ?", (cam_id,)
        ).fetchone()
        cam_gc_raw = str(cam_meta["group_code"] or "").strip() if cam_meta else ""
        token_gcs = set(self._extract_camera_group_tokens(cam_gc_raw)) if cam_gc_raw else set()
        if cam_gc_raw:
            token_gcs.add(cam_gc_raw.upper())
        cand_gcs = {
            gc
            for gc in (active_gcs | token_gcs)
            if gc
            and conn.execute(
                """
                SELECT COUNT(*) AS c FROM spots
                WHERE UPPER(group_code) = ?
                  AND camera_id NOT IN ('UNASSIGNED', ?)
                  AND camera_id NOT LIKE 'CAM_MAP_%'
                """,
                (gc, cam_id),
            ).fetchone()["c"]
            == 0
        }

        unassigned_all = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM spots WHERE camera_id = 'UNASSIGNED'"
            ).fetchall()
        ]
        matched: List[Dict[str, Any]] = []
        prefix_dash = f"{cam_id}-"
        prefix_under = f"{cam_id}_"
        for row in unassigned_all:
            sid = str(row.get("spot_id") or "")
            gc_up = str(row.get("group_code") or "").strip().upper()
            if sid.startswith(prefix_dash) or sid.startswith(prefix_under):
                matched.append(row)
            elif gc_up and gc_up in cand_gcs:
                matched.append(row)
        matched.sort(key=self._spot_natural_order_key)
        return matched

    def list_reclaimable_spots_for_camera(self, camera_id: str) -> List[Dict[str, Any]]:
        cam_id = str(camera_id or "").strip()
        if not cam_id or cam_id == "UNASSIGNED":
            return []
        with self._lock, self._connect() as conn:
            cam_row = conn.execute(
                "SELECT zone_id FROM cameras WHERE camera_id = ?", (cam_id,)
            ).fetchone()
            zid = str(cam_row["zone_id"]) if cam_row else ""
            prev_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM spots WHERE camera_id = ? ORDER BY spot_id", (cam_id,)
                ).fetchall()
            ]
            reclaim_rows = self._list_reclaimable_unassigned_rows_conn(conn, cam_id, zid, prev_rows)
            out: List[Dict[str, Any]] = []
            for r in reclaim_rows:
                d = dict(r)
                d["polygon"] = json.loads(d.pop("polygon_json", "[]") or "[]")
                vp_raw = d.pop("vehicle_polygon_json", "") or ""
                d["vehicle_polygon"] = json.loads(vp_raw) if vp_raw else []
                d.pop("ref_free_sig_b64", None)
                out.append(d)
            return out

    def save_camera_spots(
        self,
        camera_id: str,
        zone_id: str,
        spots_payload: List[Dict[str, Any]],
        image_width: int = 1280,
        image_height: int = 720,
        calibration_layer: str = "both",
    ) -> List[Dict[str, Any]]:
        from app.homography import derive_vehicle_envelope_polygon

        cal_layer = str(calibration_layer or "both").strip().lower()
        if cal_layer not in ("ground", "vehicle", "both"):
            cal_layer = "both"

        now = time.time()
        with self._lock, self._connect() as conn:
            prev_cam_row = conn.execute(
                "SELECT zone_id FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            same_zone = prev_cam_row is not None and prev_cam_row["zone_id"] == zone_id
            prev_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM spots WHERE camera_id = ? ORDER BY spot_id", (camera_id,)
                ).fetchall()
            ]
            prev_rows.sort(key=self._spot_sort_key)
            reclaim_rows = self._list_reclaimable_unassigned_rows_conn(
                conn, camera_id, zone_id, prev_rows
            )
            prev_active_ids = {r["spot_id"] for r in prev_rows}
            reclaim_ids_set = {r["spot_id"] for r in reclaim_rows if r["spot_id"] not in prev_active_ids}
            candidate_rows = prev_rows + [r for r in reclaim_rows if r["spot_id"] in reclaim_ids_set]
            candidate_rows.sort(key=self._spot_natural_order_key)

            existing_sigs = {
                r["label"]: r["ref_free_sig_b64"]
                for r in candidate_rows
                if r.get("ref_free_sig_b64")
            }
            prev_by_id = {r["spot_id"]: r for r in candidate_rows}
            prev_by_label = {r["label"]: r for r in candidate_rows}
            custom_prev = [
                r for r in candidate_rows
                if (same_zone or r["spot_id"] in reclaim_ids_set)
                and r.get("map_custom")
                and r.get("map_x") is not None
                and r.get("map_y") is not None
            ]

            # Match incoming payload stalls to previous/reclaimable rows deterministically (first by
            # reclaim_spot_id / exact ID / label, then sequentially for unclaimed rows), and detach any
            # unclaimed map_custom stalls to UNASSIGNED with a non-colliding MAP_* ID so floor-plan stalls
            # are never erased and NEVER squat on {camera_id}-S.. primary keys.
            assigned_prev_by_idx: Dict[int, Dict[str, Any]] = {}
            claimed_prev_ids: set[str] = set()
            for idx, sp in enumerate(spots_payload):
                reclaim_sid = str(sp.get("reclaim_spot_id") or "").strip()
                cand_id = str(sp.get("spot_id") or f"{camera_id}-S{idx+1:02d}")
                cand_lbl = str(sp.get("label") or f"车位-{idx+1:02d}")
                hit = None
                if reclaim_sid and reclaim_sid in prev_by_id and reclaim_sid not in claimed_prev_ids:
                    hit = prev_by_id[reclaim_sid]
                elif cand_id in prev_by_id and prev_by_id[cand_id]["spot_id"] not in claimed_prev_ids:
                    hit = prev_by_id[cand_id]
                elif cand_lbl in prev_by_label and prev_by_label[cand_lbl]["spot_id"] not in claimed_prev_ids:
                    hit = prev_by_label[cand_lbl]
                if hit is not None:
                    assigned_prev_by_idx[idx] = hit
                    claimed_prev_ids.add(hit["spot_id"])

            unclaimed_prev = [r for r in candidate_rows if r["spot_id"] not in claimed_prev_ids]
            unclaimed_cursor = 0
            for idx in range(len(spots_payload)):
                if idx in assigned_prev_by_idx:
                    continue
                if unclaimed_cursor < len(unclaimed_prev):
                    hit = unclaimed_prev[unclaimed_cursor]
                    unclaimed_cursor += 1
                    assigned_prev_by_idx[idx] = hit
                    claimed_prev_ids.add(hit["spot_id"])

            reserved_spot_ids: set[str] = set()
            if len(spots_payload) == 0:
                for d_idx, p_row in enumerate(candidate_rows):
                    p_sid = p_row["spot_id"]
                    if p_sid in prev_active_ids and p_row.get("map_custom"):
                        conn.execute(
                            """
                            UPDATE spots
                            SET camera_id = 'UNASSIGNED',
                                status = 'UNKNOWN',
                                free_units = 0,
                                occupied_units = 0,
                                pending_status = NULL,
                                pending_free_units = NULL,
                                pending_since = NULL,
                                last_reason = '机位清空标定后保留平面图车位（待重新绑定）'
                            WHERE spot_id = ?
                            """,
                            (p_sid,),
                        )
                        self._rename_spot_id_to_map_placeholder_conn(
                            conn,
                            p_sid,
                            str(p_row.get("group_code") or ""),
                            str(p_row.get("zone_id") or zone_id),
                            d_idx + 1,
                            now,
                            reserved_spot_ids,
                        )
            else:
                # Calibrated spot count is strictly authoritative ("以空间定标的数量为准"):
                # remove all unclaimed UNASSIGNED placeholders belonging to this camera's bound group(s)
                # and let `DELETE FROM spots WHERE camera_id = ?` remove any surplus previous stalls.
                for r_sid in reclaim_ids_set:
                    conn.execute(
                        "DELETE FROM spots WHERE spot_id = ? AND camera_id = 'UNASSIGNED'",
                        (r_sid,),
                    )

            for c_sid in (claimed_prev_ids & reclaim_ids_set):
                conn.execute(
                    "DELETE FROM spots WHERE spot_id = ? AND camera_id = 'UNASSIGNED'",
                    (c_sid,),
                )

            conn.execute(
                "UPDATE cameras SET zone_id = ?, image_width = ?, image_height = ? WHERE camera_id = ?",
                (zone_id, image_width, image_height, camera_id),
            )
            conn.execute("DELETE FROM spots WHERE camera_id = ?", (camera_id,))
            placed_coords: List[Dict[str, float]] = []
            for idx, sp in enumerate(spots_payload):
                raw_req_sid = str(sp.get("spot_id") or "").strip()
                if not raw_req_sid or raw_req_sid.startswith("MAP_"):
                    raw_req_sid = f"{camera_id}-S{idx+1:02d}"
                conflict_row = conn.execute(
                    "SELECT spot_id, camera_id, group_code, zone_id FROM spots WHERE spot_id = ?",
                    (raw_req_sid,),
                ).fetchone()
                if conflict_row and str(conflict_row["camera_id"] or "") == "UNASSIGNED":
                    self._rename_spot_id_to_map_placeholder_conn(
                        conn,
                        raw_req_sid,
                        str(conflict_row["group_code"] or ""),
                        str(conflict_row["zone_id"] or zone_id),
                        idx + 1,
                        now,
                        reserved_spot_ids,
                    )
                spot_id = self._mint_unique_spot_id_conn(
                    conn, raw_req_sid, f"{camera_id}-S", reserved_spot_ids
                )
                any_prev_for_lbl = assigned_prev_by_idx.get(idx)
                label = str(
                    sp.get("label")
                    or (
                        any_prev_for_lbl.get("label")
                        if any_prev_for_lbl and any_prev_for_lbl.get("group_code")
                        else ""
                    )
                    or f"车位-{idx+1:02d}"
                )
                spot_type = sp.get("spot_type", "standard")
                total_meters = float(sp.get("total_meters", 16.0 if spot_type == "elastic_strip" else 5.2))
                capacity = (
                    max(1, int(total_meters // 5.2))
                    if spot_type == "elastic_strip"
                    else int(sp.get("capacity", 1))
                )
                raw_status = str(sp.get("initial_status") or sp.get("status") or "FREE").upper()
                if raw_status not in ("FREE", "OCCUPIED", "BLOCKED", "UNKNOWN"):
                    raw_status = "FREE"
                if spot_type == "elastic_strip":
                    free_u = int(sp.get("free_units", capacity if raw_status == "FREE" else 0))
                    occ_u = int(sp.get("occupied_units", 0 if raw_status == "FREE" else capacity))
                else:
                    free_u = 1 if raw_status == "FREE" else 0
                    occ_u = 1 if raw_status == "OCCUPIED" else 0
                conf = float(sp.get("confidence", 1.0))
                reason = str(sp.get("reason") or sp.get("last_reason") or "新标定车位（待抓拍更新）")
                engine = str(sp.get("engine") or sp.get("last_engine") or "calibration")
                ref_sig = sp.get("ref_free_sig_b64") or existing_sigs.get(label)
                default_obs = (
                    "vehicle"
                    if raw_status == "OCCUPIED"
                    else ("immovable" if raw_status == "BLOCKED" else "none")
                )
                obs_type = str(sp.get("obstacle_type") or default_obs)
                act_hint = str(sp.get("action_hint") or "")

                # Preserve user-tuned site-map stall coordinates & decoupled layer polygons across calibration
                any_prev = assigned_prev_by_idx.get(idx)
                matched_prev = (
                    any_prev
                    if (
                        any_prev is not None
                        and (same_zone or int(any_prev.get("map_custom") or 0) == 1)
                    )
                    else None
                )

                # Strict layer decoupling:
                # - "ground": updates ground polygon ONLY; keeps existing vehicle_polygon untouched!
                # - "vehicle": updates vehicle_polygon ONLY; keeps existing ground polygon & ref_sig untouched!
                if cal_layer == "vehicle" and any_prev and any_prev.get("polygon_json"):
                    final_gpoly_json = any_prev["polygon_json"]
                    ref_sig = any_prev.get("ref_free_sig_b64") or ref_sig
                else:
                    final_gpoly_json = json.dumps(sp["polygon"])

                if cal_layer == "ground" and any_prev and any_prev.get("vehicle_polygon_json"):
                    final_vpoly_json = any_prev["vehicle_polygon_json"]
                    final_vpoly_custom = int(any_prev.get("vehicle_poly_custom") or 0)
                else:
                    if isinstance(sp.get("vehicle_polygon"), list) and len(sp["vehicle_polygon"]) >= 3:
                        final_vpoly_json = json.dumps(sp["vehicle_polygon"])
                        final_vpoly_custom = 1 if (cal_layer == "vehicle" or sp.get("vehicle_poly_custom")) else 0
                    else:
                        g_for_derive = json.loads(final_gpoly_json)
                        final_vpoly_json = json.dumps(
                            derive_vehicle_envelope_polygon(
                                g_for_derive, img_w=image_width, img_h=image_height
                            )
                        )
                        final_vpoly_custom = 0

                grp_code = str(
                    sp.get("group_code")
                    if sp.get("group_code") is not None
                    else (any_prev.get("group_code") if any_prev else "")
                ).strip()
                grp_name = str(
                    sp.get("group_name")
                    if sp.get("group_name") is not None
                    else (any_prev.get("group_name") if any_prev else "")
                ).strip()
                near_uid = str(
                    sp.get("near_unit_id")
                    if sp.get("near_unit_id") is not None
                    else (any_prev.get("near_unit_id") if any_prev else "")
                ).strip()
                near_ucust = int(
                    sp.get("near_unit_custom")
                    if sp.get("near_unit_custom") is not None
                    else (any_prev.get("near_unit_custom") if any_prev else 0)
                )
                veh_model = str(
                    sp.get("vehicle_model")
                    if sp.get("vehicle_model") is not None
                    else (any_prev.get("vehicle_model") if any_prev else "sedan")
                ).strip() or "sedan"

                map_x = sp.get("map_x")
                map_y = sp.get("map_y")
                map_w = sp.get("map_w")
                map_h = sp.get("map_h")
                map_angle = sp.get("map_angle")
                map_custom = int(sp.get("map_custom", 0))

                if map_x is None and matched_prev and matched_prev.get("map_x") is not None:
                    map_x = matched_prev.get("map_x")
                    map_y = matched_prev.get("map_y")
                    map_w = matched_prev.get("map_w")
                    map_h = matched_prev.get("map_h")
                    map_angle = matched_prev.get("map_angle")
                    map_custom = int(matched_prev.get("map_custom") or 0)
                elif map_x is None and custom_prev and placed_coords:
                    # Extrapolate newly added stall right beside the customized row
                    last_c = placed_coords[-1]
                    if len(placed_coords) >= 2:
                        dx = last_c["x"] - placed_coords[-2]["x"]
                        dy = last_c["y"] - placed_coords[-2]["y"]
                    else:
                        import math
                        rad = math.radians(last_c["angle"])
                        dx = (last_c["w"] + 0.32) * math.cos(rad)
                        dy = (last_c["w"] + 0.32) * math.sin(rad)
                    map_x = round(max(1.5, min(98.5, last_c["x"] + dx)), 2)
                    map_y = round(max(1.5, min(98.5, last_c["y"] + dy)), 2)
                    map_w = last_c["w"]
                    map_h = last_c["h"]
                    map_angle = last_c["angle"]
                    map_custom = 1

                if map_x is not None and map_y is not None:
                    placed_coords.append(
                        {
                            "x": float(map_x),
                            "y": float(map_y),
                            "w": float(map_w or 1.75),
                            "h": float(map_h or 3.25),
                            "angle": float(map_angle or 0.0),
                        }
                    )

                conn.execute(
                    """
                    INSERT INTO spots (
                        spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                        polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                        pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                        ref_free_sig_b64, obstacle_type, action_hint,
                        map_x, map_y, map_w, map_h, map_angle, map_custom,
                        vehicle_polygon_json, vehicle_poly_custom, group_code, group_name,
                        near_unit_id, near_unit_custom, vehicle_model
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        spot_id,
                        camera_id,
                        zone_id,
                        label,
                        spot_type,
                        1 if sp.get("compact_only") else 0,
                        total_meters,
                        capacity,
                        final_gpoly_json,
                        raw_status,
                        free_u,
                        occ_u,
                        conf,
                        reason,
                        engine,
                        now,
                        now,
                        ref_sig,
                        obs_type,
                        act_hint,
                        float(map_x) if map_x is not None else None,
                        float(map_y) if map_y is not None else None,
                        float(map_w) if map_w is not None else None,
                        float(map_h) if map_h is not None else None,
                        float(map_angle) if map_angle is not None else None,
                        map_custom,
                        final_vpoly_json,
                        final_vpoly_custom,
                        grp_code,
                        grp_name,
                        near_uid,
                        near_ucust,
                        veh_model,
                    ),
                )
            self._snapshot_active_slot_calibration_conn(conn, camera_id)
        return self.list_spots(camera_id=camera_id)

    def update_spot_state(self, spot_id: str, updates: Dict[str, Any]) -> None:
        if not updates:
            return
        cols = ", ".join(f"{k} = ?" for k in updates.keys())
        vals = list(updates.values()) + [spot_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE spots SET {cols} WHERE spot_id = ?", vals)

    def get_site_map(self, include_backdrop: bool = True) -> Dict[str, Any]:
        with self._lock:
            if self._site_map_cache is None:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM site_map WHERE map_id = 'default'"
                    ).fetchone()
                if not row:
                    self._site_map_cache = {
                        "map_id": "default",
                        "source_type": "seed",
                        "has_backdrop": False,
                        "backdrop_jpeg_b64": "",
                        "aspect_ratio": 1.45,
                        "topology": json.loads(json.dumps(DEFAULT_SITE_MAP_TOPOLOGY)),
                        "meta": {
                            "scene_summary": "默认零实景矢量平面图（支持上传小区平面图自动生成）",
                            "estimated_span_meters": 185.0,
                        },
                        "updated_at": 0.0,
                    }
                else:
                    d = dict(row)
                    has_bg = bool(d.get("backdrop_jpeg_b64"))
                    try:
                        topology = json.loads(d.get("topology_json") or "{}")
                    except Exception:
                        topology = {}
                    if not topology.get("gates") and not topology.get("roads") and not topology.get("walkways"):
                        topology = normalize_site_map_topology(
                            json.loads(json.dumps(DEFAULT_SITE_MAP_TOPOLOGY))
                        )
                    else:
                        topology = normalize_site_map_topology(topology)
                    try:
                        meta = json.loads(d.get("meta_json") or "{}")
                    except Exception:
                        meta = {}
                    self._site_map_cache = {
                        "map_id": d["map_id"],
                        "source_type": d.get("source_type", "custom"),
                        "has_backdrop": has_bg,
                        "backdrop_jpeg_b64": d.get("backdrop_jpeg_b64") or "",
                        "aspect_ratio": float(d.get("aspect_ratio") or 1.45),
                        "topology": topology,
                        "meta": meta,
                        "updated_at": float(d.get("updated_at") or 0.0),
                    }

            cached = self._site_map_cache
            est_span = float((cached.get("meta") or {}).get("estimated_span_meters") or 185.0)
            out = {
                "map_id": cached["map_id"],
                "source_type": cached["source_type"],
                "has_backdrop": cached["has_backdrop"],
                "backdrop_jpeg_b64": cached["backdrop_jpeg_b64"] if include_backdrop else None,
                "aspect_ratio": cached["aspect_ratio"],
                "estimated_span_meters": est_span,
                "topology": json.loads(json.dumps(cached["topology"])),
                "meta": dict(cached["meta"]),
                "updated_at": cached["updated_at"],
            }
            return out

    def get_site_map_cache_bytes(self) -> int:
        with self._lock:
            if not self._site_map_cache:
                return 0
            bg = self._site_map_cache.get("backdrop_jpeg_b64") or ""
            return len(bg) + 1024

    def save_site_map(
        self,
        source_type: str,
        backdrop_jpeg_b64: Optional[str],
        aspect_ratio: float,
        topology: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = time.time()
        aspect = max(0.65, min(2.6, float(aspect_ratio or 1.45)))
        norm_topo = normalize_site_map_topology(topology)
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT backdrop_jpeg_b64 FROM site_map WHERE map_id = 'default'"
            ).fetchone()
            final_bg = (
                backdrop_jpeg_b64
                if backdrop_jpeg_b64 is not None
                else (existing["backdrop_jpeg_b64"] if existing else "")
            )
            conn.execute(
                """
                INSERT INTO site_map (
                    map_id, source_type, backdrop_jpeg_b64, aspect_ratio, topology_json, meta_json, updated_at
                ) VALUES ('default', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(map_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    backdrop_jpeg_b64 = excluded.backdrop_jpeg_b64,
                    aspect_ratio = excluded.aspect_ratio,
                    topology_json = excluded.topology_json,
                    meta_json = excluded.meta_json,
                    updated_at = excluded.updated_at
                """,
                (
                    source_type,
                    final_bg,
                    aspect,
                    json.dumps(norm_topo, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                ),
            )
            self._site_map_cache = None
        return self.get_site_map(include_backdrop=False)

    @staticmethod
    def _spot_natural_order_key(sp: Dict[str, Any]) -> Tuple[int, str, str]:
        lbl = str(sp.get("label") or sp.get("spot_id") or "")
        nums = re.findall(r"\d+", lbl)
        tail_num = int(nums[-1]) if nums else 999999
        return (tail_num, lbl, str(sp.get("spot_id") or ""))

    def _reconcile_spot_groups_conn(
        self,
        conn: sqlite3.Connection,
        units: List[Dict[str, Any]],
        zones_by_id: Dict[str, Dict[str, Any]],
        topology: Dict[str, Any],
        road_graph: Dict[str, Any],
    ) -> None:
        spots = [dict(r) for r in conn.execute("SELECT * FROM spots").fetchall()]
        if not spots:
            return
        cams_by_id = {
            r["camera_id"]: dict(r)
            for r in conn.execute("SELECT * FROM cameras").fetchall()
        }
        units_by_id = {u["unit_id"]: u for u in units}

        # 0a) Purge completely unplaced UNASSIGNED orphan rows (deleted cameras without map coordinates)
        dead_orphan_ids = [
            str(sp["spot_id"])
            for sp in spots
            if str(sp.get("camera_id") or "UNASSIGNED").strip() == "UNASSIGNED"
            and (sp.get("map_x") is None or sp.get("map_y") is None)
        ]
        if dead_orphan_ids:
            conn.executemany(
                "DELETE FROM spots WHERE spot_id = ? AND camera_id = 'UNASSIGNED'",
                [(sid,) for sid in dead_orphan_ids],
            )
            dead_set = set(dead_orphan_ids)
            spots = [sp for sp in spots if str(sp["spot_id"]) not in dead_set]
            if not spots:
                return

        # 0b) Self-heal split camera groups: when a calibrated camera owns unplaced real stalls
        # AND placed map_editor/UNASSIGNED slots already exist under the same group_code, merge 1-to-1!
        unplaced_real_by_cam: Dict[str, List[Dict[str, Any]]] = {}
        placed_slots_by_gc: Dict[str, List[Dict[str, Any]]] = {}
        for sp in spots:
            cid_s = str(sp.get("camera_id") or "UNASSIGNED").strip()
            gc_s = str(sp.get("group_code") or "").strip()
            is_editor = str(sp.get("last_engine") or "") == "map_editor"
            has_xy = sp.get("map_x") is not None and sp.get("map_y") is not None
            if cid_s != "UNASSIGNED" and cid_s in cams_by_id and not is_editor and not has_xy:
                unplaced_real_by_cam.setdefault(cid_s, []).append(sp)
            if gc_s and has_xy and (cid_s == "UNASSIGNED" or is_editor):
                placed_slots_by_gc.setdefault(gc_s.upper(), []).append((gc_s, sp))

        healed_any = False
        for cid_heal, unplaced_list in unplaced_real_by_cam.items():
            cand_gcs: List[str] = []
            for usp in unplaced_list:
                ugc = str(usp.get("group_code") or "").strip()
                if ugc and ugc not in cand_gcs:
                    cand_gcs.append(ugc)
            cam_decl = str((cams_by_id.get(cid_heal) or {}).get("group_code") or "").strip()
            for tok in re.split(r"[,;/\s]+", cam_decl):
                tok_c = tok.strip()
                if tok_c and tok_c not in cand_gcs:
                    cand_gcs.append(tok_c)
            for c_gc in cand_gcs:
                matched_entries = placed_slots_by_gc.get(c_gc.upper()) or []
                eligible = [
                    sp_e
                    for _, sp_e in matched_entries
                    if str(sp_e.get("camera_id") or "UNASSIGNED") in ("UNASSIGNED", cid_heal)
                ]
                if eligible:
                    actual_gc = matched_entries[0][0]
                    self._bind_camera_to_group_conn(conn, camera_id=cid_heal, group_code=actual_gc)
                    healed_any = True
                    break
        if healed_any:
            spots = [dict(r) for r in conn.execute("SELECT * FROM spots").fetchall()]
            if not spots:
                return

        # Clean up any legacy orphan camera_ids (from cameras deleted/renamed before cascade)
        # and backfill missing group_code on bound camera spots so they never turn into virtual CAM_CAM_* ghosts
        existing_cam_groups: Dict[str, str] = {}
        for sp in spots:
            gc_existing = str(sp.get("group_code") or "").strip()
            cid_existing = str(sp.get("camera_id") or "").strip()
            if gc_existing and cid_existing and cid_existing != "UNASSIGNED":
                existing_cam_groups.setdefault(gc_existing.upper(), cid_existing)

        # Detach any uncalibrated map_editor placeholders attached to a camera whose primary group
        # already has real spatially calibrated stalls (prevents foreign map-editor groups from inflating count)
        calibrated_gcs_by_cam: Dict[str, set[str]] = {}
        for sp in spots:
            cid0 = str(sp.get("camera_id") or "UNASSIGNED").strip()
            gc0 = str(sp.get("group_code") or "").strip().upper()
            if cid0 != "UNASSIGNED" and str(sp.get("last_engine") or "") != "map_editor" and gc0:
                calibrated_gcs_by_cam.setdefault(cid0, set()).add(gc0)

        cleaned_spots: List[Dict[str, Any]] = []
        for sp in spots:
            cid = str(sp.get("camera_id") or "UNASSIGNED").strip() or "UNASSIGNED"
            if cid != "UNASSIGNED" and cid in calibrated_gcs_by_cam and str(sp.get("last_engine") or "") == "map_editor":
                sp_gc_up = str(sp.get("group_code") or "").strip().upper()
                cam_decl_raw = str((cams_by_id.get(cid) or {}).get("group_code") or "").strip()
                cam_decl_gcs = {
                    tok.strip().upper()
                    for tok in re.split(r"[,;/\s]+", cam_decl_raw)
                    if tok.strip()
                }
                allowed_gcs = calibrated_gcs_by_cam[cid] | cam_decl_gcs
                if sp_gc_up and sp_gc_up not in allowed_gcs:
                    conn.execute(
                        """
                        UPDATE spots
                        SET camera_id = 'UNASSIGNED', status = 'UNKNOWN', free_units = 0, occupied_units = 0,
                            pending_status = NULL, pending_free_units = NULL, pending_since = NULL,
                            last_reason = '平面图预设车位（待接入摄像头）'
                        WHERE spot_id = ?
                        """,
                        (sp["spot_id"],),
                    )
                    sp["camera_id"] = "UNASSIGNED"
                    sp["status"] = "UNKNOWN"
                    cid = "UNASSIGNED"
            if cid != "UNASSIGNED" and cid not in cams_by_id:
                if sp.get("map_x") is None or sp.get("map_y") is None:
                    conn.execute("DELETE FROM spots WHERE spot_id = ?", (sp["spot_id"],))
                    continue
                conn.execute(
                    """
                    UPDATE spots
                    SET camera_id = 'UNASSIGNED', status = 'UNKNOWN', free_units = 0, occupied_units = 0,
                        pending_status = NULL, pending_free_units = NULL, pending_since = NULL
                    WHERE spot_id = ?
                    """,
                    (sp["spot_id"],),
                )
                sp["camera_id"] = "UNASSIGNED"
                sp["status"] = "UNKNOWN"
                cid = "UNASSIGNED"

            gc = str(sp.get("group_code") or "").strip()
            has_xy_sp = sp.get("map_x") is not None and sp.get("map_y") is not None
            if not gc and cid != "UNASSIGNED" and cid in cams_by_id:
                cam_meta = cams_by_id[cid]
                cam_gc_raw = str(cam_meta.get("group_code") or "").strip()
                cand_gc = re.split(r"[,;/\s]+", cam_gc_raw)[0].strip() if cam_gc_raw else ""
                if not cand_gc and has_xy_sp:
                    stripped = re.sub(r"^CAM_", "", cid, flags=re.IGNORECASE).strip()
                    owner = existing_cam_groups.get(stripped.upper()) if stripped else None
                    cand_gc = stripped if (stripped and owner in (None, cid)) else cid
                if cand_gc:
                    conn.execute(
                        "UPDATE spots SET group_code = ? WHERE spot_id = ?",
                        (cand_gc, sp["spot_id"]),
                    )
                    sp["group_code"] = cand_gc
                    existing_cam_groups.setdefault(cand_gc.upper(), cid)
            cleaned_spots.append(sp)

        groups: Dict[str, List[Dict[str, Any]]] = {}
        for sp in cleaned_spots:
            gc = str(sp.get("group_code") or "").strip()
            if gc:
                groups.setdefault(gc, []).append(sp)

        for gc, g_spots in groups.items():
            real_cam_ids_pre = [
                str(sp["camera_id"])
                for sp in g_spots
                if sp.get("camera_id") and sp["camera_id"] != "UNASSIGNED" and sp["camera_id"] in cams_by_id
            ]
            has_real_calibrated_in_group = bool(
                real_cam_ids_pre
                and any(
                    str(sp.get("camera_id") or "") in real_cam_ids_pre
                    and str(sp.get("last_engine") or "") != "map_editor"
                    and sp.get("map_x") is not None
                    and sp.get("map_y") is not None
                    for sp in g_spots
                )
            )
            if has_real_calibrated_in_group:
                # Calibrated camera spot count is authoritative for this group: purge orphan UNASSIGNED leftovers
                orphan_unassigned = [
                    sp["spot_id"] for sp in g_spots if str(sp.get("camera_id") or "UNASSIGNED") == "UNASSIGNED"
                ]
                if orphan_unassigned:
                    for o_sid in orphan_unassigned:
                        conn.execute(
                            "DELETE FROM spots WHERE spot_id = ? AND camera_id = 'UNASSIGNED'",
                            (o_sid,),
                        )
                    g_spots = [
                        sp for sp in g_spots if str(sp.get("camera_id") or "UNASSIGNED") != "UNASSIGNED"
                    ]
                    groups[gc] = g_spots
                    if not g_spots:
                        continue

            placed = [
                sp for sp in g_spots if sp.get("map_x") is not None and sp.get("map_y") is not None
            ]
            cx = (
                sum(float(sp["map_x"]) for sp in placed) / len(placed)
                if placed
                else None
            )
            cy = (
                sum(float(sp["map_y"]) for sp in placed) / len(placed)
                if placed
                else None
            )

            # 1) Unify zone_id across the entire group_code so split groups never fragment
            real_cam_ids = [
                str(sp["camera_id"])
                for sp in g_spots
                if sp.get("camera_id") and sp["camera_id"] != "UNASSIGNED" and sp["camera_id"] in cams_by_id
            ]
            target_zone_id = ""
            if real_cam_ids:
                target_zone_id = str(cams_by_id[real_cam_ids[0]]["zone_id"])
            else:
                z_counts: Dict[str, int] = {}
                for sp in g_spots:
                    zid = str(sp.get("zone_id") or "")
                    if zid in zones_by_id:
                        z_counts[zid] = z_counts.get(zid, 0) + 1
                if len(z_counts) == 1:
                    target_zone_id = next(iter(z_counts.keys()))
                elif z_counts and cx is not None and cy is not None:
                    cand_zids = list(z_counts.keys())
                    target_zone_id = min(
                        cand_zids,
                        key=lambda zid: (
                            -z_counts[zid],
                            math.hypot(
                                cx - (float(zones_by_id[zid].get("map_x") or 50.0) + float(zones_by_id[zid].get("map_w") or 12.0) * 0.5),
                                cy - (float(zones_by_id[zid].get("map_y") or 50.0) + float(zones_by_id[zid].get("map_h") or 6.0) * 0.5),
                            ),
                        ),
                    )
                elif zones_by_id and cx is not None and cy is not None:
                    target_zone_id = min(
                        zones_by_id.keys(),
                        key=lambda zid: math.hypot(
                            cx - (float(zones_by_id[zid].get("map_x") or 50.0) + float(zones_by_id[zid].get("map_w") or 12.0) * 0.5),
                            cy - (float(zones_by_id[zid].get("map_y") or 50.0) + float(zones_by_id[zid].get("map_h") or 6.0) * 0.5),
                        ),
                    )

            # 2) Resolve nearest building/unit (by physical building footprint proximity) unless customized
            custom_near = next(
                (
                    str(sp.get("near_unit_id") or "").strip()
                    for sp in g_spots
                    if int(sp.get("near_unit_custom") or 0) == 1
                    and str(sp.get("near_unit_id") or "").strip() in units_by_id
                ),
                "",
            )
            near_uid = custom_near
            near_custom_flag = 1 if custom_near else 0
            if not near_uid and units:
                gc_prefix_m = re.match(r"^(?:CAM_)*(\d+)(?:[LRFBSNSEW]|#|栋|-|_)", gc, re.IGNORECASE)
                gc_bldg_hint = gc_prefix_m.group(1) if gc_prefix_m else ""
                if not gc_bldg_hint:
                    hint_texts = [
                        next((str(sp.get("group_name") or "").strip() for sp in g_spots if str(sp.get("group_name") or "").strip()), ""),
                        str(cams_by_id[real_cam_ids[0]].get("name") or "") if real_cam_ids else "",
                        str(cams_by_id[real_cam_ids[0]].get("mount_address") or "") if real_cam_ids else "",
                    ]
                    for htxt in hint_texts:
                        hm = re.search(r"(\d+)\s*[号#栋]", htxt)
                        if hm:
                            gc_bldg_hint = hm.group(1)
                            break

                if cx is not None and cy is not None:
                    vw_iso = float(road_graph.get("vw") or 100.0)
                    vh_iso = float(road_graph.get("vh") or 100.0)
                    m_iso = float(road_graph.get("m_per_iso") or 1.5)

                    best_u = None
                    best_score = 1e12
                    for u in units:
                        ux = float(u.get("map_x") or 50.0)
                        uy = float(u.get("map_y") or 50.0)
                        uw = max(3.0, float(u.get("map_w") or 8.0))
                        uh = max(2.5, float(u.get("map_h") or 4.2))
                        # Distance from group centroid to building block edge (0 if inside block)
                        dx_edge = max(0.0, abs(cx - ux) - uw * 0.5) * vw_iso / 100.0 * m_iso
                        dy_edge = max(0.0, abs(cy - uy) - uh * 0.5) * vh_iso / 100.0 * m_iso
                        d_edge_m = math.hypot(dx_edge, dy_edge)
                        dx_ctr = (cx - ux) * vw_iso / 100.0 * m_iso
                        dy_ctr = (cy - uy) * vh_iso / 100.0 * m_iso
                        d_ctr_m = math.hypot(dx_ctr, dy_ctr)
                        score = d_edge_m * 0.8 + d_ctr_m * 0.2
                        u_bldg = extract_building_info(u.get("unit_id", ""), u.get("name", ""))["building_id"]
                        if gc_bldg_hint and u_bldg == gc_bldg_hint and d_ctr_m <= 65.0:
                            score -= 22.0
                        if score < best_score:
                            best_score = score
                            best_u = u
                    if best_u:
                        near_uid = str(best_u["unit_id"])
                elif gc_bldg_hint:
                    matched_u = next(
                        (
                            u
                            for u in units
                            if extract_building_info(u.get("unit_id", ""), u.get("name", ""))["building_id"]
                            == gc_bldg_hint
                        ),
                        None,
                    )
                    if matched_u:
                        near_uid = str(matched_u["unit_id"])

            # 3) Resolve human-friendly regional group_name
            explicit_gname = next(
                (str(sp.get("group_name") or "").strip() for sp in g_spots if str(sp.get("group_name") or "").strip()),
                "",
            )
            near_u_obj = units_by_id.get(near_uid)
            # Refresh auto-generated "<X>号楼<方位> · <gc>" label if nearest building changed
            if explicit_gname and near_u_obj and explicit_gname.endswith(f"· {gc}"):
                expected_bname = extract_building_info(
                    near_u_obj.get("unit_id", ""), near_u_obj.get("name", "")
                )["building_name"]
                if not explicit_gname.startswith(expected_bname):
                    explicit_gname = ""
            if not explicit_gname and real_cam_ids:
                cam_gname = str(cams_by_id[real_cam_ids[0]].get("group_name") or "").strip()
                if cam_gname:
                    explicit_gname = cam_gname
            if not explicit_gname:
                if cx is not None and cy is not None:
                    explicit_gname = _auto_directional_group_name(cx, cy, near_u_obj, gc)
                else:
                    explicit_gname = gc

            for sp in g_spots:
                next_zid = target_zone_id or sp["zone_id"]
                if (
                    sp["zone_id"] != next_zid
                    or str(sp.get("near_unit_id") or "") != near_uid
                    or int(sp.get("near_unit_custom") or 0) != near_custom_flag
                    or str(sp.get("group_name") or "") != explicit_gname
                ):
                    conn.execute(
                        """
                        UPDATE spots
                        SET zone_id = ?, near_unit_id = ?, near_unit_custom = ?, group_name = ?
                        WHERE spot_id = ?
                        """,
                        (next_zid, near_uid, near_custom_flag, explicit_gname, sp["spot_id"]),
                    )

    def reconcile_spot_groups_and_doors(self, span_meters: Optional[float] = None) -> None:
        smap = self.get_site_map(include_backdrop=False)
        topo = smap.get("topology") or {}
        if span_meters is None:
            span_meters = float((smap.get("meta") or {}).get("estimated_span_meters") or 185.0)
        span_m = max(60.0, min(600.0, float(span_meters)))
        road_graph = build_road_walk_graph(smap=smap, span_meters=span_m)

        with self._lock, self._connect() as conn:
            units = [dict(r) for r in conn.execute("SELECT * FROM units").fetchall()]
            for u in units:
                orig_dx = u.get("door_x")
                orig_dy = u.get("door_y")
                dx, dy = resolve_unit_door_xy(u, topo)
                u["door_x"] = dx
                u["door_y"] = dy
                if not int(u.get("door_custom") or 0) and (
                    orig_dx is None
                    or orig_dy is None
                    or abs(float(orig_dx) - dx) > 0.01
                    or abs(float(orig_dy) - dy) > 0.01
                ):
                    conn.execute(
                        "UPDATE units SET door_x = ?, door_y = ? WHERE unit_id = ?",
                        (dx, dy, u["unit_id"]),
                    )
            zones_by_id = {
                r["zone_id"]: dict(r)
                for r in conn.execute("SELECT * FROM zones").fetchall()
            }
            self._reconcile_spot_groups_conn(
                conn,
                units=units,
                zones_by_id=zones_by_id,
                topology=topo,
                road_graph=road_graph,
            )
        self.recompute_unit_zone_distances(span_meters=span_m, road_graph=road_graph)

    def recompute_unit_zone_distances(
        self,
        span_meters: Optional[float] = None,
        road_graph: Optional[Dict[str, Any]] = None,
    ) -> None:
        smap = self.get_site_map(include_backdrop=False)
        topo = smap.get("topology") or {}
        if span_meters is None:
            span_meters = float(
                (smap.get("meta") or {}).get("estimated_span_meters") or 185.0
            )
        span_m = max(60.0, min(600.0, float(span_meters)))
        r_graph = road_graph if road_graph is not None else build_road_walk_graph(smap=smap, span_meters=span_m)

        with self._lock, self._connect() as conn:
            units = [dict(r) for r in conn.execute("SELECT * FROM units").fetchall()]
            zones = [dict(r) for r in conn.execute("SELECT * FROM zones").fetchall()]
            if not units or not zones:
                return

            spots_by_zone: Dict[str, List[Tuple[float, float]]] = {}
            for sr in conn.execute(
                "SELECT zone_id, map_x, map_y FROM spots WHERE map_x IS NOT NULL AND map_y IS NOT NULL"
            ).fetchall():
                spots_by_zone.setdefault(sr["zone_id"], []).append(
                    (float(sr["map_x"]), float(sr["map_y"]))
                )

            pairs = []
            for u in units:
                door_x, door_y = resolve_unit_door_xy(u, topo)
                for z in zones:
                    z_pts = spots_by_zone.get(z["zone_id"])
                    if z_pts:
                        dists = [
                            compute_walk_between_map_points(
                                door_x,
                                door_y,
                                sx,
                                sy,
                                road_graph=r_graph,
                            )["walk_meters"]
                            for sx, sy in z_pts
                        ]
                        walk_m = round(0.65 * min(dists) + 0.35 * (sum(dists) / len(dists)), 1)
                    else:
                        zx_c = float(z["map_x"]) + max(2.0, float(z["map_w"])) * 0.5
                        zy_c = float(z["map_y"]) + max(2.0, float(z["map_h"])) * 0.5
                        walk_m = compute_walk_between_map_points(
                            door_x,
                            door_y,
                            zx_c,
                            zy_c,
                            road_graph=r_graph,
                        )["walk_meters"]
                    pairs.append((u["unit_id"], z["zone_id"], walk_m))

            conn.execute("DELETE FROM unit_zone_dist")
            conn.executemany(
                "INSERT INTO unit_zone_dist (unit_id, zone_id, walk_meters) VALUES (?, ?, ?)",
                pairs,
            )

    def _resolve_group_spots_conn(
        self, conn: sqlite3.Connection, group_code: str
    ) -> Tuple[str, List[Dict[str, Any]]]:
        gc = str(group_code or "").strip()
        if not gc:
            return "", []
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM spots WHERE group_code = ?", (gc,)
            ).fetchall()
        ]
        if rows:
            return gc, rows
        rows_ci = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM spots WHERE UPPER(group_code) = ?", (gc.upper(),)
            ).fetchall()
        ]
        if rows_ci:
            canonical = str(rows_ci[0].get("group_code") or gc).strip() or gc
            return canonical, rows_ci

        # Fallback for synthetic camera group IDs (e.g. CAM_CAM_10BH2 or CAM_10BH2)
        cand_cam_ids = [gc]
        if gc.upper().startswith("CAM_"):
            s1 = gc[4:]
            if s1:
                cand_cam_ids.append(s1)
            if s1.upper().startswith("CAM_") and len(s1) > 4:
                cand_cam_ids.append(s1[4:])
        for cid in cand_cam_ids:
            cam_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM spots WHERE camera_id = ?", (cid,)
                ).fetchall()
            ]
            if cam_rows:
                clean_gc = re.sub(r"^CAM_", "", cid, flags=re.IGNORECASE).strip() or cid
                conflict = conn.execute(
                    "SELECT 1 FROM spots WHERE UPPER(group_code) = ? AND camera_id != ?",
                    (clean_gc.upper(), cid),
                ).fetchone()
                resolved_gc = cid if conflict else clean_gc
                conn.execute(
                    "UPDATE spots SET group_code = ? WHERE camera_id = ? AND (group_code = '' OR group_code IS NULL)",
                    (resolved_gc, cid),
                )
                for r in cam_rows:
                    if not str(r.get("group_code") or "").strip():
                        r["group_code"] = resolved_gc
                return str(cam_rows[0].get("group_code") or resolved_gc), cam_rows
        return gc, []

    def _detach_group_from_camera_conn(
        self,
        conn: sqlite3.Connection,
        group_code: str,
        now: float,
    ) -> int:
        gc, all_rows = self._resolve_group_spots_conn(conn, group_code)
        if not gc:
            return 0
        rows = [
            sp for sp in all_rows if str(sp.get("camera_id") or "UNASSIGNED") != "UNASSIGNED"
        ]
        if not rows:
            return 0
        rows.sort(key=self._spot_natural_order_key)
        detached = 0
        detached_cam_ids: set[str] = set()
        cam_meta_cache: Dict[str, Dict[str, str]] = {}
        for idx, sp in enumerate(rows):
            sp_cam_id = str(sp.get("camera_id") or "")
            if sp_cam_id and sp_cam_id != "UNASSIGNED":
                detached_cam_ids.add(sp_cam_id)
            is_calibrated = str(sp.get("last_engine") or "") != "map_editor"
            if is_calibrated and sp.get("map_x") is not None and sp.get("map_y") is not None:
                # Recreate the UNASSIGNED floor-plan placeholder at the drawn coordinates AND keep
                # the camera's calibrated image-space stall intact (unplaced & unbound on map) so
                # reconcile never auto-rebinds it back to the floor-plan group.
                if sp_cam_id not in cam_meta_cache:
                    crow = conn.execute(
                        "SELECT group_code, group_name, mount_address FROM cameras WHERE camera_id = ?",
                        (sp_cam_id,),
                    ).fetchone()
                    own_gn = str(((crow["mount_address"] if crow else "") or "")).strip()
                    cam_meta_cache[sp_cam_id] = {"gn": own_gn}
                own_meta = cam_meta_cache[sp_cam_id]
                new_sid = f"MAP_{gc}_{idx + 1:02d}_{int(now * 1000) % 100000}"
                default_poly = [
                    [120 + (idx % 6) * 160, 220],
                    [250 + (idx % 6) * 160, 220],
                    [250 + (idx % 6) * 160, 440],
                    [120 + (idx % 6) * 160, 440],
                ]
                conn.execute(
                    """
                    INSERT INTO spots (
                        spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                        polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                        pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                        obstacle_type, action_hint, map_x, map_y, map_w, map_h, map_angle, map_custom,
                        group_code, group_name, near_unit_id, near_unit_custom, vehicle_model
                    ) VALUES (
                        ?, 'UNASSIGNED', ?, ?, ?, ?, ?, ?, ?, 'UNKNOWN', 0, 0, NULL, NULL, NULL, 1.0,
                        '平面图预设车位（待接入摄像头）', 'map_editor', ?, NULL, 'none', '',
                        ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        new_sid,
                        sp["zone_id"],
                        f"{gc}-{idx + 1:02d}",
                        sp["spot_type"],
                        int(sp.get("compact_only") or 0),
                        float(sp.get("total_meters") or 5.2),
                        int(sp.get("capacity") or 1),
                        json.dumps(default_poly),
                        now,
                        sp["map_x"],
                        sp["map_y"],
                        sp.get("map_w") or 1.75,
                        sp.get("map_h") or 3.25,
                        sp.get("map_angle") or 0.0,
                        gc,
                        str(sp.get("group_name") or ""),
                        str(sp.get("near_unit_id") or ""),
                        int(sp.get("near_unit_custom") or 0),
                        str(sp.get("vehicle_model") or "sedan"),
                    ),
                )
                conn.execute(
                    """
                    UPDATE spots
                    SET map_x = NULL, map_y = NULL, map_w = NULL, map_h = NULL,
                        map_angle = NULL, map_custom = 0, group_code = '', group_name = ?
                    WHERE spot_id = ?
                    """,
                    (own_meta["gn"], sp["spot_id"]),
                )
            elif is_calibrated:
                conn.execute(
                    "UPDATE spots SET group_code = '' WHERE spot_id = ?",
                    (sp["spot_id"],),
                )
            else:
                conn.execute(
                    """
                    UPDATE spots
                    SET camera_id = 'UNASSIGNED', status = 'UNKNOWN', free_units = 0, occupied_units = 0,
                        pending_status = NULL, pending_free_units = NULL, pending_since = NULL,
                        last_reason = '平面图预设车位（待接入摄像头）'
                    WHERE spot_id = ?
                    """,
                    (sp["spot_id"],),
                )
                self._rename_spot_id_to_map_placeholder_conn(
                    conn,
                    str(sp["spot_id"]),
                    gc,
                    str(sp.get("zone_id") or ""),
                    idx + 1,
                    now,
                )
            detached += 1

        for d_cid in detached_cam_ids:
            rem_rows = conn.execute(
                """
                SELECT DISTINCT group_code, group_name
                FROM spots
                WHERE camera_id = ? AND map_x IS NOT NULL AND map_y IS NOT NULL
                  AND COALESCE(group_code, '') != ''
                """,
                (d_cid,),
            ).fetchall()
            rem_gcs = [str(r["group_code"]).strip() for r in rem_rows if str(r["group_code"] or "").strip()]
            new_cam_gc = ",".join(rem_gcs)
            new_cam_gn = str(rem_rows[0]["group_name"] or "").strip() if rem_rows else ""
            conn.execute(
                "UPDATE cameras SET group_code = ?, group_name = ? WHERE camera_id = ?",
                (new_cam_gc, new_cam_gn, d_cid),
            )
        return detached

    def _bind_camera_to_group_conn(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
        group_code: str,
        group_name: Optional[str] = None,
        near_unit_id: Optional[str] = None,
        zone_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = now or time.time()
        cam_id = str(camera_id or "").strip()
        raw_gc = str(group_code or "").strip()
        if not raw_gc:
            raise ValueError("group_code 不能为空")
        gc, resolved_pre = self._resolve_group_spots_conn(conn, raw_gc)

        explicit_zid = str(zone_id or "").strip()
        if explicit_zid:
            z_exists = conn.execute(
                "SELECT 1 FROM zones WHERE zone_id = ?", (explicit_zid,)
            ).fetchone()
            if not z_exists:
                raise ValueError(f"所属小区片区不存在: {explicit_zid}")

        if not cam_id or cam_id == "UNASSIGNED":
            detached = self._detach_group_from_camera_conn(conn, gc, ts)
            if group_name is not None:
                conn.execute(
                    "UPDATE spots SET group_name = ? WHERE group_code = ?",
                    (str(group_name).strip(), gc),
                )
            if near_unit_id is not None:
                uid_clean = str(near_unit_id).strip()
                conn.execute(
                    "UPDATE spots SET near_unit_id = ?, near_unit_custom = ? WHERE group_code = ?",
                    (uid_clean, 1 if uid_clean else 0, gc),
                )
            if explicit_zid:
                conn.execute(
                    "UPDATE spots SET zone_id = ? WHERE group_code = ?",
                    (explicit_zid, gc),
                )
            return {
                "camera_id": "UNASSIGNED",
                "group_code": gc,
                "zone_id": explicit_zid or None,
                "detached": detached,
                "bound_spots": 0,
            }

        cam_row = conn.execute(
            "SELECT * FROM cameras WHERE camera_id = ?", (cam_id,)
        ).fetchone()
        if not cam_row:
            raise ValueError(f"未找到机位: {cam_id}")
        cam = dict(cam_row)

        # If this group is currently bound to a different camera, detach that camera first
        other_cam_rows = conn.execute(
            "SELECT DISTINCT camera_id FROM spots WHERE group_code = ? AND camera_id NOT IN ('UNASSIGNED', ?)",
            (gc, cam_id),
        ).fetchall()
        for ocr in other_cam_rows:
            self._detach_group_from_camera_conn(conn, gc, ts)
            break

        # If this camera was previously bound to another group_code, detach that old group first
        prev_cam_groups = [
            str(r["group_code"]).strip()
            for r in conn.execute(
                "SELECT DISTINCT group_code FROM spots WHERE camera_id = ? AND group_code != '' AND group_code != ?",
                (cam_id, gc),
            ).fetchall()
            if str(r["group_code"] or "").strip()
        ]
        for old_gc in prev_cam_groups:
            self._detach_group_from_camera_conn(conn, old_gc, ts)

        group_spots = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM spots WHERE group_code = ?", (gc,)
            ).fetchall()
        ]
        if not group_spots:
            raise ValueError(f"未找到平面图车位组: {gc}")
        group_spots.sort(key=self._spot_natural_order_key)

        unassigned_group_spots = [
            sp for sp in group_spots if sp["camera_id"] == "UNASSIGNED"
        ]
        all_cam_spots = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM spots WHERE camera_id = ?", (cam_id,)
            ).fetchall()
        ]
        all_cam_spots.sort(key=self._spot_natural_order_key)
        real_cal_all = [
            sp for sp in all_cam_spots if str(sp.get("last_engine") or "") != "map_editor"
        ]
        has_real_cal = len(real_cal_all) > 0

        if has_real_cal:
            matching_gc_cal = [
                sp
                for sp in real_cal_all
                if str(sp.get("group_code") or "").strip().upper() == gc.upper()
            ]
            unplaced_cal = [
                sp
                for sp in real_cal_all
                if sp.get("map_x") is None
                or sp.get("map_y") is None
                or int(sp.get("map_custom") or 0) == 0
            ]
            cal_spots = matching_gc_cal or unplaced_cal or real_cal_all
        else:
            cal_spots = []
        cal_spot_ids = {str(sp["spot_id"]) for sp in cal_spots}

        # Collect floor-plan template slots BEFORE deleting any map_editor placeholders!
        # Includes both UNASSIGNED drawn spots AND map_editor spots already stamped with cam_id.
        placed_placeholder_slots = [
            sp
            for sp in group_spots
            if str(sp["spot_id"]) not in cal_spot_ids
            and sp.get("map_x") is not None
            and sp.get("map_y") is not None
            and (
                sp["camera_id"] in ("UNASSIGNED", cam_id)
                or str(sp.get("last_engine") or "") == "map_editor"
                or str(sp["camera_id"]).startswith("CAM_MAP_")
            )
        ]
        placed_placeholder_slots.sort(key=self._spot_natural_order_key)

        # If duplicate map_editor batches accumulated in the same group, prefer the newest user-drawn batch
        slot_sources = placed_placeholder_slots
        if has_real_cal and len(slot_sources) > len(cal_spots):
            editor_only = [
                s for s in slot_sources if str(s.get("last_engine") or "") == "map_editor"
            ]
            if len(editor_only) >= len(cal_spots):
                chgs = [float(s.get("last_changed_at") or 0.0) for s in editor_only]
                max_chg, min_chg = max(chgs), min(chgs)
                if max_chg - min_chg > 10.0:
                    newest_batch = [
                        s
                        for s in editor_only
                        if (max_chg - float(s.get("last_changed_at") or 0.0)) <= 5.0
                    ]
                    if len(newest_batch) >= len(cal_spots):
                        newest_batch.sort(key=self._spot_natural_order_key)
                        slot_sources = newest_batch

        sample_sp = (
            slot_sources[0]
            if slot_sources
            else (unassigned_group_spots[0] if unassigned_group_spots else group_spots[0])
        )
        target_zone_id = (
            explicit_zid
            or str(sample_sp.get("zone_id") or cam["zone_id"]).strip()
            or cam["zone_id"]
        )
        final_gname = (
            str(group_name).strip()
            if group_name is not None and str(group_name).strip()
            else str(sample_sp.get("group_name") or cam.get("group_name") or gc).strip()
        )
        final_near_uid = (
            str(near_unit_id).strip()
            if near_unit_id is not None
            else str(sample_sp.get("near_unit_id") or "").strip()
        )
        final_near_custom = (
            (1 if final_near_uid else 0)
            if near_unit_id is not None
            else int(sample_sp.get("near_unit_custom") or 0)
        )

        conn.execute(
            "UPDATE cameras SET zone_id = ?, group_code = ?, group_name = ? WHERE camera_id = ?",
            (target_zone_id, gc, final_gname, cam_id),
        )

        bound_count = 0
        if cal_spots and slot_sources:
            # Merge 1-to-1: preserve camera's calibrated image polygons & live occupancy status,
            # while adopting the drawn floor-plan group's exact map_x/map_y/map_w/map_h/map_angle!
            step_x, step_y = 2.1, 0.0
            if len(slot_sources) >= 2:
                step_x = float(slot_sources[-1]["map_x"]) - float(slot_sources[-2]["map_x"])
                step_y = float(slot_sources[-1]["map_y"]) - float(slot_sources[-2]["map_y"])
                if math.hypot(step_x, step_y) < 0.2:
                    step_x, step_y = 2.1, 0.0

            for idx, cal_sp in enumerate(cal_spots):
                if idx < len(slot_sources):
                    src = slot_sources[idx]
                    mx = src["map_x"]
                    my = src["map_y"]
                    mw = src.get("map_w") or 1.75
                    mh = src.get("map_h") or 3.25
                    mang = src.get("map_angle") or 0.0
                    vmod = str(src.get("vehicle_model") or cal_sp.get("vehicle_model") or "sedan").strip() or "sedan"
                else:
                    last_src = slot_sources[-1]
                    extra_k = idx - len(slot_sources) + 1
                    mx = round(max(1.5, min(98.5, float(last_src.get("map_x") or 50.0) + step_x * extra_k)), 2)
                    my = round(max(1.5, min(98.5, float(last_src.get("map_y") or 50.0) + step_y * extra_k)), 2)
                    mw = last_src.get("map_w") or 1.75
                    mh = last_src.get("map_h") or 3.25
                    mang = last_src.get("map_angle") or 0.0
                    vmod = str(last_src.get("vehicle_model") or cal_sp.get("vehicle_model") or "sedan").strip() or "sedan"

                conn.execute(
                    """
                    UPDATE spots
                    SET zone_id = ?, map_x = ?, map_y = ?, map_w = ?, map_h = ?, map_angle = ?,
                        map_custom = 1, group_code = ?, group_name = ?, near_unit_id = ?, near_unit_custom = ?,
                        vehicle_model = ?
                    WHERE spot_id = ?
                    """,
                    (
                        target_zone_id,
                        mx,
                        my,
                        mw,
                        mh,
                        mang,
                        gc,
                        final_gname,
                        final_near_uid,
                        final_near_custom,
                        vmod,
                        cal_sp["spot_id"],
                    ),
                )
                bound_count += 1

            # Spatial calibration count is strictly authoritative ("以空间定标的数量为准"):
            # delete ALL map_editor / UNASSIGNED placeholders in this group & on this camera!
            purge_ids: set[str] = set()
            for gsp in group_spots:
                gsid = str(gsp["spot_id"])
                if gsid not in cal_spot_ids and (
                    gsp["camera_id"] in ("UNASSIGNED", cam_id)
                    or str(gsp.get("last_engine") or "") == "map_editor"
                    or str(gsp["camera_id"]).startswith("CAM_MAP_")
                ):
                    purge_ids.add(gsid)
            for csp in all_cam_spots:
                csid = str(csp["spot_id"])
                if csid not in cal_spot_ids and str(csp.get("last_engine") or "") == "map_editor":
                    purge_ids.add(csid)
            for pid in purge_ids:
                conn.execute("DELETE FROM spots WHERE spot_id = ?", (pid,))

            # Normalize legacy MAP_* IDs on calibrated rows to clean {cam_id}-S{idx:02d}
            reserved_norm: set[str] = set()
            for idx, cal_sp in enumerate(cal_spots):
                old_sid = str(cal_sp["spot_id"])
                if old_sid.startswith("MAP_") or old_sid.startswith("SP_"):
                    pref_sid = f"{cam_id}-S{idx + 1:02d}"
                    new_sid = self._mint_unique_spot_id_conn(
                        conn, pref_sid, f"{cam_id}-S", reserved_norm
                    )
                    if new_sid != old_sid:
                        conn.execute(
                            "UPDATE spots SET spot_id = ? WHERE spot_id = ?",
                            (new_sid, old_sid),
                        )
        elif cal_spots:
            # Camera already owns placed calibrated spots in this group; sync metadata & purge stale placeholders
            purge_ids_meta: set[str] = set()
            for gsp in group_spots:
                gsid = str(gsp["spot_id"])
                if gsid not in cal_spot_ids and (
                    gsp["camera_id"] in ("UNASSIGNED", cam_id)
                    or str(gsp.get("last_engine") or "") == "map_editor"
                ):
                    purge_ids_meta.add(gsid)
            for csp in all_cam_spots:
                csid = str(csp["spot_id"])
                if csid not in cal_spot_ids and str(csp.get("last_engine") or "") == "map_editor":
                    purge_ids_meta.add(csid)
            for pid in purge_ids_meta:
                conn.execute("DELETE FROM spots WHERE spot_id = ?", (pid,))
            for cal_sp in cal_spots:
                conn.execute(
                    """
                    UPDATE spots
                    SET zone_id = ?, group_code = ?, group_name = ?, near_unit_id = ?, near_unit_custom = ?
                    WHERE spot_id = ?
                    """,
                    (
                        target_zone_id,
                        gc,
                        final_gname,
                        final_near_uid,
                        final_near_custom,
                        cal_sp["spot_id"],
                    ),
                )
            bound_count = len(cal_spots)
        elif placed_placeholder_slots or unassigned_group_spots:
            attach_list = placed_placeholder_slots or unassigned_group_spots
            for sp in attach_list:
                cap = max(1, int(sp.get("capacity") or 1))
                next_st = "FREE" if sp["status"] == "UNKNOWN" else sp["status"]
                next_fu = cap if next_st == "FREE" else int(sp.get("free_units") or 0)
                conn.execute(
                    """
                    UPDATE spots
                    SET camera_id = ?, zone_id = ?, status = ?, free_units = ?,
                        group_code = ?, group_name = ?, near_unit_id = ?, near_unit_custom = ?,
                        last_reason = '平面图车位组已绑定机位', last_seen_at = ?
                    WHERE spot_id = ?
                    """,
                    (
                        cam_id,
                        target_zone_id,
                        next_st,
                        next_fu,
                        gc,
                        final_gname,
                        final_near_uid,
                        final_near_custom,
                        ts,
                        sp["spot_id"],
                    ),
                )
                bound_count += 1
            self._normalize_camera_map_placeholders_conn(conn, cam_id, force=False)
        else:
            conn.execute(
                """
                UPDATE spots
                SET zone_id = ?, group_name = ?, near_unit_id = ?, near_unit_custom = ?
                WHERE group_code = ?
                """,
                (target_zone_id, final_gname, final_near_uid, final_near_custom, gc),
            )
            bound_count = len(group_spots)

        return {
            "camera_id": cam_id,
            "group_code": gc,
            "group_name": final_gname,
            "zone_id": target_zone_id,
            "near_unit_id": final_near_uid,
            "bound_spots": bound_count,
        }

    def _unbind_camera_all_groups_conn(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
    ) -> List[str]:
        now = time.time()
        grp_rows = conn.execute(
            """
            SELECT DISTINCT group_code
            FROM spots
            WHERE camera_id = ? AND COALESCE(group_code, '') != ''
            """,
            (camera_id,),
        ).fetchall()
        detached_groups: List[str] = []
        for gr in grp_rows:
            gc = str(gr["group_code"] or "").strip()
            if gc:
                self._detach_group_from_camera_conn(conn, gc, now)
                detached_groups.append(gc)
        conn.execute(
            "UPDATE cameras SET group_code = '', group_name = '' WHERE camera_id = ?",
            (camera_id,),
        )
        return detached_groups

    def unbind_camera(self, camera_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            cur_row = conn.execute(
                "SELECT camera_id FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
            if not cur_row:
                raise ValueError(f"机位 {camera_id} 不存在")
            cnt_row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM spots
                WHERE camera_id = ? AND COALESCE(group_code, '') != ''
                """,
                (camera_id,),
            ).fetchone()
            detached_spots = int(cnt_row["c"] or 0) if cnt_row else 0
            detached_groups = self._unbind_camera_all_groups_conn(conn, camera_id)
        self.reconcile_spot_groups_and_doors()
        return {
            "camera_id": camera_id,
            "detached_groups": detached_groups,
            "detached_spots": detached_spots,
            "camera": self.get_camera(camera_id),
        }

    def bind_camera_to_spot_group(
        self,
        camera_id: str,
        group_code: str,
        group_name: Optional[str] = None,
        near_unit_id: Optional[str] = None,
        zone_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            res = self._bind_camera_to_group_conn(
                conn,
                camera_id=camera_id,
                group_code=group_code,
                group_name=group_name,
                near_unit_id=near_unit_id,
                zone_id=zone_id,
            )
        self.reconcile_spot_groups_and_doors()
        return res

    def rename_spot_group(
        self,
        group_code: str,
        new_group_code: Optional[str] = None,
        group_name: Optional[str] = None,
        near_unit_id: Optional[str] = None,
        zone_id: Optional[str] = None,
        for_camera_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_gc = str(group_code or "").strip()
        if not raw_gc:
            raise ValueError("group_code 不能为空")
        explicit_zid = str(zone_id or "").strip()
        intended_cam = str(for_camera_id or "").strip()
        with self._lock, self._connect() as conn:
            gc, rows = self._resolve_group_spots_conn(conn, raw_gc)
            if not rows:
                raise ValueError(f"未找到车位组: {raw_gc}")
            if explicit_zid:
                z_exists = conn.execute(
                    "SELECT 1 FROM zones WHERE zone_id = ?", (explicit_zid,)
                ).fetchone()
                if not z_exists:
                    raise ValueError(f"所属小区片区不存在: {explicit_zid}")
            # If caller is renaming this floor-plan group while rebinding to a different camera
            # (or unbinding), detach any previously bound camera first so its calibrated spots
            # and camera metadata (group_name / zone_id) are never polluted.
            existing_cam_ids = {
                str(r["camera_id"])
                for r in rows
                if r.get("camera_id") and str(r["camera_id"]) != "UNASSIGNED"
            }
            if intended_cam and existing_cam_ids and existing_cam_ids != {intended_cam}:
                self._detach_group_from_camera_conn(conn, gc, time.time())
                gc, rows = self._resolve_group_spots_conn(conn, gc)
            target_gc = str(new_group_code or gc).strip() or gc
            if target_gc.upper() != gc.upper():
                placed_conflict = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM spots
                    WHERE UPPER(group_code) = UPPER(?)
                      AND map_x IS NOT NULL AND map_y IS NOT NULL
                    """,
                    (target_gc,),
                ).fetchone()["cnt"]
                if int(placed_conflict or 0) > 0:
                    raise ValueError(
                        f"平面图已存在编码为「{target_gc}」的车位组，请勿重名合并；若需绑定至 {target_gc}，请直接选择该车位组。"
                    )
            row_spot_ids = [str(r["spot_id"]) for r in rows]
            id_placeholders = ",".join("?" for _ in row_spot_ids)
            cam_ids = {
                str(r["camera_id"])
                for r in rows
                if r.get("camera_id") and str(r["camera_id"]) != "UNASSIGNED"
            }
            if intended_cam and intended_cam != "UNASSIGNED":
                cam_ids = {cid for cid in cam_ids if cid == intended_cam}
            elif intended_cam == "UNASSIGNED":
                cam_ids = set()
            if target_gc != gc:
                conn.execute(
                    f"UPDATE spots SET group_code = ? WHERE spot_id IN ({id_placeholders})",
                    [target_gc, *row_spot_ids],
                )
                for cid in cam_ids:
                    conn.execute(
                        "UPDATE cameras SET group_code = ? WHERE camera_id = ?",
                        (target_gc, cid),
                    )
            if group_name is not None:
                gname_clean = str(group_name).strip()
                conn.execute(
                    f"UPDATE spots SET group_name = ? WHERE spot_id IN ({id_placeholders})",
                    [gname_clean, *row_spot_ids],
                )
                for cid in cam_ids:
                    conn.execute(
                        "UPDATE cameras SET group_name = ? WHERE camera_id = ?",
                        (gname_clean, cid),
                    )
            if near_unit_id is not None:
                uid_clean = str(near_unit_id).strip()
                conn.execute(
                    f"UPDATE spots SET near_unit_id = ?, near_unit_custom = ? WHERE spot_id IN ({id_placeholders})",
                    [uid_clean, 1 if uid_clean else 0, *row_spot_ids],
                )
            if explicit_zid:
                conn.execute(
                    f"UPDATE spots SET zone_id = ? WHERE spot_id IN ({id_placeholders})",
                    [explicit_zid, *row_spot_ids],
                )
                for cid in cam_ids:
                    conn.execute(
                        "UPDATE cameras SET zone_id = ? WHERE camera_id = ?",
                        (explicit_zid, cid),
                    )
        self.reconcile_spot_groups_and_doors()
        return {
            "group_code": target_gc,
            "group_name": str(group_name or "").strip(),
            "near_unit_id": str(near_unit_id or "").strip() if near_unit_id is not None else None,
            "zone_id": explicit_zid or None,
        }

    def place_camera_on_sitemap(
        self,
        camera_id: str,
        map_x: Optional[float] = None,
        map_y: Optional[float] = None,
        end_map_x: Optional[float] = None,
        end_map_y: Optional[float] = None,
        map_angle: Optional[float] = None,
        orientation: str = "auto",
        group_code: Optional[str] = None,
        group_name: Optional[str] = None,
        near_unit_id: Optional[str] = None,
        zone_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Auto-draw and immediately bind a camera's calibrated stalls onto the floor plan
        without hijacking or detaching any other camera's placed spot group."""
        cam_id = str(camera_id or "").strip()
        if not cam_id or cam_id == "UNASSIGNED":
            raise ValueError("请指定要摆放到平面图的有效摄像头机位")

        smap = self.get_site_map(include_backdrop=False)
        aspect = min(2.6, max(0.45, float(smap.get("aspect_ratio") or 1.45)))
        world_w = round(100.0 * aspect, 3) if aspect >= 1.0 else 100.0
        world_h = 100.0 if aspect >= 1.0 else round(100.0 / aspect, 3)

        def to_world(nx_val: float, ny_val: float) -> Tuple[float, float]:
            return (float(nx_val) / 100.0) * world_w, (float(ny_val) / 100.0) * world_h

        def to_pct(sx_val: float, sy_val: float) -> Tuple[float, float]:
            return (
                round(min(98.5, max(1.5, (float(sx_val) / world_w) * 100.0)), 2),
                round(min(98.5, max(1.5, (float(sy_val) / world_h) * 100.0)), 2),
            )

        now = time.time()
        with self._lock, self._connect() as conn:
            cam_row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (cam_id,)
            ).fetchone()
            if not cam_row:
                raise ValueError(f"摄像头不存在: {cam_id}")
            cam = dict(cam_row)

            # Determine unique, non-conflicting group_code
            base_gc = (
                str(group_code or "").strip()
                or str(cam.get("group_code") or "").strip()
                or re.sub(r"^CAM_", "", cam_id, flags=re.IGNORECASE).strip()
                or cam_id
            )
            target_gc = base_gc
            suffix_idx = 2
            while True:
                conflict = conn.execute(
                    """
                    SELECT 1 FROM spots
                    WHERE UPPER(group_code) = UPPER(?)
                      AND camera_id != ?
                      AND map_x IS NOT NULL AND map_y IS NOT NULL
                    """,
                    (target_gc, cam_id),
                ).fetchone()
                if not conflict:
                    break
                target_gc = f"{base_gc}_{suffix_idx}"
                suffix_idx += 1

            target_gname = (
                str(group_name).strip()
                if group_name is not None and str(group_name).strip()
                else str(cam.get("group_name") or cam.get("mount_address") or cam.get("name") or target_gc).strip()
            )
            explicit_zid = str(zone_id or "").strip()
            if explicit_zid:
                z_ok = conn.execute(
                    "SELECT 1 FROM zones WHERE zone_id = ?", (explicit_zid,)
                ).fetchone()
                if not z_ok:
                    raise ValueError(f"所属小区片区不存在: {explicit_zid}")
            target_zid = explicit_zid or str(cam.get("zone_id") or "").strip()
            if not target_zid:
                first_z = conn.execute("SELECT zone_id FROM zones ORDER BY zone_id LIMIT 1").fetchone()
                target_zid = str(first_z["zone_id"]) if first_z else "ZONE_DEFAULT"

            cam_spots = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM spots WHERE camera_id = ?", (cam_id,)
                ).fetchall()
            ]
            real_cal = [
                r for r in cam_spots if str(r.get("last_engine") or "") != "map_editor"
            ]
            real_cal.sort(key=self._spot_natural_order_key)
            map_placeholders = [
                r for r in cam_spots if str(r.get("last_engine") or "") == "map_editor"
            ]
            map_placeholders.sort(key=self._spot_natural_order_key)

            if real_cal:
                active_spots = real_cal
                # Clean up stale map_editor placeholders belonging to this camera
                for ph in map_placeholders:
                    conn.execute("DELETE FROM spots WHERE spot_id = ?", (ph["spot_id"],))
            elif map_placeholders:
                active_spots = map_placeholders
            else:
                # Parse expected stall count from group_code / camera_id (e.g. 10BH2 -> 2, 10BV7 -> 7)
                m_cnt = re.search(r"[HV](\d+)$", base_gc, re.IGNORECASE) or re.search(
                    r"[HV](\d+)$", cam_id, re.IGNORECASE
                )
                inferred_cnt = min(24, max(1, int(m_cnt.group(1)))) if m_cnt else 2
                active_spots = []
                for idx in range(inferred_cnt):
                    sid = f"{cam_id}-S{idx + 1:02d}"
                    default_poly = [
                        [120 + (idx % 6) * 160, 220],
                        [250 + (idx % 6) * 160, 220],
                        [250 + (idx % 6) * 160, 440],
                        [120 + (idx % 6) * 160, 440],
                    ]
                    conn.execute(
                        """
                        INSERT INTO spots (
                            spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                            polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                            pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                            obstacle_type, action_hint, map_x, map_y, map_w, map_h, map_angle, map_custom,
                            group_code, group_name, near_unit_id, near_unit_custom, vehicle_model
                        ) VALUES (
                            ?, ?, ?, ?, 'standard', 0, 5.2, 1, ?, 'UNKNOWN', 0, 0, NULL, NULL, NULL, 1.0,
                            '平面图摆放待标定车位', 'map_editor', ?, NULL, 'none', '',
                            NULL, NULL, NULL, NULL, NULL, 0, ?, ?, '', 0, 'sedan'
                        )
                        """,
                        (
                            sid,
                            cam_id,
                            target_zid,
                            f"{idx + 1:02d}",
                            json.dumps(default_poly),
                            now,
                            target_gc,
                            target_gname,
                        ),
                    )
                    active_spots.append(
                        dict(
                            conn.execute(
                                "SELECT * FROM spots WHERE spot_id = ?", (sid,)
                            ).fetchone()
                        )
                    )

            count = len(active_spots)

            # Resolve target near_unit_id & smart fallback coordinates when map_x/map_y omitted
            units_all = [
                dict(u)
                for u in conn.execute("SELECT * FROM units").fetchall()
            ]
            resolved_uid = str(near_unit_id or "").strip()
            if not resolved_uid:
                for s0 in active_spots:
                    if str(s0.get("near_unit_id") or "").strip():
                        resolved_uid = str(s0["near_unit_id"]).strip()
                        break
            if not resolved_uid:
                b_match = re.match(r"^(\d+)", base_gc) or re.search(
                    r"(\d+)\s*[号栋]", f"{cam.get('name', '')} {target_gname}"
                )
                if b_match:
                    bnum = b_match.group(1)
                    cand_u = next(
                        (
                            u
                            for u in units_all
                            if extract_building_info(
                                str(u.get("unit_id") or ""), str(u.get("name") or "")
                            ).get("building_id")
                            == bnum
                            or str(u.get("unit_id") or "").startswith(f"{bnum}-")
                        ),
                        None,
                    )
                    if cand_u:
                        resolved_uid = str(cand_u["unit_id"])

            if map_x is None or map_y is None:
                anchor_u = next(
                    (u for u in units_all if str(u.get("unit_id")) == resolved_uid),
                    None,
                )
                hint_text = f"{base_gc} {target_gname} {cam.get('mount_address', '')} {cam.get('name', '')}"
                if anchor_u and anchor_u.get("map_x") is not None and anchor_u.get("map_y") is not None:
                    ux, uy = float(anchor_u["map_x"]), float(anchor_u["map_y"])
                    if any(k in hint_text for k in ("东", "右")):
                        cx_pct, cy_pct = ux + 4.8, uy + 0.5
                    elif any(k in hint_text for k in ("西", "左")):
                        cx_pct, cy_pct = ux - 5.2, uy + 0.5
                    elif "南" in hint_text:
                        cx_pct, cy_pct = ux, uy + 6.5
                    else:
                        cx_pct, cy_pct = ux - 3.5, uy - 7.5
                else:
                    zrow = conn.execute(
                        "SELECT map_x, map_y, map_w, map_h FROM zones WHERE zone_id = ?",
                        (target_zid,),
                    ).fetchone()
                    if zrow:
                        cx_pct = float(zrow["map_x"] or 45.0) + float(zrow["map_w"] or 12.0) * 0.5
                        cy_pct = float(zrow["map_y"] or 45.0) + float(zrow["map_h"] or 6.0) * 0.5
                    else:
                        cx_pct, cy_pct = 50.0, 50.0

                # Offset cleanly if another camera's spots already occupy this neighborhood
                other_pts = [
                    (float(r["map_x"]), float(r["map_y"]))
                    for r in conn.execute(
                        """
                        SELECT map_x, map_y FROM spots
                        WHERE camera_id != ? AND map_x IS NOT NULL AND map_y IS NOT NULL
                        """,
                        (cam_id,),
                    ).fetchall()
                ]
                for step_try in range(8):
                    cand_x = min(94.0, max(6.0, cx_pct + (step_try % 3) * 3.8))
                    cand_y = min(94.0, max(6.0, cy_pct + (step_try // 3) * 3.6))
                    if all(
                        math.hypot(cand_x - ox, cand_y - oy) >= 3.2
                        for ox, oy in other_pts
                    ):
                        cx_pct, cy_pct = cand_x, cand_y
                        break
            else:
                cx_pct = min(98.0, max(2.0, float(map_x)))
                cy_pct = min(98.0, max(2.0, float(map_y)))

            # Determine horizontal (parallel H) vs vertical (perpendicular V) stall geometry
            orient_norm = str(orientation or "auto").strip().lower()
            if orient_norm in ("horizontal", "parallel", "h"):
                is_horiz = True
            elif orient_norm in ("vertical", "perpendicular", "perp", "v"):
                is_horiz = False
            else:
                is_horiz = bool(
                    re.search(r"H\d+$", base_gc, re.IGNORECASE)
                    or re.search(r"H\d+$", cam_id, re.IGNORECASE)
                    or ("横" in f"{cam.get('name', '')} {target_gname}")
                )

            stall_w = 2.74 if is_horiz else 1.20
            stall_h = 1.35 if is_horiz else 2.25
            step_world = 3.12 if is_horiz else 1.52
            v_model = "parallel" if is_horiz else "sedan"

            cx_w, cy_w = to_world(cx_pct, cy_pct)

            # Compute row tangent angle (explicit end point > explicit map_angle > nearest road tangent)
            has_span = end_map_x is not None and end_map_y is not None
            ex_w, ey_w = (
                to_world(float(end_map_x), float(end_map_y))
                if has_span
                else (cx_w, cy_w)
            )
            span_dist = math.hypot(ex_w - cx_w, ey_w - cy_w) if has_span else 0.0

            if has_span and span_dist >= 1.2 and count >= 2:
                row_angle = round(math.degrees(math.atan2(ey_w - cy_w, ex_w - cx_w)), 1)
                if row_angle > 90.0:
                    row_angle -= 180.0
                elif row_angle <= -90.0:
                    row_angle += 180.0
                world_centers = [
                    (
                        cx_w + (ex_w - cx_w) * (idx / max(1, count - 1)),
                        cy_w + (ey_w - cy_w) * (idx / max(1, count - 1)),
                    )
                    for idx in range(count)
                ]
            else:
                if map_angle is not None:
                    row_angle = float(map_angle)
                else:
                    row_angle = 0.0
                    best_d2 = 14.0 * 14.0
                    topo = smap.get("topology") or {}
                    polylines = list(topo.get("routes") or []) + list(topo.get("roads") or [])
                    for item in polylines:
                        pts = item.get("points") or []
                        for pidx in range(len(pts) - 1):
                            ax, ay = to_world(pts[pidx][0], pts[pidx][1])
                            bx, by = to_world(pts[pidx + 1][0], pts[pidx + 1][1])
                            vx, vy = bx - ax, by - ay
                            seg_l2 = vx * vx + vy * vy
                            if seg_l2 < 1e-4:
                                continue
                            t_proj = max(0.0, min(1.0, ((cx_w - ax) * vx + (cy_w - ay) * vy) / seg_l2))
                            px_seg, py_seg = ax + t_proj * vx, ay + t_proj * vy
                            d2 = (cx_w - px_seg) ** 2 + (cy_w - py_seg) ** 2
                            if d2 < best_d2:
                                best_d2 = d2
                                ang = math.degrees(math.atan2(vy, vx))
                                if ang > 90.0:
                                    ang -= 180.0
                                elif ang <= -90.0:
                                    ang += 180.0
                                row_angle = round(ang, 1)

                rad = math.radians(row_angle)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                mid = (count - 1) / 2.0
                world_centers = [
                    (
                        cx_w + (idx - mid) * step_world * cos_a,
                        cy_w + (idx - mid) * step_world * sin_a,
                    )
                    for idx in range(count)
                ]

            placed_records: List[Dict[str, Any]] = []
            for idx, sp in enumerate(active_spots):
                wx, wy = world_centers[idx]
                nx_pct, ny_pct = to_pct(wx, wy)
                new_label = f"{idx + 1:02d}"
                conn.execute(
                    """
                    UPDATE spots
                    SET zone_id = ?, label = ?, map_x = ?, map_y = ?, map_w = ?, map_h = ?,
                        map_angle = ?, map_custom = 1, group_code = ?, group_name = ?,
                        near_unit_id = ?, near_unit_custom = ?, vehicle_model = ?
                    WHERE spot_id = ?
                    """,
                    (
                        target_zid,
                        new_label,
                        nx_pct,
                        ny_pct,
                        stall_w,
                        stall_h,
                        round(row_angle, 1),
                        target_gc,
                        target_gname,
                        resolved_uid,
                        1 if resolved_uid else 0,
                        v_model,
                        sp["spot_id"],
                    ),
                )
                placed_records.append(
                    {
                        "spot_id": sp["spot_id"],
                        "label": new_label,
                        "map_x": nx_pct,
                        "map_y": ny_pct,
                        "map_w": stall_w,
                        "map_h": stall_h,
                        "map_angle": round(row_angle, 1),
                        "vehicle_model": v_model,
                    }
                )

            conn.execute(
                """
                UPDATE cameras
                SET zone_id = ?, group_code = ?, group_name = ?
                WHERE camera_id = ?
                """,
                (target_zid, target_gc, target_gname, cam_id),
            )

            # Expand zone bounding box gently if needed
            z_pts = conn.execute(
                """
                SELECT MIN(map_x) AS min_x, MAX(map_x) AS max_x,
                       MIN(map_y) AS min_y, MAX(map_y) AS max_y
                FROM spots
                WHERE zone_id = ? AND map_x IS NOT NULL AND map_y IS NOT NULL
                """,
                (target_zid,),
            ).fetchone()
            if z_pts and z_pts["min_x"] is not None:
                zx = round(max(1.0, float(z_pts["min_x"]) - 3.0), 2)
                zy = round(max(1.0, float(z_pts["min_y"]) - 2.6), 2)
                zw = round(min(98.0 - zx, max(7.0, float(z_pts["max_x"]) - float(z_pts["min_x"]) + 6.0)), 2)
                zh = round(min(98.0 - zy, max(5.0, float(z_pts["max_y"]) - float(z_pts["min_y"]) + 5.2)), 2)
                conn.execute(
                    "UPDATE zones SET map_x = ?, map_y = ?, map_w = ?, map_h = ? WHERE zone_id = ?",
                    (zx, zy, zw, zh, target_zid),
                )

        self.reconcile_spot_groups_and_doors()
        return {
            "camera_id": cam_id,
            "group_code": target_gc,
            "group_name": target_gname,
            "zone_id": target_zid,
            "near_unit_id": resolved_uid,
            "placed_spots": count,
            "spot_count": count,
            "orientation": "horizontal" if is_horiz else "vertical",
            "map_angle": round(row_angle, 1),
            "spots": placed_records,
        }

    def delete_spot_group(
        self, group_code: str, camera_action: str = "keep"
    ) -> Dict[str, Any]:
        raw_gc = str(group_code or "").strip()
        if not raw_gc:
            raise ValueError("group_code 不能为空")
        action = str(camera_action or "keep").strip().lower()
        deleted_cameras: List[str] = []
        with self._lock, self._connect() as conn:
            gc, rows = self._resolve_group_spots_conn(conn, raw_gc)
            if not rows:
                raise ValueError(f"未找到车位组: {raw_gc}")
            cam_ids = sorted(
                {
                    str(r["camera_id"])
                    for r in rows
                    if r.get("camera_id") and str(r["camera_id"]) != "UNASSIGNED"
                }
            )
            spot_ids = [str(r["spot_id"]) for r in rows]
            conn.executemany(
                "DELETE FROM spots WHERE spot_id = ?",
                [(sid,) for sid in spot_ids],
            )
            for cid in cam_ids:
                rem = int(
                    conn.execute(
                        "SELECT COUNT(*) AS cnt FROM spots WHERE camera_id = ?", (cid,)
                    ).fetchone()["cnt"]
                )
                if action == "delete_camera" and rem == 0:
                    conn.execute("DELETE FROM cameras WHERE camera_id = ?", (cid,))
                    deleted_cameras.append(cid)
                elif rem == 0:
                    conn.execute(
                        "UPDATE cameras SET group_code = '', group_name = '' WHERE camera_id = ?",
                        (cid,),
                    )
        self.reconcile_spot_groups_and_doors()
        return {
            "deleted": True,
            "deleted_group_code": gc,
            "deleted_spots": len(rows),
            "deleted_camera_ids": deleted_cameras,
        }

    def apply_sitemap_layout(
        self,
        zones_payload: Optional[List[Dict[str, Any]]] = None,
        units_payload: Optional[List[Dict[str, Any]]] = None,
        spots_payload: Optional[List[Dict[str, Any]]] = None,
        deleted_spot_ids: Optional[List[str]] = None,
        deleted_unit_ids: Optional[List[str]] = None,
        topology_patch: Optional[Dict[str, Any]] = None,
        span_meters: Optional[float] = None,
        replace_units: bool = False,
        reset_spot_positions: bool = False,
    ) -> Dict[str, Any]:
        now = time.time()
        explicit_zone_ids: set[str] = set()
        touched_zone_ids: set[str] = set()
        updated_spots_count = 0
        deleted_spots_count = 0

        with self._lock, self._connect() as conn:
            if reset_spot_positions:
                conn.execute(
                    """
                    UPDATE spots
                    SET map_x = NULL, map_y = NULL, map_w = NULL, map_h = NULL,
                        map_angle = NULL, map_custom = 0
                    """
                )

            if zones_payload:
                existing_zones = {
                    r["zone_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM zones").fetchall()
                }
                for zp in zones_payload:
                    zid = str(zp.get("zone_id") or "").strip()
                    if not zid:
                        continue
                    explicit_zone_ids.add(zid)
                    cur = existing_zones.get(zid)
                    mx = round(max(0.5, min(98.0, float(zp.get("map_x", cur["map_x"] if cur else 20.0)))), 2)
                    my = round(max(0.5, min(98.0, float(zp.get("map_y", cur["map_y"] if cur else 20.0)))), 2)
                    mw = round(max(3.0, min(99.0 - mx, float(zp.get("map_w", cur["map_w"] if cur else 16.0)))), 2)
                    mh = round(max(3.0, min(99.0 - my, float(zp.get("map_h", cur["map_h"] if cur else 8.0)))), 2)
                    angle = round(max(-180.0, min(180.0, float(zp.get("layout_angle", cur["layout_angle"] if cur else 0.0)))), 1)
                    name = str(zp.get("name") or (cur["name"] if cur else zid)).strip()
                    dead_end = float(zp.get("dead_end_penalty", cur["dead_end_penalty"] if cur else 0.0))
                    gate_acc = str(zp.get("gate_access") or (cur["gate_access"] if cur else "south,east"))
                    desc = str(zp.get("description") if zp.get("description") is not None else (cur["description"] if cur else ""))
                    is_demo = int(zp.get("is_demo_seed", 0))

                    if cur:
                        conn.execute(
                            """
                            UPDATE zones
                            SET name = ?, dead_end_penalty = ?, gate_access = ?,
                                map_x = ?, map_y = ?, map_w = ?, map_h = ?,
                                description = ?, layout_angle = ?, is_demo_seed = ?
                            WHERE zone_id = ?
                            """,
                            (name, dead_end, gate_acc, mx, my, mw, mh, desc, angle, is_demo, zid),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO zones (
                                zone_id, name, dead_end_penalty, gate_access,
                                map_x, map_y, map_w, map_h, description, layout_angle, is_demo_seed
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (zid, name, dead_end, gate_acc, mx, my, mw, mh, desc, angle, is_demo),
                        )

            if deleted_spot_ids:
                for sid in deleted_spot_ids:
                    sid_clean = str(sid or "").strip()
                    if not sid_clean:
                        continue
                    old_sp = conn.execute(
                        "SELECT zone_id FROM spots WHERE spot_id = ?", (sid_clean,)
                    ).fetchone()
                    if old_sp:
                        touched_zone_ids.add(old_sp["zone_id"])
                        deleted_spots_count += 1
                    conn.execute("DELETE FROM spots WHERE spot_id = ?", (sid_clean,))

            deferred_group_binds: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
            deferred_group_unbinds: set[str] = set()

            if spots_payload:
                existing_spots = {
                    r["spot_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM spots").fetchall()
                }
                cams_all = {
                    r["camera_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM cameras").fetchall()
                }
                zones_all = {
                    r["zone_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM zones").fetchall()
                }

                # Precompute group centroids so newly drawn stalls in the same group share ONE zone_id
                group_centroids: Dict[str, List[Tuple[float, float]]] = {}
                for sp in spots_payload:
                    gc_pre = str(sp.get("group_code") or "").strip()
                    if gc_pre and sp.get("map_x") is not None and sp.get("map_y") is not None:
                        group_centroids.setdefault(gc_pre, []).append(
                            (float(sp["map_x"]), float(sp["map_y"]))
                        )
                group_zone_choice: Dict[str, str] = {}
                if zones_all:
                    for gc_pre, pts_pre in group_centroids.items():
                        gx = sum(p[0] for p in pts_pre) / len(pts_pre)
                        gy = sum(p[1] for p in pts_pre) / len(pts_pre)
                        group_zone_choice[gc_pre] = min(
                            zones_all.keys(),
                            key=lambda k: math.hypot(
                                gx - (float(zones_all[k].get("map_x") or 50.0) + float(zones_all[k].get("map_w") or 12.0) * 0.5),
                                gy - (float(zones_all[k].get("map_y") or 50.0) + float(zones_all[k].get("map_h") or 6.0) * 0.5),
                            ),
                        )

                # Detect cameras that already own real calibrated image-space stalls
                cams_with_real_cal: set[str] = {
                    r["camera_id"]
                    for r in conn.execute(
                        """
                        SELECT DISTINCT camera_id FROM spots
                        WHERE camera_id != 'UNASSIGNED'
                          AND last_engine != 'map_editor'
                        """
                    ).fetchall()
                }
                unplaced_cal_sids_by_cam: Dict[str, set[str]] = {}
                for r in conn.execute(
                    """
                    SELECT spot_id, camera_id FROM spots
                    WHERE camera_id != 'UNASSIGNED'
                      AND last_engine != 'map_editor'
                      AND (map_x IS NULL OR map_custom = 0)
                    """
                ).fetchall():
                    unplaced_cal_sids_by_cam.setdefault(r["camera_id"], set()).add(r["spot_id"])

                # If spots_payload directly updates calibrated spots themselves with coordinates,
                # mark those calibrated spots as placed so manual additions in the same batch don't steal them.
                for sp_pre in spots_payload:
                    sid_pre = str(sp_pre.get("spot_id") or "").strip()
                    cur_pre = existing_spots.get(sid_pre)
                    if (
                        cur_pre
                        and str(cur_pre.get("last_engine") or "") != "map_editor"
                        and ("map_x" in sp_pre or cur_pre.get("map_x") is not None)
                    ):
                        cid_pre = str(sp_pre.get("camera_id") or cur_pre["camera_id"]).strip()
                        if cid_pre in unplaced_cal_sids_by_cam:
                            unplaced_cal_sids_by_cam[cid_pre].discard(sid_pre)

                cams_with_unplaced_cal: set[str] = {
                    cid_k for cid_k, sids_v in unplaced_cal_sids_by_cam.items() if sids_v
                }

                for idx, sp in enumerate(spots_payload):
                    sid = str(sp.get("spot_id") or "").strip()
                    if not sid:
                        continue
                    cur_sp = existing_spots.get(sid)
                    mx = round(max(1.0, min(99.0, float(sp.get("map_x", cur_sp.get("map_x") if cur_sp and cur_sp.get("map_x") is not None else 50.0)))), 2)
                    my = round(max(1.0, min(99.0, float(sp.get("map_y", cur_sp.get("map_y") if cur_sp and cur_sp.get("map_y") is not None else 50.0)))), 2)
                    mw = round(max(0.8, min(28.0, float(sp.get("map_w", cur_sp.get("map_w") if cur_sp and cur_sp.get("map_w") is not None else 1.75)))), 2)
                    mh = round(max(0.8, min(28.0, float(sp.get("map_h", cur_sp.get("map_h") if cur_sp and cur_sp.get("map_h") is not None else 3.25)))), 2)
                    raw_ang = float(sp.get("map_angle", cur_sp.get("map_angle") if cur_sp and cur_sp.get("map_angle") is not None else 0.0))
                    m_ang = round(((raw_ang + 180.0) % 360.0) - 180.0, 1)
                    m_custom = int(sp.get("map_custom", 1))

                    if cur_sp:
                        lbl = str(sp.get("label") or cur_sp["label"]).strip()
                        zid = str(sp.get("zone_id") or cur_sp["zone_id"]).strip()
                        req_cid = str(sp.get("camera_id") or cur_sp["camera_id"]).strip()
                        st_type = str(sp.get("spot_type") or cur_sp["spot_type"]).strip()
                        comp = 1 if sp.get("compact_only", cur_sp["compact_only"]) else 0
                        cap = max(1, int(sp.get("capacity", cur_sp["capacity"])))
                        grp_c = str(
                            sp.get("group_code")
                            if sp.get("group_code") is not None
                            else (cur_sp.get("group_code") or "")
                        ).strip()
                        grp_n = str(
                            sp.get("group_name")
                            if sp.get("group_name") is not None
                            else (cur_sp.get("group_name") or "")
                        ).strip()
                        near_u = str(
                            sp.get("near_unit_id")
                            if sp.get("near_unit_id") is not None
                            else (cur_sp.get("near_unit_id") or "")
                        ).strip()
                        near_ucust = (
                            (1 if near_u else 0)
                            if "near_unit_id" in sp
                            else int(cur_sp.get("near_unit_custom") or 0)
                        )
                        v_mod = str(
                            sp.get("vehicle_model")
                            if sp.get("vehicle_model") is not None
                            else (cur_sp.get("vehicle_model") or "sedan")
                        ).strip() or "sedan"

                        # Smart 1-to-1 binding when user binds a drawn/placeholder group to a calibrated camera
                        if (
                            req_cid != "UNASSIGNED"
                            and grp_c
                            and req_cid in cams_with_unplaced_cal
                            and (
                                cur_sp["camera_id"] != req_cid
                                or str(cur_sp.get("last_engine") or "") == "map_editor"
                            )
                        ):
                            deferred_group_binds[grp_c] = (
                                req_cid,
                                grp_n or None,
                                near_u if "near_unit_id" in sp else None,
                            )
                            cid = "UNASSIGNED"
                        elif (
                            cur_sp["camera_id"] != "UNASSIGNED"
                            and req_cid == "UNASSIGNED"
                            and grp_c
                            and str(cur_sp.get("last_engine") or "") != "map_editor"
                        ):
                            deferred_group_unbinds.add(grp_c)
                            cid = cur_sp["camera_id"]
                        else:
                            cid = req_cid

                        if cid and cid != "UNASSIGNED" and cid in cams_all and "zone_id" not in sp:
                            zid = cams_all[cid]["zone_id"]
                        elif grp_c and grp_c in group_zone_choice and "zone_id" not in sp:
                            zid = group_zone_choice[grp_c]

                        next_status = cur_sp["status"]
                        next_free = cur_sp["free_units"]
                        if cur_sp["camera_id"] == "UNASSIGNED" and cid != "UNASSIGNED" and next_status == "UNKNOWN":
                            next_status = "FREE"
                            next_free = cap
                        elif cid == "UNASSIGNED" and cur_sp["camera_id"] != "UNASSIGNED":
                            next_status = "UNKNOWN"
                            next_free = 0
                        conn.execute(
                            """
                            UPDATE spots
                            SET label = ?, camera_id = ?, zone_id = ?, spot_type = ?, compact_only = ?, capacity = ?,
                                status = ?, free_units = ?,
                                map_x = ?, map_y = ?, map_w = ?, map_h = ?, map_angle = ?, map_custom = ?,
                                group_code = ?, group_name = ?, near_unit_id = ?, near_unit_custom = ?,
                                vehicle_model = ?
                            WHERE spot_id = ?
                            """,
                            (
                                lbl,
                                cid,
                                zid,
                                st_type,
                                comp,
                                cap,
                                next_status,
                                next_free,
                                mx,
                                my,
                                mw,
                                mh,
                                m_ang,
                                m_custom,
                                grp_c,
                                grp_n,
                                near_u,
                                near_ucust,
                                v_mod,
                                sid,
                            ),
                        )
                        if cid == "UNASSIGNED":
                            self._rename_spot_id_to_map_placeholder_conn(
                                conn, sid, grp_c, zid, idx + 1, now
                            )
                        touched_zone_ids.add(zid)
                        touched_zone_ids.add(cur_sp["zone_id"])
                        updated_spots_count += 1
                    else:
                        req_cam = str(sp.get("camera_id") or "").strip()
                        req_zone = str(sp.get("zone_id") or "").strip()
                        grp_c = str(sp.get("group_code") or "").strip()
                        grp_n = str(sp.get("group_name") or "").strip()
                        near_u = str(sp.get("near_unit_id") or "").strip()
                        near_ucust = 1 if ("near_unit_id" in sp and near_u) else 0

                        if (
                            req_cam
                            and req_cam != "UNASSIGNED"
                            and grp_c
                            and req_cam in cams_with_unplaced_cal
                        ):
                            deferred_group_binds[grp_c] = (
                                req_cam,
                                grp_n or None,
                                near_u if "near_unit_id" in sp else None,
                            )
                            req_cam = "UNASSIGNED"

                        if req_cam and req_cam != "UNASSIGNED" and req_cam in cams_all and not req_zone:
                            req_zone = cams_all[req_cam]["zone_id"]
                        if not req_zone or req_zone not in zones_all:
                            if grp_c and grp_c in group_zone_choice:
                                req_zone = group_zone_choice[grp_c]
                            elif zones_all:
                                best_zid = min(
                                    zones_all.keys(),
                                    key=lambda k: math.hypot(
                                        mx - (float(zones_all[k].get("map_x") or 50.0) + float(zones_all[k].get("map_w") or 12.0) * 0.5),
                                        my - (float(zones_all[k].get("map_y") or 50.0) + float(zones_all[k].get("map_h") or 6.0) * 0.5),
                                    ),
                                )
                                req_zone = best_zid
                            else:
                                req_zone = "Z_MAP_CUSTOM"
                                conn.execute(
                                    """
                                    INSERT INTO zones (
                                        zone_id, name, dead_end_penalty, gate_access,
                                        map_x, map_y, map_w, map_h, description, layout_angle, is_demo_seed
                                    ) VALUES (?, '自定义平面停车带', 0.0, 'south,east', ?, ?, 12.0, 6.0, '平面图直接添加车位', 0.0, 0)
                                    """,
                                    (req_zone, max(2.0, mx - 6.0), max(2.0, my - 3.0)),
                                )
                                zones_all[req_zone] = {"zone_id": req_zone}
                        if req_cam == "UNASSIGNED":
                            pass
                        elif not req_cam or req_cam not in cams_all:
                            cam_in_zone = next(
                                (cid for cid, c in cams_all.items() if c["zone_id"] == req_zone),
                                None,
                            )
                            if cam_in_zone and grp_c and cam_in_zone in cams_with_unplaced_cal:
                                deferred_group_binds[grp_c] = (
                                    cam_in_zone,
                                    grp_n or None,
                                    near_u if "near_unit_id" in sp else None,
                                )
                                req_cam = "UNASSIGNED"
                            elif cam_in_zone and cam_in_zone in cams_with_real_cal:
                                req_cam = "UNASSIGNED"
                            elif cam_in_zone:
                                req_cam = cam_in_zone
                            else:
                                req_cam = "UNASSIGNED"

                        lbl = str(sp.get("label") or f"{idx+1:02d}").strip()
                        st_type = str(sp.get("spot_type") or "standard").strip()
                        comp = 1 if sp.get("compact_only") else 0
                        cap = max(1, int(sp.get("capacity", 1)))
                        v_mod = str(sp.get("vehicle_model") or ("compact" if comp else "sedan")).strip() or "sedan"
                        default_st = "UNKNOWN" if req_cam == "UNASSIGNED" else "FREE"
                        raw_st = str(sp.get("status") or default_st).upper()
                        if raw_st not in ("FREE", "OCCUPIED", "BLOCKED", "UNKNOWN"):
                            raw_st = default_st
                        free_u = cap if raw_st == "FREE" else 0
                        occ_u = cap if raw_st == "OCCUPIED" else 0
                        default_poly = sp.get("polygon") or [
                            [120 + (idx % 6) * 160, 220],
                            [250 + (idx % 6) * 160, 220],
                            [250 + (idx % 6) * 160, 440],
                            [120 + (idx % 6) * 160, 440],
                        ]
                        conn.execute(
                            """
                            INSERT INTO spots (
                                spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                                polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                                pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                                obstacle_type, action_hint, map_x, map_y, map_w, map_h, map_angle, map_custom,
                                group_code, group_name, near_unit_id, near_unit_custom, vehicle_model
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, 5.2, ?, ?, ?, ?, ?, NULL, NULL, NULL, 1.0,
                                ?, 'map_editor', ?, ?, 'none', '', ?, ?, ?, ?, ?, 1,
                                ?, ?, ?, ?, ?
                            )
                            """,
                            (
                                sid,
                                req_cam,
                                req_zone,
                                lbl,
                                st_type,
                                comp,
                                cap,
                                json.dumps(default_poly),
                                raw_st,
                                free_u,
                                occ_u,
                                "平面图预设车位（待接入摄像头）" if req_cam == "UNASSIGNED" else "平面图微调新增车位",
                                now,
                                None if req_cam == "UNASSIGNED" else now,
                                mx,
                                my,
                                mw,
                                mh,
                                m_ang,
                                grp_c,
                                grp_n,
                                near_u,
                                near_ucust,
                                v_mod,
                            ),
                        )
                        touched_zone_ids.add(req_zone)
                        updated_spots_count += 1

                for unb_gc in deferred_group_unbinds:
                    self._detach_group_from_camera_conn(conn, unb_gc, now)
                for bind_gc, (bind_cid, bind_gname, bind_near_u) in deferred_group_binds.items():
                    self._bind_camera_to_group_conn(
                        conn,
                        camera_id=bind_cid,
                        group_code=bind_gc,
                        group_name=bind_gname,
                        near_unit_id=bind_near_u,
                        now=now,
                    )

                touched_cams = {
                    str(sp.get("camera_id") or "").strip()
                    for sp in spots_payload
                    if str(sp.get("camera_id") or "").strip() not in ("", "UNASSIGNED")
                }
                for tcid in touched_cams:
                    self._normalize_camera_map_placeholders_conn(conn, tcid, force=False)

            for tzid in touched_zone_ids:
                if tzid in explicit_zone_ids:
                    continue
                sp_rows = conn.execute(
                    "SELECT map_x, map_y, map_angle FROM spots WHERE zone_id = ? AND map_x IS NOT NULL AND map_y IS NOT NULL",
                    (tzid,),
                ).fetchall()
                if sp_rows:
                    xs = [float(r["map_x"]) for r in sp_rows]
                    ys = [float(r["map_y"]) for r in sp_rows]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    zw = round(max(5.0, min(40.0, (max_x - min_x) + 4.5)), 2)
                    zh = round(max(4.5, min(36.0, (max_y - min_y) + 4.5)), 2)
                    zx = round(max(0.5, min(99.0 - zw, (min_x + max_x) / 2.0 - zw / 2.0)), 2)
                    zy = round(max(0.5, min(99.0 - zh, (min_y + max_y) / 2.0 - zh / 2.0)), 2)
                    first_ang = float(sp_rows[0]["map_angle"] or 0.0)
                    conn.execute(
                        "UPDATE zones SET map_x = ?, map_y = ?, map_w = ?, map_h = ?, layout_angle = ? WHERE zone_id = ?",
                        (zx, zy, zw, zh, round(first_ang, 1), tzid),
                    )

            if deleted_unit_ids:
                for duid in deleted_unit_ids:
                    duid_clean = str(duid or "").strip()
                    if duid_clean:
                        conn.execute("DELETE FROM units WHERE unit_id = ?", (duid_clean,))
                        conn.execute("DELETE FROM unit_zone_dist WHERE unit_id = ?", (duid_clean,))

            if units_payload:
                if replace_units:
                    conn.execute("DELETE FROM units")
                existing_units = {
                    r["unit_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM units").fetchall()
                }
                for up in units_payload:
                    raw_uid = str(up.get("unit_id") or "").strip()
                    raw_name = str(up.get("name") or "").strip()
                    if not raw_uid and not raw_name:
                        continue
                    cur_pre = existing_units.get(raw_uid)
                    uid, uname = normalize_unit_id_and_name(
                        raw_uid, raw_name or (str(cur_pre.get("name") or "") if cur_pre else "")
                    )
                    if not uid:
                        continue
                    cur_u = existing_units.get(uid)
                    raw_ux = up.get("map_x") if up.get("map_x") is not None else up.get("x")
                    raw_uy = up.get("map_y") if up.get("map_y") is not None else up.get("y")
                    raw_uw = up.get("map_w") if up.get("map_w") is not None else up.get("w")
                    raw_uh = up.get("map_h") if up.get("map_h") is not None else up.get("h")
                    ux = round(max(2.0, min(98.0, float(raw_ux if raw_ux is not None else (cur_u["map_x"] if cur_u else 50.0)))), 2)
                    uy = round(max(2.0, min(98.0, float(raw_uy if raw_uy is not None else (cur_u["map_y"] if cur_u else 50.0)))), 2)
                    uw = round(max(4.0, min(28.0, float(raw_uw if raw_uw is not None else (cur_u["map_w"] if cur_u else 8.0)))), 2)
                    uh = round(max(3.0, min(20.0, float(raw_uh if raw_uh is not None else (cur_u["map_h"] if cur_u else 4.2)))), 2)
                    ugate = str(up.get("default_gate") or (cur_u["default_gate"] if cur_u else "south"))

                    if up.get("door_x") is not None and up.get("door_y") is not None:
                        dx = round(max(0.5, min(99.5, float(up["door_x"]))), 2)
                        dy = round(max(0.5, min(99.5, float(up["door_y"]))), 2)
                        d_custom = int(up.get("door_custom", 1))
                    elif cur_u and int(cur_u.get("door_custom") or 0) == 1 and cur_u.get("door_x") is not None:
                        old_ux = float(cur_u.get("map_x") or ux)
                        old_uy = float(cur_u.get("map_y") or uy)
                        dx = round(max(0.5, min(99.5, float(cur_u["door_x"]) + (ux - old_ux))), 2)
                        dy = round(max(0.5, min(99.5, float(cur_u["door_y"]) + (uy - old_uy))), 2)
                        d_custom = 1
                    else:
                        dx, dy, d_custom = None, None, 0

                    if cur_u and not replace_units:
                        conn.execute(
                            """
                            UPDATE units
                            SET name = ?, default_gate = ?, map_x = ?, map_y = ?, map_w = ?, map_h = ?,
                                door_x = ?, door_y = ?, door_custom = ?
                            WHERE unit_id = ?
                            """,
                            (uname, ugate, ux, uy, uw, uh, dx, dy, d_custom, uid),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO units (
                                unit_id, name, default_gate, map_x, map_y, map_w, map_h,
                                door_x, door_y, door_custom
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (uid, uname, ugate, ux, uy, uw, uh, dx, dy, d_custom),
                        )

        if topology_patch or span_meters is not None:
            cur_map = self.get_site_map(include_backdrop=True)
            merged_topo = dict(cur_map["topology"])
            patch_obj = topology_patch or {}
            for k in ("gates", "roads", "walkways", "greens", "routes"):
                if k in patch_obj and isinstance(patch_obj[k], list):
                    merged_topo[k] = patch_obj[k]
            sync_routes = ("roads" in patch_obj) and ("routes" not in patch_obj)
            merged_topo = normalize_site_map_topology(
                merged_topo, sync_routes_from_roads=sync_routes
            )
            meta = dict(cur_map.get("meta") or {})
            meta["layout_tuned_at"] = now
            if span_meters is not None:
                meta["estimated_span_meters"] = max(60.0, min(600.0, float(span_meters)))
            self.save_site_map(
                source_type=cur_map["source_type"] if cur_map["source_type"] != "seed" else "manual_tuned",
                backdrop_jpeg_b64=None,
                aspect_ratio=cur_map["aspect_ratio"],
                topology=merged_topo,
                meta=meta,
            )

        self.reconcile_spot_groups_and_doors(span_meters=span_meters)
        return {
            "updated_spots": updated_spots_count,
            "deleted_spots": deleted_spots_count,
            "zones": self.list_zones(),
            "units": self.list_units(),
            "spots": self.list_spots(),
            "site_map": self.get_site_map(include_backdrop=False),
        }

    def reset_seed_data(self) -> None:
        with self._lock, self._connect() as conn:
            for tbl in ("spots", "cameras", "unit_zone_dist", "zones", "units", "site_map"):
                conn.execute(f"DELETE FROM {tbl}")
            self._site_map_cache = None
        self._seed_if_empty()
        self.reconcile_spot_groups_and_doors()


db = Database()
