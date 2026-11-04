"""The shell without the master realm's credentials (`shell.masterCredentials: false`).

The bootstrap init container always gets master's credentials (the bootstrap client's secret,
the master administrator's password): it converges the realm and master. The long-running
shell, the container people exec into, gets them only while `shell.masterCredentials` is on
(the default, so an existing release renders as before). Off, bootstrap records the realm's own
admin client (celine-admin-cli) in a memory volume both containers mount, and the shell's sync
signs in as it: an exec into the shell reaches that realm, not master and not other realms.

    uv run --no-project --with pytest --with pyyaml pytest tests/policies-shell
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/celine-policies-shell"
MASTER = {"CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET", "CELINE_KEYCLOAK_ADMIN_PASSWORD", "CELINE_KEYCLOAK_ADMIN_USER"}
SECRETS = "/run/celine-policies-secrets"


def render(*sets: str) -> list[dict]:
    args = ["helm", "template", "policies-shell", str(CHART),
            "--set", "keycloak.bootstrapClient.secret=x", "--set", "keycloak.adminPassword=y"]
    for s in sets:
        args += ["--set", s]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def pod(ds: list[dict]) -> dict:
    return next(d for d in ds if d["kind"] == "Deployment")["spec"]["template"]["spec"]


def container(ds: list[dict], name: str) -> dict:
    p = pod(ds)
    return next(c for c in p["containers"] + p.get("initContainers", []) if c["name"] == name)


def env_names(c: dict) -> set[str]:
    return {e["name"] for e in c.get("env", [])}


def mounts(c: dict) -> set[str]:
    return {m["mountPath"] for m in c.get("volumeMounts", [])}


def test_by_default_the_shell_holds_master_as_before():
    ds = render()
    assert MASTER <= env_names(container(ds, "shell"))
    assert "SECRET_FILE=/tmp/.secrets.yaml" in container(ds, "shell")["command"][2]
    assert SECRETS not in mounts(container(ds, "shell"))


def test_off_the_shell_holds_no_master_credential():
    ds = render("shell.masterCredentials=false")
    assert not MASTER & env_names(container(ds, "shell"))
    assert MASTER <= env_names(container(ds, "bootstrap"))


def test_off_bootstrap_records_the_realm_admin_client_where_the_shell_reads_it():
    ds = render("shell.masterCredentials=false")
    boot, sh = container(ds, "bootstrap"), container(ds, "shell")
    assert f"--secrets-file {SECRETS}/secrets.yaml" in boot["command"][2]
    assert f"SECRET_FILE={SECRETS}/secrets.yaml" in sh["command"][2]
    assert SECRETS in mounts(boot) and SECRETS in mounts(sh)
    assert SECRETS not in mounts(container(ds, "bootstrap-record"))
    vol = next(v for v in pod(ds)["volumes"] if v["name"] == "realm-admin-secret")
    assert vol["emptyDir"]["medium"] == "Memory"


def test_off_a_realm_admin_client_secret_from_an_existing_secret_still_reaches_the_shell():
    """celine-admin-cli's secret is realm-scoped: not a master credential."""
    ds = render("shell.masterCredentials=false", "keycloak.adminPassword=", "keycloak.adminClientSecretKey=cli")
    assert "CELINE_KEYCLOAK_ADMIN_CLIENT_SECRET" in env_names(container(ds, "shell"))
    assert "CELINE_KEYCLOAK_ADMIN_PASSWORD" not in env_names(container(ds, "shell"))


def test_without_bootstrap_there_is_no_volume_to_share():
    ds = render("shell.masterCredentials=false", "bootstrap.enabled=false")
    sh = container(ds, "shell")
    assert not MASTER & env_names(sh)
    assert "SECRET_FILE=/tmp/.secrets.yaml" in sh["command"][2]
    assert not [v for v in pod(ds).get("volumes", []) if v["name"] == "realm-admin-secret"]
