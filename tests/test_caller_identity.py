"""Mutual authentication: the agent checking who is calling it.

Uses a stub DNS resolver so the crypto is covered without depending on a
published record or a working network.
"""

import json
import time
import unittest
from unittest.mock import patch

from brand_agent.ans import ANSName, AgentIdentity
from common import caller_identity

ASSISTANT = "a2a://hcpAssistant.consult.assistant.v1.0.0.scriptsync.health"
AGENT_A = "a2a://labelAgent.drugInfo.simvastatin.v1.0.0.scriptsync.health"
AGENT_B = "a2a://labelAgent.drugInfo.clarithromycin.v1.0.0.scriptsync.health"


class CallerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = AgentIdentity.load_or_create(ANSName.parse(ASSISTANT))
        self.body = json.dumps({"question": "interaction?"}, separators=(",", ":")).encode()
        # Stand in for DNS: publish the assistant's real key at its record.
        published = f"v=agentkey1; k={self.identity.public_key_b64}"
        self.resolver = lambda host: (
            ([published], "stub") if host == "_agentid.assistant.scriptsync.health" else (None, "stub")
        )

    def verify(self, headers, target, body, **kw):
        with patch("brand_agent.ans.resolve_txt", self.resolver):
            return caller_identity.verify_request(headers, target, body, **kw)

    def test_a_signed_call_verifies(self):
        headers = caller_identity.sign_request(self.identity, AGENT_A, self.body)
        result = self.verify({k.lower(): v for k, v in headers.items()}, AGENT_A, self.body)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["caller"], ASSISTANT)

    def test_an_unsigned_call_is_rejected(self):
        self.assertFalse(self.verify({}, AGENT_A, self.body)["ok"])

    def test_a_signature_for_one_agent_does_not_work_on_another(self):
        """Binding the target stops a captured call being replayed sideways."""
        headers = caller_identity.sign_request(self.identity, AGENT_A, self.body)
        result = self.verify({k.lower(): v for k, v in headers.items()}, AGENT_B, self.body)
        self.assertFalse(result["ok"])

    def test_the_question_cannot_be_swapped_under_a_valid_signature(self):
        headers = caller_identity.sign_request(self.identity, AGENT_A, self.body)
        other = json.dumps({"question": "is it safe?"}, separators=(",", ":")).encode()
        self.assertFalse(self.verify({k.lower(): v for k, v in headers.items()}, AGENT_A, other)["ok"])

    def test_an_old_call_is_rejected(self):
        headers = {k.lower(): v for k, v in
                   caller_identity.sign_request(self.identity, AGENT_A, self.body).items()}
        future = int(time.time() * 1000) + caller_identity.MAX_AGE_MS + 5_000
        result = self.verify(headers, AGENT_A, self.body, now_ms=future)
        self.assertFalse(result["ok"])
        self.assertIn("stale", result["reason"])

    def test_a_caller_cannot_present_its_own_key(self):
        """An impostor signs with a key the domain does not publish."""
        impostor = AgentIdentity.load_or_create(
            ANSName.parse("a2a://hcpAssistant.consult.impostor.v1.0.0.scriptsync.health"))
        headers = caller_identity.sign_request(impostor, AGENT_A, self.body)
        headers["X-Agent-Identity"] = ASSISTANT  # claim to be the real assistant
        result = self.verify({k.lower(): v for k, v in headers.items()}, AGENT_A, self.body)
        self.assertFalse(result["ok"])
        self.assertIn("signature", result["reason"])

    def test_an_unknown_caller_has_no_published_key(self):
        stranger = AgentIdentity.load_or_create(
            ANSName.parse("a2a://hcpAssistant.consult.stranger.v1.0.0.scriptsync.health"))
        headers = caller_identity.sign_request(stranger, AGENT_A, self.body)
        self.assertFalse(self.verify({k.lower(): v for k, v in headers.items()}, AGENT_A, self.body)["ok"])

    def test_both_sides_build_the_same_payload(self):
        payload = caller_identity.caller_payload(ASSISTANT, AGENT_A, 1758300000000, "abc")
        self.assertEqual(payload, f"agent-caller-v1|{ASSISTANT}|{AGENT_A}|1758300000000|abc")


if __name__ == "__main__":
    unittest.main()
