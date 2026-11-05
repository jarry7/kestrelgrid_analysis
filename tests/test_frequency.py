import json
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from kestrelgrid_tools.common import sha256, write_json
from kestrelgrid_tools.frequency import PhaseConvention, verify_phase_convention, waveform_frequency
from kestrelgrid_tools.workflow import load_dataset


def signal(frequency=60.1, seconds=20, start=0.0, rate=4000):
    t = start + np.arange(int(rate * seconds)) / rate
    return pd.DataFrame({
        "time_utc": pd.to_datetime(t, unit="s", utc=True),
        "voltage_v": 170 * np.sin(2 * np.pi * frequency * t)
                     + 4 * np.sin(2 * np.pi * 3 * frequency * t),
        "gps_locked": True, "segment": 0,
    })


@pytest.mark.parametrize("frequency", [49.95, 50.1, 59.95, 60.1])
def test_waveform_frequency_known_value_harmonics_and_batch_continuity(frequency):
    table = signal(frequency)
    expected = waveform_frequency(table)
    assert np.allclose(expected.frequency_hz, frequency, atol=0.0002)
    batched = Mock()
    batched.iter_tables.return_value = [table.iloc[:2345], table.iloc[2345:45000], table.iloc[45000:]]
    actual = waveform_frequency(batched)
    pd.testing.assert_frame_equal(actual, expected)


def test_waveform_frequency_gaps_nan_and_missing_cycles():
    first, second = signal(seconds=5), signal(seconds=5, start=8)
    second["segment"] = 1
    table = pd.concat([first, second], ignore_index=True)
    result = waveform_frequency(table)
    assert not result.time_utc.between(pd.Timestamp(4.5, unit="s", tz="UTC"),
                                       pd.Timestamp(8.5, unit="s", tz="UTC"), inclusive="neither").any()
    broken = signal(seconds=5)
    broken.loc[8000:8100, "voltage_v"] = np.nan
    result = waveform_frequency(broken)
    assert result.frequency_hz.dropna().between(60.09, 60.11).all()
    missing = signal(seconds=5)
    missing.loc[7000:7200, "voltage_v"] = -50
    result = waveform_frequency(missing)
    assert (~result.frequency_valid).any()
    assert result.frequency_hz[result.frequency_valid].between(60.09, 60.11).all()


def test_convention_validation_requires_distinct_evidence():
    t = np.arange(120) / 2
    deviation = 0.03 * np.sin(t / 6) + 0.01
    theta = np.angle(np.exp(1j * np.cumsum(deviation) * np.pi))
    measurements = pd.DataFrame({"time_utc": pd.to_datetime(t, unit="s", utc=True), "theta": theta})
    from kestrelgrid_tools.frequency import phase_frequency

    raw = pd.DataFrame({"time_utc": measurements.time_utc,
                        "frequency_hz": phase_frequency(t, theta, PhaseConvention())})
    evidence = verify_phase_convention(measurements, raw)
    assert evidence["convention"] == {"reference_hz": 60.0, "units": "radians", "sign": 1}
    assert evidence["rmse_hz"] < 1e-10
    raw["frequency_hz"] = 60.0
    assert verify_phase_convention(measurements, raw) is None


def make_dataset(root, table, registry=None):
    directory = root / "data/processed/example"
    directory.mkdir(parents=True)
    path = directory / "4021_measurements.parquet"
    table.to_parquet(path, index=False)
    source = "stored" if "frequency_hz" in table else "unavailable: theta convention not established"
    write_json(directory / "manifest.json", {
        "normalized_files": [{"path": path.name, "sha256": sha256(path), "wmu": "4021", "product": "measurements"}],
        "summaries": [{"wmu": "4021", "product": "measurements", "frequency_source": source}],
    })
    if registry:
        write_json(root / "config/frequency_conventions.json", registry)
    return directory


def test_cached_theta_dataset_gets_frequency_without_network_and_source_is_persisted(tmp_path):
    t = np.arange(40) / 2
    table = pd.DataFrame({"time_utc": pd.to_datetime(t, unit="s", utc=True),
                          "theta": np.angle(np.exp(1j * 2 * np.pi * .05 * t)), "rms_v": 120., "wmu": "4021"})
    registry = {"4021": {"convention": PhaseConvention().to_dict(), "evidence": {"status": "test verified"}}}
    directory = make_dataset(tmp_path, table, registry)
    dataset = load_dataset(directory)
    assert np.allclose(dataset.frequencies["4021"].frequency_hz.dropna(), 60.05)
    record = next(item for item in dataset.manifest["normalized_files"] if item["product"] == "frequency")
    assert sha256(directory / record["path"]) == record["sha256"]
    loaded = load_dataset(directory)
    pd.testing.assert_frame_equal(loaded.frequencies["4021"], dataset.frequencies["4021"])
    summary = next(item for item in loaded.manifest["summaries"] if item["product"] == "frequency")
    assert "verified" in summary["frequency_source"]
    assert "frequency_hz" not in loaded.measurements["4021"]


def test_stored_frequency_is_never_overridden(tmp_path):
    t = np.arange(40) / 2
    table = pd.DataFrame({"time_utc": pd.to_datetime(t, unit="s", utc=True), "frequency_hz": 59.99,
                          "theta": t * np.pi, "wmu": "4021"})
    dataset = load_dataset(make_dataset(tmp_path, table), convention=PhaseConvention(50, "degrees", -1))
    assert (dataset.frequencies["4021"].frequency_hz == 59.99).all()
    summary = next(item for item in dataset.manifest["summaries"] if item["product"] == "frequency")
    assert summary["frequency_source"] == "stored frequency" and summary["phase_convention"] is None


def test_unknown_device_does_not_guess_reference_and_new_registry_invalidates_cache(tmp_path):
    t = np.arange(40) / 2
    table = pd.DataFrame({"time_utc": pd.to_datetime(t, unit="s", utc=True), "theta": .1 * t, "wmu": "4021"})
    directory = make_dataset(tmp_path, table)
    first = load_dataset(directory)
    assert not first.frequencies and "4021" in first.manifest["frequency_unavailable"]
    write_json(tmp_path / "config/frequency_conventions.json", {
        "4021": {"convention": PhaseConvention(50, "radians", -1).to_dict()}
    })
    second = load_dataset(directory)
    assert "4021" in second.frequencies
    assert second.frequencies["4021"].frequency_hz.dropna().lt(50).all()
    assert "4021" not in second.manifest["frequency_unavailable"]
    assert json.loads((directory / "manifest.json").read_text())["frequency_algorithm_version"] == 1
