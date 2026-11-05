"""Local credential loading without secret-bearing representations."""

import json
import os
import stat
from pathlib import Path

DEFAULT_PATH = Path("~/.config/kestrelgrid/credentials.json")


def load_credentials(path: str | Path | None = None) -> tuple[str, str]:
    path = Path(path or os.environ.get("KESTRELGRID_CREDENTIALS", DEFAULT_PATH)).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Create the local credential file at {path}; see README.md")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError(f"Credential file must be owner-only: chmod 600 {path}")
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise ValueError("Credential file is not valid JSON") from None
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in ("username", "password")
    ):
        raise ValueError("Credentials require nonempty username and password strings")
    return data["username"], data["password"]
