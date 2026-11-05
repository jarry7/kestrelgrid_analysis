"""Bounded 7-Zip extraction into a new directory, with preflight member validation."""

import shutil
import subprocess
from pathlib import Path, PurePosixPath


def extract_raw(archive: Path, destination: Path, max_extracted_bytes=1024**3):
    executable = shutil.which("7zz") or shutil.which("7z")
    if not executable:
        raise RuntimeError("Install 7-Zip for .7z files (macOS: brew install sevenzip)")
    listing = subprocess.run(
        [executable, "l", "-slt", "--", str(archive.resolve())],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    if "----------" not in listing:
        raise ValueError("Could not inspect archive members")
    entries = []
    for block in listing.split("----------", 1)[1].strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        path = PurePosixPath(fields["Path"].replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in str(path):
            raise ValueError("Unsafe archive member path")
        if any("link" in key.lower() for key in fields) or "L" in fields.get("Attributes", ""):
            raise ValueError("Archive links are not supported")
        entries.append(fields)
    if sum(int(item.get("Size", 0)) for item in entries) > max_extracted_bytes:
        raise ValueError("Archive exceeds extraction-size limit")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        subprocess.run(
            [executable, "x", "-y", f"-o{destination.resolve()}", "--", str(archive.resolve())],
            capture_output=True, check=True, timeout=300,
        )
        if sum(path.stat().st_size for path in destination.rglob("*") if path.is_file()) > max_extracted_bytes:
            raise ValueError("Extracted data exceeds size limit")
        result = list(destination.rglob("*.raw"))
        if not result:
            raise ValueError("Archive contains no .raw files")
        return result
    except Exception:
        shutil.rmtree(destination)
        raise
