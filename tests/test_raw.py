"""Raw alignment, structural validation, upstream scaling and CSV tests."""

import numpy as np
import pandas as pd
import pytest

from kestrelgrid_tools.interpretation import export_raw_csv, read_waveform
from kestrelgrid_tools.raw_validation import align_frames, validate_raw


def test_alignment_and_truncation_detection(raw_bytes):
    """Find consecutive frame boundaries and reject truncated or mismatched raw data."""
    data = raw_bytes()
    frames = validate_raw(data, "4021")
    assert len(frames) == 8
    assert frames[0].rate_hz == 4000
    for invalid in (data[:-2], data + b"partial", b""):
        with pytest.raises(ValueError):
            validate_raw(invalid)
    aligned, skip, valid = align_frames(b"random prefix" + data + b"incomplete", "4021")
    assert skip == 13 and aligned == data and len(valid) == 8
    with pytest.raises(ValueError):
        validate_raw(data, "2004")


def test_upstream_decode_clip_scale_and_csv(tmp_path, raw_bytes):
    """Verify package scaling, exact clipping, CSV row counts and sample-memory limits."""
    path = tmp_path / "sample.raw"
    path.write_bytes(raw_bytes(count=4))
    start = pd.to_datetime(1_700_000_000.25, unit="s", utc=True)
    end = start + pd.Timedelta(seconds=1)
    table, summary = read_waveform(path, start, end)
    assert len(table) == 4000
    assert table.time_utc.min() >= start and table.time_utc.max() < end
    assert summary["sample_rates_hz"] == [4000]
    assert np.isclose(summary["interval_rms_v"], 100 / np.sqrt(2), atol=0.1)
    csv = tmp_path / "voltage.csv"
    export_raw_csv(path, csv)
    exported = pd.read_csv(csv)
    assert len(exported) == 16000
    assert exported.columns.tolist() == ["timestamp_utc", "timestamp_epoch", "voltage_v"]
    with pytest.raises(ValueError, match="memory limit"):
        read_waveform(path, max_samples=10)
