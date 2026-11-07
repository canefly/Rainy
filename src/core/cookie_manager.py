import os
import json
import time
import shutil
import sqlite3
import hashlib
import tempfile
import configparser
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

from .database import db


# Key Google / YouTube session authentication cookie names required for private Drive / YouTube access
GOOGLE_AUTH_COOKIE_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}


@dataclass
class CookieEntry:
    domain: str
    name: str
    value: str
    path: str = "/"
    secure: bool = True
    expires: Optional[int] = None


class CookieManager:
    """
    Pure-local, zero-port Cookie Authentication Manager (yt-dlp style).
    Supports:
      1. 'disabled' — Public links only (no cookies sent)
      2. 'firefox'  — Auto-detects Firefox profiles and reads cookies.sqlite via safe temp shadow-copy
      3. 'file'     — Imports Netscape cookies.txt or JSON cookie files
      4. 'paste'    — Parses raw pasted cookies (Netscape format, JSON array, or raw Cookie: header)
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CookieManager, cls).__new__(cls)
            cls._instance._cached_cookies: List[CookieEntry] = []
            cls._instance._cache_dirty = True
        return cls._instance

    @classmethod
    def instance(cls) -> "CookieManager":
        return cls()

    # --- Settings Accessors ---
    def get_mode(self) -> str:
        mode = db.get_setting("cookie_source_mode", "disabled") or "disabled"
        if mode not in ("disabled", "firefox", "file", "paste"):
            return "disabled"
        return mode

    def set_mode(self, mode: str):
        if mode not in ("disabled", "firefox", "file", "paste"):
            mode = "disabled"
        db.set_setting("cookie_source_mode", mode)
        self.invalidate_cache()

    def get_firefox_profile(self) -> str:
        return db.get_setting("cookie_firefox_profile", "") or ""

    def set_firefox_profile(self, profile_path: str):
        db.set_setting("cookie_firefox_profile", profile_path)
        self.invalidate_cache()

    def get_cookie_file_path(self) -> str:
        return db.get_setting("cookie_file_path", "") or ""

    def set_cookie_file_path(self, file_path: str):
        db.set_setting("cookie_file_path", file_path)
        self.invalidate_cache()

    def get_raw_cookie_text(self) -> str:
        return db.get_setting("cookie_raw_text", "") or ""

    def set_raw_cookie_text(self, raw_text: str, flush: bool = True):
        db.set_setting("cookie_raw_text", raw_text, flush=flush)
        self.invalidate_cache()

    def invalidate_cache(self):
        self._cache_dirty = True
        self._cached_cookies = []

    # --- Firefox Profile Auto-Discovery ---
    @staticmethod
    def get_firefox_base_dir() -> str:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return os.path.join(appdata, "Mozilla", "Firefox")
        return os.path.expanduser("~/AppData/Roaming/Mozilla/Firefox")

    @classmethod
    def discover_firefox_profiles(cls, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Scans %APPDATA%\\Mozilla\\Firefox\\profiles.ini and Profiles directory.
        Returns a sorted list of profile dicts with default-release / active cookie profiles first.
        """
        ff_dir = base_dir or cls.get_firefox_base_dir()
        if not os.path.exists(ff_dir):
            return []

        profiles: List[Dict[str, Any]] = []
        seen_paths = set()

        ini_path = os.path.join(ff_dir, "profiles.ini")
        if os.path.exists(ini_path):
            cfg = configparser.RawConfigParser()
            try:
                cfg.read(ini_path, encoding="utf-8")
                for section in cfg.sections():
                    if not section.lower().startswith("profile"):
                        continue
                    name = cfg.get(section, "Name", fallback=section)
                    raw_path = cfg.get(section, "Path", fallback="")
                    is_relative = cfg.get(section, "IsRelative", fallback="1") == "1"
                    is_default = cfg.get(section, "Default", fallback="0") == "1"
                    if not raw_path:
                        continue
                    full_path = os.path.normpath(os.path.join(ff_dir, raw_path) if is_relative else raw_path)
                    if not os.path.isdir(full_path):
                        continue
                    cookies_db = os.path.join(full_path, "cookies.sqlite")
                    has_cookies = os.path.isfile(cookies_db)
                    folder_id = os.path.basename(full_path)
                    seen_paths.add(os.path.normcase(full_path))
                    profiles.append({
                        "id": folder_id,
                        "name": name,
                        "path": full_path,
                        "has_cookies": has_cookies,
                        "is_default": is_default or ("default-release" in folder_id.lower()),
                    })
            except Exception:
                pass

        # Fallback scan of Profiles/ subdirectory for any unlisted folders
        profiles_subdir = os.path.join(ff_dir, "Profiles")
        if os.path.isdir(profiles_subdir):
            try:
                for entry in os.listdir(profiles_subdir):
                    full_path = os.path.normpath(os.path.join(profiles_subdir, entry))
                    if not os.path.isdir(full_path):
                        continue
                    norm_key = os.path.normcase(full_path)
                    if norm_key in seen_paths:
                        continue
                    cookies_db = os.path.join(full_path, "cookies.sqlite")
                    has_cookies = os.path.isfile(cookies_db)
                    parts = entry.split(".", 1)
                    display_name = parts[1] if len(parts) > 1 else entry
                    profiles.append({
                        "id": entry,
                        "name": display_name,
                        "path": full_path,
                        "has_cookies": has_cookies,
                        "is_default": "default-release" in entry.lower(),
                    })
            except Exception:
                pass

        # Sort so profiles with cookies.sqlite and default-release appear first
        profiles.sort(key=lambda p: (not p["has_cookies"], not p["is_default"], p["name"].lower()))
        return profiles

    # --- Cookie Readers & Parsers ---
    @staticmethod
    def read_firefox_sqlite(profile_path: str) -> List[CookieEntry]:
        """
        Safely copies Firefox's cookies.sqlite (+ WAL/SHM if open) to a temporary directory
        so Firefox is never locked even while actively running, then extracts cookies.
        """
        if not profile_path:
            return []
        cookies_db = (
            profile_path
            if os.path.isfile(profile_path) and profile_path.endswith(".sqlite")
            else os.path.join(profile_path, "cookies.sqlite")
        )
        if not os.path.isfile(cookies_db):
            return []

        temp_dir = tempfile.mkdtemp(prefix="knot_ff_")
        entries: List[CookieEntry] = []
        try:
            temp_db = os.path.join(temp_dir, "cookies.sqlite")
            shutil.copy2(cookies_db, temp_db)
            for suffix in ("-wal", "-shm"):
                aux = cookies_db + suffix
                if os.path.isfile(aux):
                    try:
                        shutil.copy2(aux, temp_db + suffix)
                    except Exception:
                        pass

            now = int(time.time())
            conn = sqlite3.connect(temp_db)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT host, name, value, path, isSecure, expiry FROM moz_cookies"
                )
                for host, name, value, path, is_secure, expiry in cursor.fetchall():
                    if not host or not name:
                        continue
                    # Keep session cookies (expiry == 0/None) or non-expired cookies
                    if expiry and int(expiry) > 0 and int(expiry) < now:
                        continue
                    entries.append(
                        CookieEntry(
                            domain=str(host).strip(),
                            name=str(name).strip(),
                            value=str(value or ""),
                            path=str(path or "/"),
                            secure=bool(is_secure),
                            expires=int(expiry) if expiry else None,
                        )
                    )
            finally:
                conn.close()
        except Exception:
            return []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return entries

    @staticmethod
    def parse_cookie_text(raw_text: str) -> List[CookieEntry]:
        """
        Universal parser supporting:
          1. Netscape cookies.txt format (7 tab-separated columns)
          2. JSON array of cookie objects (Cookie-Editor / EditThisCookie export)
          3. Raw HTTP Cookie header string ('SID=abc; HSID=def; __Secure-1PSID=xyz')
        """
        if not raw_text or not raw_text.strip():
            return []

        text = raw_text.strip()
        entries: List[CookieEntry] = []

        # 1. Check if JSON array or JSON object
        if text.startswith("[") or text.startswith("{"):
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "cookies" in data:
                    data = data["cookies"]
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name", "")).strip()
                        val = str(item.get("value", "")).strip()
                        domain = str(item.get("domain", item.get("host", ".google.com"))).strip()
                        if not name:
                            continue
                        exp = item.get("expirationDate") or item.get("expiry") or item.get("expires")
                        entries.append(
                            CookieEntry(
                                domain=domain or ".google.com",
                                name=name,
                                value=val,
                                path=str(item.get("path", "/")),
                                secure=bool(item.get("secure", True)),
                                expires=int(float(exp)) if exp else None,
                            )
                        )
                    return entries
            except Exception:
                pass

        # 2. Check line-by-line for Netscape cookies.txt or raw key=value lines
        for line in text.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            # Handle #HttpOnly_ prefix in Netscape cookies.txt
            if raw_line.startswith("#HttpOnly_"):
                raw_line = raw_line[len("#HttpOnly_"):]
            elif raw_line.startswith("#"):
                continue

            # Netscape format has 6 or 7 tab-separated (or whitespace-separated) fields:
            # domain, include_subdomains, path, secure, expiry, name, value
            parts = raw_line.split("\t")
            if len(parts) >= 7:
                domain = parts[0].strip()
                path = parts[2].strip() or "/"
                secure = parts[3].strip().upper() == "TRUE"
                try:
                    expiry = int(parts[4].strip())
                except ValueError:
                    expiry = None
                name = parts[5].strip()
                value = parts[6].strip()
                if name:
                    entries.append(
                        CookieEntry(
                            domain=domain,
                            name=name,
                            value=value,
                            path=path,
                            secure=secure,
                            expires=expiry,
                        )
                    )
                continue

            # 3. Fallback: Raw 'Cookie: k=v; k2=v2' header format
            header_body = raw_line
            if header_body.lower().startswith("cookie:"):
                header_body = header_body.split(":", 1)[1].strip()

            if "=" in header_body:
                for pair in header_body.split(";"):
                    pair = pair.strip()
                    if not pair or "=" not in pair:
                        continue
                    k, v = pair.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if not k or k.lower() in ("path", "domain", "expires", "max-age", "secure", "httponly", "samesite"):
                        continue
                    entries.append(
                        CookieEntry(
                            domain=".google.com",
                            name=k,
                            value=v,
                            path="/",
                            secure=True,
                            expires=None,
                        )
                    )

        return entries

    # --- Active Cookie Resolution ---
    def load_cookies(self, force_reload: bool = False) -> List[CookieEntry]:
        if not self._cache_dirty and not force_reload:
            return self._cached_cookies

        mode = self.get_mode()
        if mode == "disabled":
            self._cached_cookies = []
        elif mode == "firefox":
            profile_path = self.get_firefox_profile()
            has_valid_db = bool(
                profile_path and (
                    (os.path.isfile(profile_path) and profile_path.endswith(".sqlite")) or
                    os.path.isfile(os.path.join(profile_path, "cookies.sqlite"))
                )
            )
            if not has_valid_db:
                profiles = self.discover_firefox_profiles()
                if profiles:
                    profile_path = profiles[0]["path"]
                    self.set_firefox_profile(profile_path, flush=True)
            self._cached_cookies = self.read_firefox_sqlite(profile_path)
        elif mode == "file":
            fpath = self.get_cookie_file_path()
            if fpath and os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        self._cached_cookies = self.parse_cookie_text(f.read())
                except Exception:
                    self._cached_cookies = []
            else:
                self._cached_cookies = []
        elif mode == "paste":
            raw = self.get_raw_cookie_text()
            self._cached_cookies = self.parse_cookie_text(raw)
        else:
            self._cached_cookies = []

        self._cache_dirty = False
        return self._cached_cookies

    def get_cookies_for_domain(self, domain_or_url: str) -> Dict[str, str]:
        """Returns a dictionary of {cookie_name: cookie_value} matching the target domain or URL."""
        target = domain_or_url.lower().strip()
        if "://" in target:
            target = urlparse(target).hostname or target
        target = target.lstrip(".")

        matched: Dict[str, str] = {}
        for c in self.load_cookies():
            cdom = c.domain.lower().lstrip(".")
            if target == cdom or target.endswith("." + cdom) or cdom.endswith("." + target):
                matched[c.name] = c.value
        return matched

    def get_cookie_header(self, domain_or_url: str = "drive.google.com") -> str:
        cookies = self.get_cookies_for_domain(domain_or_url)
        if not cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def get_cookie_jar(self) -> CookieJar:
        jar = CookieJar()
        for c in self.load_cookies():
            domain = c.domain if c.domain.startswith(".") else "." + c.domain
            cookie = Cookie(
                version=0,
                name=c.name,
                value=c.value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=c.path or "/",
                path_specified=True,
                secure=c.secure,
                expires=c.expires,
                discard=c.expires is None,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
            jar.set_cookie(cookie)
        return jar

    def get_google_sapisidhash(self, origin: str = "https://drive.google.com") -> Optional[str]:
        """
        Generates Google's SAPISIDHASH authorization token from loaded cookies
        for authenticated Google Drive / YouTube requests.
        """
        g_cookies = self.get_cookies_for_domain("google.com")
        sapisid = g_cookies.get("SAPISID") or g_cookies.get("__Secure-3PAPISID")
        if not sapisid:
            return None
        ts = int(time.time())
        digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode("utf-8")).hexdigest()
        return f"SAPISIDHASH {ts}_{digest}"

    def get_status_summary(self, force_reload: bool = False) -> Dict[str, Any]:
        """Returns diagnostic status for the Preferences UI."""
        mode = self.get_mode()
        if mode == "disabled":
            return {
                "mode": "disabled",
                "state": "disabled",
                "total_cookies": 0,
                "google_cookies": 0,
                "youtube_cookies": 0,
                "auth_keys_found": [],
                "has_google_auth": False,
                "message": "Disabled — Public links only (Private Drive folders restricted)",
            }

        cookies = self.load_cookies(force_reload=force_reload)
        total = len(cookies)
        google_count = 0
        yt_count = 0
        auth_found = set()

        for c in cookies:
            dom = c.domain.lower()
            if "google." in dom or "drive.google" in dom:
                google_count += 1
                if c.name in GOOGLE_AUTH_COOKIE_NAMES and c.value:
                    auth_found.add(c.name)
            elif "youtube." in dom or "youtu.be" in dom:
                yt_count += 1
                if c.name in GOOGLE_AUTH_COOKIE_NAMES and c.value:
                    auth_found.add(c.name)

        has_auth = len(auth_found) > 0

        if total == 0:
            if mode == "firefox":
                msg = "No cookies loaded — Check Firefox profile selection"
            elif mode == "file":
                msg = "No cookies loaded — Select a valid cookies.txt file"
            else:
                msg = "No cookies loaded — Paste Netscape, JSON, or Cookie header text"
            return {
                "mode": mode,
                "state": "warning",
                "total_cookies": 0,
                "google_cookies": 0,
                "youtube_cookies": 0,
                "auth_keys_found": [],
                "has_google_auth": False,
                "message": msg,
            }

        if has_auth:
            msg = (
                f"Active • {google_count} Google & {yt_count} YouTube cookies loaded "
                f"({len(auth_found)} auth tokens ready)"
            )
            state = "ready"
        else:
            msg = (
                f"Loaded {total} cookies, but no Google login session (SID / SAPISID) detected"
            )
            state = "warning"

        return {
            "mode": mode,
            "state": state,
            "total_cookies": total,
            "google_cookies": google_count,
            "youtube_cookies": yt_count,
            "auth_keys_found": sorted(auth_found),
            "has_google_auth": has_auth,
            "message": msg,
        }


cookie_manager = CookieManager.instance()
