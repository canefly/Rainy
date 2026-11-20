"""Licensing core (no Qt): device key, signed requests, token checks and the unlock state machine.

Talks to rainy-api. The wire format (headers, canonical bytes, token layout, error codes) is
documented in docs/rainy-api-plan.md ("Wire format") and must stay byte-for-byte compatible
with rainy-api/app/security/signing.py. tests/test_license_core.py pins that with fixed vectors.

Security notes
- The device private key lives in the OS secret store (Windows Credential Manager), never in
  the settings DB. Only the signed token and non-secret ids are stored in settings.
- A token is only trusted after its Ed25519 signature checks out against a public key that is
  compiled into the app, and it must name *this* device (id + key fingerprint).
- Dev overrides (API URL, test keys, skipping the gate) are ignored in compiled builds, so a
  shipped app cannot be pointed at a fake server.
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Mapping, Optional, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEFAULT_API_URL = "https://rainy.canefly.xyz"

# kid -> base64url Ed25519 public key. Filled in at release time with the output of
# `python -m app.cli gen-key` from rainy-api. While empty, a release build cannot verify any
# token, so it stays locked (fails closed) instead of silently trusting something.
EMBEDDED_PUBLIC_KEYS: Dict[str, str] = {}

REQUEST_PREFIX = b"RAINY-REQ-1\n"
TOKEN_PREFIX = b"RAINY-TOKEN-1\n"
TOKEN_VERSION = 1

KEYRING_SERVICE = "com.canefly.knot"
KEYRING_USER = "device-key-v1"

IS_COMPILED = "__compiled__" in globals()  # Nuitka defines this in compiled modules


def dev_overrides_allowed() -> bool:
    """True only when running from source. Compiled/frozen builds ignore every dev override."""
    return not IS_COMPILED and not getattr(sys, "frozen", False)


# --------------------------------------------------------------------------- encoding / crypto
_B64U_RE = re.compile(r"[A-Za-z0-9_-]*")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    if not _B64U_RE.fullmatch(s):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_public_key(b64: str) -> Ed25519PublicKey:
    raw = b64u_decode(b64)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return b64u(private_key.public_key().public_bytes_raw())


def public_key_fingerprint(b64: str) -> str:
    return hashlib.sha256(b64u_decode(b64)).hexdigest()[:32]


def canonical_request(method: str, target: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    lines = [method.upper(), target, timestamp, nonce, hashlib.sha256(body).hexdigest()]
    return REQUEST_PREFIX + "\n".join(lines).encode("utf-8")


def sign_request_headers(
    private_key: Ed25519PrivateKey, method: str, target: str, body: bytes = b"",
    *, device_id: Optional[str] = None, now: Optional[float] = None, nonce: Optional[str] = None,
) -> Dict[str, str]:
    ts = str(int(time.time() if now is None else now))
    nonce = nonce or b64u(os.urandom(16))
    sig = private_key.sign(canonical_request(method, target, ts, nonce, body))
    headers = {"X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": b64u(sig)}
    if device_id:
        headers["X-Device-Id"] = device_id
    return headers


class TokenError(Exception):
    """code: malformed, unknown_kid, bad_signature, expired, unsupported, wrong_device, not_approved."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def verify_token(token: str, keys: Mapping[str, Ed25519PublicKey], now: float) -> dict:
    try:
        payload_b64, sig_b64 = token.split(".")
        payload_bytes = b64u_decode(payload_b64)
        sig = b64u_decode(sig_b64)
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        raise TokenError("malformed") from None

    key = keys.get(payload.get("kid"))
    if key is None:
        raise TokenError("unknown_kid")
    try:
        key.verify(sig, TOKEN_PREFIX + payload_bytes)
    except InvalidSignature:
        raise TokenError("bad_signature") from None

    if payload.get("v") != TOKEN_VERSION:
        raise TokenError("unsupported")
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= now:
        raise TokenError("expired")
    return payload


def validate_token(token: str, keys: Mapping[str, Ed25519PublicKey], device_id: str,
                   device_public_b64: str, now: float) -> dict:
    """verify_token plus: it must be for this device and say 'approved'."""
    payload = verify_token(token, keys, now)
    if payload.get("did") != device_id or payload.get("dpk") != public_key_fingerprint(device_public_b64):
        raise TokenError("wrong_device")
    if payload.get("status") != "approved":
        raise TokenError("not_approved")
    return payload


# --------------------------------------------------------------------------- configuration
def api_base_url(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    url = DEFAULT_API_URL
    if dev_overrides_allowed() and env.get("KNOT_API_URL"):
        url = env["KNOT_API_URL"]
    return url.rstrip("/")


def trusted_public_keys(env: Optional[Mapping[str, str]] = None) -> Dict[str, Ed25519PublicKey]:
    """Server public keys by kid. Dev builds may add test keys via KNOT_LICENSE_KEYS=kid:key,..."""
    env = os.environ if env is None else env
    raw: Dict[str, str] = dict(EMBEDDED_PUBLIC_KEYS)
    if dev_overrides_allowed() and env.get("KNOT_LICENSE_KEYS"):
        for part in env["KNOT_LICENSE_KEYS"].split(","):
            kid, _, key = part.strip().partition(":")
            if kid and key:
                raw[kid] = key
    return {kid: load_public_key(b64) for kid, b64 in raw.items()}


def license_gate_disabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Dev/test escape hatch (KNOT_SKIP_LICENSE=1). Never honored in compiled builds."""
    env = os.environ if env is None else env
    return dev_overrides_allowed() and env.get("KNOT_SKIP_LICENSE") == "1"


# --------------------------------------------------------------------------- storage
class SecureStorageError(Exception):
    pass


class KeyStore(Protocol):
    def load_seed(self) -> Optional[str]: ...
    def save_seed(self, seed_b64: str) -> None: ...


class KeyringStore:
    """Device private key in the OS secret store (Windows Credential Manager / macOS Keychain)."""

    def load_seed(self) -> Optional[str]:
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception as e:
            raise SecureStorageError(f"Secure storage unavailable: {e}") from e

    def save_seed(self, seed_b64: str) -> None:
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, seed_b64)
        except Exception as e:
            raise SecureStorageError(f"Could not save the device key: {e}") from e


class MemoryKeyStore:
    def __init__(self, seed: Optional[str] = None):
        self.seed = seed

    def load_seed(self) -> Optional[str]:
        return self.seed

    def save_seed(self, seed_b64: str) -> None:
        self.seed = seed_b64


class SettingsStore(Protocol):
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]: ...
    def set(self, key: str, value: str) -> None: ...


class MemorySettings:
    def __init__(self):
        self.data: Dict[str, str] = {}

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


# --------------------------------------------------------------------------- HTTP client
class ApiUnreachable(Exception):
    """Network failure, timeout, or a server-side 5xx / proxy error page."""


class ApiRejected(Exception):
    def __init__(self, status: int, code: str, message: str = "", extra: Optional[dict] = None):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


class LicenseApi:
    def __init__(self, base_url: str, *, timeout: float = 8.0, app_version: str = "0",
                 opener: Callable = urllib.request.urlopen, clock: Callable[[], float] = time.time):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = f"RainyTools/{app_version} Native"
        self._open = opener
        self._clock = clock

    def _post(self, path: str, body: bytes, headers: Dict[str, str]) -> dict:
        req = urllib.request.Request(
            self.base_url + path, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": self.user_agent, **headers},
        )
        try:
            with self._open(req, timeout=self.timeout) as res:
                raw = res.read(1_000_000)
        except urllib.error.HTTPError as e:
            raw = e.read(1_000_000) if hasattr(e, "read") else b""
            try:
                err = json.loads(raw).get("error", {})
                if not isinstance(err, dict):
                    raise ValueError
            except (ValueError, AttributeError):
                err = None
            if e.code >= 500 and not (err and err.get("code")):
                raise ApiUnreachable(f"server error {e.code}") from None
            if not err:
                err = {"code": f"http_{e.code}", "message": str(e.reason)}
            extra = {k: v for k, v in err.items() if k not in ("code", "message")}
            raise ApiRejected(e.code, str(err.get("code", f"http_{e.code}")), str(err.get("message", "")), extra) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise ApiUnreachable(str(e)) from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiUnreachable("server sent a non-JSON reply") from None
        if not isinstance(data, dict):
            raise ApiUnreachable("server sent an unexpected reply")
        return data

    def register(self, payload: dict, private_key: Ed25519PrivateKey) -> dict:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = sign_request_headers(private_key, "POST", "/v1/devices/register", body, now=self._clock())
        return self._post("/v1/devices/register", body, headers)

    def refresh(self, device_id: str, private_key: Ed25519PrivateKey) -> dict:
        headers = sign_request_headers(private_key, "POST", "/v1/license/refresh", b"",
                                       device_id=device_id, now=self._clock())
        return self._post("/v1/license/refresh", b"", headers)


# --------------------------------------------------------------------------- state machine
class LicenseState(str, Enum):
    CHECKING = "checking"
    NEEDS_REGISTRATION = "needs_registration"
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"
    EXPIRED = "expired"
    UNREACHABLE = "unreachable"
    CLOCK_SKEW = "clock_skew"
    ERROR = "error"


@dataclass(frozen=True)
class LicenseSnapshot:
    state: LicenseState
    editor_name: str = ""
    device_id: str = ""
    message: str = ""
    offline: bool = False             # APPROVED from the cached token, server not confirmed this time
    seconds_left: Optional[int] = None  # time until the cached token expires
    skew_seconds: Optional[int] = None  # server clock minus our clock (CLOCK_SKEW)

    @property
    def unlocked(self) -> bool:
        return self.state is LicenseState.APPROVED

    def time_left_text(self) -> str:
        s = self.seconds_left
        if s is None or s <= 0:
            return ""
        days, hours = s // 86400, (s % 86400) // 3600
        if days:
            return f"{days}d {hours}h"
        return f"{hours}h" if hours else f"{max(1, s // 60)}m"


K_DEVICE = "license.device_id"
K_NAME = "license.editor_name"
K_TOKEN = "license.token"
K_HIGH_WATER = "license.high_water"

_TOKEN_MESSAGES = {
    "unknown_kid": "This build has no license key configured, so it cannot verify licenses.",
    "bad_signature": "The server sent a license that failed verification.",
    "malformed": "The server sent a license that could not be read.",
    "wrong_device": "The server sent a license meant for a different device.",
    "unsupported": "This app is too old to read the server's license. Please update.",
    "not_approved": "The server sent a license that is not approved.",
    "expired": "The server sent an already-expired license. Check your system clock.",
}


def _clean_name(name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name or len(name) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    return name


class LicenseController:
    """Decides whether the app is unlocked. Blocking (does network), so run it off the UI thread."""

    def __init__(self, api: LicenseApi, keystore: KeyStore, store: SettingsStore,
                 keys: Mapping[str, Ed25519PublicKey], *, specs: Callable[[], dict],
                 clock: Callable[[], float] = time.time, app_version: str = "0"):
        self.api = api
        self.keystore = keystore
        self.store = store
        self.keys = keys
        self._specs = specs
        self._clock = clock
        self.app_version = app_version
        self.snapshot = LicenseSnapshot(LicenseState.CHECKING)

    # -- helpers ---------------------------------------------------------------------
    def _now(self) -> float:
        """Trusted 'now': never earlier than the latest time we've seen, so rolling the system
        clock back can't stretch an offline token."""
        real = self._clock()
        try:
            high = float(self.store.get(K_HIGH_WATER) or 0)
        except ValueError:
            high = 0.0
        if real > high + 60:
            self.store.set(K_HIGH_WATER, str(int(real)))
        return max(real, high)

    def _private_key(self, create: bool) -> Optional[Ed25519PrivateKey]:
        seed = self.keystore.load_seed()
        if seed:
            try:
                return Ed25519PrivateKey.from_private_bytes(b64u_decode(seed))
            except ValueError:
                raise SecureStorageError("The stored device key is damaged.") from None
        if not create:
            return None
        key = Ed25519PrivateKey.generate()
        self.keystore.save_seed(b64u(key.private_bytes_raw()))
        return key

    def _set(self, state: LicenseState, **kw) -> LicenseSnapshot:
        name = kw.pop("editor_name", self.store.get(K_NAME) or "")
        device_id = kw.pop("device_id", self.store.get(K_DEVICE) or "")
        self.snapshot = LicenseSnapshot(state, editor_name=name, device_id=device_id, **kw)
        return self.snapshot

    def _clear_token(self) -> None:
        self.store.set(K_TOKEN, "")

    def _cached_token(self, priv: Ed25519PrivateKey) -> tuple[Optional[dict], Optional[TokenError]]:
        token = self.store.get(K_TOKEN) or ""
        if not token:
            return None, None
        try:
            payload = validate_token(token, self.keys, self.store.get(K_DEVICE) or "",
                                     public_key_b64(priv), self._now())
            return payload, None
        except TokenError as e:
            return None, e

    def _approved_offline(self, payload: dict, message: str = "") -> LicenseSnapshot:
        return self._set(LicenseState.APPROVED, offline=True, message=message,
                         seconds_left=int(payload["exp"] - self._now()))

    def _no_server(self, priv: Ed25519PrivateKey, message: str) -> LicenseSnapshot:
        payload, err = self._cached_token(priv)
        if payload:
            return self._approved_offline(payload, message)
        if err and err.code == "expired":
            return self._set(LicenseState.EXPIRED, message="Your offline license ran out. Connect to the internet to renew it.")
        return self._set(LicenseState.UNREACHABLE, message=message)

    # -- public operations -----------------------------------------------------------
    def load_local(self) -> LicenseSnapshot:
        """No network: what can we tell from what's stored on this machine?"""
        if not self.store.get(K_DEVICE):
            return self._set(LicenseState.NEEDS_REGISTRATION,
                             editor_name=self.store.get(K_NAME) or self.store.get("editorName") or "")
        try:
            priv = self._private_key(create=False)
        except SecureStorageError as e:
            return self._set(LicenseState.ERROR, message=str(e))
        if priv is None:
            self.store.set(K_DEVICE, "")
            self._clear_token()
            return self._set(LicenseState.NEEDS_REGISTRATION, device_id="",
                             message="This device's key is missing, so it needs to register again.")
        payload, _ = self._cached_token(priv)
        if payload:
            return self._approved_offline(payload)
        return self._set(LicenseState.CHECKING, message="Checking your license...")

    def register(self, editor_name: str) -> LicenseSnapshot:
        name = _clean_name(editor_name)
        if name is None:
            return self._set(LicenseState.NEEDS_REGISTRATION, editor_name=(editor_name or "").strip(),
                             message="Enter a name between 1 and 100 characters.")
        try:
            priv = self._private_key(create=True)
        except SecureStorageError as e:
            return self._set(LicenseState.ERROR, editor_name=name, message=str(e))

        specs = self._specs()
        payload = {"public_key": public_key_b64(priv), "hwid": specs.get("hwid") or "UNKNOWN_HWID",
                   "editor_name": name, "app_version": self.app_version}
        for k in ("cpu", "gpu", "ram_gb", "os"):
            if specs.get(k) not in (None, "", 0):
                payload[k] = specs[k]

        try:
            resp = self.api.register(payload, priv)
        except ApiUnreachable:
            return self._set(LicenseState.NEEDS_REGISTRATION, editor_name=name,
                             message="Can't reach the server. Check your connection and try again.")
        except ApiRejected as e:
            return self._registration_rejected(e, name)

        device_id = str(resp.get("device_id") or "")
        status = resp.get("status")
        if not device_id or status not in ("pending", "approved", "revoked"):
            return self._set(LicenseState.ERROR, editor_name=name, message="The server sent an unexpected reply.")
        self.store.set(K_DEVICE, device_id)
        self.store.set(K_NAME, name)
        self._clear_token()
        if status == "pending":
            return self._set(LicenseState.PENDING, editor_name=name, device_id=device_id)
        if status == "revoked":
            return self._set(LicenseState.REVOKED, editor_name=name, device_id=device_id)
        return self.refresh()  # already approved (e.g. reinstall with the same key): fetch the token

    def _registration_rejected(self, e: ApiRejected, name: str) -> LicenseSnapshot:
        if e.code == "clock_skew":
            return self._skew(e)
        messages = {
            "rate_limited": "Too many attempts. Wait a bit and try again.",
            "registration_closed": "Registration is closed right now. Contact Cane.",
            "invalid_request": "The server didn't accept those details. Try a different name.",
            "body_too_large": "The server didn't accept those details. Try a shorter name.",
        }
        return self._set(LicenseState.NEEDS_REGISTRATION, editor_name=name,
                         message=messages.get(e.code, "The server refused the request. Try again later."))

    def _skew(self, e: ApiRejected) -> LicenseSnapshot:
        server_time = e.extra.get("server_time")
        skew = int(server_time - self._clock()) if isinstance(server_time, (int, float)) else None
        return self._set(LicenseState.CLOCK_SKEW, skew_seconds=skew,
                         message="Your system clock doesn't match the server's.")

    def refresh(self) -> LicenseSnapshot:
        device_id = self.store.get(K_DEVICE) or ""
        if not device_id:
            return self.load_local()
        try:
            priv = self._private_key(create=False)
        except SecureStorageError as e:
            return self._set(LicenseState.ERROR, message=str(e))
        if priv is None:
            return self.load_local()

        try:
            resp = self.api.refresh(device_id, priv)
        except ApiUnreachable:
            return self._no_server(priv, "Offline: couldn't reach the server.")
        except ApiRejected as e:
            if e.code == "clock_skew":
                payload, _ = self._cached_token(priv)
                return self._approved_offline(payload, "Your system clock is off.") if payload else self._skew(e)
            if e.code == "auth_failed":
                self.store.set(K_DEVICE, "")
                self._clear_token()
                return self._set(LicenseState.NEEDS_REGISTRATION, device_id="",
                                 message="The server doesn't recognise this install any more. Please register again.")
            return self._no_server(priv, "The server is busy. Trying again shortly." if e.code == "rate_limited"
                                   else "The server refused the request. Trying again shortly.")

        status = resp.get("status")
        if status == "approved":
            token = resp.get("token")
            try:
                payload = validate_token(token or "", self.keys, device_id, public_key_b64(priv), self._now())
            except TokenError as te:
                self._clear_token()
                return self._set(LicenseState.ERROR, message=_TOKEN_MESSAGES.get(te.code, "Invalid license."))
            self.store.set(K_TOKEN, token)
            return self._set(LicenseState.APPROVED, offline=False, seconds_left=int(payload["exp"] - self._now()))
        if status == "pending":
            self._clear_token()
            return self._set(LicenseState.PENDING)
        if status == "revoked":
            self._clear_token()
            return self._set(LicenseState.REVOKED)
        return self._set(LicenseState.ERROR, message="The server sent an unexpected reply.")
