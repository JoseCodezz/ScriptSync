"""Brand agents: one drug label each, ANS-verifiable identity.

Loads .env on import so ANTHROPIC_API_KEY and the ANS_* settings are available
whether the agent is started from a shell, a script, or a test.
"""

import warnings
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    # Swallowing this silently means a populated .env is ignored and the agent
    # quietly runs without its API key - which looks like a bad key, not a
    # missing package. Say so.
    if _ENV_FILE.exists():
        warnings.warn(
            f"{_ENV_FILE.name} exists but python-dotenv is not installed, so it "
            "was NOT loaded. Run: pip install -r requirements.txt",
            RuntimeWarning,
            stacklevel=2,
        )
else:
    load_dotenv(_ENV_FILE)
