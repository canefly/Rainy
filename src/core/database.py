import os
import json
import sqlite3
import time
from typing import List, Optional, Dict, Any
from .models import DocumentState, ScriptItem, EditorNote, GamificationStats
from .logger import get_logger
from .app_paths import app_home

logger = get_logger("Database")

DB_DIR = app_home()
DB_PATH = os.environ.get("KNOT_DB_PATH") or os.path.join(DB_DIR, "knot.db")
# Folder holding the DB plus its backups; follows the override so isolated runs
# never write snapshots into the real app home.
STATE_DIR = os.path.dirname(os.path.abspath(DB_PATH))

class Database:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._settings_cache = {}
            cls._instance._dirty_settings = {}
            cls._instance._init_db()
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        os.makedirs(STATE_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self):
        # 1. Verify integrity on startup and take daily rolling snapshot
        self.verify_integrity()
        self.backup_database()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check user_version for micro-migrations
            cursor.execute("PRAGMA user_version")
            version = cursor.fetchone()[0]

            if version == 0:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """)

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_files (
                    path TEXT PRIMARY KEY,
                    name TEXT,
                    updated_at REAL
                )
                """)

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    file_signature TEXT UNIQUE,
                    name TEXT,
                    file_path TEXT,
                    data_json TEXT,
                    is_deleted INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL
                )
                """)

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS delta_overrides (
                    doc_signature TEXT,
                    item_id TEXT,
                    start_tc TEXT,
                    end_tc TEXT,
                    text_override TEXT,
                    PRIMARY KEY (doc_signature, item_id)
                )
                """)

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS gamification_stats (
                    id INTEGER PRIMARY KEY,
                    lifetime_seconds INTEGER DEFAULT 0,
                    total_aroll_count INTEGER DEFAULT 0,
                    total_duration_logged INTEGER DEFAULT 0,
                    last_used_date TEXT DEFAULT ''
                )
                """)

                cursor.execute("""
                INSERT OR IGNORE INTO gamification_stats (id, lifetime_seconds, total_aroll_count, total_duration_logged, last_used_date)
                VALUES (1, 0, 0, 0, '')
                """)

                cursor.execute("PRAGMA user_version = 1")
                version = 1
                conn.commit()

            if version < 2:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS link_metadata (
                    norm_url TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    description TEXT,
                    author TEXT,
                    provider TEXT,
                    domain TEXT,
                    file_type TEXT,
                    thumbnail_url TEXT,
                    thumbnail_blob BLOB,
                    is_restricted INTEGER DEFAULT 0,
                    updated_at REAL
                )
                """)
                cursor.execute("PRAGMA user_version = 2")
                version = 2
                conn.commit()

            if version < 3:
                cols = {row[1] for row in cursor.execute("PRAGMA table_info(link_metadata)").fetchall()}
                new_cols = [
                    ("file_size_bytes", "INTEGER"),
                    ("file_size_display", "TEXT"),
                    ("resolution", "TEXT"),
                    ("duration_seconds", "REAL"),
                    ("duration_display", "TEXT"),
                ]
                for col_name, col_type in new_cols:
                    if col_name not in cols:
                        cursor.execute(f"ALTER TABLE link_metadata ADD COLUMN {col_name} {col_type}")
                cursor.execute("PRAGMA user_version = 3")
                conn.commit()

            if version < 4:
                # Clean up legacy cached link_metadata rows that lack media stat probes so they re-probe automatically
                cursor.execute("""
                    DELETE FROM link_metadata 
                    WHERE (file_size_display IS NULL OR file_size_display = '') 
                      AND (resolution IS NULL OR resolution = '')
                      AND is_restricted = 0
                """)
                cursor.execute("PRAGMA user_version = 4")
                conn.commit()

            if version < 5:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS local_media_probe (
                    file_path TEXT PRIMARY KEY,
                    file_size INTEGER,
                    mtime REAL,
                    duration_seconds REAL,
                    duration_display TEXT,
                    width INTEGER,
                    height INTEGER,
                    resolution TEXT,
                    frame_rate REAL,
                    audio_channels INTEGER,
                    sample_rate INTEGER,
                    video_codec TEXT,
                    audio_codec TEXT,
                    probed_at REAL
                )
                """)
                cursor.execute("PRAGMA user_version = 5")
                conn.commit()
                logger.info("Migrated SQLite schema to version 5 (added local_media_probe)")

            if version < 6:
                cols = {row[1] for row in cursor.execute("PRAGMA table_info(local_media_probe)").fetchall()}
                new_cols = [
                    ("start_time", "REAL DEFAULT 0.0"),
                    ("start_timecode", "TEXT DEFAULT ''"),
                    ("is_vfr", "INTEGER DEFAULT 0"),
                    ("avg_frame_rate", "REAL DEFAULT 0.0"),
                ]
                for col_name, col_def in new_cols:
                    if col_name not in cols:
                        cursor.execute(f"ALTER TABLE local_media_probe ADD COLUMN {col_name} {col_def}")
                cursor.execute("PRAGMA user_version = 6")
                conn.commit()
                logger.info("Migrated SQLite schema to version 6 (added start_time, start_timecode, is_vfr, avg_frame_rate to local_media_probe)")

            if version < 7:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS render_queue (
                    id TEXT PRIMARY KEY,
                    source_path TEXT,
                    config_json TEXT,
                    status TEXT,
                    progress REAL DEFAULT 0.0,
                    speed REAL DEFAULT 0.0,
                    eta_seconds REAL DEFAULT 0.0,
                    size_before INTEGER DEFAULT 0,
                    size_after INTEGER DEFAULT 0,
                    output_path TEXT DEFAULT '',
                    error_msg TEXT DEFAULT '',
                    summary_msg TEXT DEFAULT '',
                    created_at REAL,
                    updated_at REAL,
                    sort_order INTEGER DEFAULT 0
                )
                """)
                cursor.execute("PRAGMA user_version = 7")
                conn.commit()
                logger.info("Migrated SQLite schema to version 7 (added render_queue)")

            if version < 8:
                cols = {row[1] for row in cursor.execute("PRAGMA table_info(local_media_probe)").fetchall()}
                new_cols = [
                    ("is_interlaced", "INTEGER DEFAULT 0"),
                    ("field_order", "TEXT DEFAULT 'progressive'"),
                ]
                for col_name, col_def in new_cols:
                    if col_name not in cols:
                        cursor.execute(f"ALTER TABLE local_media_probe ADD COLUMN {col_name} {col_def}")
                cursor.execute("PRAGMA user_version = 8")
                conn.commit()
                logger.info("Migrated SQLite schema to version 8 (added is_interlaced, field_order to local_media_probe)")

    def verify_integrity(self) -> bool:
        """Verifies database integrity using PRAGMA quick_check; restores from latest backup if corrupt."""
        try:
            if not os.path.exists(DB_PATH):
                return True
            conn = sqlite3.connect(DB_PATH, timeout=3.0)
            row = conn.execute("PRAGMA quick_check;").fetchone()
            conn.close()
            if row and row[0] == "ok":
                return True
        except Exception:
            pass
        return self._restore_latest_backup()

    def backup_database(self, max_backups: int = 7) -> Optional[str]:
        """Creates an atomic zero-risk hot backup of the database using SQLite's native backup API."""
        if not os.path.exists(DB_PATH):
            return None
        try:
            backups_dir = os.path.join(STATE_DIR, "backups")
            os.makedirs(backups_dir, exist_ok=True)
            today_str = time.strftime("%Y-%m-%d")
            backup_file = os.path.join(backups_dir, f"knot-{today_str}.db")
            latest_bak = os.path.join(STATE_DIR, "knot.db.bak")
            
            with self._get_connection() as conn:
                # 1. Daily snapshot
                b_conn = sqlite3.connect(backup_file)
                conn.backup(b_conn)
                b_conn.close()

                # 2. Latest quick-recovery shadow copy
                bak_conn = sqlite3.connect(latest_bak)
                conn.backup(bak_conn)
                bak_conn.close()

            # Prune old backups past max_backups
            backup_files = sorted([
                os.path.join(backups_dir, f) for f in os.listdir(backups_dir)
                if f.startswith("knot-") and f.endswith(".db")
            ])
            while len(backup_files) > max_backups:
                old = backup_files.pop(0)
                try:
                    os.remove(old)
                except Exception:
                    pass
            return backup_file
        except Exception:
            return None

    def _restore_latest_backup(self) -> bool:
        latest_bak = os.path.join(STATE_DIR, "knot.db.bak")
        if os.path.exists(latest_bak):
            try:
                import shutil
                shutil.copy2(latest_bak, DB_PATH)
                return True
            except Exception:
                pass
        return False

    # Settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._settings_cache:
            return self._settings_cache[key]
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                self._settings_cache[key] = row["value"]
                return row["value"]
            return default

    def set_setting(self, key: str, value: str, flush: bool = True):
        val_str = str(value)
        prev = self._settings_cache.get(key)
        self._settings_cache[key] = val_str
        if not flush:
            self._dirty_settings[key] = val_str
            return
        if prev == val_str and key not in self._dirty_settings:
            return
        self._dirty_settings[key] = val_str
        self.flush_settings()

    def flush_settings(self):
        if not self._dirty_settings:
            return
        pending = list(self._dirty_settings.items())
        self._dirty_settings.clear()
        with self._get_connection() as conn:
            conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", pending)
            conn.commit()

    # Recent Files
    def add_recent_file(self, path: str, name: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO recent_files (path, name, updated_at) VALUES (?, ?, ?)",
                (path, name, time.time())
            )
            conn.commit()

    def get_recent_files(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT path, name, updated_at FROM recent_files ORDER BY updated_at DESC LIMIT ?", 
                (limit,)
            ).fetchall()
            return [{"path": r["path"], "name": r["name"], "updated_at": r["updated_at"]} for r in rows]

    # Delta Overrides (Non-destructive in-memory edits, persisted inside .rainyproj savefiles)
    def save_override(self, doc_signature: str, item_id: str, start_tc: str, end_tc: str, text: Optional[str] = None):
        if not hasattr(self, "_in_memory_overrides"):
            self._in_memory_overrides = {}
        self._in_memory_overrides.setdefault(doc_signature, {})[item_id] = {
            "start_tc": start_tc,
            "end_tc": end_tc,
            "text": text
        }

    def get_overrides(self, doc_signature: str) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "_in_memory_overrides"):
            self._in_memory_overrides = {}
        return dict(self._in_memory_overrides.get(doc_signature, {}))

    # Document State Management (Demoted from SQLite; projects are isolated self-contained .rainyproj files)
    def save_document(self, doc: DocumentState):
        """Deprecated: SQLite no longer stores massive document JSON dumps. State is saved in .rainyproj files."""
        pass

    def get_document_by_signature(self, signature: str) -> Optional[DocumentState]:
        return None

    def get_active_documents(self) -> List[Dict[str, Any]]:
        return []

    def get_deleted_documents(self) -> List[Dict[str, Any]]:
        return []

    def soft_delete_document(self, doc_id: str):
        pass

    def restore_document(self, doc_id: str):
        pass

    def migrate_document_signature(self, old_signature: str, new_signature: str):
        if not hasattr(self, "_in_memory_overrides"):
            self._in_memory_overrides = {}
        if old_signature in self._in_memory_overrides:
            self._in_memory_overrides[new_signature] = self._in_memory_overrides.pop(old_signature)

    def _deserialize_doc(self, data: Dict[str, Any]) -> DocumentState:
        items = [ScriptItem(**i) for i in data.get("items", [])]
        notes = [EditorNote(**n) for n in data.get("notes", [])]
        custom_notes = [EditorNote(**cn) for cn in data.get("custom_notes", [])]
        default_geom = {
            "width_pt": 612.0,
            "height_pt": 792.0,
            "margin_top_pt": 72.0,
            "margin_bottom_pt": 72.0,
            "margin_left_pt": 72.0,
            "margin_right_pt": 72.0,
        }
        
        return DocumentState(
            id=data["id"],
            file_signature=data["file_signature"],
            name=data["name"],
            file_path=data["file_path"],
            raw_html=data.get("raw_html", ""),
            items=items,
            notes=notes,
            custom_notes=custom_notes,
            headings=data.get("headings", []),
            links=data.get("links", []),
            checked_links=data.get("checked_links", {}),
            checked_notes=data.get("checked_notes", {}),
            link_colors=data.get("link_colors", {}),
            page_geometry=data.get("page_geometry", default_geom),
            blocks=data.get("blocks", []),
            vo_file_path=data.get("vo_file_path", ""),
            vo_segments=data.get("vo_segments", []),
            vo_block_diffs=data.get("vo_block_diffs", {}),
            timeline_assembly=data.get("timeline_assembly", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            is_deleted=bool(data.get("is_deleted", False))
        )

    # Gamification Stats
    def get_stats(self) -> GamificationStats:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM gamification_stats WHERE id = 1").fetchone()
            if not row:
                return GamificationStats()
            return GamificationStats(
                lifetime_seconds=row["lifetime_seconds"],
                total_aroll_count=row["total_aroll_count"],
                total_duration_logged=row["total_duration_logged"],
                last_used_date=row["last_used_date"] or ""
            )

    def increment_stats(self, duration_seconds: int = 0, aroll_count: int = 0):
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE gamification_stats
            SET lifetime_seconds = lifetime_seconds + ?,
                total_aroll_count = total_aroll_count + ?,
                total_duration_logged = total_duration_logged + ?,
                last_used_date = ?
            WHERE id = 1
            """, (duration_seconds, aroll_count, duration_seconds, time.strftime("%Y-%m-%d")))
            conn.commit()

    def reset_stats(self):
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE gamification_stats
            SET lifetime_seconds = 0,
                total_aroll_count = 0,
                total_duration_logged = 0
            WHERE id = 1
            """)
            conn.commit()

    # Persistent Link Unfurl Metadata Cache
    def save_link_metadata(self, norm_url: str, meta: Dict[str, Any]):
        if not norm_url or norm_url == "#":
            return
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO link_metadata (
                norm_url, url, title, description, author, provider,
                domain, file_type, thumbnail_url, thumbnail_blob,
                is_restricted, updated_at,
                file_size_bytes, file_size_display, resolution,
                duration_seconds, duration_display
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                norm_url,
                meta.get("url", norm_url),
                meta.get("title", ""),
                meta.get("description", ""),
                meta.get("author", ""),
                meta.get("provider", ""),
                meta.get("domain", ""),
                meta.get("file_type", "LINK"),
                meta.get("thumbnail_url"),
                meta.get("thumbnail_bytes"),
                1 if meta.get("is_restricted") else 0,
                meta.get("updated_at", time.time()),
                meta.get("file_size_bytes"),
                meta.get("file_size_display", ""),
                meta.get("resolution", ""),
                meta.get("duration_seconds"),
                meta.get("duration_display", ""),
            ))
            conn.commit()

    def get_link_metadata(self, norm_url: str) -> Optional[Dict[str, Any]]:
        if not norm_url or norm_url == "#":
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM link_metadata WHERE norm_url = ?",
                (norm_url,)
            ).fetchone()
            if not row:
                return None
            keys = row.keys()
            return {
                "norm_url": row["norm_url"],
                "url": row["url"],
                "title": row["title"] or "",
                "description": row["description"] or "",
                "author": row["author"] or "",
                "provider": row["provider"] or "",
                "domain": row["domain"] or "",
                "file_type": row["file_type"] or "LINK",
                "thumbnail_url": row["thumbnail_url"],
                "thumbnail_bytes": row["thumbnail_blob"],
                "is_restricted": bool(row["is_restricted"]),
                "updated_at": float(row["updated_at"] or 0.0),
                "file_size_bytes": row["file_size_bytes"] if "file_size_bytes" in keys else None,
                "file_size_display": (row["file_size_display"] or "") if "file_size_display" in keys else "",
                "resolution": (row["resolution"] or "") if "resolution" in keys else "",
                "duration_seconds": row["duration_seconds"] if "duration_seconds" in keys else None,
                "duration_display": (row["duration_display"] or "") if "duration_display" in keys else "",
            }

    def delete_link_metadata(self, norm_url: Optional[str] = None, restricted_only: bool = False):
        with self._get_connection() as conn:
            if norm_url:
                conn.execute("DELETE FROM link_metadata WHERE norm_url = ?", (norm_url,))
            elif restricted_only:
                conn.execute("DELETE FROM link_metadata WHERE is_restricted = 1")
            else:
                conn.execute("DELETE FROM link_metadata")
            conn.commit()

    def get_local_media_probe(self, file_path: str, file_size: int, mtime: float) -> Optional[Dict[str, Any]]:
        """Returns cached media probe record if file_size and mtime match exactly."""
        norm_path = os.path.normpath(file_path)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM local_media_probe WHERE file_path = ?",
                (norm_path,)
            ).fetchone()
            if not row:
                return None
            if row["file_size"] != file_size or abs(float(row["mtime"]) - mtime) > 0.01:
                return None
            return dict(row)

    def store_local_media_probe(
        self,
        file_path: str,
        file_size: int,
        mtime: float,
        duration_seconds: float,
        duration_display: str,
        width: int,
        height: int,
        resolution: str,
        frame_rate: float,
        audio_channels: int,
        sample_rate: int,
        video_codec: str,
        audio_codec: str,
        start_time: float = 0.0,
        start_timecode: str = "",
        is_vfr: bool = False,
        avg_frame_rate: float = 0.0,
        is_interlaced: bool = False,
        field_order: str = "progressive"
    ):
        """Persists probed media stream properties into SQLite."""
        norm_path = os.path.normpath(file_path)
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO local_media_probe (
                file_path, file_size, mtime, duration_seconds, duration_display,
                width, height, resolution, frame_rate, audio_channels,
                sample_rate, video_codec, audio_codec, start_time,
                start_timecode, is_vfr, avg_frame_rate, is_interlaced, field_order, probed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                norm_path, file_size, mtime, duration_seconds, duration_display,
                width, height, resolution, frame_rate, audio_channels,
                sample_rate, video_codec, audio_codec, float(start_time or 0.0),
                str(start_timecode or ""), 1 if is_vfr else 0, float(avg_frame_rate or 0.0),
                1 if is_interlaced else 0, str(field_order or "progressive"), time.time()
            ))
            conn.commit()

    def delete_local_media_probe(self, file_path: str) -> bool:
        """Deletes a specific cached media probe record."""
        norm_path = os.path.normpath(file_path)
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM local_media_probe WHERE file_path = ?", (norm_path,))
            conn.commit()
            return cursor.rowcount > 0

    delete_media_probe = delete_local_media_probe

    def get_probed_media_count(self) -> int:
        """Returns total number of cached media probe records."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM local_media_probe").fetchone()
            return int(row[0]) if row else 0

    def clear_probed_media(self) -> int:
        """Clears all cached media probe records and returns count deleted."""
        with self._get_connection() as conn:
            count = self.get_probed_media_count()
            conn.execute("DELETE FROM local_media_probe")
            conn.commit()
            logger.info("Cleared %d cached local media probe records", count)
            return count

    # Render Queue Persistence
    def save_render_job(self, job_dict: Dict[str, Any]):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO render_queue (
                id, source_path, config_json, status, progress, speed, eta_seconds,
                size_before, size_after, output_path, error_msg, summary_msg,
                created_at, updated_at, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_dict["id"],
                job_dict["source_path"],
                job_dict.get("config_json", "{}"),
                job_dict.get("status", "QUEUED"),
                float(job_dict.get("progress", 0.0)),
                float(job_dict.get("speed", 0.0)),
                float(job_dict.get("eta_seconds", 0.0)),
                int(job_dict.get("size_before", 0)),
                int(job_dict.get("size_after", 0)),
                job_dict.get("output_path", ""),
                job_dict.get("error_msg", ""),
                job_dict.get("summary_msg", ""),
                float(job_dict.get("created_at", time.time())),
                float(job_dict.get("updated_at", time.time())),
                int(job_dict.get("sort_order", 0)),
            ))
            conn.commit()

    def get_render_jobs(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM render_queue ORDER BY sort_order ASC, created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_render_job(self, job_id: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM render_queue WHERE id = ?", (job_id,))
            conn.commit()

    def clear_finished_render_jobs(self):
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM render_queue WHERE status IN ('DONE', 'CANCELLED', 'FAILED', 'INTERRUPTED')"
            )
            conn.commit()

db = Database()
