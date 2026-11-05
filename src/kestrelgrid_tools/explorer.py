"""Jupyter widget interface. Network access occurs only on explicit actions."""

import html
import json
from pathlib import Path

import ipywidgets as widgets
import pandas as pd
import plotly.graph_objects as go
from IPython.display import display

from .client import KGridClient
from .common import utc, write_json
from .frequency import PhaseConvention
from .plotting import MEASUREMENTS, build_figure, export_figure
from .workflow import (
    Selection,
    demo_dataset,
    derive_dataset_frequency,
    fetch_dataset,
    load_dataset,
    preview,
)


class Explorer:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.locations = json.loads((self.root / "config/wmu_locations.json").read_text())
        self.dataset = None
        self.figures = {}
        self.client = None
        self.preview_selection = None
        self.wmus = widgets.SelectMultiple(
            options=[(f"{wmu} — {value['location']}", wmu) for wmu, value in self.locations.items()],
            value=("4021",), description="WMUs", rows=6,
        )
        self.extra = widgets.Text(description="Other IDs", placeholder="e.g. 4015,5001")
        self.mode = widgets.Dropdown(options=[("Latest closed archive", "latest"), ("UTC interval", "interval")])
        self.duration = widgets.FloatText(value=60, description="Seconds")
        self.start = widgets.Text(description="Start UTC", placeholder="2026-11-04T12:00:00Z")
        self.end = widgets.Text(description="End UTC", placeholder="2026-11-04T12:01:00Z")
        self.products = widgets.SelectMultiple(
            options=[("Raw waveform", "waveform"), ("RMS / theta / frequency", "measurements")],
            value=("waveform", "measurements"), description="Products",
        )
        self.budget = widgets.FloatText(value=100, description="Max MiB")
        self.credentials = widgets.Text(
            value="~/.config/kestrelgrid/credentials.json", description="Credentials",
            layout=widgets.Layout(width="650px"),
        )
        self.phase_enabled = widgets.Checkbox(value=False, description="Derive frequency using my phase convention")
        self.reference = widgets.FloatText(value=60, description="Reference Hz")
        self.units = widgets.Dropdown(options=["radians", "degrees"], description="Theta units")
        self.sign = widgets.Dropdown(options=[("+ derivative", 1), ("− derivative", -1)], description="Sign")
        self.plot_start = widgets.Text(description="Plot start", placeholder="Blank = all downloaded data")
        self.plot_end = widgets.Text(description="Plot end", placeholder="Blank = all downloaded data")
        self.columns = widgets.SelectMultiple(
            options=[(name[0], key) for key, name in MEASUREMENTS.items()],
            value=("voltage_v", "rms_v", "frequency_hz", "theta"), description="Traces", rows=4,
        )
        self.max_points = widgets.IntText(value=20000, description="Max points")
        self.export_name = widgets.Text(value="kgrid", description="File prefix")
        self.formats = widgets.SelectMultiple(options=["png", "html"], value=("png", "html"), description="Formats", rows=2)
        self.width = widgets.IntText(value=1600, description="PNG width")
        self.height = widgets.IntText(value=900, description="PNG height")
        self.scale = widgets.FloatText(value=2, description="PNG scale")
        self.cache = widgets.Dropdown(description="Local dataset", layout=widgets.Layout(width="650px"))
        self.status = widgets.HTML("Ready. Discover or load a local/synthetic dataset.")
        self.output = widgets.Output()
        self.plots = widgets.VBox()
        self.refresh_cache()
        self.discover_button = self.button("1. Discover / preview", self.discover, "info")
        self.download_button = self.button("2. Download selected", self.download, "success")
        self.render_button = self.button("3. Update plots (local)", self.render, "info")
        self.export_button = self.button("4. Export current view", self.export, "success")
        self.load_button = self.button("Load local dataset", self.load)
        self.demo_button = self.button("Offline synthetic demo", self.demo)
        self.refresh_button = self.button("Refresh local list", self.refresh_cache)
        self.mode.observe(self.update_mode, names="value")
        self.update_mode()
        self.widget = widgets.VBox([
            widgets.HTML("<h2>KGRID Explorer</h2><p>Explicit downloads · UTC plots · offline PNG/HTML exports</p>"),
            self.credentials,
            widgets.HBox([self.wmus, widgets.VBox([self.extra, self.mode, self.duration, self.start, self.end, self.budget])]),
            self.products,
            widgets.HBox([self.discover_button, self.download_button]),
            widgets.HTML("<h3>Local datasets</h3>"), self.cache,
            widgets.HBox([self.load_button, self.refresh_button, self.demo_button]),
            widgets.HTML("<h3>Local plot controls</h3><p>These controls never download data. Blank bounds show all loaded coverage.</p>"),
            widgets.HBox([self.plot_start, self.plot_end]),
            widgets.HBox([self.columns, widgets.VBox([self.max_points, self.phase_enabled, self.reference, self.units, self.sign])]),
            widgets.HTML("<p>Theta reference/sign are not supplied in the current files. Enable derivation only with an established convention; at 2 sps, phase unwrapping can alias deviations beyond ±1 Hz.</p>"),
            self.render_button, self.status, self.output, self.plots,
            widgets.HTML("<h3>Export current zoom and trace visibility</h3>"),
            widgets.HBox([self.export_name, self.formats]),
            widgets.HBox([self.width, self.height, self.scale]), self.export_button,
        ])

    def button(self, label, action, style=""):
        button = widgets.Button(description=label, button_style=style,
                                layout=widgets.Layout(width="220px"))

        def callback(_):
            button.disabled = True
            self.output.clear_output()
            try:
                with self.output:
                    action()
            except Exception as error:
                # Avoid repr(traceback/session), which can expose credential-bearing objects.
                self.status.value = f"<b>{type(error).__name__}:</b> {html.escape(str(error))}"
            finally:
                button.disabled = False

        button.on_click(callback)
        return button

    def update_mode(self, *_):
        self.start.disabled = self.end.disabled = self.mode.value != "interval"
        self.duration.disabled = self.mode.value == "interval"

    def selection(self):
        extra = [value.strip() for value in self.extra.value.split(",") if value.strip()]
        selected = Selection(
            wmu_ids=tuple(dict.fromkeys([*self.wmus.value, *extra])), mode=self.mode.value,
            duration_seconds=self.duration.value,
            start_utc=self.start.value or None, end_utc=self.end.value or None,
            products=tuple(self.products.value), max_download_mib=self.budget.value,
        )
        selected.validate()
        return selected

    def phase_convention(self):
        if not self.phase_enabled.value:
            return None
        return PhaseConvention(self.reference.value, self.units.value, self.sign.value)

    def progress(self, message):
        self.status.value = html.escape(message)
        print(message)

    def discover(self):
        selected = self.selection()
        self.client = KGridClient(self.credentials.value, selected.max_download_mib)
        rows = preview(self.client, selected)
        self.preview_selection = selected
        display(pd.DataFrame([{key: value for key, value in row.items() if key != "remote"} for row in rows]))
        self.progress("Preview ready. Sizes are listing estimates; raw downloads use bounded ranges.")

    def download(self):
        selected = self.selection()
        if self.client is None or selected != self.preview_selection:
            raise ValueError("Discover / preview this selection before downloading")
        self.dataset = fetch_dataset(
            self.root, selected, self.credentials.value, self.phase_convention(),
            client=self.client, progress=self.progress,
        )
        self.client = None  # A fresh download starts a fresh transfer budget.
        self.activate()
        self.refresh_cache()

    def refresh_cache(self):
        options = [(path.parent.name, str(path.parent)) for path in sorted(
            (self.root / "data/processed").glob("*/manifest.json")
        )]
        self.cache.options = options

    def activate(self):
        self.plot_start.value = self.plot_end.value = ""
        display(self.dataset.summary)
        print("Dataset:", self.dataset.directory)
        for warning in self.dataset.manifest.get("warnings", []):
            print(warning)
        self.render()

    def load(self):
        if not self.cache.value:
            raise ValueError("No local dataset; download data or use the synthetic demo")
        self.dataset = load_dataset(Path(self.cache.value))
        self.activate()

    def demo(self):
        self.dataset = demo_dataset(self.root)
        self.activate()
        self.refresh_cache()

    def render(self):
        if self.dataset is None:
            raise ValueError("Load or download a dataset first")
        convention = self.phase_convention()
        if convention:
            derive_dataset_frequency(self.dataset, convention)
        else:
            # Return to persisted measurements if a previous interactive derivation was enabled.
            self.dataset = load_dataset(self.dataset.directory)
        start = utc(self.plot_start.value) if self.plot_start.value else None
        end = utc(self.plot_end.value) if self.plot_end.value else None
        if start is not None and end is not None and end <= start:
            raise ValueError("Plot end must be after plot start")
        figures, widgets_to_show = {}, []
        for product, source in (("waveform", self.dataset.waveforms), ("measurements", self.dataset.measurements)):
            tables = {}
            for wmu, original in source.items():
                table = original
                if start is not None:
                    table = table[table.time_utc >= start]
                if end is not None:
                    table = table[table.time_utc < end]
                if not table.empty:
                    tables[wmu] = table
            if not tables or not any(column in table for table in tables.values() for column in self.columns.value):
                continue
            label = "SYNTHETIC DEMO" if self.dataset.manifest.get("synthetic") else "KGRID"
            frequency_note = (
                " · theta-derived frequency (user convention)" if convention
                else " · stored frequency only"
            ) if product == "measurements" else ""
            figure = go.FigureWidget(build_figure(
                tables, self.locations, list(self.columns.value), self.max_points.value,
                f"{label} {product}{frequency_note}",
            ))
            figures[product] = figure
            widgets_to_show.append(figure)
        if not figures:
            raise ValueError("No selected traces in the local plot interval")
        self.figures = figures
        self.plots.children = tuple(widgets_to_show)
        unavailable = [
            MEASUREMENTS[column][0]
            for column in self.columns.value
            if not any(
                column in table
                for source in (self.dataset.waveforms, self.dataset.measurements)
                for table in source.values()
            )
        ]
        self.status.value = "Plots ready. Zoom, pan, or toggle traces; export preserves the current view."
        if unavailable:
            self.status.value += "<br>Unavailable in loaded data: " + html.escape(", ".join(unavailable))

    def export(self):
        if not self.figures:
            raise ValueError("Render plots first")
        if self.width.value <= 0 or self.height.value <= 0 or self.scale.value <= 0:
            raise ValueError("PNG dimensions and scale must be positive")
        directory = self.dataset.directory / "plots"
        for product, figure in self.figures.items():
            outputs = export_figure(
                figure, directory, f"{self.export_name.value}_{product}", tuple(self.formats.value),
                self.width.value, self.height.value, self.scale.value,
            )
            print("\n".join(str(path) for path in outputs))
        write_json(directory / "plot_config.resolved.json", {
            "dataset": str(self.dataset.directory), "source_manifest": self.dataset.manifest,
            "plot_start_utc": self.plot_start.value or None,
            "plot_end_utc": self.plot_end.value or None,
            "columns": list(self.columns.value), "max_plot_points": self.max_points.value,
            "phase_convention": self.phase_convention().to_dict() if self.phase_convention() else None,
        })
        self.status.value = f"Exported to {html.escape(str(directory))}"
