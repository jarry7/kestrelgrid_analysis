"""Reproducible selections, cached dataset bundles, and notebook-facing orchestration."""

import json
import math
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from pathlib import Path

import pandas as pd

from .archive import extract_raw
from .client import KGridClient
from .common import sha256, utc, write_json
from .discovery import select_files
from .frequency_workflow import ensure_frequency
from .parquet_reader import read_measurements
from .raw_validation import iter_raw_frames
from .storage import (
    analysis_directory,
    available_directory,
    device_directory,
    identity,
    reuse_dataset,
)
from .waveform_store import WaveformStore, normalize_waveform


@dataclass
class Selection:
    """Explicit UTC interval and resource limits shared by notebook and Python callers."""

    wmu_ids: tuple[str, ...] = ("4021",)
    start_utc: str | None = None
    end_utc: str | None = None
    max_download_mib: float = 100
    max_samples: int = 240_000_000

    def validate(self):
        """Reject invalid devices, timezone-naive/reversed bounds, and nonpositive limits."""
        if not self.wmu_ids or any(not str(wmu).isdigit() for wmu in self.wmu_ids):
            raise ValueError("Select at least one numeric WMU ID")
        if any(
            not math.isfinite(value) or value <= 0
            for value in (self.max_download_mib, self.max_samples)
        ):
            raise ValueError("Resource limits must be positive")
        if not self.start_utc or not self.end_utc or utc(self.end_utc) <= utc(self.start_utc):
            raise ValueError("Provide a valid timezone-aware start/end interval")

    @property
    def bounds(self):
        """Return the requested half-open interval as UTC timestamps."""
        return utc(self.start_utc), utc(self.end_utc)

    @property
    def requested_seconds(self):
        """Return elapsed seconds between requested bounds."""
        start, end = self.bounds
        return (end - start).total_seconds()

    @property
    def products(self):
        """Select Parquet only beyond one hour, otherwise waveform and Parquet."""
        return ("measurements",) if self.requested_seconds > 3600 else ("waveform", "measurements")

    @property
    def data_mode(self):
        """Return the duration-dependent download mode stored in manifests."""
        return "parquet_only" if self.requested_seconds > 3600 else "waveform_and_parquet"


@dataclass
class Dataset:
    """Local manifest, disk-backed waveforms, and measurement/frequency dataframes."""

    directory: Path
    manifest: dict
    waveforms: dict[str, WaveformStore | pd.DataFrame] = field(default_factory=dict)
    measurements: dict[str, pd.DataFrame] = field(default_factory=dict)
    frequencies: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def summary(self):
        """Return manifest quality summaries as a dataframe."""
        return pd.DataFrame(self.manifest.get("summaries", []))


def preview(client, selection: Selection):
    """Discover matching partitions and enforce the provider's waveform-file limit."""
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
                rows.append(
                    {
                        "wmu": str(wmu),
                        "product": product,
                        "file": item.name,
                        "size_label": item.size_label,
                        "partition_utc": item.start.isoformat(),
                        "transfer": "bounded range" if item.kind == "raw" else "complete file",
                        "remote": item,
                    }
                )
    if not rows:
        raise ValueError("No files available for this selection")
    if sum(row["product"] == "waveform" for row in rows) > 12:
        raise ValueError("More than 12 waveform archives selected; coordinate with the provider")
    return rows


def local_raw_sample(source, destination, start, end, max_samples=5_000_000):
    """Index an extracted archive without loading its full waveform into memory."""
    frames = list(iter_raw_frames(source))
    with source.open("rb") as handle:
        if not frames:
            raise ValueError("Empty extracted raw file")
        chosen = [frame for frame in frames if frame.timestamp < end and frame.end > start]
        if not chosen:
            raise ValueError("No frames overlap requested interval")
        if sum(frame.n_samples for frame in chosen) > max_samples:
            raise ValueError("Extracted sample exceeds sample-memory limit")
        handle.seek(chosen[0].offset)
        remaining = chosen[-1].offset + chosen[-1].n_bytes - chosen[0].offset
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(".raw.part")
        with part.open("wb") as output:
            while remaining:
                chunk = handle.read(min(1024**2, remaining))
                if not chunk:
                    raise ValueError("Incomplete extracted source")
                output.write(chunk)
                remaining -= len(chunk)
        part.replace(destination)
    return {"requested_start": start, "requested_end": end, "sha256": sha256(destination)}


def load_dataset(directory: Path, convention=None) -> Dataset:
    """Hash-check local normalized files and generate/reload frequency without network access."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    dataset = Dataset(directory, manifest)
    for record in manifest["normalized_files"]:
        path = (directory / record["path"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Invalid dataset path")
        if sha256(path) != record["sha256"]:
            raise ValueError("Cached dataset hash mismatch")
        if record["product"] == "waveform":
            previous = dataset.waveforms.get(record["wmu"])
            dataset.waveforms[record["wmu"]] = WaveformStore(
                [*(previous.paths if previous else []), path]
            )
        elif record["product"] == "frequency":
            dataset.frequencies[record["wmu"]] = pd.read_parquet(path)
        else:
            table = pd.read_parquet(path)
            previous = dataset.measurements.get(record["wmu"])
            dataset.measurements[record["wmu"]] = (
                pd.concat([previous, table], ignore_index=True) if previous is not None else table
            )
    return ensure_frequency(dataset, convention)


def fetch_dataset(
    root: Path,
    selection: Selection,
    credentials=None,
    convention=None,
    client=None,
    progress=print,
    analysis_name="",
):
    """Reuse or download an explicit interval into a readable named analysis directory.

    Analysis names and resource budgets do not alter cache identity. Waveforms stream to
    disk; normalized manifests retain source hashes, timing quality and frequency provenance.
    """
    selection.validate()
    root = Path(root).resolve()
    start, end = selection.bounds
    convention_value = convention.to_dict() if convention else None
    key = identity(selection.wmu_ids, start, end, convention_value)
    base = analysis_directory(root, selection, analysis_name)
    cached = reuse_dataset(root, selection, analysis_name, convention_value)
    if cached:
        progress(f"Reusing local analysis {cached}")
        return load_dataset(cached, convention)
    directory = available_directory(base, key)
    client = client or KGridClient(credentials, selection.max_download_mib)
    rows = preview(client, selection)
    configuration = {
        "cache_schema_version": 5,
        "selection": asdict(selection),
        "automatic_data_mode": selection.data_mode,
        "effective_products": list(selection.products),
        "phase_convention": convention.to_dict() if convention else None,
        "source_files": [{"wmu": row["wmu"], "file": row["file"]} for row in rows],
    }
    estimated_bytes = 0
    for row in rows:
        remote = row["remote"]
        if remote.kind == "raw":
            estimate = client.estimate_raw_transfer(remote, start, end)
            estimated_bytes += (
                estimate["payload_estimate_bytes"] + estimate["probe_allowance_bytes"]
            )
            if estimate["sample_estimate"] > selection.max_samples:
                raise ValueError("Requested interval exceeds sample limit")
        else:
            metadata = client.metadata(remote.url)
            estimate = {"complete_file_bytes": metadata["size"]}
            estimated_bytes += metadata["size"]
        row["transfer_estimate"] = estimate
    if estimated_bytes > client.remaining:
        raise ValueError(
            f"Estimated transfer including probes: {estimated_bytes / 1024**2:.1f} MiB; "
            f"remaining budget: {client.remaining / 1024**2:.1f} MiB. Increase Max MiB before downloading."
        )
    progress(
        f"Automatic mode: {selection.data_mode}; estimated transfer with probe allowance: {estimated_bytes / 1024**2:.1f} MiB"
    )
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        **configuration,
        "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selection_rule": "explicit UTC interval",
        "analysis_name": analysis_name.strip() or "Unnamed analysis",
        "selection_id": key,
        "sources": [],
        "summaries": [],
        "normalized_files": [],
        "warnings": [],
        "filename_partition_timezone": "America/Los_Angeles",
        "versions": {
            name: version(name) for name in ("kestrelgrid", "numpy", "pandas", "pyarrow", "plotly")
        },
        "requested_duration_seconds": selection.requested_seconds,
        "estimated_transfer_bytes": estimated_bytes,
    }
    dataset = Dataset(directory, manifest)
    for row in rows:
        remote = row["remote"]
        progress(f"Loading {remote.wmu}/{remote.name} ({row['transfer']})")
        download_dir = root / "data/downloads" / device_directory(root, [remote.wmu])
        legacy_cache = root / "data/downloads" / remote.wmu
        if legacy_cache.exists() and not download_dir.exists():
            legacy_cache.rename(download_dir)
        sample = directory / f"{remote.wmu}_{remote.name.rsplit('.', 1)[0]}.raw"
        normalized = directory / f"{remote.wmu}_{remote.name.rsplit('.', 1)[0]}_waveform.parquet"
        if remote.kind == "raw":
            record = client.raw_sample(
                remote,
                sample,
                start=start.timestamp(),
                end=end.timestamp(),
                max_samples=selection.max_samples,
            )
            effective_start = pd.to_datetime(record["requested_start"], unit="s", utc=True)
            effective_end = pd.to_datetime(record["requested_end"], unit="s", utc=True)
            table, summary = normalize_waveform(sample, normalized, effective_start, effective_end)
        else:
            downloaded = download_dir / remote.name
            record = client.download(remote, downloaded)
            if remote.kind == "7z":
                extracted = download_dir / f"{downloaded.stem}_{record['sha256'][:12]}_extracted"
                members = (
                    list(extracted.rglob("*.raw"))
                    if extracted.exists()
                    else extract_raw(downloaded, extracted)
                )
                if len(members) != 1:
                    raise ValueError(
                        "Archive contains multiple raw files; select a member explicitly via local helpers"
                    )
                sample_record = local_raw_sample(
                    members[0],
                    sample,
                    start.timestamp(),
                    end.timestamp(),
                    selection.max_samples,
                )
                record["extracted_member"] = members[0].name
                record["sample"] = sample_record
                effective_start = pd.to_datetime(
                    sample_record["requested_start"], unit="s", utc=True
                )
                effective_end = pd.to_datetime(sample_record["requested_end"], unit="s", utc=True)
                table, summary = normalize_waveform(
                    sample, normalized, effective_start, effective_end
                )
            else:
                table, summary = read_measurements(downloaded, remote.wmu, start, end, convention)
        record.update(
            {
                "wmu": remote.wmu,
                "name": remote.name,
                "product": row["product"],
                "transfer_estimate": row["transfer_estimate"],
            }
        )
        manifest["sources"].append(record)
        summary["product"] = row["product"]
        if summary["wmu"] != remote.wmu:
            raise ValueError("Decoded data sensor ID does not match selection")
        manifest["summaries"].append(summary)
        if table.empty:
            manifest["warnings"].append(
                f"No {row['product']} data in selected interval for {remote.wmu}"
            )
            continue
        target = dataset.waveforms if row["product"] == "waveform" else dataset.measurements
        previous = target.get(remote.wmu)
        if row["product"] == "waveform":
            target[remote.wmu] = WaveformStore(
                [*(previous.paths if previous else []), *table.paths]
            )
            manifest["normalized_files"].append(
                {
                    "wmu": remote.wmu,
                    "product": "waveform",
                    "path": normalized.name,
                    "sha256": sha256(normalized),
                }
            )
        else:
            target[remote.wmu] = (
                pd.concat([previous, table], ignore_index=True) if previous is not None else table
            )
            if len(target[remote.wmu]) > 5_000_000:
                raise ValueError("Combined Parquet interval exceeds five million rows; shorten it")
    for wmu, table in dataset.measurements.items():
        table = table.sort_values("time_utc", kind="stable")
        duplicate_count = int(table.time_utc.duplicated().sum())
        if duplicate_count:
            manifest["warnings"].append(
                f"{wmu}: removed {duplicate_count} duplicate measurement timestamps"
            )
        table = table.drop_duplicates("time_utc", keep="first").reset_index(drop=True)
        dataset.measurements[wmu] = table
        cadence = table.time_utc.diff().dt.total_seconds()
        typical = cadence[cadence > 0].median()
        gap_count = int((cadence > 1.5 * typical).sum())
        if gap_count:
            manifest["warnings"].append(f"{wmu}: {gap_count} gaps in combined Parquet measurements")
        path = directory / f"{wmu}_measurements.parquet"
        table.to_parquet(path, index=False)
        manifest["normalized_files"].append(
            {
                "wmu": wmu,
                "product": "measurements",
                "path": path.name,
                "sha256": sha256(path),
            }
        )
    if start is not None:
        for wmu in selection.wmu_ids:
            for product in selection.products:
                intervals = sorted(
                    (max(start, item["remote"].start), min(end, item["remote"].end))
                    for item in rows
                    if item["wmu"] == wmu and item["product"] == product
                )
                cursor = start
                for left, right in intervals:
                    if left > cursor:
                        manifest["warnings"].append(
                            f"{wmu} {product}: missing source partition {cursor} to {left}"
                        )
                    cursor = max(cursor, right)
                if cursor < end:
                    manifest["warnings"].append(
                        f"{wmu} {product}: missing source partition {cursor} to {end}"
                    )
                summaries = [
                    item
                    for item in manifest["summaries"]
                    if item["wmu"] == wmu and item["product"] == product and item["sample_count"]
                ]
                if summaries:
                    actual_start = min(utc(item["start_utc"]) for item in summaries)
                    actual_last = max(utc(item["last_sample_utc"]) for item in summaries)
                    manifest["warnings"].append(
                        f"{wmu} {product}: requested [{start}, {end}); actual samples {actual_start} to {actual_last}"
                    )
                    if product == "waveform":
                        for previous, current in zip(summaries, summaries[1:]):
                            rates = previous["sample_rates_hz"]
                            difference = (
                                utc(current["start_utc"]) - utc(previous["last_sample_utc"])
                            ).total_seconds()
                            if difference > 1.5 / min(rates):
                                manifest["warnings"].append(
                                    f"{wmu}: waveform gap across partitions: {difference:.6f} seconds"
                                )
    if not dataset.waveforms and not dataset.measurements:
        raise ValueError("No data available within requested interval")
    manifest["bytes_transferred_this_run"] = client.transferred
    manifest["latest_age_seconds"] = {
        f"{product}/{wmu}": float(
            (pd.Timestamp.now(tz="UTC") - table.time_utc.max()).total_seconds()
        )
        for product, tables in (("measurements", dataset.measurements),)
        for wmu, table in tables.items()
    }
    for summary in manifest["summaries"]:
        if summary["product"] == "waveform":
            manifest["latest_age_seconds"][f"waveform/{summary['wmu']}"] = float(
                (pd.Timestamp.now(tz="UTC") - utc(summary["last_sample_utc"])).total_seconds()
            )
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "summary.json", manifest["summaries"])
    progress(f"Saved dataset {key}; transferred {client.transferred / 1024**2:.2f} MiB")
    return ensure_frequency(dataset, convention)


def demo_dataset(root: Path):
    """Offline synthetic dataset clearly separated from downloaded measurements."""
    import numpy as np

    selection = Selection(start_utc="2026-01-01T00:00:00Z", end_utc="2026-01-01T00:01:00Z")
    directory = analysis_directory(root, selection, "synthetic-demo")
    t = np.arange(8000) / 4000
    time = pd.Timestamp("2026-01-01T00:00:00Z")
    waveform = pd.DataFrame(
        {
            "time_utc": time + pd.to_timedelta(t, unit="s"),
            "voltage_v": 120 * np.sqrt(2) * np.sin(2 * np.pi * 60 * t),
            "wmu": "4021",
            "gps_locked": True,
        }
    )
    measurement_t = np.arange(120) / 2
    measurements = pd.DataFrame(
        {
            "time_utc": time + pd.to_timedelta(measurement_t, unit="s"),
            "rms_v": 120 + 0.1 * np.sin(measurement_t / 5),
            "frequency_hz": 60 + 0.02 * np.sin(measurement_t / 10),
            "wmu": "4021",
            "gps_locked": True,
        }
    )
    manifest = {
        "synthetic": True,
        "analysis_name": "Synthetic demo",
        "selection": asdict(selection),
        "summaries": [{"wmu": "4021", "note": "SYNTHETIC demonstration"}],
        "normalized_files": [],
    }
    directory.mkdir(parents=True, exist_ok=True)
    for product, table in (("waveform", waveform), ("measurements", measurements)):
        path = directory / f"4021_{product}.parquet"
        table.to_parquet(path, index=False)
        manifest["normalized_files"].append(
            {"product": product, "wmu": "4021", "path": path.name, "sha256": sha256(path)}
        )
    write_json(directory / "manifest.json", manifest)
    return Dataset(directory, manifest, {"4021": waveform}, {"4021": measurements})
