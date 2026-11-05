"""Local archive sampling, selection validation and extraction preflight tests."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from kestrelgrid_tools.archive import extract_raw
from kestrelgrid_tools.common import utc
from kestrelgrid_tools.workflow import Selection, local_raw_sample


def test_local_sample_and_limits(tmp_path, raw_bytes):
    """Extract bounded frame ranges and enforce decoded sample limits."""
    source = tmp_path / "daily.raw"
    source.write_bytes(raw_bytes(count=30))
    sample = tmp_path / "sample.raw"
    record = local_raw_sample(source, sample, start=1_700_000_028.0, end=1_700_000_030.0)
    assert record["requested_end"] - record["requested_start"] == 2
    assert sample.stat().st_size == (88 + 8000) * 2
    with pytest.raises(ValueError, match="memory limit"):
        local_raw_sample(source, sample, start=1_700_000_028.0, end=1_700_000_030.0, max_samples=10)


def test_timezone_and_selection():
    """Reject naive or reversed intervals and normalize explicit timezone offsets."""
    with pytest.raises(ValueError, match="timezone-aware"):
        utc("2026-11-04T12:00:00")
    with pytest.raises(ValueError):
        Selection(start_utc="2026-11-04T12:00:00Z", end_utc="2026-11-04T11:00:00Z").validate()
    assert utc("2026-11-04T04:00:00-08:00") == pd.Timestamp("2026-11-04T12:00:00Z")


def test_archive_preflight_rejects_unsafe_and_oversize(tmp_path, monkeypatch):
    """Reject traversal paths, oversized archive contents and symbolic links pre-extraction."""
    monkeypatch.setattr("kestrelgrid_tools.archive.shutil.which", lambda _: "/fake/7zz")
    for member, expected in (
        ("Path = ../escape.raw\nSize = 1", "Unsafe"),
        ("Path = data.raw\nSize = 99999999999", "size limit"),
        ("Path = data.raw\nSize = 1\nSymbolic Link = /elsewhere", "links"),
    ):
        run = Mock(return_value=Mock(stdout="archive info\n----------\n" + member))
        monkeypatch.setattr("kestrelgrid_tools.archive.subprocess.run", run)
        with pytest.raises(ValueError, match=expected):
            extract_raw(Path("example.7z"), tmp_path / "extracted")
        assert run.call_count == 1
