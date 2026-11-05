"""Jupyter widget interface. Network access occurs only on explicit actions."""

import html
import json
from pathlib import Path
from urllib.parse import quote

import ipywidgets as widgets
import pandas as pd
import plotly.graph_objects as go
from IPython.display import HTML, display

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
        self.wmu = widgets.Dropdown(
            options=[(f"{wmu} — {value['location']}", wmu) for wmu, value in self.locations.items()],
            value="4021", description="WMU device", layout=widgets.Layout(width="450px"),
        )
        self.extra = widgets.Text(description="Other WMU", placeholder="Optional numeric device ID")
        # A concrete recent UTC window, never a silently resolved 'latest' request.
        default_end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1) + pd.Timedelta(hours=12)
        self.start = widgets.Text(description="Start UTC", value=(default_end - pd.Timedelta(minutes=1)).isoformat(),
                                  layout=widgets.Layout(width="450px"))
        self.end = widgets.Text(description="End UTC", value=default_end.isoformat(),
                                layout=widgets.Layout(width="450px"))
        self.route = widgets.HTML()
        self.preset = widgets.Dropdown(
            options=[("Custom window", "custom"), ("PJM July 22 — event hour (07:30–08:30 EDT)", "hour"),
                     ("PJM July 22 — full Eastern day (Parquet only)", "day")],
            value="custom", description="Time preset", layout=widgets.Layout(width="650px"),
        )
        self.budget = widgets.FloatText(value=100, description="Max MiB")
        self.credentials = widgets.Text(
            value="~/.config/kestrelgrid/credentials.json", description="Credentials",
            layout=widgets.Layout(width="650px"),
        )
        self.phase_enabled = widgets.Checkbox(value=False, description="Override automatic theta convention")
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
        self.status = widgets.HTML("Choose a device and UTC time window, then Download and plot.")
        self.output = widgets.Output()
        self.plots = widgets.VBox()
        self.refresh_cache()
        self.discover_button = self.button("Preview source files", self.discover, "info")
        self.download_button = self.button("Download and plot", self.download, "success")
        self.render_button = self.button("Update plots (local)", self.render, "info")
        self.export_button = self.button("Export current view", self.export, "success")
        self.load_button = self.button("Load local dataset", self.load)
        self.demo_button = self.button("Offline synthetic demo", self.demo)
        self.refresh_button = self.button("Refresh local list", self.refresh_cache)
        self.start.observe(self.update_route, names="value")
        self.end.observe(self.update_route, names="value")
        self.preset.observe(self.apply_preset, names="value")
        self.update_route()
        advanced = widgets.Accordion(children=[widgets.VBox([
            self.credentials, self.extra, self.budget, self.max_points,
            self.phase_enabled, widgets.HBox([self.reference, self.units, self.sign]),
            widgets.HTML("<p>Frequency is automatic: stored values, waveform estimates, or a verified WMU theta convention. Override only with a known convention.</p>"),
            self.export_name, widgets.HBox([self.width, self.height, self.scale]),
        ])], selected_index=None)
        advanced.set_title(0, "Advanced settings")
        self.widget = widgets.VBox([
            widgets.HTML("<h2>KGRID Explorer</h2><p>Choose one device and a UTC window. Download, plot, and save PNG + offline HTML in one action.</p>"),
            self.wmu, self.preset, widgets.HBox([self.start, self.end]), self.route,
            widgets.HBox([self.download_button, self.discover_button]), advanced,
            widgets.HTML("<h3>Local datasets</h3>"), self.cache,
            widgets.HBox([self.load_button, self.refresh_button, self.demo_button]),
            widgets.HTML("<h3>Local plot controls</h3><p>These controls never download data. Blank bounds show all loaded coverage.</p>"),
            widgets.HBox([self.plot_start, self.plot_end]),
            self.columns,
            self.render_button, self.status, self.output, self.plots,
            widgets.HTML("<h3>Export current zoom and trace visibility</h3>"),
            self.formats, self.export_button,
        ])

    def apply_preset(self, *_):
        if self.preset.value == "custom":
            return
        config = json.loads((self.root / "config/plots/pjm_july22.json").read_text())
        window = config["windows"][self.preset.value]
        self.start.value, self.end.value = window["start_utc"], window["end_utc"]
        self.status.value = "PJM July 22 window selected; choose 4012 (Charlottesville) or 4011 (Nashville), then Download and plot."

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

    def update_route(self, *_):
        try:
            selection = self.selection()
            message = (
                "Waveform + Parquet measurements" if selection.data_mode == "waveform_and_parquet"
                else "Parquet measurements only — no .raw or .7z downloads"
            )
            self.route.value = f"<b>{selection.requested_seconds / 60:.2f} minutes:</b> {message}. Outputs: PNG + HTML."
        except ValueError as error:
            self.route.value = html.escape(str(error))

    def selection(self):
        selected = Selection(
            wmu_ids=(self.extra.value.strip() or self.wmu.value,), mode="interval",
            start_utc=self.start.value or None, end_utc=self.end.value or None,
            max_download_mib=self.budget.value,
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
            self.client = KGridClient(self.credentials.value, selected.max_download_mib)
        self.figures = {}
        self.plots.children = ()
        self.dataset = fetch_dataset(
            self.root, selected, self.credentials.value, self.phase_convention(),
            client=self.client, progress=self.progress,
        )
        self.client = None  # A fresh download starts a fresh transfer budget.
        self.activate()
        self.refresh_cache()
        self.formats.value = ("png", "html")
        # Automatic first export uses a deterministic device/window name.
        left, right = selected.bounds
        self.export_name.value = f"wmu{selected.wmu_ids[0]}_{left.strftime('%Y%m%dT%H%M%S')}_{right.strftime('%Y%m%dT%H%M%S')}"
        self.export()

    def refresh_cache(self):
        options = [(path.parent.name, str(path.parent)) for path in sorted(
            (self.root / "data/processed").glob("*/manifest.json")
        )]
        self.cache.options = options

    def activate(self):
        self.plot_start.value = self.plot_end.value = ""
        self.dataset = load_dataset(self.dataset.directory, self.phase_convention())
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
        for product, source in (("waveform", self.dataset.waveforms), ("measurements", self.dataset.measurements),
                                ("frequency", self.dataset.frequencies)):
            columns = [column for column in self.columns.value if
                       (column == "frequency_hz") == (product == "frequency")]
            tables = {}
            for wmu, original in source.items():
                if hasattr(original, "plot_table"):
                    table = original.plot_table(start, end, self.max_points.value)
                else:
                    table = original
                    if start is not None:
                        table = table[table.time_utc >= start]
                    if end is not None:
                        table = table[table.time_utc < end]
                if not table.empty:
                    tables[wmu] = table
            if not columns or not tables or not any(column in table for table in tables.values() for column in columns):
                continue
            label = "SYNTHETIC DEMO" if self.dataset.manifest.get("synthetic") else "KGRID"
            frequency_note = ""
            if product == "frequency":
                sources = list(dict.fromkeys(item["frequency_source"] for item in self.dataset.manifest["summaries"]
                                              if item.get("product") == "frequency"))
                frequency_note = " · " + "; ".join(sources)
            figure = go.FigureWidget(build_figure(
                tables, self.locations, columns, self.max_points.value,
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
                for source in (self.dataset.waveforms, self.dataset.measurements, self.dataset.frequencies)
                for table in source.values()
            )
        ]
        self.status.value = "Plots ready. Zoom, pan, or toggle traces; export preserves the current view."
        if unavailable:
            self.status.value += "<br>Unavailable in loaded data: " + html.escape(", ".join(unavailable))
        for wmu, reason in self.dataset.manifest.get("frequency_unavailable", {}).items():
            self.status.value += f"<br>WMU {html.escape(wmu)} frequency: {html.escape(reason)}"

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
            for path in outputs:
                print(path)
                # Jupyter /files/ links resolve even when the working directory is notebooks/.
                relative = path.resolve().relative_to(self.root)
                display(HTML(f'<a href="/files/{quote(relative.as_posix())}" target="_blank">'
                             f'{html.escape(path.name)}</a>'))
        write_json(directory / "plot_config.resolved.json", {
            "dataset": str(self.dataset.directory), "source_manifest": self.dataset.manifest,
            "plot_start_utc": self.plot_start.value or None,
            "plot_end_utc": self.plot_end.value or None,
            "columns": list(self.columns.value), "max_plot_points": self.max_points.value,
            "phase_convention": self.phase_convention().to_dict() if self.phase_convention() else None,
        })
        self.status.value = f"Exported to {html.escape(str(directory))}"
