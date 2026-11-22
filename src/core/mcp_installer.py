"""
Registers the Rainy MCP server in AI apps' config files (Settings > MCP).

Each app keeps its MCP servers in its own file and format:

| App            | File                                              | Format                   |
|----------------|---------------------------------------------------|--------------------------|
| Claude Code    | ~/.claude.json                                    | JSON `mcpServers` (user) |
| Claude Desktop | %APPDATA%/Claude/claude_desktop_config.json       | JSON `mcpServers`        |
| Antigravity    | ~/.gemini/config/mcp_config.json                  | JSON `mcpServers`        |
| Gemini CLI     | ~/.gemini/settings.json                           | JSON `mcpServers`        |
| Codex          | ~/.codex/config.toml                              | TOML `[mcp_servers.x]`   |
| Cursor         | ~/.cursor/mcp.json                                | JSON `mcpServers`        |
| VS Code        | %APPDATA%/Code/User/mcp.json                      | JSON `servers` (stdio)   |

Writes only touch our own entry, keep everything else in the file, back the file up once
(`<file>.knot.bak`) and replace it atomically. A file we can't parse is never written.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

SERVER_KEY = "knot-tool"
KNOWN_KEYS = (SERVER_KEY,)
BACKUP_SUFFIX = ".knot.bak"

# States
NOT_FOUND = "not_found"          # app doesn't seem to be on this machine
NOT_INSTALLED = "not_installed"  # app found, no Rainy entry
INSTALLED = "installed"          # entry points at this Rainy
OUTDATED = "outdated"            # entry points somewhere else (moved / older install)
ERROR = "error"                  # config file can't be read

_which = shutil.which  # patched in tests


@dataclass
class McpTarget:
    id: str
    name: str
    path: str
    fmt: str          # "json" | "vscode" | "toml"
    detected: bool


# ---------------------------------------------------------------------------
# What to run
# ---------------------------------------------------------------------------
def server_command() -> Tuple[str, List[str]]:
    """The command an AI app should launch to reach this Rainy."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(exe_dir, "knot-mcp.exe"), []
    py = os.path.abspath(sys.executable)
    if os.path.basename(py).lower() == "pythonw.exe":  # stdio needs a console python
        console = os.path.join(os.path.dirname(py), "python.exe")
        if os.path.isfile(console):
            py = console
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))
    return py, [script]


def server_entry(fmt: str = "json") -> Dict[str, Any]:
    cmd, args = server_command()
    if fmt == "vscode":
        return {"type": "stdio", "command": cmd, "args": args}
    return {"command": cmd, "args": args}


def config_json_snippet() -> str:
    """Generic `mcpServers` JSON for any app not covered by auto-install."""
    return json.dumps({"mcpServers": {SERVER_KEY: server_entry()}}, indent=2)


# ---------------------------------------------------------------------------
# Where each app keeps its config
# ---------------------------------------------------------------------------
def _home() -> str:
    return os.path.expanduser("~")


def _appdata() -> str:
    if sys.platform == "win32":
        return os.environ.get("APPDATA") or os.path.join(_home(), "AppData", "Roaming")
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Application Support")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")


def _local_programs(*parts: str) -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(_home(), "AppData", "Local")
    return os.path.join(base, "Programs", *parts)


def targets() -> List[McpTarget]:
    h, ad = _home(), _appdata()
    gem = os.path.join(h, ".gemini")

    ag_candidates = [os.path.join(gem, "config", "mcp_config.json"), os.path.join(gem, "antigravity", "mcp_config.json")]
    ag_path = next((p for p in ag_candidates if os.path.isfile(p)), ag_candidates[0])

    claude_code = os.path.join(h, ".claude.json")
    claude_desktop = os.path.join(ad, "Claude", "claude_desktop_config.json")
    gemini_cli = os.path.join(gem, "settings.json")
    codex = os.path.join(h, ".codex", "config.toml")
    cursor = os.path.join(h, ".cursor", "mcp.json")
    vscode = os.path.join(ad, "Code", "User", "mcp.json")

    return [
        McpTarget("claude_code", "Claude Code", claude_code, "json",
                  os.path.isfile(claude_code) or os.path.isdir(os.path.join(h, ".claude")) or bool(_which("claude"))),
        McpTarget("claude_desktop", "Claude Desktop", claude_desktop, "json",
                  os.path.isdir(os.path.dirname(claude_desktop))),
        McpTarget("antigravity", "Antigravity", ag_path, "json",
                  os.path.isfile(ag_path) or os.path.isdir(os.path.join(gem, "antigravity"))
                  or os.path.isdir(_local_programs("Antigravity"))),
        McpTarget("gemini_cli", "Gemini CLI", gemini_cli, "json",
                  os.path.isfile(gemini_cli) or bool(_which("gemini"))),
        McpTarget("codex", "Codex", codex, "toml",
                  os.path.isdir(os.path.dirname(codex)) or bool(_which("codex"))),
        McpTarget("cursor", "Cursor", cursor, "json",
                  os.path.isdir(os.path.dirname(cursor)) or os.path.isdir(_local_programs("cursor"))),
        McpTarget("vscode", "VS Code", vscode, "vscode",
                  os.path.isdir(os.path.dirname(vscode))),
    ]


def target_by_id(target_id: str) -> Optional[McpTarget]:
    return next((t for t in targets() if t.id == target_id), None)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("top level is not a JSON object")
    return data


def _servers_key(fmt: str) -> str:
    return "servers" if fmt == "vscode" else "mcpServers"


def _read_entries(t: McpTarget) -> Dict[str, Any]:
    """{key: entry} for our known keys present in the target's config."""
    if t.fmt == "toml":
        if not os.path.isfile(t.path):
            return {}
        with open(t.path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        toml = _toml_module()
        if toml is not None:
            servers = (toml.loads(text).get("mcp_servers") or {})
            return {k: servers[k] for k in KNOWN_KEYS if isinstance(servers.get(k), dict)}
        return {k: _toml_block_values(text, k) for k in KNOWN_KEYS if _toml_block_span(text, k)}
    servers = _read_json(t.path).get(_servers_key(t.fmt)) or {}
    if not isinstance(servers, dict):
        return {}
    return {k: servers[k] for k in KNOWN_KEYS if isinstance(servers.get(k), dict)}


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _entry_matches(entry: Dict[str, Any]) -> bool:
    cmd, args = server_command()
    e_args = entry.get("args") or []
    if not isinstance(e_args, list) or len(e_args) != len(args):
        return False
    return _same_path(entry.get("command", ""), cmd) and all(_same_path(x, y) for x, y in zip(e_args, args))


def status(t: McpTarget) -> Dict[str, Any]:
    """{"state", "key", "detail"} for one app."""
    try:
        entries = _read_entries(t)
    except Exception as e:
        return {"state": ERROR, "key": None, "detail": f"Can't read {os.path.basename(t.path)}: {e}"}
    if not entries:
        if not t.detected:
            return {"state": NOT_FOUND, "key": None, "detail": "Not found on this computer"}
        return {"state": NOT_INSTALLED, "key": None, "detail": "Not installed"}
    for key, entry in entries.items():
        if _entry_matches(entry):
            if entry.get("disabled") is True:
                return {"state": OUTDATED, "key": key, "detail": "Installed but disabled in the app"}
            return {"state": INSTALLED, "key": key, "detail": "Installed"}
    key = next(iter(entries))
    return {"state": OUTDATED, "key": key, "detail": "Points to a different Rainy copy"}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _backup(path: str):
    if os.path.isfile(path):
        try:
            shutil.copy2(path, path + BACKUP_SUFFIX)
        except OSError:
            pass


def _write_atomic(path: str, text: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _backup(path)
    tmp = f"{path}.knot-tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _toml_module():
    try:
        import tomllib  # Python 3.11+
        return tomllib
    except ImportError:
        try:
            import tomli  # same API, Python < 3.11
            return tomli
        except ImportError:
            return None


_TOML_VALUE = re.compile(r"'([^']*)'|\"((?:[^\"\\]|\\.)*)\"")


def _toml_block_values(text: str, key: str) -> Dict[str, Any]:
    """Minimal reader for our own block (command / args) when no TOML parser is installed."""
    span = _toml_block_span(text, key)
    out: Dict[str, Any] = {}
    if not span:
        return out
    for line in text.splitlines()[span[0] + 1:span[1]]:
        if _ANY_HEADER.match(line):
            break
        name, _, value = line.partition("=")
        name = name.strip()
        vals = [a if a or not b else json.loads(f'"{b}"') for a, b in _TOML_VALUE.findall(value)]
        if name == "command" and vals:
            out["command"] = vals[0]
        elif name == "args":
            out["args"] = vals
    return out


def _toml_str(s: str) -> str:
    s = str(s)
    if "'" not in s and "\n" not in s:
        return f"'{s}'"  # literal string: Windows backslashes stay as they are
    return json.dumps(s)


def _toml_block(key: str, entry: Dict[str, Any]) -> str:
    args = ", ".join(_toml_str(a) for a in entry.get("args") or [])
    return f"[mcp_servers.{key}]\ncommand = {_toml_str(entry['command'])}\nargs = [{args}]\n"


_TOML_HEADER = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?([A-Za-z0-9_-]+)"?\s*(?:\.[^\]]*)?\]\s*$')
_ANY_HEADER = re.compile(r"^\s*\[")


def _toml_block_span(text: str, key: str) -> Optional[Tuple[int, int]]:
    """Line span [start, end) of `[mcp_servers.<key>]` and its sub-tables."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, line in enumerate(lines):
        if _ANY_HEADER.match(line):
            m = _TOML_HEADER.match(line)
            mine = bool(m and m.group(1) == key)
            if start is None and mine:
                start = i
            elif start is not None and not mine:
                end = i
                break
    if start is None:
        return None
    return start, (end if end is not None else len(lines))


def _install_toml(t: McpTarget, key: str):
    text = ""
    if os.path.isfile(t.path):
        with open(t.path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    block = _toml_block(key, server_entry())
    span = _toml_block_span(text, key)
    lines = text.splitlines(keepends=True)
    if span:
        a, b = span
        lines[a:b] = [block + ("\n" if b < len(lines) else "")]
        text = "".join(lines)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n" if text.strip() else "") + block
    _write_atomic(t.path, text)


def _remove_toml(t: McpTarget):
    if not os.path.isfile(t.path):
        return
    with open(t.path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    for key in KNOWN_KEYS:
        span = _toml_block_span(text, key)
        if span:
            lines = text.splitlines(keepends=True)
            del lines[span[0]:span[1]]
            text = "".join(lines)
    _write_atomic(t.path, re.sub(r"\n{3,}", "\n\n", text))


def install(t: McpTarget) -> str:
    """Adds or repairs the Rainy entry. Returns a short message."""
    current = status(t)
    if current["state"] == ERROR:
        raise RuntimeError(current["detail"] + ". Fix or remove the file, then try again.")
    key = current["key"] or SERVER_KEY
    if t.fmt == "toml":
        _install_toml(t, key)
    else:
        data = _read_json(t.path)
        skey = _servers_key(t.fmt)
        servers = data.get(skey)
        if not isinstance(servers, dict):
            servers = {}
        entry = dict(servers.get(key) or {})
        entry.update(server_entry(t.fmt))
        if "disabled" in entry:
            entry["disabled"] = False
        servers[key] = entry
        data[skey] = servers
        _write_atomic(t.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    verb = "Updated" if current["state"] == OUTDATED else "Installed"
    return f"{verb} in {t.name}. Restart {t.name} (or reload its MCP servers) to pick it up."


def remove(t: McpTarget) -> str:
    current = status(t)
    if current["state"] == ERROR:
        raise RuntimeError(current["detail"])
    if t.fmt == "toml":
        _remove_toml(t)
    else:
        data = _read_json(t.path)
        servers = data.get(_servers_key(t.fmt))
        if isinstance(servers, dict):
            for k in KNOWN_KEYS:
                servers.pop(k, None)
            _write_atomic(t.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return f"Removed from {t.name}."


# ---------------------------------------------------------------------------
# Connection test: run the real MCP server the way an AI app would
# ---------------------------------------------------------------------------
TEST_CLIENT = {"name": "knot-self-test", "version": "1.0"}


def run_connection_test(timeout_s: float = 12.0, pipe_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Starts the MCP server, does the MCP handshake, lists tools and calls
    get_workspace_status. Must NOT run on the GUI thread (the last step needs it free).
    """
    steps: List[Dict[str, Any]] = []
    cmd, args = server_command()
    t_all = time.perf_counter()

    def step(name: str, ok: bool, t0: float, detail: str = ""):
        steps.append({"name": name, "ok": ok, "ms": round((time.perf_counter() - t0) * 1000.0), "detail": detail})
        return ok

    def done() -> Dict[str, Any]:
        return {"ok": bool(steps) and all(s["ok"] for s in steps), "steps": steps,
                "total_ms": round((time.perf_counter() - t_all) * 1000.0)}

    t0 = time.perf_counter()
    if not os.path.isfile(cmd):
        step("Start MCP server", False, t0, f"Not found: {cmd}")
        return done()
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = dict(os.environ)
        if pipe_name:
            env["KNOT_MCP_PIPE"] = pipe_name
        proc = subprocess.Popen(
            [cmd, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags, env=env,
        )
    except Exception as e:
        step("Start MCP server", False, t0, str(e))
        return done()

    lines: "queue.Queue[bytes]" = queue.Queue()

    def reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                lines.put(raw)
        except Exception:
            pass

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout_s

    def send(msg: Dict[str, Any]):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def rpc(req_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"No reply to {method}")
            try:
                raw = lines.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"No reply to {method}")
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if msg.get("id") == req_id:
                return msg

    try:
        try:
            init = rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": TEST_CLIENT})
            name = ((init.get("result") or {}).get("serverInfo") or {}).get("name", "?")
            has_sop = bool((init.get("result") or {}).get("instructions"))
            if not step("Start MCP server", "result" in init, t0, f"{name}" + (" · instructions loaded" if has_sop else " · no instructions")):
                return done()
        except Exception as e:
            step("Start MCP server", False, t0, str(e))
            return done()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        t0 = time.perf_counter()
        try:
            tools = ((rpc(2, "tools/list", {}).get("result") or {}).get("tools")) or []
            if not step("List tools", bool(tools), t0, f"{len(tools)} tools"):
                return done()
        except Exception as e:
            step("List tools", False, t0, str(e))
            return done()

        t0 = time.perf_counter()
        try:
            res = rpc(3, "tools/call", {"name": "get_workspace_status", "arguments": {}}).get("result") or {}
            text = ((res.get("content") or [{}])[0]).get("text", "")
            try:
                body = json.loads(text)
            except ValueError:
                body = {}
            err = str(body.get("error") or text)
            if res.get("isError") and "No script document" in err:
                step("Reach Rainy", True, t0, "Connected · no project open")
            elif res.get("isError"):
                step("Reach Rainy", False, t0, err[:300])
            else:
                step("Reach Rainy", True, t0, f"Project: {body.get('document_name') or '?'}")
        except Exception as e:
            step("Reach Rainy", False, t0, str(e))
        return done()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
