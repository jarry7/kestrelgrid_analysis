"""Display reduction, offline exports and dataset integrity tests."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from kestrelgrid_tools.plotting import (
    build_figure,
    build_measurements_figure,
    display_series,
    export_figure,
)
from kestrelgrid_tools.workflow import demo_dataset, load_dataset


def test_envelope_preserves_extrema_and_gap():
    """Retain waveform extrema and gap breaks within the display point budget."""
    values = np.zeros(1000)
    values[333], values[777] = 100, -200
    seconds = np.arange(1000, dtype=float)
    seconds[500:] += 100
    table = pd.DataFrame(
        {"time_utc": pd.to_datetime(seconds, unit="s", utc=True), "voltage_v": values}
    )
    x, y, reduced = display_series(table, "voltage_v", 100)
    assert reduced and 100 in y and -200 in y
    assert x.count(None) == 2
    assert len(x) <= 100


def test_offline_html_snapshot_cache_and_hash_check(tmp_path):
    """Preserve zoom/visibility in offline HTML and reject modified cached files."""
    dataset = demo_dataset(tmp_path)
    loaded = load_dataset(dataset.directory)
    assert len(loaded.waveforms["4021"]) == 8000
    figure = go.FigureWidget(build_figure(loaded.measurements, title="SYNTHETIC"))
    figure.data[0].visible = "legendonly"
    figure.update_xaxes(range=["2026-01-01T00:00:10Z", "2026-01-01T00:00:20Z"])
    outputs = export_figure(figure, dataset.directory / "plots", formats=("html",))
    text = outputs[0].read_text()
    assert "plotly.js" in text and "legendonly" in text and "00:00:10Z" in text
    assert '<script src="https://' not in text
    path = dataset.directory / "4021_waveform.parquet"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dataset(dataset.directory)


def test_frequency_edges_do_not_force_reduction():
    """Keep all available frequency points when only interval edges are invalid."""
    table = pd.DataFrame(
        {
            "time_utc": pd.date_range("2026-01-01", periods=14000, freq="500ms", tz="UTC"),
            "frequency_hz": np.r_[np.nan, np.full(13998, 60.01), np.nan],
        }
    )
    _, y, reduced = display_series(table, "frequency_hz", 20000)
    assert not reduced
    assert sum(value == 60.01 for value in y if value is not None) == 13998


def test_measurements_panels_preserve_independent_rms_and_frequency_times(tmp_path):
    """Place RMS above frequency without joining mismatched clocks or plotting theta."""
    measurements = pd.DataFrame(
        {
            "time_utc": pd.date_range("2026-07-22T11:30:00.2Z", periods=4, freq="500ms"),
            "rms_v": [120.0, 121.0, 122.0, 121.0],
            "theta": [1.0, 2.0, 3.0, 4.0],
        }
    )
    frequency = pd.DataFrame(
        {
            "time_utc": pd.date_range("2026-07-22T11:30:01Z", periods=3, freq="500ms"),
            "frequency_hz": [60.0, 60.05, 60.02],
        }
    )
    figure = build_measurements_figure({"4012": measurements}, {"4012": frequency})
    assert [annotation.text for annotation in figure.layout.annotations] == [
        "Voltage RMS",
        "System frequency",
    ]
    assert figure.layout.yaxis.title.text == "RMS (V)"
    assert figure.layout.yaxis2.title.text == "Frequency (Hz)"
    assert figure.data[0].yaxis == "y" and figure.data[1].yaxis == "y2"
    assert len(figure.data[0].x) == 5 and len(figure.data[1].x) == 4
    assert "00.200000Z" in figure.data[0].x[0]
    assert "01.000000Z" in figure.data[1].x[0]
    output = export_figure(figure, tmp_path, name="example_measurements", formats=("html",))[0]
    assert "System frequency" in output.read_text()
    assert "Phase angle" not in output.read_text()
