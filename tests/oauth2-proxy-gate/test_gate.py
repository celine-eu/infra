"""An admin UI behind its own oauth2-proxy, as a reverse proxy, with no Traefik Middleware,
rendered through helmfile.

On a cluster where a tenant may not create `middlewares.traefik.io` (Traefik's egress is
unrestricted, so a tenant-written forwardAuth address would bypass the tenant's egress
policy), the forwardAuth gate cannot exist. Each entry of `oauth2_proxy.gates` is then a
release `oauth2-proxy-<name>` of the oauth2-proxy chart that owns the host
`<host>.<domain>` (host defaults to name), signs the user in and forwards to `upstream`:

- its own Ingress on that host, backed by its own Service, with no forwardAuth annotation;
- its own config (no `oauth2-proxy-config` ConfigMap, no Redis): cookie session, the
  callback on its own host, only `role:<auth_setup.adminRole>` admitted;
- the environment's oauth2-proxy client (`auth_setup.clientID`/`clientSecret`/
  `cookieSecret`), so the Keycloak client stays the one celine-policies declares;
- with `keycloak.internal_url`, the back channel (token, keys) goes to that in-cluster
  address and discovery is skipped; the issuer and the browser's login stay public;
- Pod Security `restricted` holds.

The UI's own Ingress gives the host up with `prefect_server.ingress_enabled: false` /
`marquez.ingress_enabled: false`. Without gates nothing changes.

    task test:oauth2-proxy-gate

It needs `helmfile`, `helm` and network access (oauth2-proxy's and Prefect's chart
repositories). No secrets: it renders over `envs/dev/values.yaml` plus an override.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
AUTH = "0020-auth.yaml.gotmpl"

GATES = """namespace: celine-tenant
domain: example.labs
ingress_controller:
  class: traefik
  annotations: traefik
tls_setup:
  ingress_cluster_issuer: platform-ca
  self_signed: false
oauth2_proxy:
  gates:
    - name: prefect
      upstream: http://prefect-server:4200/
    - name: lineage
      host: marquez
      upstream: http://marquez-marquez-web:5002/
"""

INTERNAL = GATES + """keycloak:
  realm: celine-warehouse
  public_url: https://keycloak.example.labs
  internal_url: http://keycloak-keycloakx-http.identity.svc
"""


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


def _helmfile(helmfile: str, args: list[str], override: str, env: str = "example") -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, helmfile, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(["helmfile", "-f", str(state), "-e", env, *args],
                              capture_output=True, text=True, env=environ, timeout=600, check=False)


def render(helmfile: str, release: str, override: str) -> dict[str, dict]:
    result = _helmfile(helmfile, ["template", "-q", "-l", f"name={release}"], override)
    assert result.returncode == 0, result.stderr
    text = re.sub(r"[ \t]+$", "", result.stdout, flags=re.MULTILINE)
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in yaml.safe_load_all(text) if d}


def releases(helmfile: str, override: str) -> set[str]:
    result = _helmfile(helmfile, ["list", "--output", "json"], override)
    assert result.returncode == 0, result.stderr
    return {r["name"] for r in json.loads(result.stdout)}


def config_of(objects: dict[str, dict], release: str) -> str:
    return objects[f"ConfigMap/{release}"]["data"]["oauth2_proxy.cfg"]


@pytest.fixture(scope="module")
def prefect_gate() -> dict[str, dict]:
    return render(AUTH, "oauth2-proxy-prefect", GATES)


@pytest.fixture(scope="module")
def internal_gate() -> dict[str, dict]:
    return render(AUTH, "oauth2-proxy-prefect", INTERNAL)


def test_without_gates_there_is_no_gate_release():
    assert not {r for r in releases(AUTH, "") if r.startswith("oauth2-proxy-")}


def test_one_release_per_gate():
    assert {"oauth2-proxy-prefect", "oauth2-proxy-lineage"} <= releases(AUTH, GATES)


def test_the_gate_owns_its_host_with_no_middleware(prefect_gate):
    ingress = prefect_gate["Ingress/oauth2-proxy-prefect"]
    rule = ingress["spec"]["rules"][0]
    assert rule["host"] == "prefect.example.labs"
    assert rule["http"]["paths"][0]["backend"]["service"]["name"] == "oauth2-proxy-prefect"
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert ingress["spec"]["tls"][0]["hosts"] == ["prefect.example.labs"]
    annotations = ingress["metadata"]["annotations"]
    assert annotations["cert-manager.io/cluster-issuer"] == "platform-ca"
    assert not [k for k in annotations if "middlewares" in k or k.startswith("nginx.")]
    assert not [k for k in prefect_gate if k.startswith("Middleware/")]


def test_host_defaults_to_name_and_can_be_set():
    lineage = render(AUTH, "oauth2-proxy-lineage", GATES)
    assert lineage["Ingress/oauth2-proxy-lineage"]["spec"]["rules"][0]["host"] == "marquez.example.labs"
    assert 'redirect_url = "https://marquez.example.labs/oauth2/callback"' in config_of(lineage, "oauth2-proxy-lineage")


def test_the_gate_forwards_to_its_upstream_and_admits_platform_admins_only(prefect_gate):
    cfg = config_of(prefect_gate, "oauth2-proxy-prefect")
    assert 'upstreams = [ "http://prefect-server:4200/" ]' in cfg
    assert 'redirect_url = "https://prefect.example.labs/oauth2/callback"' in cfg
    assert 'allowed_groups = [ "role:platform-admin" ]' in cfg
    assert 'session_store_type = "cookie"' in cfg
    assert "cookie_secure = true" in cfg
    assert "redis" not in cfg
    assert "cookie_domains" not in cfg, "the session cookie stays on the gate's own host"


def test_the_gate_uses_the_environments_oauth2_proxy_client(prefect_gate):
    secret = prefect_gate["Secret/oauth2-proxy-prefect"]
    data = secret.get("data") or {}
    import base64
    got = {k: base64.b64decode(v).decode() for k, v in data.items()}
    assert got["client-id"] == "oauth2_proxy"
    assert got["client-secret"] == "oauth2_proxy"
    assert got["cookie-secret"] == "3d749938f5b65074ea3c7359a06ab108"


def test_public_discovery_without_an_internal_url(prefect_gate):
    cfg = config_of(prefect_gate, "oauth2-proxy-prefect")
    assert 'oidc_issuer_url = "https://keycloak.example.labs/realms/celine"' in cfg
    assert "skip_oidc_discovery" not in cfg


def test_back_channel_to_the_internal_url(internal_gate):
    cfg = config_of(internal_gate, "oauth2-proxy-prefect")
    realm = "realms/celine-warehouse/protocol/openid-connect"
    assert 'oidc_issuer_url = "https://keycloak.example.labs/realms/celine-warehouse"' in cfg
    assert "skip_oidc_discovery = true" in cfg
    assert f'login_url = "https://keycloak.example.labs/{realm}/auth"' in cfg
    assert f'redeem_url = "http://keycloak-keycloakx-http.identity.svc/{realm}/token"' in cfg
    assert f'oidc_jwks_url = "http://keycloak-keycloakx-http.identity.svc/{realm}/certs"' in cfg


def test_the_gate_passes_restricted(prefect_gate):
    pod = prefect_gate["Deployment/oauth2-proxy-prefect"]["spec"]["template"]["spec"]
    for c in [*(pod.get("initContainers") or []), *pod["containers"]]:
        sc = c.get("securityContext") or {}
        assert sc.get("allowPrivilegeEscalation") is False, c["name"]
        assert "ALL" in ((sc.get("capabilities") or {}).get("drop") or []), c["name"]
        assert sc.get("runAsNonRoot") is True, c["name"]
        assert (sc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", c["name"]


def test_a_gate_without_upstream_is_refused():
    result = _helmfile(AUTH, ["template", "-q", "-l", "name=oauth2-proxy-x"],
                       "oauth2_proxy:\n  gates:\n    - name: x\n")
    assert result.returncode != 0
    assert "upstream" in result.stderr


@pytest.mark.parametrize("helmfile,release,key", [
    ("0040-pipelines.yaml.gotmpl", "prefect-server", "prefect_server"),
    ("0030-apps.yaml.gotmpl", "marquez", "marquez"),
])
def test_the_ui_gives_its_host_up(helmfile, release, key):
    before = render(helmfile, release, GATES)
    assert [k for k in before if k.startswith("Ingress/")], "the UI keeps its Ingress by default"
    after = render(helmfile, release, GATES + f"{key}:\n  ingress_enabled: false\n")
    assert not [k for k in after if k.startswith("Ingress/")]
