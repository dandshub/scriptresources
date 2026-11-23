"""Point the server at a throwaway database before it is imported."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="macstate-test-")
os.environ["MACSTATE_DB"] = os.path.join(_tmp, "test.db")
os.environ["MACSTATE_AGENT_DIR"] = os.path.join(_tmp, "build")
os.environ["MACSTATE_ADMIN_PASSWORD"] = "admin-password"
os.environ["MACSTATE_SCHEDULER"] = "0"
