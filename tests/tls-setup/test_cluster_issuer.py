"""tls-setup can add a ClusterIssuer beside its namespaced Issuer, one environment at a time.

The namespaced ACME Issuer serves only Ingresses in its own namespace. Releases installed in
namespaces of their own (an add-on's, one per participant) need a ClusterIssuer to name. So
`tls_setup.cluster_issuer.enabled` renders one with the namespaced Issuer's ACME settings
(server, e-mail, HTTP-01 solver and its IngressClass), its account key in its own Secret
(cert-manager keeps a ClusterIssuer's in its cluster resource namespace). Its name is
`tls_setup.cluster_issuer.name`, default `<issuer>-cluster`. Off by default: an environment
that does not set the key renders unchanged.

    task test:tls-setup

Needs `helmfile` and `helm`, no secrets: it renders over `envs/dev/values.yaml` plus an
override.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ACME = """tls_setup:
  self_signed: false
  issuer: example-tls
  email: ops@example.org
ingress_controller:
  class: traefik
"""


def render(override: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = (REPO / "helmfile.d" / "0010-core.yaml.gotmpl").read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        (tmp / "override.yaml").write_text(override)
        header = (f"environments:\n  example:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        out = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "example", "template", "-q",
             "--skip-deps", "--skip-needs", "-l", "name=tls-setup"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


def test_no_cluster_issuer_by_default() -> None:
    assert not of_kind(render(ACME), "ClusterIssuer")


def test_the_cluster_issuer_mirrors_the_namespaced_one() -> None:
    docs = render(ACME.replace("tls_setup:\n", "tls_setup:\n  cluster_issuer:\n    enabled: true\n"))
    (issuer,) = of_kind(docs, "Issuer")
    (cluster,) = of_kind(docs, "ClusterIssuer")
    assert cluster["metadata"]["name"] == "example-tls-cluster"
    acme, nacme = cluster["spec"]["acme"], issuer["spec"]["acme"]
    assert acme["server"] == nacme["server"]
    assert acme["email"] == nacme["email"] == "ops@example.org"
    assert acme["solvers"] == nacme["solvers"] == [{"http01": {"ingress": {"ingressClassName": "traefik"}}}]
    assert acme["privateKeySecretRef"]["name"] == "example-tls-cluster"


def test_the_cluster_issuer_takes_a_name() -> None:
    docs = render(ACME.replace("tls_setup:\n", "tls_setup:\n  cluster_issuer:\n    enabled: true\n    name: shared-tls\n"))
    (cluster,) = of_kind(docs, "ClusterIssuer")
    assert cluster["metadata"]["name"] == "shared-tls"
    assert cluster["spec"]["acme"]["privateKeySecretRef"]["name"] == "shared-tls"


def test_the_namespaced_issuer_is_unchanged() -> None:
    plain = of_kind(render(ACME), "Issuer")
    with_cluster = of_kind(render(ACME.replace("tls_setup:\n", "tls_setup:\n  cluster_issuer:\n    enabled: true\n")), "Issuer")
    assert plain == with_cluster
