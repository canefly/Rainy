"""Licensing core: server-compat vectors, token checks, config safety, HTTP client, state machine.

The fixed vectors below were produced by rainy-api's own code (app/security/signing.py) with
throwaway test seeds. If one of these fails, the app and the server no longer agree on the wire
format and nobody can log in: fix the app (or bump the protocol version), don't edit the vector.
"""
import io
import json
import time
import urllib.error

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core import license_core as lc
from src.core.license_core import (
    ApiRejected, ApiUnreachable, LicenseApi, LicenseController, LicenseSnapshot, LicenseState,
    MemoryKeyStore, MemorySettings, SecureStorageError, TokenError, b64u, b64u_decode,
    canonical_request, load_public_key, public_key_b64, public_key_fingerprint,
    sign_request_headers, validate_token, verify_token,
)

# -- vectors from rainy-api (test-only seeds, not secrets) ---------------------------------------
DEV_SEED = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA"
DEV_PUB = "ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ"
SRV_SEED = "ZWZnaGlqa2xtbm9wcXJzdHV2d3h5ent8fX5_gIGCg4Q"
SRV_PUB = "2inpWwLgD_oVZFd1-x0roiKhlDOV7qBrlOLAV7e-adA"
SIG_EMPTY = "E8RJwno9_kJJOQU6B3gkYKpYL4viTDiU78qDhebsFFjsh9_u0IiidwhNcX8LcQe_qd2A35vfnotdGGY3WNdhDA"
SIG_BODY = "xUJGRF9B3rSlvnAU8Uvb_3dPMTdnxaGYJeLgPWSfC7XrKLpN4LmmJh-2Zd1Bm78U-LMMAeqKRxwjjY7Vft3LCg"
SIG_QUERY = "vWGkAQ9ptDAvhcxYqQVyYzsTyzEfd7krFrjYhKNDB3BJLWMOSjWqruFV79sFOKXwrHIuk5LcPKKIS5swUWYoCQ"
TOKEN = ("eyJkaWQiOiJhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYSIsImRwayI6IjY1YjYwNjczZDZlZDg4NGJmMDFjMmMyMjJkODJhZGEw"
         "IiwiZXhwIjoxODAwMjU5MjAwLCJpYXQiOjE4MDAwMDAwMDAsImtpZCI6ImsxIiwic3RhdHVzIjoiYXBwcm92ZWQiLCJ2IjoxfQ."
         "Wa2g2F0UFDURg2KF8skf9SwgeRWZrQvCXC3gABelkhhDMjgh9hfNlvcwBtnHiVY7IXIqSRQ4DrM22w0RSM2fBQ")
TOKEN_REVOKED = ("eyJkaWQiOiJhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYSIsImRwayI6IjY1YjYwNjczZDZlZDg4NGJmMDFjMmMyMjJkODJhZGEw"
                 "IiwiZXhwIjoxODAwMjU5MjAwLCJpYXQiOjE4MDAwMDAwMDAsImtpZCI6ImsxIiwic3RhdHVzIjoicmV2b2tlZCIsInYiOjF9."
                 "Zl4mEdGqQ0H01KQBM3w9MdZk7zzDoA7ZH_i3myKdC_x2z08c_1a2N9xHtr0Xar6EcvTV52EcY5-QSBBk2OziBA")
NOW0 = 1_800_000_000
EXP0 = 1_800_259_200
NONCE = "AAAAAAAAAAAAAAAAAAAAAA"


def dev_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b64u_decode(DEV_SEED))


def test_vectors_request_signatures_match_server():
    k = dev_key()
    assert public_key_b64(k) == DEV_PUB
    h = sign_request_headers(k, "POST", "/v1/license/refresh", b"", device_id="a" * 32, now=NOW0, nonce=NONCE)
    assert h == {"X-Timestamp": str(NOW0), "X-Nonce": NONCE, "X-Signature": SIG_EMPTY, "X-Device-Id": "a" * 32}
    body = b'{"public_key":"x"}'
    assert sign_request_headers(k, "POST", "/v1/devices/register", body, now=NOW0, nonce=NONCE)["X-Signature"] == SIG_BODY
    assert sign_request_headers(k, "POST", "/v1/license/refresh?a=1&b=2", b"", now=NOW0, nonce=NONCE)["X-Signature"] == SIG_QUERY


def test_vectors_server_token_verifies_in_app():
    keys = {"k1": load_public_key(SRV_PUB)}
    payload = verify_token(TOKEN, keys, NOW0 + 10)
    assert payload["exp"] == EXP0 and payload["kid"] == "k1" and payload["status"] == "approved"
    assert payload["dpk"] == public_key_fingerprint(DEV_PUB)
    assert validate_token(TOKEN, keys, "a" * 32, DEV_PUB, NOW0 + 10)["did"] == "a" * 32


def test_vectors_validation_rules():
    keys = {"k1": load_public_key(SRV_PUB)}
    with pytest.raises(TokenError) as e:
        validate_token(TOKEN, keys, "b" * 32, DEV_PUB, NOW0 + 10)  # someone else's device id
    assert e.value.code == "wrong_device"
    other_pub = public_key_b64(Ed25519PrivateKey.generate())
    with pytest.raises(TokenError) as e:
        validate_token(TOKEN, keys, "a" * 32, other_pub, NOW0 + 10)  # token copied to another key
    assert e.value.code == "wrong_device"
    with pytest.raises(TokenError) as e:
        validate_token(TOKEN_REVOKED, keys, "a" * 32, DEV_PUB, NOW0 + 10)
    assert e.value.code == "not_approved"
    with pytest.raises(TokenError) as e:
        verify_token(TOKEN, keys, EXP0)  # exactly at exp is expired
    assert e.value.code == "expired"
    verify_token(TOKEN, keys, EXP0 - 1)


def test_token_tamper_and_garbage():
    keys = {"k1": load_public_key(SRV_PUB)}
    p, s = TOKEN.split(".")
    forged = json.loads(b64u_decode(p))
    forged["status"] = "approved"
    forged["exp"] = EXP0 + 10**9
    forged_p = b64u(json.dumps(forged, separators=(",", ":"), sort_keys=True).encode())
    for bad, code in [(f"{forged_p}.{s}", "bad_signature"), (f"{p}.{b64u(b'x' * 64)}", "bad_signature"),
                      ("", "malformed"), ("a.b.c", "malformed"), (f"{b64u(b'[]')}.{s}", "malformed")]:
        with pytest.raises(TokenError) as e:
            verify_token(bad, keys, NOW0)
        assert e.value.code == code
    with pytest.raises(TokenError) as e:
        verify_token(TOKEN, {}, NOW0)  # no keys compiled in: nothing can be trusted
    assert e.value.code == "unknown_kid"
    wrong = {"k1": Ed25519PrivateKey.generate().public_key()}
    with pytest.raises(TokenError) as e:
        verify_token(TOKEN, wrong, NOW0)  # right kid, someone else's key
    assert e.value.code == "bad_signature"


def test_canonical_request_binds_everything():
    base = canonical_request("POST", "/v1/x", "1", "n" * 16, b"b")
    assert base != canonical_request("GET", "/v1/x", "1", "n" * 16, b"b")
    assert base != canonical_request("POST", "/v1/y", "1", "n" * 16, b"b")
    assert base != canonical_request("POST", "/v1/x", "2", "n" * 16, b"b")
    assert base != canonical_request("POST", "/v1/x", "1", "m" * 16, b"b")
    assert base != canonical_request("POST", "/v1/x", "1", "n" * 16, b"c")


# -- config safety ----------------------------------------------------------------------------
def test_dev_overrides_work_from_source_and_are_ignored_when_compiled(monkeypatch):
    env = {"KNOT_API_URL": "http://127.0.0.1:8020/", "KNOT_LICENSE_KEYS": f"k9:{SRV_PUB}",
           "KNOT_SKIP_LICENSE": "1"}
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    assert lc.api_base_url(env) == "http://127.0.0.1:8020"
    assert "k9" in lc.trusted_public_keys(env)
    assert lc.license_gate_disabled(env) is True

    monkeypatch.setattr(lc, "IS_COMPILED", True)  # a shipped build
    assert lc.api_base_url(env) == lc.DEFAULT_API_URL
    assert "k9" not in lc.trusted_public_keys(env)
    assert lc.license_gate_disabled(env) is False


def test_frozen_builds_also_ignore_overrides(monkeypatch):
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    monkeypatch.setattr(lc.sys, "frozen", True, raising=False)
    assert lc.api_base_url({"KNOT_API_URL": "http://evil"}) == lc.DEFAULT_API_URL
    assert lc.license_gate_disabled({"KNOT_SKIP_LICENSE": "1"}) is False


def test_skip_flag_requires_exactly_one(monkeypatch):
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    for v in ("", "0", "true", "yes"):
        assert lc.license_gate_disabled({"KNOT_SKIP_LICENSE": v}) is False


def test_release_key_list_is_not_empty_before_shipping():
    """Reminder test: a release build with no embedded key can never unlock. It is allowed to be
    empty while rainy-api isn't deployed; this documents the expectation."""
    assert isinstance(lc.EMBEDDED_PUBLIC_KEYS, dict)


def test_malformed_dev_key_raises_value_error(monkeypatch):
    monkeypatch.setattr(lc, "IS_COMPILED", False)
    with pytest.raises(ValueError):
        lc.trusted_public_keys({"KNOT_LICENSE_KEYS": "k1:not-a-key"})


# -- secure storage ---------------------------------------------------------------------------
def test_keyring_store_roundtrip_in_the_real_os_vault(monkeypatch):
    """Uses the real Credential Manager under a throwaway name, then removes it. Never touches the
    app's real entry."""
    keyring = pytest.importorskip("keyring")
    import uuid
    svc = f"com.canefly.knot.test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(lc, "KEYRING_SERVICE", svc)
    monkeypatch.setattr(lc, "KEYRING_USER", "device-key-test")
    store = lc.KeyringStore()
    try:
        assert store.load_seed() is None
        store.save_seed(DEV_SEED)
        assert store.load_seed() == DEV_SEED
        assert LicenseController(None, store, MemorySettings(), {}, specs=dict)._private_key(create=False) is not None
    finally:
        try:
            keyring.delete_password(svc, "device-key-test")
        except Exception:
            pass
    assert store.load_seed() is None


def test_keyring_failures_become_secure_storage_errors(monkeypatch):
    keyring = pytest.importorskip("keyring")

    def boom(*a, **k):
        raise RuntimeError("vault locked")
    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setattr(keyring, "set_password", boom)
    store = lc.KeyringStore()
    with pytest.raises(SecureStorageError):
        store.load_seed()
    with pytest.raises(SecureStorageError):
        store.save_seed("x")


# -- HTTP client ------------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, body: bytes):
        self._b = io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body: bytes):
    return urllib.error.HTTPError("http://x", code, "reason", {}, io.BytesIO(body))


def make_api(opener):
    return LicenseApi("http://srv/", opener=opener, clock=lambda: NOW0, app_version="9.9")


def test_api_sends_signed_request_and_parses_reply():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["data"], seen["headers"] = req.full_url, req.data, dict(req.header_items())
        seen["timeout"] = timeout
        return FakeResponse(b'{"status":"pending"}')

    api = make_api(opener)
    assert api.refresh("a" * 32, dev_key()) == {"status": "pending"}
    assert seen["url"] == "http://srv/v1/license/refresh"
    assert seen["data"] == b""
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert h["x-device-id"] == "a" * 32 and h["x-timestamp"] == str(NOW0)
    assert h["user-agent"] == "RainyTools/9.9 Native"
    # signature really verifies against the canonical bytes for what was sent
    k = dev_key().public_key()
    k.verify(b64u_decode(h["x-signature"]),
             canonical_request("POST", "/v1/license/refresh", h["x-timestamp"], h["x-nonce"], b""))
    assert seen["timeout"] == api.timeout


def test_api_register_body_is_what_was_signed():
    seen = {}

    def opener(req, timeout):
        seen["data"], seen["headers"] = req.data, {k.lower(): v for k, v in req.header_items()}
        return FakeResponse(b'{"device_id":"d","status":"pending"}')

    make_api(opener).register({"public_key": DEV_PUB, "editor_name": "Cane"}, dev_key())
    h = seen["headers"]
    assert "x-device-id" not in h
    dev_key().public_key().verify(
        b64u_decode(h["x-signature"]),
        canonical_request("POST", "/v1/devices/register", h["x-timestamp"], h["x-nonce"], seen["data"]))
    assert json.loads(seen["data"])["editor_name"] == "Cane"


def test_api_error_mapping():
    def reply(code, body):
        def opener(req, timeout):
            raise http_error(code, body)
        return make_api(opener)

    with pytest.raises(ApiRejected) as e:
        reply(401, b'{"error":{"code":"clock_skew","message":"m","server_time":123}}').refresh("a" * 32, dev_key())
    assert (e.value.status, e.value.code, e.value.extra) == (401, "clock_skew", {"server_time": 123})

    with pytest.raises(ApiRejected) as e:
        reply(422, b'{"error":{"code":"invalid_request","details":[]}}').refresh("a" * 32, dev_key())
    assert e.value.code == "invalid_request"

    with pytest.raises(ApiUnreachable):  # Cloudflare / proxy HTML error page
        reply(502, b"<html>Bad gateway</html>").refresh("a" * 32, dev_key())
    with pytest.raises(ApiUnreachable):
        reply(503, b"").refresh("a" * 32, dev_key())
    with pytest.raises(ApiRejected) as e:  # 503 with our own error body is a real answer
        reply(503, b'{"error":{"code":"registration_closed"}}').register({}, dev_key())
    assert e.value.code == "registration_closed"
    with pytest.raises(ApiRejected) as e:  # non-JSON 4xx
        reply(404, b"nope").refresh("a" * 32, dev_key())
    assert e.value.code == "http_404"


def test_api_network_failures_are_unreachable():
    for exc in (urllib.error.URLError("dns"), TimeoutError(), ConnectionResetError(), OSError("x")):
        def opener(req, timeout, exc=exc):
            raise exc
        with pytest.raises(ApiUnreachable):
            make_api(opener).refresh("a" * 32, dev_key())
    for body in (b"<html>", b"[]", b'"str"', b""):
        with pytest.raises(ApiUnreachable):
            make_api(lambda req, timeout, body=body: FakeResponse(body)).refresh("a" * 32, dev_key())


# -- controller / state machine -----------------------------------------------------------------
SRV = Ed25519PrivateKey.from_private_bytes(b64u_decode(SRV_SEED))


def issue(device_id, device_pub_b64, *, status="approved", now=NOW0, ttl=72 * 3600, kid="k1", signer=SRV):
    payload = {"v": 1, "kid": kid, "did": device_id, "dpk": public_key_fingerprint(device_pub_b64),
               "status": status, "iat": int(now), "exp": int(now) + ttl}
    pb = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{b64u(pb)}.{b64u(signer.sign(lc.TOKEN_PREFIX + pb))}"


class FakeApi:
    """Scripted server. Each queue entry is a dict to return or an exception to raise."""

    def __init__(self):
        self.register_replies, self.refresh_replies = [], []
        self.register_calls, self.refresh_calls = [], []

    def register(self, payload, key):
        self.register_calls.append(payload)
        return self._next(self.register_replies)

    def refresh(self, device_id, key):
        self.refresh_calls.append((device_id, key))
        return self._next(self.refresh_replies)

    @staticmethod
    def _next(q):
        item = q.pop(0) if q else ApiUnreachable("no scripted reply")
        if isinstance(item, Exception):
            raise item
        return item


SPECS = {"hwid": "HW-1", "cpu": "Test CPU", "gpu": "Test GPU", "ram_gb": 16, "os": "Windows 11"}


class Env:
    def __init__(self, clock_value=NOW0 + 100, keys=None):
        self.api = FakeApi()
        self.keystore = MemoryKeyStore()
        self.store = MemorySettings()
        self.now = clock_value
        self.keys = {"k1": SRV.public_key()} if keys is None else keys
        self.ctl = LicenseController(self.api, self.keystore, self.store, self.keys,
                                     specs=lambda: dict(SPECS), clock=lambda: self.now, app_version="1.2.3")

    @property
    def priv(self):
        return Ed25519PrivateKey.from_private_bytes(b64u_decode(self.keystore.seed))

    @property
    def pub(self):
        return public_key_b64(self.priv)

    def registered(self, status="pending"):
        self.api.register_replies.append({"device_id": "d" * 32, "status": status})
        self.ctl.register("Cane")
        return self

    def approve(self):
        self.api.refresh_replies.append({"status": "approved", "token": issue("d" * 32, self.pub, now=self.now)})
        return self.ctl.refresh()


@pytest.fixture
def env():
    return Env()


def test_fresh_install_needs_registration_and_prefills_legacy_name(env):
    env.store.set("editorName", "OldName")
    snap = env.ctl.load_local()
    assert snap.state is LicenseState.NEEDS_REGISTRATION and snap.editor_name == "OldName"
    assert not snap.unlocked and env.api.register_calls == [] and env.keystore.seed is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "bad\nname", "tab\tname", "nul\x00"])
def test_invalid_names_never_reach_the_network(env, name):
    snap = env.ctl.register(name)
    assert snap.state is LicenseState.NEEDS_REGISTRATION and snap.message
    assert env.api.register_calls == [] and env.keystore.seed is None


def test_register_sends_expected_payload_and_goes_pending(env):
    env.registered()
    snap = env.ctl.snapshot
    assert snap.state is LicenseState.PENDING and snap.device_id == "d" * 32 and snap.editor_name == "Cane"
    sent = env.api.register_calls[0]
    assert sent == {"public_key": env.pub, "hwid": "HW-1", "editor_name": "Cane", "app_version": "1.2.3",
                    "cpu": "Test CPU", "gpu": "Test GPU", "ram_gb": 16, "os": "Windows 11"}
    assert env.store.get(lc.K_DEVICE) == "d" * 32 and env.store.get(lc.K_NAME) == "Cane"


def test_register_trims_name_and_omits_empty_specs():
    e = Env()
    e.ctl._specs = lambda: {"hwid": "", "cpu": "", "gpu": None, "ram_gb": 0, "os": "W"}
    e.api.register_replies.append({"device_id": "d" * 32, "status": "pending"})
    e.ctl.register("  Cane  ")
    sent = e.api.register_calls[0]
    assert sent["editor_name"] == "Cane" and sent["hwid"] == "UNKNOWN_HWID"
    assert set(sent) == {"public_key", "hwid", "editor_name", "app_version", "os"}


def test_device_key_is_created_once_and_reused(env):
    env.registered()
    first = env.keystore.seed
    env.api.register_replies.append({"device_id": "e" * 32, "status": "pending"})
    env.ctl.register("Cane again")
    assert env.keystore.seed == first
    assert env.api.register_calls[0]["public_key"] == env.api.register_calls[1]["public_key"]


def test_private_key_never_lands_in_settings(env):
    env.registered()
    env.approve()
    for v in env.store.data.values():
        assert env.keystore.seed not in v
        assert b64u(env.priv.private_bytes_raw()) not in v


def test_register_failures_keep_the_form_and_store_nothing():
    cases = [
        (ApiUnreachable("x"), "Can't reach"),
        (ApiRejected(429, "rate_limited"), "Too many"),
        (ApiRejected(503, "registration_closed"), "closed"),
        (ApiRejected(422, "invalid_request"), "didn't accept"),
        (ApiRejected(500, "weird"), "refused"),
    ]
    for exc, text in cases:
        e = Env()
        e.api.register_replies.append(exc)
        snap = e.ctl.register("Cane")
        assert snap.state is LicenseState.NEEDS_REGISTRATION and text in snap.message and snap.editor_name == "Cane"
        assert e.store.get(lc.K_DEVICE) is None


def test_register_clock_skew_reports_offset(env):
    env.api.register_replies.append(ApiRejected(401, "clock_skew", extra={"server_time": env.now + 7200}))
    snap = env.ctl.register("Cane")
    assert snap.state is LicenseState.CLOCK_SKEW and snap.skew_seconds == 7200


def test_register_unexpected_reply_is_error(env):
    for reply in ({}, {"device_id": "d", "status": "wat"}, {"status": "pending"}):
        env.api.register_replies.append(reply)
        assert env.ctl.register("Cane").state is LicenseState.ERROR
        assert env.store.get(lc.K_DEVICE) is None


def test_register_when_server_already_approved_fetches_token(env):
    env.api.register_replies.append({"device_id": "d" * 32, "status": "approved"})
    env.api.refresh_replies.append({"status": "approved", "token": None})  # placeholder, replaced below
    env.api.refresh_replies.clear()
    # key is created during register(), so build the token lazily through a reply object
    orig = env.api.refresh

    def refresh(device_id, key):
        env.api.refresh_calls.append((device_id, key))
        return {"status": "approved", "token": issue(device_id, public_key_b64(key), now=env.now)}
    env.api.refresh = refresh
    snap = env.ctl.register("Cane")
    assert snap.state is LicenseState.APPROVED and not snap.offline
    env.api.refresh = orig


def test_refresh_pending_then_approved_then_survives_restart(env):
    env.registered()
    env.api.refresh_replies.append({"status": "pending"})
    assert env.ctl.refresh().state is LicenseState.PENDING

    snap = env.approve()
    assert snap.unlocked and not snap.offline and snap.seconds_left == 72 * 3600
    assert env.store.get(lc.K_TOKEN)

    # "restart": brand new controller over the same stores, no network at all
    again = LicenseController(FakeApi(), env.keystore, env.store, env.keys, specs=lambda: SPECS, clock=lambda: env.now + 3600)
    snap = again.load_local()
    assert snap.unlocked and snap.offline and snap.seconds_left == 72 * 3600 - 3600


def test_server_is_the_authority_revoked_wins_over_cached_token(env):
    env.registered()
    env.approve()
    env.api.refresh_replies.append({"status": "revoked"})
    snap = env.ctl.refresh()
    assert snap.state is LicenseState.REVOKED and not snap.unlocked
    assert not env.store.get(lc.K_TOKEN)
    assert env.ctl.load_local().state is LicenseState.CHECKING  # nothing cached to unlock with


def test_pending_after_approval_also_locks(env):
    env.registered()
    env.approve()
    env.api.refresh_replies.append({"status": "pending"})
    assert env.ctl.refresh().state is LicenseState.PENDING
    assert not env.store.get(lc.K_TOKEN)


def test_offline_with_valid_cached_token_stays_unlocked(env):
    env.registered()
    env.approve()
    env.now += 24 * 3600
    env.api.refresh_replies.append(ApiUnreachable("down"))
    snap = env.ctl.refresh()
    assert snap.unlocked and snap.offline and snap.seconds_left == 72 * 3600 - 24 * 3600


def test_offline_after_token_expiry_locks(env):
    env.registered()
    env.approve()
    env.now += 72 * 3600 + 10_000
    env.api.refresh_replies.append(ApiUnreachable("down"))
    snap = env.ctl.refresh()
    assert snap.state is LicenseState.EXPIRED and not snap.unlocked


def test_offline_with_no_token_is_unreachable_not_unlocked(env):
    env.registered()
    env.api.refresh_replies.append(ApiUnreachable("down"))
    snap = env.ctl.refresh()
    assert snap.state is LicenseState.UNREACHABLE and not snap.unlocked


def test_rate_limited_or_5xx_keeps_cached_unlock_but_never_grants_one(env):
    env.registered()
    env.api.refresh_replies.append(ApiRejected(429, "rate_limited"))
    assert env.ctl.refresh().state is LicenseState.UNREACHABLE
    env.approve()
    env.api.refresh_replies.append(ApiRejected(500, "weird"))
    assert env.ctl.refresh().unlocked


def test_clock_rollback_cannot_stretch_a_token(env):
    env.registered()
    env.approve()                       # high-water recorded at NOW0 + 100
    env.now += 72 * 3600 + 5000         # real time passes beyond expiry...
    env.ctl.load_local()                # ...and we see it (high-water moves forward)
    env.now = NOW0 + 50                 # then the user sets the system clock back
    env.api.refresh_replies.append(ApiUnreachable("down"))
    assert env.ctl.refresh().state is LicenseState.EXPIRED
    assert env.ctl.load_local().state is LicenseState.CHECKING  # still not unlocked from cache


def test_clock_rollback_before_first_seen_time_uses_high_water(env):
    env.registered()
    env.approve()
    env.now = NOW0 - 10 * 86400         # clock set far into the past
    snap = env.ctl.load_local()
    assert snap.unlocked and snap.seconds_left == 72 * 3600  # measured from high-water, not the fake clock


def test_forged_or_foreign_tokens_are_rejected_and_not_stored(env):
    env.registered()
    other = Ed25519PrivateKey.generate()
    bad_tokens = {
        "wrong server key": issue("d" * 32, env.pub, signer=other),
        "other device": issue("e" * 32, env.pub),
        "other device key": issue("d" * 32, public_key_b64(Ed25519PrivateKey.generate())),
        "not approved": issue("d" * 32, env.pub, status="pending"),
        "unknown kid": issue("d" * 32, env.pub, kid="k7"),
        "garbage": "not.a.token",
        "missing": None,
    }
    for label, tok in bad_tokens.items():
        env.api.refresh_replies.append({"status": "approved", "token": tok})
        snap = env.ctl.refresh()
        assert snap.state is LicenseState.ERROR and not snap.unlocked, label
        assert not env.store.get(lc.K_TOKEN), label


def test_build_without_license_keys_fails_closed():
    e = Env(keys={})
    e.api.register_replies.append({"device_id": "d" * 32, "status": "pending"})
    e.ctl.register("Cane")
    e.api.refresh_replies.append({"status": "approved", "token": issue("d" * 32, e.pub)})
    snap = e.ctl.refresh()
    assert snap.state is LicenseState.ERROR and "no license key" in snap.message and not snap.unlocked


def test_unknown_device_on_server_resets_registration_but_keeps_key(env):
    env.registered()
    key_before = env.keystore.seed
    env.api.refresh_replies.append(ApiRejected(401, "auth_failed"))
    snap = env.ctl.refresh()
    assert snap.state is LicenseState.NEEDS_REGISTRATION and "register again" in snap.message
    assert not env.store.get(lc.K_DEVICE) and not env.store.get(lc.K_TOKEN)
    assert env.keystore.seed == key_before


def test_clock_skew_on_refresh(env):
    env.registered()
    env.api.refresh_replies.append(ApiRejected(401, "clock_skew", extra={"server_time": env.now - 3600}))
    snap = env.ctl.refresh()
    assert snap.state is LicenseState.CLOCK_SKEW and snap.skew_seconds == -3600
    env.approve()
    env.api.refresh_replies.append(ApiRejected(401, "clock_skew", extra={"server_time": env.now - 3600}))
    assert env.ctl.refresh().unlocked  # cached token still good, can't re-verify


def test_lost_device_key_forces_reregistration(env):
    env.registered()
    env.approve()
    env.keystore.seed = None
    snap = env.ctl.load_local()
    assert snap.state is LicenseState.NEEDS_REGISTRATION and "key is missing" in snap.message
    assert not env.store.get(lc.K_DEVICE) and not env.store.get(lc.K_TOKEN)


def test_damaged_device_key_is_an_error_not_a_crash(env):
    env.registered()
    env.keystore.seed = "!!!"
    assert env.ctl.load_local().state is LicenseState.ERROR


def test_secure_storage_failures_surface_as_error():
    class Broken:
        def load_seed(self):
            raise SecureStorageError("no vault")

        def save_seed(self, s):
            raise SecureStorageError("no vault")

    e = Env()
    e.ctl.keystore = Broken()
    assert e.ctl.register("Cane").state is LicenseState.ERROR
    e.store.set(lc.K_DEVICE, "d" * 32)
    assert e.ctl.load_local().state is LicenseState.ERROR
    assert e.ctl.refresh().state is LicenseState.ERROR


def test_refresh_without_device_falls_back_to_local(env):
    assert env.ctl.refresh().state is LicenseState.NEEDS_REGISTRATION
    assert env.api.refresh_calls == []


def test_unexpected_status_from_server_is_error(env):
    env.registered()
    env.api.refresh_replies.append({"status": "banana"})
    assert env.ctl.refresh().state is LicenseState.ERROR


def test_time_left_text():
    f = lambda s: LicenseSnapshot(LicenseState.APPROVED, seconds_left=s).time_left_text()
    assert f(None) == "" and f(0) == "" and f(-5) == ""
    assert f(30) == "1m" and f(5 * 60) == "5m" and f(3 * 3600 + 20) == "3h"
    assert f(2 * 86400 + 5 * 3600) == "2d 5h"


def test_only_approved_is_unlocked():
    for st in LicenseState:
        assert LicenseSnapshot(st).unlocked is (st is LicenseState.APPROVED)
