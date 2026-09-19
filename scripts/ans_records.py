"""Print the DNS TXT records to publish for each brand agent.

    ANS_DOMAIN=scriptsync.health .venv/bin/python scripts/ans_records.py

Add each record in your registrar's DNS panel (Porkbun: Details -> DNS Records),
then verify with:

    dig +short TXT _agentid.simvastatin.scriptsync.health
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # allow running as `python scripts/ans_records.py`

from brand_agent.ans import ANSName, AgentIdentity  # noqa: E402
DOMAIN = os.environ.get("ANS_DOMAIN", "scriptsync.health")

print(f"Domain: {DOMAIN}\n")
for label_file in sorted((ROOT / "labels").glob("*.json")):
    drug = json.loads(label_file.read_text())["agent"]["drug"]
    name = ANSName.build(drug, DOMAIN)
    identity = AgentIdentity.load_or_create(name)
    print(f"--- {drug} ---")
    print(f"  ANS name : {name.full}")
    print(f"  Type     : TXT")
    print(f"  Host     : {name.txt_host}")
    print(f"  Answer   : {identity.txt_value()}")
    print(f"  TTL      : 600")
    print(f"  Verify   : dig +short TXT {name.txt_record}")
    print()
