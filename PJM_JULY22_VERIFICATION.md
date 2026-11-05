# July 22, 2026 PJM event: notebook verification

## Test basis and device selection

Source: [PJM/Dominion August 6 presentation](https://www.pjm.com/-/media/DotCom/committees-groups/committees/oc/2026/20260806/20260806-item-05---dominion-july-loss-of-load-event---presentation.pdf).
Pages 2–3 describe approximately 3,800 MW transferring to backup power after a 230 kV fault,
with frequency reaching 60.092 Hz. Dominion's timeline on page 27 gives 07:56:09 for initial
line opening, 07:56:33 for initial load loss and 07:57:40 for secondary load loss.

The presentation does not explicitly label the time zone. These tests interpret its clock
times as **America/New_York (EDT, UTC−04:00)**. Event marker: approximately
**2026-07-22 11:56:09 UTC**.

- **WMU 4012, Charlottesville VA:** closest device to Dominion/PJM in the supplied legend;
  primary event sensor, recorded GPS lock 100% in the tested hour.
- **WMU 4011, Nashville TN:** Eastern Interconnection comparison, outside PJM; recorded GPS
  lock 0% in the tested hour. The comparison is regional, not a colocated reference.

## Requested windows

| Test | Eastern time | UTC bounds | Download rule |
|---|---|---|---|
| One hour | July 22 07:30–08:30 EDT | July 22 11:30–12:30 UTC | Waveform + Parquet |
| One day | July 22 00:00–July 23 00:00 EDT | July 22 04:00–July 23 04:00 UTC | Parquet only |

The day is the Eastern calendar day, not a UTC calendar day. It intersects the server's
July 21 and July 22 Pacific-time archive partitions. Raw byte ranges cover only the hour;
neither day test downloaded raw or compressed waveform data.

## Failures reproduced and fixes

1. **Broken notebook export links.** IPython FileLink checked repository-relative paths from
   the kernel's `notebooks/` working directory, displaying "doesn't exist" despite successful
   exports. Replaced it with explicit escaped `/files/data/...` links. Browser clicks and
   same-origin fetches returned HTTP 200 for HTML/PNG.
2. **Nashville repeated/backward raw timestamps.** Strict ordering stopped the entire hour.
   Structural validation remains strict for dimensions/payloads/sensor identity, but the
   normalization path now reports time discontinuities, excludes repeated/backward samples
   using a stream-order temporal high-water mark, and preserves recording segments/gaps.
3. **Upstream conversion skipped backward frames.** `raw_to_tv()` updates a time bound while
   reading. For anomalous chunks, use public `read_raw()` output and explicit frame timing;
   clean chunks still use both package methods. Statistics/plots use cleaned normalized data.
4. **Kernel/session ambiguity.** Added `uv run kgrid-notebook`, which registers a named project
   kernel, fixes the file-server root and opens the notebook. Existing sessions need Restart
   Kernel and Run All Cells to reload code and live widget callbacks.

## Browser-verified outcomes

All four tests used the **actual JupyterLab widget controls**, not just direct Python calls.
Device and preset selections propagated to Python; Download and plot completed; plot widgets
rendered; exports were linked. Hour tests produced waveform, RMS/theta and frequency figures.
Day tests produced RMS/theta and frequency figures, with no waveform figure/source.

| Device/window | Normalized waveform samples | Parquet records | Frequency records | Local dataset |
|---|---:|---:|---:|---|
| 4012 hour | 14,400,021 | 7,200 | 7,197 | `2cd82d4f16277b80` |
| 4011 hour | 14,288,098 | 7,177 | 7,131 | `94b88a4581e7d96a` |
| 4012 day | — | 172,736 | 172,736 (172,732 finite) | `a637feaa8b5dc1a3` |
| 4011 day | — | 171,985 | 171,985 (171,899 finite) | `5b2f75b52dd075ec` |

Outputs are under `data/processed/<dataset>/plots/`, with `wmu<id>_<start>_<end>_frequency.png`
and `.html` plus measurement/waveform outputs as applicable. HTML embeds Plotly.js; tested
zoom/trace-visibility updates work with no external script dependency. Larger day traces use
labelled extrema-preserving display reduction; all native-cadence data stays in local Parquet.

Combined two-device hour/day frequency comparisons (PNG + HTML), with an approximate event
marker at 11:56:09 UTC, are in `data/pjm_july22_comparison/`.

Nashville's hour has **seven timing discontinuities** and **64,417 rejected overlapping/backward
samples**. Its day has multiple Parquet gaps. Charlottesville's day includes an approximately
32-second missing interval at the archive boundary. These sources are not complete ideal
records; manifests retain coverage, gaps and quality information.

## Frequency validation and event observations

Both devices' theta conventions were independently verified against their hour waveforms:

| Device | Convention | Correlation | RMSE |
|---|---|---:|---:|
| 4012 | radians, 60 Hz reference, positive derivative | 0.999832 | 0.000337 Hz |
| 4011 | radians, 60 Hz reference, positive derivative | 0.998664 | 0.000982 Hz |

Verified entries are in `config/frequency_conventions.json`; long-window derivatives reuse
these conventions without additional raw downloads. Stored frequency still takes precedence.

Within 11:55–12:05 UTC:

- **4012 waveform estimate:** peak **60.09714 Hz at 11:56:15.5 UTC**.
- **4011 waveform estimate:** peak **60.08808 Hz at 11:56:16.5 UTC**.
- **4012 day theta derivative:** peak **60.09570 Hz at 11:56:15.308 UTC**.
- **4011 day theta derivative:** peak **60.08813 Hz at 11:56:16.565 UTC**.

PJM reports 60.092 Hz. The sensor results show an event-time frequency rise of comparable
scale, but do not exactly reproduce PJM's reported measurement. Different locations,
estimation windows and Nashville's unlocked timing limit direct comparison. Outlet RMS is
not a direct measurement of the transmission-voltage traces in the presentation.

## Reopen and repeat

From the repository root:

```bash
uv sync
uv run kgrid-notebook
```

Select **KGRID Analysis (project Python)**, then **Kernel → Restart Kernel and Run All Cells**.
Choose device 4012 or 4011 and the July 22 hour/day preset, then Download and plot. The
validated bundles can be reused; **Load local dataset** avoids all network access.

Regression checks cover notebook links from a `notebooks/` working directory, repeated/backward
frames, UTC/EDT presets, routing and existing estimator tests. Generated datasets and server
logs remain Git-ignored. Notebook cell code/documentation are retained; obsolete saved widget
outputs are cleared after verification.

Final checks: **37 tests passed**, Ruff passed, Git whitespace checks passed. Headless execution
also passed using the named project kernel with the working directory set to `notebooks/`.
