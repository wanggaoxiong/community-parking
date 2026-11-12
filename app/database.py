import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import DB_PATH


DEFAULT_SITE_MAP_TOPOLOGY: Dict[str, Any] = {
    "gates": [
        {"gate_id": "south", "name": "南门入口", "x": 47.0, "y": 96.5, "w": 14.0, "h": 4.2},
        {"gate_id": "east", "name": "东门", "x": 96.2, "y": 52.0, "w": 5.5, "h": 12.0},
    ],
    "roads": [
        {
            "name": "小区内部主环路",
            "points": [[8.0, 83.0], [88.0, 83.0], [88.0, 22.0], [12.0, 22.0]],
            "width": 6.2,
        }
    ],
    "greens": [
        {"name": "中心景观花园", "x": 42.0, "y": 34.0, "w": 20.0, "h": 11.0}
    ],
}


class Database:
    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_schema()
        self._seed_if_empty()

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
                    ref_free_sig_b64 TEXT
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
                """
            )
            self._ensure_migrations(conn)

    @staticmethod
    def _ensure_migrations(conn: sqlite3.Connection) -> None:
        cam_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cameras)").fetchall()}
        for col_name, col_def in (
            ("template_jpeg_b64", "TEXT"),
            ("template_gray_b64", "TEXT"),
            ("template_meta_json", "TEXT"),
        ):
            if col_name not in cam_cols:
                conn.execute(f"ALTER TABLE cameras ADD COLUMN {col_name} {col_def}")

        spot_cols = {r["name"] for r in conn.execute("PRAGMA table_info(spots)").fetchall()}
        if "ref_free_sig_b64" not in spot_cols:
            conn.execute("ALTER TABLE spots ADD COLUMN ref_free_sig_b64 TEXT")

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
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM units ORDER BY unit_id").fetchall()
            return [dict(r) for r in rows]

    def list_zones(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM zones ORDER BY zone_id").fetchall()
            return [dict(r) for r in rows]

    def get_unit_distances(self, unit_id: str) -> Dict[str, float]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT zone_id, walk_meters FROM unit_zone_dist WHERE unit_id = ?",
                (unit_id,),
            ).fetchall()
            return {r["zone_id"]: float(r["walk_meters"]) for r in rows}

    @staticmethod
    def _format_camera_row(row: sqlite3.Row, include_blobs: bool = False) -> Dict[str, Any]:
        d = dict(row)
        has_tpl = bool(d.get("template_jpeg_b64"))
        meta_raw = d.pop("template_meta_json", None)
        d["has_template"] = has_tpl
        d["template_meta"] = json.loads(meta_raw) if meta_raw else None
        if not include_blobs:
            d.pop("template_jpeg_b64", None)
            d.pop("template_gray_b64", None)
        return d

    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM cameras ORDER BY camera_id").fetchall()
            return [self._format_camera_row(r, include_blobs=False) for r in rows]

    def get_camera(self, camera_id: str, include_blobs: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            return self._format_camera_row(row, include_blobs=include_blobs) if row else None

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
        return self.get_camera(camera_id, include_blobs=False)

    def upsert_camera(
        self,
        camera_id: str,
        name: str,
        zone_id: str,
        mount_type: str = "业主窗台Wi-Fi",
        image_width: int = 1280,
        image_height: int = 720,
    ) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT camera_id FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE cameras
                    SET name = ?, zone_id = ?, mount_type = ?, image_width = ?, image_height = ?
                    WHERE camera_id = ?
                    """,
                    (name, zone_id, mount_type, image_width, image_height, camera_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO cameras (
                        camera_id, name, zone_id, mount_type, image_width, image_height, last_heartbeat, frames_received
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (camera_id, name, zone_id, mount_type, image_width, image_height, time.time()),
                )
        return self.get_camera(camera_id)  # type: ignore

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
            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["polygon"] = json.loads(d.pop("polygon_json"))
                d["compact_only"] = bool(d["compact_only"])
                d["has_ref_signature"] = bool(d.get("ref_free_sig_b64"))
                if not include_signatures:
                    d.pop("ref_free_sig_b64", None)
                result.append(d)
            return result

    def save_camera_spots(
        self, camera_id: str, zone_id: str, spots_payload: List[Dict[str, Any]], image_width: int = 1280, image_height: int = 720
    ) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            existing_sigs = {
                r["label"]: r["ref_free_sig_b64"]
                for r in conn.execute(
                    "SELECT label, ref_free_sig_b64 FROM spots WHERE camera_id = ?", (camera_id,)
                ).fetchall()
                if r["ref_free_sig_b64"]
            }
            conn.execute(
                "UPDATE cameras SET zone_id = ?, image_width = ?, image_height = ? WHERE camera_id = ?",
                (zone_id, image_width, image_height, camera_id),
            )
            conn.execute("DELETE FROM spots WHERE camera_id = ?", (camera_id,))
            for idx, sp in enumerate(spots_payload):
                spot_id = sp.get("spot_id") or f"{camera_id}-S{idx+1:02d}"
                label = sp.get("label") or f"车位-{idx+1:02d}"
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

                conn.execute(
                    """
                    INSERT INTO spots (
                        spot_id, camera_id, zone_id, label, spot_type, compact_only, total_meters, capacity,
                        polygon_json, status, free_units, occupied_units, pending_status, pending_free_units,
                        pending_since, confidence, last_reason, last_engine, last_changed_at, last_seen_at,
                        ref_free_sig_b64
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?
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
                        json.dumps(sp["polygon"]),
                        raw_status,
                        free_u,
                        occ_u,
                        conf,
                        reason,
                        engine,
                        now,
                        now,
                        ref_sig,
                    ),
                )
        return self.list_spots(camera_id=camera_id)

    def update_spot_state(self, spot_id: str, updates: Dict[str, Any]) -> None:
        if not updates:
            return
        cols = ", ".join(f"{k} = ?" for k in updates.keys())
        vals = list(updates.values()) + [spot_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE spots SET {cols} WHERE spot_id = ?", vals)

    def get_site_map(self, include_backdrop: bool = True) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM site_map WHERE map_id = 'default'"
            ).fetchone()
            if not row:
                return {
                    "map_id": "default",
                    "source_type": "seed",
                    "has_backdrop": False,
                    "backdrop_jpeg_b64": "" if include_backdrop else None,
                    "aspect_ratio": 1.45,
                    "topology": json.loads(json.dumps(DEFAULT_SITE_MAP_TOPOLOGY)),
                    "meta": {
                        "scene_summary": "默认零实景矢量平面图（支持上传小区平面图自动生成）",
                        "estimated_span_meters": 185.0,
                    },
                    "updated_at": 0.0,
                }
            d = dict(row)
            has_bg = bool(d.get("backdrop_jpeg_b64"))
            try:
                topology = json.loads(d.get("topology_json") or "{}")
            except Exception:
                topology = {}
            if not topology.get("gates") and not topology.get("roads"):
                topology = json.loads(json.dumps(DEFAULT_SITE_MAP_TOPOLOGY))
            try:
                meta = json.loads(d.get("meta_json") or "{}")
            except Exception:
                meta = {}
            return {
                "map_id": d["map_id"],
                "source_type": d.get("source_type", "custom"),
                "has_backdrop": has_bg,
                "backdrop_jpeg_b64": (d.get("backdrop_jpeg_b64") or "") if include_backdrop else None,
                "aspect_ratio": float(d.get("aspect_ratio") or 1.45),
                "topology": topology,
                "meta": meta,
                "updated_at": float(d.get("updated_at") or 0.0),
            }

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
                    json.dumps(topology, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_site_map(include_backdrop=False)

    def recompute_unit_zone_distances(self, span_meters: Optional[float] = None) -> None:
        with self._lock, self._connect() as conn:
            if span_meters is None:
                smap = self.get_site_map(include_backdrop=False)
                span_meters = float(
                    (smap.get("meta") or {}).get("estimated_span_meters") or 185.0
                )
            span_m = max(60.0, min(600.0, float(span_meters)))
            m_per_unit = span_m / 100.0

            units = [dict(r) for r in conn.execute("SELECT * FROM units").fetchall()]
            zones = [dict(r) for r in conn.execute("SELECT * FROM zones").fetchall()]
            if not units or not zones:
                return

            pairs = []
            for u in units:
                ux = float(u["map_x"])
                uy = float(u["map_y"])
                for z in zones:
                    zx0 = float(z["map_x"])
                    zy0 = float(z["map_y"])
                    zx1 = zx0 + max(2.0, float(z["map_w"]))
                    zy1 = zy0 + max(2.0, float(z["map_h"]))
                    # Distance from building entrance to nearest edge of parking zone box
                    nx = min(max(ux, zx0), zx1)
                    ny = min(max(uy, zy0), zy1)
                    dx = abs(ux - nx)
                    dy = abs(uy - ny)
                    euclid = (dx * dx + dy * dy) ** 0.5
                    manhattan = dx + dy
                    blended_units = 0.65 * euclid + 0.35 * manhattan
                    walk_m = round(max(12.0, 8.0 + blended_units * m_per_unit * 1.15), 1)
                    pairs.append((u["unit_id"], z["zone_id"], walk_m))

            conn.execute("DELETE FROM unit_zone_dist")
            conn.executemany(
                "INSERT INTO unit_zone_dist (unit_id, zone_id, walk_meters) VALUES (?, ?, ?)",
                pairs,
            )

    def apply_sitemap_layout(
        self,
        zones_payload: Optional[List[Dict[str, Any]]] = None,
        units_payload: Optional[List[Dict[str, Any]]] = None,
        topology_patch: Optional[Dict[str, Any]] = None,
        span_meters: Optional[float] = None,
        replace_units: bool = False,
    ) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            if zones_payload:
                existing_zones = {
                    r["zone_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM zones").fetchall()
                }
                for zp in zones_payload:
                    zid = str(zp["zone_id"]).strip()
                    if not zid:
                        continue
                    cur = existing_zones.get(zid)
                    mx = round(max(0.5, min(96.0, float(zp.get("map_x", cur["map_x"] if cur else 20.0)))), 2)
                    my = round(max(0.5, min(96.0, float(zp.get("map_y", cur["map_y"] if cur else 20.0)))), 2)
                    mw = round(max(6.0, min(98.0 - mx, float(zp.get("map_w", cur["map_w"] if cur else 24.0)))), 2)
                    mh = round(max(5.0, min(98.0 - my, float(zp.get("map_h", cur["map_h"] if cur else 10.0)))), 2)
                    angle = round(max(-85.0, min(85.0, float(zp.get("layout_angle", cur["layout_angle"] if cur else 0.0)))), 1)
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

            if units_payload:
                if replace_units:
                    conn.execute("DELETE FROM units")
                existing_units = {
                    r["unit_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM units").fetchall()
                }
                for up in units_payload:
                    uid = str(up["unit_id"]).strip()
                    if not uid:
                        continue
                    cur_u = existing_units.get(uid)
                    ux = round(max(3.0, min(97.0, float(up.get("map_x", cur_u["map_x"] if cur_u else 50.0)))), 2)
                    uy = round(max(3.0, min(97.0, float(up.get("map_y", cur_u["map_y"] if cur_u else 50.0)))), 2)
                    uw = round(max(6.0, min(28.0, float(up.get("map_w", cur_u["map_w"] if cur_u else 12.4)))), 2)
                    uh = round(max(4.5, min(20.0, float(up.get("map_h", cur_u["map_h"] if cur_u else 6.8)))), 2)
                    uname = str(up.get("name") or (cur_u["name"] if cur_u else f"{uid}号楼")).strip()
                    ugate = str(up.get("default_gate") or (cur_u["default_gate"] if cur_u else "south"))
                    if cur_u and not replace_units:
                        conn.execute(
                            """
                            UPDATE units
                            SET name = ?, default_gate = ?, map_x = ?, map_y = ?, map_w = ?, map_h = ?
                            WHERE unit_id = ?
                            """,
                            (uname, ugate, ux, uy, uw, uh, uid),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO units (
                                unit_id, name, default_gate, map_x, map_y, map_w, map_h
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (uid, uname, ugate, ux, uy, uw, uh),
                        )

            if topology_patch:
                cur_map = self.get_site_map(include_backdrop=True)
                merged_topo = dict(cur_map["topology"])
                for k in ("gates", "roads", "greens"):
                    if k in topology_patch and isinstance(topology_patch[k], list):
                        merged_topo[k] = topology_patch[k]
                meta = dict(cur_map.get("meta") or {})
                meta["layout_tuned_at"] = time.time()
                self.save_site_map(
                    source_type=cur_map["source_type"] if cur_map["source_type"] != "seed" else "manual_tuned",
                    backdrop_jpeg_b64=None,
                    aspect_ratio=cur_map["aspect_ratio"],
                    topology=merged_topo,
                    meta=meta,
                )

        self.recompute_unit_zone_distances(span_meters=span_meters)
        return {
            "zones": self.list_zones(),
            "units": self.list_units(),
            "site_map": self.get_site_map(include_backdrop=False),
        }

    def reset_seed_data(self) -> None:
        with self._lock, self._connect() as conn:
            for tbl in ("spots", "cameras", "unit_zone_dist", "zones", "units", "site_map"):
                conn.execute(f"DELETE FROM {tbl}")
        self._seed_if_empty()


db = Database()
