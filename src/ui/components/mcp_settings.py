"""
Settings > MCP: the live bridge, auto-install into AI apps, and a live log of AI calls.

Built by PreferencesWindow, which passes in its card class and page header so the page
looks like every other settings page (no import cycle back into preferences_dialog).
"""
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PushButton, SwitchButton, ToolButton

from ...core import mcp_installer as mi
from ...core.database import db
from ...core.gui_ipc_server import PIPE_NAME
from ...core.mcp_activity import mcp_activity
from ..theme import ThemeManager
from .widgets import safe_copy_to_clipboard

MONO = "'Cascadia Mono', 'Consolas', monospace"

_STATE_TEXT = {
    mi.INSTALLED: "Installed",
    mi.OUTDATED: "Needs update",
    mi.NOT_INSTALLED: "Not installed",
    mi.NOT_FOUND: "Not found",
    mi.ERROR: "Can't read config",
}
_STATE_BUTTON = {
    mi.INSTALLED: "Reinstall",
    mi.OUTDATED: "Update",
    mi.NOT_INSTALLED: "Install",
    mi.NOT_FOUND: "Install anyway",
    mi.ERROR: "Install",
}


def _ago(ts: float) -> str:
    s = max(0, int(time.time() - float(ts or 0)))
    if s < 10:
        return "just now"
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


class _TestSignals(QObject):
    finished = Signal(dict)


class _ConnectionTest(QRunnable):
    def __init__(self, signals: _TestSignals):
        super().__init__()
        self.signals = signals

    def run(self):
        try:
            result = mi.run_connection_test()
        except Exception as e:  # never leave the button stuck
            result = {"ok": False, "steps": [{"name": "Test", "ok": False, "ms": 0, "detail": str(e)}], "total_ms": 0}
        self.signals.finished.emit(result)


class McpSettingsPage(QWidget):
    """The whole MCP settings page."""

    bridge_toggled = Signal(bool)

    COLS = ("Time", "App", "Tool", "What happened", "ms")

    def __init__(self, card_cls: Callable[[], QFrame], header_layout, parent=None):
        super().__init__(parent)
        self._card_cls = card_cls
        self._paused = False
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._test_signals = _TestSignals(self)
        self._test_signals.finished.connect(self._on_test_finished)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(header_layout)
        lay.addWidget(self._build_bridge_card())
        lay.addWidget(self._build_install_card())
        lay.addWidget(self._build_activity_card())
        lay.addStretch(1)

        mcp_activity.entry_added.connect(self._on_entry_added)
        mcp_activity.cleared.connect(self._reload_log)
        mcp_activity.clients_changed.connect(self._refresh_clients)
        mcp_activity.bridge_changed.connect(self._refresh_bridge)

        self._clock = QTimer(self)
        self._clock.setInterval(15000)
        self._clock.timeout.connect(self._refresh_clients)
        self._clock.start()

        self._reload_log()
        self._refresh_clients()
        self._refresh_bridge()
        self.refresh_install_rows()
        self.apply_theme()

    # ------------------------------------------------------------------
    # Card 1: bridge + test + connected apps
    # ------------------------------------------------------------------
    def _build_bridge_card(self) -> QFrame:
        card = self._card_cls()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Live bridge", card)
        title.setObjectName("PrefSectionTitle")
        top.addWidget(title)
        self.bridge_badge = QLabel("", card)
        top.addWidget(self.bridge_badge)
        top.addStretch(1)
        self.bridge_switch = SwitchButton(card)
        self.bridge_switch.setOnText("On")
        self.bridge_switch.setOffText("Off")
        self.bridge_switch.setChecked(db.get_setting("mcp_bridge_enabled", "true") != "false")
        self.bridge_switch.checkedChanged.connect(self._on_bridge_switch)
        top.addWidget(self.bridge_switch)
        v.addLayout(top)

        desc = QLabel(
            "Lets AI apps read your project, lock A-roll cards, highlight the script and export the XML "
            "through this running Rainy. Turn it off and every AI call is refused.", card)
        desc.setObjectName("PrefBodyText")
        desc.setWordWrap(True)
        v.addWidget(desc)

        pipe = QLabel(f"Pipe  \\\\.\\pipe\\{PIPE_NAME}", card)
        pipe.setObjectName("PrefCaption")
        pipe.setStyleSheet(f"font-family: {MONO}; font-size: 11.5px;")
        pipe.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(pipe)

        row = QHBoxLayout()
        self.test_btn = PushButton("Test connection", card)
        self.test_btn.setIcon(FluentIcon.SPEED_HIGH)
        self.test_btn.setFixedHeight(30)
        self.test_btn.setToolTip("Starts the MCP server like an AI app would and asks Rainy for the project status.")
        self.test_btn.clicked.connect(self.run_test)
        row.addWidget(self.test_btn, 0, Qt.AlignTop)
        self.test_lbl = QLabel("", card)
        self.test_lbl.setObjectName("PrefCaption")
        self.test_lbl.setWordWrap(True)
        self.test_lbl.setTextFormat(Qt.RichText)
        row.addWidget(self.test_lbl, 1)
        v.addLayout(row)

        sep = QFrame(card)
        sep.setFixedHeight(1)
        sep.setObjectName("McpSep")
        v.addWidget(sep)

        cl = QLabel("AI apps seen this session", card)
        cl.setStyleSheet("font-weight: 600;")
        cl.setObjectName("PrefBodyText")
        v.addWidget(cl)
        self.clients_lbl = QLabel("", card)
        self.clients_lbl.setObjectName("PrefCaption")
        self.clients_lbl.setWordWrap(True)
        self.clients_lbl.setTextFormat(Qt.RichText)
        v.addWidget(self.clients_lbl)
        return card

    def _on_bridge_switch(self, on: bool):
        db.set_setting("mcp_bridge_enabled", "true" if on else "false")
        self.bridge_toggled.emit(bool(on))
        QTimer.singleShot(50, self._refresh_bridge)

    def _refresh_bridge(self, *_):
        on = mcp_activity.bridge_running
        if on:
            txt, fg, bg, bd = "Listening", "#34d399", "rgba(16,185,129,0.14)", "rgba(16,185,129,0.35)"
        else:
            txt, fg, bg, bd = "Off", "#a1a1aa", "rgba(161,161,170,0.12)", "rgba(161,161,170,0.30)"
        self.bridge_badge.setText(txt)
        self.bridge_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {bd}; border-radius: 5px; "
            "font-size: 11px; font-weight: 700; padding: 2px 9px;")
        self.test_btn.setEnabled(on and self.test_btn.text() == "Test connection")

    def _refresh_clients(self, *_):
        clients = mcp_activity.clients()
        if not clients:
            self.clients_lbl.setText("None yet. Ask your AI something about the project and it shows up here.")
            return
        parts = []
        for c in clients:
            ver = f" {c['version']}" if c.get("version") else ""
            calls = int(c.get("calls") or 0)
            parts.append(
                f"<b>{c['label']}</b>{ver} · {_ago(c.get('last_seen'))}"
                + (f" · {calls} call{'s' if calls != 1 else ''}" if calls else ""))
        self.clients_lbl.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    def run_test(self):
        if not mcp_activity.bridge_running:
            self.test_lbl.setText("Turn the bridge on first.")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        self.test_lbl.setText("Starting the MCP server…")
        QThreadPool.globalInstance().start(_ConnectionTest(self._test_signals))

    def _on_test_finished(self, result: Dict[str, Any]):
        self.test_btn.setText("Test connection")
        self.test_btn.setEnabled(mcp_activity.bridge_running)
        ok_col = "#34d399"
        bad_col = "#f87171"
        lines = []
        for s in result.get("steps") or []:
            mark = f"<span style='color:{ok_col}'>✓</span>" if s.get("ok") else f"<span style='color:{bad_col}'>✕</span>"
            detail = f" — {_esc(s.get('detail', ''))}" if s.get("detail") else ""
            lines.append(f"{mark} {_esc(s.get('name', ''))} · {int(s.get('ms') or 0)} ms{detail}")
        head = (f"<b style='color:{ok_col}'>Working</b>" if result.get("ok")
                else f"<b style='color:{bad_col}'>Not working</b>")
        self.test_lbl.setText(f"{head} ({int(result.get('total_ms') or 0)} ms)<br>" + "<br>".join(lines))
        self.last_test_result = result

    # ------------------------------------------------------------------
    # Card 2: install into AI apps
    # ------------------------------------------------------------------
    def _build_install_card(self) -> QFrame:
        card = self._card_cls()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Install in your AI apps", card)
        title.setObjectName("PrefSectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        refresh = ToolButton(FluentIcon.SYNC, card)
        refresh.setFixedSize(28, 28)
        refresh.setToolTip("Check again")
        refresh.clicked.connect(self.refresh_install_rows)
        top.addWidget(refresh)
        v.addLayout(top)

        sub = QLabel(
            "Adds Rainy to the app's MCP settings and keeps everything else in that file "
            "(a backup is saved next to it). Restart the app afterwards so it loads the new tools.", card)
        sub.setObjectName("PrefCaption")
        sub.setWordWrap(True)
        v.addWidget(sub)

        self._install_rows: Dict[str, Dict[str, Any]] = {}
        for t in mi.targets():
            row = QFrame(card)
            row.setObjectName("McpAppRow")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 7, 10, 7)
            h.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(1)
            name = QLabel(t.name, row)
            name.setStyleSheet("font-weight: 700; font-size: 12.5px;")
            col.addWidget(name)
            path = QLabel("", row)
            path.setObjectName("PrefCaption")
            path.setStyleSheet(f"font-family: {MONO}; font-size: 11px;")
            path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(path)
            h.addLayout(col, 1)
            state = QLabel("", row)
            state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            state.setMinimumWidth(110)
            h.addWidget(state)
            btn = PushButton("Install", row)
            btn.setFixedSize(124, 28)
            btn.clicked.connect(lambda _=False, tid=t.id: self._on_install(tid))
            h.addWidget(btn)
            rm = ToolButton(FluentIcon.DELETE, row)
            rm.setFixedSize(28, 28)
            rm.setToolTip(f"Remove Rainy from {t.name}")
            rm.clicked.connect(lambda _=False, tid=t.id: self._on_remove(tid))
            h.addWidget(rm)
            v.addWidget(row)
            self._install_rows[t.id] = {"frame": row, "name": name, "path": path, "state": state, "btn": btn, "rm": rm}

        foot = QHBoxLayout()
        copy_btn = PushButton("Copy config JSON", card)
        copy_btn.setIcon(FluentIcon.COPY)
        copy_btn.setFixedHeight(30)
        copy_btn.setToolTip("For any other MCP app: paste this into its MCP settings.")
        copy_btn.clicked.connect(self._on_copy_json)
        foot.addWidget(copy_btn)
        other = QLabel("Another app? Paste this into its MCP settings.", card)
        other.setObjectName("PrefCaption")
        foot.addWidget(other)
        foot.addStretch(1)
        v.addLayout(foot)
        return card

    def refresh_install_rows(self):
        dark = ThemeManager.is_dark
        colors = {
            mi.INSTALLED: "#34d399",
            mi.OUTDATED: "#fbbf24",
            mi.NOT_INSTALLED: "#a1a1aa" if dark else "#71717a",
            mi.NOT_FOUND: "#71717a" if dark else "#a1a1aa",
            mi.ERROR: "#f87171",
        }
        for t in mi.targets():
            w = self._install_rows.get(t.id)
            if not w:
                continue
            st = mi.status(t)
            state = st["state"]
            w["path"].setText(_short_path(t.path))
            w["path"].setToolTip(t.path)
            w["state"].setText(_STATE_TEXT.get(state, state))
            w["state"].setToolTip(st.get("detail", ""))
            w["state"].setStyleSheet(f"color: {colors.get(state, '#a1a1aa')}; font-weight: 600; font-size: 12px;")
            w["btn"].setText(_STATE_BUTTON.get(state, "Install"))
            w["rm"].setEnabled(state in (mi.INSTALLED, mi.OUTDATED))
            w["name"].setEnabled(state != mi.NOT_FOUND)
            w["state_value"] = state

    def _on_install(self, target_id: str):
        t = mi.target_by_id(target_id)
        if not t:
            return
        try:
            msg = mi.install(t)
            self._toast(True, "MCP installed", msg)
        except Exception as e:
            self._toast(False, f"Couldn't install in {t.name}", str(e))
        self.refresh_install_rows()

    def _on_remove(self, target_id: str):
        t = mi.target_by_id(target_id)
        if not t:
            return
        try:
            self._toast(True, "MCP removed", mi.remove(t))
        except Exception as e:
            self._toast(False, f"Couldn't remove from {t.name}", str(e))
        self.refresh_install_rows()

    def _on_copy_json(self):
        safe_copy_to_clipboard(mi.config_json_snippet())
        self._toast(True, "Config copied", "Rainy MCP config JSON is on your clipboard.")

    def _toast(self, ok: bool, title: str, content: str):
        win = self.window()
        try:
            (InfoBar.success if ok else InfoBar.error)(
                title, content, parent=win, position=InfoBarPosition.TOP_RIGHT, duration=3500 if ok else 6000)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Card 3: live activity
    # ------------------------------------------------------------------
    def _build_activity_card(self) -> QFrame:
        card = self._card_cls()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Live activity", card)
        title.setObjectName("PrefSectionTitle")
        top.addWidget(title)
        self.count_lbl = QLabel("", card)
        self.count_lbl.setObjectName("PrefCaption")
        top.addWidget(self.count_lbl)
        top.addStretch(1)
        self.pause_btn = ToolButton(FluentIcon.PAUSE, card)
        self.pause_btn.setFixedSize(28, 28)
        self.pause_btn.setToolTip("Pause the live list")
        self.pause_btn.clicked.connect(self.toggle_pause)
        top.addWidget(self.pause_btn)
        copy_btn = ToolButton(FluentIcon.COPY, card)
        copy_btn.setFixedSize(28, 28)
        copy_btn.setToolTip("Copy the log")
        copy_btn.clicked.connect(self._copy_log)
        top.addWidget(copy_btn)
        clear_btn = ToolButton(FluentIcon.DELETE, card)
        clear_btn.setFixedSize(28, 28)
        clear_btn.setToolTip("Clear the log")
        clear_btn.clicked.connect(mcp_activity.clear)
        top.addWidget(clear_btn)
        v.addLayout(top)

        sub = QLabel("Every call an AI app makes, newest first. Click a line to see exactly what it sent and got back. "
                     "Kept while Rainy is open (last 500 calls).", card)
        sub.setObjectName("PrefCaption")
        sub.setWordWrap(True)
        v.addWidget(sub)

        self.log_tree = QTreeWidget(card)
        self.log_tree.setObjectName("McpLogTree")
        self.log_tree.setColumnCount(len(self.COLS))
        self.log_tree.setHeaderLabels(list(self.COLS))
        self.log_tree.setRootIsDecorated(False)
        self.log_tree.setUniformRowHeights(True)
        self.log_tree.setAlternatingRowColors(False)
        self.log_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.log_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_tree.setFixedHeight(260)
        hdr = self.log_tree.header()
        hdr.setStretchLastSection(False)
        for i, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                  QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.ResizeToContents)):
            hdr.setSectionResizeMode(i, mode)
        self.log_tree.currentItemChanged.connect(self._on_row_selected)
        v.addWidget(self.log_tree)

        self.empty_lbl = QLabel("Nothing yet. Calls show up here the moment an AI app talks to Rainy.", card)
        self.empty_lbl.setObjectName("PrefCaption")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.empty_lbl)

        self.detail = QPlainTextEdit(card)
        self.detail.setObjectName("McpLogDetail")
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(200)
        self.detail.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.detail.hide()
        v.addWidget(self.detail)
        return card

    def _make_item(self, e: Dict[str, Any]) -> QTreeWidgetItem:
        t = time.strftime("%H:%M:%S", time.localtime(e.get("ts", time.time())))
        ms = "" if e.get("kind") == "connect" else f"{e.get('duration_ms', 0):.0f}"
        tool = "—" if e.get("kind") == "connect" else e.get("action", "")
        it = QTreeWidgetItem([t, e.get("client", ""), tool, e.get("summary", ""), ms])
        it.setData(0, Qt.UserRole, e.get("id"))
        it.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
        it.setToolTip(3, e.get("summary", ""))
        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(8.5)
        for col in (0, 4):
            it.setFont(col, mono)
        if not e.get("ok", True):
            for col in range(len(self.COLS)):
                it.setForeground(col, QColor("#f87171"))
        elif e.get("kind") == "connect":
            for col in range(len(self.COLS)):
                it.setForeground(col, QColor("#60cdff" if ThemeManager.is_dark else "#0078d4"))
        return it

    def _reload_log(self, *_):
        self.log_tree.clear()
        self._rows = {}
        for e in mcp_activity.entries():
            self._rows[str(e["id"])] = e
            self.log_tree.insertTopLevelItem(0, self._make_item(e))
        self.detail.hide()
        self._refresh_count()

    def _on_entry_added(self, e: Dict[str, Any]):
        self._rows[str(e["id"])] = e
        if len(self._rows) > 600:  # mirror the ring buffer
            keep = {str(x["id"]) for x in mcp_activity.entries()}
            self._rows = {k: v for k, v in self._rows.items() if k in keep}
        if self._paused:
            self._refresh_count()
            return
        self.log_tree.insertTopLevelItem(0, self._make_item(e))
        while self.log_tree.topLevelItemCount() > 500:
            self.log_tree.takeTopLevelItem(self.log_tree.topLevelItemCount() - 1)
        self._refresh_count()

    def _refresh_count(self):
        n = len(mcp_activity.entries())
        calls = sum(1 for e in mcp_activity.entries() if e.get("kind") == "call")
        errors = sum(1 for e in mcp_activity.entries() if not e.get("ok", True))
        bits = [f"{calls} call{'s' if calls != 1 else ''}"]
        if errors:
            bits.append(f"{errors} error{'s' if errors != 1 else ''}")
        if self._paused:
            bits.append("paused")
        self.count_lbl.setText(" · ".join(bits) if n else "")
        self.empty_lbl.setVisible(n == 0)

    def toggle_pause(self):
        self._paused = not self._paused
        self.pause_btn.setIcon(FluentIcon.PLAY if self._paused else FluentIcon.PAUSE)
        self.pause_btn.setToolTip("Resume the live list" if self._paused else "Pause the live list")
        if not self._paused:
            self._reload_log()
        self._refresh_count()

    def _on_row_selected(self, cur: Optional[QTreeWidgetItem], _prev=None):
        if cur is None:
            self.detail.hide()
            return
        e = self._rows.get(str(cur.data(0, Qt.UserRole)))
        if not e:
            self.detail.hide()
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("ts", time.time())))
        if e.get("kind") == "connect":
            text = f"{e.get('summary')}  ·  {stamp}\n\nCLIENT INFO\n{e.get('request', '')}"
        else:
            status = "OK" if e.get("ok") else "ERROR"
            text = (f"{e.get('action')}  ·  {e.get('client')}  ·  {status}  ·  {e.get('duration_ms', 0):.0f} ms  ·  {stamp}\n\n"
                    f"REQUEST\n{e.get('request', '')}\n\n{'REPLY' if e.get('ok') else 'ERROR'}\n{e.get('response', '')}")
        self.detail.setPlainText(text)
        self.detail.show()

    def _copy_log(self):
        lines = []
        for e in mcp_activity.entries():
            stamp = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            lines.append(f"{stamp}  {e.get('client', ''):<16} {e.get('action', ''):<28} "
                         f"{'ok ' if e.get('ok') else 'ERR'} {e.get('duration_ms', 0):>6.0f} ms  {e.get('summary', '')}")
        safe_copy_to_clipboard("\n".join(lines) or "(no MCP activity)")
        self._toast(True, "Log copied", f"{len(lines)} line(s) on your clipboard.")

    # ------------------------------------------------------------------
    def apply_theme(self):
        dark = ThemeManager.is_dark
        text = "#e4e4e7" if dark else "#27272a"
        muted = "#a1a1aa" if dark else "#71717a"
        well = "rgba(0,0,0,0.22)" if dark else "rgba(0,0,0,0.025)"
        border = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.08)"
        sel = "rgba(96,205,255,0.16)" if dark else "rgba(0,120,212,0.10)"
        hover = "rgba(255,255,255,0.04)" if dark else "rgba(0,0,0,0.03)"
        row_bg = "rgba(255,255,255,0.03)" if dark else "rgba(0,0,0,0.02)"
        self.setStyleSheet(f"""
            QFrame#McpSep {{ background: {border}; border: none; }}
            QFrame#McpAppRow {{ background: {row_bg}; border: 1px solid {border}; border-radius: 6px; }}
            QTreeWidget#McpLogTree {{
                background: {well}; color: {text}; border: 1px solid {border}; border-radius: 6px;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 12px; outline: none;
            }}
            QTreeWidget#McpLogTree::item {{ padding: 3px 4px; border: none; }}
            QTreeWidget#McpLogTree::item:hover {{ background: {hover}; }}
            QTreeWidget#McpLogTree::item:selected {{ background: {sel}; color: {text}; }}
            QTreeWidget#McpLogTree QHeaderView::section {{
                background: transparent; color: {muted}; border: none; border-bottom: 1px solid {border};
                padding: 4px 6px; font-size: 11px; font-weight: 600;
            }}
            QPlainTextEdit#McpLogDetail {{
                background: {well}; color: {text}; border: 1px solid {border}; border-radius: 6px;
                font-family: {MONO}; font-size: 11px; padding: 6px;
            }}
        """)
        self.refresh_install_rows()
        self._refresh_bridge()


def _esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short_path(p: str) -> str:
    home = os.path.expanduser("~")
    if os.path.normcase(p).startswith(os.path.normcase(home)):
        return "~" + p[len(home):]
    return p
