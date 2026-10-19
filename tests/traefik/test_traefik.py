"""infra's Traefik (release `traefik`, group `core`) keeps serving while its pods move,
rendered through helmfile.

The Service keeps `externalTrafficPolicy: Local` (the client address reaches the
allowlists), so a node whose Traefik pod is gone refuses connections until the cloud load
balancer's health check drops it. On GKE Autopilot that happened on node scale-down: about
half the connections refused for a minute. Hence:

- The pods carry `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`: the autoscaler
  does not evict them to remove a node.
- A `preStop` sleep of `ingress_controller.drain_seconds` (default 45): the endpoint turns
  terminating, the node's health check fails and the load balancer drops the node while the
  pod still serves. `0` turns the sleep off. The grace period covers the sleep plus
  Traefik's own shutdown.

    task test:traefik

It needs `helmfile`, `helm` and network access (the chart comes from Traefik's repository).
No secrets: it renders over `envs/dev/values.yaml` plus an override.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HELMFILE = "0010-core.yaml.gotmpl"
RELEASE = "traefik"
ON = "ingress_controller:\n  enabled: true\n"
# Traefik's default graceTimeOut: what it takes to finish requests after the sleep.
TRAEFIK_SHUTDOWN = 10


def _state(tmp: Path, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    (tmp / "override.yaml").write_text(override or "{}\n")
    source = (REPO / "helmfile.d" / HELMFILE).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def render(override: str, env: str = "example") -> dict[str, dict]:
    """Render the traefik release; return its objects by `<kind>/<name>`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def pod_spec(objects: dict[str, dict]) -> dict:
    return objects["Deployment/traefik"]["spec"]["template"]


def container(objects: dict[str, dict]) -> dict:
    (traefik,) = [c for c in pod_spec(objects)["spec"]["containers"] if c["name"] == "traefik"]
    return traefik


@pytest.fixture(scope="module")
def default() -> dict[str, dict]:
    return render(ON)


def test_pods_are_not_evicted_for_node_scale_down(default):
    annotations = pod_spec(default)["metadata"].get("annotations", {})
    assert annotations.get("cluster-autoscaler.kubernetes.io/safe-to-evict") == "false"


def test_pre_stop_sleeps_45_seconds_by_default(default):
    assert container(default)["lifecycle"]["preStop"] == {"sleep": {"seconds": 45}}


def test_grace_period_covers_the_sleep_and_traefiks_shutdown(default):
    grace = pod_spec(default)["spec"]["terminationGracePeriodSeconds"]
    assert grace >= 45 + TRAEFIK_SHUTDOWN


def test_service_keeps_the_client_address(default):
    assert default["Service/traefik"]["spec"]["externalTrafficPolicy"] == "Local"


def test_drain_seconds_sets_the_sleep_and_the_grace_period():
    objects = render(ON + "  drain_seconds: 20\n")
    assert container(objects)["lifecycle"]["preStop"] == {"sleep": {"seconds": 20}}
    assert pod_spec(objects)["spec"]["terminationGracePeriodSeconds"] >= 20 + TRAEFIK_SHUTDOWN


def test_drain_seconds_zero_turns_the_sleep_off():
    objects = render(ON + "  drain_seconds: 0\n")
    assert "preStop" not in (container(objects).get("lifecycle") or {})
    annotations = pod_spec(objects)["metadata"].get("annotations", {})
    assert annotations.get("cluster-autoscaler.kubernetes.io/safe-to-evict") == "false"
