"""The shell's client secrets are a Secret, not values in the pod spec (`secretEnv`).

The start-up sync substitutes SVC_<ID>_SECRET and the like into clients.yaml. They used to be
plain `extraEnv` values on the Deployment, so `kubectl get deploy -o yaml` and every helm diff
printed them. Now the chart renders `secretEnv` into the Secret `<fullname>-env`, the shell
loads it with envFrom, and a pod annotation carries its checksum so a changed value still
restarts the shell (as a changed env value did).

    uv run --no-project --with pytest --with pyyaml pytest tests/policies-shell
"""

from __future__ import annotations

import subprocess

from test_realm_scoped_shell import CHART, container, render

SECRET = "a-client-secret-0123456789abcdef"


def env_secret(ds: list[dict]) -> dict:
    return next(d for d in ds if d["kind"] == "Secret" and d["metadata"]["name"] == "policies-shell-env")


def test_no_client_secret_is_a_value_in_the_pod_spec():
    ds = render(f"secretEnv.SVC_DT_SECRET={SECRET}")
    spec = next(d for d in ds if d["kind"] == "Deployment")
    assert SECRET not in str(spec)
    assert not {e["name"] for e in container(ds, "shell").get("env", []) if e["name"].endswith("_SECRET")
                and "value" in e}


def test_the_secrets_are_the_charts_secret_loaded_by_the_shell():
    ds = render(f"secretEnv.SVC_DT_SECRET={SECRET}")
    assert env_secret(ds)["stringData"]["SVC_DT_SECRET"] == SECRET
    assert {"secretRef": {"name": "policies-shell-env"}} in container(ds, "shell")["envFrom"]


def test_a_changed_secret_restarts_the_shell():
    def checksum(v: str) -> str:
        ds = render(f"secretEnv.SVC_DT_SECRET={v}")
        return next(d for d in ds if d["kind"] == "Deployment")["spec"]["template"]["metadata"][
            "annotations"]["checksum/secret-env"]
    assert checksum("one") != checksum("two")


def test_without_secret_env_there_is_no_secret_and_no_env_from():
    ds = render("secretEnv=null")
    assert not [d for d in ds if d["kind"] == "Secret" and d["metadata"]["name"] == "policies-shell-env"]
    assert "envFrom" not in container(ds, "shell")


def test_a_name_that_is_no_variable_stops_the_render():
    r = subprocess.run(["helm", "template", "policies-shell", str(CHART), "--set", "keycloak.bootstrapClient.secret=x",
                        "--set", "keycloak.adminPassword=y", "--set-string", "secretEnv.not-a-name=v"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "not an environment variable name" in r.stderr
