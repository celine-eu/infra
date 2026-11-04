"""mqtt-ingestor's pod and container security contexts: set, they reach the Deployment; unset,
the render is as before.

    task test:mqtt-ingestor
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/mqtt-ingestor"


def pod(values: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values, f)
        r = subprocess.run(["helm", "template", "x", str(CHART), "-f", f.name],
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    [dep] = [d for d in yaml.safe_load_all(r.stdout) if d and d["kind"] == "Deployment"]
    return dep["spec"]["template"]["spec"]


def test_unset_none():
    spec = pod({})
    assert "securityContext" not in spec
    assert "securityContext" not in spec["containers"][0]


def test_set_both_reach_the_pod():
    p = {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
    c = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    spec = pod({"podSecurityContext": p, "securityContext": c})
    assert spec["securityContext"] == p
    assert spec["containers"][0]["securityContext"] == c
