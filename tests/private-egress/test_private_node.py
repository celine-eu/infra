"""The releases that send mail and SMS can run on private nodes, one environment at a time.

Brevo (mail and SMS) accepts calls only from addresses on its allowlist. On GKE Autopilot a
pod on an ordinary node leaves by that node's ephemeral address, which changes with every
move. A pod with the node selector `cloud.google.com/private-node: "true"` runs on a node
without an external address and leaves through the cluster's Cloud NAT, whose address is
reserved (prod, 2026-10-22: one allowlist entry). So the senders take a node selector per
environment:

- `onboarding.node_selector` (SMS through Brevo's API, mail through SMTP);
- `keycloak.node_selector` (the realm's mail: password reset, e-mail verification).

Unset, nothing changes: the charts keep their default (no selector).

    task test:private-egress

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
PRIVATE = {"cloud.google.com/private-node": "true"}
ONBOARDING = """onboarding:
  enabled: true
  encryption_key: "{key}"
""".format(key="A" * 43 + "=")


def render(helmfile: str, release: str, override: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = (REPO / "helmfile.d" / helmfile).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        (tmp / "override.yaml").write_text(override or "{}\n")
        header = (f"environments:\n  dev:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        out = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "dev", "template", "-q",
             "-l", f"name={release}", "--skip-tests"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def pod_spec(docs: list[dict], kind: str) -> dict:
    (w,) = [d for d in docs if d.get("kind") == kind]
    return w["spec"]["template"]["spec"]


def onboarding(extra: str = "") -> dict:
    return pod_spec(render("0050-celine-services.yaml.gotmpl", "onboarding", ONBOARDING + extra), "Deployment")


def keycloak(extra: str = "") -> dict:
    return pod_spec(render("0020-auth.yaml.gotmpl", "keycloak", extra), "StatefulSet")


def test_onboarding_has_no_selector_by_default() -> None:
    assert not onboarding().get("nodeSelector")


def test_onboarding_takes_the_private_node_selector() -> None:
    spec = onboarding('  node_selector:\n    cloud.google.com/private-node: "true"\n')
    assert spec["nodeSelector"] == PRIVATE


def test_keycloak_has_no_selector_by_default() -> None:
    assert not keycloak().get("nodeSelector")


def test_keycloak_takes_the_private_node_selector() -> None:
    spec = keycloak('keycloak:\n  node_selector:\n    cloud.google.com/private-node: "true"\n')
    assert spec["nodeSelector"] == PRIVATE
