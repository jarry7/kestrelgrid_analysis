# KGRID notebook implementation instructions and decisions

## Current entry point and duration rule

### July 22 PJM browser regression verification

See `PJM_JULY22_VERIFICATION.md` for full windows, source timing assumptions, datasets and
outcomes. Actual JupyterLab widget tests passed for devices 4012 (Charlottesville) and 4011
(Nashville), each for a one-hour event window and the full Eastern day. Added these presets.
Fixed export links relative to the notebook working directory, tolerant-but-reported raw
timing discontinuities, and the upstream converter's skipping of backward frames.
Nashville's hour excludes 64,417 overlapping samples and reports seven timing discontinuities.

Added `uv run kgrid-notebook` with a named project kernel. Saved obsolete widgets must be
rerun, so clear outputs after verification and instruct Restart Kernel and Run All Cells.
Both Eastern devices now have independently verified theta conventions. Day requests remain
Parquet-only. Clicked HTML/PNG links returned HTTP 200; standalone HTML zoom and visibility
worked. Final regression suite: 37 passed, Ruff/whitespace checks passed.

### Automatic frequency outcome

Frequency gets its own figure/PNG/HTML by default: stored Parquet frequency first, otherwise
one-second interpolated-positive-zero-crossing waveform regression at 2 estimates/s, otherwise
segment-aware theta derivatives with a verified per-WMU convention. Read bounded full-resolution
batches, reject invalid timing/cycles/fits, and leave unsupported edges unavailable.

WMU 4021's cached hour establishes radians/60 Hz/+derivative: 0.9993 correlation and 0.00052 Hz
RMSE at theta timestamps; the automatic half-second-grid estimator revalidates it at 0.9984 and
0.00080 Hz. Evidence is in `config/frequency_conventions.json`. Other device candidates require
>=30 comparisons, sufficient waveform variation, RMSE <0.005 Hz, correlation >=0.8 and a distinct
winner. Persist learned evidence in ignored `data/frequency_conventions.json`.

Upgrade cached data locally without a download. Retain unchanged source measurements/theta;
store frequency separately with hashes, source, quality, units/reference/sign and validation
evidence in manifests. Stored frequency always takes precedence, including with user overrides.
Low-rate phase aliasing remains a limitation. This supersedes earlier opt-in-only theta handling.

The notebook starts with **one WMU device and explicit UTC start/end times**.
**Download and plot** performs discovery, transfer, decoding, interactive rendering, and
automatic PNG + standalone HTML export. Preview is optional; settings beyond device/window
are grouped under Advanced settings. Output filenames contain the device and requested window.

The requested duration determines products in `Selection`, before any transfer:

- `0 < duration <= 3600 seconds`: raw waveform interval + available Parquet measurements.
- `duration > 3600 seconds`: Parquet only, with no raw/7z payload or probe requests.
- Preserve Parquet's native cadence; this is product selection, not extra temporal averaging.

Defined intervals are never silently replaced. Select all intersecting daily/hourly partitions,
deduplicate measurements, report missing partitions and actual timestamp coverage. The rule
uses requested duration even if returned data covers only part of the request.

To support a full hour, stream raw payloads to disk, decode bounded frame-aligned chunks
(~250,000 samples) through public `kestrelgrid` methods, write normalized Parquet row groups,
and calculate full-resolution statistics incrementally. `WaveformStore` loads local short
windows or builds min/max previews in bounded passes; it does not materialize an hour in RAM.
The old five-million-sample whole-window cap is replaced by a 240-million-sample disk limit,
with the transfer budget enforced separately. A 4 ksps hour is 14.4 million samples (~27.5 MiB
raw payload); high-rate units may need larger transfer budgets. Preflight first/last frame
headers estimate payload plus probe allowance before waveform download.

Default Python selection is explicit interval. Latest remains a lower-level compatibility
mode, subject to the same duration routing rule. Configuration example:
`config/plots/time_window.json`.

## Repository and deliverable

- Repository: https://github.com/jarry7/kestrelgrid_analysis (public; default branch `main`).
- Existing checkout: `/Users/jwright2/Documents/work/github/kestrelgrid/kestrelgrid_analysis`.
- Primary interface: `notebooks/kgrid_explorer.ipynb` in JupyterLab.
- Reusable Python modules: `src/kestrelgrid_tools/`; do not shadow upstream `kestrelgrid`.
- Environment: Python 3.12, `uv`, locked dependencies in `uv.lock`.
- Commit source, cleared notebook, docs, configs and synthetic tests. Keep credentials and
  generated datasets/plots outside version control. Git operations require a separate request.

## 1. Credentials and discovery

1. Store the supplied account credentials in `~/.config/kestrelgrid/credentials.json`, mode
   `0600`, inside a `0700` directory. Only username/password fields are needed. Never copy the
   password into Markdown, notebooks, manifests or logs.
2. Authenticate with HTTP Basic over verified HTTPS at `https://data.kestrelgrid.com/`.
3. Use the operating system's trust store for institutional certificate chains.
4. Parse per-WMU nginx HTML listings. Restrict all authenticated URLs to the approved host,
   refuse unexpected redirects, use timeouts and bounded retries.
5. Cache listings in a client session, preview source filenames, listing sizes and partition
   dates before the user explicitly downloads. Unknown WMU locations remain unknown.

## 2. WMU registry

Provider legend implemented in `config/wmu_locations.json`:

| WMU | Location |
|---|---|
| 2003 | Pullman WA |
| 2004 | Vancouver WA |
| 2005 | Berkeley CA |
| 2007 | Boise ID |
| 1002 | Salt Lake City UT |
| 1010 | Phoenix AZ |
| 4011 | Nashville TN |
| 4013 | Lyon France |
| 4012 | Charlottesville VA |
| 4016 | Riverside CA |
| 4021 | Vancouver WA (GPS-equipped) |
| 4023 | Seattle WA |

GPS-equipped is a hardware note, not an assertion that all samples are GPS-locked.

## 3. Define a selection

Implement `Selection` with device IDs, explicit UTC interval, automatic products, transfer
budget and disk sample limit. Notebook default: 4021, an editable recent minute,
100 MiB cumulative transfer budget. Use bounded decoding and disk-backed waveform storage;
the five-million-row limit applies only to combined measurement dataframes.

UTC start/end must be timezone-aware. Normalized selection uses `[start, end)` while the
intermediate raw sample contains complete boundary frames. Latest resolves to actual recorded
timestamps and manifests include data age. Different WMUs may have different latest coverage;
each trace displays its actual interval rather than pretending they are synchronized.

Live inspection on 2026-11-05 found Pacific daily partitions, not UTC-day partitions:
the `2026-11-04.raw` source ends near 2026-11-05 08:00 UTC. Use DST-aware
`America/Los_Angeles` filename conversion. Filter current-day partitions out of latest mode,
then check immutable HTTP metadata across sample retrieval. Revisit the partition adapter if
the provider changes naming conventions. Hourly filename parsing is supported.

## 4. Download small samples on a constrained server

The provider requests coordination for campaigns larger than roughly a dozen raw files.
Download serially, refuse more than 12 waveform sources per selection, and enforce byte limits.
Do not mirror directories. A single daily 4 ksps int16 source is roughly 661 MiB.

For uncompressed `.raw`:

1. HEAD the source for exact size, ETag and modification metadata.
2. Probe a bounded suffix, align confirmed frame boundaries and reject partial final frames.
3. Locate interval bounds by bounded byte-range probes; include the overlapping start frame.
4. Require HTTP 206 and exact Content-Range; use If-Match where possible. Refuse an ignored
   range without accepting a full archive download.
5. Fetch only selected complete frames. Recheck source metadata, validate structure, write
   through a `.part` file, calculate SHA-256, and record source byte offsets.
6. Bound both transfer bytes and decoded sample count. Normalized frame metadata records gaps,
   overlaps, rates, boot counts and GPS lock.

For `.7z`: enforce complete-download budget; inspect member paths, links and extracted sizes
with 7-Zip; extract into a new directory; index complete raw frames and select a bounded sample.
The automatic workflow currently requires one raw member per archive. Multi-member archives
require explicit selection using the lower-level helpers. No arbitrary compressed-byte slicing.

For Parquet: cache complete small source files, compare remote metadata and local hash for
reuse, enforce row limits before reading, and store selected normalized measurements.

## 5. Use the actual kestrelgrid package

Pin `kestrelgrid==0.1.0` and call its public methods:

```python
from kestrelgrid import raw_reader
frames, corrupted = raw_reader.read_raw(path, scale_data=True)
timestamps, voltage = raw_reader.raw_to_tv(path)
raw_reader.raw_to_csv(path, csv_path, include_string_time=True)
```

The returned frames are dictionaries with `data`, `pmu_id`, `sample_rate`, `start_time`,
`scale_factor`, `boot_count`, `gps_locked`. `sample_rate` is ksps, scaled data is already volts.
The README's richer header-field description differs from the published frame dictionary.

Observed upstream 0.1.0 edge cases: a single bound is not applied; filtering is frame-start
based; raw_to_tv can leave zero padding at exact end boundaries; incomplete final headers or
payloads can escape the corruption flag. Use independently validated, frame-aligned local
samples without upstream time bounds, verify counts/timestamps, then clip normalized samples.
The small adapter uses upstream RawHeader; it does not replace upstream waveform decoding.

## 6. Parquet and frequency

Observed schema: `t` epoch seconds, `rms` volts, `theta`, `gps`, approximately 2 sps.
Also accept explicit common timestamp/RMS/frequency aliases for future products. Reject unknown
schemas or naive timestamps. Report original columns, units assumptions, cadence and duplicates.

The provider announces hourly 10-sps RMS/frequency files and a REST API; these are not assumed
deployed. A REST adapter can replace discovery/download once an actual API is documented.

Stored frequency takes precedence. Theta-derived frequency is disabled by default because
current files have no reference/sign/unit metadata. With an established user convention:

`f = reference_hz + sign × d(unwrapped_theta)/dt / (2π)`.

Convert degrees if needed, use actual timestamps, split at gaps/duplicates/invalid values,
mark segment edges and suspicious phase increments unavailable, preserve source theta.
At 2 sps, deviations beyond ±1 Hz from the reference can alias undetectably. Do not assume
the French sensor's phase reference. Label interactive derived plots and persist the chosen
convention in export metadata; do not overwrite stored frequency.

## 7. Notebook controls and exports

Use ipywidgets and Plotly FigureWidget/anywidget. Run all cells without any network request.
Provide device/window controls and one-action download/render/PNG+HTML export, optional preview,
local dataset reload, clearly labelled synthetic
demo, local plot interval/traces/point limit, explicit theta convention, export prefix/formats/
PNG dimensions. Handle actionable errors in a status panel without printing credential objects.

Use the same Plotly figure snapshot for PNG and standalone HTML, including current zoom and
trace visibility. Embed Plotly.js for offline HTML. PNG uses Kaleido with Chrome/Chromium.
Use WebGL for large traces, labelled chronological min/max envelopes, discontinuous gap lines,
consistent WMU colours, units and UTC labels. Never calculate statistics on reduced data.
HTML has a size limit; zoom only sees embedded points. Full-resolution cycle inspection uses
shorter notebook intervals. Shared axes link measurement panels within a figure.

Store manifests, normalized data and plots under ignored `data/processed/<selection-id>/`.
Persist SHA-256, URLs without secrets, remote metadata, byte ranges, actual coverage, selection
rule, source schemas, quality and versions. Persist current plot/export settings separately.
Notebook controls needing Python are not present in the standalone HTML export.

## 8. Verification

- Offline tests: credentials/permissions, host and redirect restriction, byte/range/changed-source
  handling, Pacific partitions, raw truncation/scaling/counts/CSV, theta gaps/conventions,
  current and updated Parquet schema, display extrema/gaps, local bundle hashes, offline HTML.
- Execute notebook headlessly without contacting the server, then exercise synthetic demo.
- Download a bounded live 4021 sample, inspect coverage/quality and export PNG/HTML.
- Verify PNG dimensions/content and offline HTML rendering/interaction.
- Ensure saved notebook outputs are empty and no credentials appear in repository files.
- Run `uv run pytest -q` and `uv run ruff check .`.

## References

- https://data.kestrelgrid.com/
- https://pypi.org/project/kestrelgrid/
- https://kestrelgrid.com/about
- https://plotly.com/python/static-image-export/
- https://plotly.com/python/interactive-html-export/

Account/WMU/file/bandwidth details were supplied by the user. The published package source
and authenticated server listings/small sample were inspected directly during implementation.

## Completed verification (2026-11-05)

### Updated time-window workflow

- Automatic frequency added and checked locally against previously downloaded data. The hour
  produces 7,197 valid waveform estimates; the two-hour Parquet-only window produces 14,379
  valid theta-derived estimates. Dedicated `_frequency.png` and `_frequency.html` exports
  are created. Unknown WMU conventions are not guessed, and stored frequency is never overridden.
- Estimator tests cover 50/60 Hz, harmonic distortion, batch boundaries, timing gaps, missing
  cycles, phase-convention evidence, registry cache invalidation and unchanged stored values.
- The notebook executes with the frequency figure and both exports from cached two-hour data.
- Final frequency checks: 34 tests passed; Ruff and Git whitespace checks passed. The
  two-hour frequency export retains all 14,381 rows (14,379 finite frequency values).

- Final checks: 24 tests passed; Ruff and Git whitespace checks passed. Dependency timedelta
  deprecation warnings remain non-failing. Saved notebook outputs are empty.
- Boundary tests cover 3599, 3600, 3600.001 and 86400 seconds; long-window tests assert no
  raw sampling or waveform estimate calls and preserve Parquet's native cadence.
- A synthetic complete hour (14.4 million samples) was decoded in >50 bounded chunks;
  min/max overview and full-resolution local cycle views passed.
- Updated notebook executed through nbclient, with offline widgets and PNG/HTML exports.
- Live one-action minute: 240,000 waveform samples; PNG/HTML automatically saved under
  `data/processed/5fa1d4534bc94f52/plots/`.
- Live two-hour request: 14,381 measurement records, Parquet only; no waveform sources.
  Outputs: `data/processed/b93ab996a393816a/plots/`.
- Live exactly-one-hour request: 14,400,019 samples (frame timing is not exactly nominal),
  7,181 Parquet records; 30.70 MiB transferred including probes. Disk-backed normalization
  and PNG/HTML hour overview succeeded: `data/processed/7e56e8abb645fa71/plots/`.

### Original sample workflow verification

- `uv run pytest -q`: 15 passed; one NumPy/pandas timedelta deprecation warning.
- `uv run ruff check .`: passed.
- Notebook validated and executed through nbclient; synthetic and cached live datasets rendered
  as FigureWidgets and exported successfully to both formats. Saved notebook outputs remain empty.
- Live WMU 4021 sample: 240,000 voltage samples at 4,000 samples/s, approximately
  2026-11-05 07:59:02.712 UTC to 08:00:02.712 UTC; all selected samples GPS-locked.
- Matching Parquet coverage: 96 RMS/theta records through 07:59:50.212 UTC, about 12 seconds
  shorter at the end than the waveform. Frequency intentionally not derived without convention.
- Final sample run transferred 3.46 MiB, reusing the earlier cached 3.4 MiB Parquet source.
  Local verified dataset: `data/processed/a55cbb0dc3ae26b3/`.
- PNG plots inspected, including a full-resolution 0.1-second cycle view. HTML opened in a
  browser through a temporary loopback server; zoom and trace visibility updated correctly,
  with no external scripts required.
- Generated datasets/plots are ignored by Git; credential file is outside the checkout with
  owner-only permissions. No credential strings appear in project source/docs/notebook files.
