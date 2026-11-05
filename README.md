# Kestrel Grid analysis

Interactive Jupyter exploration of [KGRID](https://kestrelgrid.com/) voltage waveforms and
derived measurements. **Choose a WMU device and UTC start/end times, then click Download and
plot.** Matching **PNG** and self-contained **HTML** plots are saved automatically.

| Requested window | Automatically downloaded |
|---|---|
| Up to and including 1 hour | Selected raw waveform interval + available Parquet measurements |
| More than 1 hour | **Parquet only** — no raw or compressed waveform downloads |

Parquet contains lower-rate computed RMS/phase/frequency measurements. It retains its native
cadence; selecting a long interval does not introduce an additional averaging step.

Repository: https://github.com/jarry7/kestrelgrid_analysis

## Start

From the cloned repository root:

```bash
uv sync
uv run kgrid-notebook
```

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is selected by `.python-version`.
The launcher registers **KGRID Analysis (project Python)** using this environment's interpreter,
then starts JupyterLab with the repository as its file-server root. Select that named kernel.
After code updates use **Kernel → Restart Kernel and Run All Cells**; widgets saved in a notebook
from an old session do not contain live callbacks. Run all notebook cells to display
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

### Tested PJM July 22 presets

Select **4012 — Charlottesville VA** (closest listed device to Dominion/PJM) or
**4011 — Nashville TN** (Eastern Interconnection comparison, outside PJM), then choose:

- **PJM July 22 — event hour:** July 22, 07:30–08:30 EDT = **11:30–12:30 UTC**.
- **PJM July 22 — full Eastern day:** **04:00 UTC July 22 to 04:00 UTC July 23**; Parquet only.

Both devices/windows were exercised through real JupyterLab controls, including PNG/HTML
export links. The presentation's 07:56:09 time is interpreted as Eastern time; its timezone
is not explicitly labelled. See [PJM_JULY22_VERIFICATION.md](PJM_JULY22_VERIFICATION.md).

Export links are served relative to the repository root, independent of the kernel's
`notebooks/` working directory. Nashville has source timing anomalies: raw decoding now
reports these and excludes overlapping/backward samples in stream order rather than failing
the entire request. Gaps are retained and frequency windows do not span them.

1. Choose **one WMU** (default 4021) and defined **UTC start/end** times. The prefilled recent
   minute is editable; duration and automatic data mode are shown before execution.
2. **Download and plot:** discover sources, download serially, validate/decode, display plots,
   and save **both PNG and HTML** with device/window filenames. Output links and paths appear
   below. **Preview source files** is optional.
3. **Advanced settings:** credentials, optional unmapped device ID, transfer budget
   (100 MiB default), phase convention, display limit, PNG dimensions.
4. **Update plots (local):** filter downloaded data and choose traces. For individual waveform
   cycles, select a 0.1–1 second local interval, loaded from disk in bounded batches.
5. **Export current view:** save the current visible traces and zoom ranges again.

Changing controls never automatically downloads. Only discovery/download actions contact
the server. No separate preview is required for the primary action. A raw archive of roughly
661 MiB was sampled with only several MiB of range/probe traffic during live verification.

The provider asks for coordination for downloads exceeding about a dozen raw files.
Selections exceeding 12 waveform archives are blocked. The application does not mirror the
server, and enforces the configured cumulative transfer budget. The >1-hour routing rule
is enforced in the shared Python selection layer.

A full hour at 4 ksps contains **14.4 million samples** (~27.5 MiB raw payload). HTTP payloads
stream to disk; `kestrelgrid` decodes frame-aligned chunks of approximately 250,000 samples.
Statistics accumulate across all samples. A rate-based transfer estimate plus probe allowance
is checked before payload download. High-rate units may need a higher transfer budget.
Historical `.7z` inputs require their complete compressed archive to fit the budget.

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

### Automatic frequency plots

Frequency has its own interactive plot and PNG/HTML exports by default. Source priority:

1. **Stored Parquet frequency**, if present.
2. **Waveform estimate** for short windows: interpolate positive zero crossings from full
   voltage data and regress cycle count in one-second windows at 2 estimates/s. Timing gaps,
   invalid samples, inconsistent/missing cycles and poor fits are excluded. This is an analysis
   estimator, not a standards-compliant PMU algorithm.
3. **Theta derivative** for Parquet-only windows, with a verified per-WMU convention.

**WMU 4021 is verified:** radians, 60 Hz reference, positive derivative. Its cached hour agrees
with waveform estimates at correlation 0.9993 and RMSE 0.00052 Hz at measurement timestamps.
The automatic half-second-grid estimate revalidated it at 0.9984 correlation and 0.00080 Hz RMSE.
Evidence is in `config/frequency_conventions.json` and the derived dataset manifest.

WMUs **4012** and **4011** also have independently verified conventions from the July 22 tests
(both radians/60 Hz/+derivative). Evidence is included in the registry. Nashville was not GPS
locked, so its timestamps/frequency precision deserve separate interpretation.

Other WMUs are not assigned that convention. Simultaneous waveform/theta data is used to check
candidate 50/60 Hz references, units and signs when there is sufficient variation and overlap.
A distinct high-agreement winner is saved in ignored `data/frequency_conventions.json` for
later Parquet-only use. Otherwise short windows show waveform frequency and long windows
explain why frequency is unavailable. The Lyon reference is never guessed from location.

For a known manual convention, use **Advanced settings → Override automatic theta convention**:

```text
frequency = reference_hz + sign × d(unwrapped_theta)/dt / (2π)
```

Derivatives are computed separately across continuous segments; segment edges and suspicious
phase jumps are unavailable. At 2 sps, deviations beyond ±1 Hz from the rotating reference can
alias undetectably. Stored frequency always wins, even with a manual override. Frequency uses
full-resolution input, never the plotting envelope. Waveform times are one-second window
centers, leaving unsupported interval edges unavailable.

Existing cached datasets gain frequency locally when loaded; no download is required. Source
measurements/theta stay unchanged. Computed frequency is stored separately with hashes and
provenance. Revalidate device conventions if provider definitions change.

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
  <WMU>_<date>_waveform.parquet        # Disk-backed full-resolution waveform, bounded row groups
  <WMU>_measurements.parquet           # Selected derived measurements
  <WMU>_frequency.parquet              # Stored or computed frequency, with provenance
  plots/
    wmu<id>_<start>_<end>_waveform.png / .html
    wmu<id>_<start>_<end>_measurements.png / .html
    wmu<id>_<start>_<end>_frequency.png / .html
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

dataset = fetch_dataset(Path.cwd(), Selection(
    wmu_ids=("4021",), start_utc="2026-11-05T07:59:00Z", end_utc="2026-11-05T08:00:00Z",
))
figure = build_figure(dataset.waveforms)
export_figure(figure, dataset.directory / "plots", name="waveform")

# Fully offline reload:
dataset = load_dataset(dataset.directory)
# Separate frequency dataframe / plot:
frequency = dataset.frequencies["4021"]
figure = build_figure(dataset.frequencies, columns=["frequency_hz"])
export_figure(figure, dataset.directory / "plots", name="frequency")
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

For an exact local full-resolution window:

```python
store = dataset.waveforms["4021"]
detail = store.to_dataframe(start="2026-11-05T07:59:10Z", end="2026-11-05T07:59:11Z")
detail.to_csv(dataset.directory / "detail.csv", index=False)
# Large full-resolution processing: for table in store.iter_tables(): ...
```

`waveforms` now contains disk-backed `WaveformStore` objects, not whole-window dataframes.
Default `to_dataframe()` limits loads to one million samples; use a smaller interval or iterate
batches. The notebook automatically builds bounded min/max previews. Full boundary frames
are retained in `.raw` files; normalized Parquet contains the exact requested samples.

## Verification

```bash
uv run pytest -q
uv run ruff check .
```

Tests use synthetic raw frames and Parquet files, mocked HTTP responses, and offline HTML
export. No account is required. The notebook and PNG export are additionally verified using
the local environment. See `IMPLEMENTATION_PLAN.md` for implementation decisions and limits.
