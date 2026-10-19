"""The services dashboard (a static page of links behind oauth2-proxy) can be turned off.

`auth_setup.dashboard_enabled: false` renders none of it: no Deployment, Service, Ingress or
ConfigMap, so no public `dashboard.` host and no certificate for it. Unset, it stays, so an
environment that does not set the key renders unchanged.

    task test:auth-setup
"""

from __future__ import annotations

import yaml
from test_admin_mfa import render

OFF = "auth_setup:\n  dashboard_enabled: false\n"


def objects(extra: str = "") -> set[str]:
    result = render("false", extra)
    assert result.returncode == 0, result.stderr
    return {f"{d['kind']}/{d['metadata']['name']}" for d in yaml.safe_load_all(result.stdout) if d}


def test_the_dashboard_stays_by_default():
    assert {"Deployment/dashboard", "Service/dashboard", "Ingress/dashboard", "ConfigMap/dashboard-html"} <= objects()


def test_the_dashboard_can_be_turned_off():
    rendered = objects(OFF)
    assert not {o for o in rendered if "dashboard" in o}


def test_turning_it_off_keeps_the_rest():
    rendered = objects(OFF)
    assert {"ConfigMap/keycloak-realm-celine", "ConfigMap/oauth2-proxy-config", "Secret/oauth2-proxy-secret"} <= rendered
