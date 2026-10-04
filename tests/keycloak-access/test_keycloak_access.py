"""Who reaches Keycloak, and through which door, rendered through helmfile.

- The public host serves the platform realm only: its Ingress lists `/realms/<realm>` and
  `/resources`, so `/admin` (admin console and admin REST API), `/realms/master`, any other
  realm and `/` match no rule.
- The master realm and the admin console are reached through `kubectl port-forward` on
  http://localhost:<keycloak.admin_forward_port>: KC_HOSTNAME_ADMIN says so, KC_HOSTNAME stays
  the public URL. There is no admin Ingress.
- What administers Keycloak from inside the cluster (the policies-shell bootstrap and sync,
  provisioning) uses the Keycloak Service.
- The policies-shell bootstrap is told the environment (CELINE_KEYCLOAK_ENV) and, outside dev,
  gets its master-realm client's secret through a Secret; the render refuses a non-dev
  environment without one. The admin second factor is bootstrap's alone: the realm import has
  no flow, and `keycloak.admin_mfa` becomes CELINE_KEYCLOAK_ADMIN_MFA_REQUIRED.

    task test:keycloak-access

It needs `helmfile` and `helm`, and network access the first time (the keycloakx chart is an
OCI chart). No secrets: it renders over `envs/dev/values.yaml` plus an override, under the
helmfile environment `dev` or a staging-like one. The values are placeholders
(`example.org`), never a deployment's.
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
DOMAIN = "celine.test"  # envs/dev/values.yaml
REALM = "celine"
INTERNAL = "http://keycloak-keycloakx-http"
SECRET = "0123456789abcdef" * 4  # a placeholder of the required length, nothing real
SECRET_NAME = "policies-shell-bootstrap-client"


def render(helmfile: str, release: str, override: str = "", env: str = "dev") -> subprocess.CompletedProcess:
    """`helmfile template` one release over the dev values, as environment `env`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = (REPO / "helmfile.d" / helmfile).read_text()
        # one environment, the dev values plus the override, no secrets (not committed)
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.M)
        (tmp / "override.yaml").write_text(override or "{}\n")
        header = (f"environments:\n  {env}:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", env, "template", "-q",
             "-l", f"name={release}", "--skip-tests"],
            capture_output=True, text=True, env=environ, timeout=600,
        )


def docs_of(result: subprocess.CompletedProcess) -> list[dict]:
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def containers(docs: list[dict], kind: str = "Deployment") -> dict[str, dict]:
    out = {}
    for d in of_kind(docs, kind):
        spec = d["spec"]["template"]["spec"]
        for c in spec.get("initContainers", []) + spec["containers"]:
            out[c["name"]] = c
    return out


def env_of(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env") or []}


# ---------------------------------------------------------------------------
# the public Ingress and the admin console URL (release keycloak)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keycloak_dev() -> list[dict]:
    return docs_of(render("0020-auth.yaml.gotmpl", "keycloak"))


@pytest.fixture(scope="module")
def keycloak_staging() -> list[dict]:
    return docs_of(render("0020-auth.yaml.gotmpl", "keycloak", env="staging",
                          override="domain: example.org\nkeycloak:\n  admin_forward_port: 18443\n"))


def public_paths(docs: list[dict], host: str) -> dict[str, str]:
    ingresses = of_kind(docs, "Ingress")
    assert len(ingresses) == 1, [i["metadata"]["name"] for i in ingresses]  # no console/admin Ingress
    rules = ingresses[0]["spec"]["rules"]
    assert [r["host"] for r in rules] == [host]
    return {p["path"]: p["pathType"] for p in rules[0]["http"]["paths"]}


@pytest.mark.parametrize("which,host", [("dev", f"keycloak.{DOMAIN}"), ("staging", "keycloak.example.org")])
def test_the_public_host_serves_the_platform_realm_and_its_resources_only(which, host, keycloak_dev, keycloak_staging):
    docs = keycloak_dev if which == "dev" else keycloak_staging
    assert public_paths(docs, host) == {f"/realms/{REALM}": "Prefix", "/resources": "Prefix"}


@pytest.mark.parametrize("path", ["/", "/admin", "/admin/master/console/", "/admin/realms/celine",
                                  "/realms/master", "/realms/master/protocol/openid-connect/token",
                                  "/realms/celine2", "/metrics", "/health"])
def test_admin_master_and_everything_else_match_no_public_rule(path, keycloak_staging):
    # Kubernetes Prefix matching: whole path elements, so /realms/celine does not match /realms/celine2
    def matches(rule: str) -> bool:
        return path == rule or path.startswith(rule.rstrip("/") + "/")
    assert not any(matches(p) for p in public_paths(keycloak_staging, "keycloak.example.org")), path


@pytest.mark.parametrize("path", ["/realms/celine", "/realms/celine/protocol/openid-connect/auth",
                                  "/realms/celine/.well-known/openid-configuration", "/realms/celine/account",
                                  "/resources/abc12/login/rec/css/login.css"])
def test_what_sign_in_and_the_account_console_need_still_matches(path, keycloak_staging):
    assert any(path == p or path.startswith(p + "/") for p in public_paths(keycloak_staging, "keycloak.example.org"))


def test_more_public_realms_can_be_listed():
    docs = docs_of(render("0020-auth.yaml.gotmpl", "keycloak", override="keycloak:\n  public_realms: [celine, other]\n"))
    assert set(public_paths(docs, f"keycloak.{DOMAIN}")) == {"/realms/celine", "/realms/other", "/resources"}


def test_master_can_never_be_a_public_realm():
    result = render("0020-auth.yaml.gotmpl", "keycloak", override="keycloak:\n  public_realms: [celine, master]\n")
    assert result.returncode != 0
    assert "keycloak.public_realms must not name master" in result.stderr


def keycloak_env(docs: list[dict]) -> dict[str, dict]:
    (container,) = [c for c in containers(docs, "StatefulSet").values() if c["name"] == "keycloak"]
    return env_of(container)


def test_the_admin_console_answers_on_the_forwarded_localhost_url(keycloak_dev, keycloak_staging):
    assert keycloak_env(keycloak_dev)["KC_HOSTNAME_ADMIN"]["value"] == "http://localhost:18080"
    assert keycloak_env(keycloak_staging)["KC_HOSTNAME_ADMIN"]["value"] == "http://localhost:18443"


def test_the_public_hostname_is_unchanged(keycloak_staging):
    env = keycloak_env(keycloak_staging)
    assert env["KC_HOSTNAME"]["value"] == "https://keycloak.example.org"
    assert env["KC_HOSTNAME_STRICT"]["value"] == "false"


# ---------------------------------------------------------------------------
# the policies-shell bootstrap and sync (release policies-shell)
# ---------------------------------------------------------------------------


def policies_shell(override: str = "", env: str = "dev") -> subprocess.CompletedProcess:
    return render("0050-celine-services.yaml.gotmpl", "policies-shell", override=override, env=env)


@pytest.fixture(scope="module")
def shell_dev() -> list[dict]:
    return docs_of(policies_shell())


@pytest.fixture(scope="module")
def shell_staging() -> list[dict]:
    return docs_of(policies_shell(f"policies_shell:\n  bootstrap:\n    client_secret: {SECRET}\n", env="staging"))


def test_dev_is_told_it_is_dev_and_needs_no_bootstrap_client(shell_dev):
    for name in ("bootstrap", "shell"):
        env = env_of(containers(shell_dev)[name])
        assert env["CELINE_KEYCLOAK_ENV"]["value"] == "dev"
        assert "CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET" not in env
    assert not [s for s in of_kind(shell_dev, "Secret") if s["metadata"]["name"] == SECRET_NAME]


@pytest.mark.parametrize("name", ["bootstrap", "shell"])
def test_staging_is_production_and_both_containers_get_the_client_secret_by_reference(name, shell_staging):
    env = env_of(containers(shell_staging)[name])
    assert env["CELINE_KEYCLOAK_ENV"]["value"] == "prod"
    assert env["CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {"name": SECRET_NAME, "key": "secret"}
    # the master admin stays: the very first run creates the client with it
    assert env["CELINE_KEYCLOAK_ADMIN_USER"]["value"]
    assert env["CELINE_KEYCLOAK_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "keycloak-keycloakx-keycloak-admin-secret"


def test_the_client_secret_is_in_a_secret_and_nowhere_in_the_pod_spec(shell_staging):
    (secret,) = [s for s in of_kind(shell_staging, "Secret") if s["metadata"]["name"] == SECRET_NAME]
    assert secret["stringData"] == {"secret": SECRET}
    for d in of_kind(shell_staging, "Deployment") + of_kind(shell_staging, "ConfigMap"):
        assert SECRET not in yaml.safe_dump(d)


@pytest.mark.parametrize("override", ["{}\n", "policies_shell:\n  bootstrap:\n    client_secret: too-short\n"])
def test_a_non_dev_environment_without_a_strong_client_secret_does_not_render(override):
    result = policies_shell(override, env="staging")
    assert result.returncode != 0
    assert "policies_shell.bootstrap.client_secret is required outside dev" in result.stderr


def test_policies_shell_env_overrides_the_environment_name():
    docs = docs_of(policies_shell("policies_shell:\n  env: dev\n", env="staging"))
    assert env_of(containers(docs)["bootstrap"])["CELINE_KEYCLOAK_ENV"]["value"] == "dev"
    result = policies_shell("policies_shell:\n  env: prod\n")
    assert result.returncode != 0 and "client_secret is required outside dev" in result.stderr


@pytest.mark.parametrize("which", ["dev", "staging"])
def test_bootstrap_and_sync_reach_keycloak_inside_the_cluster(which, shell_dev, shell_staging):
    docs = shell_dev if which == "dev" else shell_staging
    for name in ("bootstrap", "shell"):
        url = env_of(containers(docs)[name])["CELINE_KEYCLOAK_BASE_URL"]["value"]
        assert url == INTERNAL, (name, url)
    # the bootstrap waits on /realms/master, which only the Service serves
    script = containers(docs)["bootstrap"]["command"][-1]
    assert "CELINE_KEYCLOAK_BASE_URL" in script and "/realms/master/" in script


def test_the_internal_url_can_be_overridden():
    docs = docs_of(policies_shell("keycloak:\n  internal_url: http://keycloak.auth.svc:8080\n"))
    assert env_of(containers(docs)["shell"])["CELINE_KEYCLOAK_BASE_URL"]["value"] == "http://keycloak.auth.svc:8080"


@pytest.mark.parametrize("value,expected", [
    (None, None), ("true", "true"), ("false", "false"), ("{enabled: true}", "true"), ("{enabled: false}", "false"),
])
def test_admin_mfa_is_handed_to_bootstrap_only_when_set(value, expected):
    override = f"keycloak:\n  admin_mfa: {value}\n" if value is not None else ""
    env = env_of(containers(docs_of(policies_shell(override)))["bootstrap"])
    if expected is None:
        assert "CELINE_KEYCLOAK_ADMIN_MFA_REQUIRED" not in env
    else:
        assert env["CELINE_KEYCLOAK_ADMIN_MFA_REQUIRED"]["value"] == expected


@pytest.mark.parametrize("value", ['"yes"', "{enabled: yes-please}", "[true]"])
def test_any_other_admin_mfa_value_stops_the_render(value):
    result = policies_shell(f"keycloak:\n  admin_mfa: {value}\n")
    assert result.returncode != 0
    assert "keycloak.admin_mfa must be true, false or {enabled: true|false}" in result.stderr


# ---------------------------------------------------------------------------
# provisioning, the other in-cluster administrator
# ---------------------------------------------------------------------------


def test_provisioning_reaches_keycloak_inside_the_cluster():
    docs = docs_of(render("0050-celine-services.yaml.gotmpl", "provisioning"))
    (container,) = containers(docs).values()
    assert env_of(container)["CELINE_KEYCLOAK_BASE_URL"]["value"] == INTERNAL


def test_nothing_else_administers_keycloak_through_the_public_host():
    # every CELINE_KEYCLOAK_BASE_URL / keycloak.baseUrl in the environment defaults
    hits = []
    for path in (REPO / "defaults").rglob("*.gotmpl"):
        text = path.read_text()
        for m in re.finditer(r"(CELINE_KEYCLOAK_BASE_URL[^\n]*\n[^\n]*|baseUrl:[^\n]*)", text):
            if "keycloak." in m.group(0) and "https://keycloak." in m.group(0):
                hits.append(f"{path.relative_to(REPO)}: {m.group(0)!r}")
    assert hits == []
