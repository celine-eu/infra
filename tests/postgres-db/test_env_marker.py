"""The env marker Job accepts the four sync_tables environments (dev, prod, staging, preprod)
and refuses any other name.

    task test:postgres-db
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[2] / "charts/postgres-db"


def render(env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["helm", "template", "x", str(CHART), "--set", f"envMarker.environment={env}",
         "--set", "envMarker.database=datasets", "--set", "envMarker.secret=s", "--set", "envMarker.image=pg:17",
         "--show-only", "templates/env-marker-job.yaml"],
        capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("env", ["dev", "prod", "staging", "preprod"])
def test_known_environments_render(env):
    r = render(env)
    assert r.returncode == 0, r.stderr
    assert f"celine:env={env}" in r.stdout


def test_another_name_is_refused():
    r = render("qa")
    assert r.returncode != 0 and "must be dev, prod, staging or preprod" in r.stderr
