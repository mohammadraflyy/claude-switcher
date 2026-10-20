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

__version__ = "1.1.0"

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
        store[uid] = {"email": snap["account"].get("emailAddress", uid), "blob": _seal(snap)}
        _write(STORE, store)
        return store[uid]["email"]


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
        })
    unsaved = None
    if cur and cur not in _store():
        try:
            unsaved = _read(IDENT)["oauthAccount"].get("emailAddress", cur)
        except (OSError, KeyError, ValueError):
            pass
    return out, unsaved


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


def running_sessions():
    """Number of running `claude.exe` processes (native install). npm installs run as node.exe and are not counted."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in out.splitlines() if line.lower().startswith('"claude.exe"'))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "add":
        print("saved", save_current())
    elif cmd == "list":
        for u, e, a in accounts():
            print("*" if a else " ", e, u)
    elif cmd == "switch" and len(argv) > 2:
        m = [u for u, e, _ in accounts() if argv[2] in (u, e)]
        if not m:
            sys.exit("no such account")
        switch(m[0])
        print("switched; start a new `claude` session")
    else:
        sys.exit("usage: switcher.py add | list | switch <email|uuid>")


if __name__ == "__main__":
    main(sys.argv)
