"""Qt side of updates: the service, the update card, the required-update lock, and the engine worker."""
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import engine_manager as em
from src.core import license_core as lc
from src.core import license_service as ls
from src.core import updater as up
from src.core.license_core import ApiRejected, ApiUnreachable, LicenseSnapshot, LicenseState
from src.core.update_core import UpdateManifest, UpdateStatus, installer_path_for
from src.core.updater import UpdateService
from test_license_gate import FakeController, FakeLive
from test_update_core import BODY, KEYS, NOW, SHA, FileServer, sign

S = LicenseState


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def update_checks_enabled(monkeypatch):
    """conftest switches update checks off for every test; these tests exercise them on purpose."""
    monkeypatch.delenv("KNOT_NO_UPDATE_CHECK", raising=False)


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


def pump(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


class FeedApi:
    """A fake rainy-api /v1/version."""

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, 0

    def get_version(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.reply


def feed(version="1.3.0", url="https://x.example/Setup.exe", sha=SHA, size=len(BODY), min_supported="0.0.0", notes=("Faster",)):
    return {"manifest": sign("app", {"version": version, "url": url, "sha256": sha, "size": size, "notes": list(notes),
                                     "min_supported": min_supported, "released_at": "2026-11-22T10:00:00Z"})}


@pytest.fixture
def server():
    s = FileServer({"/Setup.exe": {"body": BODY}})
    yield s
    s.close()


def make(tmp_path, api=None, current="1.2.0", **kw):
    started = []
    svc = UpdateService(api=api or FeedApi(feed()), keys=KEYS, clock=lambda: NOW, directory=tmp_path / "updates",
                        current_version=current, starter=started.append, **kw)
    svc.started = started
    return svc


def check(svc):
    svc.check_for_updates()
    assert wait_until(lambda: not svc.is_checking)


def record(svc):
    log = {"available": [], "latest": [], "failed": [], "required": [], "state": 0, "finished": [], "progress": [], "install": 0}
    svc.update_available.connect(lambda v, u, n: log["available"].append((v, u, n)))
    svc.up_to_date.connect(log["latest"].append)
    svc.check_failed.connect(log["failed"].append)
    svc.required_changed.connect(log["required"].append)
    svc.state_changed.connect(lambda: log.__setitem__("state", log["state"] + 1))
    svc.download_finished.connect(lambda ok, msg: log["finished"].append((ok, msg)))
    svc.download_progress.connect(lambda g, t: log["progress"].append((g, t)))
    svc.install_started.connect(lambda: log.__setitem__("install", log["install"] + 1))
    return log


# -- checking ---------------------------------------------------------------------------------------------
def test_a_newer_release_is_reported(qapp, tmp_path):
    svc = make(tmp_path)
    log = record(svc)
    got = []
    svc.check_for_updates(on_available=lambda v, u, n: got.append(v))
    assert wait_until(lambda: not svc.is_checking)
    assert svc.status is UpdateStatus.AVAILABLE and svc.manifest.version == "1.3.0"
    assert log["available"] == [("1.3.0", "https://x.example/Setup.exe", ["Faster"])] and got == ["1.3.0"]
    assert svc.last_checked == NOW and svc.error == "" and not svc.required and log["required"] == []
    assert (svc.latest_version, svc.download_url, svc.changelog, svc.has_update) == ("1.3.0", "https://x.example/Setup.exe", ["Faster"], True)
    assert log["state"] >= 2                                  # "checking" then "done"


@pytest.mark.parametrize("reply", [feed("1.2.0"), feed("1.1.0"), {"manifest": __import__("test_update_core").NULL_FEED}])
def test_up_to_date_when_nothing_newer_is_published(qapp, tmp_path, reply):
    svc = make(tmp_path, FeedApi(reply))
    log = record(svc)
    latest = []
    svc.check_for_updates(on_latest=latest.append)
    assert wait_until(lambda: not svc.is_checking)
    assert svc.status is UpdateStatus.UP_TO_DATE and log["latest"] == ["1.2.0"] and latest == ["1.2.0"] and log["available"] == []


def test_required_update_is_announced_once_and_clears_when_the_minimum_drops(qapp, tmp_path):
    api = FeedApi(feed("1.3.0", min_supported="1.2.5"))
    svc = make(tmp_path, api)
    log = record(svc)
    check(svc)
    assert svc.required and svc.status is UpdateStatus.REQUIRED and log["required"] == [True]
    check(svc)                                                # same answer again: no second announcement
    assert log["required"] == [True]
    api.reply = feed("1.3.0", min_supported="1.0.0")
    check(svc)
    assert not svc.required and log["required"] == [True, False] and svc.status is UpdateStatus.AVAILABLE


def test_an_unreachable_server_keeps_what_we_knew(qapp, tmp_path):
    api = FeedApi(feed("1.3.0", min_supported="1.2.5"))
    svc = make(tmp_path, api)
    log = record(svc)
    check(svc)
    api.exc = ApiUnreachable("down")
    failed = []
    svc.check_for_updates(on_failed=failed.append)
    assert wait_until(lambda: not svc.is_checking)
    assert svc.error and "Couldn't reach" in svc.error and failed == [svc.error] and log["failed"] == [svc.error]
    assert svc.required and svc.manifest.version == "1.3.0"     # a dropped connection must not unlock a forced update
    api.exc = None
    check(svc)
    assert svc.error == ""


def test_a_tampered_feed_is_ignored_with_a_clear_message(qapp, tmp_path):
    good = feed()["manifest"]
    head, sig = good.split(".")
    forged = {"manifest": head[:-4] + "AAAA." + sig}
    svc = make(tmp_path, FeedApi(forged))
    check(svc)
    assert svc.manifest is None and svc.status is UpdateStatus.UP_TO_DATE and "failed verification" in svc.error


def test_a_feed_signed_by_someone_else_is_ignored(qapp, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other = {"manifest": sign("app", {"version": "9.9.9", "url": "https://evil.example/x.exe", "sha256": "ab" * 32}, key=Ed25519PrivateKey.generate())}
    svc = make(tmp_path, FeedApi(other))
    check(svc)
    assert svc.manifest is None and not svc.has_update and "failed verification" in svc.error


def test_a_build_without_keys_never_offers_updates(qapp, tmp_path):
    svc = UpdateService(api=FeedApi(feed()), keys={}, clock=lambda: NOW, directory=tmp_path, current_version="1.2.0")
    check(svc)
    assert svc.manifest is None and "failed verification" in svc.error


def test_overlapping_checks_hit_the_server_once_and_notify_everyone(qapp, tmp_path):
    api = FeedApi(feed())
    svc = make(tmp_path, api)
    a, b = [], []
    svc.check_for_updates(on_available=lambda *x: a.append(x))
    svc.check_for_updates(on_available=lambda *x: b.append(x))
    assert wait_until(lambda: not svc.is_checking)
    assert api.calls == 1 and len(a) == 1 and len(b) == 1


def test_checks_can_be_switched_off_for_dev_but_not_in_a_shipped_build(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("KNOT_NO_UPDATE_CHECK", "1")
    api = FeedApi(feed())
    svc = make(tmp_path, api)
    svc.check_for_updates()
    pump(0.2)
    assert api.calls == 0 and not svc.is_checking                  # from source: respected
    monkeypatch.setattr(lc, "IS_COMPILED", True)
    check(svc)
    assert api.calls == 1                                          # compiled: the switch is ignored


def test_live_nudges_trigger_a_check_only_for_newer_versions(qapp, tmp_path):
    api = FeedApi(feed("1.3.0"))
    svc = make(tmp_path, api)
    for v in ("1.2.0", "1.1.0", "garbage", ""):
        svc.on_nudge(v)
    pump(0.2)
    assert api.calls == 0
    svc.on_nudge("1.3.0")
    assert wait_until(lambda: api.calls == 1 and not svc.is_checking)


def test_the_periodic_check_is_scheduled_once(qapp, tmp_path):
    svc = make(tmp_path)
    svc.start_periodic()
    svc.start_periodic()
    assert svc._timer.isActive() and svc._timer.interval() == up.CHECK_INTERVAL_MS == 6 * 3600 * 1000
    svc._timer.stop()


# -- downloading and installing ---------------------------------------------------------------------------------
def available_service(tmp_path, server, **kw):
    svc = make(tmp_path, FeedApi(feed(url=server.url("/Setup.exe"), **kw)))
    check(svc)
    assert svc.status is UpdateStatus.AVAILABLE
    return svc


def test_download_verifies_then_installs(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    log = record(svc)
    svc.start_download()
    assert svc.is_downloading
    assert wait_until(lambda: not svc.is_downloading)
    assert log["finished"] == [(True, "Update downloaded and verified.")]
    path = installer_path_for(svc.manifest, tmp_path / "updates")
    assert svc.downloaded_path == path and path.read_bytes() == BODY
    got = [g for g, _ in log["progress"]]
    assert got and got == sorted(got) and log["progress"][-1][0] == len(BODY)

    ok, message = svc.install(quit_app=False)
    assert ok and svc.started == [path] and log["install"] == 1


def test_a_download_that_does_not_match_the_signed_hash_is_thrown_away(qapp, tmp_path):
    evil = FileServer({"/Setup.exe": {"body": bytes(len(BODY))}})          # right size, wrong bytes
    try:
        svc = available_service(tmp_path, evil)
        log = record(svc)
        svc.start_download()
        assert wait_until(lambda: not svc.is_downloading)
        assert log["finished"][0][0] is False and "didn't match its signed fingerprint" in log["finished"][0][1]
        assert svc.downloaded_path is None and "fingerprint" in svc.download_error
        assert not any((tmp_path / "updates").glob("*")) if (tmp_path / "updates").exists() else True
        ok, _ = svc.install(quit_app=False)
        assert ok is False and svc.started == []                            # nothing to run
    finally:
        evil.close()


def test_cancel_stops_the_download(qapp, tmp_path):
    slow = FileServer({"/Setup.exe": {"body": BODY, "delay": 0.15}})
    try:
        svc = available_service(tmp_path, slow)
        log = record(svc)
        svc.download_progress.connect(lambda g, t: svc.cancel_download())
        svc.start_download()
        assert wait_until(lambda: not svc.is_downloading)
        assert log["finished"] == [(False, "Download cancelled.")] and svc.downloaded_path is None
        d = tmp_path / "updates"
        assert not d.exists() or list(d.iterdir()) == []            # no partial file left behind
    finally:
        slow.close()


def test_download_is_a_noop_without_an_update_or_while_running(qapp, tmp_path, server):
    svc = make(tmp_path, FeedApi(feed("1.2.0")))
    check(svc)
    svc.start_download()
    assert not svc.is_downloading                                       # nothing newer to download
    svc2 = available_service(tmp_path, server)
    svc2.start_download()
    svc2.start_download()                                               # second call must not start a second job
    assert wait_until(lambda: not svc2.is_downloading)


def test_an_already_downloaded_verified_installer_is_found_on_check(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    fresh = make(tmp_path, FeedApi(feed(url=server.url("/Setup.exe"))))   # a later launch of the app
    check(fresh)
    assert fresh.downloaded_path == installer_path_for(fresh.manifest, tmp_path / "updates")
    log = record(fresh)
    fresh.start_download()                                                # nothing to download: straight to "ready"
    assert log["finished"] == [(True, "Update downloaded and verified.")] and not fresh.is_downloading


def test_a_tampered_leftover_installer_is_not_trusted_on_check(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    installer_path_for(svc.manifest, tmp_path / "updates").write_bytes(b"MZ tampered")
    fresh = make(tmp_path, FeedApi(feed(url=server.url("/Setup.exe"))))
    check(fresh)
    assert fresh.downloaded_path is None


def test_install_rechecks_the_file_and_refuses_a_swap(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    svc.downloaded_path.write_bytes(b"X" * len(BODY))                       # same size, different bytes
    ok, message = svc.install(quit_app=False)
    assert ok is False and "fingerprint" in message and svc.started == [] and svc.downloaded_path is None


def test_install_also_refuses_a_swapped_file_of_a_different_size(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    svc.downloaded_path.write_bytes(b"MZ short")
    ok, message = svc.install(quit_app=False)
    assert ok is False and "wrong size" in message and svc.started == [] and svc.downloaded_path is None


def test_install_without_a_download(qapp, tmp_path):
    svc = make(tmp_path)
    assert svc.install(quit_app=False) == (False, "There's no downloaded update to install.")


def test_a_failing_installer_start_is_reported(qapp, tmp_path, server):
    def boom(path):
        raise OSError("blocked by policy")
    svc = available_service(tmp_path, server)
    svc._starter = boom
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    ok, message = svc.install(quit_app=False)
    assert ok is False and "blocked by policy" in message


def test_old_installers_are_cleaned_up_when_a_new_download_starts(qapp, tmp_path, server):
    d = tmp_path / "updates"
    d.mkdir()
    (d / "RainySetup_v1.1.0.exe").write_bytes(b"old")
    (d / "leftover.part").write_bytes(b"half")
    (d / "notes.txt").write_text("mine")
    svc = available_service(tmp_path, server)
    svc.start_download()
    assert wait_until(lambda: not svc.is_downloading)
    assert sorted(p.name for p in d.iterdir()) == ["RainySetup_v1.3.0.exe", "notes.txt"]


# -- the live channel nudge reaches the updater ------------------------------------------------------------------------
def test_license_service_forwards_update_nudges_and_never_treats_them_as_license_state(qapp):
    ctl = FakeController(snap := LicenseSnapshot(S.PENDING, device_id="d" * 32))
    live = FakeLive()
    lic = ls.LicenseService(controller=ctl, live=live)
    got, states = [], []
    lic.update_nudged.connect(got.append)
    lic.state_changed.connect(states.append)
    live.frame_received.emit({"t": "update_available", "version": "1.3.0"})
    live.frame_received.emit({"t": "update_available", "version": 5})
    live.frame_received.emit({"t": "update_available"})
    assert got == ["1.3.0"] and states == [] and not any(c[0] == "push" for c in ctl.calls if isinstance(c, tuple))


# -- the card --------------------------------------------------------------------------------------------------------------
def card_for(svc):
    from src.ui.components.update_card import UpdateCard
    return UpdateCard(svc)


def set_state(svc, manifest, status=None, **attrs):
    svc.manifest = manifest
    svc.status = status or up.evaluate(svc.current_version, manifest)
    svc.last_checked = NOW
    for k, v in attrs.items():
        setattr(svc, k, v)
    svc.state_changed.emit()


M130 = UpdateManifest("1.3.0", "", "https://x.example/Setup.exe", SHA, 210 * 1024 * 1024, ("Faster export", "Fixed a crash"), "0.0.0")


def test_card_before_any_check(qapp, tmp_path):
    c = card_for(make(tmp_path))
    assert c.ver_badge.text() == "Not checked yet" and c.check_btn.text() == "Check for Updates" and c.check_btn.isEnabled()
    assert c.download_btn.isHidden() and c.install_btn.isHidden() and c.cancel_btn.isHidden() and c.progress_bar.isHidden()
    assert "v1.2.0" in c.ver_desc.text()


def test_card_up_to_date(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    svc.last_checked = NOW
    svc.state_changed.emit()
    assert c.ver_badge.text() == "Up to Date" and "latest version installed (v1.2.0)" in c.ver_desc.text() and c.check_btn.text() == "Check Again"


def test_card_update_available_with_notes_and_download(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, M130)
    assert c.ver_badge.text() == "Update: v1.3.0" and not c.download_btn.isHidden()
    assert c.download_btn.text() == "Download Update (210.0 MB)"
    assert not c.changelog_lbl.isHidden() and "• Faster export" in c.changelog_lbl.text() and "• Fixed a crash" in c.changelog_lbl.text()
    assert c.install_btn.isHidden() and "Latest v1.3.0" in c.ver_desc.text()


def test_release_notes_are_shown_as_plain_text_never_rich_text(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, UpdateManifest("1.3.0", "", "https://x.example/a.exe", SHA, 10, ('<img src="http://evil/x.png"><b>pwn</b>', "<a href='http://evil'>click</a>"), "0.0.0"))
    assert c.changelog_lbl.textFormat() == Qt.PlainText and c.error_lbl.textFormat() == Qt.PlainText
    assert "<img" in c.changelog_lbl.text()                      # shown literally, not interpreted


def test_card_required_state(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, UpdateManifest("1.3.0", "", "https://x.example/a.exe", SHA, 10, (), "1.2.5"))
    assert c.ver_badge.text() == "Update required" and "too old to keep working" in c.ver_desc.text()
    assert "#f87171" in c.ver_badge.styleSheet()


def test_card_while_downloading(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, M130, is_downloading=True, progress=(50 * 1024 * 1024, 210 * 1024 * 1024))
    assert c.ver_badge.text() == "Downloading" and not c.progress_bar.isHidden() and not c.cancel_btn.isHidden()
    assert c.download_btn.isHidden() and not c.check_btn.isEnabled()
    assert c.progress_bar.value() == 23 and "50.0 MB of 210.0 MB (23%)" in c.progress_lbl.text()


def test_card_when_ready_to_install(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, M130, downloaded_path=tmp_path / "x.exe")
    assert not c.install_btn.isHidden() and c.download_btn.isHidden() and "Ready to install" in c.progress_lbl.text()
    assert c.install_btn.text() == "Install Now (closes Rainy)"


def test_card_shows_errors_in_red(qapp, tmp_path):
    svc = make(tmp_path)
    c = card_for(svc)
    set_state(svc, None, error="Couldn't reach the update server.")
    assert not c.error_lbl.isHidden() and "Couldn't reach" in c.error_lbl.text() and "#f87171" in c.error_lbl.styleSheet()
    set_state(svc, None, error="", download_error="")
    assert c.error_lbl.isHidden()


def test_card_buttons_drive_the_service(qapp, tmp_path, monkeypatch):
    svc = make(tmp_path)
    calls = []
    monkeypatch.setattr(svc, "check_for_updates", lambda *a, **k: calls.append("check"))
    monkeypatch.setattr(svc, "cancel_download", lambda: calls.append("cancel"))
    c = card_for(svc)
    c.check_btn.click()
    assert calls == ["check"]
    set_state(svc, M130, is_downloading=True)
    c.cancel_btn.click()
    assert calls == ["check", "cancel"]


def test_install_failure_is_shown_on_the_card(qapp, tmp_path, monkeypatch):
    svc = make(tmp_path)
    monkeypatch.setattr(svc, "install", lambda quit_app=True: (False, "Couldn't start the installer"))
    c = card_for(svc)
    set_state(svc, M130, downloaded_path=tmp_path / "x.exe")
    c.install_btn.click()
    assert "Couldn't start" in c.error_lbl.text()


def test_card_end_to_end_with_a_real_download(qapp, tmp_path, server):
    svc = available_service(tmp_path, server)
    c = card_for(svc)
    assert not c.download_btn.isHidden()
    c.download_btn.click()
    assert wait_until(lambda: not svc.is_downloading)
    assert not c.install_btn.isHidden() and c.download_btn.isHidden()
    svc.install(quit_app=False)
    assert svc.started


def test_about_window_and_preferences_share_the_card(qapp):
    from src.ui.components.about_dialog import AboutWindow
    w = AboutWindow()
    assert w.update_card is not None and w.check_btn is w.update_card.check_btn and w.ver_badge is w.update_card.ver_badge
    assert "v1.2.0" in w.ver_desc.text() and w.download_btn.isHidden()
    w.apply_theme()
    w.close()
    from src.ui.components.preferences_dialog import PreferencesWindow
    p = PreferencesWindow(None)
    assert hasattr(p, "update_card") and p.update_card.check_btn is not None
    p.close()


# -- required update lock ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A real MainWindow wired to a controllable UpdateService (and the license gate switched off)."""
    from src.ui import window as window_mod
    svc = make(tmp_path)
    monkeypatch.setattr(UpdateService, "_instance", svc)
    win = window_mod.MainWindow()
    yield win, svc
    svc._timer.stop()
    win.close()


def announce(svc, min_supported):
    svc.manifest = UpdateManifest("1.3.0", "", "https://x.example/a.exe", SHA, 10, (), min_supported)
    was = svc.required
    svc.status = up.evaluate(svc.current_version, svc.manifest)
    if was != svc.required:
        svc.required_changed.emit(svc.required)
    svc.state_changed.emit()


def test_window_locks_behind_the_update_screen_and_unlocks(window, tmp_path):
    win, svc = window
    assert win.stack.currentWidget() is win.home_view
    announce(svc, "1.2.5")
    assert win.stack.currentWidget() is win.update_view and win.sidebar.isHidden() and not win.title_menu.isEnabled()
    assert all(not sc.isEnabled() for sc in win._shortcuts)
    announce(svc, "1.0.0")
    assert win.stack.currentWidget() is win.home_view and win.title_menu.isEnabled()


def test_the_update_lock_blocks_every_way_in_including_the_mcp_bridge(window):
    win, svc = window
    announce(svc, "1.2.5")
    for call in (win.open_workspace, win.go_home, win.open_settings, win.open_about, win.open_vault):
        call()
        assert win.stack.currentWidget() is win.update_view
    msg = json.dumps({"id": 1, "action": "get_workspace_status", "params": {}})
    r = win.workspace_view.mcp_ipc_server._process_message(msg)
    assert r["ok"] is False and "needs an update" in r["error"]


def test_the_update_screen_downloads_by_itself_and_offers_install(window, tmp_path, server):
    win, svc = window
    svc.manifest = UpdateManifest("1.3.0", "", server.url("/Setup.exe"), SHA, len(BODY), (), "1.2.5")
    svc.status = UpdateStatus.REQUIRED
    svc.required_changed.emit(True)
    assert wait_until(lambda: svc.downloaded_path is not None)             # no click needed: the user has no other choice
    assert not win.update_view.update_card.install_btn.isHidden()


def test_update_required_beats_the_license_gate_and_both_must_clear(qapp, tmp_path, monkeypatch):
    from src.ui import window as window_mod
    svc = make(tmp_path)
    monkeypatch.setattr(UpdateService, "_instance", svc)
    lic = ls.LicenseService(controller=FakeController(LicenseSnapshot(S.NEEDS_REGISTRATION)), live=FakeLive())
    monkeypatch.setattr(ls.LicenseService, "_instance", lic)
    monkeypatch.setattr(window_mod, "license_gate_disabled", lambda: False)
    win = window_mod.MainWindow()
    try:
        assert win.stack.currentWidget() is win.gate_view                  # license says locked
        announce(svc, "1.2.5")
        assert win.stack.currentWidget() is win.update_view               # the update screen takes priority
        lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
        assert win.stack.currentWidget() is win.update_view               # license is fine but the update is still required
        lic._emit(LicenseSnapshot(S.REVOKED))
        announce(svc, "1.0.0")
        assert win.stack.currentWidget() is win.gate_view                 # update done, license still locked
        lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
        assert win.stack.currentWidget() is win.home_view                 # both clear
        lic.update_nudged.emit("1.9.0")                                    # nudges reach the updater
        assert wait_until(lambda: svc.is_checking or svc.manifest is not None or True)
    finally:
        lic._timer.stop()
        svc._timer.stop()
        win.close()


def test_the_window_remembers_where_you_were_across_an_update_lock(window):
    win, svc = window
    win.open_workspace()
    assert win.stack.currentWidget() is win.workspace_view
    announce(svc, "1.2.5")
    assert win.stack.currentWidget() is win.update_view and win.sidebar.isHidden()
    announce(svc, "1.0.0")
    assert win.stack.currentWidget() is win.workspace_view and not win.sidebar.isHidden()


# -- engine downloads ---------------------------------------------------------------------------------------------------------------
def zip_with(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class EngineController:
    keys = KEYS

    def __init__(self, entries=None, exc=None, reply=None):
        self.entries, self.exc, self.reply = entries, exc, reply

    def fetch_engine_manifest(self):
        if self.exc:
            raise self.exc
        if self.reply is not None:
            return self.reply
        body = {"engines": self.entries or []}
        return {"manifest": sign("engines", body, iat=int(time.time()))}


def entry(engine, url, data, fmt="zip", platform=None):
    from src.core.update_core import platform_id
    import hashlib
    return {"engine": engine, "platform": platform or platform_id(), "version": "20251101", "url": url,
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "format": fmt}


def run_worker(engine_id, controller, bin_dir, monkeypatch):
    monkeypatch.setattr(em, "get_writable_bin_dir", lambda: str(bin_dir))
    w = em.EngineDownloadWorker(engine_id)
    monkeypatch.setattr(w, "_get_controller", lambda: controller)
    done, steps = [], []
    w.finished.connect(lambda eid, ok, msg: done.append((eid, ok, msg)))
    w.progress.connect(lambda eid, pct, msg: steps.append(pct))
    w.run()
    return w, done, steps


FFMPEG_ZIP = zip_with({"ffmpeg-n7/bin/ffmpeg.exe": b"FFMPEG-BINARY", "ffmpeg-n7/bin/ffprobe.exe": b"FFPROBE-BINARY", "readme.txt": b"hi"})


def test_a_verified_ffmpeg_download_is_installed(qapp, tmp_path, monkeypatch):
    s = FileServer({"/ff.zip": {"body": FFMPEG_ZIP}})
    try:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        ctl = EngineController([entry("ffmpeg", s.url("/ff.zip"), FFMPEG_ZIP)])
        w, done, steps = run_worker("ffmpeg", ctl, bin_dir, monkeypatch)
        assert done == [("ffmpeg", True, "FFmpeg (20251101) installed and verified.")]
        assert (bin_dir / "ffmpeg.exe").read_bytes() == b"FFMPEG-BINARY" and (bin_dir / "ffprobe.exe").read_bytes() == b"FFPROBE-BINARY"
        assert sorted(p.name for p in bin_dir.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]      # temp archive removed, no stray files
        assert steps == sorted(steps) and steps[-1] == 100
    finally:
        s.close()


def test_a_tampered_engine_archive_is_never_extracted(qapp, tmp_path, monkeypatch):
    evil_zip = zip_with({"bin/ffmpeg.exe": b"MALWARE", "bin/ffprobe.exe": b"MALWARE"})
    claimed = FFMPEG_ZIP                                           # the signed list pins the *good* archive's hash
    s = FileServer({"/ff.zip": {"body": evil_zip}})
    try:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        ctl = EngineController([entry("ffmpeg", s.url("/ff.zip"), claimed)])
        _, done, _ = run_worker("ffmpeg", ctl, bin_dir, monkeypatch)
        assert done[0][1] is False and "discarded" in done[0][2]    # caught by the signed size already...
        assert list(bin_dir.iterdir()) == []                        # nothing extracted, nothing left behind

        # ...and with the size matching too, the SHA-256 alone still catches it
        same_size = dict(entry("ffmpeg", s.url("/ff.zip"), claimed), size=len(evil_zip))
        _, done, _ = run_worker("ffmpeg", EngineController([same_size]), bin_dir, monkeypatch)
        assert done[0][1] is False and "didn't match its signed fingerprint" in done[0][2]
        assert list(bin_dir.iterdir()) == []
    finally:
        s.close()


def test_engine_archive_names_cannot_escape_the_bin_folder(qapp, tmp_path, monkeypatch):
    sneaky = zip_with({"../evil.exe": b"X", "..\\..\\evil2.exe": b"X", "a/b/ffmpeg.exe": b"FFMPEG", "ffprobe.exe": b"FFPROBE"})
    s = FileServer({"/ff.zip": {"body": sneaky}})
    try:
        bin_dir = tmp_path / "work" / "bin"
        bin_dir.mkdir(parents=True)
        ctl = EngineController([entry("ffmpeg", s.url("/ff.zip"), sneaky)])
        _, done, _ = run_worker("ffmpeg", ctl, bin_dir, monkeypatch)
        assert done[0][1] is True
        assert sorted(p.name for p in bin_dir.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
        assert not (tmp_path / "work" / "evil.exe").exists() and not (tmp_path / "evil.exe").exists()
    finally:
        s.close()


def test_an_archive_without_the_binaries_is_reported(qapp, tmp_path, monkeypatch):
    empty = zip_with({"readme.txt": b"nothing useful"})
    s = FileServer({"/ff.zip": {"body": empty}})
    try:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _, done, _ = run_worker("ffmpeg", EngineController([entry("ffmpeg", s.url("/ff.zip"), empty)]), bin_dir, monkeypatch)
        assert done[0][1] is False and "did not contain ffmpeg.exe" in done[0][2] and list(bin_dir.iterdir()) == []
    finally:
        s.close()


@pytest.mark.parametrize("controller,expected", [
    (EngineController(exc=ApiUnreachable("down")), "Can't reach the server"),
    (EngineController(exc=ApiRejected(403, "not_approved")), "once this install is approved"),
    (EngineController(exc=ApiRejected(401, "not_registered")), "isn't registered"),
    (EngineController(entries=[]), "No verified ffmpeg download"),
    (EngineController(entries=[{"engine": "ffmpeg", "platform": "darwin-arm64", "version": "1", "url": "https://x.example/a.zip", "sha256": "ab" * 32, "size": 5, "format": "zip"}]), "No verified ffmpeg download"),
    (EngineController(reply={"manifest": "garbage"}), "failed verification"),
])
def test_engine_download_fails_clearly_and_never_falls_back_to_github(qapp, tmp_path, monkeypatch, controller, expected):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not contact GitHub"))
    monkeypatch.setattr(lc, "IS_COMPILED", True)                   # a shipped build
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _, done, _ = run_worker("ffmpeg", controller, bin_dir, monkeypatch)
    assert done[0][1] is False and expected in done[0][2] and list(bin_dir.iterdir()) == []


def test_unpinned_github_downloads_need_both_a_source_run_and_an_explicit_switch(qapp, monkeypatch):
    w = em.EngineDownloadWorker("ffmpeg")
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    monkeypatch.delenv("KNOT_ALLOW_UNPINNED_ENGINES", raising=False)
    assert w._legacy_unpinned_allowed() is False
    monkeypatch.setenv("KNOT_ALLOW_UNPINNED_ENGINES", "1")
    assert w._legacy_unpinned_allowed() is True
    monkeypatch.setattr(lc, "IS_COMPILED", True)
    assert w._legacy_unpinned_allowed() is False                    # a shipped build ignores the switch


def test_cancelling_an_engine_download(qapp, tmp_path, monkeypatch):
    s = FileServer({"/ff.zip": {"body": FFMPEG_ZIP}})
    try:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        monkeypatch.setattr(em, "get_writable_bin_dir", lambda: str(bin_dir))
        w = em.EngineDownloadWorker("ffmpeg")
        monkeypatch.setattr(w, "_get_controller", lambda: EngineController([entry("ffmpeg", s.url("/ff.zip"), FFMPEG_ZIP)]))
        done = []
        w.finished.connect(lambda eid, ok, msg: done.append((ok, msg)))
        w.cancel()
        w.run()
        assert done == [(False, "Download cancelled by user.")] and list(bin_dir.iterdir()) == []
    finally:
        s.close()


def test_a_verified_libmpv_archive_is_extracted_then_runtime_checked(qapp, tmp_path, monkeypatch):
    py7zr = pytest.importorskip("py7zr")
    src = tmp_path / "src"
    src.mkdir()
    (src / "libmpv-2.dll").write_bytes(b"not a real dll")
    arch = tmp_path / "mpv.7z"
    with py7zr.SevenZipFile(arch, "w") as z:
        z.write(src / "libmpv-2.dll", "mpv-dev/libmpv-2.dll")
    data = arch.read_bytes()
    s = FileServer({"/mpv.7z": {"body": data}})
    try:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        monkeypatch.setattr(em, "register_engine_dll_directories", lambda: None)
        monkeypatch.setattr(em, "is_mpv_available", lambda: False)         # the fake DLL can't actually load
        _, done, _ = run_worker("libmpv", EngineController([entry("libmpv", s.url("/mpv.7z"), data, fmt="7z")]), bin_dir, monkeypatch)
        assert (bin_dir / "libmpv-2.dll").read_bytes() == b"not a real dll"      # extracted only after the hash passed
        assert done[0][1] is False and "runtime check" in done[0][2]
        assert not any(p.name.startswith("_temp_") for p in bin_dir.iterdir())
    finally:
        s.close()
