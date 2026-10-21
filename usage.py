"""Usage alerts from Claude Code's statusline data.

Claude Code pipes a JSON document to the statusLine command; for subscription plans it carries
`rate_limits.five_hour` / `rate_limits.seven_day` with `used_percentage`. Switching on is opt-in: the
statusLine command in ~/.claude/settings.json is pointed at `ClaudeSwitcher.exe --statusline`, which records
the percentages here and then runs the previous statusLine command unchanged.

This only tells you where you stand and suggests a switch. It never switches by itself.
"""

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import switcher

SETTINGS = Path.home() / ".claude" / "settings.json"
WINDOWS = ("five_hour", "seven_day")
WINDOW_LABEL = {"five_hour": "5-hour", "seven_day": "7-day"}
STALE_AFTER = 1800  # seconds; usage only updates while a session is open


def _path():
    return switcher.STORE.with_name("usage.json")


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(raw):
    """Store the active account's usage from one statusline payload. True if it carried rate limits."""
    try:
        doc = json.loads(raw.lstrip("﻿"))  # tolerate a UTF-8 BOM from whatever feeds stdin
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    limits = doc.get("rate_limits")
    try:
        entry = {}
        for w in WINDOWS:
            pct = limits[w]["used_percentage"]
            entry[w] = {"pct": float(pct), "resets_at": limits[w].get("resets_at")}
        uid = switcher.current()
    except (ValueError, KeyError, TypeError, AttributeError):
        _note_missing(doc, limits)
        return False
    if not uid:
        return False
    data = load()
    data[uid] = {**entry, "ts": time.time()}
    data.pop(DIAG, None)  # numbers are flowing, the explanation is no longer needed
    switcher._write(_path(), data)
    return True


DIAG = "_diag"


def _note_missing(doc, limits):
    """Remember, once, which keys Claude Code sent when there were no usable rate limits (names only, no values),
    so the dashboard can say why there are no numbers (plan or Claude Code version)."""
    seen = {"keys": sorted(doc), "rate_limits": sorted(limits) if isinstance(limits, dict) else None}
    data = load()
    if data.get(DIAG, {}).get("seen") != seen:
        data[DIAG] = {"seen": seen, "ts": time.time()}
        switcher._write(_path(), data)


def diag():
    """What the last payload without usable rate limits looked like, or None."""
    return load().get(DIAG)


def _usage_of(uid, data):
    e = data.get(uid) or {}
    return {w: (e.get(w) or {}).get("pct") for w in WINDOWS}, e.get("ts")


def for_dashboard(uid):
    """{five_hour, seven_day, age_seconds} for one account, or None when nothing was seen yet."""
    pct, ts = _usage_of(uid, load())
    if ts is None or all(v is None for v in pct.values()):
        return None
    return {**pct, "age": max(0, int(time.time() - ts))}


def _age_text(seconds):
    return f"{seconds // 3600}h ago" if seconds >= 3600 else f"{max(1, seconds // 60)}m ago"


def check(notified, now=None):
    """Messages for the active account crossing 90% / 95%. `notified` remembers what was already said
    (per window, reset time and level) and is cleared for a window once usage falls back below 90%."""
    now = now or time.time()
    data, cur = load(), switcher.current()
    entry = data.get(cur)
    if not entry or now - entry.get("ts", 0) > STALE_AFTER:
        return []
    accounts, _ = switcher.details()
    me = next((a for a in accounts if a["uuid"] == cur), None)
    name = (me["label"] or me["email"]) if me else "Active account"
    out = []
    for w in WINDOWS:
        pct = (entry.get(w) or {}).get("pct")
        if pct is None:
            continue
        level = 95 if pct >= 95 else 90 if pct >= 90 else 0
        if not level:
            notified.difference_update({k for k in notified if k[:2] == (cur, w)})
            continue
        key = (cur, w, str((entry.get(w) or {}).get("resets_at")), level)
        if key in notified:
            continue
        notified.add(key)
        best = None
        for a in accounts:
            if a["uuid"] == cur or a["expired"]:
                continue
            p, ts = _usage_of(a["uuid"], data)
            if p[w] is not None and (best is None or p[w] < best[1]):
                best = (a["label"] or a["email"], p[w], now - ts)
        hint = (f" {best[0]} was at {best[1]:.0f}% ({_age_text(int(best[2]))})." if best else "") + \
               " Press Ctrl+Alt+Shift+S to switch."
        out.append(f"{name}: {pct:.0f}% of the {WINDOW_LABEL[w]} limit used.{hint}")
    return out


# --- statusLine hook -------------------------------------------------------------------------------------

def _command():
    return f'"{sys.executable}" --statusline'


def _settings_write(data):
    """Atomic write; the first version of settings.json is kept as settings.json.switcher-bak, never overwritten."""
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    bak = SETTINGS.with_name(SETTINGS.name + ".switcher-bak")
    if SETTINGS.exists() and not bak.exists():
        bak.write_bytes(SETTINGS.read_bytes())
    tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS)


def _settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def enabled():
    try:
        return "--statusline" in (_settings().get("statusLine") or {}).get("command", "")
    except (OSError, ValueError):
        return False


def enable(command=None):
    """Point statusLine at our hook and remember the previous statusLine so it keeps running and can be restored."""
    command = command or _command()
    with switcher._LOCK:
        s = _settings()
        cur = s.get("statusLine")
        if enabled():
            return
        switcher.set_setting("statusline_original", cur)
        s["statusLine"] = {**(cur or {}), "type": "command", "command": command}
        _settings_write(s)
        switcher.set_setting("statusline_written", _sha(SETTINGS))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def disable():
    with switcher._LOCK:
        s = _settings()
        if not enabled():
            return
        bak = SETTINGS.with_name(SETTINGS.name + ".switcher-bak")
        if bak.exists() and switcher.get_setting("statusline_written") == _sha(SETTINGS):
            # Nothing else changed since we wrote it: give back the original file byte for byte.
            tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
            tmp.write_bytes(bak.read_bytes())
            os.replace(tmp, SETTINGS)
            return
        orig = switcher.get_setting("statusline_original")
        if orig:
            s["statusLine"] = orig
        else:
            s.pop("statusLine", None)
        _settings_write(s)


def _chain_command():
    orig = switcher.get_setting("statusline_original") or {}
    return orig.get("command") if orig.get("type", "command") == "command" else None


# --- entry point used by the exe (windowed build has no sys.stdin / sys.stdout) --------------------------

def _std(handle_id):
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = wintypes.HANDLE
    h = k32.GetStdHandle(handle_id)
    return k32, (None if h in (None, -1, ctypes.c_void_p(-1).value) else h)


def _read_stdin():
    k32, h = _std(-10)
    if h is None:
        return b""
    chunks, buf, n = [], ctypes.create_string_buffer(65536), wintypes.DWORD()
    while k32.ReadFile(h, buf, 65536, ctypes.byref(n), None) and n.value:
        chunks.append(buf.raw[:n.value])
    return b"".join(chunks)


def _write_stdout(data):
    k32, h = _std(-11)
    n = wintypes.DWORD()
    if h is not None and data:
        k32.WriteFile(h, data, len(data), ctypes.byref(n), None)


def statusline_main(read=_read_stdin, write=_write_stdout):
    raw = read()
    try:
        record(raw.decode("utf-8", "replace"))
    except Exception:
        pass  # a statusline must never fail because of us
    chain = _chain_command()
    if chain:
        try:
            # shell=True on purpose: `chain` is the user's own statusLine command string from their settings.json
            # (quotes and all), run the same way Claude Code runs it. No outside input reaches it.
            r = subprocess.run(chain, input=raw, capture_output=True, shell=True, timeout=5,
                               creationflags=switcher._NO_WINDOW)
            write(r.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
