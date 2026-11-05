---
name: kgrid-analysis-storage
description: Use when naming KGRID analyses, changing download/output directories, reusing or migrating cached datasets, fixing local dataset labels or resolving storage collisions. Guides readable UTC/device paths, label-independent selection identity, verified offline migration and nested-root-safe provenance.
compatibility: OpenCode; repository filesystem and uv project commands
---

# KGRID analysis storage

## Inspect existing data and callers

Read [storage.py](../../../src/kestrelgrid_tools/storage.py),
[workflow.py](../../../src/kestrelgrid_tools/workflow.py) and
[frequency_workflow.py](../../../src/kestrelgrid_tools/frequency_workflow.py).
Confirm manifests and user changes before moving anything. Generated files are ignored but
may be valuable user work; source cleanup does not authorize deleting downloads.

## Path and identity rules

- Analysis names are optional display labels. `slug()` normalizes accepted names and rejects
  traversal/separators; blank labels use `unnamed-analysis`.
- `analysis_directory()` uses analysis slug, device/location, exact UTC start/end (including
  fractions) and waveform/Parquet mode. Use `device_directory()` for shared download caches.
- `identity()` hashes WMUs, bounds and phase settings. Labels and budgets do not define data.
- `reuse_dataset()` runs before network/credentials. Renaming an analysis copies matching local
  data into an independent bundle; never download again just to change a label.
- `available_directory()` selects by identity or adds `__vN` for conflicts/incomplete folders.
  Hash validation follows before actual reuse. A matching but corrupted bundle raises an error;
  do not overwrite it or silently download replacements. Identity does not include algorithm
  versions: inspect manifest processing compatibility before a change that alters interpretation.
- `dataset_label()` presents name, location/device, UTC interval, product mode and version.
- Resolve the repository with `project_root()`, not a fixed number of parent directories.
- Manifests use relative normalized paths, verify SHA-256 and record coverage/provenance.
  Keep absolute export metadata consistent after moves/copies.

## Migration workflow

1. Preview with `uv run kgrid-migrate`; optionally supply `--analysis-name`.
2. Review proposed sources/destinations and conflicts. Validate all normalized hashes before
   applying any move. A missing/corrupt bundle must not trigger a partial destructive cleanup.
3. Apply explicitly with `uv run kgrid-migrate --apply`. Preserve files, independent versions
   and plot configuration; rename numeric download caches without merging ambiguous collisions.
4. Reload moved datasets without contacting the server and verify frequency-registry lookup,
   readable selector labels, figures and output links.
5. Keep legacy bundles loadable. Legacy latest-mode clipping is not equivalent to an exact
   explicit interval and must not be silently matched for reuse.

## Verification and outputs

Use `test_storage.py`, `test_documentation.py` and notebook-link regressions. Test unsafe names,
fractional UTC precision, blank labels, conflicting phase settings, offline label/budget reuse,
legacy migration, corrupted hashes and nested paths. No live data is needed.

Report the chosen readable destination, whether data was reused/copied/moved, any retained
versions and validation results. Update README and notebook guidance if naming behavior changes.
Keep credentials, server logs, downloads and rendered outputs outside version control.
