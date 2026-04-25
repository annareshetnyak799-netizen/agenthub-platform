"""Atomic JSON snapshot persistence for the provider store.

If DATA_DIR is set, every mutation is persisted to {DATA_DIR}/providers.json.
On startup the file is loaded before INITIAL_PROVIDERS env is applied, so
the env acts as a seed only when the file does not exist yet.
"""
import json
import os
import tempfile
from typing import Any

DATA_DIR = os.getenv("DATA_DIR", "")
_SNAPSHOT_FILE = os.path.join(DATA_DIR, "providers.json") if DATA_DIR else ""


def load_snapshot() -> list[dict[str, Any]] | None:
    """Return persisted provider records or None if not available."""
    if not _SNAPSHOT_FILE:
        return None
    try:
        with open(_SNAPSHOT_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def save_snapshot(records: list[dict[str, Any]]) -> None:
    """Atomically write provider records to disk. No-op if DATA_DIR is unset."""
    if not _SNAPSHOT_FILE:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    dir_path = os.path.dirname(_SNAPSHOT_FILE)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_path, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(records, tmp, default=str)
        tmp_path = tmp.name
    os.replace(tmp_path, _SNAPSHOT_FILE)
