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

    def login(self, n, tok):
        cred = json.loads(switcher.CRED.read_text()) if switcher.CRED.exists() else {"mcpOAuth": {"k": 1}}
        cred["claudeAiOauth"] = {"accessToken": tok}
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

    def test_cross_site_and_rebinding_blocked(self):
        switcher.save_current()
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Host": "evil.test"})[0], 403)
        self.assertEqual(self.call("POST", "/api/remove", {"uuid": "u1"}, {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(len(switcher.accounts()), 1)


if __name__ == "__main__":
    unittest.main()
