"""oauth2-proxy trusts a CA bundle for its provider (`oauth2_proxy.provider_ca`).

A realm on an internal host (keycloak.internal_ingress) has a certificate from the
platform's own CA, which oauth2-proxy's image does not trust. `oauth2_proxy.provider_ca`
({configmap, key}) mounts that key of a ConfigMap in the namespace (e.g. trust-manager's
`platform-ca-bundle`: the public roots plus the platform's) and points
OAUTH2_PROXY_PROVIDER_CA_FILES at it. Unset, the render is today's. Both keys are required.

    task test:auth-setup

It needs `helmfile`, `helm` and network access (the oauth2-proxy chart is remote). No secrets:
it renders over `envs/dev/values.yaml` plus an override.
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
# the dev values use the self-signed CA (tls_setup.self_signed), which mounts its own
PUBLIC_CA = "tls_setup:\n  self_signed: false\n"
CA = "oauth2_proxy:\n  provider_ca:\n    configmap: platform-ca-bundle\n    key: ca-certificates.crt\n"


def render(override: str = "", base: str = PUBLIC_CA) -> subprocess.CompletedProcess:
    override = base + override
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override or "{}\n")
        source = (REPO / "helmfile.d" / "0020-auth.yaml.gotmpl").read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = ("environments:\n  example:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "example", "template", "-q",
             "-l", "name=oauth2-proxy"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


def deployment(override: str = "") -> dict:
    result = render(override)
    assert result.returncode == 0, result.stderr
    (dep,) = [d for d in yaml.safe_load_all(result.stdout) if d and d.get("kind") == "Deployment"]
    return dep


def proxy(dep: dict) -> dict:
    (c,) = [c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "oauth2-proxy"]
    return c


def env_of(c: dict) -> dict[str, dict]:
    return {e["name"]: e for e in c.get("env") or []}


def test_unset_no_provider_ca():
    dep = deployment()
    assert "OAUTH2_PROXY_PROVIDER_CA_FILES" not in env_of(proxy(dep))
    assert "provider-ca" not in yaml.safe_dump(dep)


def test_the_bundle_is_mounted_and_named_as_the_providers_ca():
    dep = deployment(CA)
    c = proxy(dep)
    assert env_of(c)["OAUTH2_PROXY_PROVIDER_CA_FILES"]["value"] == "/etc/ssl/provider-ca/ca-certificates.crt"
    (mount,) = [m for m in c["volumeMounts"] if m["name"] == "provider-ca"]
    assert mount == {"name": "provider-ca", "mountPath": "/etc/ssl/provider-ca", "readOnly": True}
    (vol,) = [v for v in dep["spec"]["template"]["spec"]["volumes"] if v["name"] == "provider-ca"]
    assert vol["configMap"] == {"name": "platform-ca-bundle",
                                "items": [{"key": "ca-certificates.crt", "path": "ca-certificates.crt"}]}


@pytest.mark.parametrize("override", [CA.replace("    key: ca-certificates.crt\n", ""),
                                      CA.replace("    configmap: platform-ca-bundle\n", "")])
def test_both_keys_are_required(override):
    result = render(override)
    assert result.returncode != 0
    assert "oauth2_proxy.provider_ca" in result.stderr


def test_self_signed_and_provider_ca_together_are_refused():
    result = render(CA, base="tls_setup:\n  self_signed: true\n  ca_secret: celine-ca\n")
    assert result.returncode != 0
    assert "oauth2_proxy.provider_ca" in result.stderr
