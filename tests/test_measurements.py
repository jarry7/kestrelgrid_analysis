"""Phase timing quality and recognized measurement schema tests."""

import numpy as np
import pandas as pd
import pytest

from kestrelgrid_tools.frequency import PhaseConvention, phase_frequency
from kestrelgrid_tools.parquet_reader import read_measurements


def test_phase_known_frequency_gaps_and_duplicates():
    """Recover a known offset without deriving across gaps or duplicate times."""
    t = np.r_[np.arange(10) / 2, 10 + np.arange(10) / 2]
    theta = np.angle(np.exp(1j * 2 * np.pi * 0.1 * t))
    result = phase_frequency(t, theta, PhaseConvention())
    assert np.allclose(result[np.isfinite(result)], 60.1)
    assert np.isnan(result[[0, 9, 10, 19]]).all()
    duplicate = phase_frequency([0, 0.5, 0.5, 1, 1.5], [0, 0.1, 0.1, 0.2, 0.3], PhaseConvention())
    assert np.isnan(duplicate[1:3]).all()


def test_parquet_current_and_updated_schema(tmp_path):
    """Read theta and stored-frequency products and reject unknown schemas."""
    path = tmp_path / "measurements.parquet"
    t = 1_700_000_000 + np.arange(20) / 2
    df = pd.DataFrame({"t": t, "theta": 2 * np.pi * 0.1 * t, "rms": 120.0, "gps": True})
    df.to_parquet(path)
    table, summary = read_measurements(path, "4021")
    assert "frequency_hz" not in table
    assert summary["cadence_seconds"] == 0.5
    table, summary = read_measurements(path, "4021", convention=PhaseConvention())
    assert np.allclose(table.frequency_hz.dropna(), 60.1, atol=1e-6)
    df["frequency"] = 59.99
    df.to_parquet(path)
    table, summary = read_measurements(path, "4021", convention=PhaseConvention())
    assert summary["frequency_source"] == "stored"
    assert (table.frequency_hz == 59.99).all()
    pd.DataFrame({"unknown": [1]}).to_parquet(path)
    with pytest.raises(ValueError, match="Unknown"):
        read_measurements(path, "4021")
