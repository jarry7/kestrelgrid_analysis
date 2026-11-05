"""Disk-backed waveform data, bounded decoding, and extrema-preserving plot previews."""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import utc
from .interpretation import read_waveform
from .raw_validation import iter_raw_frames


def normalize_waveform(raw_path, destination, start=None, end=None, chunk_samples=250_000):
    """Decode bounded frame-aligned chunks using public kestrelgrid methods."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(".parquet.part")
    writer = None
    total = locked = frame_count = rejected_overlap_samples = 0
    value_sum = square_sum = 0.0
    minimum, maximum = np.inf, -np.inf
    rates, boots, gaps = set(), set(), []
    first_time = last_time = previous = None
    segment = 0
    try:
        with (
            tempfile.TemporaryDirectory(prefix="kgrid-chunk-") as temp,
            raw_path.open("rb") as source,
        ):
            chunk_path = Path(temp) / "chunk.raw"
            chunk, frames = bytearray(), []

            def flush():
                """Decode buffered frames, exclude overlaps and append bounded Parquet row groups."""
                nonlocal writer, total, locked, value_sum, square_sum, minimum, maximum
                nonlocal first_time, last_time
                nonlocal rejected_overlap_samples
                if not frames:
                    return
                chunk_path.write_bytes(chunk)
                table, _ = read_waveform(
                    chunk_path, max_samples=chunk_samples + 16_384, allow_time_discontinuities=True
                )
                table["segment"] = np.concatenate(
                    [np.full(frame.n_samples, number, dtype="int32") for frame, number in frames]
                )
                if start is not None:
                    table = table[table.time_utc >= utc(start)]
                if end is not None:
                    table = table[table.time_utc < utc(end)]
                if last_time is not None:
                    accepted = table.time_utc > last_time
                    rejected_overlap_samples += int((~accepted).sum())
                    table = table[accepted]
                if not table.time_utc.is_monotonic_increasing or table.time_utc.duplicated().any():
                    # Keep only a new temporal high-water mark in stream order. Do not
                    # sort erroneous timestamps into an apparently continuous waveform.
                    ns = table.time_utc.astype("int64").to_numpy()
                    accepted = np.r_[True, ns[1:] > np.maximum.accumulate(ns[:-1])]
                    rejected_overlap_samples += int((~accepted).sum())
                    table = table[accepted]
                if table.empty:
                    return
                table = table.reset_index(drop=True)
                values = table.voltage_v.to_numpy(dtype="float64")
                total += len(table)
                locked += int(table.gps_locked.sum())
                value_sum += float(values.sum())
                square_sum += float(np.dot(values, values))
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
                first_time = first_time if first_time is not None else table.time_utc.iloc[0]
                last_time = table.time_utc.iloc[-1]
                arrow = pa.Table.from_pandas(table, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(part, arrow.schema, compression="zstd")
                writer.write_table(arrow, row_group_size=chunk_samples)

            for frame in iter_raw_frames(raw_path, allow_time_discontinuities=True):
                if previous is not None:
                    difference = frame.timestamp - previous.end
                    if abs(difference) > 1 / max(previous.rate_hz, frame.rate_hz):
                        segment += 1
                        gaps.append(
                            {"start_epoch": frame.timestamp, "difference_seconds": difference}
                        )
                previous = frame
                rates.add(frame.rate_hz)
                boots.add(frame.boot_count)
                frame_count += 1
                if frames and sum(item.n_samples for item, _ in frames) >= chunk_samples:
                    flush()
                    chunk, frames = bytearray(), []
                source.seek(frame.offset)
                chunk.extend(source.read(frame.n_bytes))
                frames.append((frame, segment))
            flush()
        if not total:
            raise ValueError("No waveform samples in requested interval")
        writer.close()
        writer = None
        part.replace(destination)
    except Exception:
        if writer:
            writer.close()
        part.unlink(missing_ok=True)
        raise
    summary = {
        "wmu": frame.wmu,
        "frames": frame_count,
        "sample_count": total,
        "sample_rates_hz": sorted(rates),
        "boot_counts": sorted(boots),
        "start_utc": first_time.isoformat(),
        "last_sample_utc": last_time.isoformat(),
        "minimum_v": minimum,
        "maximum_v": maximum,
        "mean_v": value_sum / total,
        "interval_rms_v": float(np.sqrt(square_sum / total)),
        "gps_locked_fraction": locked / total,
        "gaps_or_overlaps": gaps,
        "validated": True,
        "kestrelgrid_version": "0.1.0",
        "decoding_chunk_samples": chunk_samples,
        "rejected_overlap_samples": rejected_overlap_samples,
    }
    return WaveformStore([destination]), summary


class WaveformStore:
    """Full-resolution samples stay on disk. Explicit small windows can be loaded locally."""

    def __init__(self, paths):
        """Inspect ordered Parquet sources without loading full-resolution waveforms."""
        self.paths = [Path(path) for path in paths]
        self.columns = pq.ParquetFile(self.paths[0]).schema_arrow.names
        self.count = sum(pq.ParquetFile(path).metadata.num_rows for path in self.paths)

    def __len__(self):
        """Return the stored row count before overlap reconciliation during iteration."""
        return self.count

    def __contains__(self, column):
        """Return whether a column exists in the waveform schema."""
        return column in self.columns

    @property
    def empty(self):
        """Return whether source files contain no samples."""
        return not self.count

    def iter_tables(self, start=None, end=None, batch_size=250_000):
        """Yield bounded UTC-filtered dataframes with deduplicated ordering and gap segments."""
        start, end = (
            utc(start) if start is not None else None,
            utc(end) if end is not None else None,
        )
        previous_last = None
        previous_segment = 0
        for path in self.paths:
            parquet = pq.ParquetFile(path)
            segment_offset = None
            for batch in parquet.iter_batches(batch_size=batch_size):
                table = batch.to_pandas()
                if start is not None:
                    table = table[table.time_utc >= start]
                if end is not None:
                    table = table[table.time_utc < end]
                if not table.empty:
                    if previous_last is not None:
                        table = table[table.time_utc > previous_last]
                    if table.empty:
                        continue
                    table = table.reset_index(drop=True)
                    if "segment" in table:
                        if segment_offset is None:
                            cadence = table.time_utc.diff().dt.total_seconds().median()
                            gap = (
                                previous_last is not None
                                and (table.time_utc.iloc[0] - previous_last).total_seconds()
                                > 1.5 * cadence
                            )
                            segment_offset = (
                                previous_segment + int(gap) - int(table.segment.iloc[0])
                            )
                        table["segment"] += segment_offset
                        previous_segment = int(table.segment.iloc[-1])
                    previous_last = table.time_utc.iloc[-1]
                    yield table

    def to_dataframe(self, start=None, end=None, max_samples=1_000_000):
        """Materialize a short local interval, rejecting loads above the sample-memory limit."""
        tables, count = [], 0
        for table in self.iter_tables(start, end):
            count += len(table)
            if count > max_samples:
                raise ValueError(
                    "Local window exceeds memory limit; shorten it or use iter_tables()"
                )
            tables.append(table)
        return (
            pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=self.columns)
        )

    def plot_table(self, start=None, end=None, max_points=20_000):
        """Two bounded passes; retain per-batch endpoints/extrema and original gap segments."""
        counts = [len(table) for table in self.iter_tables(start, end)]
        total = sum(counts)
        if not total:
            return pd.DataFrame(columns=self.columns)
        if total <= max_points:
            return self.to_dataframe(start, end, max_samples=max_points)
        # Account for endpoint overhead, even for very high-rate files.
        if len(counts) * 4 > max_points:
            raise ValueError("Display point budget is too small for this recording; increase it")
        available = max_points - 4 * len(counts)
        result = []
        for count, table in zip(counts, self.iter_tables(start, end)):
            budget = 4 + int(available * count / total)
            selected = {0, len(table) - 1}
            values = table.voltage_v.to_numpy()
            segments = table["segment"].to_numpy() if "segment" in table else np.zeros(count)
            for indices in np.split(np.arange(count), np.flatnonzero(np.diff(segments)) + 1):
                if not len(indices):
                    continue
                selected.update([int(indices[0]), int(indices[-1])])
            for indices in np.array_split(np.arange(count), max(1, (budget - len(selected)) // 2)):
                selected.update(
                    [
                        int(indices[np.argmin(values[indices])]),
                        int(indices[np.argmax(values[indices])]),
                    ]
                )
            result.append(table.iloc[sorted(selected)])
        table = pd.concat(result, ignore_index=True)
        if len(table) > max_points:
            raise ValueError("Too many recording gaps for display budget; choose a shorter window")
        table.attrs["display_reduced"] = True
        return table
