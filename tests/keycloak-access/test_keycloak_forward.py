"""`task keycloak:forward`: where it sends you for the admin console.

- On the port-forward (KC_HOSTNAME_ADMIN http://localhost:<port>): master's console on that
  URL, after checking master's issuer, then the forward itself.
- With `keycloak.admin_public` (KC_HOSTNAME_ADMIN https://...): the public host, no forward.
  Without `admin_public.realms` that is master's console there; with `realms` the console
  Ingress serves only those realms' consoles, so the hint lists them and says master is
  administered by API and CLI only, never `/admin/master/console/`.

It runs the real task with a fake `kubectl` first on PATH: no cluster is touched.

    task test:keycloak-access
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NS = "celine-test"
PUBLIC = "https://keycloak.example.org"

FAKE_KUBECTL = r"""#!/bin/sh
# answers only what keycloak:forward asks; records every call
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "get statefulset"*) printf '%s' "$FAKE_URL" ;;
  "get ingress"*) [ -n "$FAKE_PATHS" ] || { echo 'Error from server (NotFound)' >&2; exit 1; }; printf '%s' "$FAKE_PATHS" ;;
  "get --raw"*) printf '{"issuer":"%s/realms/master"}' "$FAKE_URL" ;;
  "port-forward"*) echo "FORWARDING" ;;
  *) echo "unexpected kubectl $*" >&2; exit 2 ;;
esac
"""


def forward(tmp_path: Path, url: str, paths: str = "") -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(0o755)
    log = tmp_path / "kubectl.log"
    log.touch()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "KUBECONFIG": "/dev/null",
           "FAKE_URL": url, "FAKE_PATHS": paths, "FAKE_LOG": str(log)}
    result = subprocess.run([shutil.which("task") or "task", "-d", str(REPO), "keycloak:forward", f"NAMESPACE={NS}"],
                            capture_output=True, text=True, env=env, timeout=60, check=False)
    return result, log.read_text()


def test_on_the_port_forward_master_console_and_the_forward(tmp_path):
    result, calls = forward(tmp_path, "http://localhost:18080")
    assert result.returncode == 0, result.stderr
    assert "Admin console: http://localhost:18080/admin/master/console/" in result.stdout
    assert "FORWARDING" in result.stdout
    assert f"port-forward -n {NS} svc/keycloak-keycloakx-http 18080:http" in calls


def test_admin_public_without_realms_points_at_masters_console_on_the_public_host(tmp_path):
    result, calls = forward(tmp_path, PUBLIC, "/admin /realms/master")
    assert result.returncode == 0, result.stderr
    assert f"{PUBLIC}/admin/master/console/" in result.stdout
    assert "port-forward" not in calls


@pytest.mark.parametrize("paths,realms", [
    ("/admin/celine-staging/ /admin/realms/celine-staging /admin/realms/celine-staging/ /admin/serverinfo",
     ["celine-staging"]),
    ("/admin/a/ /admin/realms/a /admin/realms/a/ /admin/b/ /admin/realms/b /admin/realms/b/ /admin/serverinfo",
     ["a", "b"]),
])
def test_admin_public_with_realms_points_at_their_consoles_and_never_masters(tmp_path, paths, realms):
    result, calls = forward(tmp_path, PUBLIC, paths)
    assert result.returncode == 0, result.stderr
    assert "/admin/master/console/" not in result.stdout
    for realm in realms:
        assert f"{PUBLIC}/admin/{realm}/console/" in result.stdout
    assert "/admin/serverinfo/console/" not in result.stdout and "/admin/realms/console/" not in result.stdout
    assert "master is administered by API and CLI only" in result.stdout
    assert "port-forward" not in calls


def test_admin_public_without_a_readable_console_ingress_keeps_todays_hint(tmp_path):
    result, _ = forward(tmp_path, PUBLIC, "")
    assert result.returncode == 0, result.stderr
    assert f"{PUBLIC}/admin/master/console/" in result.stdout
