# Kestrel Grid analysis

Interactive Jupyter exploration of [KGRID](https://kestrelgrid.com/) voltage waveforms and
derived measurements. Select sensors and UTC intervals, download bounded samples, explore
Plotly plots, and export matching **PNG** and self-contained **HTML** files.

Repository: https://github.com/jarry7/kestrelgrid_analysis

## Start

From the cloned repository root:

```bash
uv sync
uv run jupyter lab notebooks/kgrid_explorer.ipynb
```

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is selected by `.python-version`.
Use the Python kernel from this project's environment. Run all notebook cells to display
the explorer. An **Offline synthetic demo** button exercises plotting without credentials
or network access. A **Load local dataset** button reuses downloaded data offline.

### Credentials

The account owner's credentials have been configured locally on the development machine.
For another installation, create `~/.config/kestrelgrid/credentials.json` outside the repository:

```json
{
  "username": "YOUR_USERNAME",
  "password": "YOUR_PASSWORD"
}
```

```bash
chmod 700 ~/.config/kestrelgrid
chmod 600 ~/.config/kestrelgrid/credentials.json
```

The notebook accepts a different credential-file path. Python callers can use the
`KESTRELGRID_CREDENTIALS` environment variable. Authentication is HTTP Basic over HTTPS;
the system trust store is used, including locally trusted institutional certificates.
TLS verification remains enabled. No credentials are included in this public repository.

### Export / archive dependencies

- **PNG:** Kaleido needs an existing Chrome/Chromium installation. If needed, install Chrome
  or run `uv run plotly_get_chrome` explicitly.
- **HTML:** embeds Plotly.js and the displayed samples; opens offline without a Python server.
- **`.7z` archives:** install 7-Zip (`brew install sevenzip` on macOS). Recent `.raw` files
  use HTTP Range sampling and do not need 7-Zip.

## Notebook workflow

1. Select WMUs, products, latest/explicit interval, and transfer budget.
2. **Discover / preview:** inspect candidate files. Unmapped WMUs can be entered in Other IDs.
3. **Download selected:** serial downloads, raw-frame validation, upstream decoding, and local
   normalized data storage. Default: WMU 4021, latest closed daily partition, 60 seconds,
   100 MiB transfer budget, five million waveform samples maximum.
4. **Update plots (local):** filter downloaded data and choose traces. To inspect individual
   waveform cycles, use a 0.1–1 second local plot interval. Zoom and toggle traces directly.
5. **Export current view:** writes PNG and HTML with the same visible traces and zoom ranges.

Changing controls never automatically downloads. Only discovery/download actions contact
the server. Changing a download selection requires another preview. A raw archive of roughly
661 MiB was sampled with only several MiB of range/probe traffic during live verification.

The provider asks for coordination for downloads exceeding about a dozen raw files.
Selections exceeding 12 waveform archives are blocked. The application does not mirror the
server, and enforces the configured cumulative transfer budget.

## Data and timing

| Input | Interpretation |
|---|---|
| `.raw` | Continuous voltage through `kestrelgrid==0.1.0`; scaled once, sample rate from headers |
| `.7z` | Bounded 7-Zip extraction, then selection of complete raw frames |
| `.parquet` | pandas/PyArrow; observed `t`, `rms`, `theta`, `gps` schema or recognized frequency schema |

All user bounds use explicit timezone-aware UTC, with half-open `[start, end)` selections.
Live inspection on 2026-11-05 showed Pacific-time daily partitions: `2026-11-04.raw` covers
approximately 2026-11-04 08:00 UTC through 2026-11-05 08:00 UTC. Filename partition handling
uses `America/Los_Angeles`, including DST. Filenames are selection hints; manifests record
actual timestamps. A future change to server partition conventions requires updating the
discovery adapter.

The current Parquet data is approximately 2 sps. The announced hourly 10-sps RMS/frequency
format and REST API were not present in the inspected WMU directory. Recognized hourly
filenames and common RMS/frequency columns are supported; an unknown schema raises an error
instead of silently guessing. Numeric `t` timestamps are interpreted as epoch seconds; the
reader exposes `timestamp_unit` for other confirmed numeric formats.

WMU locations are in `config/wmu_locations.json`. IDs in the directory that are absent from
the provider legend remain unknown locations. GPS-equipped is distinct from recorded GPS lock.

### Frequency from theta

Stored frequency is preferred. Current files contain theta without unit/reference/sign
metadata, so frequency derivation is **off by default**. Once the convention is established,
enable it and supply units, reference Hz, and derivative sign:

```text
frequency = reference_hz + sign × d(unwrapped_theta)/dt / (2π)
```

Derivatives are computed separately across continuous segments; segment edges and suspicious
phase jumps are unavailable. At 2 sps, phase unwrapping is ambiguous for deviations beyond
±1 Hz from the rotating reference, and not all aliasing can be detected. Do not assume a
60 Hz phase reference for the Lyon sensor solely from the provider's general description.

### Display fidelity

Plots keep gaps as breaks. Large traces use a chronological min/max envelope, clearly labelled
as reduced data. The full selected data remains in normalized Parquet; statistics use those
full-resolution values. Offline HTML can only zoom into the embedded plotted points, not
recover omitted samples. Adjust the notebook's local interval for full-resolution cycle views.
GPS-lock fractions are displayed per trace and waveform gaps/boot counts appear in summaries.

## Local files

```text
data/downloads/<WMU>/                  # Cached complete Parquet / .7z sources
data/processed/<selection-id>/
  manifest.json                       # Selection, hashes, source byte ranges, coverage, quality
  <WMU>_<date>.raw                     # Frame-aligned raw sample (not a full daily archive)
  <WMU>_waveform.parquet               # Exact selected full-resolution waveform samples
  <WMU>_measurements.parquet           # Selected derived measurements
  plots/
    kgrid_waveform.png / .html
    kgrid_measurements.png / .html
    *.plot.json                       # Current view / visibility / export settings
    plot_config.resolved.json
```

`data/` is Git-ignored. Notebook outputs are cleared in the repository. Exported HTML contains
the displayed data, not credentials. Persisted data bundles are hash-checked on offline reload.
An identical download selection can reuse a local bundle after discovery; use Load local
dataset for a completely offline workflow.

## Python usage

```python
from pathlib import Path
from kestrelgrid_tools.workflow import Selection, fetch_dataset, load_dataset
from kestrelgrid_tools.plotting import build_figure, export_figure

dataset = fetch_dataset(Path.cwd(), Selection(wmu_ids=("4021",), duration_seconds=60))
figure = build_figure(dataset.waveforms)
export_figure(figure, dataset.directory / "plots", name="waveform")

# Fully offline reload:
dataset = load_dataset(dataset.directory)
```

For an explicit interval:

```python
selection = Selection(
    wmu_ids=("4021",), mode="interval",
    start_utc="2026-11-05T07:59:00Z", end_utc="2026-11-05T08:00:00Z",
)
```

To export all samples in a frame-aligned raw file using the public package's CSV method:

```python
from kestrelgrid_tools.interpretation import export_raw_csv
export_raw_csv(next(dataset.directory.glob("*.raw")), dataset.directory / "raw_voltage.csv")
```

For exact selected boundaries, export `dataset.waveforms["4021"]` with pandas instead; a raw
file necessarily contains complete boundary frames.

## Verification

```bash
uv run pytest -q
uv run ruff check .
```

Tests use synthetic raw frames and Parquet files, mocked HTTP responses, and offline HTML
export. No account is required. The notebook and PNG export are additionally verified using
the local environment. See `IMPLEMENTATION_PLAN.md` for implementation decisions and limits.
