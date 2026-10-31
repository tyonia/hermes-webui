"""Actual browser module behavior with a small dependency-free DOM fixture."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_selection_and_profile_draft_lifetime():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the provider editor DOM fixture")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests/fixtures/provider_manager.cjs"), str(root / "static/provider_manager.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS selection refresh and profile draft isolation" in result.stdout
