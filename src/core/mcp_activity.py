"""
Live record of what AI apps do over the MCP bridge, shown in Settings > MCP.

Every tool call that reaches the GUI pipe is recorded here (on the Qt main thread) with
who made it, a one-line summary, how long it took, and the full request / reply JSON.
Only the last MAX_ENTRIES calls are kept, in memory: nothing is written to disk.
"""
import json
import time
from collections import deque
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

MAX_ENTRIES = 500
MAX_PAYLOAD_CHARS = 20000
HELLO_ACTION = "__hello"

# Substring of the MCP clientInfo.name -> friendly app name. Order matters
# ("cursor-vscode" is Cursor, "claude-code" is not Claude Desktop).
_CLIENT_NAMES = (
    ("knot", "Rainy self-test"),
    ("cursor", "Cursor"),
    ("claude-code", "Claude Code"),
    ("claude code", "Claude Code"),
    ("claude", "Claude Desktop"),
    ("codex", "Codex"),
    ("antigravity", "Antigravity"),
    ("gemini", "Gemini CLI"),
    ("visual studio code", "VS Code"),
    ("vscode", "VS Code"),
)


def client_label(client: Optional[Dict[str, Any]]) -> str:
    """Friendly name for an MCP clientInfo dict ({"name": ..., "version": ...})."""
    raw = str((client or {}).get("name") or "").strip()
    if not raw:
        return "Unknown AI"
    low = raw.lower()
    for needle, label in _CLIENT_NAMES:
        if needle in low:
            return label
    return raw


def _clip_json(obj: Any) -> str:
    try:
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except Exception:
        text = repr(obj)
    if len(text) > MAX_PAYLOAD_CHARS:
        text = text[:MAX_PAYLOAD_CHARS] + f"\n… ({len(text) - MAX_PAYLOAD_CHARS:,} more characters cut)"
    return text


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summarize(action: str, params: Dict[str, Any], ok: bool, result: Any = None, error: str = "") -> str:
    """One-line, human summary of a tool call for the log list."""
    if not ok:
        first = str(error or "failed").strip().splitlines()[0] if str(error or "").strip() else "failed"
        return f"Error: {first}"[:200]
    r = result if isinstance(result, dict) else {}
    p = params if isinstance(params, dict) else {}
    try:
        if action == "get_workspace_status":
            name = r.get("document_name") or "no project"
            bits = [str(name), _plural(int(r.get("vo_segment_count") or 0), "VO line"),
                    f"{int(r.get('vo_locked_aroll_items') or 0)}/{int(r.get('total_aroll_items') or 0)} A-roll locked"]
            if int(r.get("open_review_flags") or 0):
                bits.append(f"{int(r['open_review_flags'])} to review")
            return " · ".join(bits)
        if action == "get_masked_script_and_aroll":
            return " · ".join([
                _plural(len(r.get("masked_paragraphs") or []), "paragraph"),
                _plural(int(r.get("total_aroll_clips") or len(r.get("aroll_manifest") or [])), "A-roll cue"),
            ])
        if action == "get_vo_segments":
            return " · ".join([
                _plural(int(r.get("returned_segments") or len(r.get("segments") or [])), "VO line"),
                _plural(len(r.get("cut_points") or []), "cut point"),
            ])
        if action == "apply_script_vo_highlight":
            return f"Highlighted {_plural(int(r.get('updated_blocks') or len(p.get('diffs') or [])), 'paragraph')}"
        if action == "simulate_aroll_lock":
            rows = r.get("locked_rows") or []
            return f"Locked {_plural(len(rows), 'card')} · {r.get('total_locked', '?')}/{r.get('total_items', '?')} total"
        if action == "trigger_xml_export":
            import os
            bits = [f"Exported {os.path.basename(str(r.get('exported_xml_path') or 'XML'))}"]
            if r.get("vo_clip_count") is not None:
                bits.append(_plural(int(r.get("vo_clip_count") or 0), "VO clip"))
            if r.get("aroll_clip_count") is not None:
                bits.append(f"{int(r.get('aroll_clip_count') or 0)} A-roll")
            snapped = r.get("snapped_cuts") or []
            if snapped:
                bits.append(f"{len(snapped)} cut(s) snapped")
            return " · ".join(bits)
    except Exception:
        pass
    return "OK"


class McpActivityLog(QObject):
    """In-memory ring buffer of MCP calls plus the AI apps seen. Lives on the GUI thread."""

    entry_added = Signal(dict)
    clients_changed = Signal()
    cleared = Signal()
    bridge_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge_running = False
        self._entries: deque = deque(maxlen=MAX_ENTRIES)
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._seq = 0

    def set_bridge_running(self, running: bool):
        running = bool(running)
        if running != self.bridge_running:
            self.bridge_running = running
            self.bridge_changed.emit(running)

    # --- recording ---
    def note_client(self, client: Optional[Dict[str, Any]], connected: bool = False) -> str:
        label = client_label(client)
        now = time.time()
        info = self._clients.get(label)
        is_new = info is None
        if is_new:
            info = {"label": label, "name": str((client or {}).get("name") or ""), "calls": 0, "first_seen": now}
            self._clients[label] = info
        info["version"] = str((client or {}).get("version") or info.get("version") or "")
        info["last_seen"] = now
        if connected:
            self._append({
                "kind": "connect",
                "client": label,
                "action": "connected",
                "summary": f"{label} connected" + (f" (v{info['version']})" if info["version"] else ""),
                "ok": True,
                "duration_ms": 0.0,
                "request": _clip_json(client or {}),
                "response": "",
            })
        self.clients_changed.emit()
        return label

    def record_call(
        self,
        action: str,
        params: Optional[Dict[str, Any]],
        client: Optional[Dict[str, Any]],
        ok: bool,
        result: Any = None,
        error: str = "",
        duration_ms: float = 0.0,
    ) -> Dict[str, Any]:
        label = self.note_client(client)
        self._clients[label]["calls"] = int(self._clients[label].get("calls", 0)) + 1
        return self._append({
            "kind": "call",
            "client": label,
            "action": str(action or "?"),
            "summary": summarize(action, params or {}, ok, result, error),
            "ok": bool(ok),
            "duration_ms": float(duration_ms),
            "request": _clip_json(params or {}),
            "response": _clip_json(result) if ok else str(error or ""),
        })

    def _append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        self._seq += 1
        entry = {"id": self._seq, "ts": time.time(), **entry}
        self._entries.append(entry)
        self.entry_added.emit(entry)
        return entry

    # --- reading ---
    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def clients(self) -> List[Dict[str, Any]]:
        return sorted(self._clients.values(), key=lambda c: -float(c.get("last_seen", 0)))

    def clear(self):
        self._entries.clear()
        self.cleared.emit()


mcp_activity = McpActivityLog()
