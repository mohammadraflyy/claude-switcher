"""System-tray UI: pick an account to switch. Run: pythonw tray.py (or the installed exe)."""

import ctypes
import sys
import webbrowser

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


def _menu(url):
    def pick(uid, email):
        def run(icon, item):
            try:
                switcher.switch(uid)
                icon.notify("New `claude` sessions use this account.", email)
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

    def accounts():
        items = [
            pystray.MenuItem(email, pick(uid, email), checked=lambda item, a=active: a, radio=True)
            for uid, email, active in switcher.accounts()
        ]
        yield from items or [pystray.MenuItem("(none saved yet)", None, enabled=False)]

    return pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda icon, item: webbrowser.open(url), default=True),
        pystray.MenuItem("Accounts", pystray.Menu(accounts)),
        pystray.MenuItem("Save current login", add),
        pystray.MenuItem("Start with Windows", toggle_start, checked=lambda item: autostart_enabled(),
                         visible=getattr(sys, "frozen", False)),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    )


def _single_instance():
    h = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ClaudeSwitcherTray")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if not _single_instance():
        return
    _, url = dashboard.start()
    if not switcher.accounts():
        webbrowser.open(url)  # first run: show where to start
    pystray.Icon("claude-switcher", _icon(), "Claude Switcher", _menu(url)).run()


if __name__ == "__main__":
    main()
