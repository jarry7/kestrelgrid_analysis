from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from kestrelgrid_tools.archive import extract_raw
from kestrelgrid_tools.common import utc
from kestrelgrid_tools.workflow import Selection, local_raw_sample


def test_local_sample_and_limits(tmp_path, raw_bytes):
    source = tmp_path / "daily.raw"
    source.write_bytes(raw_bytes(count=30))
    sample = tmp_path / "sample.raw"
    record = local_raw_sample(source, sample, duration=2)
    assert record["requested_end"] - record["requested_start"] == 2
    assert sample.stat().st_size == (88 + 8000) * 2
    with pytest.raises(ValueError, match="memory limit"):
        local_raw_sample(source, sample, duration=2, max_samples=10)


def test_timezone_and_selection():
    with pytest.raises(ValueError, match="timezone-aware"):
        utc("2026-11-04T12:00:00")
    with pytest.raises(ValueError):
        Selection(mode="interval", start_utc="2026-11-04T12:00:00Z", end_utc="2026-11-04T11:00:00Z").validate()
    assert utc("2026-11-04T04:00:00-08:00") == pd.Timestamp("2026-11-04T12:00:00Z")


def test_archive_preflight_rejects_unsafe_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr("kestrelgrid_tools.archive.shutil.which", lambda _: "/fake/7zz")
    for member, expected in (("Path = ../escape.raw\nSize = 1", "Unsafe"), ("Path = data.raw\nSize = 99999999999", "size limit"), ("Path = data.raw\nSize = 1\nSymbolic Link = /elsewhere", "links")):
        run = Mock(return_value=Mock(stdout="archive info\n----------\n" + member))
        monkeypatch.setattr("kestrelgrid_tools.archive.subprocess.run", run)
        with pytest.raises(ValueError, match=expected):
            extract_raw(Path("example.7z"), tmp_path / "extracted")
        assert run.call_count == 1
