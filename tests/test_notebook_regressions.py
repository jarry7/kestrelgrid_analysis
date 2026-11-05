"""Notebook export links, event presets and anomalous raw-timing regressions."""

import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kestrelgrid_tools.explorer import Explorer
from kestrelgrid_tools.interpretation import read_waveform
from kestrelgrid_tools.raw_validation import iter_raw_frames
from kestrelgrid_tools.waveform_store import normalize_waveform
from kestrelgrid_tools.workflow import demo_dataset


def test_backward_frames_are_reported_and_excluded_without_upstream_skip(tmp_path, raw_bytes):
    """Report and exclude backward samples without silently losing upstream frames."""
    data = raw_bytes(count=8)
    frame_bytes = 8088
    # Repeated header at frame 3 then an overlapping backwards frame at frame 4.
    damaged_timing = bytearray(data)
    struct.pack_into("<d", damaged_timing, 3 * frame_bytes + 8, 1_700_000_002.0)
    struct.pack_into("<d", damaged_timing, 4 * frame_bytes + 8, 1_700_000_001.5)
    path = tmp_path / "sample.raw"
    path.write_bytes(damaged_timing)
    with pytest.raises(ValueError, match="unordered"):
        list(iter_raw_frames(path))
    table, _ = read_waveform(path, allow_time_discontinuities=True)
    assert len(table) == 32000
    store, summary = normalize_waveform(path, tmp_path / "clean.parquet", chunk_samples=12000)
    clean = store.to_dataframe()
    assert clean.time_utc.is_monotonic_increasing and clean.time_utc.is_unique
    assert summary["rejected_overlap_samples"] == 8000
    assert summary["gaps_or_overlaps"]
    assert np.isclose(summary["interval_rms_v"], 100 / np.sqrt(2), atol=0.1)
    assert clean.segment.nunique() > 1


def test_export_links_work_from_notebooks_working_directory(tmp_path, monkeypatch):
    """Generate valid repository-root links from a nested kernel working directory."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config/wmu_locations.json").write_text('{"4021":{"location":"Vancouver"}}')
    (tmp_path / "notebooks").mkdir()
    explorer = Explorer(tmp_path)
    explorer.dataset = demo_dataset(tmp_path)
    explorer.render()
    shown = []
    monkeypatch.setattr("kestrelgrid_tools.explorer.display", shown.append)

    def export(figure, directory, name, formats, *args):
        """Create a fixture HTML export for link validation without browser dependencies."""
        directory.mkdir(exist_ok=True, parents=True)
        path = directory / (name + ".html")
        path.write_text("test")
        return [path]

    monkeypatch.setattr("kestrelgrid_tools.explorer.export_figure", export)
    monkeypatch.chdir(tmp_path / "notebooks")
    explorer.export()
    assert len(shown) == 3
    assert all("/files/data/analyses/synthetic-demo/" in item.data for item in shown)
    assert all("doesn't exist" not in item.data for item in shown)


def test_pjm_presets_timezone_duration_and_routing():
    """Map event presets to UTC and retain the strict one-hour product boundary."""
    root = Path(__file__).resolve().parents[1]
    explorer = Explorer(root)
    explorer.wmu.value = "4012"
    explorer.preset.value = "hour"
    assert explorer.selection().requested_seconds == 3600
    assert explorer.selection().data_mode == "waveform_and_parquet"
    assert pd.Timestamp(explorer.start.value).tz_convert("America/New_York").hour == 7
    explorer.preset.value = "day"
    assert explorer.selection().requested_seconds == 86400
    assert explorer.selection().products == ("measurements",)
