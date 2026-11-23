"""Settings > MCP: auto-install into AI apps, the live activity log, and the connection test."""
import json
import os
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout

from src.core import mcp_installer as mi
from src.core.mcp_activity import McpActivityLog, client_label, mcp_activity, summarize


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Points every AI app's config at an empty temp home."""
    home = tmp_path / "home"
    appdata = home / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setattr(mi, "_which", lambda name: None)
    return home


def _t(tid):
    return mi.target_by_id(tid)


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------
def test_nothing_installed_reports_not_found(fake_home):
    states = {t.id: mi.status(t)["state"] for t in mi.targets()}
    assert set(states) == {"claude_code", "claude_desktop", "antigravity", "gemini_cli", "codex", "cursor", "vscode"}
    assert set(states.values()) == {mi.NOT_FOUND}


def test_json_install_keeps_other_servers_and_backs_up(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text(json.dumps({"numStartups": 7, "mcpServers": {"premiere-pro": {"command": "node", "args": ["x.js"]}}}))
    t = _t("claude_code")
    assert mi.status(t)["state"] == mi.NOT_INSTALLED

    msg = mi.install(t)
    assert "Restart Claude Code" in msg
    data = json.loads(cfg.read_text())
    assert data["numStartups"] == 7
    assert data["mcpServers"]["premiere-pro"] == {"command": "node", "args": ["x.js"]}
    cmd, args = mi.server_command()
    assert data["mcpServers"][mi.SERVER_KEY] == {"command": cmd, "args": args}
    assert os.path.isfile(str(cfg) + mi.BACKUP_SUFFIX)
    assert mi.status(t)["state"] == mi.INSTALLED

    mi.remove(t)
    data = json.loads(cfg.read_text())
    assert mi.SERVER_KEY not in data["mcpServers"] and "premiere-pro" in data["mcpServers"]
    assert mi.status(t)["state"] == mi.NOT_INSTALLED


def test_moved_install_is_outdated_and_update_reuses_the_key(fake_home):
    cfg = fake_home / ".gemini" / "config" / "mcp_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"knot-tool": {"command": "C:/old/python.exe", "args": ["C:/old/mcp_server.py"], "disabled": True}}}))
    t = _t("antigravity")
    assert t.path == str(cfg)
    st = mi.status(t)
    assert st["state"] == mi.OUTDATED and st["key"] == "knot-tool"

    assert mi.install(t).startswith("Updated")
    entry = json.loads(cfg.read_text())["mcpServers"]["knot-tool"]
    assert entry["disabled"] is False
    assert mi.status(t)["state"] == mi.INSTALLED


def test_disabled_entry_counts_as_needing_update(fake_home):
    cfg = fake_home / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cmd, args = mi.server_command()
    cfg.write_text(json.dumps({"mcpServers": {mi.SERVER_KEY: {"command": cmd, "args": args, "disabled": True}}}))
    assert mi.status(_t("cursor"))["state"] == mi.OUTDATED


def test_vscode_uses_servers_with_stdio_type(fake_home):
    folder = fake_home / "AppData" / "Roaming" / "Code" / "User"
    folder.mkdir(parents=True)
    t = _t("vscode")
    assert mi.status(t)["state"] == mi.NOT_INSTALLED  # folder exists, file doesn't
    mi.install(t)
    data = json.loads((folder / "mcp.json").read_text())
    assert data["servers"][mi.SERVER_KEY]["type"] == "stdio"
    assert mi.status(t)["state"] == mi.INSTALLED


def test_unreadable_config_is_never_overwritten(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text("{ this is not json")
    t = _t("claude_code")
    assert mi.status(t)["state"] == mi.ERROR
    with pytest.raises(RuntimeError):
        mi.install(t)
    assert cfg.read_text() == "{ this is not json"


def test_codex_toml_install_update_remove(fake_home):
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'model = "o4"\n\n'
        "[mcp_servers.other]\ncommand = 'node'\nargs = ['a.js']\n\n"
        "[mcp_servers.knot-tool]\ncommand = 'C:\\old\\python.exe'\nargs = ['C:\\old\\mcp_server.py']\n\n"
        "[mcp_servers.knot-tool.env]\nFOO = 'bar'\n\n"
        "[profiles.fast]\nmodel = 'mini'\n"
    )
    t = _t("codex")
    assert mi.status(t)["state"] == mi.OUTDATED
    mi.install(t)
    tomllib = mi._toml_module()
    data = tomllib.loads(cfg.read_text())
    cmd, args = mi.server_command()
    assert data["mcp_servers"]["knot-tool"] == {"command": cmd, "args": args}
    assert data["mcp_servers"]["other"]["command"] == "node"
    assert data["profiles"]["fast"]["model"] == "mini" and data["model"] == "o4"
    assert mi.status(t)["state"] == mi.INSTALLED

    mi.remove(t)
    data = tomllib.loads(cfg.read_text())
    assert "knot-tool" not in data["mcp_servers"] and "other" in data["mcp_servers"]
    assert data["profiles"]["fast"]["model"] == "mini"


def test_codex_toml_fresh_file(fake_home):
    (fake_home / ".codex").mkdir()
    t = _t("codex")
    assert mi.status(t)["state"] == mi.NOT_INSTALLED
    mi.install(t)
    tomllib = mi._toml_module()
    assert mi.SERVER_KEY in tomllib.loads((fake_home / ".codex" / "config.toml").read_text())["mcp_servers"]


def test_codex_block_reader_without_a_toml_library():
    text = (
        "[mcp_servers.knot-tool]\n"
        r"command = 'C:\py\python.exe'" "\n"
        r"""args = ['C:\x\mcp_server.py', "b\"q"]""" "\n"
        "\n[other]\na = 1\n"
    )
    assert mi._toml_block_values(text, "knot-tool") == {
        "command": r"C:\py\python.exe", "args": [r"C:\x\mcp_server.py", 'b"q']}


def test_config_json_snippet_uses_the_same_key():
    data = json.loads(mi.config_json_snippet())
    assert list(data["mcpServers"]) == [mi.SERVER_KEY]
    assert data["mcpServers"][mi.SERVER_KEY]["args"][0].endswith("mcp_server.py")


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------
def test_client_labels():
    assert client_label({"name": "claude-code"}) == "Claude Code"
    assert client_label({"name": "claude-ai"}) == "Claude Desktop"
    assert client_label({"name": "cursor-vscode"}) == "Cursor"
    assert client_label({"name": "Visual Studio Code"}) == "VS Code"
    assert client_label({"name": "codex-mcp-client"}) == "Codex"
    assert client_label({"name": "antigravity"}) == "Antigravity"
    assert client_label({"name": "gemini-cli-mcp-client"}) == "Gemini CLI"
    assert client_label({}) == "Unknown AI"
    assert client_label({"name": "MyBot"}) == "MyBot"


def test_summaries():
    assert summarize("simulate_aroll_lock", {}, True, {"locked_rows": [1, 2], "total_locked": 5, "total_items": 9}) == "Locked 2 cards · 5/9 total"
    s = summarize("trigger_xml_export", {}, True, {"exported_xml_path": "C:/x/Ep1.xml", "vo_clip_count": 71, "aroll_clip_count": 24, "snapped_cuts": [{}]})
    assert s == "Exported Ep1.xml · 71 VO clips · 24 A-roll · 1 cut(s) snapped"
    assert summarize("get_vo_segments", {}, True, {"returned_segments": 1, "cut_points": [1, 2]}) == "1 VO line · 2 cut points"
    assert summarize("anything", {}, False, error="boom\nmore") == "Error: boom"


def test_ring_buffer_and_clients(qapp):
    log = McpActivityLog()
    seen = []
    log.entry_added.connect(seen.append)
    for i in range(510):
        log.record_call("get_workspace_status", {}, {"name": "claude-code", "version": "2"}, True, {"document_name": "D"})
    assert len(log.entries()) == 500 and len(seen) == 510
    assert log.clients()[0]["label"] == "Claude Code" and log.clients()[0]["calls"] == 510
    big = {"x": "y" * 50000}
    e = log.record_call("get_vo_segments", big, {}, True, big)
    assert len(e["request"]) < 21000 and "more characters cut" in e["request"]
    log.clear()
    assert log.entries() == []


def test_pipe_records_calls_with_the_client_name(qapp):
    from src.core.gui_ipc_server import GuiIpcServer, send_gui_ipc_request

    def handler(action, params):
        if action == "boom":
            raise RuntimeError("nope")
        return {"document_name": "Pipe Doc", "vo_segment_count": 3}

    server = GuiIpcServer(handler_callback=handler)
    pipe = "knot-test-mcp-activity"
    assert server.start(pipe_name=pipe)
    assert mcp_activity.bridge_running is True
    before = len(mcp_activity.entries())
    try:
        hello = send_gui_ipc_request("__hello", {}, pipe_name=pipe, client={"name": "codex-mcp-client", "version": "0.5"})
        assert hello["ok"] and hello["result"]["client"] == "Codex"
        ok = send_gui_ipc_request("get_workspace_status", {}, pipe_name=pipe, client={"name": "codex-mcp-client"})
        assert ok["ok"]
        bad = send_gui_ipc_request("boom", {"a": 1}, pipe_name=pipe, client={"name": "codex-mcp-client"})
        assert not bad["ok"]
    finally:
        server.stop()
    assert mcp_activity.bridge_running is False
    new = mcp_activity.entries()[before:]
    assert [e["kind"] for e in new] == ["connect", "call", "call"]
    assert new[0]["summary"].startswith("Codex connected")
    assert new[1]["client"] == "Codex" and new[1]["summary"].startswith("Pipe Doc · 3 VO lines")
    assert new[2]["ok"] is False and new[2]["summary"] == "Error: nope" and '"a": 1' in new[2]["request"]


def test_connection_test_runs_the_real_server(qapp):
    """Spawns mcp_server.py on a private pipe; the GUI side answers on this thread."""
    from src.core.gui_ipc_server import GuiIpcServer

    server = GuiIpcServer(handler_callback=lambda a, p: {"document_name": "Self Test Doc"})
    pipe = f"knot-test-selftest-{os.getpid()}"
    assert server.start(pipe_name=pipe)
    before = len(mcp_activity.entries())
    out = {}
    th = threading.Thread(target=lambda: out.update(mi.run_connection_test(timeout_s=20, pipe_name=pipe)))
    th.start()
    deadline = time.time() + 30
    while th.is_alive() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    for _ in range(50):  # let the hello from the background thread land
        qapp.processEvents()
        time.sleep(0.01)
    server.stop()
    assert out.get("ok"), out
    assert [s["name"] for s in out["steps"]] == ["Start MCP server", "List tools", "Reach Rainy"]
    assert "instructions loaded" in out["steps"][0]["detail"]
    assert out["steps"][2]["detail"] == "Project: Self Test Doc"
    labels = {e["client"] for e in mcp_activity.entries()[before:]}
    assert labels == {"Rainy self-test"}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def test_settings_page_log_install_and_pause(qapp, fake_home):
    from src.ui.components.mcp_settings import McpSettingsPage

    (fake_home / ".cursor").mkdir()
    page = McpSettingsPage(QFrame, QVBoxLayout())
    mcp_activity.clear()
    assert page.empty_lbl.isVisibleTo(page)

    mcp_activity.record_call("simulate_aroll_lock", {"locks": [{"row_index": 1, "after_segment_id": 12}]},
                             {"name": "claude-code"}, True, {"locked_rows": [1], "total_locked": 1, "total_items": 4}, duration_ms=12)
    assert page.log_tree.topLevelItemCount() == 1
    item = page.log_tree.topLevelItem(0)
    assert item.text(1) == "Claude Code" and item.text(2) == "simulate_aroll_lock" and item.text(3) == "Locked 1 card · 1/4 total"
    page.log_tree.setCurrentItem(item)
    assert page.detail.isVisibleTo(page) and '"after_segment_id": 12' in page.detail.toPlainText()

    page.toggle_pause()
    mcp_activity.record_call("get_vo_segments", {}, {"name": "claude-code"}, True, {"segments": []})
    assert page.log_tree.topLevelItemCount() == 1 and "paused" in page.count_lbl.text()
    page.toggle_pause()
    assert page.log_tree.topLevelItemCount() == 2

    row = page._install_rows["cursor"]
    assert row["state"].text() == "Not installed" and row["btn"].text() == "Install"
    row["btn"].click()
    assert json.loads((fake_home / ".cursor" / "mcp.json").read_text())["mcpServers"][mi.SERVER_KEY]
    assert row["state"].text() == "Installed" and row["rm"].isEnabled()
    row["rm"].click()
    assert row["state"].text() == "Not installed"

    mcp_activity.clear()
    assert page.log_tree.topLevelItemCount() == 0
    page.deleteLater()


# -- the bridge an installed build hands to AI apps ---------------------------------------------------
def test_an_installed_build_points_ai_apps_at_the_bridge_exe_in_the_mcp_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(mi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mi.sys, "executable", str(tmp_path / "Rainy" / "Rainy.exe"))
    cmd, args = mi.server_command()
    assert cmd == str(tmp_path / "Rainy" / "mcp" / "knot-mcp.exe") and args == []
    assert mi.server_entry()["command"] == cmd


def test_the_bridge_stays_standard_library_only_so_it_can_be_a_tiny_separate_exe():
    """scripts/build_nuitka.ps1 compiles mcp_server.py alone with PySide6 and the in-process Qt path left out.
    A new third-party import here would silently break that build, so it is caught here instead."""
    import ast
    import sys
    from pathlib import Path
    src = Path(mi.__file__).with_name("mcp_server.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    outside_stdlib = {m for m in imported if m.split(".")[0] not in sys.stdlib_module_names}
    assert outside_stdlib == {"src.core.gui_ipc_server"}, outside_stdlib       # the one lazy, test-only import the build excludes
    build = (Path(mi.__file__).parents[2] / "scripts" / "build_nuitka.ps1").read_text(encoding="utf-8")
    assert "--nofollow-import-to=src.core.gui_ipc_server" in build and "--nofollow-import-to=PySide6" in build
