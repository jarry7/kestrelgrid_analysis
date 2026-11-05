"""Enforce docstrings for every authored Python callable, including nested helpers."""

import ast
from pathlib import Path


def test_all_authored_python_callables_have_docstrings():
    """Require module/class/function docstrings in source and test files."""
    root = Path(__file__).resolve().parents[1]
    missing = []
    for folder in (root / "src", root / "tests"):
        for path in folder.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    if not ast.get_docstring(node):
                        missing.append(
                            f"{path.relative_to(root)}:{getattr(node, 'lineno', 1)}:{getattr(node, 'name', 'module')}"
                        )
    assert not missing, "Missing docstrings:\n" + "\n".join(missing)
