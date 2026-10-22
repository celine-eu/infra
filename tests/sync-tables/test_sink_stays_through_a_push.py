"""sync-tables' sink (release `sync-tables`, group `celine-services`) stays up through a
push, rendered through helmfile.

On GKE Autopilot the cluster autoscaler moved the sink's only pod in the middle of the
first push (2026-10-21): the push ended with HTTP 503. A push runs for minutes, one table
per transaction, so the sink must not be moved voluntarily while it runs. Hence, opt-in per
environment (off, nothing changes):

- `sync_tables.sink.pod_annotations`: annotations on the sink's pods, e.g.
  `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` (on Autopilot that makes it an
  extended-duration pod, at least 500m CPU: the sink already asks for that).
- `sync_tables.sink.pdb: true`: a PodDisruptionBudget with `maxUnavailable: 0`, so a drain
  waits for the operator instead of cutting a push.
- `sync_tables.sink.resources`: the sink's requests, explicit instead of Autopilot's default.

    task test:sync-tables

It needs `helmfile` and `helm`, no secrets: it renders over `envs/dev/values.yaml` plus an
override that carries throwaway PEM strings and a token.
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
HELMFILE = "0050-celine-services.yaml.gotmpl"
RELEASE = "sync-tables"
SINK = """sync_tables:
  image: ghcr.io/celine-eu/sync-tables:0.5.1
  sink:
    enabled: true
    host: sync.example.org
    ingress_flavor: traefik
    allow_source_ranges: 192.0.2.1/32
    dsn_secret: { name: mirror-dsn, key: connection-string }
    allow_schemas: [ds_dev_gold]
    tokens: { push: "0123456789abcdef0123456789abcdef" }
    ca: "-----BEGIN CERTIFICATE-----"
    server_cert: "-----BEGIN CERTIFICATE-----"
    server_key: "-----BEGIN PRIVATE KEY-----"
"""


def _state(tmp: Path, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    (tmp / "override.yaml").write_text(override)
    source = (REPO / "helmfile.d" / HELMFILE).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def render(override: str, env: str = "example") -> dict[str, dict]:
    """Render the sync-tables release; return its objects by `<kind>/<name>`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", "--skip-deps",
             "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def pod_template(objects: dict[str, dict]) -> dict:
    return objects["Deployment/sync-tables-sink"]["spec"]["template"]


@pytest.fixture(scope="module")
def default() -> dict[str, dict]:
    return render(SINK)


@pytest.fixture(scope="module")
def kept() -> dict[str, dict]:
    return render(SINK + """    pod_annotations:
      cluster-autoscaler.kubernetes.io/safe-to-evict: "false"
    pdb: true
    resources:
      requests: { cpu: 500m, memory: 2Gi }
""")


def test_default_adds_no_budget(default):
    assert not [k for k in default if k.startswith("PodDisruptionBudget/")]


def test_default_pods_carry_only_the_checksums(default):
    annotations = pod_template(default)["metadata"]["annotations"]
    assert set(annotations) == {"checksum/ca", "checksum/allow-schemas"}


def test_pod_annotations_reach_the_sink_pods(kept):
    annotations = pod_template(kept)["metadata"]["annotations"]
    assert annotations["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "false"


def test_pod_annotations_keep_the_checksums(kept):
    annotations = pod_template(kept)["metadata"]["annotations"]
    assert {"checksum/ca", "checksum/allow-schemas"} <= set(annotations)


def test_pdb_allows_no_voluntary_disruption(kept):
    spec = kept["PodDisruptionBudget/sync-tables-sink"]["spec"]
    assert spec["maxUnavailable"] == 0
    assert "minAvailable" not in spec


def test_pdb_selects_the_sink_pods_only(kept):
    selector = kept["PodDisruptionBudget/sync-tables-sink"]["spec"]["selector"]["matchLabels"]
    labels = pod_template(kept)["metadata"]["labels"]
    assert selector["app.kubernetes.io/component"] == "sink"
    assert all(labels.get(k) == v for k, v in selector.items())


def test_resources_reach_the_sink_container(kept):
    (sink,) = pod_template(kept)["spec"]["containers"]
    assert sink["resources"] == {"requests": {"cpu": "500m", "memory": "2Gi"}}
