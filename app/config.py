"""Runtime configuration, read from environment variables (see .env.example)."""
import os
from zoneinfo import ZoneInfo


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


APP_NAME = "Saathi — Elder Medication Check-in Agent"
TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Kolkata"))

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "saathi.db"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "8"))
# Classifier result below this confidence falls back to the deterministic rule engine.
LLM_MIN_CONFIDENCE = float(os.getenv("LLM_MIN_CONFIDENCE", "0.6"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Call behaviour (demo-friendly defaults; production would use minutes, not seconds).
RING_TIMEOUT_S = int(os.getenv("RING_TIMEOUT_S", "25"))
RETRY_INTERVAL_S = int(os.getenv("RETRY_INTERVAL_S", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))  # retries after the first attempt
MAX_TURNS = int(os.getenv("MAX_TURNS", "8"))

SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
SCHEDULER_TICK_S = float(os.getenv("SCHEDULER_TICK_S", "2"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Where the agent reads caregiver records from. Empty = read the built-in Mock Caregiver
# Dashboard in-process (same JSON as its REST API). Set to a base URL (e.g. https://my-app.onrender.com)
# to make the agent fetch records over HTTP from any dashboard exposing the same API.
CAREGIVER_API_BASE = os.getenv("CAREGIVER_API_BASE", "").rstrip("/")


def llm_enabled() -> bool:
    return bool(GEMINI_API_KEY)
