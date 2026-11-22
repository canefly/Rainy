"""Update core: signed feed, download rules, installer safety, engine manifest.

The FEED / NULL_FEED / ENGINES strings were produced by rainy-api's own signing code with a
throwaway test seed (the same one test_license_core uses). If they stop verifying, the app and the
server disagree on the manifest format and nobody can update: fix the app, don't edit the vectors.
"""
import hashlib
import http.server
import json
import os
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core import license_core as lc
from src.core import update_core as uc
from src.core.license_core import (
    ApiRejected, ApiUnreachable, MANIFEST_PREFIX, ManifestError, b64u, b64u_decode, load_public_key,
    sign_request_headers, canonical_request, verify_manifest,
)
from src.core.update_core import (
    DownloadError, UpdateManifest, UpdateStatus, download_verified, evaluate, parse_engine_manifest, parse_feed,
    parse_version,
)
from test_license_core import SRV_PUB, SRV_SEED, Env, dev_key, issue

KEYS = {"k1": load_public_key(SRV_PUB)}
SRV = Ed25519PrivateKey.from_private_bytes(b64u_decode(SRV_SEED))
NOW = 1_800_000_100

FEED = 'eyJpYXQiOjE4MDAwMDAwMDAsImtpZCI6ImsxIiwia2luZCI6ImFwcCIsIm1pbl9zdXBwb3J0ZWQiOiIxLjIuMCIsIm5vdGVzIjpbIkZhc3RlciBleHBvcnQiLCJGaXhlZCBhIGNyYXNoIl0sInJlbGVhc2VkX2F0IjoiMjAyNi0xMS0yMlQxMDowMDowMFoiLCJzaGEyNTYiOiJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiIiwic2l6ZSI6MTIzNDU2LCJ1cmwiOiJodHRwczovL2NsaWNrLmNhbmVmbHkueHl6L2Rvd25sb2FkL0NsaWNrYWJsZXNUb29sc19TZXR1cF92MS4zLjAuZXhlIiwidiI6MSwidmVyc2lvbiI6IjEuMy4wIn0.MJ6twCjwF09UCo79QsHvUYO7H86gvf1gbcbKB6muFUV_SXYm56WIL20EiCRAJemrPhMMYDh9w8Qz-6WzxZmZCw'
NULL_FEED = 'eyJpYXQiOjE4MDAwMDAwMDAsImtpZCI6ImsxIiwia2luZCI6ImFwcCIsInYiOjEsInZlcnNpb24iOm51bGx9.vgUm1l5r0-uXGmLm10ouiQxSUkwuTybKdudTuOqHqxous1j1nDwOEHwKRbEd-ZKQFIKsOa9Kvp0QKHQ5xsolAA'
ENGINES = 'eyJlbmdpbmVzIjpbeyJlbmdpbmUiOiJmZm1wZWciLCJmb3JtYXQiOiJ6aXAiLCJwbGF0Zm9ybSI6Indpbi14NjQiLCJzaGEyNTYiOiJjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkY2RjZGNkIiwic2l6ZSI6NDA5NiwidXJsIjoiaHR0cHM6Ly9lbmcuZXhhbXBsZS5vcmcvZmYuemlwIiwidmVyc2lvbiI6IjIwMjUxMTAxIn0seyJlbmdpbmUiOiJsaWJtcHYiLCJmb3JtYXQiOiI3eiIsInBsYXRmb3JtIjoid2luLXg2NCIsInNoYTI1NiI6ImVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWZlZmVmZWYiLCJzaXplIjo4MTkyLCJ1cmwiOiJodHRwczovL2VuZy5leGFtcGxlLm9yZy9tcHYuN3oiLCJ2ZXJzaW9uIjoiMjAyNTExMDIifV0sImlhdCI6MTgwMDAwMDAwMCwia2lkIjoiazEiLCJraW5kIjoiZW5naW5lcyIsInYiOjF9.kXgrmguEv2wdYr7JzHG1Cwj9C2HCUbV0d7PHd_GO4Yi58hHaFh-63yEMxEcaBx4wojBXXcSmvzLqqw3JWkiLBg'


def sign(kind, body, *, iat=1_800_000_000, kid="k1", key=SRV, v=1, prefix=MANIFEST_PREFIX):
    """An independent signer (not the server's code) for building malformed-but-validly-signed payloads."""
    payload = {**body, "v": v, "kind": kind, "kid": kid, "iat": iat}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{b64u(raw)}.{b64u(key.sign(prefix + raw))}"


GOOD_APP = {"version": "1.3.0", "url": "https://x.example/a.exe", "sha256": "ab" * 32, "size": 100,
            "notes": ["a"], "min_supported": "1.0.0", "released_at": "2026-11-22T10:00:00Z"}


def feed_of(**over):
    return {"manifest": sign("app", {**GOOD_APP, **over})}


# -- vectors from the real server ---------------------------------------------------------------
def test_server_feed_verifies_and_parses():
    m = parse_feed({"manifest": FEED}, KEYS, NOW)
    assert m == UpdateManifest(
        version="1.3.0", released_at="2026-11-22T10:00:00Z", size=123456, sha256="ab" * 32, min_supported="1.2.0",
        url="https://rainy.canefly.xyz/download/RainyTools_Setup_v1.3.0.exe", notes=("Faster export", "Fixed a crash"))


def test_server_null_feed_means_nothing_published():
    assert parse_feed({"manifest": NULL_FEED}, KEYS, NOW) is None


def test_server_engine_manifest_verifies_and_parses():
    entries = parse_engine_manifest({"manifest": ENGINES}, KEYS, NOW)
    assert [(e.engine, e.platform, e.format, e.size) for e in entries] == [("ffmpeg", "win-x64", "zip", 4096), ("libmpv", "win-x64", "7z", 8192)]
    assert entries[0].sha256 == "cd" * 32 and entries[1].url == "https://eng.example.org/mpv.7z"


def test_a_feed_is_not_an_engine_list_and_vice_versa():
    with pytest.raises(ManifestError) as e:
        parse_engine_manifest({"manifest": FEED}, KEYS, NOW)
    assert e.value.code == "wrong_kind"
    with pytest.raises(ManifestError) as e:
        parse_feed({"manifest": ENGINES}, KEYS, NOW)
    assert e.value.code == "wrong_kind"


def test_a_license_token_cannot_pass_as_a_manifest():
    dev = Env()
    dev.registered()
    with pytest.raises(ManifestError) as e:
        verify_manifest(issue("d" * 32, dev.pub), KEYS, NOW, kind="app")
    assert e.value.code == "bad_signature"


def test_tampering_unknown_keys_and_garbage():
    head, sig = FEED.split(".")
    payload = json.loads(b64u_decode(head))
    payload["url"] = "https://evil.example/malware.exe"
    forged = b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    for reply, code in [({"manifest": f"{forged}.{sig}"}, "bad_signature"), ({"manifest": ""}, "malformed"),
                        ({"manifest": "a.b.c"}, "malformed"), ({}, "malformed"), ({"manifest": 5}, "malformed"),
                        ({"manifest": None}, "malformed"), ("not a dict", "malformed")]:
        with pytest.raises(ManifestError) as e:
            parse_feed(reply, KEYS, NOW)
        assert e.value.code == code, reply
    with pytest.raises(ManifestError) as e:
        parse_feed({"manifest": FEED}, {}, NOW)                 # a build with no keys can trust nothing
    assert e.value.code == "unknown_kid"
    with pytest.raises(ManifestError) as e:
        parse_feed({"manifest": FEED}, {"k1": Ed25519PrivateKey.generate().public_key()}, NOW)
    assert e.value.code == "bad_signature"


def test_replayed_old_feeds_are_refused_but_normal_age_is_fine():
    parse_feed({"manifest": FEED}, KEYS, 1_800_000_000 + 29 * 86400)
    with pytest.raises(ManifestError) as e:
        parse_feed({"manifest": FEED}, KEYS, 1_800_000_000 + 31 * 86400)
    assert e.value.code == "stale"
    with pytest.raises(ManifestError) as e:                      # an engine list goes stale faster
        parse_engine_manifest({"manifest": ENGINES}, KEYS, 1_800_000_000 + 8 * 86400)
    assert e.value.code == "stale"
    with pytest.raises(ManifestError) as e:
        parse_feed({"manifest": FEED}, KEYS, 1_800_000_000 - 3 * 86400)   # claims to be from the future
    assert e.value.code == "from_future"


def test_signature_prefix_is_separate_from_licenses_and_requests():
    assert MANIFEST_PREFIX == b"RAINY-MANIFEST-1\n"
    wrong_prefix = sign("app", GOOD_APP, prefix=b"RAINY-TOKEN-1\n")
    with pytest.raises(ManifestError) as e:
        verify_manifest(wrong_prefix, KEYS, NOW, kind="app")
    assert e.value.code == "bad_signature"
    assert sign("app", GOOD_APP, v=2) and pytest.raises(ManifestError, verify_manifest, sign("app", GOOD_APP, v=2), KEYS, NOW, kind="app")


# -- signed but malformed payloads are still refused -----------------------------------------------
@pytest.mark.parametrize("over", [
    {"version": "1.3"}, {"version": "v1.3.0"}, {"version": 130}, {"version": "01.3.0"},
    {"min_supported": "x"}, {"min_supported": 5},
    {"url": None}, {"url": 5},
    {"sha256": "ab" * 31}, {"sha256": "AB" * 32}, {"sha256": "zz" * 32}, {"sha256": 5}, {"sha256": None},
    {"size": 0}, {"size": -1}, {"size": "100"}, {"size": True}, {"size": 1.5},
    {"notes": "text"}, {"notes": [1, 2]}, {"notes": [["x"]]},
])
def test_malformed_signed_feed_fields_are_refused(over):
    with pytest.raises(ManifestError) as e:
        parse_feed(feed_of(**over), KEYS, NOW)
    assert e.value.code == "malformed"


def test_optional_fields_and_note_limits():
    m = parse_feed(feed_of(size=None, notes=["n" * 1000] * 80), KEYS, NOW)
    assert m.size is None and len(m.notes) == 50 and len(m.notes[0]) == 300
    m = parse_feed({"manifest": sign("app", {k: v for k, v in GOOD_APP.items() if k in ("version", "url", "sha256")})}, KEYS, NOW)
    assert m.min_supported == "0.0.0" and m.notes == () and m.size is None


@pytest.mark.parametrize("entry_over", [
    {"engine": "evil"}, {"format": "rar"}, {"sha256": "ab" * 31}, {"sha256": "AB" * 32}, {"size": 0}, {"size": True},
    {"size": "9"}, {"url": 5}, {"platform": 5},
])
def test_malformed_engine_entries_poison_the_whole_list(entry_over):
    good = {"engine": "ffmpeg", "platform": "win-x64", "version": "1", "url": "https://x.example/a.zip", "sha256": "ab" * 32,
            "size": 10, "format": "zip"}
    with pytest.raises(ManifestError) as e:
        parse_engine_manifest({"manifest": sign("engines", {"engines": [good, {**good, **entry_over}]})}, KEYS, NOW)
    assert e.value.code == "malformed"
    for body in ({"engines": "x"}, {}, {"engines": [5]}, {"engines": [{"engine": "ffmpeg"}]}):
        with pytest.raises(ManifestError):
            parse_engine_manifest({"manifest": sign("engines", body)}, KEYS, NOW)


# -- versions ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("cur,latest,min_s,expected", [
    ("1.2.0", "1.3.0", "0.0.0", UpdateStatus.AVAILABLE),
    ("1.2.0", "1.2.0", "0.0.0", UpdateStatus.UP_TO_DATE),
    ("1.3.0", "1.2.0", "0.0.0", UpdateStatus.UP_TO_DATE),          # never "update" backwards
    ("1.2.0", "1.10.0", "0.0.0", UpdateStatus.AVAILABLE),          # numeric, not text, ordering
    ("1.1.0", "1.3.0", "1.2.0", UpdateStatus.REQUIRED),
    ("1.2.0", "1.3.0", "1.2.0", UpdateStatus.AVAILABLE),           # exactly at the minimum is still allowed
    ("1.0.0", "1.1.0", "2.0.0", UpdateStatus.REQUIRED),
    ("v1.2.0", "1.3.0", "0.0.0", UpdateStatus.AVAILABLE),
])
def test_evaluate(cur, latest, min_s, expected):
    m = UpdateManifest(latest, "", "https://x/a.exe", "ab" * 32, None, (), min_s)
    assert evaluate(cur, m) is expected


def test_nothing_published_is_up_to_date():
    assert evaluate("1.2.0", None) is UpdateStatus.UP_TO_DATE


def test_lenient_parse_version_for_the_running_app():
    assert parse_version("1.2.0") == (1, 2, 0) and parse_version("v1.2") == (1, 2, 0) and parse_version("") == (0, 0, 0)
    assert parse_version("v1.3.0-rc1") == (1, 3, 0) and parse_version("garbage") == (0, 0, 0)


# -- local file server for download tests ----------------------------------------------------------------
class FileServer:
    def __init__(self, routes):
        self.routes = routes
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                r = outer.routes.get(self.path)
                if r is None:
                    self.send_error(404)
                    return
                if "redirect" in r:
                    self.send_response(302)
                    self.send_header("Location", r["redirect"])
                    self.end_headers()
                    return
                body = r["body"]
                self.send_response(200)
                self.send_header("Content-Length", str(r.get("declared", len(body))))
                self.end_headers()
                try:
                    for i in range(0, len(body), 65536):
                        self.wfile.write(body[i:i + 65536])
                        self.wfile.flush()
                        if r.get("delay"):
                            time.sleep(r["delay"])     # lets a test cancel while the download is still running
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path="/a.exe"):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


BODY = os.urandom(300_000)
SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def server():
    s = FileServer({"/a.exe": {"body": BODY}})
    yield s
    s.close()


# -- download_verified ------------------------------------------------------------------------------------
def test_good_download_is_saved_atomically_and_reports_progress(server, tmp_path):
    dest = tmp_path / "up" / "Setup.exe"
    seen = []
    out = download_verified(server.url(), dest, sha256=SHA, size=len(BODY), progress=lambda g, t: seen.append((g, t)))
    assert out == dest and dest.read_bytes() == BODY
    assert not (tmp_path / "up" / "Setup.exe.part").exists()
    assert seen[-1] == (len(BODY), len(BODY)) and [g for g, _ in seen] == sorted(g for g, _ in seen)


def test_uppercase_hash_from_the_manifest_is_accepted(server, tmp_path):
    download_verified(server.url(), tmp_path / "s.exe", sha256=SHA.upper(), size=len(BODY))


def test_wrong_content_is_discarded_and_never_left_on_disk(tmp_path):
    evil = bytes(len(BODY))                                       # same size, different bytes
    s = FileServer({"/a.exe": {"body": evil}})
    try:
        dest = tmp_path / "Setup.exe"
        with pytest.raises(DownloadError) as e:
            download_verified(s.url(), dest, sha256=SHA, size=len(BODY))
        assert e.value.code == "hash_mismatch"
        assert not dest.exists() and not (tmp_path / "Setup.exe.part").exists()
    finally:
        s.close()


def test_a_failed_download_leaves_the_previous_good_file_alone(tmp_path):
    dest = tmp_path / "Setup.exe"
    dest.write_bytes(b"previous verified installer")
    s = FileServer({"/a.exe": {"body": b"x" * 1000}})
    try:
        with pytest.raises(DownloadError):
            download_verified(s.url(), dest, sha256=SHA, size=1000)
        assert dest.read_bytes() == b"previous verified installer"
    finally:
        s.close()


def test_wrong_size_is_refused_even_if_it_hashes_right(tmp_path):
    s = FileServer({"/a.exe": {"body": BODY}})
    try:
        with pytest.raises(DownloadError) as e:
            download_verified(s.url(), tmp_path / "s.exe", sha256=SHA, size=len(BODY) + 5)
        assert e.value.code == "size_mismatch" and not (tmp_path / "s.exe").exists()
    finally:
        s.close()


def test_an_endless_or_oversized_download_is_cut_off_early(tmp_path):
    big = os.urandom(5_000_000)
    s = FileServer({"/a.exe": {"body": big}})
    got = []
    try:
        with pytest.raises(DownloadError) as e:
            download_verified(s.url(), tmp_path / "s.exe", sha256=SHA, size=1000, progress=lambda g, t: got.append(g))
        assert e.value.code == "too_big"
        assert max(got) < 2_100_000                              # stopped long before reading all 5 MB
        assert not (tmp_path / "s.exe.part").exists()
    finally:
        s.close()


def test_max_bytes_applies_when_no_size_is_signed(server, tmp_path):
    with pytest.raises(DownloadError) as e:
        download_verified(server.url(), tmp_path / "s.exe", sha256=SHA, max_bytes=100_000)
    assert e.value.code == "too_big"


def test_cancelling_stops_the_download_and_cleans_up(server, tmp_path):
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 2
    with pytest.raises(DownloadError) as e:
        download_verified(server.url(), tmp_path / "s.exe", sha256=SHA, size=len(BODY), cancelled=cancelled)
    assert e.value.code == "cancelled"
    assert list(tmp_path.iterdir()) == []


def test_network_failures_are_reported_as_network(tmp_path):
    s = FileServer({})
    port_url = s.url("/missing.exe")
    try:
        with pytest.raises(DownloadError) as e:
            download_verified(port_url, tmp_path / "s.exe", sha256=SHA)    # 404
        assert e.value.code == "network"
    finally:
        s.close()
    with pytest.raises(DownloadError) as e:
        download_verified(port_url, tmp_path / "s.exe", sha256=SHA)        # nothing listening any more
    assert e.value.code == "network" and list(tmp_path.iterdir()) == []


def test_unwritable_destination_is_a_disk_error(server, tmp_path):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    with pytest.raises(DownloadError) as e:
        download_verified(server.url(), blocker / "s.exe", sha256=SHA)
    assert e.value.code == "disk"


@pytest.mark.parametrize("url", [
    "http://example.com/a.exe", "ftp://example.com/a.exe", "file:///C:/a.exe", "https://", "https:///a.exe",
    "", "javascript:alert(1)", "https://exa mple.com/a.exe", "https://example.com/a\n.exe", "//example.com/a.exe",
    "http://localhost.evil.example/a.exe", "http://127.0.0.1.evil.example/a.exe",
])
def test_only_https_urls_are_accepted(tmp_path, url):
    with pytest.raises(DownloadError) as e:
        download_verified(url, tmp_path / "s.exe", sha256=SHA, allow_http_local=True)
    assert e.value.code == "bad_url"


def test_plain_http_to_localhost_needs_the_dev_switch(server, tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "IS_COMPILED", True)                  # a shipped build
    with pytest.raises(DownloadError) as e:
        download_verified(server.url(), tmp_path / "s.exe", sha256=SHA)
    assert e.value.code == "bad_url"
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    download_verified(server.url(), tmp_path / "s.exe", sha256=SHA, size=len(BODY))   # from source it is fine


def test_redirects_cannot_downgrade_to_http_or_leave_for_another_host(tmp_path):
    s = FileServer({"/a.exe": {"body": BODY}, "/same": {"redirect": "/a.exe"},
                    "/evil": {"redirect": "http://example.org/a.exe"}, "/ftp": {"redirect": "ftp://example.org/a.exe"}})
    try:
        download_verified(s.url("/same"), tmp_path / "ok.exe", sha256=SHA, size=len(BODY), allow_http_local=True)
        for path in ("/evil", "/ftp"):
            with pytest.raises(DownloadError) as e:
                download_verified(s.url(path), tmp_path / "bad.exe", sha256=SHA, allow_http_local=True)
            assert e.value.code == "bad_url", path
        assert not (tmp_path / "bad.exe").exists()
    finally:
        s.close()


def test_an_injected_opener_is_used_and_missing_headers_are_fine(tmp_path):
    class Res:
        def __init__(self):
            self.chunks = [b"abc", b"def", b""]

        def read(self, n):
            return self.chunks.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    out = download_verified("https://x.example/a.exe", tmp_path / "s.exe", sha256=hashlib.sha256(b"abcdef").hexdigest(),
                            opener=lambda req, timeout: Res())
    assert out.read_bytes() == b"abcdef"


# -- verify_file + installer files -------------------------------------------------------------------------
def test_verify_file(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(BODY)
    uc.verify_file(f, SHA, len(BODY))
    uc.verify_file(f, SHA)
    for sha, size, code in [("0" * 64, None, "hash_mismatch"), (SHA, 1, "size_mismatch")]:
        with pytest.raises(DownloadError) as e:
            uc.verify_file(f, sha, size)
        assert e.value.code == code
    with pytest.raises(DownloadError) as e:
        uc.verify_file(tmp_path / "missing", SHA)
    assert e.value.code == "bad_file"


def manifest(version="1.3.0", url="https://x.example/dl/Setup.exe", sha=SHA, size=len(BODY)):
    return UpdateManifest(version, "", url, sha, size, (), "0.0.0")


def test_installer_names_come_from_the_version_never_the_url(tmp_path):
    for url, ext in [("https://x.example/../../Windows/evil.exe", ".exe"), ("https://x.example/a.msi", ".msi"),
                     ("https://x.example/a.bat", ".exe"), ("https://x.example/a", ".exe"), ("https://x.example/a.EXE?x=1", ".exe"),
                     ("https://x.example/a.exe/../../b.cmd", ".exe")]:
        p = uc.installer_path_for(manifest(url=url), tmp_path)
        assert p.parent == tmp_path and p.name == f"RainyTools_Setup_v1.3.0{ext}", url


def test_default_updates_dir_is_under_the_app_data_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert uc.updates_dir() == tmp_path / "com.canefly.knot" / "updates"


def test_prune_keeps_only_what_it_should(tmp_path):
    (tmp_path / "RainyTools_Setup_v1.2.0.exe").write_bytes(b"old")
    (tmp_path / "RainyTools_Setup_v1.3.0.exe").write_bytes(b"keep")
    (tmp_path / "RainyTools_Setup_v1.3.0.exe.part").write_bytes(b"half")
    (tmp_path / "unrelated.txt").write_text("mine")
    uc.prune_updates(tmp_path, keep=tmp_path / "RainyTools_Setup_v1.3.0.exe")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["RainyTools_Setup_v1.3.0.exe", "unrelated.txt"]
    uc.prune_updates(tmp_path / "does-not-exist")                  # must not raise


def test_find_verified_download(tmp_path):
    m = manifest()
    assert uc.find_verified_download(m, tmp_path) is None
    path = uc.installer_path_for(m, tmp_path)
    path.write_bytes(BODY)
    assert uc.find_verified_download(m, tmp_path) == path
    path.write_bytes(BODY[:-1] + b"X")
    assert uc.find_verified_download(m, tmp_path) is None          # tampered while it sat on disk
    path.write_bytes(BODY + b"extra")
    assert uc.find_verified_download(m, tmp_path) is None


def test_launch_verifies_then_starts(tmp_path):
    m = manifest()
    path = uc.installer_path_for(m, tmp_path)
    path.write_bytes(BODY)
    started = []
    uc.launch_installer(path, m, directory=tmp_path, starter=started.append)
    assert started == [path]


def test_launch_refuses_a_file_swapped_after_download(tmp_path):
    m = manifest()
    path = uc.installer_path_for(m, tmp_path)
    path.write_bytes(BODY)
    path.write_bytes(b"MZ malware")                                 # replaced between download and click
    started = []
    with pytest.raises(DownloadError) as e:
        uc.launch_installer(path, m, directory=tmp_path, starter=started.append)
    assert e.value.code in ("hash_mismatch", "size_mismatch") and started == []


def test_launch_refuses_files_outside_the_updates_folder_and_missing_ones(tmp_path):
    m = manifest()
    inside = tmp_path / "updates"
    inside.mkdir()
    outside = tmp_path / "elsewhere.exe"
    outside.write_bytes(BODY)
    started = []
    for bad in (outside, inside / "missing.exe", inside, inside / ".." / "elsewhere.exe"):
        with pytest.raises(DownloadError) as e:
            uc.launch_installer(bad, m, directory=inside, starter=started.append)
        assert e.value.code == "bad_file", bad
    assert started == []


def test_launch_refuses_symlinks(tmp_path):
    m = manifest()
    inside = tmp_path / "updates"
    inside.mkdir()
    target = tmp_path / "real.exe"
    target.write_bytes(BODY)
    link = inside / "RainyTools_Setup_v1.3.0.exe"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this machine")
    with pytest.raises(DownloadError):
        uc.launch_installer(link, m, directory=inside, starter=lambda p: None)


# -- engines -------------------------------------------------------------------------------------------------
def test_platform_id_shape_and_picking():
    assert uc.platform_id().split("-")[0] in ("win", "darwin", "linux")
    entries = parse_engine_manifest({"manifest": ENGINES}, KEYS, NOW)
    assert uc.pick_engine(entries, "ffmpeg", "win-x64").version == "20251101"
    assert uc.pick_engine(entries, "ffmpeg", "darwin-arm64") is None
    assert uc.pick_engine(entries, "whisper", "win-x64") is None


class FakeController:
    keys = KEYS

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc = reply, exc

    def fetch_engine_manifest(self):
        if self.exc:
            raise self.exc
        return self.reply


@pytest.mark.parametrize("exc,code", [
    (ApiUnreachable("down"), "unreachable"), (ApiRejected(401, "not_registered"), "not_registered"),
    (ApiRejected(403, "not_approved"), "not_approved"), (ApiRejected(500, "weird"), "rejected"),
    (ApiRejected(429, "rate_limited"), "rejected"),
])
def test_engine_list_errors_become_messages_for_the_user(exc, code):
    with pytest.raises(uc.EngineManifestError) as e:
        uc.load_engine_entries(FakeController(exc=exc), NOW)
    assert e.value.code == code and e.value.message and not e.value.message.startswith("Api")


def test_engine_list_that_fails_verification_downloads_nothing():
    with pytest.raises(uc.EngineManifestError) as e:
        uc.load_engine_entries(FakeController(reply={"manifest": FEED}), NOW)       # the wrong kind of manifest
    assert e.value.code == "invalid"
    forged = {"manifest": ENGINES.rsplit(".", 1)[0] + "." + b64u(b"x" * 64)}
    with pytest.raises(uc.EngineManifestError) as e:
        uc.load_engine_entries(FakeController(reply=forged), NOW)
    assert e.value.code == "invalid"


def test_engine_list_success():
    entries = uc.load_engine_entries(FakeController(reply={"manifest": ENGINES}), NOW)
    assert len(entries) == 2


# -- the HTTP client additions and the controller's engine call ------------------------------------------------
def test_get_version_is_a_plain_unsigned_get():
    seen = {}

    class Resp:
        def read(self, n=-1):
            return b'{"manifest": "x"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.update(method=req.get_method(), data=req.data, url=req.full_url, headers={k.lower(): v for k, v in req.header_items()})
        return Resp()
    api = lc.LicenseApi("http://srv/", opener=opener, clock=lambda: 1)
    assert api.get_version() == {"manifest": "x"}
    assert seen["method"] == "GET" and seen["data"] is None and seen["url"] == "http://srv/v1/version"
    assert "x-signature" not in seen["headers"] and "content-type" not in seen["headers"]   # public: nothing to sign


def test_get_engine_manifest_is_a_signed_get():
    seen = {}

    class Resp:
        def read(self, n=-1):
            return b'{"manifest": "x"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.update(method=req.get_method(), data=req.data, headers={k.lower(): v for k, v in req.header_items()})
        return Resp()
    api = lc.LicenseApi("http://srv/", opener=opener, clock=lambda: 1_800_000_000)
    api.get_engine_manifest("a" * 32, dev_key())
    h = seen["headers"]
    assert seen["method"] == "GET" and seen["data"] is None and h["x-device-id"] == "a" * 32
    dev_key().public_key().verify(b64u_decode(h["x-signature"]),
                                  canonical_request("GET", "/v1/engines/manifest", h["x-timestamp"], h["x-nonce"], b""))


def test_controller_fetch_engine_manifest():
    env = Env()
    with pytest.raises(ApiRejected) as e:
        env.ctl.fetch_engine_manifest()                              # not registered yet
    assert e.value.code == "not_registered"
    env.registered()
    calls = []
    env.api.get_engine_manifest = lambda device_id, key: calls.append((device_id, key)) or {"manifest": "x"}
    assert env.ctl.fetch_engine_manifest() == {"manifest": "x"}
    assert calls[0][0] == "d" * 32
    env.keystore.seed = None                                          # key lost from the vault
    with pytest.raises(ApiRejected) as e:
        env.ctl.fetch_engine_manifest()
    assert e.value.code == "not_registered"
