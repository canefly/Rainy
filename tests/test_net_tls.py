"""HTTPS trust: the app trusts Windows' roots AND a bundled list, never less strictly than before."""
import datetime
import http.server
import ipaddress
import os
import socket
import ssl
import threading
import urllib.error
import urllib.request

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from src.core import net_tls


# -- a throwaway certificate authority and a server signed by it ----------------------------------------
def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _make_ca():
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(_name("Test Root")).issuer_name(_name("Test Root"))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def _make_leaf(ca_key, ca_cert):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(_name("localhost")).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    ca_key, ca_cert = _make_ca()
    leaf_key, leaf_cert = _make_leaf(ca_key, ca_cert)
    (d / "ca.pem").write_bytes(_pem(ca_cert))
    (d / "leaf.pem").write_bytes(_pem(leaf_cert) + _key_pem(leaf_key))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(d / "leaf.pem"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"https://127.0.0.1:{server.server_address[1]}/health", str(d / "ca.pem")
    server.shutdown()


def _get(url, ctx):
    with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
        return r.read()


# -- trust ---------------------------------------------------------------------------------------------
def test_a_server_signed_by_an_authority_we_add_is_trusted_and_one_we_do_not_is_refused(tls_server):
    url, ca_file = tls_server
    assert _get(url, net_tls.build_context(cafile=ca_file)) == b'{"status":"ok"}'      # trusted when added to the list
    with pytest.raises(urllib.error.URLError) as e:                                     # not trusted by the normal list
        _get(url, ssl.create_default_context())
    assert isinstance(e.value.reason, ssl.SSLCertVerificationError)
    with pytest.raises(urllib.error.URLError):                                          # nor by the app's own default
        _get(url, net_tls.build_context())


def test_adding_roots_never_turns_verification_off():
    ctx = net_tls.build_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_the_default_context_really_includes_the_bundled_roots_on_top_of_the_systems(monkeypatch):
    """Start from a system store that knows NOTHING (a fresh Windows): the bundled list must still be in there."""
    def empty_system_store():
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname = True
        c.verify_mode = ssl.CERT_REQUIRED
        return c
    monkeypatch.setattr(net_tls.ssl, "create_default_context", empty_system_store)
    assert net_tls.build_context().cert_store_stats()["x509_ca"] > 100
    assert net_tls.build_context(cafile="").cert_store_stats()["x509_ca"] == 0     # an explicit empty choice adds nothing


def test_open_url_passes_the_shared_context_to_urllib(monkeypatch):
    seen = {}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None, context=None: seen.update(timeout=timeout, context=context) or "resp")
    assert net_tls.open_url("https://example.com", timeout=7) == "resp"
    assert seen == {"timeout": 7, "context": net_tls.ssl_context()}


def test_the_context_is_built_once_and_shared():
    assert net_tls.ssl_context() is net_tls.ssl_context()


def test_the_bundled_list_is_really_there_and_adds_public_roots():
    path = net_tls.bundled_cafile()
    assert path and os.path.isfile(path) and os.path.getsize(path) > 100_000
    base = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)                                       # starts with nothing trusted
    base.load_verify_locations(cafile=path)
    subjects = {tuple(x[0] for x in c["subject"] if x[0][0] == "organizationName") for c in base.get_ca_certs()}
    assert len(base.get_ca_certs()) > 100
    names = " ".join(str(s) for s in subjects)
    # the authorities behind Cloudflare (this server), GitHub (engine downloads) and Hugging Face (AI models)
    for authority in ("Google Trust Services", "Internet Security Research Group", "DigiCert", "Sectigo"):
        assert authority in names, authority


def test_a_missing_or_broken_bundle_falls_back_to_the_system_roots(tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("this is not a certificate")
    ctx = net_tls.build_context(cafile=str(bad))                                         # logged, not fatal
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    ctx2 = net_tls.build_context(cafile=str(tmp_path / "nope.pem"))
    assert ctx2.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("host", ["rainy.canefly.xyz", "github.com", "release-assets.githubusercontent.com", "huggingface.co"])
def test_the_bundled_roots_alone_verify_every_site_the_app_talks_to(host):
    """The sandbox failure end to end: with NO system roots at all, the bundled list still validates the live
    chains behind the license server (Google Trust Services), engine downloads (GitHub) and AI models (Hugging Face).
    Skipped when there's no internet."""
    try:
        socket.create_connection((host, 443), timeout=5).close()
    except OSError:
        pytest.skip("no network")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=net_tls.bundled_cafile())
    assert ctx.cert_store_stats()["x509_ca"] > 100
    with socket.create_connection((host, 443), timeout=10) as s:
        with ctx.wrap_socket(s, server_hostname=host) as t:
            assert t.version().startswith("TLS")


# -- why a connection failed ---------------------------------------------------------------------------
@pytest.mark.parametrize("exc,kind", [
    (urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate")), "certificate"),
    (ssl.SSLCertVerificationError("bad"), "certificate"),
    (urllib.error.URLError(ssl.SSLError("handshake failure")), "tls"),
    (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "dns"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (TimeoutError("timed out"), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "refused"),
    (ConnectionRefusedError(), "refused"),
    (urllib.error.URLError("something odd"), "other"),
    (ValueError("nope"), "other"),
])
def test_failure_kinds(exc, kind):
    assert net_tls.failure_kind(exc) == kind
    assert net_tls.describe_failure(exc).startswith(kind + ":")
