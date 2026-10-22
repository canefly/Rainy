import os
import json
import sqlite3
import time
from typing import List, Optional, Dict, Any
from .models import DocumentState, ScriptItem, EditorNote, GamificationStats

DB_DIR = os.path.expanduser("~/.knot")
DB_PATH = os.path.join(DB_DIR, "knot.db")

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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
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
                conn.commit()

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

    def _deserialize_doc(self, data: Dict[str, Any]) -> DocumentState:
        items = [ScriptItem(**i) for i in data.get("items", [])]
        notes = [EditorNote(**n) for n in data.get("notes", [])]
        custom_notes = [EditorNote(**cn) for cn in data.get("custom_notes", [])]
        
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

db = Database()
