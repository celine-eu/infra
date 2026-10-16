"""Network perimeter and pod-security level of the `celine-<env>` namespace, rendered
through helmfile (release `celine-network-policy`, group `core`).

- Off unless the environment sets `network_policy.enabled: true`: an environment that
  does not ask renders nothing, so its render is unchanged.
- On: one policy denies every incoming connection to every pod of the namespace, a second
  allows the same namespace, the ingress controller's namespace and the CNPG operator's
  (`cnpg`), plus `network_policy.allow_namespaces`. Ingress only: no egress rule.
- The ingress controller's namespace is infra's Traefik (`ingress_controller.namespace`,
  default `ingress`) where infra installs it, else `ingress-nginx`, unless
  `network_policy.ingress_namespace` names another.
- `network_policy.pod_security` labels the namespace (enforce, warn, audit, version) from a
  presync hook; a level other than privileged, baseline or restricted stops the render.

    task test:network-policy

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override.
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
RELEASE = "celine-network-policy"


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


def helmfile(command: list[str], env: str = "staging", override: str = "") -> subprocess.CompletedProcess:
    """Run `helmfile <command>` on the network-policy release as environment `env`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, *command, "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


def render(env: str = "staging", override: str = "") -> subprocess.CompletedProcess:
    return helmfile(["template", "-q", "--skip-deps"], env, override)


def policies(result: subprocess.CompletedProcess) -> dict[str, dict]:
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "NetworkPolicy"}


def allowed_namespaces(allow: dict) -> set[str]:
    names = set()
    for rule in allow["spec"]["ingress"]:
        for peer in rule["from"]:
            sel = peer.get("namespaceSelector")
            if sel is None:
                continue
            for expr in sel.get("matchExpressions", []):
                assert expr["key"] == "kubernetes.io/metadata.name"
                assert expr["operator"] == "In"
                names.update(expr["values"])
    return names


ON = "network_policy:\n  enabled: true\n"


# ---------------------------------------------------------------------------
# off by default
# ---------------------------------------------------------------------------


def test_off_by_default_renders_nothing():
    result = render("staging")
    assert result.returncode == 0, result.stderr
    assert "NetworkPolicy" not in result.stdout


# ---------------------------------------------------------------------------
# on
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prod_like() -> dict[str, dict]:
    return policies(render("example", ON + "ingress_controller:\n  enabled: true\n"))


def test_default_deny_selects_every_pod_and_allows_nothing(prod_like):
    deny = prod_like["default-deny-ingress"]
    assert deny["spec"]["podSelector"] == {}
    assert deny["spec"]["policyTypes"] == ["Ingress"]
    assert not deny["spec"].get("ingress")


def test_perimeter_allows_the_namespace_itself(prod_like):
    allow = prod_like["allow-perimeter"]
    assert allow["spec"]["podSelector"] == {}
    peers = [p for r in allow["spec"]["ingress"] for p in r["from"]]
    assert {"podSelector": {}} in peers


def test_perimeter_allows_traefik_and_cnpg_namespaces(prod_like):
    assert allowed_namespaces(prod_like["allow-perimeter"]) == {"ingress", "cnpg"}


def test_ingress_only_no_egress_rules(prod_like):
    for policy in prod_like.values():
        assert policy["spec"]["policyTypes"] == ["Ingress"]
        assert "egress" not in policy["spec"]


def test_without_infra_ingress_controller_allows_ingress_nginx():
    pols = policies(render("staging", ON))
    assert allowed_namespaces(pols["allow-perimeter"]) == {"ingress-nginx", "cnpg"}


def test_traefik_namespace_follows_its_value():
    pols = policies(render("example", ON + "ingress_controller:\n  enabled: true\n  namespace: edge\n"))
    assert allowed_namespaces(pols["allow-perimeter"]) == {"edge", "cnpg"}


def test_ingress_namespace_override_and_extra_namespaces():
    pols = policies(render("example", ON + "  ingress_namespace: lb\n  allow_namespaces: [ds-a, ds-b]\n"))
    assert allowed_namespaces(pols["allow-perimeter"]) == {"lb", "cnpg", "ds-a", "ds-b"}


def test_policies_live_in_the_environment_namespace():
    result = helmfile(["list", "--output", "json"], "example", ON)
    assert result.returncode == 0, result.stderr
    assert '"namespace":"celine-example"' in result.stdout.replace(" ", "")


# ---------------------------------------------------------------------------
# pod security labels (presync hook)
# ---------------------------------------------------------------------------

PSA = ("  pod_security:\n    enforce: baseline\n    warn: restricted\n"
       "    audit: restricted\n    version: v1.35\n")


def built_hooks(override: str) -> str:
    result = helmfile(["build"], "example", ON + override)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_pod_security_labels_set_by_a_presync_hook():
    out = built_hooks(PSA)
    for label in ("pod-security.kubernetes.io/enforce=baseline",
                  "pod-security.kubernetes.io/enforce-version=v1.35",
                  "pod-security.kubernetes.io/warn=restricted",
                  "pod-security.kubernetes.io/audit=restricted"):
        assert label in out
    assert "celine-example" in out


def test_no_pod_security_value_no_label_command():
    assert "pod-security.kubernetes.io" not in built_hooks("")


def test_unknown_pod_security_level_stops_the_render():
    result = render("example", ON + "  pod_security:\n    enforce: strict\n")
    assert result.returncode != 0
    assert "pod_security.enforce" in result.stderr
