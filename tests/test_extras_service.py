"""Qt side of announcements, bug reports and analytics: the services, with a fake rainy-api."""
import json
import os
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import extras_service as es
from src.core import license_service as ls
from src.core.extras_core import AnalyticsQueue
from src.core.extras_service import AnalyticsService, AnnouncementService, BugReportService
from src.core.license_core import ApiRejected, ApiUnreachable, LicenseSnapshot, LicenseState, MemorySettings
from test_license_gate import FakeController, FakeLive
from test_update_core import KEYS, NOW, sign

S = LicenseState
ID1, ID2 = "a" * 32, "b" * 32


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


def settle(seconds=0.15):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


class Api:
    """A fake controller: records every signed call and answers from a script."""

    keys = KEYS

    def __init__(self):
        self.calls = []          # (method, path, parsed body or None)
        self.replies = {}        # (method, path) -> dict | Exception | callable
        self.listing = None

    def signed_call(self, method, path, body=None):
        self.calls.append((method, path, json.loads(body) if body else None))
        reply = self.replies.get((method, path), {"ok": True})
        if callable(reply):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def fetch_announcements(self):
        return self.signed_call("GET", "/v1/announcements")

    def paths(self):
        return [(m, p) for m, p, _b in self.calls]

    def bodies(self, path):
        return [b for _m, p, b in self.calls if p == path]


def item(id_=ID1, **kw):
    base = {"id": id_, "title": "Maintenance tonight", "body": "Back at 9pm", "severity": "warning", "created_at": NOW - 100, "expires_at": None}
    return {**base, **kw}


def listing(*items, iat=NOW - 50):
    return {"manifest": sign("announcements", {"announcements": list(items)}, iat=iat)}


# =============================================================================================
# announcements
# =============================================================================================
def make_ann(api=None):
    api = api or Api()
    return AnnouncementService(controller=api, clock=lambda: NOW), api


def test_refresh_reports_verified_notices(qapp):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1), item(ID2, created_at=NOW - 5))
    got = []
    svc.changed.connect(got.append)
    svc.refresh()
    assert wait_until(lambda: got)
    assert [a.id for a in got[-1]] == [ID2, ID1] and [a.id for a in svc.items] == [ID2, ID1]


def test_dismissing_removes_it_at_once_and_tells_the_server(qapp):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1), item(ID2))
    svc.refresh()
    assert wait_until(lambda: len(svc.items) == 2)
    got = []
    svc.changed.connect(got.append)
    svc.mark_read(ID1)
    assert [a.id for a in svc.items] == [ID2] and [a.id for a in got[-1]] == [ID2]       # no waiting for the network
    assert wait_until(lambda: ("POST", f"/v1/announcements/{ID1}/read") in api.paths())


def test_a_failed_read_receipt_is_harmless(qapp):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1))
    api.replies[("POST", f"/v1/announcements/{ID1}/read")] = ApiUnreachable("down")
    svc.refresh()
    assert wait_until(lambda: svc.items)
    svc.mark_read(ID1)
    assert wait_until(lambda: ("POST", f"/v1/announcements/{ID1}/read") in api.paths())
    settle()
    assert svc.items == []


@pytest.mark.parametrize("error", [
    ApiUnreachable("down"), ApiRejected(403, "not_approved"), ApiRejected(401, "auth_failed"), ApiRejected(500, "boom"),
    RuntimeError("weird"),
])
def test_errors_never_disturb_the_current_list(qapp, error):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1))
    svc.refresh()
    assert wait_until(lambda: svc.items)
    api.replies[("GET", "/v1/announcements")] = error
    got = []
    svc.changed.connect(got.append)
    svc.refresh()
    assert wait_until(lambda: not svc._busy)
    settle()
    assert [a.id for a in svc.items] == [ID1] and got == []


def test_a_forged_or_stale_list_is_ignored_and_keeps_what_was_shown(qapp):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1))
    svc.refresh()
    assert wait_until(lambda: svc.items)
    for bad in ({"manifest": sign("announcements", {"announcements": [item(ID2, title="Download evil.exe")]}, key=Ed25519PrivateKey.generate())},
                listing(item(ID2), iat=NOW - 5 * 86400),
                {"manifest": sign("app", {"announcements": [item(ID2)]})},
                listing(item(ID2, severity="bogus"))):
        api.replies[("GET", "/v1/announcements")] = bad
        svc.refresh()
        assert wait_until(lambda: not svc._busy)
        settle(0.05)
        assert [a.id for a in svc.items] == [ID1]


def test_a_refresh_requested_while_busy_runs_once_more(qapp):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = lambda: (time.sleep(0.15), listing(item(ID1)))[1]
    svc.refresh()
    svc.refresh()
    svc.refresh()
    assert wait_until(lambda: len([c for c in api.calls if c[0] == "GET"]) == 2 and not svc._busy)
    assert len([c for c in api.calls if c[0] == "GET"]) == 2         # three requests, two fetches


def test_an_empty_list_clears_old_notices(qapp):
    svc, api = make_ann()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1))
    svc.refresh()
    assert wait_until(lambda: svc.items)
    api.replies[("GET", "/v1/announcements")] = listing()
    svc.refresh()
    assert wait_until(lambda: not svc.items)


def test_the_live_nudge_is_forwarded_and_is_not_license_state(qapp):
    ctl = FakeController(LicenseSnapshot(S.PENDING, device_id="d" * 32))
    live = FakeLive()
    lic = ls.LicenseService(controller=ctl, live=live)
    got, states = [], []
    lic.announcements_nudged.connect(lambda: got.append(1))
    lic.state_changed.connect(states.append)
    live.frame_received.emit({"t": "announcements"})
    live.frame_received.emit({"t": "announcements", "title": "ignored extra"})
    assert got == [1, 1] and states == [] and not any(isinstance(c, tuple) and c[0] == "push" for c in ctl.calls)


# =============================================================================================
# bug reports
# =============================================================================================
def make_bug(api=None):
    api = api or Api()
    return BugReportService(controller=api, app_version="1.4.0"), api


def test_a_report_is_sent_with_a_scrubbed_log(qapp, monkeypatch):
    monkeypatch.setattr(es, "read_recent_log_text", lambda *a, **k: "start\nopened C:\\Users\\Mizuusky\\Videos\\a.mp4\nuser mizuusky@example.com\nboom")
    monkeypatch.setattr(es, "current_user_context", lambda: ("C:\\Users\\Mizuusky", "Mizuusky"))
    svc, api = make_bug()
    api.replies[("POST", "/v1/bugs")] = {"id": "0123456789abcdef" * 2}
    done = []
    svc.finished.connect(lambda ok, msg, rid: done.append((ok, msg, rid)))
    svc.send("Export crashes", "It closes", include_log=True)
    assert wait_until(lambda: done)
    assert done[0][0] is True and done[0][2] == "01234567"
    (body,) = api.bodies("/v1/bugs")
    assert body["title"] == "Export crashes" and body["description"] == "It closes" and body["app_version"] == "1.4.0"
    assert "Mizuusky" not in body["log"] and "mizuusky" not in body["log"].lower() and "<email>" in body["log"]
    assert body["log"].endswith("boom") and "~\\Videos\\a.mp4" in body["log"]


def test_leaving_the_log_out_sends_no_log_at_all(qapp, monkeypatch):
    monkeypatch.setattr(es, "read_recent_log_text", lambda *a, **k: "SECRET LOG")
    svc, api = make_bug()
    done = []
    svc.finished.connect(lambda ok, msg, rid: done.append(ok))
    svc.send("t", "d", include_log=False)
    assert wait_until(lambda: done)
    assert api.bodies("/v1/bugs")[0]["log"] == ""


@pytest.mark.parametrize("error,needle", [
    (ApiRejected(403, "not_approved"), "approved"), (ApiRejected(429, "rate_limited"), "tomorrow"),
    (ApiRejected(413, "body_too_large"), "too large"), (ApiRejected(422, "invalid_request"), "title"),
    (ApiRejected(401, "clock_skew"), "clock"), (ApiRejected(500, "weird"), "didn't accept"),
    (ApiUnreachable("down"), "reach the server"), (RuntimeError("weird"), "went wrong"),
])
def test_failures_are_explained_plainly(qapp, monkeypatch, error, needle):
    monkeypatch.setattr(es, "read_recent_log_text", lambda *a, **k: "")
    svc, api = make_bug()
    api.replies[("POST", "/v1/bugs")] = error
    done = []
    svc.finished.connect(lambda ok, msg, rid: done.append((ok, msg, rid)))
    svc.send("t", "d", include_log=False)
    assert wait_until(lambda: done)
    ok, msg, rid = done[0]
    assert ok is False and needle in msg and rid == ""
    assert "Traceback" not in msg and "Error(" not in msg


def test_only_one_report_at_a_time_and_it_can_be_sent_again_afterwards(qapp, monkeypatch):
    monkeypatch.setattr(es, "read_recent_log_text", lambda *a, **k: "")
    svc, api = make_bug()
    api.replies[("POST", "/v1/bugs")] = lambda: (time.sleep(0.1), {"id": "f" * 32})[1]
    done = []
    svc.finished.connect(lambda ok, msg, rid: done.append(ok))
    svc.send("a", "", False)
    svc.send("b", "", False)           # ignored: one is in flight
    assert wait_until(lambda: done)
    settle(0.2)
    assert len(api.bodies("/v1/bugs")) == 1 and done == [True] and svc.sending is False
    svc.send("c", "", False)
    assert wait_until(lambda: len(done) == 2)


def test_a_missing_log_file_is_fine(qapp, monkeypatch):
    monkeypatch.setattr(es, "get_log_file_path", lambda: "Z:\\definitely\\not\\here.log")
    assert es.read_recent_log_text() == ""


def test_the_log_tail_is_read_from_the_end_of_the_file(qapp, monkeypatch, tmp_path):
    p = tmp_path / "knot.log"
    p.write_text("old " * 1000 + "NEWEST", encoding="utf-8")
    monkeypatch.setattr(es, "get_log_file_path", lambda: str(p))
    assert es.read_recent_log_text(max_bytes=100).endswith("NEWEST") and len(es.read_recent_log_text(max_bytes=100)) <= 100


# =============================================================================================
# analytics
# =============================================================================================
def make_analytics(api=None, *, can_send=True, store=None):
    api = api or Api()
    svc = AnalyticsService(controller=api, store=store or MemorySettings(), can_send=lambda: can_send)
    return svc, api


def test_it_is_off_by_default_and_records_nothing(qapp):
    svc, api = make_analytics()
    assert svc.enabled is False and svc.asked is False
    svc.track("app_start", os="win")
    svc.flush()
    settle(0.05)
    assert len(svc.queue) == 0 and api.calls == []


def test_after_opting_in_events_are_sent_in_the_wire_format(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    assert svc.enabled and svc.asked
    svc.track("export.xml", clips=3)
    svc.track("app_start")
    svc.flush()
    assert wait_until(lambda: api.bodies("/v1/analytics"))
    (body,) = api.bodies("/v1/analytics")
    assert [e["name"] for e in body["events"]] == ["export.xml", "app_start"]
    assert body["events"][0]["props"] == {"clips": 3} and all(isinstance(e["age"], int) for e in body["events"])
    assert wait_until(lambda: not svc._flushing) and len(svc.queue) == 0


def test_invalid_events_never_enter_the_queue(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    svc.track("Bad Name")
    svc.track("ok", path="C:\\Users\\me\\clip.mp4")
    svc.track("ok", title="Secret Project")
    assert len(svc.queue) == 0


def test_nothing_is_sent_while_locked_or_unapproved(qapp):
    svc, api = make_analytics(can_send=False)
    svc.set_enabled(True)
    svc.track("app_start")
    svc.flush()
    settle(0.05)
    assert api.calls == [] and len(svc.queue) == 1          # kept for later, not lost


def test_a_network_failure_keeps_the_events_for_the_next_try(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    api.replies[("POST", "/v1/analytics")] = ApiUnreachable("down")
    svc.track("a")
    svc.track("b")
    svc.flush()
    assert wait_until(lambda: api.calls and not svc._flushing)
    assert [ev["name"] for _t, ev in svc.queue.take()] == ["a", "b"]


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_refused_batch_is_dropped_not_retried_forever(qapp, status):
    svc, api = make_analytics()
    svc.set_enabled(True)
    api.replies[("POST", "/v1/analytics")] = ApiRejected(status, "invalid_request")
    svc.track("a")
    svc.flush()
    assert wait_until(lambda: api.calls and not svc._flushing)
    assert len(svc.queue) == 0


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (403, "not_approved"), (401, "clock_skew")])
def test_temporary_refusals_keep_the_events(qapp, status, code):
    svc, api = make_analytics()
    svc.set_enabled(True)
    api.replies[("POST", "/v1/analytics")] = ApiRejected(status, code)
    svc.track("a")
    svc.flush()
    assert wait_until(lambda: api.calls and not svc._flushing)
    assert len(svc.queue) == 1


def test_when_the_owner_switches_analytics_off_the_app_stops_for_the_session(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    api.replies[("POST", "/v1/analytics")] = {"accepted": 0, "dropped": 1, "disabled": True}
    svc.track("a")
    svc.flush()
    assert wait_until(lambda: api.calls and not svc._flushing)
    assert len(svc.queue) == 0
    svc.track("b")
    svc.flush()
    settle(0.05)
    assert len(api.calls) == 1                               # nothing more is sent
    svc.set_enabled(False)
    svc.set_enabled(True)                                    # a fresh choice lets it try again
    api.replies[("POST", "/v1/analytics")] = {"accepted": 1, "dropped": 0}
    svc.track("c")
    svc.flush()
    assert wait_until(lambda: len(api.bodies("/v1/analytics")) == 2)


def test_many_events_send_without_waiting_for_the_timer(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    for i in range(es.ANALYTICS_FLUSH_AT):
        svc.track("tick")
    assert wait_until(lambda: api.bodies("/v1/analytics"))
    assert len(api.bodies("/v1/analytics")[0]["events"]) == es.ANALYTICS_FLUSH_AT


def test_a_big_backlog_goes_out_in_batches_of_fifty(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    for _ in range(130):
        svc.queue.add("tick")
    svc.flush()
    assert wait_until(lambda: len(api.bodies("/v1/analytics")) == 2 and not svc._flushing, 8)
    settle(0.2)
    assert [len(b["events"]) for b in api.bodies("/v1/analytics")] == [50, 50]
    assert len(svc.queue) == 30          # less than a full batch: it waits for the next timer tick


def test_opting_out_clears_the_queue_and_deletes_earlier_data(qapp):
    store = MemorySettings()
    svc, api = make_analytics(store=store)
    svc.set_enabled(True)
    svc.track("a")
    changes = []
    svc.enabled_changed.connect(changes.append)
    svc.set_enabled(False)
    assert svc.enabled is False and len(svc.queue) == 0 and changes == [False]
    assert wait_until(lambda: ("POST", "/v1/analytics/forget") in api.paths())
    assert wait_until(lambda: store.get(es.KEY_ANALYTICS_FORGET) == "0")
    svc.track("b")
    assert len(svc.queue) == 0


def test_a_failed_deletion_request_is_retried_later(qapp):
    store = MemorySettings()
    svc, api = make_analytics(store=store)
    svc.set_enabled(True)
    api.replies[("POST", "/v1/analytics/forget")] = ApiUnreachable("down")
    svc.set_enabled(False)
    assert wait_until(lambda: not svc._forgetting and api.paths().count(("POST", "/v1/analytics/forget")) == 1)
    assert store.get(es.KEY_ANALYTICS_FORGET) == "1"
    api.replies[("POST", "/v1/analytics/forget")] = {"deleted": 4}
    svc2, _ = make_analytics(api=api, store=store)           # next launch
    svc2.start()
    try:
        assert wait_until(lambda: store.get(es.KEY_ANALYTICS_FORGET) == "0")
    finally:
        svc2.stop()


def test_opting_in_again_cancels_a_pending_deletion(qapp):
    store = MemorySettings()
    store.set(es.KEY_ANALYTICS_FORGET, "1")
    svc, api = make_analytics(store=store)
    svc.set_enabled(True)
    svc.retry_forget()
    settle(0.05)
    assert store.get(es.KEY_ANALYTICS_FORGET) == "0" and ("POST", "/v1/analytics/forget") not in api.paths()


def test_saying_no_is_remembered_so_nobody_is_asked_twice(qapp):
    store = MemorySettings()
    svc, api = make_analytics(store=store)
    svc.set_enabled(False)
    assert svc.asked and not svc.enabled
    again, _ = make_analytics(store=store)
    assert again.asked and not again.enabled


def test_crashes_are_reported_only_after_opting_in_and_the_hook_is_removable(qapp):
    original = sys.excepthook
    seen = []
    sys.excepthook = lambda *a: seen.append(a[0])
    svc, api = make_analytics()
    try:
        svc.start()
        try:
            raise KeyError("secret file name.mp4")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        assert len(svc.queue) == 0 and seen == [KeyError]       # opted out: nothing recorded
        svc.set_enabled(True)
        try:
            raise ValueError("secret")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        (_t, ev), = svc.queue.take()
        assert ev["name"] == "crash" and ev["props"]["kind"] == "ValueError" and "secret" not in json.dumps(ev)
        svc.stop()
        assert sys.excepthook.__name__ != "hook"
    finally:
        svc.stop()
        sys.excepthook = original


def test_flush_on_exit_sends_what_is_waiting(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    svc.track("app_close")
    svc.flush_on_exit(3000)
    QCoreApplication.processEvents()
    assert api.bodies("/v1/analytics") and api.bodies("/v1/analytics")[0]["events"][0]["name"] == "app_close"


def test_opting_out_stops_sending_even_events_that_were_already_queued(qapp):
    svc, api = make_analytics()
    svc.set_enabled(True)
    svc.track("a")
    svc.set_enabled(False)
    svc.queue.add("late")                      # something slipped in after the choice
    svc.flush()
    settle(0.05)
    assert [p for p in api.paths() if p[1] == "/v1/analytics"] == []


def test_the_choice_is_read_from_storage_once_not_on_every_event(qapp):
    class CountingStore(MemorySettings):
        reads = 0

        def get(self, key, default=None):
            if key == es.KEY_ANALYTICS_ENABLED:
                CountingStore.reads += 1
            return super().get(key, default)

    svc, api = make_analytics(store=CountingStore())
    for _ in range(50):
        svc.track("tick")
    assert CountingStore.reads == 1
    svc.set_enabled(True)
    assert svc.enabled and CountingStore.reads == 1        # a change is remembered without re-reading
