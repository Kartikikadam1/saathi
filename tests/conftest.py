import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

from app import seed  # noqa: E402

seed.ensure_seeded()
