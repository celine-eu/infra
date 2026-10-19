"""Superset is no longer part of CELINE: `auth-setup` renders nothing for it.

No sign-out Ingresses on its host (they asked cert-manager for a certificate of a host that
serves nothing, two Ingresses on one TLS Secret), no redirect URI for it in the realm's
oauth2-proxy client, no dashboard link.

    task test:auth-setup
"""

from __future__ import annotations

import yaml
from test_admin_mfa import realm_of, render


def test_no_object_mentions_superset():
    result = render("false")
    assert result.returncode == 0, result.stderr
    assert "superset" not in result.stdout.lower()


def test_no_ingress_on_the_superset_host():
    result = render("false")
    docs = [d for d in yaml.safe_load_all(result.stdout) if d and d.get("kind") == "Ingress"]
    hosts = {rule["host"] for d in docs for rule in d["spec"].get("rules", [])}
    assert not any(h.startswith("superset.") for h in hosts)


def test_the_oauth2_proxy_client_keeps_its_other_redirects():
    realm = realm_of(render("false"))
    (client,) = [c for c in realm["clients"] if c["clientId"] == "oauth2_proxy"]
    assert not any("superset" in uri for uri in client["redirectUris"])
    assert any(uri.endswith("/oauth2/callback") for uri in client["redirectUris"])
