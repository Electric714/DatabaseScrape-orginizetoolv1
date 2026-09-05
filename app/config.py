from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
DB_PATH = DATA_DIR / "crawler.db"

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
