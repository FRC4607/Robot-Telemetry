"""
Local secrets — loaded from .env file (which is not committed to version control).

Reads INFLUX_TOKEN and DB_PASSWORD from the .env file in the project root,
falling back to environment variables if already set.
"""

import os
from pathlib import Path

# Load .env file if it exists (simple key=value parser, no extra dependencies)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.is_file():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            _key, _, _val = _line.partition("=")
            if _key and _ and _key not in os.environ:
                os.environ[_key] = _val

INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
