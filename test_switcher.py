import json
import tempfile
import unittest
from pathlib import Path

import switcher


def acct(n):
    return {"accountUuid": f"u{n}", "emailAddress": f"a{n}@x.test"}


class SwitchTest(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        switcher.CRED = d / "cred.json"
        switcher.IDENT = d / "claude.json"
        switcher.STORE = d / "store" / "accounts.json"
        self.login(1, "tok1")

    def login(self, n, tok, **oauth):
        cred = json.loads(switcher.CRED.read_text()) if switcher.CRED.exists() else {"mcpOAuth": {"k": 1}}
        cred["claudeAiOauth"] = {"accessToken": tok, **oauth}
        switcher.CRED.write_text(json.dumps(cred))
        switcher.IDENT.write_text(json.dumps({"oauthAccount": acct(n), "other": "keep"}))

    def token(self):
        return json.loads(switcher.CRED.read_text())["claudeAiOauth"]["accessToken"]

    def test_switch_roundtrip_and_syncback(self):
        switcher.save_current()          # a1 saved
        self.login(2, "tok2")
        switcher.save_current()          # a2 saved
        switcher.switch("u1")
        self.assertEqual(switcher.current(), "u1")
        self.assertEqual(self.token(), "tok1")
        self.assertEqual(json.loads(switcher.CRED.read_text())["mcpOAuth"], {"k": 1})
        self.assertEqual(json.loads(switcher.IDENT.read_text())["other"], "keep")
        # a1 session rotates its token, then we switch away: new token must be kept
        c = json.loads(switcher.CRED.read_text())
        c["claudeAiOauth"]["accessToken"] = "tok1-rotated"
        switcher.CRED.write_text(json.dumps(c))
        switcher.switch("u2")
        self.assertEqual(self.token(), "tok2")
        switcher.switch("u1")
        self.assertEqual(self.token(), "tok1-rotated")

    def test_store_is_encrypted(self):
        switcher.save_current()
        self.assertNotIn("tok1", switcher.STORE.read_text())


class SyncTest(SwitchTest):
    def saved_token(self, uid):
        return switcher._open(switcher._store()[uid]["blob"])["oauth"]["accessToken"]

    def test_rotated_token_is_synced_to_active_slot(self):
        switcher.save_current()
        self.assertFalse(switcher.sync_active(settle=0))  # nothing changed yet
        self.login(1, "tok1-rotated")                      # claude refreshed the token
        self.assertTrue(switcher.sync_active(settle=0))
        self.assertEqual(self.saved_token("u1"), "tok1-rotated")
        self.assertFalse(switcher.sync_active(settle=0))  # already in sync

    def test_unsaved_account_is_never_stored_by_sync(self):
        self.assertFalse(switcher.sync_active(settle=0))
        self.assertEqual(switcher.accounts(), [])

    def test_half_written_login_is_ignored(self):
        switcher.save_current()
        orig = switcher._snapshot
        calls = []

        def flapping():  # first read sees a login mid-switch, second read sees it settled
            calls.append(1)
            s = orig()
            if len(calls) == 1:
                s["oauth"] = {"accessToken": "someone-elses"}
            return s

        switcher._snapshot = flapping
        self.addCleanup(setattr, switcher, "_snapshot", orig)
        self.assertFalse(switcher.sync_active(settle=0))
        self.assertEqual(self.saved_token("u1"), "tok1")

    def test_running_sessions_is_a_count(self):
        n = switcher.running_sessions()
        self.assertIsInstance(n, int)
        self.assertGreaterEqual(n, 0)


class FeatureTest(SwitchTest):
    def setUp(self):
        super().setUp()
        switcher.save_current()                    # u1
        self.login(2, "tok2")
        switcher.save_current()                    # u2
        self.login(3, "tok3", refreshTokenExpiresAt=1)
        switcher.save_current()                    # u3, refresh token long dead
        switcher.switch("u1")

    def by_uuid(self):
        return {a["uuid"]: a for a in switcher.details()[0]}

    def test_expired_flag(self):
        d = self.by_uuid()
        self.assertTrue(d["u3"]["expired"])
        self.assertFalse(d["u1"]["expired"] or d["u2"]["expired"])

    def test_next_skips_expired_and_wraps(self):
        self.assertEqual(switcher.next_account(), "a2@x.test")
        self.assertEqual(switcher.current(), "u2")
        self.assertEqual(switcher.next_account(), "a1@x.test")  # u3 is expired: skipped, wraps to u1
        self.assertEqual(switcher.current(), "u1")

    def test_next_with_nothing_to_go_to(self):
        switcher.remove("u2")
        self.assertIsNone(switcher.next_account())
        self.assertEqual(switcher.current(), "u1")

    def test_label_survives_resave_and_is_validated(self):
        switcher.set_label("u1", "W" * 40, "#3b82f6")
        self.assertEqual(self.by_uuid()["u1"]["label"], "W" * 24)
        self.login(1, "tok1b")
        switcher.save_current()
        self.assertEqual(self.by_uuid()["u1"]["color"], "#3b82f6")
        with self.assertRaises(ValueError):
            switcher.set_label("u1", "x", "red; background:url(x)")
        with self.assertRaises(KeyError):
            switcher.set_label("nope", "x")

    def test_backup_roundtrip(self):
        data = switcher.export_store()
        self.assertNotIn("tok", json.dumps(data))
        switcher.remove("u2")
        self.assertEqual(switcher.import_store(data), 3)
        self.assertIn("u2", self.by_uuid())

    def test_bad_backups_import_nothing(self):
        before = switcher.STORE.read_text()
        data = switcher.export_store()
        data["accounts"]["u1"]["blob"] = "AAAA"
        for bad in (data, {}, {"accounts": []}, {"accounts": {"x": {"email": "e"}}}, "junk"):
            with self.assertRaises(ValueError):
                switcher.import_store(bad)
        self.assertEqual(switcher.STORE.read_text(), before)


class UpdatesAndNoticesTest(SwitchTest):
    def test_unsaved_email_and_settings(self):
        self.assertEqual(switcher.unsaved_email(), "a1@x.test")
        switcher.save_current()
        self.assertIsNone(switcher.unsaved_email())
        self.assertTrue(switcher.get_setting("update_check", True))
        switcher.set_setting("update_check", False)
        self.assertFalse(switcher.get_setting("update_check", True))

    def test_version_compare_is_numeric(self):
        import updates
        self.assertGreater(updates._ver("1.10.0"), updates._ver("1.9.0"))
        self.assertGreater(updates._ver("v1.2.1"), updates._ver("1.2.0"))

    def test_check_only_reports_newer_release(self):
        import updates
        self.addCleanup(setattr, updates, "available", None)
        orig = updates.latest
        self.addCleanup(setattr, updates, "latest", orig)
        updates.latest = lambda: (switcher.__version__, updates.PAGE_PREFIX + "x")
        self.assertIsNone(updates.check())
        updates.latest = lambda: ("99.0.0", updates.PAGE_PREFIX + "releases/tag/v99.0.0")
        self.assertEqual(updates.check()[0], "99.0.0")

    def test_release_page_outside_the_repo_is_rejected(self):
        import io
        import updates

        class Resp(io.BytesIO):
            __enter__ = lambda self: self
            __exit__ = lambda self, *a: None

        orig = updates.urllib.request.urlopen
        self.addCleanup(setattr, updates.urllib.request, "urlopen", orig)
        body = lambda url: Resp(json.dumps({"tag_name": "v9.0.0", "html_url": url}).encode())
        updates.urllib.request.urlopen = lambda *a, **k: body("https://evil.test/x")
        self.assertIsNone(updates.latest())
        updates.urllib.request.urlopen = lambda *a, **k: body(updates.PAGE_PREFIX + "releases/tag/v9.0.0")
        self.assertEqual(updates.latest()[0], "9.0.0")

    def test_npm_install_sessions_are_counted(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        switcher._npm_cache = (0.0, 0)
        before = switcher._npm_sessions()
        p = subprocess.Popen([node, "-e", "setTimeout(()=>{},20000)", "C:/x/node_modules/@anthropic-ai/claude-code/cli.js"],
                             creationflags=switcher._NO_WINDOW)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        switcher._npm_cache = (0.0, 0)
        self.assertEqual(switcher._npm_sessions(), before + 1)


class UsageTest(SwitchTest):
    def setUp(self):
        super().setUp()
        import usage
        self.usage = usage
        d = Path(tempfile.mkdtemp())
        orig = usage.SETTINGS
        usage.SETTINGS = d / "settings.json"
        self.addCleanup(setattr, usage, "SETTINGS", orig)
        self.settings_dir = d

    def put(self, uid, five, seven, ts=None, resets="r1"):
        data = self.usage.load()
        data[uid] = {"ts": ts or __import__("time").time(),
                     "five_hour": {"pct": five, "resets_at": resets}, "seven_day": {"pct": seven, "resets_at": resets}}
        switcher._write(self.usage._path(), data)

    def test_record_reads_rate_limits_and_ignores_other_payloads(self):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 96, "resets_at": "2026-10-21T10:00:00Z"},
                                   "seven_day": {"used_percentage": 40, "resets_at": 123}}}
        self.assertTrue(self.usage.record(json.dumps(payload)))
        self.assertEqual(self.usage.load()["u1"]["five_hour"]["pct"], 96.0)
        self.assertTrue(self.usage.record("﻿" + json.dumps(payload)))  # BOM-prefixed input still parses
        accounts = lambda: {k: v for k, v in self.usage.load().items() if k != self.usage.DIAG}
        before = accounts()
        for bad in ("{}", "junk", json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5}}}), "[]"):
            self.assertFalse(self.usage.record(bad))
        self.assertEqual(accounts(), before)

    def test_missing_rate_limits_leaves_a_one_time_diagnostic_without_values(self):
        doc = {"model": {"display_name": "SECRET-MODEL"}, "workspace": {"current_dir": "C:/private"}}
        self.assertFalse(self.usage.record(json.dumps(doc)))
        first = self.usage.diag()
        self.assertEqual(first["seen"], {"keys": ["model", "workspace"], "rate_limits": None})
        self.assertNotIn("SECRET-MODEL", self.usage._path().read_text())   # names only, never values
        self.assertNotIn("private", self.usage._path().read_text())
        self.assertFalse(self.usage.record(json.dumps(doc)))
        self.assertEqual(self.usage.diag()["ts"], first["ts"])              # unchanged keys: not rewritten
        self.assertTrue(self.usage.record(json.dumps({"rate_limits": {
            "five_hour": {"used_percentage": 1}, "seven_day": {"used_percentage": 2}}})))
        self.assertIsNone(self.usage.diag())                                # numbers flowing: explanation dropped

    def test_alerts_fire_once_per_level_and_reset_below_90(self):
        switcher.save_current()
        self.login(2, "tok2")
        switcher.save_current()
        switcher.switch("u1")
        self.put("u2", 12.0, 30.0)
        seen = set()
        self.put("u1", 96.0, 40.0)
        first = self.usage.check(seen)
        self.assertEqual(len(first), 1)
        self.assertIn("96%", first[0])
        self.assertIn("5-hour", first[0])
        self.assertIn("a2@x.test was at 12%", first[0])
        self.assertEqual(self.usage.check(seen), [])           # same level, same window: silent
        self.put("u1", 50.0, 40.0)
        self.assertEqual(self.usage.check(seen), [])           # back under 90: forgotten
        self.put("u1", 91.0, 40.0)
        self.assertEqual(len(self.usage.check(seen)), 1)       # crossing again alerts again
        self.put("u1", 97.0, 40.0)
        self.assertEqual(len(self.usage.check(seen)), 1)       # 90 -> 95 is a new level

    def test_stale_usage_is_ignored(self):
        switcher.save_current()
        self.put("u1", 99.0, 99.0, ts=__import__("time").time() - 3 * 3600)
        self.assertEqual(self.usage.check(set()), [])

    def test_enable_disable_keeps_original_statusline_and_old_backup(self):
        original = {"model": "x", "tags": ["a", "b"], "statusLine": {"type": "command", "command": "orig cmd", "padding": 1}}
        original_bytes = json.dumps(original, separators=(",", ":")).encode()  # deliberately not our own formatting
        self.usage.SETTINGS.write_bytes(original_bytes)
        sentinel = self.settings_dir / "settings.json.bak"
        sentinel.write_text("older backup")
        ours = '"C:\\x\\ClaudeSwitcher.exe" --statusline'
        self.usage.enable(ours)
        now = json.loads(self.usage.SETTINGS.read_text())
        self.assertEqual(now["statusLine"], {"type": "command", "command": ours, "padding": 1})
        self.assertEqual(now["model"], "x")
        self.assertTrue(self.usage.enabled())
        bak = self.settings_dir / "settings.json.switcher-bak"
        self.assertEqual(json.loads(bak.read_text()), original)
        self.assertEqual(sentinel.read_text(), "older backup")  # never touch someone else's .bak
        self.usage.enable(ours)                                 # second call changes nothing
        self.assertEqual(json.loads(bak.read_text()), original)
        self.usage.disable()
        self.assertEqual(self.usage.SETTINGS.read_bytes(), original_bytes)  # untouched since: restored byte for byte
        self.assertFalse(self.usage.enabled())

    def test_enable_without_a_previous_statusline_and_disable_removes_it(self):
        self.usage.SETTINGS.write_text(json.dumps({"model": "x"}))
        self.usage.enable('"x.exe" --statusline')
        self.usage.disable()
        self.assertEqual(json.loads(self.usage.SETTINGS.read_text()), {"model": "x"})

    def test_disable_leaves_a_statusline_the_user_changed_since(self):
        self.usage.SETTINGS.write_text(json.dumps({"statusLine": {"type": "command", "command": "orig"}}))
        self.usage.enable('"x.exe" --statusline')
        s = json.loads(self.usage.SETTINGS.read_text())
        s["statusLine"]["command"] = "user changed it"
        self.usage.SETTINGS.write_text(json.dumps(s))
        self.usage.disable()
        self.assertEqual(json.loads(self.usage.SETTINGS.read_text())["statusLine"]["command"], "user changed it")

    def test_statusline_records_and_passes_the_previous_statusline_through(self):
        switcher.save_current()
        switcher.set_setting("statusline_original", {
            "type": "command", "command": 'python -c "import sys; sys.stdout.write(\'OUT:\' + sys.stdin.read()[:6])"'})
        payload = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 7}, "seven_day": {"used_percentage": 8}}})
        out = []
        self.usage.statusline_main(read=lambda: payload.encode(), write=out.append)
        self.assertEqual(out, [b'OUT:{"rate'])
        self.assertEqual(self.usage.load()["u1"]["seven_day"]["pct"], 8.0)

    def test_statusline_survives_garbage_and_a_broken_chain(self):
        switcher.set_setting("statusline_original", {"type": "command", "command": "no-such-program-xyz"})
        out = []
        self.usage.statusline_main(read=lambda: b"\xff\xfe not json", write=out.append)  # must not raise
        self.assertEqual(self.usage.load(), {})

    def test_latest_session_and_resume(self):
        import os
        import subprocess
        root = Path(tempfile.mkdtemp())
        a, b = root / "workA", root / "workB"
        a.mkdir(), b.mkdir()
        switcher.PROJECTS = root / "projects"
        self.addCleanup(setattr, switcher, "PROJECTS", Path.home() / ".claude" / "projects")
        (switcher.PROJECTS / "pa").mkdir(parents=True)
        (switcher.PROJECTS / "pb").mkdir(parents=True)
        old, new = switcher.PROJECTS / "pa" / "old-id.jsonl", switcher.PROJECTS / "pb" / "new-id.jsonl"
        old.write_text(json.dumps({"cwd": str(a)}) + "\n")
        new.write_text("not json\n" + json.dumps({"cwd": str(b)}) + "\n")
        os.utime(old, (1, 1))
        self.assertEqual(switcher.latest_session(), ("new-id", str(b)))
        new.write_text(json.dumps({"cwd": str(root / "gone")}) + "\n")  # newest points at a folder that no longer exists
        self.assertEqual(switcher.latest_session(), ("old-id", str(a)))
        calls = []
        orig = subprocess.Popen
        subprocess.Popen = lambda *args, **kw: calls.append((args, kw))
        self.addCleanup(setattr, subprocess, "Popen", orig)
        self.assertEqual(switcher.resume_latest(), str(a))
        self.assertEqual(calls[0][0][0], ["cmd.exe", "/k", "claude", "--resume", "old-id"])
        self.assertEqual(calls[0][1]["cwd"], str(a))
        old.unlink(), new.unlink()
        with self.assertRaises(ValueError):
            switcher.resume_latest()


class DashboardTest(SwitchTest):
    def setUp(self):
        super().setUp()
        import dashboard
        self.srv, _ = dashboard.start(port=18766)
        self.port = self.srv.server_address[1]
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        return r.status, r.read().decode()

    def test_api_has_no_tokens_and_switch_works(self):
        switcher.save_current()
        self.login(2, "tok2-secret")
        switcher.save_current()
        status, text = self.call("GET", "/api/accounts")
        self.assertEqual(status, 200)
        self.assertNotIn("tok", text)
        self.assertEqual(len(json.loads(text)["accounts"]), 2)
        self.assertEqual(self.call("POST", "/api/switch", {"uuid": "u1"})[0], 200)
        self.assertEqual(switcher.current(), "u1")

    def test_label_backup_restore_endpoints(self):
        switcher.save_current()
        self.assertEqual(self.call("POST", "/api/label", {"uuid": "u1", "label": "Work", "color": "#d97757"})[0], 200)
        self.assertEqual(self.call("POST", "/api/label", {"uuid": "u1", "color": "javascript:1"})[0], 400)
        status, text = self.call("GET", "/api/backup")
        self.assertEqual(status, 200)
        backup = json.loads(text)
        self.assertEqual(backup["accounts"]["u1"]["label"], "Work")
        self.assertNotIn("tok", text)
        self.call("POST", "/api/remove", {"uuid": "u1"})
        self.assertEqual(self.call("POST", "/api/restore", backup)[0], 200)
        self.assertEqual(len(switcher.accounts()), 1)
        self.assertEqual(self.call("POST", "/api/restore", {"accounts": {"u9": {"email": "e", "blob": "AAAA"}}})[0], 400)
        self.assertEqual(self.call("POST", "/api/restore", backup, {"Origin": "http://evil.test"})[0], 403)

    def test_usage_fields_and_switch_with_resume(self):
        switcher.save_current()
        self.login(2, "tok2")
        switcher.save_current()
        status, text = self.call("GET", "/api/accounts")
        j = json.loads(text)
        self.assertIn("usage_enabled", j)
        self.assertTrue(all("usage" in a for a in j["accounts"]))
        self.assertNotIn("tok", text)
        resumed = []
        orig = switcher.resume_latest
        switcher.resume_latest = lambda: resumed.append(1) or "C:/x"
        self.addCleanup(setattr, switcher, "resume_latest", orig)
        self.assertEqual(self.call("POST", "/api/switch", {"uuid": "u1", "resume": True})[0], 200)
        self.assertEqual((switcher.current(), resumed), ("u1", [1]))
        self.assertEqual(self.call("POST", "/api/switch", {"uuid": "u2"})[0], 200)
        self.assertEqual(resumed, [1])                       # plain switch does not open a window
        switcher.resume_latest = lambda: (_ for _ in ()).throw(ValueError("no recent conversation found"))
        status, text = self.call("POST", "/api/switch", {"uuid": "u1", "resume": True})
        self.assertEqual(status, 400)
        self.assertIn("no recent conversation", text)

    def test_cross_site_and_rebinding_blocked(self):
        switcher.save_current()
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Host": "evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(len(switcher.accounts()), 1)


if __name__ == "__main__":
    unittest.main()
