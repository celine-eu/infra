"""The policies-shell for a realm that is not the services' platform realm.

- `syncBase` ({name, content}): the deployment's own base declaration, mounted from a
  ConfigMap and synced instead of the image's clients.yaml; a change restarts the shell.
- `keycloak.adminPassword`: the master administrator's password rendered as a Secret of the
  chart's own, for a Keycloak that runs in another namespace or cluster.

Both empty: the render is as before (clients.yaml from the image, `existingSecret`).

    uv run --no-project --with pytest --with pyyaml pytest tests/policies-shell
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/celine-policies-shell"
BASE = "realm: example\nclients:\n  - client_id: oauth2_proxy\n"


def render(*sets: str, files: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    args = ["helm", "template", "policies-shell", str(CHART), "--set", "bootstrap.enabled=false"]
    for s in sets:
        args += ["--set", s]
    for k, v in (files or {}).items():
        args += ["--set-string", f"{k}={v}"]
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def docs(result: subprocess.CompletedProcess) -> list[dict]:
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def deployment(ds: list[dict]) -> dict:
    return next(d for d in ds if d["kind"] == "Deployment")


def shell(ds: list[dict]) -> dict:
    return next(c for c in deployment(ds)["spec"]["template"]["spec"]["containers"] if c["name"] == "shell")


def test_without_a_base_the_image_declaration_is_synced():
    ds = docs(render())
    assert 'CLIENTS_FILE="/app/clients.yaml"' in shell(ds)["command"][2]
    assert not [d for d in ds if d["metadata"]["name"].endswith("-sync-base")]


def test_a_base_is_mounted_and_synced_instead():
    ds = docs(render("syncBase.name=clients.yaml", files={"syncBase.content": BASE}))
    cm = next(d for d in ds if d["metadata"]["name"] == "policies-shell-sync-base")
    assert cm["data"]["clients.yaml"] == BASE
    sh = shell(ds)
    assert 'CLIENTS_FILE="/etc/celine-policies-sync-base/clients.yaml"' in sh["command"][2]
    assert {"name": "sync-base", "mountPath": "/etc/celine-policies-sync-base", "readOnly": True} in sh["volumeMounts"]
    pod = deployment(ds)["spec"]["template"]
    assert "checksum/sync-base" in pod["metadata"]["annotations"]
    assert {"name": "sync-base", "configMap": {"name": "policies-shell-sync-base"}} in pod["spec"]["volumes"]


@pytest.mark.parametrize("bad, message", [
    ({"syncBase.name": "../x"}, "is not a file name"),
    ({"syncBase.name": "clients.yaml"}, "is empty"),
])
def test_a_wrong_base_stops_the_render(bad, message):
    result = render(*[f"{k}={v}" for k, v in bad.items()])
    assert result.returncode != 0 and message in result.stderr


def test_a_base_and_a_clients_file_together_stop_the_render():
    result = render("syncBase.name=clients.yaml", "clientsFile=/tmp/x.yaml", files={"syncBase.content": BASE})
    assert result.returncode != 0 and "set one" in result.stderr


def test_without_a_password_the_existing_secret_is_read():
    ds = docs(render())
    env = {e["name"]: e for e in shell(ds)["env"]}
    assert env["CELINE_KEYCLOAK_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "keycloak-admin-credentials"
    assert not [d for d in ds if d["kind"] == "Secret" and d["metadata"]["name"] == "policies-shell-keycloak-admin"]


def test_a_password_becomes_a_secret_of_its_own():
    ds = docs(render(files={"keycloak.adminPassword": "s3cret"}))
    secret = next(d for d in ds if d["kind"] == "Secret" and d["metadata"]["name"] == "policies-shell-keycloak-admin")
    assert secret["stringData"] == {"password": "s3cret"}
    env = {e["name"]: e for e in shell(ds)["env"]}
    ref = env["CELINE_KEYCLOAK_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "policies-shell-keycloak-admin", "key": "password"}
    assert "s3cret" not in yaml.safe_dump(deployment(ds))


# ---------------------------------------------------------------------------
# bootstrap.platformAdminPasswordTemplate (celine-policies REQ-0022)
# ---------------------------------------------------------------------------


def render_bootstrap(values: str = "") -> subprocess.CompletedProcess:
    """Values as YAML: helm's --set reads `{…}` as a list."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        f.write(values or "{}\n")
        f.flush()
        return subprocess.run(["helm", "template", "policies-shell", str(CHART), "-f", f.name],
                              capture_output=True, text=True, timeout=60)


def containers(ds: list[dict]) -> dict[str, dict]:
    spec = deployment(ds)["spec"]["template"]["spec"]
    return {c["name"]: c for c in spec.get("initContainers", []) + spec["containers"]}


def test_without_passwords_no_secret_and_no_env():
    ds = docs(render_bootstrap())
    assert not [d for d in ds if d["metadata"]["name"].endswith("-bootstrap-platform-admins")]
    assert "envFrom" not in containers(ds)["bootstrap"]


def test_passwords_reach_the_bootstrap_container_only():
    ds = docs(render_bootstrap(
        "bootstrap:\n  platformAdminPasswordTemplate: '{username}.x'\n"
    ))
    secret = next(d for d in ds if d["metadata"]["name"] == "policies-shell-bootstrap-platform-admins")
    # Outside CELINE_KEYCLOAK_PLATFORM_*, which celine-policies reads as realm-setting overrides.
    assert secret["stringData"] == {"CELINE_KEYCLOAK_ADMIN_TEMP_PASSWORD_TEMPLATE": "{username}.x"}
    cs = containers(ds)
    assert cs["bootstrap"]["envFrom"] == [{"secretRef": {"name": "policies-shell-bootstrap-platform-admins"}}]
    assert "envFrom" not in cs["shell"] and "envFrom" not in cs["bootstrap-record"]


def test_a_template_without_the_username_stops_the_render():
    result = render_bootstrap("bootstrap:\n  platformAdminPasswordTemplate: same\n")
    assert result.returncode != 0 and "{username}" in result.stderr
