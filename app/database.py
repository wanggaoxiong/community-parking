import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import DB_PATH


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
                    last_seen_at REAL
                );
                """
            )

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
                "INSERT INTO units VALUES (?, ?, ?, ?, ?)", units
            )

            zones = [
                ("Z_SOUTH_MAIN", "1号楼南侧主干道顺停区", 0.0, "south,east", 16, 86, 36, 9, "宽敞主干道，进南门即达，随时掉头"),
                ("Z_BLDG3_EAST", "3号楼东侧窗台俯拍区", 5.0, "south,east", 34, 56, 32, 10, "4楼志愿者窗台俯拍覆盖，连排标准位"),
                ("Z_BLDG5_DEADEND", "5号楼北侧断头路尽头", 42.0, "east,south", 18, 28, 28, 9, "通道尽头狭窄，若仅剩孤位倒车较难"),
                ("Z_BLDG6_STRIP", "6号楼西侧无划线弹性带", 8.0, "east,south", 68, 40, 14, 34, "18米老旧路侧无划线带，支持弹性测算"),
            ]
            conn.executemany(
                "INSERT INTO zones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", zones
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
            conn.executemany("INSERT INTO cameras VALUES (?, ?, ?, ?, ?, ?, ?, ?)", cameras)

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
                INSERT INTO spots VALUES (
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

    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM cameras ORDER BY camera_id").fetchall()
            return [dict(r) for r in rows]

    def get_camera(self, camera_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
            return dict(row) if row else None

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
                    "INSERT INTO cameras VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
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

    def list_spots(self, camera_id: Optional[str] = None, zone_id: Optional[str] = None) -> List[Dict[str, Any]]:
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
                result.append(d)
            return result

    def save_camera_spots(
        self, camera_id: str, zone_id: str, spots_payload: List[Dict[str, Any]], image_width: int = 1280, image_height: int = 720
    ) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET zone_id = ?, image_width = ?, image_height = ? WHERE camera_id = ?",
                (zone_id, image_width, image_height, camera_id),
            )
            conn.execute("DELETE FROM spots WHERE camera_id = ?", (camera_id,))
            for idx, sp in enumerate(spots_payload):
                spot_id = sp.get("spot_id") or f"{camera_id}-S{idx+1:02d}"
                spot_type = sp.get("spot_type", "standard")
                total_meters = float(sp.get("total_meters", 16.0 if spot_type == "elastic_strip" else 5.2))
                capacity = (
                    max(1, int(total_meters // 5.2))
                    if spot_type == "elastic_strip"
                    else int(sp.get("capacity", 1))
                )
                conn.execute(
                    """
                    INSERT INTO spots VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FREE', ?, 0,
                        NULL, NULL, NULL, 1.0, '新标定车位（待抓拍更新）', 'calibration', ?, ?
                    )
                    """,
                    (
                        spot_id,
                        camera_id,
                        zone_id,
                        sp.get("label") or f"车位-{idx+1:02d}",
                        spot_type,
                        1 if sp.get("compact_only") else 0,
                        total_meters,
                        capacity,
                        json.dumps(sp["polygon"]),
                        capacity,
                        now,
                        now,
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

    def reset_seed_data(self) -> None:
        with self._lock, self._connect() as conn:
            for tbl in ("spots", "cameras", "unit_zone_dist", "zones", "units"):
                conn.execute(f"DELETE FROM {tbl}")
        self._seed_if_empty()


db = Database()
