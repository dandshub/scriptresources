"""Runtime configuration, read from environment variables at import time."""
from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SQLite database file.
DB_PATH = os.environ.get("MACSTATE_DB", os.path.join(_ROOT, "data", "macstate.db"))
# Directory holding compiled agent binaries (see build-agent.sh).
AGENT_DIR = os.environ.get("MACSTATE_AGENT_DIR", os.path.join(_ROOT, "build"))
# Public base URL agents use to reach this server, e.g. https://macstate.example.com.
# When unset it is derived from the incoming request.
PUBLIC_URL = os.environ.get("MACSTATE_PUBLIC_URL", "").rstrip("/")
# Initial admin password (only used when no users exist yet).
ADMIN_PASSWORD = os.environ.get("MACSTATE_ADMIN_PASSWORD", "")
# Set to 0 to disable the background scheduler (used by tests).
SCHEDULER = os.environ.get("MACSTATE_SCHEDULER", "1") != "0"
# Set to 0 to skip seeding the sample software/task library on first start.
SEED = os.environ.get("MACSTATE_SEED", "1") != "0"
# Cookies flagged Secure (enable when served over HTTPS).
HTTPS_COOKIES = os.environ.get("MACSTATE_HTTPS_COOKIES", "0") == "1"
# Seconds between agent check-ins.
POLL_SECONDS = int(os.environ.get("MACSTATE_POLL_SECONDS", "60"))
# A computer counts as online if it checked in within this many seconds.
ONLINE_SECONDS = int(os.environ.get("MACSTATE_ONLINE_SECONDS", "300"))
# Running sessions with no progress for this long are marked failed.
SESSION_TIMEOUT = int(os.environ.get("MACSTATE_SESSION_TIMEOUT", str(6 * 3600)))
