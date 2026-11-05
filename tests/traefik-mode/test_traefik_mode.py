"""Traefik mode (`ingress_controller.annotations: traefik`) of the public hosts, rendered
through helmfile: the frontends, the legal host, the API gateway, auth-setup's dashboard
and oauth2-proxy.

On a cluster whose Traefik runs only its Kubernetes Ingress and CRD providers, the
`nginx.ingress.kubernetes.io/*` annotations do nothing. Traefik mode renders what they
did as Traefik's own objects, in the release's namespace (a tenant may reference no
Middleware of another namespace, and a forwardAuth address must be a Service of its own):

- forward auth with sign-in (`auth-url` + `auth-signin`) -> the oauth2-proxy release's
  Middleware `oauth2-proxy-auth` (the proxy's root, which redirects to the provider);
  forward auth without sign-in (`auth-url` only, a bare 401) -> `oauth2-proxy-auth-check`
  (its `/oauth2/auth`);
- `custom-headers` -> a `headers` Middleware with exactly the ConfigMap's headers;
- `permanent-redirect` -> a `redirectRegex` Middleware to the same target, 302;
- `rewrite-target: /$2` on a `/<p>(/|$)(.*)` path -> a Prefix `/<p>` and `stripPrefix`;
- buffer and body-size annotations -> nothing (Traefik has neither limit).

Every path then reaches the Ingress nginx mode sends it to: Traefik's Ingress provider
writes `PathPrefix(<p>)` / `Path(<p>)` and ranks routers by rule length. The nginx mode
render is unchanged (compared byte for byte by the deployment's baseline renders).

    task test:traefik-mode

It needs `helmfile`, `helm` and the oauth2-proxy chart repository (`helmfile repos`). No secrets: it renders over `envs/dev/values.yaml` plus an
override; placeholders only.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
NS = "celine-dev"
DOMAIN = "celine.test"
ISSUER = "example-cluster-issuer"
ENABLE = (
    "onboarding:\n  enabled: true\n"
    "legal:\n  enabled: true\n  image: registry.example.org/legal-site\n  image_tag: v0.0.0\n"
    "security_headers:\n  enabled: true\n"
)
TRAEFIK = (
    "ingress_controller:\n  class: traefik\n  annotations: traefik\n"
    f"tls_setup:\n  ingress_cluster_issuer: {ISSUER}\n"
)
SERVICES = (
    "0050-celine-services.yaml.gotmpl",
    [
        "frontend-webapp",
        "frontend-roi",
        "frontend-grid",
        "frontend-community",
        "frontend-assistant",
        "frontend-onboarding",
        "legal",
        "api-gateway",
    ],
)
AUTH = ("0020-auth.yaml.gotmpl", ["oauth2-proxy", "auth-setup"])
NGINX = "nginx.ingress.kubernetes.io/"
MW = "traefik.ingress.kubernetes.io/router.middlewares"


def render(helmfile: str, releases: list[str], override: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / helmfile).read_text()
        source = re.sub(
            r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE
        )
        header = (
            f"environments:\n  dev:\n    values:\n"
            f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n"
        )
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        # The user's helm configuration: oauth2-proxy is a chart repository's.
        env = dict(os.environ)
        docs = []
        for release in releases:
            # The legal host refuses to render without the deployment's data: one placeholder.
            extra = ["--set", "data.files.register\\.yaml=placeholder"] if release == "legal" else []
            result = subprocess.run(
                ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "dev",
                 "template", "-q", "--skip-deps", "-l", f"name={release}", *extra],
                capture_output=True, text=True, env=env, timeout=600, check=False,
            )
            assert result.returncode == 0, f"{release}: {result.stderr}"
            for d in yaml.safe_load_all(result.stdout):
                if d:
                    d.setdefault("_release", release)
                    docs.append(d)
    return docs


def both(override: str) -> list[dict]:
    return render(*SERVICES, override) + render(*AUTH, override)


NGINX_DOCS = both(ENABLE)
TRAEFIK_DOCS = both(ENABLE + TRAEFIK)


def kind(docs, k):
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == k}


def annotations(doc) -> dict:
    return doc["metadata"].get("annotations") or {}


N_ING = kind(NGINX_DOCS, "Ingress")
T_ING = kind(TRAEFIK_DOCS, "Ingress")
T_MW = kind(TRAEFIK_DOCS, "Middleware")
N_CM = kind(NGINX_DOCS, "ConfigMap")


def middlewares(doc) -> list[str]:
    """Names of the Middlewares an Ingress chains, namespace prefix checked and stripped."""
    value = annotations(doc).get(MW, "")
    out = []
    for ref in filter(None, (v.strip() for v in value.split(","))):
        assert ref.endswith("@kubernetescrd"), ref
        ref = ref[: -len("@kubernetescrd")]
        assert ref.startswith(NS + "-"), f"{doc['metadata']['name']}: {ref} outside {NS}"
        out.append(ref[len(NS) + 1:])
    return out


def chained(doc, type_: str) -> list[dict]:
    return [T_MW[n] for n in middlewares(doc) if n in T_MW and type_ in T_MW[n]["spec"]]


def test_same_ingresses_in_both_modes():
    assert set(T_ING) == set(N_ING)
    assert len(T_ING) > 20


@pytest.mark.parametrize("name", sorted(N_ING))
def test_no_nginx_annotation_and_traefik_router(name):
    a = annotations(T_ING[name])
    assert not [k for k in a if k.startswith(NGINX)], name
    assert a.get("traefik.ingress.kubernetes.io/router.entrypoints") == "websecure"
    assert a.get("cert-manager.io/cluster-issuer") == ISSUER
    assert T_ING[name]["spec"]["ingressClassName"] == "traefik"


@pytest.mark.parametrize("name", sorted(N_ING))
def test_every_chained_middleware_is_rendered(name):
    for n in middlewares(T_ING[name]):
        assert n in T_MW, f"{name} chains {n}, not rendered"


@pytest.mark.parametrize("name", sorted(N_ING))
def test_forward_auth_as_nginx(name):
    a = annotations(N_ING[name])
    mws = middlewares(T_ING[name])
    if f"{NGINX}auth-url" not in a:
        assert not [m for m in mws if m.startswith("oauth2-proxy-auth")], name
    elif f"{NGINX}auth-signin" in a:
        assert "oauth2-proxy-auth" in mws, name
    else:
        assert "oauth2-proxy-auth-check" in mws, name


def test_oauth2_proxy_middlewares():
    auth = T_MW["oauth2-proxy-auth"]["spec"]["forwardAuth"]
    check = T_MW["oauth2-proxy-auth-check"]["spec"]["forwardAuth"]
    assert auth["address"] == f"http://oauth2-proxy.{NS}.svc.cluster.local/"
    assert check["address"] == f"http://oauth2-proxy.{NS}.svc.cluster.local/oauth2/auth"
    for fa in (auth, check):
        for h in ("Authorization", "X-Auth-Request-Access-Token", "X-Auth-Request-User",
                  "X-Auth-Request-Email", "X-Auth-Request-Groups"):
            assert h in fa["authResponseHeaders"]


def test_oauth2_proxy_sets_the_headers_it_forwards():
    cfg = next(d for d in TRAEFIK_DOCS if d.get("kind") == "ConfigMap"
               and d["metadata"]["name"] == "oauth2-proxy-config")["data"]["oauth2_proxy.cfg"]
    for line in ("set_xauthrequest = true", "set_authorization_header = true", "pass_access_token = true"):
        assert line in cfg


@pytest.mark.parametrize("name", sorted(N_ING))
def test_security_headers_as_nginx(name):
    ref = annotations(N_ING[name]).get(f"{NGINX}custom-headers")
    headers = chained(T_ING[name], "headers")
    if not ref:
        assert not headers, name
        return
    expected = N_CM[ref.split("/", 1)[1]]["data"]
    assert len(headers) == 1, name
    assert headers[0]["spec"]["headers"]["customResponseHeaders"] == expected


@pytest.mark.parametrize("name", sorted(N_ING))
def test_redirect_as_nginx(name):
    target = annotations(N_ING[name]).get(f"{NGINX}permanent-redirect")
    redirects = chained(T_ING[name], "redirectRegex")
    if not target:
        assert not redirects, name
        return
    assert len(redirects) == 1, name
    rr = redirects[0]["spec"]["redirectRegex"]
    assert rr["replacement"] == target
    assert rr.get("permanent", False) is False  # 302, as permanent-redirect-code
    assert re.fullmatch(rr["regex"], f"https://host.{DOMAIN}/any/path?q=1")


def regex_prefix(path: str) -> str | None:
    m = re.fullmatch(r"(/[^()]*)\(/\|\$\)\(\.\*\)", path)
    return m.group(1) if m else None


@pytest.mark.parametrize("name", sorted(N_ING))
def test_rewrite_as_nginx(name):
    n, t = N_ING[name], T_ING[name]
    strips = chained(t, "stripPrefix")
    if annotations(n).get(f"{NGINX}rewrite-target") != "/$2":
        assert not strips, name
        return
    npaths = [p["path"] for r in n["spec"]["rules"] for p in r["http"]["paths"]]
    tpaths = [(p["path"], p["pathType"]) for r in t["spec"]["rules"] for p in r["http"]["paths"]]
    prefixes = [regex_prefix(p) for p in npaths]
    assert None not in prefixes, npaths
    assert tpaths == [(p, "Prefix") for p in prefixes]
    assert len(strips) == 1
    assert sorted(strips[0]["spec"]["stripPrefix"]["prefixes"]) == sorted(prefixes)


# Routing: every request reaches the same Ingress in both modes.


def matches(path, path_type, request):
    if path_type == "Exact":
        return request == path
    base = path.rstrip("/")
    return path == "/" or request == base or request.startswith(base + "/")


def routes(ings, regex_aware):
    out = defaultdict(list)
    for name, doc in ings.items():
        for rule in doc["spec"]["rules"]:
            for p in rule["http"]["paths"]:
                path, pt = p["path"], p.get("pathType", "Prefix")
                if regex_aware and (rp := regex_prefix(path)):
                    path, pt = rp, "Prefix"
                if pt == "ImplementationSpecific":
                    pt = "Prefix"
                out[rule["host"]].append((name, path, pt))
    return out


def nginx_pick(rs, request):
    exact = [r for r in rs if r[2] == "Exact" and matches(r[1], r[2], request)]
    if exact:
        return exact[0][0]
    pre = [r for r in rs if r[2] != "Exact" and matches(r[1], r[2], request)]
    return max(pre, key=lambda r: len(r[1]))[0] if pre else None


def traefik_rule(host, path, pt):
    return f"Host(`{host}`) && " + (f"Path(`{path}`)" if pt == "Exact" else f"PathPrefix(`{path}`)")


def traefik_pick(rs, request, host):
    c = [r for r in rs if matches(r[1], r[2], request)]
    return max(c, key=lambda r: len(traefik_rule(host, r[1], r[2])))[0] if c else None


N_ROUTES = routes(N_ING, regex_aware=True)
T_ROUTES = routes(T_ING, regex_aware=False)
PROBES = [
    (host, req)
    for host, rs in N_ROUTES.items()
    for _, path, pt in rs
    for req in ([path] if pt == "Exact" else [path, path.rstrip("/") + "/probe"])
]


@pytest.mark.parametrize(("host", "request_path"), PROBES, ids=[f"{h}{r}" for h, r in PROBES])
def test_traefik_mode_routes_as_nginx(host, request_path):
    assert traefik_pick(T_ROUTES[host], request_path, host) == nginx_pick(N_ROUTES[host], request_path)
