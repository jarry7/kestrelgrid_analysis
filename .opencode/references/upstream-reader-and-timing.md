# Raw reader and timing findings

Reviewed **2026-11-05** against the published `kestrelgrid==0.1.0` package and local adapters.
Source: https://pypi.org/project/kestrelgrid/ (package source inspected during implementation).
Recheck these findings when the dependency version changes; do not copy a replacement decoder.

## Actual package interface

The package exports `read_raw`, `raw_to_tv`, `raw_to_csv` and `truncate_raw` through
`kestrelgrid.raw_reader`. `read_raw()` returns `(frames, corrupted)`, where frames is a list
of dictionaries or None. Actual keys are `data`, `pmu_id`, `sample_rate`, `start_time`,
`scale_factor`, `boot_count`, `gps_locked`; the package README describes additional header
fields that are not all returned in these frame dictionaries.

- `sample_rate` is **ksps**; multiply by 1,000 for samples/s.
- Frame timestamps are epoch seconds. Per-sample times are frame start + index/rate.
- `scale_data=True` is the default; returned data is scaled once. Do not multiply again.
- RawHeader's `n_samples` and frame byte length are structural checks; the payload is int16.
- The local adapter validates headers/payloads with upstream RawHeader, then delegates waveform
  decoding to the published package.

## Observed version-0.1.0 edge cases

1. A single start or end bound in `read_raw()` is not applied; filtering operates on frame starts.
2. `raw_to_tv()` sample-count/preallocation behavior can leave trailing zeros at exact end bounds.
3. Partial final headers or payloads can evade the package's corruption boolean.
4. `raw_to_tv()` changes a lower time bound while reading; repeated/backward frames can be skipped
   and produce inconsistent timestamps/counts.

The project's workaround is validated complete frame-aligned local chunks **without upstream
time bounds**, followed by explicit sample clipping. Independent structural QA is required.
When backward frame times are allowed for normalization, use unbounded `read_raw()` frames and
their explicit timing instead of the skipping `raw_to_tv()` path. Keep count/timestamp checks.
See [interpretation.py](../../src/kestrelgrid_tools/interpretation.py).

## Structural validity versus temporal quality

Incomplete payloads, invalid dimensions/magic and mixed sensor IDs are hard failures. Repeated
or backward timestamps are a separate quality issue: preserve source raw bytes, report the anomaly,
and discard overlap/backward samples in stream order using the temporal high-water mark. Do not
sort erroneous timestamps into a seemingly continuous signal or double-count overlapping records.

[normalize_waveform()](../../src/kestrelgrid_tools/waveform_store.py) preserves segment breaks
and full-resolution summary statistics while writing bounded Parquet row groups. Chunk boundaries
must not become fake recording gaps. Physical gaps, timestamp resets and invalid samples must not
be bridged in frequency estimates or plots.

Nashville's July 22 hour empirically contained seven raw timing discontinuities and 64,417 rejected
overlap/backward samples. Charlottesville's corresponding hour was GPS-locked and ordered. These
are interval-specific findings, not permanent characterizations of all recordings from a device.

## Frequency safeguards

Stored frequency takes precedence. Otherwise use a waveform estimate where available, or an
established per-WMU theta convention. For radians relative to a rotating reference:

`frequency = reference_hz + sign * d(unwrapped_theta)/dt / (2*pi)`

Use actual timestamps, segment-aware unwrapping and explicit units/reference/sign. At 2 sps,
deviations beyond ±1 Hz can alias undetectably. Do not infer the Lyon reference from the provider's
general “60 Hz theta” wording. Match waveform/theta using actual overlap; require variation and a
distinct candidate winner. Current thresholds live in
[verify_phase_convention()](../../src/kestrelgrid_tools/frequency.py), not in this reference.

Versioned evidence for devices 4021, 4012 and 4011 is in
[config/frequency_conventions.json](../../config/frequency_conventions.json). Learned evidence
is ignored local data. Scope empirical verification to the device/product/window and revalidate
if provider definitions change. The waveform estimator is not a PMU compliance claim.

## Plotting clocks

The one-second-window waveform estimator produces half-second-center outputs that do not
necessarily coincide with the RMS timestamps. Use per-panel sources in
[build_measurements_figure()](../../src/kestrelgrid_tools/plotting.py) to display RMS above frequency.
Do not silently drop data with an exact inner join or interpolate one measurement onto another's
clock merely to align panels. Shared time axes are sufficient. Use original data for calculations,
and disclose extrema-preserving reduction on plots/standalone HTML.
