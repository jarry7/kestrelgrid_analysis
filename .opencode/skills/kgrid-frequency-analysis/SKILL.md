---
name: kgrid-frequency-analysis
description: Use when KGRID frequency plots are missing, converting theta to system frequency, modifying waveform frequency estimation, validating a WMU phase convention, or investigating timing/quality differences. Preserves stored-frequency precedence and evidence-backed per-device interpretation without unnecessary raw downloads.
compatibility: OpenCode; NumPy and pandas in the uv project environment
---

# KGRID frequency analysis

## Inspect the selected data

Read [frequency.py](../../../src/kestrelgrid_tools/frequency.py),
[frequency_workflow.py](../../../src/kestrelgrid_tools/frequency_workflow.py) and
[config/frequency_conventions.json](../../../config/frequency_conventions.json).
For timing pitfalls consult [reader/timing reference](../../references/upstream-reader-and-timing.md);
for event evidence consult [event validation](../../references/event-validation.md).

Use full-resolution cached source data. `load_dataset()`/`ensure_frequency()` can add a
frequency product locally without a server request. Do not estimate from plotted envelopes.

## Source selection and estimation

1. Prefer stored frequency, even if the user provides a conflicting phase override.
2. Otherwise estimate from available waveform via `waveform_frequency()`: interpolated
   positive zero crossings and cycle-count regression, default one-second windows with
   half-second output centers. Preserve batch-boundary continuity but not recording gaps.
3. Exclude invalid timing/samples, missing/extra/inconsistent cycles and poor fits; unsupported
   window edges remain unavailable. This estimator is not a PMU standards-compliance claim.
4. Without usable waveform, derive from theta only with an established WMU convention:
   `f = reference_hz + sign * d(unwrapped_theta)/dt / (2*pi)` for radians; convert degrees first.
5. Differentiate within continuous segments using actual timestamps. Do not span duplicate
   times, gaps or resets. At 2 sps, deviations beyond ±1 Hz can alias undetectably.
6. Consult the versioned registry, then locally learned evidence. Verified conventions for
   4021, 4012 and 4011 are device/product-specific; do not generalize them to Lyon or unknown IDs.

An explicit manual override is a user-supplied assumption, not empirical verification. Label
it as an override and retain its provenance; it does not authorize extra raw acquisition.

## Validate an unknown convention

Use `verify_phase_convention()` with simultaneous waveform estimates and source theta.
Check current thresholds in code; they require enough overlap, variation, strong agreement
and a distinct winner among 50/60 Hz references, radians/degrees and derivative signs.
An uninformative constant signal cannot establish units/sign. Record successful evidence
locally through the existing workflow; version it only after reviewing its provenance.

For requests longer than one hour, preserve Parquet-only routing. If the convention is unknown,
explain what is missing or use an already cached overlapping waveform. Do not fetch raw
behind the user's long-window request. A separate bounded validation request must be explicit.

## Persist and present

- Keep source measurements/theta unchanged; frequency is a separate hash-checked Parquet product.
- Record source, reference/units/sign, quality/valid counts, validation interval and evidence.
- Label waveform estimates, stored values and verified/user-override theta derivatives accurately.
- Keep RMS above frequency in measurements figures; retain independent native timestamps.
- Compare event results with external publications as distinct measurements, considering location,
  estimator window and GPS lock. Do not assert exact time/frequency agreement for unlocked sensors.

## Verification

Run `test_frequency.py`, `test_measurements.py` and affected notebook/plot tests. Cover 50/60 Hz,
harmonics, batching, gaps, invalid cycles, unknown-device behavior, stored precedence and registry
invalidation. Use the cached July 22 hour/day workflow to check automatic plots without downloading.
