"""The workloads prod runs in `celine-<env>` pass Pod Security `restricted`, rendered
through helmfile.

The namespace enforces `baseline` and reports `restricted` (`network_policy.pod_security`).
Before it can enforce `restricted`, every pod template the namespace receives must pass it.
The audit log (2026-10-19 → 2026-10-22) named these, all fixed here as infra defaults, so
every environment gets them:

- policies-shell (bootstrap, bootstrap-record, shell), dataset-api-shell,
  rec-registry-shell: `allowPrivilegeEscalation: false`, drop ALL.
- postgres-db's Jobs (env-marker, grants, mirror-schemas, table-tuning): the same, and
  run as the image's postgres user (uid 26) instead of root.
- keycloak and its dbchecker, redis: the same, by the charts' values.
- the app broker (mosquitto-go-auth): `allowPrivilegeEscalation: false`.
- qdrant: no root `chown` init container (`updateVolumeFsOwnership: false`; the pod's
  fsGroup makes the kubelet set the volume's group) and drop ALL.

Not checked here: the seccomp profile. GKE Autopilot sets `RuntimeDefault` on every pod
before admission, so the audit never reported it; a cluster that does not must set it.
Not covered: auth-setup's dashboard (`nginx:alpine` runs as root); it is off in prod.

    task test:pod-security

It needs `helmfile`, `helm` and network access (keycloak, redis and qdrant come from their
repositories). No secrets: it renders over `envs/dev/values.yaml` plus an override.
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

# Volume types `restricted` allows.
ALLOWED_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral",
                   "persistentVolumeClaim", "projected", "secret"}
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}

POSTGRES_JOBS = """postgres_db:
  sync_environment: prod
  warehouse_tools: [prefect]
  prefect: prefect-pass
  mirror_writer: writer-pass
  mirror:
    enabled: true
    schemas: [ds_dev_gold]
"""

POLICIES_SHELL = """policies_shell:
  bootstrap:
    client_secret: 0123456789abcdef0123456789abcdef
"""

# (helmfile, release, override)
RELEASES = {
    "policies-shell": ("0050-celine-services.yaml.gotmpl", "policies-shell", POLICIES_SHELL),
    "dataset-api-shell": ("0050-celine-services.yaml.gotmpl", "dataset-api-shell", ""),
    "rec-registry-shell": ("0050-celine-services.yaml.gotmpl", "rec-registry-shell", ""),
    "postgres-db": ("0010-core.yaml.gotmpl", "postgres-db", POSTGRES_JOBS),
    "keycloak": ("0020-auth.yaml.gotmpl", "keycloak", ""),
    "redis": ("0010-core.yaml.gotmpl", "redis", ""),
    "mosquitto-go-auth": ("0030-apps.yaml.gotmpl", "mosquitto-go-auth", ""),
    "qdrant": ("0030-apps.yaml.gotmpl", "qdrant", ""),
}


def _state(tmp: Path, helmfile: str, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    (tmp / "override.yaml").write_text(override or "{}\n")
    source = (REPO / "helmfile.d" / helmfile).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def render(helmfile: str, release: str, override: str = "", env: str = "example") -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, helmfile, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", "-l", f"name={release}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert result.returncode == 0, result.stderr
    # The redis chart (0.6.3) ends a line with a tab, which PyYAML refuses.
    text = re.sub(r"[ \t]+$", "", result.stdout, flags=re.MULTILINE)
    return [d for d in yaml.safe_load_all(text) if d]


def pod_templates(objects: list[dict]) -> dict[str, dict]:
    """Every pod spec in the render, by `<kind>/<name>`."""
    pods = {}
    for o in objects:
        if o.get("kind") not in POD_KINDS:
            continue
        # `helm test` pods run only when someone runs `helm test`, not on install.
        if "test" in ((o["metadata"].get("annotations") or {}).get("helm.sh/hook") or ""):
            continue
        spec = o["spec"]
        if o["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        if o["kind"] != "Pod":
            spec = spec["template"]["spec"]
        pods[f"{o['kind']}/{o['metadata']['name']}"] = spec
    return pods


def violations(pod: dict) -> list[str]:
    """What Pod Security `restricted` (v1.35) would refuse in this pod spec, seccomp aside."""
    found = []
    psc = pod.get("securityContext") or {}
    for field in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(field):
            found.append(f"pod sets {field}")
    for v in pod.get("volumes") or []:
        kinds = set(v) - {"name"}
        if not kinds <= ALLOWED_VOLUMES:
            found.append(f"volume {v['name']} is {sorted(kinds - ALLOWED_VOLUMES)}")
    if psc.get("runAsUser") == 0:
        found.append("pod runs as uid 0")
    containers = [*(pod.get("initContainers") or []), *(pod.get("containers") or [])]
    for c in containers:
        sc = c.get("securityContext") or {}
        name = c["name"]
        if sc.get("privileged"):
            found.append(f"{name}: privileged")
        if sc.get("allowPrivilegeEscalation") is not False:
            found.append(f"{name}: allowPrivilegeEscalation is not false")
        caps = sc.get("capabilities") or {}
        if "ALL" not in (caps.get("drop") or []):
            found.append(f"{name}: capabilities do not drop ALL")
        if set(caps.get("add") or []) - {"NET_BIND_SERVICE"}:
            found.append(f"{name}: adds {caps['add']}")
        if not (sc.get("runAsNonRoot", psc.get("runAsNonRoot")) is True):
            found.append(f"{name}: runAsNonRoot is not true")
        if sc.get("runAsUser") == 0:
            found.append(f"{name}: runs as uid 0")
        for p in c.get("ports") or []:
            if p.get("hostPort"):
                found.append(f"{name}: hostPort {p['hostPort']}")
    return found


@pytest.fixture(scope="module", params=sorted(RELEASES))
def rendered(request) -> tuple[str, list[dict]]:
    helmfile, release, override = RELEASES[request.param]
    return request.param, render(helmfile, release, override)


def test_the_release_renders_pods(rendered):
    name, objects = rendered
    assert pod_templates(objects), f"{name} renders no pod template"


def test_every_pod_passes_restricted(rendered):
    name, objects = rendered
    found = {k: v for k, pod in pod_templates(objects).items() if (v := violations(pod))}
    assert not found, f"{name}: {found}"


def test_postgres_jobs_are_all_rendered():
    objects = render(*RELEASES["postgres-db"][:2], POSTGRES_JOBS)
    jobs = {k for k in pod_templates(objects) if k.startswith("Job/")}
    assert {"Job/postgis1-env-marker", "Job/postgis1-grants", "Job/postgis1-mirror-schemas",
            "Job/postgis1-table-tuning"} <= jobs


def test_postgres_jobs_run_as_the_postgres_user():
    objects = render(*RELEASES["postgres-db"][:2], POSTGRES_JOBS)
    for key, pod in pod_templates(objects).items():
        if key.startswith("Job/"):
            assert pod["securityContext"]["runAsUser"] == 26, key


def test_qdrant_has_no_root_chown_init_container():
    objects = render(*RELEASES["qdrant"][:2])
    (pod,) = pod_templates(objects).values()
    assert not [c for c in pod.get("initContainers") or [] if c["name"] == "ensure-dir-ownership"]
    assert pod["securityContext"]["fsGroup"] == 3000


# ---------------------------------------------------------------------------
# the checker itself
# ---------------------------------------------------------------------------


def test_checker_flags_a_root_container():
    pod = {"containers": [{"name": "c", "securityContext": {"runAsUser": 0}}]}
    found = violations(pod)
    assert "c: runs as uid 0" in found
    assert "c: allowPrivilegeEscalation is not false" in found


def test_checker_passes_a_restricted_container():
    pod = {"securityContext": {"runAsNonRoot": True},
           "containers": [{"name": "c", "securityContext": {
               "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]}
    assert violations(pod) == []


def test_checker_flags_a_host_path():
    pod = {"volumes": [{"name": "h", "hostPath": {"path": "/"}}], "containers": []}
    assert violations(pod) == ["volume h is ['hostPath']"]
