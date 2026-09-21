import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
DB_PATH = DATA_DIR / "crawler.db"
RUNTIME_DIR = BASE_DIR / ".runtime"
DOL_API_KEY_PATH = RUNTIME_DIR / "dol_api_key.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_USER_AGENT = (
    "DatabaseScrapeOrganizer/0.1 (+permitted-public-data-monitor; contact site operator if needed)"
)
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_PAGES = 5000
DEFAULT_MAX_DEPTH = 12
DEFAULT_CONCURRENCY = 6
DEFAULT_DELAY_MS = 350
MAX_BODY_BYTES = 8 * 1024 * 1024
ASSET_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".css", ".js", ".woff",
    ".woff2", ".ttf", ".eot", ".xml.gz"
}


def get_dol_api_key() -> str:
    """Return the local DOL Open Data API key without logging or exposing it."""
    env_key = os.environ.get("DOL_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return DOL_API_KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def dol_api_key_configured() -> bool:
    return bool(get_dol_api_key())


def save_dol_api_key(value: str) -> None:
    key = (value or "").strip()
    if len(key) < 10 or len(key) > 512 or any(ord(ch) < 32 for ch in key):
        raise ValueError("Enter the API key exactly as issued by the Department of Labor")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    DOL_API_KEY_PATH.write_text(key, encoding="utf-8")
    try:
        DOL_API_KEY_PATH.chmod(0o600)
    except OSError:
        pass
