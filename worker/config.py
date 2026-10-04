"""Load only this application's known settings from an ignored local .env file."""
import os
from pathlib import Path

SETTINGS = {"WORKER_API_KEY", "WORKER_API_BASE", "WORKER_MODEL"}


def load_local_env(path):
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in SETTINGS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Explicit environment settings take precedence. Never execute .env contents.
        os.environ.setdefault(name, value)
