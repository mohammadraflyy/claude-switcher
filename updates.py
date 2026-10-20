"""Update check against GitHub Releases: one anonymous HTTPS GET, nothing is sent about you."""

import json
import re
import urllib.request

import switcher

REPO = "mohammadraflyy/claude-switcher"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_PREFIX = f"https://github.com/{REPO}/"

available = None  # (version, page_url) once a newer release was found; read by tray and dashboard


def _ver(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def latest():
    """(version, page_url) of the latest release, or None on any failure or unexpected URL."""
    try:
        req = urllib.request.Request(API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": f"ClaudeSwitcher/{switcher.__version__}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        url = d["html_url"]
        return (d["tag_name"].lstrip("v"), url) if url.startswith(PAGE_PREFIX) else None
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def check():
    """Look for a newer release; remember it in `available`. Returns it, or None."""
    global available
    got = latest()
    if got and _ver(got[0]) > _ver(switcher.__version__):
        available = got
    return available
