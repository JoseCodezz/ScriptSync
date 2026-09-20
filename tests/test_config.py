"""Shared config helpers: endpoint overrides for a hosted site, and agent keys from the environment.

Run from the repo root:  python -m unittest tests.test_config -v   (no network)
"""
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import agents_config, keys


class EndpointOverrideTests(unittest.TestCase):
    def test_env_var_names_are_shell_safe(self):
        self.assertEqual(agents_config.endpoint_env_var("simvastatin"), "SCRIPTSYNC_ENDPOINT_SIMVASTATIN")
        self.assertEqual(agents_config.endpoint_env_var("warfarin-sodium"), "SCRIPTSYNC_ENDPOINT_WARFARIN_SODIUM")

    def test_no_override_leaves_the_config_alone(self):
        agents = agents_config.load_agents(environ={})
        self.assertEqual({a["endpoint"] for a in agents if a["role"] == "brand"},
                         {"http://127.0.0.1:9001", "http://127.0.0.1:9002"})

    def test_override_relocates_a_label_agent_and_strips_a_trailing_slash(self):
        env = {"SCRIPTSYNC_ENDPOINT_SIMVASTATIN": "https://agent.scriptsync.health/"}
        by_drug = {a["drug"]: a for a in agents_config.load_agents(environ=env) if a["role"] == "brand"}
        self.assertEqual(by_drug["simvastatin"]["endpoint"], "https://agent.scriptsync.health")
        self.assertEqual(by_drug["clarithromycin"]["endpoint"], "http://127.0.0.1:9002")   # untouched

    def test_impostors_are_never_relocated(self):
        env = {"SCRIPTSYNC_ENDPOINT_SIMVASTATIN": "https://elsewhere.example"}
        attackers = [a for a in agents_config.load_agents(environ=env) if a["role"] == "attacker"]
        self.assertTrue(attackers and all(a["endpoint"].startswith("http://127.0.0.1:") for a in attackers))

    def test_verification_and_the_assistant_see_the_same_endpoint(self):
        """If these disagreed, verification would challenge one URL while the assistant asks another."""
        env = {"SCRIPTSYNC_ENDPOINT_SIMVASTATIN": "https://agent.scriptsync.health"}
        with patch.dict(os.environ, env):
            from assistant import server, verify
            got_server = {a["drug"]: a["endpoint"] for a in server.load_agents() if a["role"] == "brand"}
            got_verify = {a["drug"]: a["endpoint"] for a in verify._load_agents() if a["role"] == "brand"}
        self.assertEqual(got_server, got_verify)
        self.assertEqual(got_server["simvastatin"], "https://agent.scriptsync.health")


class KeyProvisioningTests(unittest.TestCase):
    PEM = b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    def env(self):
        return {"ANS_KEY_SIMVASTATIN_PEM_B64": base64.b64encode(self.PEM).decode()}

    def test_key_env_var_name(self):
        self.assertEqual(keys.key_env_var("simvastatin"), "ANS_KEY_SIMVASTATIN_PEM_B64")

    def test_writes_the_key_once_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(keys.provision_key("simvastatin", self.env(), Path(d)))
            self.assertEqual(keys.key_path("simvastatin", Path(d)).read_bytes(), self.PEM)
            keys.key_path("simvastatin", Path(d)).write_bytes(b"existing")
            self.assertFalse(keys.provision_key("simvastatin", self.env(), Path(d)))
            self.assertEqual(keys.key_path("simvastatin", Path(d)).read_bytes(), b"existing")

    def test_nothing_is_written_without_the_env_var(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(keys.provision_key("simvastatin", {}, Path(d)))
            self.assertFalse(keys.provision_key("simvastatin", {"ANS_KEY_SIMVASTATIN_PEM_B64": "  "}, Path(d)))
            self.assertEqual(list(Path(d).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
