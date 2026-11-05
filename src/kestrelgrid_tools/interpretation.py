"""Use public kestrelgrid methods on a structurally validated, bounded sample."""

from pathlib import Path

import numpy as np
import pandas as pd
from kestrelgrid import raw_reader

from .common import utc
from .raw_validation import validate_raw


def read_waveform(path: Path, start=None, end=None, max_samples=5_000_000, allow_time_discontinuities=False):
    if path.stat().st_size > min(max_samples * 90, 128 * 1024**2):
        raise ValueError("Raw input exceeds sample-memory limit")
    metadata = validate_raw(path.read_bytes(), allow_time_discontinuities=allow_time_discontinuities)
    count = sum(frame.n_samples for frame in metadata)
    if count > max_samples:
        raise ValueError(f"{count:,} samples exceed memory limit {max_samples:,}")
    frames, corrupted = raw_reader.read_raw(str(path), scale_data=True)
    if corrupted or not frames:
        raise ValueError("Upstream reader found corrupt or empty data")
    has_backward_time = any(current.timestamp <= previous.timestamp
                            for previous, current in zip(metadata, metadata[1:]))
    if has_backward_time and allow_time_discontinuities:
        # Upstream raw_to_tv updates its lower bound while reading and can silently skip
        # backward frames. read_raw (without bounds) preserves them for explicit QA.
        times = np.concatenate([frame["start_time"] + np.arange(len(frame["data"])) /
                                (frame["sample_rate"] * 1000) for frame in frames])
        voltage = np.concatenate([frame["data"] for frame in frames])
    else:
        times, voltage = raw_reader.raw_to_tv(str(path))
    if len(times) != count or len(voltage) != count:
        raise ValueError("Upstream sample count mismatch")
    expected = np.concatenate([
        frame.timestamp + np.arange(frame.n_samples) / frame.rate_hz for frame in metadata
    ])
    if not np.allclose(times, expected, rtol=0, atol=1e-6):
        raise ValueError("Upstream timestamps inconsistent with frame metadata")
    table = pd.DataFrame({
        "time_utc": pd.to_datetime(times, unit="s", utc=True),
        "voltage_v": voltage,
        "wmu": metadata[0].wmu,
        "gps_locked": np.concatenate([
            np.full(frame.n_samples, frame.gps_locked) for frame in metadata
        ]),
    })
    if start is not None:
        table = table[table.time_utc >= utc(start)]
    if end is not None:
        table = table[table.time_utc < utc(end)]
    if table.empty:
        raise ValueError("No waveform samples in selected interval")
    gaps = []
    for previous, current in zip(metadata, metadata[1:]):
        difference = current.timestamp - previous.end
        if abs(difference) > 1 / max(previous.rate_hz, current.rate_hz):
            gaps.append({"start_epoch": current.timestamp, "difference_seconds": difference})
    values = table.voltage_v.to_numpy(dtype="float64")
    summary = {
        "wmu": metadata[0].wmu, "frames": len(frames), "sample_count": len(table),
        "sample_rates_hz": sorted({frame.rate_hz for frame in metadata}),
        "start_utc": table.time_utc.iloc[0].isoformat(),
        "last_sample_utc": table.time_utc.iloc[-1].isoformat(),
        "minimum_v": float(values.min()), "maximum_v": float(values.max()),
        "mean_v": float(values.mean()), "interval_rms_v": float(np.sqrt(np.mean(values**2))),
        "gps_locked_fraction": float(table.gps_locked.mean()),
        "boot_counts": sorted({frame.boot_count for frame in metadata}),
        "gaps_or_overlaps": gaps, "validated": True, "kestrelgrid_version": "0.1.0",
    }
    return table.reset_index(drop=True), summary


def export_raw_csv(path: Path, destination: Path, max_samples=5_000_000):
    metadata = validate_raw(path.read_bytes())
    if sum(frame.n_samples for frame in metadata) > max_samples:
        raise ValueError("CSV sample limit exceeded")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw_reader.raw_to_csv(str(path), str(destination), include_string_time=True)
