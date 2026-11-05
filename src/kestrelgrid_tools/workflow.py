"""Reproducible selections, cached dataset bundles, and notebook-facing orchestration."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from pathlib import Path

import pandas as pd
from kestrelgrid.raw_reader.types import HEADER_LENGTH_BYTES

from .archive import extract_raw
from .client import KGridClient
from .common import sha256, utc, write_json
from .discovery import select_files
from .frequency import PhaseConvention
from .interpretation import read_waveform
from .parquet_reader import read_measurements
from .raw_validation import header_at, validate_raw


@dataclass
class Selection:
    wmu_ids: tuple[str, ...] = ("4021",)
    mode: str = "latest"
    duration_seconds: float = 60
    start_utc: str | None = None
    end_utc: str | None = None
    products: tuple[str, ...] = ("waveform", "measurements")
    max_download_mib: float = 100
    max_samples: int = 5_000_000

    def validate(self):
        if not self.wmu_ids or any(not str(wmu).isdigit() for wmu in self.wmu_ids):
            raise ValueError("Select at least one numeric WMU ID")
        if self.mode not in {"latest", "interval"}:
            raise ValueError("Selection mode must be latest or interval")
        if self.duration_seconds <= 0 or self.max_download_mib <= 0 or self.max_samples <= 0:
            raise ValueError("Duration and limits must be positive")
        if not self.products or not set(self.products) <= {"waveform", "measurements"}:
            raise ValueError("Select waveform and/or measurements")
        if self.mode == "interval" and (
            not self.start_utc or not self.end_utc or utc(self.end_utc) <= utc(self.start_utc)
        ):
            raise ValueError("Provide a valid timezone-aware start/end interval")

    @property
    def bounds(self):
        return (utc(self.start_utc), utc(self.end_utc)) if self.mode == "interval" else (None, None)


@dataclass
class Dataset:
    directory: Path
    manifest: dict
    waveforms: dict[str, pd.DataFrame] = field(default_factory=dict)
    measurements: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def summary(self):
        return pd.DataFrame(self.manifest.get("summaries", []))


def preview(client, selection: Selection):
    selection.validate()
    start, end = selection.bounds
    rows = []
    for wmu in selection.wmu_ids:
        files = client.discover(str(wmu))
        for product, kinds in (("waveform", {"raw", "7z"}), ("measurements", {"parquet"})):
            if product not in selection.products:
                continue
            try:
                chosen = select_files(files, kinds, start, end)
            except ValueError:
                chosen = []
            for item in chosen:
                rows.append({
                    "wmu": str(wmu), "product": product, "file": item.name,
                    "size_label": item.size_label, "partition_utc": item.start.isoformat(),
                    "transfer": "bounded range" if item.kind == "raw" else "complete file",
                    "remote": item,
                })
    if not rows:
        raise ValueError("No files available for this selection")
    if sum(row["product"] == "waveform" for row in rows) > 12:
        raise ValueError("More than 12 waveform archives selected; coordinate with the provider")
    return rows


def local_raw_sample(source, destination, duration=60, start=None, end=None, max_samples=5_000_000):
    """Index an extracted archive without loading its full waveform into memory."""
    size = source.stat().st_size
    frames = []
    with source.open("rb") as handle:
        while handle.tell() < size:
            offset = handle.tell()
            frame = header_at(handle.read(HEADER_LENGTH_BYTES))
            frame.offset = offset
            if offset + frame.n_bytes > size:
                raise ValueError("Extracted archive has a truncated frame")
            if frames and (frame.wmu != frames[0].wmu or frame.timestamp <= frames[-1].timestamp):
                raise ValueError("Extracted archive has mixed sensors or unordered frames")
            frames.append(frame)
            handle.seek(offset + frame.n_bytes)
        if not frames:
            raise ValueError("Empty extracted raw file")
        if start is None:
            end = frames[-1].end
            start = end - duration
        chosen = [frame for frame in frames if frame.timestamp < end and frame.end > start]
        if not chosen:
            raise ValueError("No frames overlap requested interval")
        if sum(frame.n_samples for frame in chosen) > max_samples:
            raise ValueError("Extracted sample exceeds sample-memory limit")
        handle.seek(chosen[0].offset)
        data = handle.read(chosen[-1].offset + chosen[-1].n_bytes - chosen[0].offset)
    validate_raw(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {"requested_start": start, "requested_end": end, "sha256": sha256(destination)}


def load_dataset(directory: Path) -> Dataset:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    dataset = Dataset(directory, manifest)
    for record in manifest["normalized_files"]:
        path = (directory / record["path"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Invalid dataset path")
        if sha256(path) != record["sha256"]:
            raise ValueError("Cached dataset hash mismatch")
        table = pd.read_parquet(path)
        target = dataset.waveforms if record["product"] == "waveform" else dataset.measurements
        target[record["wmu"]] = table
    return dataset


def fetch_dataset(
    root: Path, selection: Selection, credentials=None, convention=None, client=None, progress=print,
):
    client = client or KGridClient(credentials, selection.max_download_mib)
    rows = preview(client, selection)
    configuration = {
        "cache_schema_version": 2,
        "selection": asdict(selection),
        "phase_convention": convention.to_dict() if convention else None,
        "source_files": [{"wmu": row["wmu"], "file": row["file"]} for row in rows],
    }
    key = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()[:16]
    directory = Path(root) / "data" / "processed" / key
    if (directory / "manifest.json").exists():
        progress(f"Reusing validated local dataset {key}")
        return load_dataset(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        **configuration, "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selection_rule": "latest closed filename partition" if selection.mode == "latest" else "explicit UTC interval",
        "sources": [], "summaries": [], "normalized_files": [], "warnings": [],
        "filename_partition_timezone": "America/Los_Angeles",
        "versions": {name: version(name) for name in ("kestrelgrid", "numpy", "pandas", "pyarrow", "plotly")},
    }
    dataset = Dataset(directory, manifest)
    start, end = selection.bounds
    for row in rows:
        remote = row["remote"]
        progress(f"Loading {remote.wmu}/{remote.name} ({row['transfer']})")
        download_dir = Path(root) / "data" / "downloads" / remote.wmu
        sample = directory / f"{remote.wmu}_{remote.name.rsplit('.', 1)[0]}.raw"
        if remote.kind == "raw":
            record = client.raw_sample(
                remote, sample, selection.duration_seconds,
                start.timestamp() if start is not None else None,
                end.timestamp() if end is not None else None,
                selection.max_samples,
            )
            effective_start = pd.to_datetime(record["requested_start"], unit="s", utc=True)
            effective_end = pd.to_datetime(record["requested_end"], unit="s", utc=True)
            table, summary = read_waveform(sample, effective_start, effective_end, selection.max_samples)
        else:
            downloaded = download_dir / remote.name
            record = client.download(remote, downloaded)
            if remote.kind == "7z":
                extracted = download_dir / f"{downloaded.stem}_{record['sha256'][:12]}_extracted"
                members = list(extracted.rglob("*.raw")) if extracted.exists() else extract_raw(downloaded, extracted)
                if len(members) != 1:
                    raise ValueError("Archive contains multiple raw files; select a member explicitly via local helpers")
                sample_record = local_raw_sample(
                    members[0], sample, selection.duration_seconds,
                    start.timestamp() if start is not None else None,
                    end.timestamp() if end is not None else None,
                    selection.max_samples,
                )
                record["extracted_member"] = members[0].name
                record["sample"] = sample_record
                effective_start = pd.to_datetime(sample_record["requested_start"], unit="s", utc=True)
                effective_end = pd.to_datetime(sample_record["requested_end"], unit="s", utc=True)
                table, summary = read_waveform(sample, effective_start, effective_end, selection.max_samples)
            else:
                table, summary = read_measurements(downloaded, remote.wmu, start, end, convention)
                if selection.mode == "latest" and not table.empty:
                    # Prefer waveform coverage; otherwise use the end of this measurement file.
                    waveform = dataset.waveforms.get(remote.wmu)
                    effective_end = (
                        waveform.time_utc.max() + pd.Timedelta(microseconds=1)
                        if waveform is not None else table.time_utc.max() + pd.Timedelta(microseconds=1)
                    )
                    effective_start = effective_end - pd.Timedelta(seconds=selection.duration_seconds)
                    table, summary = read_measurements(downloaded, remote.wmu, effective_start, effective_end, convention)
        record.update({"wmu": remote.wmu, "name": remote.name, "product": row["product"]})
        manifest["sources"].append(record)
        summary["product"] = row["product"]
        if summary["wmu"] != remote.wmu:
            raise ValueError("Decoded data sensor ID does not match selection")
        manifest["summaries"].append(summary)
        if table.empty:
            manifest["warnings"].append(f"No {row['product']} data in selected interval for {remote.wmu}")
            continue
        target = dataset.waveforms if row["product"] == "waveform" else dataset.measurements
        previous = target.get(remote.wmu)
        target[remote.wmu] = (
            pd.concat([previous, table], ignore_index=True).sort_values("time_utc")
            if previous is not None else table
        )
        if len(target[remote.wmu]) > selection.max_samples:
            raise ValueError("Combined dataset exceeds sample-memory limit")
    for product, tables in (("waveform", dataset.waveforms), ("measurements", dataset.measurements)):
        for wmu, table in tables.items():
            path = directory / f"{wmu}_{product}.parquet"
            table.to_parquet(path, index=False)
            manifest["normalized_files"].append({
                "wmu": wmu, "product": product, "path": path.name, "sha256": sha256(path),
            })
    manifest["bytes_transferred_this_run"] = client.transferred
    manifest["latest_age_seconds"] = {
        f"{product}/{wmu}": float((pd.Timestamp.now(tz="UTC") - table.time_utc.max()).total_seconds())
        for product, tables in (("waveform", dataset.waveforms), ("measurements", dataset.measurements))
        for wmu, table in tables.items()
    }
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "summary.json", manifest["summaries"])
    progress(f"Saved dataset {key}; transferred {client.transferred / 1024**2:.2f} MiB")
    return dataset


def demo_dataset(root: Path):
    """Offline synthetic dataset clearly separated from downloaded measurements."""
    import numpy as np

    directory = Path(root) / "data" / "processed" / "synthetic_demo"
    t = np.arange(8000) / 4000
    time = pd.Timestamp("2026-01-01T00:00:00Z")
    waveform = pd.DataFrame({
        "time_utc": time + pd.to_timedelta(t, unit="s"),
        "voltage_v": 120 * np.sqrt(2) * np.sin(2 * np.pi * 60 * t),
        "wmu": "4021", "gps_locked": True,
    })
    measurement_t = np.arange(120) / 2
    measurements = pd.DataFrame({
        "time_utc": time + pd.to_timedelta(measurement_t, unit="s"),
        "rms_v": 120 + 0.1 * np.sin(measurement_t / 5),
        "frequency_hz": 60 + 0.02 * np.sin(measurement_t / 10),
        "wmu": "4021", "gps_locked": True,
    })
    manifest = {"synthetic": True, "summaries": [{"wmu": "4021", "note": "SYNTHETIC demonstration"}], "normalized_files": []}
    directory.mkdir(parents=True, exist_ok=True)
    for product, table in (("waveform", waveform), ("measurements", measurements)):
        path = directory / f"4021_{product}.parquet"
        table.to_parquet(path, index=False)
        manifest["normalized_files"].append({"product": product, "wmu": "4021", "path": path.name, "sha256": sha256(path)})
    write_json(directory / "manifest.json", manifest)
    return Dataset(directory, manifest, {"4021": waveform}, {"4021": measurements})


def derive_dataset_frequency(dataset: Dataset, convention: PhaseConvention):
    """Recalculate from cached theta, without contacting the server."""
    from .frequency import phase_frequency

    for wmu, table in dataset.measurements.items():
        stored = any(
            summary.get("wmu") == wmu and summary.get("frequency_source") == "stored"
            for summary in dataset.manifest.get("summaries", [])
        )
        if "theta" in table and not stored:
            table["frequency_hz"] = phase_frequency(
                table.time_utc.astype("int64").to_numpy() / 1e9, table.theta, convention
            )
    dataset.manifest["interactive_phase_convention"] = convention.to_dict()
