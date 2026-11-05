"""Automatic frequency products and local, evidence-backed device conventions."""

import json

import numpy as np

from .common import sha256, write_json
from .frequency import PhaseConvention, phase_frequency, verify_phase_convention, waveform_frequency
from .storage import project_root

ALGORITHM_VERSION = 1


def convention_registry(root):
    """Merge versioned and locally learned device conventions; local entries take precedence."""
    registry = {}
    for path in (
        root / "config/frequency_conventions.json",
        root / "data/frequency_conventions.json",
    ):
        if path.exists():
            registry.update(json.loads(path.read_text()))
    return registry


def ensure_frequency(dataset, convention=None):
    """Add frequency from stored data, waveform, or a verified per-device theta convention.

    Cached source data is never altered; derived frequency and its provenance are separate.
    No network is used, including for old datasets without a frequency product.
    """
    if dataset.manifest.get("synthetic") and dataset.frequencies and convention is None:
        return dataset
    root = project_root(dataset.directory)
    registry = convention_registry(root)
    registry_signature = json.dumps(registry, sort_keys=True)
    cached = (
        dataset.manifest.get("frequency_algorithm_version") == ALGORITHM_VERSION
        and dataset.manifest.get("frequency_registry_signature") == registry_signature
    )
    override = convention.to_dict() if convention else None
    if cached and dataset.manifest.get("frequency_override") == override:
        return dataset
    records, summaries = [], []
    dataset.frequencies = {}
    for wmu in dict.fromkeys([*dataset.measurements, *dataset.waveforms]):
        measurements = dataset.measurements.get(wmu)
        source_summary = next(
            (
                item
                for item in dataset.manifest.get("summaries", [])
                if item.get("wmu") == wmu and item.get("product") == "measurements"
            ),
            {},
        )
        stored = (
            measurements is not None
            and "frequency_hz" in measurements
            and not str(source_summary.get("frequency_source", "stored")).startswith("derived")
        )
        selected_convention = convention
        evidence = {"status": "user override"} if convention else None
        known = registry.get(wmu)
        if selected_convention is None and known:
            selected_convention = PhaseConvention(**known["convention"])
            evidence = known.get("evidence", known)
        raw = None
        if not stored and wmu in dataset.waveforms:
            raw = waveform_frequency(dataset.waveforms[wmu])
            if measurements is not None:
                validation = verify_phase_convention(measurements, raw)
                if validation:
                    selected_convention = convention or PhaseConvention(**validation["convention"])
                    if convention is None:
                        evidence = validation
                        registry[wmu] = {
                            "convention": validation["convention"],
                            "evidence": validation,
                        }
                        local = root / "data/frequency_conventions.json"
                        previous = json.loads(local.read_text()) if local.exists() else {}
                        previous[wmu] = registry[wmu]
                        write_json(local, previous)
                elif known and selected_convention and convention is None:
                    evidence = {**(evidence or {}), "current_window_revalidated": False}
        if stored:
            table = measurements[
                [
                    name
                    for name in ("time_utc", "frequency_hz", "gps_locked", "wmu")
                    if name in measurements
                ]
            ].copy()
            source = "stored frequency"
            selected_convention, evidence = None, None
        elif raw is not None and np.isfinite(raw.frequency_hz).any():
            table = raw.copy()
            table["wmu"] = wmu
            source = raw.attrs["frequency_source"]
        elif measurements is not None and "theta" in measurements and selected_convention:
            table = measurements[
                [name for name in ("time_utc", "gps_locked", "wmu") if name in measurements]
            ].copy()
            table["frequency_hz"] = phase_frequency(
                measurements.time_utc.astype("int64").to_numpy() / 1e9,
                measurements.theta.to_numpy(),
                selected_convention,
            )
            source = (
                "theta-derived frequency (user override)"
                if convention
                else "theta-derived frequency (verified WMU convention)"
            )
        else:
            dataset.manifest.setdefault("frequency_unavailable", {})[wmu] = (
                "No usable waveform/stored frequency and no verified theta convention for this WMU"
            )
            continue
        finite = np.isfinite(table.frequency_hz)
        if not finite.any():
            dataset.manifest.setdefault("frequency_unavailable", {})[wmu] = (
                "Not enough valid samples for frequency estimation"
            )
            continue
        dataset.manifest.setdefault("frequency_unavailable", {}).pop(wmu, None)
        table = table.reset_index(drop=True)
        path = dataset.directory / f"{wmu}_frequency.parquet"
        table.to_parquet(path, index=False)
        dataset.frequencies[wmu] = table
        records.append(
            {"wmu": wmu, "product": "frequency", "path": path.name, "sha256": sha256(path)}
        )
        values = table.frequency_hz[finite]
        summaries.append(
            {
                "wmu": wmu,
                "product": "frequency",
                "frequency_source": source,
                "sample_count": len(table),
                "valid_frequency_samples": int(finite.sum()),
                "start_utc": table.time_utc.iloc[0].isoformat(),
                "last_sample_utc": table.time_utc.iloc[-1].isoformat(),
                "minimum_hz": float(values.min()),
                "maximum_hz": float(values.max()),
                "mean_hz": float(values.mean()),
                "phase_convention": selected_convention.to_dict() if selected_convention else None,
                "convention_evidence": evidence,
                "quality_note": (
                    "Waveform: one-second window, positive zero crossings, fit/cycle checks; not a PMU compliance claim. "
                    "Theta: segment-aware derivatives; low-rate phase aliasing cannot always be detected."
                ),
            }
        )
    dataset.manifest["normalized_files"] = [
        item for item in dataset.manifest["normalized_files"] if item["product"] != "frequency"
    ] + records
    dataset.manifest["summaries"] = [
        item for item in dataset.manifest.get("summaries", []) if item.get("product") != "frequency"
    ] + summaries
    dataset.manifest["frequency_algorithm_version"] = ALGORITHM_VERSION
    dataset.manifest["frequency_override"] = override
    dataset.manifest["frequency_registry_signature"] = json.dumps(
        convention_registry(root), sort_keys=True
    )
    write_json(dataset.directory / "manifest.json", dataset.manifest)
    write_json(dataset.directory / "summary.json", dataset.manifest["summaries"])
    return dataset
