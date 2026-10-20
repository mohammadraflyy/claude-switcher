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

    def test_cross_site_and_rebinding_blocked(self):
        switcher.save_current()
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Host": "evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(len(switcher.accounts()), 1)


if __name__ == "__main__":
    unittest.main()
