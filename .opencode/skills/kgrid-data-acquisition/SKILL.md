---
name: kgrid-data-acquisition
description: Use when downloading or decoding KGRID data, changing WMU/time-window routing, diagnosing raw/7z/Parquet files, or fixing authentication, byte ranges, partitions and measurement schemas in this repository. Guides bounded, cache-first acquisition and quality-aware use of the published kestrelgrid package.
compatibility: OpenCode; Python 3.12 project environment managed by uv
---

# KGRID data acquisition

## Inputs and entry point

Establish the WMU, timezone-aware start/end, optional analysis name and transfer budget.
Normalize caller timezones to UTC and use `[start, end)`. Use existing APIs:

- [workflow.py](../../../src/kestrelgrid_tools/workflow.py): `Selection`, `fetch_dataset`, `preview`, `load_dataset`.
- [client.py](../../../src/kestrelgrid_tools/client.py): `KGridClient` and verified range/stream operations.
- [discovery.py](../../../src/kestrelgrid_tools/discovery.py): observed filename/partition adapters.

Read [provider/data reference](../../references/kgrid-data-and-provider.md) for server facts and
[reader/timing reference](../../references/upstream-reader-and-timing.md) for raw edge cases.

## Workflow

1. Inspect current code and local manifests. Reuse exact matching data before credentials or
   discovery; a renamed analysis must not cause another server download.
2. Route by requested duration: at most 3,600 seconds includes waveform and Parquet;
   any longer interval is Parquet-only, without waveform probes or compressed files.
3. Only when valid local reuse cannot satisfy the request, read credentials through
   `load_credentials()`; never print secrets. Use verified HTTPS
   with system trust, same-host checks, timeouts, serial requests and bounded retries.
4. Inspect the actual listing/schema. Use observed Pacific daily partitions including DST;
   retain actual recording timestamps and report missing coverage. Do not invent REST endpoints.
5. Estimate transfer using observed frame rates. A 4 ksps hour has 14.4 million samples and
   about 27.5 MiB int16 payload; high-rate units need more. Respect the selected budget.
6. For raw ranges, require HTTP 206, exact Content-Range and stable ETag/size. Refuse a full
   response when a range is ignored. Save only validated complete frames and record byte offsets.
7. For 7z, download the full archive within budget, inspect paths/links/size and extract into
   a new directory. Do not range-slice compressed bytes. Explicitly select multi-member sources.
8. Decode bounded raw chunks with the pinned external `kestrelgrid` methods. Convert ksps to
   samples/s and scale once. Use structural validation independently of upstream corruption flags.
9. Handle timing anomalies only through the quality-aware normalization path: report them,
   exclude overlaps/backward samples in stream order and preserve gaps. Do not sort corrupt
   timing into a falsely continuous waveform.
10. Normalize recognized Parquet columns, preserve native cadence and source theta, and reject
    unknown schemas/naive timestamps instead of guessing. Save source hashes, coverage and QA.

## Outputs and verification

Return the local dataset directory, sources/products used, actual bounds, transferred bytes
and quality warnings. Identify failures precisely; do not substitute a different interval.

Run relevant tests in `test_client.py`, `test_raw.py`, `test_measurements.py`, `test_time_window.py`
and `test_notebook_regressions.py`. Check exact-hour versus hour-plus-one-second routing, source
changes, truncated data and gaps. Start with synthetic/cached data; a live transfer is justified
only when actual server behavior is unresolved and the bounded budget is acceptable.

Coordinate downloads beyond roughly a dozen waveform sources with the provider; never mirror
the archive. Load the frequency or storage skill only if that part of the task also needs work.
