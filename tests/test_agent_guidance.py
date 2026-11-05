"""Validate project agent guidance, discoverable skills and offline scenario definitions."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "kgrid-data-acquisition",
    "kgrid-frequency-analysis",
    "kgrid-notebook-validation",
    "kgrid-analysis-storage",
}


def test_project_skills_have_valid_discoverable_metadata():
    """Require unique folder-matched names and specific descriptions in concise SKILL.md files."""
    files = sorted((ROOT / ".opencode/skills").glob("*/SKILL.md"))
    names = set()
    for path in files:
        text = path.read_text()
        assert text.startswith("---\n")
        metadata, body = text[4:].split("\n---\n", 1)
        fields = dict(line.split(": ", 1) for line in metadata.splitlines())
        name = fields["name"]
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        assert name == path.parent.name and len(name) <= 64 and name not in names
        assert 1 <= len(fields["description"]) <= 1024
        assert fields["description"].startswith("Use when")
        assert body.strip() and len(text.splitlines()) < 150
        names.add(name)
    assert names == SKILLS


def test_agent_guidance_relative_links_exist():
    """Resolve all local Markdown links against their own file locations."""
    paths = [ROOT / "AGENTS.md", *(ROOT / ".opencode").rglob("*.md")]
    for path in paths:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            resolved = (path.parent / target).resolve()
            assert resolved.is_relative_to(ROOT)
            assert resolved.exists(), f"Broken link: {path.relative_to(ROOT)} -> {target}"


def test_skill_scenarios_use_known_skills_and_include_negative_and_boundary_cases():
    """Keep evaluation definitions actionable, offline, and limited to discoverable skills."""
    suite = json.loads((ROOT / ".opencode/evals/skill-scenarios.json").read_text())
    scenarios = suite["scenarios"]
    ids = [item["id"] for item in scenarios]
    assert len(set(ids)) == len(ids)
    assert "duration-boundary" in ids and "generic-python-negative" in ids
    assert "no network" in suite["evaluation_mode"]
    for item in scenarios:
        assert item["prompt"] and item["expected"]
        assert set(item["skills"]) <= SKILLS
    negative = next(item for item in scenarios if item["id"] == "generic-python-negative")
    assert not negative["skills"]
