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
