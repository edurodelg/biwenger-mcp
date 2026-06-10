import os
import tempfile
from pathlib import Path


TEST_DIR = Path(tempfile.mkdtemp(prefix="biwenger-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'test.db'}"
os.environ["ENABLE_DAILY_SYNC"] = "false"
os.environ["INTERFACE_MODE"] = "api"
os.environ["API_KEY"] = "test_key"
