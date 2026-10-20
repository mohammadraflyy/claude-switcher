"""Claude Code account switcher (Windows).

Swaps `claudeAiOauth` in ~/.claude/.credentials.json and `oauthAccount` in ~/.claude.json.
Snapshots are DPAPI-encrypted in %APPDATA%\\ClaudeSwitcher\\accounts.json.
Only for your own accounts.
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

__version__ = "1.3.0"

HOME = Path.home()
CRED = HOME / ".claude" / ".credentials.json"
IDENT = HOME / ".claude.json"
STORE = Path(os.environ.get("APPDATA") or HOME) / "ClaudeSwitcher" / "accounts.json"
_LOCK = threading.RLock()  # tray thread, sync thread and dashboard threads all touch the same files


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    src = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, data):
    """Atomic write, previous file kept as .bak."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    os.replace(tmp, path)


def _store():
    return _read(STORE) if STORE.exists() else {}


def current():
    """accountUuid of the logged-in account, or None."""
    try:
        return _read(IDENT)["oauthAccount"]["accountUuid"]
    except (OSError, KeyError, ValueError):
        return None


def _snapshot():
    return {"oauth": _read(CRED)["claudeAiOauth"], "account": _read(IDENT)["oauthAccount"]}


def _seal(snap):
    return base64.b64encode(_dpapi(json.dumps(snap).encode(), True)).decode()


def _open(blob):
    return json.loads(_dpapi(base64.b64decode(blob), False))


def save_current():
    """Store/refresh the snapshot of the currently logged-in account. Returns its email."""
    with _LOCK:
        snap = _snapshot()
        store = _store()
        uid = snap["account"]["accountUuid"]
        store[uid] = {**store.get(uid, {}), "email": snap["account"].get("emailAddress", uid), "blob": _seal(snap)}  # keeps label/color
        _write(STORE, store)
        return store[uid]["email"]


COLORS = ("#d97757", "#2e7d4f", "#3b82f6", "#a855f7", "#e5a50a", "#e11d48")


def _expired(oauth):
    """True when the refresh token is dead, so only `claude /login` can revive the account."""
    t = oauth.get("refreshTokenExpiresAt")
    return bool(t) and t < time.time() * 1000


def accounts():
    """[(uuid, email, is_active)]"""
    cur = current()
    return [(u, v["email"], u == cur) for u, v in _store().items()]


def details():
    """Dashboard data, never includes tokens: ({accounts}, unsaved_email_or_None)."""
    cur, out = current(), []
    for uid, v in _store().items():
        try:
            snap = _snapshot() if uid == cur else _open(v["blob"])
        except Exception:
            snap = {"oauth": {}, "account": {}}
        o, a = snap["oauth"], snap["account"]
        out.append({
            "uuid": uid, "email": v["email"], "name": a.get("displayName") or a.get("fullName", ""),
            "org": a.get("organizationName", ""), "plan": o.get("subscriptionType", ""), "active": uid == cur,
            "label": v.get("label", ""), "color": v.get("color", ""), "expired": _expired(o),
        })
    return out, unsaved_email()


def unsaved_email():
    """Email of the logged-in account when it is not saved yet, else None."""
    cur = current()
    if cur and cur not in _store():
        try:
            return _read(IDENT)["oauthAccount"].get("emailAddress", cur)
        except (OSError, KeyError, ValueError):
            pass
    return None


def get_setting(key, default=None):
    try:
        return _read(STORE.with_name("settings.json")).get(key, default)
    except (OSError, ValueError):
        return default


def set_setting(key, value):
    with _LOCK:
        path = STORE.with_name("settings.json")
        try:
            data = _read(path)
        except (OSError, ValueError):
            data = {}
        data[key] = value
        _write(path, data)


def switch(uid):
    """Sync-back the active account (its refresh token may have rotated), then load `uid`."""
    with _LOCK:
        store = _store()
        if uid not in store:
            raise KeyError(uid)
        if current() == uid:
            return
        if current() in store:
            save_current()
            store = _store()
        snap = _open(store[uid]["blob"])
        cred = _read(CRED)  # keep mcpOAuth untouched
        cred["claudeAiOauth"] = snap["oauth"]
        ident = _read(IDENT)
        ident["oauthAccount"] = snap["account"]
        _write(CRED, cred)
        _write(IDENT, ident)


def remove(uid):
    with _LOCK:
        store = _store()
        store.pop(uid, None)
        _write(STORE, store)


def next_account():
    """Switch to the next saved account that is not expired (wraps around). Returns its email, or None."""
    with _LOCK:
        accts, _ = details()
        i = next((n for n, a in enumerate(accts) if a["active"]), -1)
        for a in accts[i + 1:] + accts[:i + 1]:
            if not a["expired"] and not a["active"]:
                switch(a["uuid"])
                return a["email"]
    return None


def set_label(uid, label="", color=""):
    label = str(label).strip()[:24]
    if color not in ("",) + COLORS:
        raise ValueError("unknown color")
    with _LOCK:
        store = _store()
        if uid not in store:
            raise KeyError(uid)
        store[uid]["label"], store[uid]["color"] = label, color
        _write(STORE, store)


def export_store():
    """Backup data: the saved accounts as stored (tokens stay DPAPI-encrypted, restorable by the same Windows user only)."""
    return {"version": 1, "accounts": _store()}


def import_store(data):
    """Merge a backup into the store. Every entry must decrypt, otherwise nothing is imported. Returns the count."""
    try:
        items = data["accounts"]
        for v in items.values():
            snap = _open(v["blob"])
            snap["oauth"], snap["account"]["accountUuid"], str(v["email"])
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise ValueError("not a valid backup for this Windows user")
    with _LOCK:
        store = _store()
        store.update({u: {k: v[k] for k in ("email", "blob", "label", "color") if k in v} for u, v in items.items()})
        _write(STORE, store)
    return len(items)


def sync_active(settle=2.0):
    """Copy the live login back into the active account's slot when its tokens changed
    (Claude Code rotates them). True if the slot was updated.

    The live login spans two files, so a /login or a switch caught half-written would look like a
    change; the snapshot must stay identical across `settle` seconds before it is trusted."""
    try:
        cur = current()
        if cur not in _store():
            return False
        first = _snapshot()
        time.sleep(settle)
        with _LOCK:
            now = _snapshot()
            if current() != cur or now != first or now["account"]["accountUuid"] != cur:
                return False
            if _open(_store()[cur]["blob"])["oauth"] == now["oauth"]:
                return False
            save_current()
            return True
    except (OSError, ValueError, KeyError):
        return False


_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_npm_cache = (0.0, 0)  # (time, count): the PowerShell lookup is slow, the dashboard polls every 5s


def _npm_sessions():
    """Claude Code installed with npm runs as `node.exe ...@anthropic-ai/claude-code/cli.js`."""
    global _npm_cache
    if time.time() - _npm_cache[0] < 15:
        return _npm_cache[1]
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
          "Where-Object { $_.CommandLine -match 'anthropic-ai[\\\\/]claude-code' } | Measure-Object).Count")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
        n = int(out.strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        n = 0
    _npm_cache = (time.time(), n)
    return n


def running_sessions():
    """Number of running Claude Code processes: native `claude.exe` plus npm installs."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    return sum(1 for line in out.splitlines() if line.lower().startswith('"claude.exe"')) + _npm_sessions()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "add":
        print("saved", save_current())
    elif cmd == "list":
        for u, e, a in accounts():
            print("*" if a else " ", e, u)
    elif cmd == "next":
        print("switched to", next_account() or "nothing (no other usable account)")
    elif cmd == "switch" and len(argv) > 2:
        m = [u for u, e, _ in accounts() if argv[2] in (u, e)]
        if not m:
            sys.exit("no such account")
        switch(m[0])
        print("switched; start a new `claude` session")
    else:
        sys.exit("usage: switcher.py add | list | next | switch <email|uuid>")


if __name__ == "__main__":
    main(sys.argv)
