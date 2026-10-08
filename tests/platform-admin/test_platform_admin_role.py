"""A platform administrator is a realm role, rendered through helmfile.

Two levels, nothing in between (celine-policies ADR-0012):
- the realm role `platform-admin`, in `realm_access.roles`, is the only platform-wide grant;
- an organisation's own groups reach a token only inside `organization.<alias>.groups` and
  count only in that organisation.

There are no realm groups, no realm roles admin/manager/editor/viewer and no top-level
`groups` claim. This file renders every release the change touches over
`envs/dev/values.yaml` (no secrets, as in tests/auth-setup) and checks what each one would be
handed:

    task test:platform-admin                   # render checks
    KEYCLOAK_URL=http://127.0.0.1:18081 \\
      task test:platform-admin                 # also import the realm and read real tokens

It needs `helmfile` and `helm`. Rendering prefect-server fetches its chart, so that part needs
network access.

The KEYCLOAK_URL layer is for a throwaway Keycloak only (master admin admin/admin): it creates
and deletes a realm named `platform-admin-check`. Never point it at a deployment.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL")

PLATFORM_ADMIN = "platform-admin"
RETIRED_ROLES = {"admin", "manager", "editor", "viewer"}
OAUTH2_PROXY = "oauth2_proxy"


def render(helmfile: str, release: str, override: str | None = None) -> list[dict]:
    """`helmfile template` one release of `helmfile.d/<helmfile>` over the dev values only."""
    result = template(helmfile, release, override)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def template(helmfile: str, release: str, override: str | None = None) -> subprocess.CompletedProcess:
    """The `helmfile template` run behind `render`, for a test that expects the render to stop."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = (REPO / "helmfile.d" / helmfile).read_text()
        # no secrets: the dev secrets file is not committed (a fresh clone cannot render it)
        source = re.sub(r"\n    secrets:\n      - [^\n]*", "", source)
        if override is not None:
            (tmp / "override.yaml").write_text(override)
            source = source.replace(
                "      - ../envs/dev/values.yaml\n",
                f"      - ../envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n",
                1,
            )
        (tmp / "helmfile.yaml.gotmpl").write_text(source.replace("../", f"{REPO}/"))
        env = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "dev", "template", "-q",
             "-l", f"name={release}", "--skip-tests"],
            capture_output=True, text=True, env=env, timeout=600,
        )
    return result


def realm_of(docs: list[dict]) -> dict:
    for doc in docs:
        if doc.get("kind") == "ConfigMap" and "celine-realm.json" in (doc.get("data") or {}):
            return json.loads(doc["data"]["celine-realm.json"])
    raise AssertionError("no keycloak-realm-celine ConfigMap in the render")


def ingress_auth_urls(docs: list[dict]) -> list[str]:
    return [doc["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-url"]
            for doc in docs
            if doc.get("kind") == "Ingress"
            and "nginx.ingress.kubernetes.io/auth-url" in ((doc.get("metadata") or {}).get("annotations") or {})]


@pytest.fixture(scope="module")
def realm() -> dict:
    return realm_of(render("0020-auth.yaml.gotmpl", "auth-setup"))


def oauth2_proxy_client(realm: dict) -> dict:
    return next(c for c in realm["clients"] if c["clientId"] == OAUTH2_PROXY)


def writes_top_level_groups(mapper: dict) -> bool:
    """A mapper that would put realm groups or realm roles into a top-level `groups` claim."""
    config = mapper.get("config") or {}
    return (config.get("claim.name") == "groups"
            or mapper.get("protocolMapper") in {"oidc-group-membership-mapper", "oidc-usermodel-realm-role-mapper"})


# ---------------------------------------------------------------------------
# unit: the realm import
# ---------------------------------------------------------------------------


def test_the_only_realm_role_is_platform_admin(realm):
    assert [r["name"] for r in realm["roles"]["realm"]] == [PLATFORM_ADMIN]


def test_there_are_no_realm_groups(realm):
    assert realm.get("groups", []) == []
    for user in realm["users"]:
        assert not user.get("groups"), user["username"]


def test_the_realm_admin_holds_the_role_directly(realm):
    admin = next(u for u in realm["users"] if u["username"] == "admin")  # dev auth_setup.realmAdminUser
    assert admin["realmRoles"] == [PLATFORM_ADMIN]


def test_no_client_writes_a_top_level_groups_claim(realm):
    for client in realm["clients"]:
        offending = [m["name"] for m in client.get("protocolMappers", []) if writes_top_level_groups(m)]
        assert offending == [], client["clientId"]


def test_the_one_groups_mapper_is_the_organisation_one(realm):
    client = oauth2_proxy_client(realm)
    groups_mappers = [m for m in client["protocolMappers"] if "group" in m["protocolMapper"]]
    assert [m["protocolMapper"] for m in groups_mappers] == ["oidc-organization-group-membership-mapper"]
    # the organisation group mapper fills `groups` inside the claim the organization scope writes
    assert "organization" in client["defaultClientScopes"]


def test_user_tokens_carry_realm_roles(realm):
    # `roles` puts realm_access.roles into the access token: how platform-admin reaches a service
    assert "roles" in oauth2_proxy_client(realm)["defaultClientScopes"]


def test_microprofile_jwt_cannot_be_requested(realm):
    # its realm-role mapper writes every realm role into a top-level `groups` claim
    client = oauth2_proxy_client(realm)
    assert "microprofile-jwt" not in client["defaultClientScopes"] + client["optionalClientScopes"]


def test_admin_mfa_is_left_to_bootstrap_and_names_no_retired_role():
    # celine-policies bootstrap owns the admin flow and conditions it on platform-admin
    # (tests/auth-setup, tests/keycloak-access); the import must not condition on anything
    mfa_realm = realm_of(render("0020-auth.yaml.gotmpl", "auth-setup", "keycloak:\n  admin_mfa: true\n"))
    assert mfa_realm["authenticatorConfig"] == []
    assert not RETIRED_ROLES & {r["name"] for r in mfa_realm["roles"]["realm"]}


# ---------------------------------------------------------------------------
# unit: the proxy gates and the MQTT broker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helmfile,release", [
    ("0030-apps.yaml.gotmpl", "marquez"),
    ("0040-pipelines.yaml.gotmpl", "prefect-server"),
])
def test_the_admin_uis_admit_platform_admins_only(helmfile, release):
    urls = ingress_auth_urls(render(helmfile, release))
    assert urls, f"{release}: no ingress behind oauth2-proxy"
    for url in urls:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        # keycloak-oidc names a realm role `role:<name>`; `admins` would be a group
        assert query["allowed_groups"] == [f"role:{PLATFORM_ADMIN}"], url


ADMIN_UIS = [("0030-apps.yaml.gotmpl", "marquez"), ("0040-pipelines.yaml.gotmpl", "prefect-server")]


@pytest.mark.parametrize("helmfile,release", ADMIN_UIS)
@pytest.mark.parametrize("role", ["ops-admin", "celine-cli:operator"])
def test_the_admin_role_is_the_environments_choice(helmfile, release, role):
    # auth_setup.adminRole; a client role is `<client>:<role>`, which keycloak-oidc names
    # `role:<client>:<role>`
    urls = ingress_auth_urls(render(helmfile, release, f"auth_setup:\n  adminRole: {role!r}\n"))
    assert urls, f"{release}: no ingress behind oauth2-proxy"
    for url in urls:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert query["allowed_groups"] == [f"role:{role}"], url


@pytest.mark.parametrize("helmfile,release", ADMIN_UIS)
@pytest.mark.parametrize("role", ["", "admins,viewers", "platform-admin&allowed_emails=x", "a:b:c"])
def test_a_malformed_admin_role_stops_the_render(helmfile, release, role):
    # an empty role would gate on `role:` and admit nobody; a list or a second query
    # parameter would widen the gate behind a value that reads like one role
    result = template(helmfile, release, f"auth_setup:\n  adminRole: {role!r}\n")
    assert result.returncode != 0
    assert "auth_setup.adminRole" in result.stderr


def test_the_broker_has_no_superuser():
    docs = render("0030-apps.yaml.gotmpl", "mosquitto-go-auth")
    conf = next(v for doc in docs if doc.get("kind") == "ConfigMap"
                for v in (doc.get("data") or {}).values() if "auth_plugin" in v)
    assert re.search(r"^\s*auth_opt_disable_superuser true\s*$", conf, re.M), conf


def test_the_dead_jwt_disable_superuser_key_is_gone():
    # the template reads goAuth.disableSuperuser; goAuth.jwt.disableSuperuser was never read
    values = (REPO / "defaults/0030-apps/mosquitto-go-auth/values.yaml.gotmpl").read_text()
    jwt = values.split("\n  jwt:\n", 1)[1].split("\n  files:\n", 1)[0]
    assert "disableSuperuser" not in jwt


def test_mqtt_auth_is_given_no_superuser_scope():
    docs = render("0050-celine-services.yaml.gotmpl", "mqtt-auth")
    names = {e.get("name") for doc in docs if doc.get("kind") == "Deployment"
             for c in doc["spec"]["template"]["spec"]["containers"] for e in c.get("env") or []}
    assert names, "no mqtt-auth container env rendered"
    assert "CELINE_MQTT_SUPERUSER_SCOPE" not in names


# ---------------------------------------------------------------------------
# integration: a throwaway Keycloak imports the realm and issues the tokens
# ---------------------------------------------------------------------------

CHECK_REALM = "platform-admin-check"


def _admin_token() -> str:
    return json.load(urllib.request.urlopen(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                "username": "admin", "password": "admin"}).encode()))["access_token"]


def _admin(method, path, body=None):
    data = None if body is None else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
    req = urllib.request.Request(f"{KEYCLOAK_URL}/admin/realms{path}", method=method, data=data)
    req.add_header("Authorization", f"Bearer {_admin_token()}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        text = r.read().decode()
        return json.loads(text) if text else r.status


def _claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture(scope="module")
def tokens(realm):
    """Import the rendered realm, add an organisation and a stale realm group, sign three users in."""
    imported = json.loads(json.dumps(realm))
    imported.pop("id", None)
    imported["realm"] = CHECK_REALM
    for role in imported["roles"]["realm"]:
        role.pop("containerId", None)
    secret = oauth2_proxy_client(imported)["secret"]
    try:
        _admin("DELETE", f"/{CHECK_REALM}")
    except urllib.error.HTTPError:
        pass
    _admin("POST", "", imported)
    try:
        r = f"/{CHECK_REALM}"
        _admin("PUT", r, {"organizationsEnabled": True})
        _admin("POST", f"{r}/organizations", {"name": "example-rec", "alias": "example_rec",
                                              "domains": [{"name": "rec.example.org"}]})
        org = _admin("GET", f"{r}/organizations")[0]["id"]
        _admin("POST", f"{r}/organizations/{org}/groups", {"name": "admins"})
        org_admins = next(g["id"] for g in _admin("GET", f"{r}/organizations/{org}/groups") if g["name"] == "admins")
        for name in ("org-admin", "legacy-admin"):
            _admin("POST", f"{r}/users", {"username": name, "enabled": True, "emailVerified": True,
                                          "email": f"{name}@rec.example.org", "firstName": "Ex", "lastName": "Ample",
                                          "credentials": [{"type": "password", "value": name, "temporary": False}]})
        uid = {u: _admin("GET", f"{r}/users?username={u}&exact=true")[0]["id"] for u in ("admin", "org-admin", "legacy-admin")}
        for u in ("admin", "org-admin"):
            _admin("POST", f"{r}/organizations/{org}/members", json.dumps(uid[u]))
            _admin("PUT", f"{r}/organizations/{org}/groups/{org_admins}/members/{uid[u]}")
        # a realm group left behind by an older realm: it must reach no token
        _admin("POST", f"{r}/groups", {"name": "admins"})
        stale = _admin("GET", f"{r}/groups?search=admins&exact=true")[0]["id"]
        _admin("PUT", f"{r}/users/{uid['legacy-admin']}/groups/{stale}")

        issued = {}
        for u in ("admin", "org-admin", "legacy-admin"):
            password = "admin" if u == "admin" else u  # dev auth_setup.realmAdminPassword
            body = json.load(urllib.request.urlopen(
                f"{KEYCLOAK_URL}/realms/{CHECK_REALM}/protocol/openid-connect/token",
                urllib.parse.urlencode({"grant_type": "password", "client_id": OAUTH2_PROXY, "client_secret": secret,
                                        "username": u, "password": password,
                                        "scope": "openid email profile organization:*"}).encode()))
            req = urllib.request.Request(f"{KEYCLOAK_URL}/realms/{CHECK_REALM}/protocol/openid-connect/userinfo")
            req.add_header("Authorization", f"Bearer {body['access_token']}")
            issued[u] = {"access": _claims(body["access_token"]), "id": _claims(body["id_token"]),
                         "userinfo": json.load(urllib.request.urlopen(req))}
        yield issued
    finally:
        _admin("DELETE", f"/{CHECK_REALM}")


needs_keycloak = pytest.mark.skipif(not KEYCLOAK_URL, reason="needs a throwaway Keycloak: KEYCLOAK_URL")


@needs_keycloak
def test_a_platform_admin_token_carries_the_role(tokens):
    assert PLATFORM_ADMIN in tokens["admin"]["access"]["realm_access"]["roles"]


@needs_keycloak
def test_an_organisation_admin_is_not_a_platform_admin(tokens):
    org_admin = tokens["org-admin"]
    assert PLATFORM_ADMIN not in org_admin["access"].get("realm_access", {}).get("roles", [])
    assert org_admin["access"]["organization"] == {"example_rec": {"groups": ["/admins"]}}


@needs_keycloak
def test_a_stale_realm_group_reaches_no_token(tokens):
    legacy = tokens["legacy-admin"]
    assert PLATFORM_ADMIN not in legacy["access"].get("realm_access", {}).get("roles", [])
    assert not RETIRED_ROLES & set(legacy["access"].get("realm_access", {}).get("roles", []))


@needs_keycloak
@pytest.mark.parametrize("user", ["admin", "org-admin", "legacy-admin"])
@pytest.mark.parametrize("where", ["access", "id", "userinfo"])
def test_no_token_has_a_top_level_groups_claim(tokens, user, where):
    assert "groups" not in tokens[user][where]
