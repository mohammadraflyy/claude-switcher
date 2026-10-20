# Claude Switcher

Keep several Claude Code logins in the Windows system tray and switch between them without logging in again.

Website: **https://mohammadraflyy.github.io/claude-switcher/**

## Download

Get `ClaudeSwitcher-Setup-x.y.z.exe` from **[Releases](https://github.com/mohammadraflyy/claude-switcher/releases/latest)** and run it. No admin rights needed.

The installer is not code-signed, so Windows SmartScreen may warn you: click **More info, then Run anyway**. Each release shows the installer's SHA-256 so you can verify the download.

## Use

1. Log in to your first account the usual way: `claude /login`.
2. Open **Claude Switcher** from the Start Menu. It lives in the system tray (bottom right; it may be under the `^` arrow). Click the icon to open the dashboard.
3. Press **Save current login**.
4. Run `claude /login` with your other account, then press **Save current login** again.
5. Pick an account in the dashboard (or right-click the tray icon, then **Accounts**) to switch.

New `claude` sessions use the active account. A session that is already running keeps the account it started with, so open a new one after switching. The dashboard shows how many are running and asks before you switch (native and npm installs).

Also in the dashboard and tray:

- **Next account:** `Ctrl+Alt+Shift+S` or the tray menu cycles through your saved accounts, skipping expired ones.
- **Labels and colors** to tell accounts apart.
- **Expired logins** are marked; log in again with `claude /login` and save.
- **Backup / Restore** of the saved accounts. The file stays DPAPI-encrypted, so it only restores for the same Windows user.
- **Remove account** from the tray menu.
- **Unsaved login notice:** after `claude /login` the tray reminds you to save the new login.
- **Update check:** every 6 hours the tray makes one anonymous request to the GitHub Releases API and shows a banner when a newer version exists. Nothing about you is sent. Switch it off with **Check for updates automatically** in the tray menu.

## How it works

Claude Code keeps its login in `~/.claude/.credentials.json` (`claudeAiOauth`) and the account identity in `~/.claude.json` (`oauthAccount`). Switching swaps those two entries. Saved logins are encrypted with Windows DPAPI in `%APPDATA%\ClaudeSwitcher\accounts.json`, so only your Windows user can read them.

- Before each switch, the current login is saved back to its slot, so a refreshed token is not lost. While the tray runs, the active account's copy is also refreshed every 30 seconds.
- MCP tokens (`mcpOAuth`) are not touched.
- Writes are atomic and keep a `.bak` of the previous file.
- The dashboard listens on `127.0.0.1` only and never sends tokens to the page. Requests from other sites are rejected.

## Please note

- Use it with **your own** accounts. Rotating accounts to get around rate limits or sharing a login with other people is against Anthropic's terms and can get an account banned.
- This is an unofficial tool, not affiliated with Anthropic. It depends on how Claude Code stores its login today; a Claude Code update can change that.
- Windows only.

## Build from source

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

This runs the tests, builds the exe with PyInstaller, and builds the installer if [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed (`winget install JRSoftware.InnoSetup`). Output: `dist\ClaudeSwitcher-Setup-<version>.exe`.

Run without installing: `pip install -r requirements.txt`, then `python tray.py`. Tests: `python -m unittest test_switcher`.

## Releasing

Bump `__version__` in `switcher.py`, add `docs/release-notes/v<version>.md`, then push a tag `v<version>`. GitHub Actions builds the installer and creates the release.

## License

[MIT](LICENSE)
