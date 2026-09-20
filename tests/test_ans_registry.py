"""The public GoDaddy ANS registration shown next to each linked agent (informational; never affects trust).

Run from the repo root:  python -m unittest tests.test_ans_registry -v   (no network)
"""
import json
import os
import unittest
import warnings
from unittest.mock import patch

from fastapi.testclient import TestClient

from common import agents_config, ans_registry

BADGE = "v=ans-badge1; version=v1.0.0; url=https://transparency.ans.godaddy.com/v1/agents/abc"
ENTRY = {
    "merkleProof": {"leafIndex": 348783, "treeSize": 348854, "path": ["x"]},
    "payload": {"producer": {"event": {
        "ansId": "6f8940c7", "ansName": "ans://v1.0.0.agent.scriptsync.health", "eventType": "AGENT_REGISTERED",
        "agent": {"host": "agent.scriptsync.health", "name": "scriptsync-brand-a", "version": "v1.0.0"}}}},
}


def resolver(values=(BADGE,), how="port-53"):
    return lambda name: (list(values) if values is not None else None, how)


def fetch(status=200, body=ENTRY, raw=None):
    payload = raw if raw is not None else json.dumps(body).encode()
    return lambda url: (status, payload)


class BadgeParsingTests(unittest.TestCase):
    def test_a_goDaddy_badge_url_is_accepted(self):
        self.assertEqual(ans_registry.parse_badge_txt([BADGE]), "https://transparency.ans.godaddy.com/v1/agents/abc")

    def test_urls_off_goDaddys_host_are_rejected(self):
        for url in ("https://evil.example/v1/agents/x", "http://transparency.ans.godaddy.com/v1/agents/x",
                    "https://transparency.ans.godaddy.com.evil.example/x", "https://godaddy.com/x"):
            self.assertIsNone(ans_registry.parse_badge_txt([f"v=ans-badge1; url={url}"]), url)

    def test_other_txt_records_are_ignored(self):
        self.assertIsNone(ans_registry.parse_badge_txt(["v=spf1 -all", "v=agentkey1; k=abc"]))
        self.assertIsNone(ans_registry.parse_badge_txt([]))
        self.assertEqual(ans_registry.parse_badge_txt(["v=spf1 -all", BADGE]),
                         "https://transparency.ans.godaddy.com/v1/agents/abc")

    def test_entry_parsing_is_defensive(self):
        self.assertEqual(ans_registry.parse_entry({})["registeredName"], None)
        got = ans_registry.parse_entry(ENTRY)
        self.assertEqual((got["registeredName"], got["leafIndex"], got["treeSize"], got["eventType"]),
                         ("scriptsync-brand-a", 348783, 348854, "AGENT_REGISTERED"))


class LookupTests(unittest.TestCase):
    def setUp(self):
        ans_registry._CACHE.clear()

    def test_success(self):
        got = ans_registry.lookup("Agent.ScriptSync.Health", resolver(), fetch())
        self.assertTrue(got["ok"])
        self.assertEqual(got["ansName"], "ans://v1.0.0.agent.scriptsync.health")
        self.assertEqual(got["registeredName"], "scriptsync-brand-a")
        self.assertEqual(got["url"], "https://transparency.ans.godaddy.com/v1/agents/abc")

    def test_a_badge_that_points_at_someone_elses_entry_is_rejected(self):
        got = ans_registry.lookup("other.scriptsync.health", resolver(), fetch())
        self.assertFalse(got["ok"])
        self.assertIn("different host", got["error"])

    def test_every_failure_is_a_clean_error_never_an_exception(self):
        cases = {
            "no dns": (resolver(None, "blocked"), fetch()),
            "no badge": (resolver(["v=spf1 -all"]), fetch()),
            "not found": (resolver(), fetch(status=404)),
            "not json": (resolver(), fetch(raw=b"<html>")),
            "too big": (resolver(), fetch(raw=b"x" * (ans_registry.MAX_BYTES + 1))),
            "no name": (resolver(), fetch(body={"payload": {"producer": {"event": {"agent": {"host": "agent.scriptsync.health"}}}}})),
        }
        for label, (res, fet) in cases.items():
            ans_registry._CACHE.clear()
            got = ans_registry.lookup("agent.scriptsync.health", res, fet)
            self.assertFalse(got["ok"], label)
            self.assertTrue(got["error"], label)

        def boom(_):
            raise OSError("down")
        ans_registry._CACHE.clear()
        self.assertFalse(ans_registry.lookup("agent.scriptsync.health", resolver(), boom)["ok"])
        ans_registry._CACHE.clear()
        self.assertFalse(ans_registry.lookup("agent.scriptsync.health", boom, fetch())["ok"])

    def test_results_are_cached(self):
        calls = []

        def counting(url):
            calls.append(url)
            return 200, json.dumps(ENTRY).encode()
        ans_registry.lookup("agent.scriptsync.health", resolver(), counting)
        ans_registry.lookup("agent.scriptsync.health", resolver(), counting)
        self.assertEqual(len(calls), 1)


class LinkingTests(unittest.TestCase):
    def test_it_is_off_unless_the_team_links_an_agent(self):
        agents = agents_config.load_agents(environ={})
        self.assertFalse(any("ansHost" in a for a in agents))

    def test_env_links_a_label_agent_to_its_registered_host(self):
        env = {"SCRIPTSYNC_ANS_HOST_SIMVASTATIN": " Agent.ScriptSync.Health "}
        by_drug = {a["drug"]: a for a in agents_config.load_agents(environ=env) if a["role"] == "brand"}
        self.assertEqual(by_drug["simvastatin"]["ansHost"], "agent.scriptsync.health")
        self.assertNotIn("ansHost", by_drug["clarithromycin"])

    def test_impostors_are_never_linked(self):
        env = {"SCRIPTSYNC_ANS_HOST_SIMVASTATIN": "agent.scriptsync.health"}
        self.assertFalse(any("ansHost" in a for a in agents_config.load_agents(environ=env) if a["role"] == "attacker"))

    def test_agents_endpoint_carries_the_registration_only_for_linked_agents(self):
        warnings.simplefilter("ignore")
        from assistant import server
        verified = {"name": "x", "ok": True, "mode": "live-dns", "checks": [], "warnings": []}
        reg = {"ok": True, "host": "agent.scriptsync.health", "registeredName": "scriptsync-brand-a"}
        with patch.dict(os.environ, {"SCRIPTSYNC_ANS_HOST_SIMVASTATIN": "agent.scriptsync.health"}), \
                patch.object(server, "is_agent_verified", return_value=verified), \
                patch.object(ans_registry, "lookup", return_value=reg) as lookup:
            got = {a["drug"]: a for a in TestClient(server.app).get("/agents").json()}
        self.assertEqual(got["simvastatin"]["ansRegistry"], reg)
        self.assertIsNone(got["clarithromycin"]["ansRegistry"])
        lookup.assert_called_once_with("agent.scriptsync.health")


if __name__ == "__main__":
    unittest.main()
