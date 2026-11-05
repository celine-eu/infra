"""Qdrant's requests and limits come from `qdrant.resources` where an environment sets it,
and stay at 500m / 512Mi where it does not.

Autopilot bills a pod's requests, not its use: prod's qdrant asked 500m / 512Mi and peaked
at 0.007 CPU / 98Mi over the week to 2026-10-30. Staging sets nothing and renders as
before.

    task test:qdrant

It needs `helmfile`, `helm` and network access (the chart is remote). No secrets: it
renders over `envs/dev/values.yaml` plus an override.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

SIZED = """qdrant:
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { cpu: 500m, memory: 512Mi }
"""


def render(override: str, env: str = "example") -> list[dict]:
    """Render the qdrant release over the dev values plus `override`, as environment `env`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override or "{}\n")
        source = (REPO / "helmfile.d" / "0030-apps.yaml.gotmpl").read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = (f"environments:\n  {env}:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", env, "template", "-q",
             "-l", "name=qdrant"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def qdrant_container(objects: list[dict]) -> dict:
    (sts,) = [o for o in objects if o.get("kind") == "StatefulSet"]
    (container,) = [c for c in sts["spec"]["template"]["spec"]["containers"] if c["name"] == "qdrant"]
    return container


def test_default_resources_are_unchanged():
    resources = qdrant_container(render(""))["resources"]
    assert resources == {"requests": {"cpu": "500m", "memory": "512Mi"},
                         "limits": {"cpu": "500m", "memory": "512Mi"}}


def test_an_environment_sets_its_own_resources():
    resources = qdrant_container(render(SIZED))["resources"]
    assert resources == {"requests": {"cpu": "100m", "memory": "256Mi"},
                         "limits": {"cpu": "500m", "memory": "512Mi"}}
