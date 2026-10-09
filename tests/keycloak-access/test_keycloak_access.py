"""Who reaches Keycloak, and through which door, rendered through helmfile.

- The public host serves the platform realm only: its Ingress lists `/realms/<realm>` and
  `/resources`, so `/admin` (admin console and admin REST API), `/realms/master`, any other
  realm and `/` match no rule.
- The master realm and the admin console are reached through `kubectl port-forward` on
  http://localhost:<keycloak.admin_forward_port>: KC_HOSTNAME_ADMIN says so, KC_HOSTNAME stays
  the public URL. There is no admin Ingress.
- What administers Keycloak from inside the cluster (the policies-shell bootstrap and sync,
  provisioning) uses the Keycloak Service.
- The admin console signs in to master through master's own URL, so the policies-shell
  bootstrap sets master's Frontend URL to that same localhost URL on every start, as the
  bootstrap client, merging it into master's attributes and writing only when it differs.
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

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# master's Frontend URL (release policies-shell, init container bootstrap)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("override,url", [("", "http://localhost:18080"),
                                          ("keycloak:\n  admin_forward_port: 18443\n", "http://localhost:18443")])
def test_master_frontend_url_derives_from_admin_forward_port_like_kc_hostname_admin(override, url):
    keycloak = keycloak_env(docs_of(render("0020-auth.yaml.gotmpl", "keycloak", override=override)))
    shell = containers(docs_of(policies_shell(override)))
    assert env_of(shell["bootstrap"])["MASTER_FRONTEND_URL"]["value"] == url
    assert keycloak["KC_HOSTNAME_ADMIN"]["value"] == url
    assert "MASTER_FRONTEND_URL" not in env_of(shell["shell"])


def test_staging_sets_master_frontend_url_as_the_bootstrap_client_and_stores_the_result():
    override = f"keycloak:\n  admin_forward_port: 18443\npolicies_shell:\n  bootstrap:\n    client_secret: {SECRET}\n"
    docs = docs_of(policies_shell(override, env="staging"))
    shell = containers(docs)
    env = env_of(shell["bootstrap"])
    assert env["MASTER_FRONTEND_URL"]["value"] == "http://localhost:18443"
    assert env["CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]["name"] == SECRET_NAME
    assert "frontend-url.txt" in shell["bootstrap-record"]["command"][-1]
    for d in of_kind(docs, "Deployment"):
        assert SECRET not in yaml.safe_dump(d)


def bootstrap_script(docs: list[dict]) -> str:
    return containers(docs)["bootstrap"]["command"][-1]


def frontend_url_step(docs: list[dict]) -> str:
    """The Python the bootstrap script feeds to `python -` for master's Frontend URL."""
    match = re.search(r"<<'PY'[^\n]*\n(.*?)\nPY\n", bootstrap_script(docs), flags=re.S)
    assert match, "no frontendUrl step in the bootstrap script"
    return match.group(1)


def test_the_bootstrap_script_is_valid_sh(shell_dev):
    result = subprocess.run(["sh", "-n"], input=bootstrap_script(shell_dev), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class FakeMaster:
    """Master as the step sees it: token, realm read/write, discovery. Records every call."""

    PUBLIC_ISSUER = "https://keycloak.example.org/realms/master"

    def __init__(self, attributes: dict, secret: str):
        self.attributes = dict(attributes)
        self.secret = secret
        self.calls: list[tuple[str, str, object]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status: int, body: object = None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self) -> bool:
                return self.headers.get("Authorization") == "Bearer t0ken"

            def body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_POST(self):
                form = self.body().decode()
                fake.calls.append(("POST", self.path, form))
                if self.path == "/realms/master/protocol/openid-connect/token" and f"client_secret={fake.secret}" in form:
                    self.reply(200, {"access_token": "t0ken"})
                else:
                    self.reply(401, {"error": "unauthorized_client"})

            def do_GET(self):
                fake.calls.append(("GET", self.path, None))
                if self.path == "/realms/master/.well-known/openid-configuration":
                    front = fake.attributes.get("frontendUrl")
                    self.reply(200, {"issuer": f"{front}/realms/master" if front else fake.PUBLIC_ISSUER})
                elif self.path == "/admin/realms/master" and self.authorized():
                    self.reply(200, {"realm": "master", "attributes": dict(fake.attributes)})
                else:
                    self.reply(403)

            def do_PUT(self):
                rep = json.loads(self.body())
                fake.calls.append(("PUT", self.path, rep))
                if self.path == "/admin/realms/master" and self.authorized():
                    fake.attributes = rep["attributes"]  # Keycloak replaces the map whole
                    self.reply(204)
                else:
                    self.reply(403)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def run(self, step: str, url: str, secret: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", ""), "MASTER_FRONTEND_URL": url, "CELINE_KEYCLOAK_BASE_URL": self.url}
        if secret:
            env["CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET"] = secret
        return subprocess.run([sys.executable, "-"], input=step, capture_output=True, text=True, env=env, timeout=60)

    def puts(self) -> list:
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture
def fake_master():
    masters = []

    def make(attributes: dict, secret: str = SECRET) -> FakeMaster:
        masters.append(FakeMaster(attributes, secret))
        return masters[-1]

    yield make
    for m in masters:
        m.server.shutdown()


def test_the_step_sets_frontend_url_and_keeps_masters_other_attributes(shell_dev, fake_master):
    master = fake_master({"cibaInterval": "5", "frontendUrl": "https://keycloak.example.org"})
    result = master.run(frontend_url_step(shell_dev), "http://localhost:18080", SECRET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert master.attributes == {"cibaInterval": "5", "frontendUrl": "http://localhost:18080"}
    assert "https://keycloak.example.org -> http://localhost:18080" in result.stdout
    assert SECRET not in result.stdout + result.stderr and "t0ken" not in result.stdout + result.stderr
    # signed in as the bootstrap client, never as the master admin user
    (token,) = [c for c in master.calls if c[0] == "POST"]
    assert "grant_type=client_credentials" in token[2] and "client_id=svc-celine-policies-bootstrap" in token[2]
    assert "password" not in token[2]


def test_the_step_writes_nothing_when_frontend_url_is_already_right(shell_dev, fake_master):
    master = fake_master({"frontendUrl": "http://localhost:18080"})
    result = master.run(frontend_url_step(shell_dev), "http://localhost:18080", SECRET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no change" in result.stdout and master.puts() == []


def test_without_the_bootstrap_client_secret_master_is_left_alone(shell_dev, fake_master):
    master = fake_master({})
    result = master.run(frontend_url_step(shell_dev), "http://localhost:18080", "")
    assert result.returncode == 0 and "left alone" in result.stdout
    assert master.calls == []


def test_a_rejected_client_fails_the_step_without_printing_the_secret(shell_dev, fake_master):
    secret = "f" * 64  # not the secret the fake master holds
    master = fake_master({})
    result = master.run(frontend_url_step(shell_dev), "http://localhost:18080", secret)
    assert result.returncode == 1 and "HTTP 401" in result.stdout
    assert secret not in result.stdout + result.stderr and master.puts() == []


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
