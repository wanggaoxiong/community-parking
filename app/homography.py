from typing import List, Optional, Tuple
import cv2
import numpy as np


Point = List[float]
Polygon = List[Point]


def subdivide_perspective_row(
    outer_quad: Polygon,
    count: int,
    direction: str = "auto",
    foreshortening_gamma: float = 1.0,
    gap_ratio: float = 0.0,
) -> List[Polygon]:
    """
    Subdivide a 4-corner perspective row polygon into `count` adaptive trapezoidal
    parking spots using 2D projective Homography (getPerspectiveTransform).

    `outer_quad` has 4 points: [P0, P1, P2, P3] in loop order (clockwise or CCW).
    - direction="along_01": divides along edge P0->P1 and opposite edge P3->P2
    - direction="along_12": divides along edge P1->P2 and opposite edge P0->P3
    - direction="auto": automatically divides along the longer pair of opposite edges
    """
    if len(outer_quad) != 4:
        raise ValueError("outer_quad must contain exactly 4 corner points")
    if count < 1 or count > 50:
        raise ValueError("count must be between 1 and 50")

    pts = np.array(outer_quad, dtype=np.float32)

    if direction == "auto":
        len_01 = float(np.linalg.norm(pts[1] - pts[0]) + np.linalg.norm(pts[2] - pts[3]))
        len_12 = float(np.linalg.norm(pts[2] - pts[1]) + np.linalg.norm(pts[3] - pts[0]))
        split_dir = "along_01" if len_01 >= len_12 else "along_12"
    else:
        split_dir = direction

    # Re-index so u in [0, 1] always runs along the subdivision axis
    if split_dir == "along_12":
        ordered_dst = np.array([pts[1], pts[2], pts[3], pts[0]], dtype=np.float32)
    else:
        ordered_dst = pts

    unit_src = np.array(
        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32
    )
    H = cv2.getPerspectiveTransform(unit_src, ordered_dst)

    gamma = max(0.35, min(2.8, float(foreshortening_gamma)))
    gap = max(0.0, min(0.25, float(gap_ratio)))

    trapezoids: List[Polygon] = []
    step = 1.0 / count
    for i in range(count):
        u0_raw = i * step + (step * gap * 0.5)
        u1_raw = (i + 1) * step - (step * gap * 0.5)
        u0 = float(u0_raw ** gamma)
        u1 = float(u1_raw ** gamma)

        cell_unit = np.array(
            [[[u0, 0.0], [u1, 0.0], [u1, 1.0], [u0, 1.0]]], dtype=np.float32
        )
        projected = cv2.perspectiveTransform(cell_unit, H)[0]
        trapezoids.append([[round(float(p[0]), 2), round(float(p[1]), 2)] for p in projected])

    return trapezoids


def _eval_catmull_rom_chain(ctrl_pts: np.ndarray, samples_per_seg: int = 32) -> np.ndarray:
    """Evaluate a smooth Catmull-Rom spline passing through `ctrl_pts` (N x 2)."""
    n = len(ctrl_pts)
    if n == 1:
        return ctrl_pts.copy()
    if n == 2:
        t = np.linspace(0.0, 1.0, max(2, samples_per_seg), dtype=np.float64)[:, None]
        return (1.0 - t) * ctrl_pts[0] + t * ctrl_pts[1]

    dense: List[np.ndarray] = []
    for i in range(n - 1):
        p0 = ctrl_pts[0] + (ctrl_pts[0] - ctrl_pts[1]) if i == 0 else ctrl_pts[i - 1]
        p1 = ctrl_pts[i]
        p2 = ctrl_pts[i + 1]
        p3 = ctrl_pts[-1] + (ctrl_pts[-1] - ctrl_pts[-2]) if (i + 2) >= n else ctrl_pts[i + 2]

        steps = samples_per_seg if i < n - 2 else samples_per_seg + 1
        ts = np.linspace(0.0, 1.0, steps, endpoint=(i == n - 2), dtype=np.float64)[:, None]
        t2 = ts * ts
        t3 = t2 * ts
        seg = 0.5 * (
            (2.0 * p1)
            + (-p0 + p2) * ts
            + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
            + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
        )
        dense.append(seg)
    return np.vstack(dense)


def _resample_curve_by_arclength(ctrl_pts: np.ndarray, u_vals: np.ndarray) -> np.ndarray:
    dense = _eval_catmull_rom_chain(ctrl_pts, samples_per_seg=40)
    diffs = np.diff(dense, axis=0)
    seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total_len = float(cum[-1])
    if total_len < 1e-6:
        return np.repeat(dense[:1], len(u_vals), axis=0)
    norm_s = cum / total_len
    xs = np.interp(u_vals, norm_s, dense[:, 0])
    ys = np.interp(u_vals, norm_s, dense[:, 1])
    return np.stack([xs, ys], axis=1)


def subdivide_curved_row(
    front_curve: Polygon,
    back_curve: Polygon,
    count: int,
    gap_ratio: float = 0.0,
) -> List[Polygon]:
    """
    Subdivide a curved / arc / fan-shaped roadside parking bay defined by a front curb
    polyline (`front_curve`, >=2 pts) and a rear boundary polyline (`back_curve`, >=2 pts)
    into `count` fan-aligned 4-point stall polygons `[back_L, back_R, front_R, front_L]`.

    Automatically aligns `back_curve` direction with `front_curve` so users can click
    left-to-right on both curves or trace around in a loop without twisting stalls.
    """
    if len(front_curve) < 2 or len(back_curve) < 2:
        raise ValueError("front_curve and back_curve must each contain at least 2 points")
    if count < 1 or count > 50:
        raise ValueError("count must be between 1 and 50")

    f_pts = np.array(front_curve, dtype=np.float64)
    b_pts = np.array(back_curve, dtype=np.float64)

    # Auto-orient back_curve to run in the same direction as front_curve
    direct_cost = np.linalg.norm(f_pts[0] - b_pts[0]) + np.linalg.norm(f_pts[-1] - b_pts[-1])
    flipped_cost = np.linalg.norm(f_pts[0] - b_pts[-1]) + np.linalg.norm(f_pts[-1] - b_pts[0])
    if flipped_cost < direct_cost:
        b_pts = b_pts[::-1].copy()

    gap = max(0.0, min(0.25, float(gap_ratio)))
    step = 1.0 / count
    u0_list = np.array([i * step + step * gap * 0.5 for i in range(count)], dtype=np.float64)
    u1_list = np.array([(i + 1) * step - step * gap * 0.5 for i in range(count)], dtype=np.float64)

    b0 = _resample_curve_by_arclength(b_pts, u0_list)
    b1 = _resample_curve_by_arclength(b_pts, u1_list)
    f1 = _resample_curve_by_arclength(f_pts, u1_list)
    f0 = _resample_curve_by_arclength(f_pts, u0_list)

    quads: List[Polygon] = []
    for i in range(count):
        quads.append(
            [
                [round(float(b0[i, 0]), 1), round(float(b0[i, 1]), 1)],
                [round(float(b1[i, 0]), 1), round(float(b1[i, 1]), 1)],
                [round(float(f1[i, 0]), 1), round(float(f1[i, 1]), 1)],
                [round(float(f0[i, 0]), 1), round(float(f0[i, 1]), 1)],
            ]
        )
    return quads


def extract_spot_patch(
    image_bgr: np.ndarray,
    polygon: Polygon,
    spot_type: str = "standard",
    out_size: Optional[Tuple[int, int]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extract an isolated single-spot or elastic-strip image patch from `image_bgr`
    without leaking neighboring vehicles.

    Returns `(patch_bgr, valid_mask_uint8)`:
    - `standard` (4-point quad): Perspective-rectified into (224, 224) bird's-eye patch.
    - `irregular` (N-gon): Tight bounding-box crop with exterior masked to black,
      aspect-ratio preserved via letterboxing into (224, 224).
    - `elastic_strip` (unmarked parallel zone): Rectified (if 4 pts) or masked into
      elongated (672, 160) strip for continuous gap measurement & VLM counting.
    """
    h_img, w_img = image_bgr.shape[:2]
    pts = np.array(polygon, dtype=np.float32)
    pts[:, 0] = np.clip(pts[:, 0], 0, max(1, w_img - 1))
    pts[:, 1] = np.clip(pts[:, 1], 0, max(1, h_img - 1))

    if spot_type == "elastic_strip":
        target_w, target_h = out_size or (672, 160)
        if len(pts) == 4:
            # Ensure longer axis maps to target_w
            len_01 = np.linalg.norm(pts[1] - pts[0]) + np.linalg.norm(pts[2] - pts[3])
            len_12 = np.linalg.norm(pts[2] - pts[1]) + np.linalg.norm(pts[3] - pts[0])
            src_quad = (
                pts if len_01 >= len_12 else np.array([pts[1], pts[2], pts[3], pts[0]], dtype=np.float32)
            )
            dst_quad = np.array(
                [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
                dtype=np.float32,
            )
            M = cv2.getPerspectiveTransform(src_quad, dst_quad)
            warped = cv2.warpPerspective(image_bgr, M, (target_w, target_h))
            mask = np.full((target_h, target_w), 255, dtype=np.uint8)
            return warped, mask
        return _masked_polygon_crop(image_bgr, pts, (target_w, target_h))

    target_w, target_h = out_size or (224, 224)
    if spot_type == "standard" and len(pts) == 4:
        dst_quad = np.array(
            [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
            dtype=np.float32,
        )
        M = cv2.getPerspectiveTransform(pts, dst_quad)
        warped = cv2.warpPerspective(image_bgr, M, (target_w, target_h))
        mask = np.full((target_h, target_w), 255, dtype=np.uint8)
        return warped, mask

    return _masked_polygon_crop(image_bgr, pts, (target_w, target_h))


def _masked_polygon_crop(
    image_bgr: np.ndarray, pts: np.ndarray, target_size: Tuple[int, int]
) -> Tuple[np.ndarray, np.ndarray]:
    target_w, target_h = target_size
    x, y, w, h = cv2.boundingRect(pts.astype(np.int32))
    w = max(4, w)
    h = max(4, h)
    sub = image_bgr[y : y + h, x : x + w].copy()

    shifted_pts = (pts - np.array([x, y], dtype=np.float32)).astype(np.int32)
    poly_mask = np.zeros((sub.shape[0], sub.shape[1]), dtype=np.uint8)
    cv2.fillPoly(poly_mask, [shifted_pts], 255)
    masked_sub = cv2.bitwise_and(sub, sub, mask=poly_mask)

    # Letterbox resize to preserve vehicle geometry without distortion
    scale = min(target_w / sub.shape[1], target_h / sub.shape[0])
    new_w = max(2, int(round(sub.shape[1] * scale)))
    new_h = max(2, int(round(sub.shape[0] * scale)))
    resized_img = cv2.resize(masked_sub, (new_w, new_h), interpolation=cv2.INTER_AREA)
    resized_mask = cv2.resize(poly_mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    mask_canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    off_x = (target_w - new_w) // 2
    off_y = (target_h - new_h) // 2
    canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized_img
    mask_canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized_mask
    return canvas, mask_canvas


def _order_quad_tl_tr_br_bl(pts: np.ndarray) -> np.ndarray:
    """Normalize a 4-point quad into [TL, TR, BR, BL] image order."""
    idx_y = np.argsort(pts[:, 1])
    top2 = pts[idx_y[:2]]
    bot2 = pts[idx_y[2:]]
    tl, tr = top2[np.argsort(top2[:, 0])]
    bl, br = bot2[np.argsort(bot2[:, 0])]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def derive_vehicle_envelope_polygon(
    ground_poly: Polygon,
    img_w: int = 1280,
    img_h: int = 720,
    height_scale: float = 0.32,
    slant_bias: float = 0.0,
) -> Polygon:
    """
    Derive a perspective-aware 3D Vehicle-Occupied Envelope (`vehicle_polygon`)
    from an empty-stall `ground_poly` (`地面标定层` -> `车辆占用立体层`).

    In elevated oblique window/pole views, a parked car (~1.55m-1.75m tall) projects
    its roof, windshield, and pillars ABOVE the far ground line (`dy < 0`) and shifts
    laterally along the camera viewing ray + stall slant, while tires and front bumper
    remain anchored near the ground front edge.
    """
    if not ground_poly or len(ground_poly) < 3:
        return [list(p) for p in (ground_poly or [])]

    w_lim = max(2.0, float(img_w or 1280) - 1.0)
    h_lim = max(2.0, float(img_h or 720) - 1.0)
    h_scale = max(0.08, min(0.75, float(height_scale)))
    s_bias = max(-0.45, min(0.45, float(slant_bias)))

    raw_pts = np.array(ground_poly, dtype=np.float32)
    cx = float(np.mean(raw_pts[:, 0]))
    radial_norm = (cx - (w_lim * 0.5)) / max(1.0, w_lim * 0.5)

    if len(raw_pts) == 4:
        tl, tr, br, bl = _order_quad_tl_tr_br_bl(raw_pts)
        mid_top = 0.5 * (tl + tr)
        mid_bot = 0.5 * (bl + br)
        v_depth = mid_top - mid_bot  # points from near/front edge toward far/back edge
        stall_h = max(16.0, float(np.linalg.norm(v_depth)))

        top_vec = tr - tl
        top_w = max(10.0, float(np.linalg.norm(top_vec)))
        unit_top = top_vec / top_w

        bot_vec = br - bl
        bot_w = max(10.0, float(np.linalg.norm(bot_vec)))
        unit_bot = bot_vec / bot_w
        avg_w = max(12.0, 0.5 * (top_w + bot_w))
        aspect_hw = stall_h / avg_w

        # Vertical roof lift + oblique lateral shift (stall perspective + lens parallax + manual bias).
        # For low/mid-floor oblique views (aspect_hw <= 1.20, e.g. 2F-4F), lift_y = stall_h * h_scale.
        # For steep high-floor bird's-eye views (aspect_hw > 1.20, e.g. 6F-12F where ground depth >> width),
        # vertical car-roof projection foreshortens with cos(theta) so 3D envelopes hug car roofs instead of
        # shooting high above the curb into trees or building walls.
        lift_y = stall_h * h_scale
        eff_depth = stall_h
        if aspect_hw > 1.20:
            sin_theta = min(0.90, aspect_hw / 2.15)
            cos_theta = float(np.sqrt(max(0.16, 1.0 - sin_theta * sin_theta)))
            steep_cap = avg_w * (h_scale * 2.10) * cos_theta
            lift_y = min(lift_y, max(avg_w * 0.22, steep_cap))
            eff_depth = min(stall_h, avg_w * 1.35)

        shift_x = (
            float(v_depth[0]) * (0.16 * (eff_depth / stall_h))
            + radial_norm * eff_depth * 0.06
            + s_bias * eff_depth
        )
        widen_top = unit_top * (top_w * 0.045)
        widen_bot = unit_bot * (bot_w * 0.02)
        front_overhang = -v_depth * 0.04  # slight front bumper overhang (~20cm)

        veh_tl = tl + np.array([shift_x, -lift_y], dtype=np.float32) - widen_top
        veh_tr = tr + np.array([shift_x, -lift_y], dtype=np.float32) + widen_top
        veh_br = br + front_overhang + np.array([shift_x * 0.12, 0.0], dtype=np.float32) + widen_bot
        veh_bl = bl + front_overhang + np.array([shift_x * 0.12, 0.0], dtype=np.float32) - widen_bot

        out_pts = np.stack([veh_tl, veh_tr, veh_br, veh_bl], axis=0)
    else:
        min_y = float(np.min(raw_pts[:, 1]))
        max_y = float(np.max(raw_pts[:, 1]))
        span_y = max(16.0, max_y - min_y)
        mid_y = min_y + span_y * 0.52
        lift_y = span_y * h_scale * 0.92
        shift_x = (radial_norm * 0.06 + s_bias) * span_y
        out_list = []
        for x, y in raw_pts:
            if y <= mid_y:
                out_list.append([x + shift_x, y - lift_y])
            else:
                out_list.append([x + shift_x * 0.12, y + span_y * 0.03])
        out_pts = np.array(out_list, dtype=np.float32)

    out_pts[:, 0] = np.clip(out_pts[:, 0], 0.0, w_lim)
    out_pts[:, 1] = np.clip(out_pts[:, 1], 0.0, h_lim)
    return [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in out_pts]

