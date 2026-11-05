"""Readable analysis paths, selection identity, and offline cache migration."""

import argparse
import hashlib
import json
import re
import shutil
import unicodedata
from pathlib import Path

from .common import sha256, utc, write_json


def slug(value: str, default="unnamed-analysis") -> str:
    """Convert a display name to a portable directory component.

    Path separators, traversal components and names without usable characters raise
    ValueError. Blank labels use ``default``; accepted labels are limited to 100 characters.
    """
    value = value.strip()
    if not value:
        return default
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError("Analysis names cannot contain path separators or '..'")
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:100].rstrip("-")
    if not result:
        raise ValueError("Analysis name must contain letters or numbers")
    return result


def project_root(path: Path) -> Path:
    """Find the repository or enclosing data directory without assuming storage depth."""
    path = Path(path).resolve()
    for parent in (path, *path.parents):
        if (parent / "config/wmu_locations.json").exists():
            return parent
    for parent in (path, *path.parents):
        if parent.name == "data":
            return parent.parent
    raise ValueError("Dataset must be inside a repository data directory")


def device_directory(root: Path, wmu_ids) -> str:
    """Return a readable device/location component for one or several WMUs."""
    ids = sorted(str(wmu) for wmu in wmu_ids)
    if not ids or any(not wmu.isdigit() for wmu in ids):
        raise ValueError("WMU identifiers must be numeric")
    if len(ids) > 1:
        return "wmus" + "-".join(ids)
    registry = root / "config/wmu_locations.json"
    locations = json.loads(registry.read_text()) if registry.exists() else {}
    location = locations.get(ids[0], {}).get("location", "unknown-location")
    return f"wmu{ids[0]}_{slug(location)}"


def time_component(value) -> str:
    """Format a timezone-aware instant with UTC Z and retain fractional-second precision."""
    timestamp = utc(value)
    fraction = timestamp.microsecond * 1000 + timestamp.nanosecond
    suffix = f".{fraction:09d}".rstrip("0") if fraction else ""
    return timestamp.strftime("%Y-%m-%dT%H%M%S") + suffix + "Z"


def analysis_directory(root: Path, selection, analysis_name="") -> Path:
    """Build the analysis/device/requested-window path without creating directories."""
    start, end = selection.bounds
    mode = "parquet" if selection.requested_seconds > 3600 else "waveform"
    return (
        Path(root).resolve()
        / "data/analyses"
        / slug(analysis_name)
        / device_directory(Path(root), selection.wmu_ids)
        / f"{time_component(start)}_to_{time_component(end)}__{mode}"
    )


def identity(wmu_ids, start, end, convention=None) -> str:
    """Hash data-defining inputs, excluding display labels and resource budgets."""
    value = {
        "wmu_ids": sorted(str(wmu) for wmu in wmu_ids),
        "start_utc": utc(start).isoformat(),
        "end_utc": utc(end).isoformat(),
        "phase_convention": convention,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest_window(manifest):
    """Recover requested or recorded bounds from current and legacy manifests."""
    selection = manifest.get("selection", {})
    start, end = selection.get("start_utc"), selection.get("end_utc")
    ids = selection.get("wmu_ids") or sorted(
        {item["wmu"] for item in manifest.get("normalized_files", [])}
    )
    if start and end:
        return ids, utc(start), utc(end)
    sources = manifest.get("sources", [])
    bounds = [
        (item.get("requested_start"), item.get("requested_end"))
        for item in sources
        if item.get("requested_start") is not None and item.get("requested_end") is not None
    ]
    if bounds:
        import pandas as pd

        return (
            ids,
            pd.to_datetime(min(item[0] for item in bounds), unit="s", utc=True),
            pd.to_datetime(max(item[1] for item in bounds), unit="s", utc=True),
        )
    summaries = [
        item
        for item in manifest.get("summaries", [])
        if item.get("start_utc") and item.get("last_sample_utc")
    ]
    if summaries:
        import pandas as pd

        return (
            ids,
            min(utc(item["start_utc"]) for item in summaries),
            max(utc(item["last_sample_utc"]) for item in summaries) + pd.Timedelta(nanoseconds=1),
        )
    return ids, None, None


def manifest_identity(manifest) -> str | None:
    """Return a comparable selection ID for explicit-window datasets.

    Legacy latest-mode datasets remain loadable but are not silently treated as exact
    explicit-window selections, because their clipping rules differ.
    """
    if manifest.get("selection", {}).get("mode", "interval") != "interval":
        return None
    ids, start, end = manifest_window(manifest)
    return (
        identity(ids, start, end, manifest.get("phase_convention")) if start is not None else None
    )


def dataset_manifests(root: Path):
    """Yield readable and legacy dataset manifests in deterministic order."""
    for folder in ("analyses", "processed"):
        yield from sorted((Path(root) / "data" / folder).glob("**/manifest.json"))


def dataset_label(manifest, directory: Path) -> str:
    """Build the notebook's analysis/device/window/mode label from manifest metadata."""
    ids, start, end = manifest_window(manifest)
    root = project_root(directory)
    registry = root / "config/wmu_locations.json"
    locations = json.loads(registry.read_text()) if registry.exists() else {}
    device_labels = [
        f"{wmu} {locations.get(wmu, {}).get('location', 'Unknown location')}" for wmu in ids
    ]
    name = manifest.get("analysis_name") or (
        "Synthetic demo" if manifest.get("synthetic") else "Unnamed analysis"
    )
    window = (
        f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M} UTC" if start is not None else directory.name
    )
    mode = "Parquet" if manifest.get("automatic_data_mode") == "parquet_only" else "Waveform"
    version = re.search(r"__v(\d+)$", directory.name)
    version_label = f" | v{version.group(1)}" if version else ""
    return f"{name} | {', '.join(device_labels)} | {window} | {mode}{version_label}"


def available_directory(base: Path, selection_id: str) -> Path:
    """Reuse an identical selection path or choose a non-overwriting __vN suffix."""
    candidate, version = base, 1
    while candidate.exists():
        manifest = candidate / "manifest.json"
        if (
            manifest.exists()
            and manifest_identity(json.loads(manifest.read_text())) == selection_id
        ):
            return candidate
        version += 1
        candidate = base.with_name(base.name + f"__v{version}")
    return candidate


def validate_bundle(directory: Path, manifest):
    """Verify all normalized paths and hashes before reusing or moving a dataset."""
    directory = directory.resolve()
    if not manifest.get("normalized_files"):
        raise ValueError("Cached dataset contains no normalized files")
    for record in manifest.get("normalized_files", []):
        path = (directory / record["path"]).resolve()
        if not path.is_relative_to(directory) or sha256(path) != record["sha256"]:
            raise ValueError(f"Invalid cached dataset file: {record['path']}")


def reuse_dataset(root: Path, selection, analysis_name, convention=None):
    """Find matching local data and copy to the named analysis, without network access.

    Copies retain independent manifests and exports. Existing original datasets are not
    deleted; ``migrate`` can move legacy bundles separately.
    """
    start, end = selection.bounds
    key = identity(selection.wmu_ids, start, end, convention)
    destination = available_directory(analysis_directory(root, selection, analysis_name), key)
    if (destination / "manifest.json").exists():
        manifest = json.loads((destination / "manifest.json").read_text())
        validate_bundle(destination, manifest)
        return destination
    for path in dataset_manifests(root):
        manifest = json.loads(path.read_text())
        if manifest_identity(manifest) != key:
            continue
        validate_bundle(path.parent, manifest)
        if path.parent.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path.parent, destination)
        manifest.update(
            {"analysis_name": analysis_name.strip() or "Unnamed analysis", "selection_id": key}
        )
        write_json(destination / "manifest.json", manifest)
        refresh_export_metadata(destination, manifest)
        return destination
    return None


def refresh_export_metadata(directory, manifest):
    """Update embedded manifest and dataset path in moved/copied plot configuration."""
    path = directory / "plots/plot_config.resolved.json"
    if path.exists():
        config = json.loads(path.read_text())
        config.update({"dataset": str(directory.resolve()), "source_manifest": manifest})
        write_json(path, config)


def migration_plan(root: Path, analysis_name=""):
    """Describe non-overwriting moves for legacy datasets and numeric download caches."""
    from types import SimpleNamespace

    root = Path(root).resolve()
    plans, reserved = [], set()
    preset_path = root / "config/plots/pjm_july22.json"
    preset = json.loads(preset_path.read_text()) if preset_path.exists() else {}
    for path in sorted((root / "data/processed").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        ids, start, end = manifest_window(manifest)
        name = (
            analysis_name
            or manifest.get("analysis_name")
            or ("synthetic-demo" if manifest.get("synthetic") else "unnamed-analysis")
        )
        if not analysis_name and not manifest.get("analysis_name") and start is not None:
            matches_preset = any(
                start == utc(window["start_utc"]) and end == utc(window["end_utc"])
                for window in preset.get("windows", {}).values()
            )
            if matches_preset and set(ids) <= set(preset.get("devices", [])):
                name = "PJM July 22 load transfer"
        if start is None:
            destination = root / "data/analyses" / slug(name) / path.parent.name
        else:
            selection = SimpleNamespace(
                wmu_ids=ids, bounds=(start, end), requested_seconds=(end - start).total_seconds()
            )
            base = analysis_directory(root, selection, name)
            destination, version = base, 1
            while destination.exists() or destination in reserved:
                version += 1
                destination = base.with_name(base.name + f"__v{version}")
        reserved.add(destination)
        plans.append(
            {
                "kind": "dataset",
                "source": str(path.parent),
                "destination": str(destination),
                "analysis_name": name,
            }
        )
    for folder in sorted((root / "data/downloads").glob("*")):
        if folder.is_dir() and folder.name.isdigit():
            destination = folder.with_name(device_directory(root, [folder.name]))
            if destination.exists():
                raise ValueError(f"Cannot merge download caches automatically: {destination}")
            plans.append(
                {"kind": "downloads", "source": str(folder), "destination": str(destination)}
            )
    return plans


def migrate(root: Path, analysis_name="", apply=False):
    """Preview or apply verified legacy-cache moves; never delete or overwrite data."""
    plans = migration_plan(root, analysis_name)
    if not apply:
        return plans
    for plan in plans:
        if plan["kind"] == "dataset":
            source = Path(plan["source"])
            validate_bundle(source, json.loads((source / "manifest.json").read_text()))
    for plan in plans:
        source, destination = Path(plan["source"]), Path(plan["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if plan["kind"] == "dataset":
            manifest = json.loads((destination / "manifest.json").read_text())
            manifest["analysis_name"] = plan["analysis_name"]
            manifest["selection_id"] = manifest_identity(manifest)
            write_json(destination / "manifest.json", manifest)
            refresh_export_metadata(destination, manifest)
    processed = Path(root) / "data/processed"
    if processed.exists() and not any(processed.iterdir()):
        processed.rmdir()
    return plans


def main():
    """Print migration proposals; execute moves only when --apply is supplied."""
    parser = argparse.ArgumentParser(description="Migrate KGRID caches to readable directories")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--analysis-name", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.root, args.analysis_name, args.apply), indent=2))
