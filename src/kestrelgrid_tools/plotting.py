"""One Plotly figure definition for notebook interaction and matching exports."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .common import write_json

MEASUREMENTS = {
    "voltage_v": ("Voltage waveform", "Voltage (V)"),
    "rms_v": ("Voltage RMS", "RMS (V)"),
    "frequency_hz": ("System frequency", "Frequency (Hz)"),
    "theta": ("Phase angle (source units)", "Theta"),
}
COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"]


def display_series(table, column, max_points=20_000):
    """Chronological min/max reduction per uninterrupted segment; retain gap breaks."""
    if max_points < 4:
        raise ValueError("max_points must be at least 4")
    times = table.time_utc
    values = table[column].to_numpy(dtype=float)
    seconds = times.astype("int64").to_numpy() / 1e9
    delta = np.diff(seconds)
    positive = delta[delta > 0]
    cadence = np.median(positive) if len(positive) else np.inf
    finite = np.isfinite(values)
    discontinuity = delta > cadence * 1.5
    if table.attrs.get("display_reduced"):
        # Sparse extrema have irregular spacing by design, not recording gaps.
        discontinuity = (
            np.diff(table["segment"].to_numpy()) != 0
            if "segment" in table
            else np.zeros(len(delta), dtype=bool)
        )
    breaks = np.flatnonzero(discontinuity | (delta <= 0) | ~finite[:-1] | ~finite[1:]) + 1
    pieces = np.split(np.arange(len(table)), breaks)
    if len(pieces) * 3 > max_points:
        raise ValueError("Too many disjoint segments for display limit; select a shorter interval")
    x, y = [], []
    reduced = len(table) > max_points or table.attrs.get("display_reduced", False)
    budget = (
        len(table)
        if len(table) + len(pieces) <= max_points
        else max(4, (max_points - len(pieces)) // max(1, len(pieces)))
    )
    for indices in pieces:
        if not len(indices):
            continue
        selected = indices
        if len(indices) > budget:
            selected_set = {indices[0], indices[-1]}
            for bucket in np.array_split(indices, max(1, (budget - 2) // 2)):
                finite = bucket[np.isfinite(values[bucket])]
                if len(finite):
                    selected_set.update(
                        [finite[np.argmin(values[finite])], finite[np.argmax(values[finite])]]
                    )
                else:
                    selected_set.add(bucket[0])
            selected = np.array(sorted(selected_set))
            reduced = True
        x.extend(times.iloc[selected].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").tolist())
        y.extend(values[selected].tolist())
        x.append(None)
        y.append(None)
    return x, y, reduced


def build_figure(tables, locations=None, columns=None, max_points=20_000, title="KGRID explorer"):
    """tables maps WMU IDs to normalized dataframes; reduction affects display only."""
    tables = {
        wmu: table.plot_table(max_points=max_points) if hasattr(table, "plot_table") else table
        for wmu, table in tables.items()
    }
    columns = columns or list(MEASUREMENTS)
    available = [column for column in columns if any(column in table for table in tables.values())]
    if not available:
        raise ValueError("No selected measurements available")
    figure = make_subplots(
        rows=len(available),
        cols=1,
        shared_xaxes=True,
        subplot_titles=[MEASUREMENTS[column][0] for column in available],
    )
    reduced = False
    intervals = []
    for color_index, (wmu, table) in enumerate(tables.items()):
        if table.empty:
            continue
        location = (locations or {}).get(wmu, {}).get("location", "Unknown location")
        label = f"{wmu} — {location}"
        if "gps_locked" in table:
            label += f" · GPS locked {table.gps_locked.mean():.0%}"
        intervals.extend([table.time_utc.min(), table.time_utc.max()])
        for row, column in enumerate(available, 1):
            if column not in table:
                continue
            x, y, downsampled = display_series(table, column, max_points)
            reduced |= downsampled
            figure.add_trace(
                go.Scattergl(
                    x=x,
                    y=y,
                    mode="lines",
                    name=label,
                    legendgroup=wmu,
                    showlegend=row == 1,
                    connectgaps=False,
                    line={"color": COLORS[color_index % len(COLORS)], "width": 1},
                    hovertemplate="%{x}<br>%{y:.6g}<extra>%{fullData.name}</extra>",
                ),
                row=row,
                col=1,
            )
            figure.update_yaxes(title_text=MEASUREMENTS[column][1], row=row, col=1)
    if not intervals:
        raise ValueError("No samples in selected plot interval")
    note = (
        "Min/max envelope — zoom does not recover omitted samples" if reduced else "Full resolution"
    )
    figure.update_layout(
        title={
            "text": f"{title}<br><sup>{min(intervals).isoformat()} to {max(intervals).isoformat()} · {note}</sup>"
        },
        template="plotly_white",
        height=max(450, 280 * len(available)),
        margin={"t": 100, "b": 65},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.12},
        meta={"display_reduced": reduced, "max_points_per_trace": max_points, "timezone": "UTC"},
    )
    figure.update_xaxes(type="date", title_text="Time (UTC)", row=len(available), col=1)
    return figure


def export_figure(
    figure,
    directory: Path,
    name="kgrid",
    formats=("png", "html"),
    width=1600,
    height=900,
    scale=2,
    max_html_mib=50,
):
    """Export a current-view snapshot to PNG/offline HTML and save plot metadata.

    Preserve zoom and visibility, enforce safe filenames/HTML size, and return output paths.
    PNG uses Kaleido and requires Chrome/Chromium on the host.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Export name may contain only letters, numbers, underscores and hyphens")
    if not formats or not set(formats) <= {"png", "html"}:
        raise ValueError("Choose png and/or html")
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = go.Figure(figure.to_plotly_json())
    outputs = []
    if "html" in formats:
        path = directory / f"{name}.html"
        document = snapshot.to_html(
            include_plotlyjs=True, full_html=True, config={"responsive": True, "displaylogo": False}
        )
        if len(document.encode("utf-8")) > max_html_mib * 1024**2:
            raise ValueError("HTML exceeds export-size limit; reduce display points or interval")
        path.write_text(document, encoding="utf-8")
        outputs.append(path)
    if "png" in formats:
        path = directory / f"{name}.png"
        snapshot.write_image(path, width=width, height=height, scale=scale)
        outputs.append(path)
    write_json(
        directory / f"{name}.plot.json",
        {
            "exported_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "formats": list(formats),
            "width": width,
            "height": height,
            "scale": scale,
            "layout": snapshot.layout.to_plotly_json(),
            "traces": [{"name": trace.name, "visible": trace.visible} for trace in snapshot.data],
        },
    )
    return outputs
