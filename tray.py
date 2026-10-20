"""System-tray UI: pick an account to switch. Run: pythonw tray.py (or the installed exe)."""

import ctypes
import sys
import threading
import time
import webbrowser
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw

import dashboard
import switcher

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "ClaudeSwitcher"


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE)
        return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass


def _icon():
    img = Image.new("RGB", (64, 64), "#d97757")
    ImageDraw.Draw(img).ellipse((18, 18, 46, 46), fill="white")
    return img


HOTKEY_TEXT = "Ctrl+Alt+Shift+S"


def next_account(icon, item=None):
    try:
        email = switcher.next_account()
        if not email:
            return icon.notify("No other usable account saved.", "Claude Switcher")
        n = switcher.running_sessions()
        icon.notify("New sessions use this account." + (f" {n} running claude session(s) keep their old one." if n else ""), email)
    except Exception as e:
        icon.notify(str(e), "Switch failed")
    icon.update_menu()


def _hotkey_loop(icon):
    """Global hotkey -> next account. Skipped silently if another app already owns the combination."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x0001 | 0x0002 | 0x0004 | 0x4000, ord("S")):  # ALT|CTRL|SHIFT|NOREPEAT
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            next_account(icon)


def _menu(url):
    def pick(uid, email):
        def run(icon, item):
            try:
                switcher.switch(uid)
                n = switcher.running_sessions()
                icon.notify("New sessions use this account." + (f" {n} running claude session(s) keep their old one." if n else ""), email)
            except Exception as e:  # tray must survive a bad file
                icon.notify(str(e), "Switch failed")
            icon.update_menu()
        return run

    def add(icon, item):
        try:
            icon.notify("Saved", switcher.save_current())
        except Exception as e:
            icon.notify(f"Not logged in? {e}", "Save failed")
        icon.update_menu()

    def toggle_start(icon, item):
        set_autostart(not autostart_enabled())

    def remove(uid, email):
        def run(icon, item):
            ask = f"Remove {email} from Claude Switcher?\nYou stay logged in if it is the active account."
            if ctypes.windll.user32.MessageBoxW(None, ask, "Claude Switcher", 0x40024) == 6:  # TOPMOST|YESNO|QUESTION -> IDYES
                switcher.remove(uid)
                icon.update_menu()
        return run

    def name(a):
        return f"{a['label']} ({a['email']})" if a["label"] else a["email"]

    def accounts():
        items = [
            pystray.MenuItem(name(a) + (" - login expired" if a["expired"] else ""), pick(a["uuid"], a["email"]),
                             checked=lambda item, on=a["active"]: on, radio=True, enabled=not a["expired"])
            for a in switcher.details()[0]
        ]
        yield from items or [pystray.MenuItem("(none saved yet)", None, enabled=False)]

    def removable():
        items = [pystray.MenuItem(name(a), remove(a["uuid"], a["email"])) for a in switcher.details()[0]]
        yield from items or [pystray.MenuItem("(none saved yet)", None, enabled=False)]

    return pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda icon, item: webbrowser.open(url), default=True),
        pystray.MenuItem("Accounts", pystray.Menu(accounts)),
        pystray.MenuItem(f"Next account ({HOTKEY_TEXT})", next_account),
        pystray.MenuItem("Save current login", add),
        pystray.MenuItem("Remove account", pystray.Menu(removable)),
        pystray.MenuItem("Start with Windows", toggle_start, checked=lambda item: autostart_enabled(),
                         visible=getattr(sys, "frozen", False)),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    )


def _single_instance():
    h = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ClaudeSwitcherTray")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _sync_loop(every=30):
    """Keep the active account's saved copy fresh: Claude Code rotates tokens while it runs."""
    while True:
        time.sleep(every)
        switcher.sync_active()


def main():
    if not _single_instance():
        return
    threading.Thread(target=_sync_loop, daemon=True).start()
    _, url = dashboard.start()
    if not switcher.accounts():
        webbrowser.open(url)  # first run: show where to start
    icon = pystray.Icon("claude-switcher", _icon(), "Claude Switcher", _menu(url))
    threading.Thread(target=_hotkey_loop, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
