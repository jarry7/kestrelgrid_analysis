import hashlib
import json
from pathlib import Path

import pandas as pd


def utc(value) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("Use timezone-aware UTC dates, e.g. 2026-11-04T12:00:00Z")
    return result.tz_convert("UTC")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
    temp.replace(path)
