"""Demo-only features stay out of the product API, and /health tells the truth.

Run from the repo root:  python -m unittest tests.test_demo_gate -v
No network: verification and agent calls are stubbed.
"""
import importlib
import os
import unittest
import warnings
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

VERIFIED = {"name": "x", "ok": True, "mode": "live-dns", "checks": [], "warnings": []}


def load_server(demo: bool):
    """Import assistant.server fresh with SCRIPTSYNC_DEMO on or off, ignoring any local .env."""
    if demo:
        os.environ["SCRIPTSYNC_DEMO"] = "1"
    else:
        os.environ.pop("SCRIPTSYNC_DEMO", None)
    import assistant.server as server
    with patch("dotenv.load_dotenv", lambda *a, **k: False):
        return importlib.reload(server)


class DemoGateTests(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")   # starlette's TestClient deprecation noise

    def tearDown(self):
        os.environ.pop("SCRIPTSYNC_DEMO", None)

    def test_product_mode_hides_impostors(self):
        server = load_server(demo=False)
        client = TestClient(server.app)
        r = client.post("/attack/lookalike", json={})
        self.assertEqual(r.status_code, 403)
        self.assertIn("SCRIPTSYNC_DEMO=1", r.json()["detail"])
        with patch.object(server, "is_agent_verified", return_value=VERIFIED):
            roles = {a["role"] for a in client.get("/agents").json()}
        self.assertEqual(roles, {"brand"})
        self.assertFalse(client.get("/health").json()["demo"])

    def test_demo_mode_exposes_impostors(self):
        server = load_server(demo=True)
        client = TestClient(server.app)
        with patch.object(server, "query_agent", AsyncMock(return_value={"status": "blocked"})):
            self.assertEqual(client.post("/attack/lookalike", json={}).status_code, 200)
        with patch.object(server, "is_agent_verified", return_value=VERIFIED):
            roles = {a["role"] for a in client.get("/agents").json()}
        self.assertIn("attacker", roles)
        self.assertTrue(client.get("/health").json()["demo"])

    def test_health_reports_what_is_live_and_what_is_simulated(self):
        server = load_server(demo=False)
        h = TestClient(server.app).get("/health").json()
        self.assertEqual(h["verification"], "live-dns")
        self.assertEqual(h["verificationDetail"]["live"], ["dns", "cert"])
        self.assertEqual(h["verificationDetail"]["simulated"], ["log", "current"])
        self.assertIn("dnssecBypass", h["verificationDetail"])
        self.assertEqual(h["signing"], "demo-hmac")

    def test_dnssec_bypass_is_reported(self):
        server = load_server(demo=False)
        with patch.dict(os.environ, {"ANS_ALLOW_UNVALIDATED_DNS": "1"}):
            self.assertTrue(TestClient(server.app).get("/health").json()["verificationDetail"]["dnssecBypass"])
        with patch.dict(os.environ, {"ANS_ALLOW_UNVALIDATED_DNS": "0"}):
            self.assertFalse(TestClient(server.app).get("/health").json()["verificationDetail"]["dnssecBypass"])


if __name__ == "__main__":
    unittest.main()
