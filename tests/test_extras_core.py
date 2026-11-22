"""Announcements, bug reports and analytics: the Qt-free core (verification, scrubbing, event rules)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import extras_core as ec
from src.core.extras_core import (
    Announcement, AnnouncementError, AnalyticsQueue, build_bug_payload, crash_event, install_crash_hook, make_event,
    parse_announcements, prepare_log, scrub_text,
)
from src.core.license_core import ManifestError
from test_update_core import KEYS, NOW, SRV, sign

ID1, ID2, ID3 = "a" * 32, "b" * 32, "c" * 32

# Produced by rainy-api's own code (Signer.sign_manifest("announcements", manifest_body(...))) with the test
# seed. If this stops verifying, the wire format drifted: fix the app (or bump the protocol), not the vector.
SERVER_LIST = 'eyJhbm5vdW5jZW1lbnRzIjpbeyJib2R5IjoiU2hvcnQgZG93bnRpbWUgYXJvdW5kIDlwbS5cblNhdmUgeW91ciB3b3JrIGZpcnN0LiIsImNyZWF0ZWRfYXQiOjE3OTk5OTI4MDAsImV4cGlyZXNfYXQiOjE4MDAwMzI0MDAsImlkIjoiYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWEiLCJzZXZlcml0eSI6Indhcm5pbmciLCJ0aXRsZSI6IlNlcnZlciBtYWludGVuYW5jZSB0b25pZ2h0In0seyJib2R5IjoiIiwiY3JlYXRlZF9hdCI6MTc5OTkxMzYwMCwiZXhwaXJlc19hdCI6bnVsbCwiaWQiOiJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYiIsInNldmVyaXR5IjoiaW5mbyIsInRpdGxlIjoidjEuNC4wIGlzIG91dCJ9XSwiaWF0IjoxODAwMDAwMDAwLCJraWQiOiJrMSIsImtpbmQiOiJhbm5vdW5jZW1lbnRzIiwidiI6MX0.IZAyg5TM8PaFrdvOVR1rTcZk33g7Uc5_Ra0y5K3l2S6VSjxBqla0sM9lXkDj6XEVm3BIlDuxa9uAbC-RVjyEDw'
SERVER_EMPTY_LIST = 'eyJhbm5vdW5jZW1lbnRzIjpbXSwiaWF0IjoxODAwMDAwMDAwLCJraWQiOiJrMSIsImtpbmQiOiJhbm5vdW5jZW1lbnRzIiwidiI6MX0.kS2X6YxCT4C-HNSlGObYbzgxM-l7aNZKIDe5wCcBNbJr5Pe5RUHse5XfuMfkP9yVktu2M-5skOk-3djgCEGiDA'
SERVER_LIST_TIME = 1_800_000_000          # when the server made it (iat); created/expires below are its own numbers

# What the server's own event validator (app/services/analytics.py parse_event) said about each of these, with
# [event, accepted]. The app must reach the same verdict on every row, or it would send events the server throws away.
AGREEMENT = json.loads(r'''[[{"name":"app_start"},true],[{"name":"export.xml"},true],[{"name":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},true],[{"name":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},false],[{"name":"Bad"},false],[{"name":"1x"},false],[{"name":""},false],[{"name":"has space"},false],[{"name":"ok_name"},true],[{"name":"x-y"},false],[{"name":"\u00fcber"},false],[{"name":"ok","props":{"v":null}},false],[{"name":"ok","props":{"v":1}},true],[{"name":"ok","props":{"v":-5}},true],[{"name":"ok","props":{"v":0}},true],[{"name":"ok","props":{"v":1.5}},true],[{"name":"ok","props":{"v":true}},true],[{"name":"ok","props":{"v":false}},true],[{"name":"ok","props":{"v":"script"}},true],[{"name":"ok","props":{"v":"win-x64"}},true],[{"name":"ok","props":{"v":"1.4.0"}},true],[{"name":"ok","props":{"v":"ui.window:open_view"}},true],[{"name":"ok","props":{"v":""}},true],[{"name":"ok","props":{"v":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}},true],[{"name":"ok","props":{"v":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}},false],[{"name":"ok","props":{"v":"two words"}},false],[{"name":"ok","props":{"v":"C:\\a\\b.mp4"}},false],[{"name":"ok","props":{"v":"a/b"}},false],[{"name":"ok","props":{"v":"line\nbreak"}},false],[{"name":"ok","props":{"v":"emoji\ud83d\ude42"}},false],[{"name":"ok","props":{"v":"a;b"}},false],[{"name":"ok","props":{"v":[1]}},false],[{"name":"ok","props":{"v":{"a":1}}},false],[{"name":"ok","props":{"v":9007199254740992}},true],[{"name":"ok","props":{"v":9007199254740993}},false],[{"name":"ok","props":{"v":1152921504606846976}},false],[{"name":"ok","props":{"a":1}},true],[{"name":"ok","props":{"A":1}},false],[{"name":"ok","props":{"1a":1}},false],[{"name":"ok","props":{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":1}},true],[{"name":"ok","props":{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":1}},false],[{"name":"ok","props":{"a_b":1}},true],[{"name":"ok","props":{"a-b":1}},false],[{"name":"ok","props":{"a b":1}},false],[{"name":"ok","props":{"":1}},false],[{"name":"ok","props":{"\u00fc":1}},false],[{"name":"ok","props":{"k0":1,"k1":1,"k2":1,"k3":1,"k4":1,"k5":1,"k6":1,"k7":1}},true],[{"name":"ok","props":{"k0":1,"k1":1,"k2":1,"k3":1,"k4":1,"k5":1,"k6":1,"k7":1,"k8":1}},false],[{"name":"ok","props":"x"},false],[{"name":"ok","extra":1},false]]''')


def item(id_=ID1, title="Maintenance tonight", body="Back at 9pm", severity="warning", created=NOW - 100, expires=None):
    return {"id": id_, "title": title, "body": body, "severity": severity, "created_at": created, "expires_at": expires}


def listing(*items, iat=NOW - 50):
    return {"manifest": sign("announcements", {"announcements": list(items)}, iat=iat)}


# -- verification --------------------------------------------------------------------------------------
def test_a_signed_list_is_parsed_newest_first():
    got = parse_announcements(listing(item(ID1, created=NOW - 500), item(ID2, created=NOW - 10), item(ID3, created=NOW - 100)), KEYS, NOW)
    assert [a.id for a in got] == [ID2, ID3, ID1]
    assert got[0] == Announcement(ID2, "Maintenance tonight", "Back at 9pm", "warning", NOW - 10, None)


def test_an_empty_list_is_fine():
    assert parse_announcements(listing(), KEYS, NOW) == []


def test_expired_notices_are_dropped_even_if_the_server_sent_them():
    got = parse_announcements(listing(item(ID1, expires=NOW - 1), item(ID2, expires=NOW), item(ID3, expires=NOW + 60)), KEYS, NOW)
    assert [a.id for a in got] == [ID3]


def test_at_most_twenty_are_kept():
    items = [item(f"{i:032x}", created=NOW - i) for i in range(30)]
    assert len(parse_announcements(listing(*items), KEYS, NOW)) == 20


def test_a_list_signed_by_someone_else_is_refused():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    forged = {"manifest": sign("announcements", {"announcements": [item()]}, key=Ed25519PrivateKey.generate())}
    with pytest.raises(ManifestError) as e:
        parse_announcements(forged, KEYS, NOW)
    assert e.value.code == "bad_signature"


def test_a_tampered_list_is_refused():
    token = listing(item())["manifest"]
    payload, sig = token.split(".")
    bad = payload[:-4] + ("AAAA" if not payload.endswith("AAAA") else "BBBB")
    with pytest.raises(ManifestError):
        parse_announcements({"manifest": bad + "." + sig}, KEYS, NOW)


@pytest.mark.parametrize("kind", ["app", "engines"])
def test_other_signed_answers_cannot_pass_as_announcements(kind):
    with pytest.raises(ManifestError) as e:
        parse_announcements({"manifest": sign(kind, {"announcements": [item()]})}, KEYS, NOW)
    assert e.value.code == "wrong_kind"


def test_an_old_replayed_list_is_refused_but_a_day_old_one_is_fine():
    with pytest.raises(ManifestError) as e:
        parse_announcements(listing(item(), iat=NOW - 3 * 86400), KEYS, NOW)
    assert e.value.code == "stale"
    assert parse_announcements(listing(item(), iat=NOW - 86400), KEYS, NOW)


def test_a_list_from_the_future_is_refused():
    with pytest.raises(ManifestError) as e:
        parse_announcements(listing(item(), iat=NOW + 2 * 86400), KEYS, NOW)
    assert e.value.code == "from_future"


@pytest.mark.parametrize("reply", [None, [], {}, {"manifest": 5}, {"manifest": None}, {"manifest": "not.a.token"}, {"other": "x"}])
def test_garbage_replies_are_refused(reply):
    with pytest.raises(ManifestError):
        parse_announcements(reply, KEYS, NOW)


@pytest.mark.parametrize("bad", [
    {**item(), "id": "short"}, {**item(), "id": "G" * 32}, {**item(), "id": 5}, {**item(), "title": ""}, {**item(), "title": "  "},
    {**item(), "title": "x" * 121}, {**item(), "title": "two\nlines"}, {**item(), "title": "bell\x07"},
    {**item(), "title": 5}, {**item(), "body": "x" * 2001}, {**item(), "body": "esc\x1b[31m"}, {**item(), "body": None},
    {**item(), "severity": "urgent"}, {**item(), "severity": None}, {**item(), "created_at": "now"}, {**item(), "created_at": True},
    {**item(), "created_at": 1.5}, {**item(), "expires_at": "never"}, {**item(), "expires_at": True},
    "string", 5, None, [], {},
])
def test_a_validly_signed_but_malformed_item_refuses_the_whole_list(bad):
    with pytest.raises(AnnouncementError):
        parse_announcements(listing(item(ID2), bad), KEYS, NOW)


def test_the_list_must_be_a_list_and_not_huge():
    with pytest.raises(AnnouncementError):
        parse_announcements({"manifest": sign("announcements", {"announcements": "x"}, iat=NOW - 50)}, KEYS, NOW)
    with pytest.raises(AnnouncementError):
        parse_announcements({"manifest": sign("announcements", {}, iat=NOW - 50)}, KEYS, NOW)
    too_many = [item(f"{i:032x}") for i in range(51)]
    with pytest.raises(AnnouncementError):
        parse_announcements(listing(*too_many), KEYS, NOW)


def test_markup_in_a_notice_is_kept_as_literal_text():
    got = parse_announcements(listing(item(title="<b>Hi</b>", body="<img src=x onerror=alert(1)>")), KEYS, NOW)
    assert got[0].title == "<b>Hi</b>" and got[0].body == "<img src=x onerror=alert(1)>"   # displayed as plain text by the UI


def test_newlines_and_tabs_are_allowed_in_the_body():
    assert parse_announcements(listing(item(body="a\nb\tc")), KEYS, NOW)[0].body == "a\nb\tc"


# -- scrubbing ---------------------------------------------------------------------------------------------
HOME = "C:\\Users\\Mizuusky"


def test_the_home_folder_becomes_a_tilde():
    out = scrub_text("loading C:\\Users\\Mizuusky\\Videos\\clip.mp4 ok", home=HOME, username="Mizuusky")
    assert out == "loading ~\\Videos\\clip.mp4 ok"


def test_forward_slash_and_case_variants_of_the_home_folder():
    assert scrub_text("c:/users/mizuusky/x.log", home=HOME) == "~/x.log"
    assert scrub_text("C:/Users/Mizuusky/x", home=HOME) == "~/x"


def test_other_users_folders_are_anonymised_too():
    assert scrub_text("C:\\Users\\bob\\AppData\\Local\\x.dll") == "C:\\Users\\<user>\\AppData\\Local\\x.dll"
    assert scrub_text("/home/alice/.config/app") == "/home/<user>/.config/app"
    assert scrub_text("/Users/carol/Library/Logs") == "/Users/<user>/Library/Logs"


def test_the_user_name_is_removed_wherever_it_appears():
    out = scrub_text("user=Mizuusky; Welcome, mizuusky!", home=None, username="Mizuusky")
    assert "izuusky" not in out and out.count("<user>") == 2


def test_very_short_user_names_are_not_replaced_everywhere():
    assert scrub_text("a cat sat", username="at") == "a cat sat"   # would otherwise shred ordinary words


def test_emails_are_removed():
    assert scrub_text("sent to person.name+tag@example.co.uk now") == "sent to <email> now"


def test_token_like_strings_are_redacted_but_normal_text_survives():
    token = "eyJ2IjoxLCJraWQiOiJrMSJ9" * 3 + "." + "A" * 30
    out = scrub_text(f"license {token} refreshed; device {'0' * 32} fine")
    assert "<redacted>" in out and token not in out and "0" * 32 in out and "refreshed" in out


def test_scrubbing_nothing_is_fine():
    assert scrub_text("") == "" and scrub_text(None) == ""


def test_prepare_log_keeps_the_newest_part_and_starts_on_a_whole_line():
    log = "\n".join(f"line {i:05d} " + "x" * 20 for i in range(2000))
    out = prepare_log(log, max_chars=1000)
    assert len(out) <= 1000 and out.endswith("line 01999 " + "x" * 20)
    assert out.startswith("line ")        # cut at a line boundary, not mid-word


def test_prepare_log_scrubs_after_trimming():
    out = prepare_log("a\nC:\\Users\\Mizuusky\\x", home=HOME, username="Mizuusky")
    assert "Mizuusky" not in out


# -- the request body ------------------------------------------------------------------------------------------
def test_bug_payload_shape_and_limits():
    body = build_bug_payload("  Export crashes  ", "  it closes  ", "log text", "1.3.0")
    assert json.loads(body) == {"title": "Export crashes", "description": "it closes", "log": "log text", "app_version": "1.3.0"}
    big = json.loads(build_bug_payload("t" * 500, "d" * 9000, "", ""))
    assert len(big["title"]) == 120 and len(big["description"]) == 4000 and "app_version" not in big


def test_bug_payload_never_exceeds_the_server_limit_even_for_awkward_text():
    for text in ("é" * 90_000, "日本語" * 40_000, "\x01\x02" * 50_000, "\n" * 150_000):
        body = build_bug_payload("t", "日" * 4000, text, "1.3.0")
        assert len(body) <= ec.BUG_BODY_MAX_BYTES < 320_000
        assert json.loads(body)["title"] == "t"


def test_bug_payload_drops_the_oldest_log_first():
    log = "".join(f"{i:06d}\n" for i in range(60_000))
    sent = json.loads(build_bug_payload("t", "", log, "1"))["log"]
    assert len(sent) < len(log) and sent.endswith("059999\n")


# -- analytics events ------------------------------------------------------------------------------------------
def test_valid_events():
    assert make_event("app_start") == {"name": "app_start", "props": {}}
    assert make_event("export.xml", {"clips": 12, "ratio": 0.5, "ok": True, "view": "script"})["props"] == {"clips": 12, "ratio": 0.5, "ok": True, "view": "script"}


@pytest.mark.parametrize("name,props", [
    ("Bad", None), ("1x", None), ("", None), ("x" * 49, None), (None, None), (5, None), ("has space", None),
    ("ok", {"Bad": 1}), ("ok", {"a": [1]}), ("ok", {"a": {"b": 1}}), ("ok", {"a": None}), ("ok", {"a": "x" * 65}),
    ("ok", {"a": "line\nbreak"}), ("ok", {f"k{i}": 1 for i in range(9)}), ("ok", {"a": 2 ** 60}), ("ok", {"a": float("nan")}),
    ("ok", {"a": float("inf")}), ("ok", {1: 2}), ("ok", {"a": b"bytes"}),
])
def test_invalid_events_are_refused_quietly(name, props):
    assert make_event(name, props) is None


def test_the_limits_that_are_allowed():
    assert make_event("a" * 48, {f"k{i}": "x" * 64 for i in range(8)}) is not None


@pytest.mark.parametrize("text", [
    "C:\\Users\\me\\clip.mp4", "D:/footage/clip.mp4", "../escape", "Secret Project", "a sentence with spaces", "multi\nline",
    "tab\there", "quote'd", 'say "hi"', "<b>x</b>", "emoji🙂", "naïve", "x" * 80, "a;b", "a,b", "(x)", "a=b",
])
def test_there_is_no_place_for_paths_titles_or_sentences(text):
    assert make_event("export", {"value": text}) is None


@pytest.mark.parametrize("text", ["script", "win-x64", "1.4.0", "ui.window:open_view", "export_xml", "A+B", "", "x" * 64])
def test_identifier_like_values_are_fine(text):
    assert make_event("export", {"value": text}) is not None


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_queue_is_ordered_bounded_and_ages_events():
    clk = Clock()
    q = AnalyticsQueue(clock=clk, maxlen=5)
    assert not q.add("Bad")
    for i in range(7):
        assert q.add(f"e{i}")
        clk.t += 10
    assert len(q) == 5                         # the oldest two fell off
    taken = q.take(3)
    assert [ev["name"] for _t, ev in taken] == ["e2", "e3", "e4"]
    wire = json.loads(q.to_wire(taken))
    assert [e["name"] for e in wire["events"]] == ["e2", "e3", "e4"]
    assert [e["age"] for e in wire["events"]] == [50, 40, 30]


def test_events_older_than_a_day_are_never_sent():
    clk = Clock()
    q = AnalyticsQueue(clock=clk)
    q.add("old")
    clk.t += 86400 + 5
    q.add("new")
    assert [ev["name"] for _t, ev in q.take()] == ["new"]


def test_a_failed_send_puts_events_back_in_order_within_the_limit():
    clk = Clock()
    q = AnalyticsQueue(clock=clk, maxlen=4)
    for n in "abcd":
        q.add(n)
    taken = q.take(2)
    q.add("e")                                  # queue is now c, d, e: one slot free
    q.put_back(taken)                           # only the newer of the two (b) fits; the oldest is dropped
    assert [ev["name"] for _t, ev in q.take(10)] == ["b", "c", "d", "e"]
    q2 = AnalyticsQueue(clock=clk, maxlen=4)
    for n in "abcd":
        q2.add(n)
    taken = q2.take(2)
    q2.put_back(taken)
    assert [ev["name"] for _t, ev in q2.take(10)] == ["a", "b", "c", "d"]


def test_the_wire_format_matches_what_the_server_accepts():
    q = AnalyticsQueue(clock=Clock())
    q.add("export.xml", {"clips": 3})
    body = json.loads(q.to_wire(q.take()))
    assert set(body) == {"events"} and body["events"][0].keys() == {"name", "props", "age"}


# -- crashes -------------------------------------------------------------------------------------------------------
def test_a_crash_event_names_the_error_and_place_but_never_the_message_or_path():
    def deep():
        raise ValueError("C:\\Users\\Mizuusky\\Secret Project\\clip.mp4 is corrupt")

    try:
        deep()
    except ValueError as e:
        ev = crash_event(type(e), e.__traceback__)
    assert ev["name"] == "crash" and ev["props"]["kind"] == "ValueError"
    assert ev["props"]["where"].endswith(":deep") and "Mizuusky" not in json.dumps(ev) and "corrupt" not in json.dumps(ev)
    assert "\\" not in ev["props"]["where"] and "/" not in ev["props"]["where"]


def test_odd_exception_names_are_cleaned():
    class Weird(Exception):
        pass

    Weird.__name__ = "Bad Name!\n"
    ev = crash_event(Weird, None)
    assert ev["props"]["kind"] == "BadName" and ev["props"]["where"] == "unknown"


def test_the_crash_hook_reports_and_still_calls_the_previous_hook():
    seen, previous_calls = [], []
    original = sys.excepthook
    sys.excepthook = lambda *a: previous_calls.append(a[0])
    try:
        undo = install_crash_hook(seen.append)
        try:
            raise KeyError("x")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        assert [e["props"]["kind"] for e in seen] == ["KeyError"] and previous_calls == [KeyError]
        for quiet in (KeyboardInterrupt, SystemExit):                  # not crashes
            sys.excepthook(quiet, quiet(), None)
        assert len(seen) == 1 and previous_calls[-2:] == [KeyboardInterrupt, SystemExit]
        undo()
        assert sys.excepthook is not None and sys.excepthook.__name__ != "hook"
    finally:
        sys.excepthook = original


def test_a_failing_sink_never_breaks_error_reporting():
    previous_calls = []
    original = sys.excepthook
    sys.excepthook = lambda *a: previous_calls.append(1)
    try:
        undo = install_crash_hook(lambda ev: (_ for _ in ()).throw(RuntimeError("sink broke")))
        try:
            raise KeyError("x")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        assert previous_calls == [1]
        undo()
    finally:
        sys.excepthook = original


# -- vectors from the real server ---------------------------------------------------------------------------------
SERVER_NOW = SERVER_LIST_TIME + 60


def test_the_servers_own_list_verifies_and_parses():
    got = parse_announcements({"manifest": SERVER_LIST}, KEYS, SERVER_NOW)
    assert got == [
        Announcement("a" * 32, "Server maintenance tonight", "Short downtime around 9pm.\nSave your work first.", "warning",
                     1799992800, 1800032400),
        Announcement("b" * 32, "v1.4.0 is out", "", "info", 1799913600, None),
    ]


def test_the_servers_empty_list_verifies():
    assert parse_announcements({"manifest": SERVER_EMPTY_LIST}, KEYS, SERVER_NOW) == []


def test_the_servers_list_is_dropped_once_it_expires_on_the_clock():
    got = parse_announcements({"manifest": SERVER_LIST}, KEYS, 1800032400 + 1)     # past the first notice's expiry
    assert [a.id for a in got] == ["b" * 32]


@pytest.mark.parametrize("kind_check", ["app", "engines"])
def test_the_servers_announcement_list_is_not_an_update_feed_or_engine_list(kind_check):
    from src.core.update_core import parse_engine_manifest, parse_feed
    parser = parse_feed if kind_check == "app" else parse_engine_manifest
    with pytest.raises(ManifestError) as e:
        parser({"manifest": SERVER_LIST}, KEYS, SERVER_NOW)
    assert e.value.code == "wrong_kind"


def test_the_app_and_server_agree_on_which_events_are_valid():
    assert len(AGREEMENT) == 50 and any(ok for _ev, ok in AGREEMENT) and any(not ok for _ev, ok in AGREEMENT)
    disagreements = [(ev, server_ok) for ev, server_ok in AGREEMENT
                     if (make_event(ev.get("name"), ev.get("props")) is not None) != server_ok
                     and set(ev) <= {"name", "props"}]                       # unknown top-level fields are the server's check
    assert disagreements == []
