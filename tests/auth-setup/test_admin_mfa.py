"""The realm's admin second factor (`keycloak.admin_mfa`), rendered through helmfile.

This repository has no test suite: rendering is the test (see the verifying-a-change
playbook). This file renders the `auth-setup` release for each form `admin_mfa` can take
and checks what Keycloak would be handed. It needs `helmfile` and `helm`, and no secrets:
it builds a throwaway helmfile over `envs/dev/values.yaml` plus one override.

    task test:auth-setup                      # render checks
    KEYCLOAK_URL=http://127.0.0.1:8081 \\
      task test:auth-setup                    # also import each render into that Keycloak

Why it exists, measured on Keycloak 26.7.3:
- `admin_mfa: true` used to render as *disabled*, without a word;
- the flow named provider ids that do not exist (`condition-user-role`). The import accepts
  them with a WARN, and then every browser sign-in fails, for every user;
- OTP and recovery codes as bare ALTERNATIVEs refuse an admin who has neither as
  "invalid username or password", so the flow needs its enrolment branch.

`keycloak-26.7.3-authenticators.json` is every authenticator id the server lists, with the
config keys each accepts (`GET /admin/realms/{realm}/authentication/authenticator-providers`
and `.../config-description/{id}`). Regenerate it when the Keycloak version changes.

The KEYCLOAK_URL layer is for a throwaway Keycloak only (master admin admin/admin): it
creates and deletes realms named `admin-mfa-check-*`. Never point it at a deployment.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
AUTHENTICATORS: dict[str, list[str] | None] = json.loads(
    (Path(__file__).parent / "keycloak-26.7.3-authenticators.json").read_text()
)
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL")

ENABLED = ["true", "{enabled: true}"]
DISABLED = ["false", "{enabled: false}", None]
INVALID = ['"yes"', "{enabled: yes-please}", "[true]"]


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


# ---------------------------------------------------------------------------
# unit: the render
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", DISABLED)
def test_disabled_renders_keycloaks_own_browser_flow(value):
    realm = realm_of(render(value))
    assert realm["browserFlow"] == "browser"
    assert realm["authenticationFlows"] == []


@pytest.mark.parametrize("value", ENABLED)
def test_enabled_renders_the_admin_flow_whichever_way_it_is_written(value):
    realm = realm_of(render(value))
    flows = {f["alias"]: f for f in realm["authenticationFlows"]}
    assert realm["browserFlow"] in flows
    assert flows[realm["browserFlow"]]["topLevel"] is True


@pytest.mark.parametrize("value", INVALID)
def test_anything_else_stops_the_render(value):
    result = render(value)
    assert result.returncode != 0
    assert "keycloak.admin_mfa must be true, false or {enabled: true|false}" in result.stderr


@pytest.fixture(scope="module")
def enabled_realm() -> dict:
    return realm_of(render("true"))


def executions(realm):
    for flow in realm["authenticationFlows"]:
        for ex in flow["authenticationExecutions"]:
            yield flow["alias"], ex


def test_every_authenticator_exists_in_keycloak(enabled_realm):
    unknown = {(f, ex["authenticator"]) for f, ex in executions(enabled_realm)
               if not ex.get("authenticatorFlow") and ex["authenticator"] not in AUTHENTICATORS}
    assert unknown == set()


def test_every_sub_flow_and_config_is_defined(enabled_realm):
    aliases = {f["alias"] for f in enabled_realm["authenticationFlows"]}
    configs = {c["alias"]: c["config"] for c in enabled_realm["authenticatorConfig"]}
    for _, ex in executions(enabled_realm):
        if ex.get("authenticatorFlow"):
            assert ex["flowAlias"] in aliases
        if "authenticatorConfig" in ex:
            assert ex["authenticatorConfig"] in configs
            accepted = set(AUTHENTICATORS[ex["authenticator"]] or [])
            assert set(configs[ex["authenticatorConfig"]]) <= accepted, ex


def _by_alias(realm):
    return {f["alias"]: f for f in realm["authenticationFlows"]}


def _config(realm, ex):
    return next(c["config"] for c in realm["authenticatorConfig"] if c["alias"] == ex["authenticatorConfig"])


def test_the_admin_branch_is_skipped_after_a_passkey_and_for_non_admins(enabled_realm):
    flows = _by_alias(enabled_realm)
    admin = next(f for f in flows.values() if any(ex.get("authenticator") == "conditional-credential"
                                                   for ex in f["authenticationExecutions"]))
    conds = {ex["authenticator"]: _config(enabled_realm, ex) for ex in admin["authenticationExecutions"]
             if ex.get("authenticator", "").startswith("conditional-")}
    assert conds["conditional-user-role"] == {"condUserRole": "admin", "negate": "false"}
    assert conds["conditional-credential"] == {"credentials": "webauthn-passwordless", "included": "false"}


def test_an_admin_with_no_second_factor_is_made_to_enrol_one(enabled_realm):
    flows = _by_alias(enabled_realm)
    enrol = next(f for f in flows.values() if any(ex.get("authenticator") == "conditional-sub-flow-executed"
                                                  for ex in f["authenticationExecutions"]))
    gate = next(ex for ex in enrol["authenticationExecutions"] if ex.get("authenticator") == "conditional-sub-flow-executed")
    checked = _config(enabled_realm, gate)
    assert checked["check_result"] == "not-executed"
    assert checked["flow_to_check"] in flows
    required = {ex["authenticator"] for ex in enrol["authenticationExecutions"] if ex["requirement"] == "REQUIRED"}
    assert {"auth-otp-form", "auth-recovery-authn-code-form"} <= required


# ---------------------------------------------------------------------------
# integration: a throwaway Keycloak accepts it and resolves every execution
# ---------------------------------------------------------------------------


def _admin(method, path, body=None):
    token = json.load(urllib.request.urlopen(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                "username": "admin", "password": "admin"}).encode()))["access_token"]
    req = urllib.request.Request(f"{KEYCLOAK_URL}/admin/realms{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        text = r.read().decode()
        return json.loads(text) if text else r.status


@pytest.mark.skipif(not KEYCLOAK_URL, reason="needs a throwaway Keycloak: KEYCLOAK_URL")
def test_keycloak_imports_the_flow_and_resolves_every_execution(enabled_realm):
    name = "admin-mfa-check-import"
    realm = {"realm": name, "enabled": True, "browserFlow": enabled_realm["browserFlow"],
             "authenticationFlows": enabled_realm["authenticationFlows"],
             "authenticatorConfig": enabled_realm["authenticatorConfig"],
             "roles": {"realm": [{"name": "admin"}]}}
    try:
        _admin("DELETE", f"/{name}")
    except urllib.error.HTTPError:
        pass
    _admin("POST", "", realm)
    try:
        flat = _admin("GET", f"/{name}/authentication/flows/{urllib.parse.quote(realm['browserFlow'], safe='')}/executions")
        providers = {e["providerId"] for e in flat if "providerId" in e}
        expected = {ex["authenticator"] for _, ex in executions(enabled_realm) if not ex.get("authenticatorFlow")}
        assert expected - {"auth-spnego"} <= providers
        assert _admin("GET", f"/{name}")["browserFlow"] == realm["browserFlow"]
    finally:
        _admin("DELETE", f"/{name}")
