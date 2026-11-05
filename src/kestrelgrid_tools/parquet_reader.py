"""Normalize observed and updated measurement schemas without guessing theta conventions."""

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .common import utc
from .frequency import PhaseConvention, phase_frequency

ALIASES = {
    "time": ("t", "timestamp_epoch", "time_utc", "timestamp_utc", "timestamp", "time"),
    "rms_v": ("rms", "rms_v", "voltage_rms", "voltage_rms_v"),
    "theta": ("theta", "phase_angle", "phase_angle_rad", "theta_rad"),
    "frequency_hz": ("frequency", "frequency_hz", "freq", "freq_hz", "f"),
    "gps_locked": ("gps", "gps_locked"),
}


def read_measurements(
    path: Path,
    wmu: str,
    start=None,
    end=None,
    convention: PhaseConvention | None = None,
    timestamp_unit="s",
    max_rows=2_000_000,
):
    """Read recognized measurement columns, normalize UTC times and report quality.

    Numeric timestamps use ``timestamp_unit`` (seconds by default); naive timestamps and
    unknown schemas raise ValueError. Optional theta conversion never replaces stored Hz.
    """
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows > max_rows:
        raise ValueError("Parquet row count exceeds memory limit")
    schema = {field.name: str(field.type) for field in parquet.schema_arrow}
    lower = {name.lower(): name for name in schema}
    columns = {
        key: next((lower[name] for name in names if name in lower), None)
        for key, names in ALIASES.items()
    }
    if not columns["time"] or not any(columns[key] for key in ("rms_v", "theta", "frequency_hz")):
        raise ValueError(f"Unknown measurement schema: {schema}; add an explicit schema adapter")
    raw = parquet.read(columns=[name for name in columns.values() if name]).to_pandas()
    timestamps = raw[columns["time"]]
    if pd.api.types.is_numeric_dtype(timestamps):
        timestamps = pd.to_datetime(timestamps, unit=timestamp_unit, utc=True)
    else:
        timestamps = pd.to_datetime(timestamps)
        if timestamps.dt.tz is None:
            raise ValueError("Parquet timestamps have no timezone; establish it before reading")
        timestamps = timestamps.dt.tz_convert("UTC")
    table = pd.DataFrame({"time_utc": timestamps, "wmu": str(wmu)})
    for key, name in columns.items():
        if key != "time" and name:
            table[key] = raw[name]
    if "gps_locked" in table:
        table["gps_locked"] = table.gps_locked.astype("boolean")
    invalid_times = int(table.time_utc.isna().sum())
    if invalid_times:
        raise ValueError("Measurement timestamps contain missing values")
    unordered = not table.time_utc.is_monotonic_increasing
    duplicates = int(table.time_utc.duplicated().sum())
    table = table.sort_values("time_utc", kind="stable").reset_index(drop=True)
    frequency_source = "unavailable: theta convention not established"
    if "frequency_hz" in table:
        frequency_source = "stored"
    elif "theta" in table and convention is not None:
        table["frequency_hz"] = phase_frequency(
            table.time_utc.astype("int64").to_numpy() / 1e9,
            table.theta.to_numpy(),
            convention,
        )
        frequency_source = "derived from theta under user-specified convention"
    if start is not None:
        table = table[table.time_utc >= utc(start)]
    if end is not None:
        table = table[table.time_utc < utc(end)]
    table = table.reset_index(drop=True)
    dt = table.time_utc.diff().dt.total_seconds()
    positive = dt[dt > 0]
    cadence = float(positive.median()) if len(positive) else None
    summary = {
        "wmu": str(wmu),
        "original_schema": schema,
        "column_mapping": columns,
        "numeric_timestamp_unit": timestamp_unit,
        "sample_count": len(table),
        "cadence_seconds": cadence,
        "duplicate_timestamps": duplicates,
        "source_unordered": unordered,
        "gaps": int((dt > 1.5 * cadence).sum()) if cadence else 0,
        "frequency_source": frequency_source,
        "phase_convention": convention.to_dict() if convention else None,
        "start_utc": table.time_utc.iloc[0].isoformat() if len(table) else None,
        "last_sample_utc": table.time_utc.iloc[-1].isoformat() if len(table) else None,
        "phase_aliasing_note": (
            "Unwrapping cannot detect all aliased phase advances. At 2 sps, deviations "
            "beyond ±1 Hz from the reference are ambiguous."
        ),
    }
    if "frequency_hz" in table:
        summary["valid_frequency_samples"] = int(np.isfinite(table.frequency_hz).sum())
    return table, summary
