"""Brand agents: one drug label each, ANS-verifiable identity.

Loads .env on import so ANTHROPIC_API_KEY and the ANS_* settings are available
whether the agent is started from a shell, a script, or a test.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional; env vars still work
    pass
