# Kestrel Grid analysis

Explore [KGRID](https://kestrelgrid.com/) voltage and computed measurements in JupyterLab.
Choose an optional **analysis name**, one **WMU**, and **UTC start/end** times, then select
**Download and plot**. Interactive figures and matching PNG/offline HTML exports are saved.

| Requested duration | Downloaded products |
|---|---|
| Up to and including one hour | Selected waveform interval and available Parquet measurements |
| More than one hour | Parquet measurements only; no raw/7z waveform downloads |

Parquet retains its native cadence; this routing does not add temporal averaging.

## Start

From the repository root:

```bash
uv sync
uv run kgrid-notebook
```

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. The launcher registers
**KGRID Analysis (project Python)** and sets the Jupyter file-server root to the repository.
Select that kernel and run all cells. After a code update, restart the kernel and run all cells
to initialize live widgets. **Load local dataset** and **Offline synthetic demo** work offline.

### Credentials and external tools

Create `~/.config/kestrelgrid/credentials.json` outside the repository:

```json
{"username": "YOUR_USERNAME", "password": "YOUR_PASSWORD"}
```

Set its containing directory to `0700` and the file to `0600`. The notebook accepts another
credential path; Python callers can set `KESTRELGRID_CREDENTIALS`. HTTP Basic authentication
uses verified HTTPS and the operating system's trust store. Credentials are never included
in manifests or exports.

- **PNG:** Kaleido requires Chrome/Chromium. Install Chrome or run `uv run plotly_get_chrome`.
- **HTML:** embeds Plotly.js and the plotted data; no Python server or internet is required.
- **7z:** install 7-Zip (`brew install sevenzip` on macOS) for compressed historical archives.

## Notebook workflow

1. Enter **Analysis name** (optional), select a WMU and set an explicit UTC interval.
2. Review duration, automatic product mode and the displayed destination.
3. Select **Download and plot**. Previewing source files first is optional.
4. Inspect quality summaries and interactive plots. Local bounds can select a short
   full-resolution waveform window without another download.
5. **Export current view** saves modified zoom ranges and visible traces.

The `*_measurements.png` and `*_measurements.html` figures show **RMS voltage above frequency**
on a shared time axis. Frequency uses its selected stored/waveform/theta-derived source and
retains its native timestamps; RMS and frequency are not interpolated onto a common grid.
Theta remains available in the source measurement dataframe. Separate frequency exports are
also available.

Advanced settings contain the transfer budget, numeric WMU override, phase convention,
display point limit and PNG dimensions. Changing controls does not trigger downloads.

The cumulative transfer budget defaults to 100 MiB. A 4 ksps hour contains 14.4 million
samples (~27.5 MiB raw payload); payloads stream to disk and decode in approximately
250,000-sample chunks. Full-resolution statistics accumulate across chunks. Preflight header
probes estimate transfers; higher-rate units or complete 7z archives may require larger budgets.
The server is bandwidth constrained; selections beyond 12 waveform archives require provider
coordination and are rejected by the application.

## Named storage

An analysis name such as **PJM July 22 load transfer** becomes `pjm-july-22-load-transfer`.
Blank labels use `unnamed-analysis`. Directory names contain the device/location, exact UTC
window and data mode. Unsafe paths are rejected; conflicting processing settings use `__v2`,
`__v3`, etc. rather than overwriting existing results.

```text
data/
  downloads/
    wmu4012_charlottesville-va/          # Shared daily Parquet/7z source cache
      2026-07-22.parquet
  analyses/
    pjm-july-22-load-transfer/
      wmu4012_charlottesville-va/
        2026-07-22T113000Z_to_2026-07-22T123000Z__waveform/
          manifest.json
          summary.json
          4012_2026-07-22.raw            # Frame-aligned partial daily source
          4012_2026-07-22_waveform.parquet
          4012_measurements.parquet
          4012_frequency.parquet
          plots/
            wmu4012_<start>_<end>_waveform.png / .html
            wmu4012_<start>_<end>_measurements.png / .html
            wmu4012_<start>_<end>_frequency.png / .html
            *.plot.json
            plot_config.resolved.json
```

Names are labels; internal selection IDs match WMUs, UTC bounds and phase settings.
Changing a label or resource budget reuses matching data **before authentication/discovery**.
An independently named analysis receives a local copy, keeping its manifests/exports separate.
Local dataset controls display analysis name, devices, UTC interval and mode instead of hashes.
All `data/` contents are Git-ignored; manifests record hashes, source ranges and actual coverage.

### Migrate older caches

Existing hash-named `data/processed/` bundles remain loadable. Preview their proposed moves:

```bash
uv run kgrid-migrate
uv run kgrid-migrate --analysis-name "PJM July 22 load transfer"
```

Apply after reviewing the proposal:

```bash
uv run kgrid-migrate --apply
```

Migration verifies normalized files before moving, updates embedded dataset metadata, renames
numeric download-cache directories and never overwrites a destination. No downloads occur.
Legacy latest-mode bundles are preserved but are not silently matched to explicit-window
requests because their clipping rules differ.

## Data interpretation

- `.raw`: voltage decoded by `kestrelgrid==0.1.0`, scaled once; sample rate comes from headers.
- `.7z`: bounded extraction, then a selected raw interval. Automatic extraction expects one
  raw member; use lower-level helpers to select from multi-member archives.
- `.parquet`: pandas/PyArrow; known timestamp, RMS, theta, GPS and frequency column aliases.
  Unknown schemas and naive timestamps are rejected. Numeric source `t` uses epoch seconds.

User windows use half-open `[start, end)` UTC bounds. Server daily filenames represent
`America/Los_Angeles` partitions, including DST; actual timestamps determine sample clipping.
Parquet's observed cadence is about 2 sps; explicit hourly schemas are supported when available.

Incomplete payloads, invalid headers and mixed sensors are rejected. Repeated/backward raw
times are reported; overlapping samples are excluded in stream order, retaining gap segments.
The upstream time/voltage converter skips backward frames, so anomalous chunks use the
public raw reader's preserved frame data and explicit timing instead. GPS-equipped hardware
does not imply recorded GPS lock. Quality summaries identify lock, boot counts and coverage.

### Frequency

Source priority is stored Hz, full-resolution waveform estimation, then verified per-WMU
theta conversion. Each figure title and manifest identifies the source.

Waveform estimation interpolates positive zero crossings and regresses cycle count in
one-second windows at 2 estimates/s. Gaps, invalid samples, inconsistent periods and poor fits
are excluded. It is an analysis estimator, not a standards-compliant PMU algorithm.

Theta conversion uses an established angle unit, rotating reference and derivative sign:

```text
frequency = reference_hz + sign × d(unwrapped_theta)/dt / (2π)
```

Verified conventions for WMUs 4021, 4012 and 4011 are in `config/frequency_conventions.json`.
Other devices require sufficient simultaneous waveform/theta evidence; learned conventions
are stored locally in `data/frequency_conventions.json`. Unknown references are not guessed.
Manual overrides are available; stored frequency always takes precedence. At 2 sps, deviations
beyond ±1 Hz from the reference can alias undetectably. Revalidate if provider definitions change.

Computed frequency is saved separately without modifying original measurement/theta files.
Loading older caches adds frequency locally. Plot reductions never feed the frequency estimator.

### Display fidelity

Large figures use labelled chronological min/max envelopes; gaps remain breaks. Full selected
data stays in local Parquet. Standalone HTML only zooms into embedded points; use shorter local
notebook bounds for full-resolution waveform cycles. Statistics use full-resolution inputs.

## PJM July 22 presets

Select 4012 (Charlottesville VA, closest listed to Dominion/PJM) or 4011 (Nashville TN,
Eastern Interconnection comparison outside PJM), then choose an event preset:

- **Event hour:** July 22, 2026 07:30–08:30 EDT = 11:30–12:30 UTC.
- **Full Eastern day:** July 22 04:00 UTC to July 23 04:00 UTC; Parquet only.

The [PJM presentation](https://www.pjm.com/-/media/DotCom/committees-groups/committees/oc/2026/20260806/20260806-item-05---dominion-july-loss-of-load-event---presentation.pdf)
reports initial line opening at 07:56:09 and a 60.092 Hz frequency peak. Eastern timezone is
an explicit assumption because the slides do not label it. Both devices/windows were verified
through JupyterLab controls and PNG/HTML exports. Charlottesville's hour has GPS lock; Nashville's
hour does not and contains timing anomalies. Sensor estimates are not identical to PJM's
measurements. Validation evidence is retained in the frequency registry and dataset manifests.

## Python interface

```python
from pathlib import Path
from kestrelgrid_tools.workflow import Selection, fetch_dataset, load_dataset
from kestrelgrid_tools.plotting import build_figure, export_figure

selection = Selection(
    wmu_ids=("4012",),
    start_utc="2026-07-22T11:30:00Z",
    end_utc="2026-07-22T12:30:00Z",
)
dataset = fetch_dataset(Path.cwd(), selection, analysis_name="PJM July 22 load transfer")
figure = build_figure(dataset.frequencies, columns=["frequency_hz"])
export_figure(figure, dataset.directory / "plots", name="frequency")
dataset = load_dataset(dataset.directory)
detail = dataset.waveforms["4012"].to_dataframe(
    start="2026-07-22T11:56:08Z", end="2026-07-22T11:56:09Z"
)
```

`WaveformStore.to_dataframe()` limits materialization to one million samples. Use
`iter_tables()` for bounded full-resolution processing. `export_raw_csv()` exports complete,
ordered raw frames through the public package; for anomalous sources export cleaned normalized
batches instead.

## Development checks

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use synthetic frames/Parquet and injected HTTP sessions. Docstrings are required for
every authored module, class, function and method, including nested helpers. Notebook outputs
are cleared before version control. Technical comments describe format/algorithm constraints;
implementation history is not embedded in source.

## Agent guidance

[AGENTS.md](AGENTS.md) defines repository-wide development and data-handling rules.
Project-specific acquisition, frequency, notebook validation and storage workflows are in
[.opencode/skills/](.opencode/skills/), with sourced implementation references and offline
evaluation scenarios alongside them. OpenCode discovers these project skills automatically;
restart OpenCode after adding or changing skill definitions.
