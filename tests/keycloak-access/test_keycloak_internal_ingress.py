"""A realm on an internal host (`keycloak.internal_ingress`, release keycloak-internal-ingress).

A shared Keycloak can serve a realm on a host that only the LAN and the VPN resolve (the
platform's internal zone, its own CA), instead of the public host: the realm's sign-in then
never answers from the internet. The release renders one Ingress on that host with, per
listed realm, `/realms/<realm>` (Exact) and `/realms/<realm>/` (Prefix), plus `/resources/`;
nothing of `/admin` and never master. Its certificate comes from the named ClusterIssuer.
The realm's issuer follows its own `frontendUrl` (policies-shell, `keycloak.realm_frontend_url`).

- Not installed unless `keycloak.internal_ingress.realms` is set, and never without the
  keycloak release (`keycloak.enabled`).
- `host` and `cluster_issuer` are required with `realms`; `realms` is a non-empty list of
  realm names, never master.
- The public Ingress is untouched by it (a realm leaves the public host through
  `keycloak.public_realms`).

    task test:keycloak-access
"""

from __future__ import annotations

import pytest

from test_keycloak_access import (ENTRYPOINTS, PUBLIC_INGRESS, TLS_OPTIONS, TRAEFIK, docs_of, ingress_named,
                                  of_kind, releases_of, render)

RELEASE = "keycloak-internal-ingress"
INGRESS = "keycloak-internal"
HOST = "auth.celine.internal.test"
ISSUER = "platform-ca"
INTERNAL = (f"keycloak:\n  internal_ingress:\n    host: {HOST}\n    cluster_issuer: {ISSUER}\n"
            "    realms: [celine-warehouse]\n")
BASE = "domain: example.org\n" + TRAEFIK


def render_internal(extra: str = INTERNAL, env: str = "staging"):
    return render("0020-auth.yaml.gotmpl", RELEASE, env=env, override=BASE + extra)


@pytest.fixture(scope="module")
def internal() -> dict:
    docs = docs_of(render_internal())
    assert [d["kind"] for d in docs] == ["Ingress"]  # the Ingress and nothing else
    return ingress_named(docs, INGRESS)


def paths(ingress: dict) -> dict[str, str]:
    (rule,) = ingress["spec"]["rules"]
    return {p["path"]: p["pathType"] for p in rule["http"]["paths"]}


def test_the_internal_ingress_serves_only_the_listed_realms_and_resources(internal):
    assert [r["host"] for r in internal["spec"]["rules"]] == [HOST]
    assert paths(internal) == {"/realms/celine-warehouse": "Exact", "/realms/celine-warehouse/": "Prefix",
                               "/resources/": "Prefix"}


def test_nothing_of_admin_or_master_is_routed(internal):
    for path in paths(internal):
        assert not path.startswith("/admin") and "master" not in path


def test_it_routes_to_the_keycloak_service(internal):
    (rule,) = internal["spec"]["rules"]
    for p in rule["http"]["paths"]:
        assert p["backend"] == {"service": {"name": "keycloak-keycloakx-http", "port": {"name": "http"}}}


def test_its_certificate_comes_from_the_named_cluster_issuer(internal):
    annotations = internal["metadata"]["annotations"]
    assert annotations["cert-manager.io/cluster-issuer"] == ISSUER
    assert internal["spec"]["tls"] == [{"hosts": [HOST], "secretName": "keycloak-internal-tls"}]


def test_it_uses_the_public_ingresss_class_entrypoint_and_tls_options(internal):
    assert internal["spec"]["ingressClassName"] == "traefik"
    annotations = internal["metadata"]["annotations"]
    assert annotations[ENTRYPOINTS] == "websecure"
    assert annotations[TLS_OPTIONS] == "celine-staging-celine-tls@kubernetescrd"


def test_several_realms():
    docs = docs_of(render_internal(INTERNAL.replace("[celine-warehouse]", "[celine-warehouse, celine-preprod]")))
    assert set(paths(ingress_named(docs, INGRESS))) == {
        "/realms/celine-warehouse", "/realms/celine-warehouse/",
        "/realms/celine-preprod", "/realms/celine-preprod/", "/resources/"}


def test_the_public_ingress_is_unchanged_by_it():
    plain = docs_of(render("0020-auth.yaml.gotmpl", "keycloak", env="staging", override=BASE))
    with_internal = docs_of(render("0020-auth.yaml.gotmpl", "keycloak", env="staging", override=BASE + INTERNAL))
    assert ingress_named(plain, PUBLIC_INGRESS) == ingress_named(with_internal, PUBLIC_INGRESS)
    assert ingress_named(with_internal, INGRESS) is None  # the keycloak release never renders it


# ---------------------------------------------------------------------------
# installed only when asked
# ---------------------------------------------------------------------------


def test_installed_with_realms():
    found = releases_of("0020-auth.yaml.gotmpl", BASE + INTERNAL, env="staging")
    assert found[RELEASE].get("installed", True) is True
    assert found[RELEASE]["chart"].endswith("/charts/keycloak-internal-ingress")
    assert found[RELEASE]["labels"]["group"] == "auth"


@pytest.mark.parametrize("extra", ["", "keycloak:\n  internal_ingress: {}\n", INTERNAL + "  enabled: false\n"])
def test_otherwise_not_installed(extra):
    found = releases_of("0020-auth.yaml.gotmpl", BASE + extra, env="staging")
    assert found[RELEASE]["installed"] is False


# ---------------------------------------------------------------------------
# refused values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra,needle", [
    (INTERNAL.replace(f"    host: {HOST}\n", ""), "keycloak.internal_ingress.host"),
    (INTERNAL.replace(f"    cluster_issuer: {ISSUER}\n", ""), "keycloak.internal_ingress.cluster_issuer"),
    (INTERNAL.replace("[celine-warehouse]", "[master]"), "master"),
    (INTERNAL.replace("[celine-warehouse]", "celine-warehouse"), "keycloak.internal_ingress.realms"),
    (INTERNAL.replace("[celine-warehouse]", "['a b']"), "is not a realm name"),
])
def test_refused_values_stop_the_render(extra, needle):
    result = render_internal(extra)
    assert result.returncode != 0
    assert needle in result.stderr
