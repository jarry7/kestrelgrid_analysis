# KGRID provider and data reference

Verified/reviewed: **2026-11-05**. Use this as sourced project context, not a server availability
guarantee. Reinspect actual listings, schemas and metadata when they change.

## Evidence categories

| Category | Meaning |
|---|---|
| User requirement | Desired application behavior, not a property of the upstream server |
| Provider-described | Account background supplied by the user or a KGRID website statement |
| Observed | Direct inspection of server/package behavior during implementation |
| Empirically verified | A measured comparison scoped to particular devices/data intervals |
| Planned | An announced change not confirmed deployed in inspected data |

## Measurement network

**Provider-described:** KGRID is a low-cost point-on-wave voltage monitoring network. Sensors
measure low-voltage 120/240 VAC outlets and transmit through Wi-Fi; GPS is optional. The About
page describes raw archives at 4–64 ksps and phasor measurements at 2 samples/s for web display.
The homepage describes typical 4 ksps raw data and simplified 2 sps phasors.

Sources (retrieved 2026-11-05):

- https://kestrelgrid.com/
- https://kestrelgrid.com/about
- https://kestrelgrid.com/news

The About page's archive-size/unit-count figures and News page's 2025 target dates are dated
descriptions, not live inventory or confirmation that an upgrade occurred. Raw outlet voltage
is not a direct transmission-voltage measurement. GPS-equipped does not mean every frame locks.

## Provider account background

**Provider-described, supplied in this conversation; reviewed 2026-11-05:**

- Access portal: https://data.kestrelgrid.com/ (credentials are local, never copied here).
- Per-WMU directories contain `.raw` continuous voltage data, most units at 4,000 samples/s.
- `.7z` contains compressed raw files; decompress before using the raw reader.
- `.parquet` contains computed RMS and 60 Hz phase-angle (`theta`) measurements at 2 sps;
  use pandas/PyArrow and a confirmed angle convention to derive frequency.
- The provider asks for coordination for downloads larger than roughly a dozen raw files
  because the server is bandwidth constrained. The project's serial/file-budget limits preserve
  that intent, rather than treating twelve large files as a default target.
- The supplied WMU legend is recorded in [the registry](../../config/wmu_locations.json).
  Extra IDs in server listings have unknown locations until confirmed; do not fabricate them.

**Planned:** the provider announced hourly rather than daily Parquet at 10 sps with RMS and
direct frequency, plus a REST API. These were not confirmed deployed in the inspected WMU
archives. Neither guessed endpoints nor blind assumptions about all new schemas are justified.

## Observed server behavior

Observed during implementation on 2026-11-05:

- Root and device listings are nginx HTML indexes behind HTTP Basic authentication.
- Raw archives advertise size, Last-Modified and ETag and support HTTP byte ranges.
  Verify each response's 206 status and exact Content-Range; ignored ranges must not become
  full-file downloads.
- Inspected Parquet schema: `t` float64 epoch seconds, `theta` float64, `rms` float64, `gps` bool.
  The cadence is approximately 0.5 seconds, measured from timestamps.
- Daily filename partitions follow Pacific time (`America/Los_Angeles`), not UTC calendar days.
  A November 4 archive extends to about November 5 08:00 UTC; July archives use UTC−07:00.
  Daily partition widths change with DST. Validate actual timestamp coverage after retrieval.
- The institutional network's certificate chain needed system trust via `truststore`; TLS
  verification remains enabled. This is a local environment finding, not evidence of an
  intrinsically invalid KGRID certificate.

## User-defined application requirements

The original goal was programmatic authenticated access, an interpretable recent sample using
the PyPI package, and implementation instructions. Subsequent decisions establish current behavior:

- Notebook-first interaction with one WMU and an explicit UTC window.
- At most one hour: waveform plus available Parquet; longer: only Parquet, no raw probes.
- Static PNG and offline interactive HTML; measurements have RMS above frequency.
- Optional analysis name and readable device/window directories; cache-first reuse/migration.
- Complete docstrings, technical-only comments and removal of obsolete code/doc references.

Consult [AGENTS.md](../../AGENTS.md) for binding project rules and
[README.md](../../README.md) for user commands. Keep secrets and personal account identifiers out
of shareable knowledge. No live authentication is necessary to read these references.
