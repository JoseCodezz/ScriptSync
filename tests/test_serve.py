"""The one-command start (scripts/serve.py) and the web UI served by the assistant.

Run from the repo root:  python -m unittest tests.test_serve -v
No network. One test really starts an agent process on a spare local port, then stops it.
"""
import base64
import importlib
import importlib.util
import os
import socket
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("serve", ROOT / "scripts" / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)

AGENTS = [
    {"id": "a", "role": "brand", "drug": "simvastatin", "endpoint": "http://127.0.0.1:9001",
     "ansName": "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync.health"},
    {"id": "b", "role": "brand", "drug": "warfarin-sodium", "endpoint": "https://agents.example.com/warfarin",
     "ansName": "a2a://labelAgent.drugInfo.warfarin.v1.0.0.scriptsync.health"},
    {"id": "x", "role": "attacker", "attack": "lookalike", "drug": "simvastatin", "endpoint": "http://127.0.0.1:9101",
     "ansName": "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync-labels.health"},
]


def load_server(serve_web: str | None):
    if serve_web is None:
        os.environ.pop("SCRIPTSYNC_SERVE_WEB", None)
    else:
        os.environ["SCRIPTSYNC_SERVE_WEB"] = serve_web
    import assistant.server as server
    with patch("dotenv.load_dotenv", lambda *a, **k: False):
        return importlib.reload(server)


class PlanTests(unittest.TestCase):
    def test_product_mode_starts_only_local_agents(self):
        plan = serve.build_plan(AGENTS, demo=False, python="py")
        self.assertEqual([p["name"] for p in plan], ["agent-simvastatin"])
        self.assertEqual(plan[0]["argv"], ["py", "-m", "brand_agent", "--label", "labels/simvastatin.json",
                                           "--port", "9001", "--domain", "scriptsync.health"])
        self.assertEqual(plan[0]["health"], "http://127.0.0.1:9001/health")

    def test_demo_mode_adds_impostors(self):
        names = [p["name"] for p in serve.build_plan(AGENTS, demo=True, python="py")]
        self.assertEqual(names, ["agent-simvastatin", "impostor-lookalike"])
        imp = serve.build_plan(AGENTS, demo=True, python="py")[1]
        self.assertIn("--mode", imp["argv"])
        self.assertIn("lookalike", imp["argv"])

    def test_real_config_plans_both_label_agents(self):
        plan = serve.build_plan(serve.load_config(), demo=False)
        self.assertEqual({p["port"] for p in plan}, {9001, 9002})


class KeyTests(unittest.TestCase):
    def test_key_env_var_names_are_shell_safe(self):
        self.assertEqual(serve.key_env_var("simvastatin"), "ANS_KEY_SIMVASTATIN_PEM_B64")
        self.assertEqual(serve.key_env_var("warfarin-sodium"), "ANS_KEY_WARFARIN_SODIUM_PEM_B64")

    def test_keys_are_written_from_env_and_never_overwritten(self):
        pem = b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
        env = {"ANS_KEY_SIMVASTATIN_PEM_B64": base64.b64encode(pem).decode()}
        with tempfile.TemporaryDirectory() as d:
            written = serve.provision_keys(AGENTS, env, Path(d))
            self.assertEqual(written, ["simvastatin.ed25519"])
            self.assertEqual((Path(d) / "simvastatin.ed25519").read_bytes(), pem)
            (Path(d) / "simvastatin.ed25519").write_bytes(b"existing")
            self.assertEqual(serve.provision_keys(AGENTS, env, Path(d)), [])
            self.assertEqual((Path(d) / "simvastatin.ed25519").read_bytes(), b"existing")

    def test_remote_agents_and_impostors_get_no_keys(self):
        env = {"ANS_KEY_WARFARIN_SODIUM_PEM_B64": "eA==", "ANS_KEY_SIMVASTATIN_PEM_B64": ""}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(serve.provision_keys(AGENTS, env, Path(d)), [])
            self.assertEqual(list(Path(d).iterdir()), [])


class WebServedByAssistantTests(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")

    def tearDown(self):
        os.environ.pop("SCRIPTSYNC_SERVE_WEB", None)

    def test_page_and_api_share_one_origin(self):
        client = TestClient(load_server(None).app)
        index = client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("ScriptSync Pulse", index.text)
        for asset in ("/app.js", "/style.css", "/config.js"):
            self.assertEqual(client.get(asset).status_code, 200, asset)
        self.assertTrue(client.get("/health").json()["ok"])       # API routes still win over the page
        self.assertEqual(client.get("/docs").status_code, 200)

    def test_serving_the_page_can_be_switched_off(self):
        client = TestClient(load_server("0").app)
        self.assertEqual(client.get("/").status_code, 404)
        self.assertTrue(client.get("/health").json()["ok"])

    def test_page_uses_its_own_origin_unless_told_otherwise(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.SCRIPTSYNC_API", js)
        self.assertIn("location.origin", js)


class RealLaunchTests(unittest.TestCase):
    def test_the_planned_command_really_starts_an_agent(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        agent = dict(AGENTS[0], endpoint=f"http://127.0.0.1:{port}")
        item = serve.build_plan([agent], demo=False)[0]
        proc = subprocess.Popen(item["argv"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(serve.wait_for(item, proc, timeout=60), "agent did not come up")
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
