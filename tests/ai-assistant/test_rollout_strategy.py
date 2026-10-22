"""ai-assistant rolls out with Recreate while it mounts its ReadWriteOnce claims.

The Deployment mounts two ReadWriteOnce claims (`data`, `qdrant`). Under the Kubernetes
default (RollingUpdate) the new pod waits for volumes the old pod still holds when the
scheduler puts it on another node, and the old pod is not removed until the new one is
ready: on GKE Autopilot (prod, 2026-10-22) the rollout stalled on a Multi-Attach error.
So, as celine-onboarding does for its claim: with an empty `strategy` and a claim
mounted, the Deployment uses Recreate; an explicit `strategy` wins; with no claim the
Kubernetes default stays.

    task test:ai-assistant

Renders the chart directly (`helm template`, its packaged library under charts/): no
environment, no secrets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "celine-ai-assistant"


def deployment(*sets: str) -> dict:
    args = ["helm", "template", "ai-assistant", str(CHART), "--set", "image.tag=v0"]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    (dep,) = [d for d in yaml.safe_load_all(out.stdout) if d and d["kind"] == "Deployment"]
    return dep


def recreate(dep: dict) -> bool:
    """Recreate, with rollingUpdate cleared (null) so a live RollingUpdate does not linger."""
    s = dep["spec"].get("strategy") or {}
    return s.get("type") == "Recreate" and s.get("rollingUpdate") is None


def test_claims_mounted_use_recreate() -> None:
    assert recreate(deployment())


@pytest.mark.parametrize("claim", ["data", "qdrant"])
def test_one_claim_is_enough(claim: str) -> None:
    other = {"data": "qdrant", "qdrant": "data"}[claim]
    assert recreate(deployment(f"persistence.{other}.enabled=false"))


def test_no_claim_keeps_the_kubernetes_default() -> None:
    dep = deployment("persistence.data.enabled=false", "persistence.qdrant.enabled=false")
    assert "strategy" not in dep["spec"]


def test_an_explicit_strategy_wins() -> None:
    dep = deployment("strategy.type=RollingUpdate", "strategy.rollingUpdate.maxSurge=0")
    assert dep["spec"]["strategy"]["type"] == "RollingUpdate"
