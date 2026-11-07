import os
import json
import sqlite3
import time
from typing import List, Optional, Dict, Any
from .models import DocumentState, ScriptItem, EditorNote, GamificationStats
from .logger import get_logger

logger = get_logger("Database")

DB_DIR = os.path.expanduser("~/.clickables")
DB_PATH = os.path.join(DB_DIR, "clickables.db")

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
        os.makedirs(DB_DIR, exist_ok=True)
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
            backups_dir = os.path.join(DB_DIR, "backups")
            os.makedirs(backups_dir, exist_ok=True)
            today_str = time.strftime("%Y-%m-%d")
            backup_file = os.path.join(backups_dir, f"clickables-{today_str}.db")
            latest_bak = os.path.join(DB_DIR, "clickables.db.bak")
            
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
                if f.startswith("clickables-") and f.endswith(".db")
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
        latest_bak = os.path.join(DB_DIR, "clickables.db.bak")
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

    # Delta Overrides (Non-destructive edits)
    def save_override(self, doc_signature: str, item_id: str, start_tc: str, end_tc: str, text: Optional[str] = None):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO delta_overrides (doc_signature, item_id, start_tc, end_tc, text_override)
            VALUES (?, ?, ?, ?, ?)
            """, (doc_signature, item_id, start_tc, end_tc, text))
            conn.commit()

    def get_overrides(self, doc_signature: str) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT item_id, start_tc, end_tc, text_override FROM delta_overrides WHERE doc_signature = ?",
                (doc_signature,)
            ).fetchall()
            return {
                r["item_id"]: {
                    "start_tc": r["start_tc"],
                    "end_tc": r["end_tc"],
                    "text": r["text_override"]
                } for r in rows
            }

    # Document State Management (Active & DataVault)
    def save_document(self, doc: DocumentState):
        doc.updated_at = time.time()
        
        # Serialize to JSON dictionary
        payload = {
            "id": doc.id,
            "file_signature": doc.file_signature,
            "name": doc.name,
            "file_path": doc.file_path,
            "raw_html": doc.raw_html,
            "items": [item.__dict__ for item in doc.items],
            "notes": [n.__dict__ for n in doc.notes],
            "custom_notes": [cn.__dict__ for cn in doc.custom_notes],
            "headings": doc.headings,
            "links": doc.links,
            "checked_links": doc.checked_links,
            "checked_notes": doc.checked_notes,
            "link_colors": doc.link_colors,
            "page_geometry": doc.page_geometry,
            "blocks": doc.blocks,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "is_deleted": doc.is_deleted
        }
        
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO documents (id, file_signature, name, file_path, data_json, is_deleted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.id, doc.file_signature, doc.name, doc.file_path, 
                json.dumps(payload), 1 if doc.is_deleted else 0,
                doc.created_at, doc.updated_at
            ))
            conn.commit()

    def get_document_by_signature(self, signature: str) -> Optional[DocumentState]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT data_json FROM documents WHERE file_signature = ?", (signature,)).fetchone()
            if not row:
                return None
            return self._deserialize_doc(json.loads(row["data_json"]))

    def get_active_documents(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, file_signature, name, file_path, updated_at FROM documents WHERE is_deleted = 0 ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_deleted_documents(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, file_signature, name, file_path, updated_at FROM documents WHERE is_deleted = 1 ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def soft_delete_document(self, doc_id: str):
        with self._get_connection() as conn:
            conn.execute("UPDATE documents SET is_deleted = 1, updated_at = ? WHERE id = ?", (time.time(), doc_id))
            conn.commit()

    def restore_document(self, doc_id: str):
        with self._get_connection() as conn:
            conn.execute("UPDATE documents SET is_deleted = 0, updated_at = ? WHERE id = ?", (time.time(), doc_id))
            conn.commit()

    def migrate_document_signature(self, old_signature: str, new_signature: str):
        """Migrates document state and overrides in-place when a .docx file is saved and its SHA256 changes."""
        if not old_signature or not new_signature or old_signature == new_signature:
            return
        with self._get_connection() as conn:
            row = conn.execute("SELECT id, data_json FROM documents WHERE file_signature = ?", (old_signature,)).fetchone()
            conn.execute("DELETE FROM documents WHERE file_signature = ? AND file_signature != ?", (new_signature, old_signature))
            if row:
                try:
                    payload = json.loads(row["data_json"])
                    payload["file_signature"] = new_signature
                    new_json = json.dumps(payload)
                except Exception:
                    new_json = row["data_json"]
                conn.execute(
                    "UPDATE documents SET file_signature = ?, data_json = ?, updated_at = ? WHERE id = ?",
                    (new_signature, new_json, time.time(), row["id"])
                )
            conn.execute("DELETE FROM delta_overrides WHERE doc_signature = ? AND doc_signature != ?", (new_signature, old_signature))
            conn.execute(
                "UPDATE delta_overrides SET doc_signature = ? WHERE doc_signature = ?",
                (new_signature, old_signature)
            )
            conn.commit()

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
        avg_frame_rate: float = 0.0
    ):
        """Persists probed media stream properties into SQLite."""
        norm_path = os.path.normpath(file_path)
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO local_media_probe (
                file_path, file_size, mtime, duration_seconds, duration_display,
                width, height, resolution, frame_rate, audio_channels,
                sample_rate, video_codec, audio_codec, start_time,
                start_timecode, is_vfr, avg_frame_rate, probed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                norm_path, file_size, mtime, duration_seconds, duration_display,
                width, height, resolution, frame_rate, audio_channels,
                sample_rate, video_codec, audio_codec, float(start_time or 0.0),
                str(start_timecode or ""), 1 if is_vfr else 0, float(avg_frame_rate or 0.0), time.time()
            ))
            conn.commit()

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

db = Database()
