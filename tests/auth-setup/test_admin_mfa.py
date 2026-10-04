"""The realm import carries no admin second factor, whatever `keycloak.admin_mfa` says.

celine-policies `keycloak bootstrap` owns the admin second factor (its REQ-0015/REQ-0016,
ADR-0013/ADR-0014): outside dev it builds and binds the flow `browser-admin-second-factor` on
the realm role `platform-admin`, and the same flow on master's `admin`, on every policies-shell
start, and it corrects an older import's flow in place. An import reaches new realms only
(IGNORE_EXISTING), so it can never be the owner, and two owners disagreed before: the import
conditioned on a role bootstrap deletes. `keycloak.admin_mfa` is now handed to bootstrap as
CELINE_KEYCLOAK_ADMIN_MFA_REQUIRED (tests/keycloak-access).

This file renders the `auth-setup` release for each form `admin_mfa` can take and checks that
the import is the same for all of them. It needs `helmfile` and `helm`, and no secrets: it
builds a throwaway helmfile over `envs/dev/values.yaml` plus one override.

    task test:auth-setup
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

FORMS = ["true", "{enabled: true}", "false", "{enabled: false}", None]


def render(admin_mfa: str | None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        override = tmp / "override.yaml"
        override.write_text(f"keycloak:\n  admin_mfa: {admin_mfa}\n" if admin_mfa is not None else "{}\n")
        values = [REPO / "envs/dev/values.yaml", override]
        if admin_mfa is None:
            # the key absent altogether: drop it from a copy of the dev values
            dev = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
            dev.get("keycloak", {}).pop("admin_mfa", None)
            (tmp / "dev.yaml").write_text(yaml.safe_dump(dev))
            values = [tmp / "dev.yaml"]
        (tmp / "helmfile.yaml.gotmpl").write_text(
            "environments:\n  dev:\n    values:\n"
            + "".join(f"      - {v}\n" for v in values)
            + "---\nreleases:\n  - name: auth-setup\n    namespace: celine-dev\n"
            f"    chart: {REPO}/charts/auth-setup\n    values:\n"
            f"      - {REPO}/defaults/0020-auth/auth-setup/values.yaml.gotmpl\n"
        )
        env = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "dev", "template", "-q"],
            capture_output=True, text=True, env=env, timeout=180,
        )


def realm_of(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "ConfigMap" and "celine-realm.json" in (doc.get("data") or {}):
            return json.loads(doc["data"]["celine-realm.json"])
    raise AssertionError("no keycloak-realm-celine ConfigMap in the render")


@pytest.mark.parametrize("value", FORMS)
def test_the_import_binds_keycloaks_own_browser_flow_whatever_admin_mfa_says(value):
    realm = realm_of(render(value))
    assert realm["browserFlow"] == "browser"
    assert realm["authenticationFlows"] == []
    assert realm["authenticatorConfig"] == []


def test_the_import_no_longer_validates_admin_mfa():
    # the policies-shell release does (tests/keycloak-access); the import ignores the key
    assert render('"yes"').returncode == 0


def test_the_auth_setup_chart_has_no_admin_mfa_values():
    assert "adminMfa" not in (REPO / "charts/auth-setup/values.yaml").read_text()
    assert "adminMfa" not in (REPO / "defaults/0020-auth/auth-setup/values.yaml.gotmpl").read_text()
    assert "adminMfa" not in (REPO / "charts/auth-setup/files/celine-realm.json.gotmpl").read_text()
