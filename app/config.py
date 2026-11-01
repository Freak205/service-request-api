"""Runtime configuration."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Override with the SRM_DB_PATH environment variable (tests use a temp file instead).
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "requests.db"


def get_db_path() -> Path:
    return Path(os.environ.get("SRM_DB_PATH", DEFAULT_DB_PATH))
