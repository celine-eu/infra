"""An intermediate environment (staging) mirrors prod's members on a schedule (release
`member-mirror`, group `celine-services`), rendered through helmfile.

One CronJob, one pod, two halves in sequence (store plan
`a-prod-member-can-try-staging-under-a-pseudonym`, C3):

1. init container `keycloak`: `celine-policies keycloak mirror-members` (celine-policies
   REQ-0021) writes this realm's mirror accounts, as the realm's `celine-admin-cli`. It exits
   0 ok, 1 refused or failed, 2 partial (some members skipped). The wrapper turns 2 into
   success, so a skipped member does not block the registry half; 1 still fails the pod.
2. container `registry`: `celine-rec-registry mirror-members --apply` (rec-registry
   REQ-0104) writes this registry's mirror members. 0 ok, 1 a write failed, 2 refused.

Both read prod's `GET /admin/export/mirror` through prod's API gateway as prod's
`svc-member-mirror` (`prod_reads.domain`). Off unless `member_mirror.enabled`; never in prod,
whatever the values say. Every secret comes from the release's Secret by `secretKeyRef`.

    task test:member-mirror

It needs `helmfile`, `helm` and `sh`, no secrets: it renders over `envs/dev/values.yaml` plus
an override carrying throwaway secrets.
"""

from __future__ import annotations

import base64
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HELMFILE = "0050-celine-services.yaml.gotmpl"
RELEASE = "member-mirror"

SOURCE_SECRET = "source-secret-0123456789abcdef"
KC_ADMIN_SECRET = "kc-admin-secret-0123456789abcdef"
CLI_SECRET = "cli-secret-0123456789abcdef"

BASE = f"""domain: staging.example.org
prod_reads:
  domain: prod.example.org
policies_shell:
  image_tag: v9.9.9
rec_registry:
  image_tag: v1.9.0
auth_setup:
  clientCLISecret: {CLI_SECRET}
"""

ON = BASE + f"""member_mirror:
  enabled: true
  source_client_secret: {SOURCE_SECRET}
  keycloak_admin_client_secret: {KC_ADMIN_SECRET}
"""


def _state(tmp: Path, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    (tmp / "override.yaml").write_text(override)
    source = (REPO / "helmfile.d" / HELMFILE).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def run(override: str, env: str = "staging", release: str = RELEASE) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", "--skip-deps",
             "-l", f"name={release}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


def render(override: str, env: str = "staging", release: str = RELEASE) -> dict[str, dict]:
    """Render one release; return its objects by `<kind>/<name>`."""
    result = run(override, env, release)
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def pod(objects: dict[str, dict]) -> dict:
    return objects["CronJob/member-mirror"]["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def keycloak(objects: dict[str, dict]) -> dict:
    (container,) = pod(objects)["initContainers"]
    assert container["name"] == "keycloak"
    return container


def registry(objects: dict[str, dict]) -> dict:
    (container,) = pod(objects)["containers"]
    assert container["name"] == "registry"
    return container


def env_of(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container["env"]}


def secret_data(objects: dict[str, dict]) -> dict[str, str]:
    secret = objects["Secret/member-mirror"]
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


@pytest.fixture(scope="module")
def on() -> dict[str, dict]:
    return render(ON + "  impersonators: [alice, bob]\n")


# --- where it renders --------------------------------------------------------------------


def test_off_by_default():
    assert render(BASE) == {}


def test_never_in_prod_even_when_enabled():
    assert render(ON, env="prod") == {}


def test_enabled_renders_one_cronjob_and_its_secret(on):
    assert set(on) == {"CronJob/member-mirror", "Secret/member-mirror"}


def test_a_prod_posture_signal_stops_the_render():
    result = run(ON + "celine_env: production\n")
    assert result.returncode != 0
    assert "member_mirror" in result.stderr


def test_without_prod_domain_the_render_stops():
    result = run(ON.replace("prod_reads:\n  domain: prod.example.org\n", ""))
    assert result.returncode != 0
    assert "prod_reads.domain" in result.stderr


@pytest.mark.parametrize("key", ["source_client_secret", "keycloak_admin_client_secret"])
def test_a_missing_secret_stops_the_render(key):
    override = "\n".join(line for line in ON.splitlines() if not line.strip().startswith(key)) + "\n"
    result = run(override)
    assert result.returncode != 0
    assert f"member_mirror.{key}" in result.stderr


# --- the schedule ------------------------------------------------------------------------


def test_every_quarter_hour_one_run_at_a_time(on):
    spec = on["CronJob/member-mirror"]["spec"]
    assert spec["schedule"] == "*/15 * * * *"
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["successfulJobsHistoryLimit"] == 1
    assert spec["failedJobsHistoryLimit"] == 3
    assert spec.get("suspend", False) is False
    job = spec["jobTemplate"]["spec"]
    assert job["backoffLimit"] == 0
    assert job["activeDeadlineSeconds"] > 0
    assert pod(on)["restartPolicy"] == "Never"


def test_schedule_and_suspend_come_from_values():
    spec = render(ON + "  schedule: '7 * * * *'\n  suspend: true\n")["CronJob/member-mirror"]["spec"]
    assert spec["schedule"] == "7 * * * *"
    assert spec["suspend"] is True


# --- the Keycloak half -------------------------------------------------------------------


def test_keycloak_half_runs_the_policies_image_of_the_environment(on):
    assert keycloak(on)["image"] == "ghcr.io/celine-eu/celine-policies:v9.9.9"


def test_keycloak_half_environment(on):
    env = env_of(keycloak(on))
    assert env["ENV"]["value"] == "staging"
    assert env["CELINE_KEYCLOAK_BASE_URL"]["value"] == "http://keycloak-keycloakx-http"
    assert env["CELINE_KEYCLOAK_REALM"]["value"] == "celine"
    assert env["CELINE_KEYCLOAK_ADMIN_CLIENT_ID"]["value"] == "celine-admin-cli"
    assert env["CELINE_MEMBER_MIRROR_SOURCE_URL"]["value"] == "https://api.prod.example.org/rec-registry"
    assert env["CELINE_MEMBER_MIRROR_SOURCE_ISSUER"]["value"] == "https://keycloak.prod.example.org/realms/celine"
    assert env["CELINE_MEMBER_MIRROR_SOURCE_CLIENT_ID"]["value"] == "svc-member-mirror"
    assert env["CELINE_MEMBER_MIRROR_IMPERSONATORS"]["value"] == "alice bob"
    assert env["CELINE_KEYCLOAK_ADMIN_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "member-mirror", "key": "keycloak-admin-client-secret"}
    assert env["CELINE_MEMBER_MIRROR_SOURCE_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "member-mirror", "key": "source-client-secret"}


def test_no_impersonators_leaves_the_variable_unset():
    assert "CELINE_MEMBER_MIRROR_IMPERSONATORS" not in env_of(keycloak(render(ON)))


def test_keycloak_half_applies_unless_dry_run(on):
    script = keycloak(on)["command"][-1]
    assert "celine-policies keycloak mirror-members" in script
    assert "--dry-run" not in script
    dry = keycloak(render(ON + "  dry_run: true\n"))["command"][-1]
    assert "celine-policies keycloak mirror-members --dry-run" in dry


# --- the registry half -------------------------------------------------------------------


def test_registry_half_runs_the_registry_image_of_the_environment(on):
    assert registry(on)["image"] == "ghcr.io/celine-eu/rec-registry:v1.9.0"


def test_registry_half_environment(on):
    env = env_of(registry(on))
    assert env["CELINE_ENV"]["value"] == "staging"
    assert env["SOURCE_REGISTRY_API_URL"]["value"] == "https://api.prod.example.org/rec-registry"
    assert env["SOURCE_REGISTRY_AUTH_URL"]["value"] == "https://keycloak.prod.example.org/realms/celine"
    assert env["SOURCE_REGISTRY_CLIENT_ID"]["value"] == "svc-member-mirror"
    assert env["REGISTRY_API_URL"]["value"] == "http://rec-registry:8004"
    assert env["REGISTRY_AUTH_URL"]["value"] == "https://keycloak.staging.example.org/realms/celine"
    assert env["REGISTRY_CLIENT_ID"]["value"] == "celine-cli"
    assert env["SOURCE_REGISTRY_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "member-mirror", "key": "source-client-secret"}
    assert env["REGISTRY_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "member-mirror", "key": "registry-client-secret"}


def test_registry_half_applies_unless_dry_run(on):
    assert registry(on)["command"] == ["celine-rec-registry", "mirror-members", "--apply"]
    dry = registry(render(ON + "  dry_run: true\n"))
    assert dry["command"] == ["celine-rec-registry", "mirror-members"]


def test_registry_client_and_secret_can_be_named():
    objects = render(ON + "  registry_client_id: svc-mirror-writer\n"
                          "  registry_client_secret: writer-secret-0123456789\n")
    assert env_of(registry(objects))["REGISTRY_CLIENT_ID"]["value"] == "svc-mirror-writer"
    assert secret_data(objects)["registry-client-secret"] == "writer-secret-0123456789"


def test_another_registry_client_without_its_secret_stops_the_render():
    result = run(ON + "  registry_client_id: svc-mirror-writer\n")
    assert result.returncode != 0
    assert "member_mirror.registry_client_secret" in result.stderr


# --- secrets -----------------------------------------------------------------------------


def test_secrets_live_in_the_secret(on):
    assert secret_data(on) == {
        "source-client-secret": SOURCE_SECRET,
        "keycloak-admin-client-secret": KC_ADMIN_SECRET,
        # celine-cli's: auth_setup.clientCLISecret, as rec-registry-shell.
        "registry-client-secret": CLI_SECRET,
    }


def test_no_secret_value_in_the_pod_spec(on):
    text = yaml.safe_dump(on["CronJob/member-mirror"])
    for value in (SOURCE_SECRET, KC_ADMIN_SECRET, CLI_SECRET):
        assert value not in text
    for container in (keycloak(on), registry(on)):
        for e in container["env"]:
            if "SECRET" in e["name"] or "TOKEN" in e["name"] or "PASSWORD" in e["name"]:
                assert "value" not in e, e["name"]
                assert e["valueFrom"]["secretKeyRef"]["name"] == "member-mirror"


# --- the wrapper: a partial Keycloak half does not block the registry half ----------------


def _run_wrapper(script: str, exit_code: int, tmp: Path) -> int:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "celine-policies"
    fake.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    environ = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    return subprocess.run(["/bin/sh", "-c", script], env=environ, capture_output=True,
                          timeout=30, check=False).returncode


@pytest.mark.parametrize("cli_exit,pod_exit", [(0, 0), (1, 1), (2, 0), (3, 3)])
def test_wrapper_turns_partial_into_success_and_keeps_failures(on, tmp_path, cli_exit, pod_exit):
    command = keycloak(on)["command"]
    assert command[:2] == ["/bin/sh", "-c"]
    assert _run_wrapper(command[-1], cli_exit, tmp_path) == pod_exit


# --- pod -------------------------------------------------------------------------------


def test_runs_unprivileged_with_resources(on):
    spec = pod(on)
    assert spec["securityContext"]["runAsNonRoot"] is True
    for container in (keycloak(on), registry(on)):
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert container["resources"]["requests"] and container["resources"]["limits"]


def test_images_pinned_by_digest_where_listed():
    digest_p = "sha256:" + "a" * 64
    digest_r = "sha256:" + "b" * 64
    objects = render(ON + "image_digests:\n"
                          f"  ghcr.io/celine-eu/celine-policies: {{v9.9.9: '{digest_p}'}}\n"
                          f"  ghcr.io/celine-eu/rec-registry: {{v1.9.0: '{digest_r}'}}\n")
    assert keycloak(objects)["image"] == f"ghcr.io/celine-eu/celine-policies:v9.9.9@{digest_p}"
    assert registry(objects)["image"] == f"ghcr.io/celine-eu/rec-registry:v1.9.0@{digest_r}"


# --- the policies shell carries this realm's svc-member-mirror secret ---------------------

POLICIES_SHELL = """policies_shell:
  image_tag: v9.9.9
  bootstrap:
    client_secret: 0123456789abcdef0123456789abcdef
"""


def _shell_env(override: str, env: str) -> dict[str, dict]:
    objects = render(override, env=env, release="policies-shell")
    (shell,) = [c for c in objects["Deployment/policies-shell"]["spec"]["template"]["spec"]["containers"]
                if c["name"] == "shell"]
    return env_of(shell)


def test_policies_shell_gets_the_member_mirror_client_secret():
    env = _shell_env(POLICIES_SHELL + "member_mirror:\n  client_secret: realm-own-secret-0123\n", "prod")
    assert env["SVC_MEMBER_MIRROR_SECRET"]["value"] == "realm-own-secret-0123"


def test_policies_shell_outside_dev_has_no_guessable_default():
    assert "SVC_MEMBER_MIRROR_SECRET" not in _shell_env(POLICIES_SHELL, "prod")


def test_policies_shell_in_dev_takes_the_dev_default():
    env = _shell_env(POLICIES_SHELL, "dev")
    assert env["SVC_MEMBER_MIRROR_SECRET"]["value"] == "svc-member-mirror"


# --- the chart refuses prod on its own, whatever renders it -------------------------------


@pytest.mark.parametrize("env", ["prod", "Production", ""])
def test_the_chart_alone_refuses_a_prod_or_missing_env(env):
    result = subprocess.run(
        ["helm", "template", "member-mirror", str(REPO / "charts" / "celine-member-mirror"),
         "--set", "enabled=true", "--set-string", f"env={env}",
         "--set", "source.url=https://api.x/rec-registry", "--set", "source.issuer=https://keycloak.x/realms/celine",
         "--set", "registry.authUrl=https://keycloak.y/realms/celine",
         "--set", "secrets.sourceClientSecret=a", "--set", "secrets.keycloakAdminClientSecret=b",
         "--set", "secrets.registryClientSecret=c"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode != 0
    assert "celine-member-mirror: env" in result.stderr


def test_the_chart_alone_renders_nothing_by_default():
    result = subprocess.run(
        ["helm", "template", "member-mirror", str(REPO / "charts" / "celine-member-mirror")],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [d for d in yaml.safe_load_all(result.stdout) if d] == []
