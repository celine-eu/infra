"""The workloads a member's request goes through stay up when the cluster autoscaler
scales a node down, rendered through helmfile.

On GKE Autopilot (prod, 2026-10-23) the autoscaler deleted `webapp` (one replica, no
PodDisruptionBudget) twice in 13 minutes, and both `dataset-api` pods: the member's webapp
answered Traefik's "503 no available server" until the new pod was ready. The autoscaler
does not remove a node whose pods a PodDisruptionBudget does not let it evict, nor one
whose pods carry `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`. Hence, opt-in
per environment and per release (absent, nothing changes):

    pod_disruption:
      <release>:                # e.g. webapp, frontend-webapp, keycloak, oauth2-proxy, redis
        pdb: false              # true: a PodDisruptionBudget over the release's pods
        max_unavailable: 0      # the budget's maxUnavailable (0: no voluntary eviction)
        pod_annotations: {}     # extra annotations on the release's pods

The budget is the cheap one on Autopilot: `safe-to-evict: "false"` makes an
extended-duration pod, which Autopilot raises to at least 500m CPU (Traefik, 2026-10-19).
Neither touches a rollout: the Deployment controller does not go through eviction, so a
RollingUpdate or Recreate release rolls as before.

    task test:pod-disruption

It needs `helmfile`, `helm` and network access (keycloak, oauth2-proxy and redis come from
their repositories). No secrets: it renders over `envs/dev/values.yaml` plus an override.
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
SAFE = "cluster-autoscaler.kubernetes.io/safe-to-evict"

# Releases a member's request goes through, by helmfile. The library charts (celine-services).
SERVICES = ["webapp", "frontend-webapp", "legal", "digital-twin", "rec-registry", "nudging",
            "flexibility-api", "dataset-api", "onboarding", "frontend-onboarding",
            "frontend-assistant", "ai-assistant"]
# The third-party charts: release -> (helmfile, kind of the workload).
THIRD_PARTY = {
    "keycloak": ("0020-auth.yaml.gotmpl", "StatefulSet"),
    "oauth2-proxy": ("0020-auth.yaml.gotmpl", "Deployment"),
    "redis": ("0010-core.yaml.gotmpl", "StatefulSet"),
}
RECREATE = ["onboarding", "ai-assistant"]

# What the releases need to render at all in the example environment.
BASE = """security_headers:
  enabled: true
onboarding:
  enabled: true
  persistence: {enabled: true}     # as prod: a ReadWriteOnce claim, so Recreate
  encryption_key: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
legal:
  enabled: true
  image_tag: v0.1.0
redis:
  password: made-up-redis-password-0123456789
"""
LEGAL_DATA = """data:
  files:
    register.yaml: "documents: []"
"""


def _state(tmp: Path, helmfile: str, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    (tmp / "override.yaml").write_text(override)
    source = (REPO / "helmfile.d" / helmfile).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    # The legal host's data comes from the deployment's own release file, not the values.
    legal = tmp / "envs" / env / "0050-celine-services" / "celine-legal"
    legal.mkdir(parents=True)
    (legal / "values.yaml.gotmpl").write_text(LEGAL_DATA)
    source = source.replace("../envs/", f"{tmp}/envs/")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def render(helmfile: str, releases: list[str], override: str, env: str = "example") -> list[dict]:
    """Render the named releases of one helmfile; return every object."""
    selectors = [arg for r in releases for arg in ("-l", f"name={r}")]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, helmfile, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", *selectors],
            capture_output=True, text=True, env=environ, timeout=900, check=False,
        )
    assert result.returncode == 0, result.stderr
    # The redis chart (0.6.3) ends a line with a tab, which PyYAML refuses.
    text = re.sub(r"[ \t]+$", "", result.stdout, flags=re.MULTILINE)
    return [d for d in yaml.safe_load_all(text) if d]


def render_all(override: str) -> list[dict]:
    objects = render("0050-celine-services.yaml.gotmpl", SERVICES, override)
    objects += render("0020-auth.yaml.gotmpl", ["keycloak", "oauth2-proxy"], override)
    objects += render("0010-core.yaml.gotmpl", ["redis"], override)
    return objects


def guarded(**extra) -> str:
    """BASE plus `pod_disruption` for every release in scope."""
    entries = {r: {"pdb": True, **extra} for r in [*SERVICES, *THIRD_PARTY]}
    entries["dataset-api"]["max_unavailable"] = 1
    return BASE + yaml.safe_dump({"pod_disruption": entries})


@pytest.fixture(scope="module")
def default() -> list[dict]:
    return render_all(BASE)


@pytest.fixture(scope="module")
def kept() -> list[dict]:
    return render_all(guarded(pod_annotations={SAFE: "false"}))


def workload(objects: list[dict], release: str) -> dict:
    kinds = {"Deployment", "StatefulSet"}
    found = [o for o in objects if o["kind"] in kinds
             and o["metadata"].get("labels", {}).get("app.kubernetes.io/instance") == release
             and o["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/instance") == release]
    assert len(found) == 1, f"{release}: {[o['metadata']['name'] for o in found]}"
    return found[0]


def budgets(objects: list[dict], release: str) -> list[dict]:
    """The PodDisruptionBudgets whose selector picks the release's pods."""
    labels = workload(objects, release)["spec"]["template"]["metadata"]["labels"]
    return [o for o in objects if o["kind"] == "PodDisruptionBudget"
            and all(labels.get(k) == v for k, v in o["spec"]["selector"]["matchLabels"].items())]


def annotations(objects: list[dict], release: str) -> dict:
    return workload(objects, release)["spec"]["template"]["metadata"].get("annotations") or {}


ALL = [*SERVICES, *THIRD_PARTY]


@pytest.mark.parametrize("release", ALL)
def test_default_adds_no_budget(default, release):
    assert budgets(default, release) == []


@pytest.mark.parametrize("release", ALL)
def test_default_pods_are_evictable(default, release):
    assert SAFE not in annotations(default, release)


@pytest.mark.parametrize("release", [r for r in ALL if r != "dataset-api"])
def test_budget_allows_no_voluntary_eviction(kept, release):
    (pdb,) = budgets(kept, release)
    assert pdb["spec"]["maxUnavailable"] == 0
    assert "minAvailable" not in pdb["spec"]


def test_two_replica_budget_lets_one_go(kept):
    (pdb,) = budgets(kept, "dataset-api")
    assert pdb["spec"]["maxUnavailable"] == 1


@pytest.mark.parametrize("release", ALL)
def test_budget_selects_this_release_only(kept, release):
    (pdb,) = budgets(kept, release)
    assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/instance"] == release


@pytest.mark.parametrize("release", ALL)
def test_pod_annotations_reach_the_pods(kept, release):
    assert annotations(kept, release)[SAFE] == "false"


@pytest.mark.parametrize("release", ALL)
def test_pod_annotations_keep_the_chart_ones(default, kept, release):
    before = annotations(default, release)
    after = annotations(kept, release)
    assert {k: v for k, v in after.items() if k != SAFE} == before


@pytest.mark.parametrize("release", ALL)
def test_replicas_unchanged(default, kept, release):
    assert workload(kept, release)["spec"].get("replicas") == workload(default, release)["spec"].get("replicas")


@pytest.mark.parametrize("release", RECREATE)
def test_recreate_stays_recreate(default, kept, release):
    assert workload(default, release)["spec"]["strategy"]["type"] == "Recreate"
    assert workload(kept, release)["spec"]["strategy"] == workload(default, release)["spec"]["strategy"]


def test_one_release_does_not_guard_another():
    objects = render("0050-celine-services.yaml.gotmpl", ["webapp", "frontend-webapp"],
                     BASE + "pod_disruption:\n  webapp:\n    pdb: true\n    pod_annotations: {a: b}\n")
    assert len(budgets(objects, "webapp")) == 1
    assert budgets(objects, "frontend-webapp") == []
    assert "a" not in annotations(objects, "frontend-webapp")


def test_redis_keeps_its_password_secret(kept):
    secrets = [o for o in kept if o["kind"] == "Secret" and o["metadata"]["name"] == "redis-auth"]
    assert len(secrets) == 1
