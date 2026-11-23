"""Which certificate authorities Rainy trusts when it talks HTTPS.

Python only trusts the root certificates already present in the Windows store. Windows downloads a missing root on
demand, but only when a Windows program (a browser, PowerShell) asks it to, and a fresh Windows (a new PC, a Windows
Sandbox, an offline-installed machine) can lack the roots behind Cloudflare, Google Trust Services or GitHub. Browsers
work there and Rainy said "can't reach the server" and could not download its engines.

So the app trusts the Windows roots AND a bundled list of public roots (certifi). A certificate is only accepted if it
chains to one of them: this adds known public roots, it never turns verification off.
"""
import socket
import ssl
import threading
import urllib.request
from typing import Optional

from .logger import get_logger

logger = get_logger("net_tls")

_lock = threading.Lock()
_context: Optional[ssl.SSLContext] = None


def bundled_cafile() -> Optional[str]:
    """Path of the bundled public-roots file, or None if it isn't available."""
    try:
        import certifi
        return certifi.where()
    except Exception as e:  # noqa: BLE001
        logger.warning("The bundled certificate list is not available: %s", e)
        return None


def build_context(cafile: Optional[str] = None) -> ssl.SSLContext:
    """A verifying context that trusts the system's roots plus `cafile` (default: the bundled list)."""
    ctx = ssl.create_default_context()
    path = cafile if cafile is not None else bundled_cafile()
    if path:
        try:
            ctx.load_verify_locations(cafile=path)
        except (OSError, ssl.SSLError) as e:
            logger.warning("Could not load %s: %s", path, e)
    return ctx


def ssl_context() -> ssl.SSLContext:
    global _context
    with _lock:
        if _context is None:
            _context = build_context()
        return _context


def open_url(req, timeout: float = 30.0):
    """urllib's urlopen with the context above. Use this instead of urllib.request.urlopen for HTTPS."""
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def failure_kind(exc: BaseException) -> str:
    """A short label for why a connection failed, for the log and for a message that says something useful:
    'certificate', 'dns', 'timeout', 'refused', or 'other'."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(exc, ssl.SSLCertVerificationError):
        return "certificate"
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(reason, ConnectionRefusedError) or isinstance(exc, ConnectionRefusedError):
        return "refused"
    return "other"


def describe_failure(exc: BaseException) -> str:
    """The exception as one line for the log: kind, class and message."""
    return f"{failure_kind(exc)}: {type(getattr(exc, 'reason', exc)).__name__}: {exc}"

