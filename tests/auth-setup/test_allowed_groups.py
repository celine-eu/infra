"""`auth_setup.allowed_groups`: only these groups pass the whole oauth2-proxy.

For an environment whose sso fronts admin UIs only (Prefect, Marquez), where the platform-admin
check cannot ride on the forwardAuth address: Traefik answers a 401 from `/oauth2/auth` as is
(no sign-in redirect without the `errors` Middleware), so its forwardAuth goes to the proxy's
root, where the `allowed_groups` query does nothing. Unset, the config is as before.

    task test:auth-setup
"""

from __future__ import annotations

import yaml

from test_admin_mfa import render


def cfg_of(extra: str = "") -> str:
    # render() reads `extra` only with an admin_mfa value; "true" is the default outside dev.
    result = render("true", extra)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "oauth2-proxy-config":
            return doc["data"]["oauth2_proxy.cfg"]
    raise AssertionError("no oauth2-proxy-config in the render")


def test_unset_every_signed_in_user_passes():
    assert "allowed_groups" not in cfg_of()


def test_set_only_those_groups_pass():
    cfg = cfg_of("auth_setup:\n  allowed_groups: [role:platform-admin]\n")
    assert 'allowed_groups = [ "role:platform-admin" ]' in cfg


def test_several_groups_are_one_list():
    cfg = cfg_of("auth_setup:\n  allowed_groups: [role:platform-admin, role:operator]\n")
    assert 'allowed_groups = [ "role:platform-admin", "role:operator" ]' in cfg
