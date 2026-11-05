"""pg-freezer's security contexts and extra volumes: set, they reach the CronJob's pod; unset,
the render is as before.

    task test:pg-freezer
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/pg-freezer"


def pod(values: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values, f)
        r = subprocess.run(["helm", "template", "x", str(CHART), "-f", f.name],
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    [cj] = [d for d in yaml.safe_load_all(r.stdout) if d and d["kind"] == "CronJob"]
    return cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def test_unset_none():
    spec = pod({})
    assert "securityContext" not in spec and "securityContext" not in spec["containers"][0]
    assert [v["name"] for v in spec["volumes"]] == ["config"]


def test_set_they_reach_the_pod():
    p = {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
    c = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    spec = pod({"podSecurityContext": p, "securityContext": c,
                "extraVolumes": [{"name": "tmp", "emptyDir": {}}],
                "extraVolumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]})
    assert spec["securityContext"] == p
    assert spec["containers"][0]["securityContext"] == c
    assert {"name": "tmp", "emptyDir": {}} in spec["volumes"]
    assert {"name": "tmp", "mountPath": "/tmp"} in spec["containers"][0]["volumeMounts"]
