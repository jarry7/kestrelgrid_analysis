---
name: kgrid-notebook-validation
description: Use when the KGRID Jupyter notebook fails, widgets do not respond, plots or PNG/HTML links are incorrect, or the RMS/frequency measurements layout changes. Guides project-kernel setup, actual browser interaction, native-clock plotting and cached one-hour/day regression checks.
compatibility: OpenCode; JupyterLab, ipywidgets, Plotly/anywidget; browser tools for UI failures
---

# KGRID notebook and export validation

## Locate the failure

Read [explorer.py](../../../src/kestrelgrid_tools/explorer.py),
[plotting.py](../../../src/kestrelgrid_tools/plotting.py) and
[launch.py](../../../src/kestrelgrid_tools/launch.py).
Use [event validation](../../references/event-validation.md) for cached regression windows.

1. Start from the repository with `uv sync` and `uv run kgrid-notebook`.
2. Select **KGRID Analysis (project Python)**. Restart the kernel and run all cells after code
   updates; old saved widget output has no live callbacks.
3. Check the actual exception, kernel interpreter and extension availability. Do not report
   success solely because a notebook parses or a figure can be built outside Jupyter.
4. Preserve user notebook edits. Work with an in-memory execution copy when possible; clear
   only generated outputs/execution counts before version control.

## Required behavior

- Entry controls: optional analysis name, one WMU, explicit UTC window and a destination preview.
- **Download and plot** acquires/reuses data, displays figures and saves PNG plus HTML.
- Local plots/loads do not download. Long windows remain Parquet-only.
- Use `build_measurements_figure()` for **RMS voltage on top, frequency below**. Native RMS
  and frequency clocks can differ: keep per-panel sources; do not inner-join or interpolate.
- Keep independent frequency exports and source labels. Theta remains source data rather
  than the lower measurements panel.
- Preserve shared axes, gaps, trace colours, current zoom and visibility in exports.
- Self-contained HTML embeds Plotly.js. It can only zoom into displayed embedded points;
  disclose display reductions and use short local windows for full-resolution cycle views.
- PNG uses Kaleido with Chrome/Chromium. Diagnose a missing exporter browser separately
  from data or widget failures.

## Browser workflow

For a reported interactive failure, use actual JupyterLab controls when browser tools are
available: run cells, select the device/window, click the action, wait for completion, inspect
rendered plots and activate output links. Some FigureWidgets render inside shadow roots;
absence of a top-level Plotly selector alone is not proof of missing plots.

Test export links from the kernel's `notebooks/` working directory. They must resolve through
the repository-root `/files/` path; do not use a relative existence check against the kernel cwd.
Use same-origin browser fetches or clicked links: direct cross-origin test requests may be
rejected by Jupyter even when the actual links work. Keep Jupyter's protections enabled.
Verify HTML zoom/legend behavior and PNG panel labels, not just file existence.

Prefer cached Charlottesville/Nashville July 22 hour and Eastern-day presets. An hour must
show waveform plus RMS/frequency; a day must not show/download waveform. Stop temporary test
servers and kernels you started; leave user-owned sessions untouched.

## Checks and report

Run relevant notebook/plot regressions plus the standard checks in AGENTS.md. Headless execution
should use the named kernel with a `notebooks/` working directory; browser tests complement it.
Report reproduced failure, fix, devices/windows, export paths and which checks actually ran.
If browser tools are unavailable, state that interactive validation remains unverified.
