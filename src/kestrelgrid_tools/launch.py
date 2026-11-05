"""Start JupyterLab with a named kernel pinned to the application's environment."""

import subprocess
import sys
from pathlib import Path

from ipykernel.kernelspec import install


def main():
    root = next((path for path in (Path.cwd(), *Path.cwd().parents)
                 if (path / "config/wmu_locations.json").exists()), None)
    if root is None:
        raise SystemExit("Run from the kestrelgrid_analysis checkout")
    install(prefix=sys.prefix, kernel_name="kgrid-analysis", display_name="KGRID Analysis (project Python)")
    result = subprocess.run([
        sys.executable, "-m", "jupyterlab", f"--ServerApp.root_dir={root}",
        str(root / "notebooks/kgrid_explorer.ipynb"), *sys.argv[1:],
    ], cwd=root)
    raise SystemExit(result.returncode)
