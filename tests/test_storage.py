"""Readable names, offline cache reuse and non-overwriting migration regressions."""

import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from kestrelgrid_tools.common import sha256, write_json
from kestrelgrid_tools.storage import (
    analysis_directory,
    available_directory,
    identity,
    manifest_identity,
    migrate,
    project_root,
    slug,
)
from kestrelgrid_tools.workflow import Selection, fetch_dataset, load_dataset


def legacy_bundle(root, folder="abc"):
    """Create a measurement bundle matching an explicit two-hour legacy selection."""
    directory = root / "data/processed" / folder
    directory.mkdir(parents=True)
    table = pd.DataFrame(
        {
            "time_utc": pd.date_range("2026-07-22T11:30:00Z", periods=4, freq="30min"),
            "frequency_hz": [60.0, 60.01, 60.02, 60.0],
            "wmu": "4012",
        }
    )
    path = directory / "4012_measurements.parquet"
    table.to_parquet(path, index=False)
    manifest = {
        "selection": {
            "wmu_ids": ["4012"],
            "mode": "interval",
            "start_utc": "2026-07-22T11:30:00Z",
            "end_utc": "2026-07-22T13:30:00Z",
            "max_download_mib": 100,
        },
        "phase_convention": None,
        "automatic_data_mode": "parquet_only",
        "summaries": [],
        "normalized_files": [
            {"path": path.name, "sha256": sha256(path), "product": "measurements", "wmu": "4012"}
        ],
    }
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "plots/plot_config.resolved.json", {"dataset": str(directory)})
    return directory, manifest


def test_readable_names_safety_precision_and_versions(tmp_path):
    """Sanitize display names while preserving exact timestamps and conflicting settings."""
    assert slug("PJM July 22 load transfer") == "pjm-july-22-load-transfer"
    for value in ("../escape", "folder/name", "folder\\name", "!!!"):
        with pytest.raises(ValueError):
            slug(value)
    selection = Selection(
        wmu_ids=("4012",),
        start_utc="2026-07-22T11:30:00.123456789Z",
        end_utc="2026-07-22T12:30:00.123456789Z",
    )
    path = analysis_directory(tmp_path, selection, "PJM")
    assert "wmu4012_unknown-location" in str(path)
    assert path.name == "2026-07-22T113000.123456789Z_to_2026-07-22T123000.123456789Z__waveform"
    path.mkdir(parents=True)
    assert available_directory(path, "different").name.endswith("__v2")


def test_named_reuse_is_offline_and_budget_or_label_independent(tmp_path):
    """Reuse matching source data before authentication and copy into a new named analysis."""
    original, _ = legacy_bundle(tmp_path)
    client = Mock()
    selection = Selection(
        wmu_ids=("4012",),
        start_utc="2026-07-22T11:30:00Z",
        end_utc="2026-07-22T13:30:00Z",
        max_download_mib=1,
    )
    first = fetch_dataset(
        tmp_path, selection, client=client, analysis_name="PJM July 22", progress=lambda _: None
    )
    second = fetch_dataset(
        tmp_path,
        selection,
        client=client,
        analysis_name="Event comparison",
        progress=lambda _: None,
    )
    assert first.directory != second.directory and original.exists()
    assert first.manifest["selection_id"] == second.manifest["selection_id"]
    assert first.manifest["analysis_name"] == "PJM July 22"
    assert len(second.frequencies["4012"]) == 4
    client.discover.assert_not_called()
    plot_config = json.loads((second.directory / "plots/plot_config.resolved.json").read_text())
    assert plot_config["dataset"] == str(second.directory)


def test_migration_dry_run_and_apply_keep_hashes_and_relative_paths(tmp_path):
    """Preview verified moves and apply them without overwrite or new downloads."""
    original, manifest = legacy_bundle(tmp_path)
    downloads = tmp_path / "data/downloads/4012"
    downloads.mkdir(parents=True)
    (downloads / "2026-07-22.parquet").write_bytes(b"fixture")
    plans = migrate(tmp_path, "PJM July 22")
    assert len(plans) == 2 and original.exists()
    assert Path(plans[0]["destination"]).name.endswith("__parquet")
    migrate(tmp_path, "PJM July 22", apply=True)
    destination = Path(plans[0]["destination"])
    assert not original.exists() and destination.exists()
    moved = load_dataset(destination)
    assert moved.manifest["selection_id"] == manifest_identity(manifest)
    assert len(moved.measurements["4012"]) == 4
    assert project_root(destination) == tmp_path
    assert (downloads.with_name("wmu4012_unknown-location") / "2026-07-22.parquet").exists()


def test_corrupt_migration_is_rejected_before_moves(tmp_path):
    """Validate every normalized file before applying any move."""
    original, _ = legacy_bundle(tmp_path)
    (original / "4012_measurements.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Invalid cached"):
        migrate(tmp_path, apply=True)
    assert original.exists()


def test_manifest_identity_excludes_legacy_latest_and_includes_phase_settings(tmp_path):
    """Do not equate legacy latest clipping with exact explicit intervals."""
    _, manifest = legacy_bundle(tmp_path)
    key = manifest_identity(manifest)
    assert key == identity(["4012"], "2026-07-22T11:30:00Z", "2026-07-22T13:30:00Z")
    manifest["phase_convention"] = {"reference_hz": 60, "units": "radians", "sign": -1}
    assert manifest_identity(manifest) != key
    manifest["selection"]["mode"] = "latest"
    assert manifest_identity(manifest) is None
