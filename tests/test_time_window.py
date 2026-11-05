"""Duration-based routing, bounded hour decoding and interactive download tests."""

import json
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from kestrelgrid_tools.common import sha256
from kestrelgrid_tools.discovery import parse_listing
from kestrelgrid_tools.explorer import Explorer
from kestrelgrid_tools.waveform_store import normalize_waveform
from kestrelgrid_tools.workflow import Selection, fetch_dataset, load_dataset, preview


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (3599, "waveform_and_parquet"),
        (3600, "waveform_and_parquet"),
        (3600.001, "parquet_only"),
        (86400, "parquet_only"),
    ],
)
def test_duration_routing_enforced_before_download(seconds, expected):
    """Route windows beyond one hour exclusively to Parquet before payload transfer."""
    start = pd.Timestamp("2026-11-04T12:00:00Z")
    selection = Selection(
        start_utc=start.isoformat(), end_utc=(start + pd.Timedelta(seconds=seconds)).isoformat()
    )
    selection.validate()
    assert selection.data_mode == expected
    files = parse_listing(
        """<a href="2026-11-04.raw">raw</a>
        <a href="2026-11-04.7z">compressed</a>
        <a href="2026-11-04.parquet">measurements</a>""",
        "https://data.kestrelgrid.com/4021/",
        "4021",
    )
    client = Mock()
    client.discover.return_value = files
    rows = preview(client, selection)
    assert {row["product"] for row in rows} == set(selection.products)
    if seconds > 3600:
        assert all(row["remote"].kind == "parquet" for row in rows)
    client.raw_sample.assert_not_called()


def test_long_window_only_transfers_parquet_and_preserves_native_cadence(tmp_path):
    """Fetch multiple Parquet partitions, deduplicate them and preserve native cadence."""
    files = parse_listing(
        """<a href="2026-11-04.raw">raw</a>
        <a href="2026-11-04.7z">zip</a>
        <a href="2026-11-04.parquet">parquet</a>
        <a href="2026-11-05.parquet">parquet</a>""",
        "https://data.kestrelgrid.com/4021/",
        "4021",
    )
    client = Mock()
    client.discover.return_value = files
    client.transferred = 0
    client.remaining = 100 * 1024**2
    client.metadata.return_value = {"size": 1024}
    selected = []

    def download(remote, destination):
        """Write synthetic overlapping partition measurements and record product choices."""
        selected.append(remote.kind)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Overlap at the partition boundary tests duplicate reconciliation.
        t = (
            pd.Timestamp("2026-11-05T07:30:00Z")
            if "11-04" in remote.name
            else pd.Timestamp("2026-11-05T08:00:00Z")
        )
        count = 3601 if "11-04" in remote.name else 3600
        table = pd.DataFrame(
            {"t": t.timestamp() + np.arange(count) / 2, "rms": 120.0, "frequency": 60.01}
        )
        table.to_parquet(destination)
        return {"sha256": sha256(destination), "url": remote.url}

    client.download.side_effect = download
    selection = Selection(start_utc="2026-11-05T07:30:00Z", end_utc="2026-11-05T08:31:00Z")
    dataset = fetch_dataset(tmp_path, selection, client=client, progress=lambda _: None)
    assert selected == ["parquet", "parquet"]
    client.raw_sample.assert_not_called()
    client.estimate_raw_transfer.assert_not_called()
    assert not dataset.waveforms
    table = dataset.measurements["4021"]
    assert len(table) == 7200
    assert table.time_utc.diff().dropna().dt.total_seconds().eq(0.5).all()
    assert dataset.manifest["automatic_data_mode"] == "parquet_only"
    assert any("duplicate" in warning for warning in dataset.manifest["warnings"])
    assert len(load_dataset(dataset.directory).measurements["4021"]) == 7200


def test_missing_partition_reported(tmp_path):
    """Report missing source coverage without replacing the requested window."""
    files = parse_listing(
        '<a href="2026-11-04.parquet">parquet</a>', "https://data.kestrelgrid.com/4021/", "4021"
    )
    client = Mock()
    client.discover.return_value = files
    client.metadata.return_value = {"size": 1}
    client.remaining, client.transferred = 10000, 0

    def download(remote, path):
        """Write a sparse fixture for the only available measurement partition."""
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {"t": [pd.Timestamp("2026-11-05T07:30:00Z").timestamp()], "rms": [120.0]}
        ).to_parquet(path)
        return {"sha256": sha256(path)}

    client.download.side_effect = download
    selection = Selection(start_utc="2026-11-05T07:30:00Z", end_utc="2026-11-05T10:00:00Z")
    dataset = fetch_dataset(tmp_path, selection, client=client, progress=lambda _: None)
    assert any("missing source partition" in warning for warning in dataset.manifest["warnings"])
    assert dataset.manifest["selection"]["end_utc"] == selection.end_utc


def test_full_hour_chunked_decoding_and_local_cycle_view(tmp_path, raw_bytes, monkeypatch):
    """Normalize a full hour in bounded chunks and reload a full-resolution local cycle."""
    import kestrelgrid_tools.waveform_store as module

    path = tmp_path / "hour.raw"
    path.write_bytes(raw_bytes(count=3600))
    original = module.read_waveform
    chunks = []

    def decode(path, *args, **kwargs):
        """Record raw chunk sizes while delegating decoding to the public reader adapter."""
        chunks.append(path.stat().st_size)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module, "read_waveform", decode)
    store, summary = normalize_waveform(path, tmp_path / "hour.parquet")
    assert len(store) == 14_400_000
    assert summary["sample_count"] == 14_400_000
    assert len(chunks) > 50 and max(chunks) < 1024**2
    assert np.isclose(summary["interval_rms_v"], 100 / np.sqrt(2), atol=0.1)
    overview = store.plot_table(max_points=20000)
    assert len(overview) <= 20000 and overview.attrs["display_reduced"]
    left = pd.to_datetime(1_700_000_000.25, unit="s", utc=True)
    detail = store.plot_table(left, left + pd.Timedelta(seconds=0.125))
    assert len(detail) == 500
    assert not detail.attrs.get("display_reduced", False)
    with pytest.raises(ValueError, match="memory limit"):
        store.to_dataframe(max_samples=1000)


def test_one_action_interface_automatically_exports(tmp_path, monkeypatch):
    """Export all three available product figures from the one-action notebook entry point."""
    from kestrelgrid_tools.workflow import demo_dataset

    (tmp_path / "config").mkdir()
    (tmp_path / "config/wmu_locations.json").write_text(
        json.dumps({"4021": {"location": "Vancouver"}})
    )
    explorer = Explorer(tmp_path)
    explorer.start.value, explorer.end.value = "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"
    assert explorer.selection().wmu_ids == ("4021",)
    assert "Waveform" in explorer.route.value
    explorer.end.value = "2026-01-01T02:00:00Z"
    assert "Parquet measurements only" in explorer.route.value
    monkeypatch.setattr("kestrelgrid_tools.explorer.KGridClient", Mock())
    monkeypatch.setattr(
        "kestrelgrid_tools.explorer.fetch_dataset", lambda *a, **k: demo_dataset(tmp_path)
    )
    outputs = []

    def export(figure, directory, name, formats, *args):
        """Record requested output formats and filenames without rendering PNGs."""
        outputs.append((name, formats))
        return []

    monkeypatch.setattr("kestrelgrid_tools.explorer.export_figure", export)
    explorer.download()
    assert len(outputs) == 3 and all(formats == ("png", "html") for _, formats in outputs)
    assert all("wmu4021_20260101T000000_20260101T020000" in name for name, _ in outputs)
    assert (explorer.dataset.directory / "plots/plot_config.resolved.json").exists()


def test_waveform_store_cross_partition_dedup_and_gap(tmp_path):
    """Reconcile overlapping partition samples while preserving a true recording gap."""
    from kestrelgrid_tools.waveform_store import WaveformStore

    paths = []
    for number, times in enumerate(([0.0, 0.25, 0.5], [0.5, 0.75, 1.0], [2.0, 2.25])):
        path = tmp_path / f"{number}.parquet"
        pd.DataFrame(
            {
                "time_utc": pd.to_datetime(times, unit="s", utc=True),
                "voltage_v": np.arange(len(times), dtype=float),
                "wmu": "4021",
                "gps_locked": True,
                "segment": 0,
            }
        ).to_parquet(path, index=False)
        paths.append(path)
    table = WaveformStore(paths).to_dataframe()
    assert len(table) == 7
    assert table.time_utc.is_unique
    assert table.segment.tolist() == [0, 0, 0, 0, 0, 1, 1]
