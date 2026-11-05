# Cached event validation scenarios

Reviewed **2026-11-05**. This reference identifies repeatable regression inputs and empirical
evidence; it is not a full engineering appraisal of the event or a guarantee caches exist on
another checkout. Inspect manifests for actual coverage and source hashes.

## July 22 Dominion/PJM event

Source (read 2026-11-05):
https://www.pjm.com/-/media/DotCom/committees-groups/committees/oc/2026/20260806/20260806-item-05---dominion-july-loss-of-load-event---presentation.pdf

Pages 2–3 describe approximately 3,800 MW transferring to backup power after a 230 kV fault,
with frequency reaching 60.092 Hz. Page 27 gives initial line opening at 07:56:09, initial load
loss at 07:56:33 and secondary loss at 07:57:40.

**Assumption:** interpret the slide clock as `America/New_York` (EDT, UTC−04:00); the slides
do not explicitly label their timezone. Event marker: approximately 11:56:09 UTC.

Authoritative preset inputs: [config/plots/pjm_july22.json](../../config/plots/pjm_july22.json).

| Scenario | UTC bounds | Products |
|---|---|---|
| Event hour | 2026-07-22 11:30 to 12:30 | Waveform + Parquet |
| Eastern calendar day | 2026-07-22 04:00 to 2026-07-23 04:00 | Parquet only |

The Eastern day intersects two Pacific archive partitions; it is not a UTC calendar day.
Use WMU 4012 (Charlottesville VA, closest supplied location to Dominion/PJM) and WMU 4011
(Nashville TN, Eastern Interconnection comparison outside PJM). Do not claim Nashville is a
PJM sensor or a colocated transmission measurement.

## Empirical results from implementation

| Device/window | Waveform samples | Parquet records | Frequency records |
|---|---:|---:|---:|
| 4012 hour | 14,400,021 | 7,200 | 7,197 |
| 4011 hour | 14,288,098 | 7,177 | 7,131 |
| 4012 day | — | 172,736 | 172,736 (172,732 finite) |
| 4011 day | — | 171,985 | 171,985 (171,899 finite) |

Both devices' hour theta conventions were verified as radians/60 Hz/positive derivative:
4012 correlation about 0.999832, RMSE 0.000337 Hz; 4011 correlation about 0.998664,
RMSE 0.000982 Hz. Evidence resides in the versioned frequency registry.

Charlottesville's hour had 100% GPS lock. Nashville's hour had 0% GPS lock, seven timing
discontinuities and 64,417 rejected overlap/backward samples. Day sources include gaps;
Charlottesville has about 32 missing seconds near the archive boundary. Preserve quality.

Within 11:55–12:05 UTC, waveform estimates peaked near 60.09714 Hz at 11:56:15.5 for 4012
and 60.08808 Hz at 11:56:16.5 for 4011. Day theta estimates peaked near 60.09570 and 60.08813 Hz.
These are different measurements from PJM's reported 60.092 Hz. Estimator window, sensor
location and Nashville timing prevent claims of exact agreement. Outlet RMS is not the
presentation's transmission-voltage profile.

## Find and reuse data

Use [dataset_manifests()](../../src/kestrelgrid_tools/storage.py) and manifests to locate
requested devices/windows. Named datasets use:

`data/analyses/pjm-july-22-load-transfer/<device>/<UTC-start>_to_<UTC-end>__<mode>/`

Do not depend on obsolete hash folder names or assume data is tracked in Git. Versions may
exist; inspect identities and coverage. `fetch_dataset(..., analysis_name="PJM July 22 load transfer")`
reuses exact matches before authentication; `load_dataset()` is explicitly local. Routine
UI/storage checks should use these caches or a synthetic demo, not repeated hour downloads.

## Interactive regression procedure

1. Launch the project kernel and run all notebook cells from a fresh session.
2. Select 4012 and hour preset; activate Download and plot. Confirm waveform, RMS/frequency
   measurements and frequency figures, PNG/HTML output links and coverage summaries.
3. Select day preset. Confirm no raw/7z acquisition and no waveform figure; measurements have
   RMS above frequency with native timestamps and source-labelled frequency.
4. Repeat with 4011 and confirm anomalies remain reported rather than hidden or fatal.
5. Click/fetch export links in the same-origin browser context and verify HTTP 200. Open HTML
   and check zoom/trace visibility; inspect PNG labels. FigureWidgets can use shadow DOM.
6. Stop only test-owned servers/kernels. Keep source cells/user edits; clear generated notebook
   outputs before committing.

Synthetic/offline regression tests cover these semantics; live browser validation was performed
during implementation. Re-run affected checks when behavior changes, and report the checks actually
performed rather than reusing historical test counts as proof of a new change.
