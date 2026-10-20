"""Traefik's NGINX provider sends each request to the Ingress path ingress-nginx would,
rendered through helmfile (the frontends, group `celine-services`).

ingress-nginx serves an `Exact` path before any prefix, then the longest `Prefix`. Traefik's
NGINX provider (v3.7.14) sets no router priority, so Traefik ranks routers by the length of
their rule, and it writes a `Prefix` path as `(Path("/p") || PathPrefix("/p/"))` but an
`Exact` one as `Path("/p")`. A short prefix then outranks a longer exact path: on prod
(2026-10-20) `/oauth2/sign_out` (Exact, redirect to oauth2-proxy's sign-out and Keycloak's
logout) lost to `/oauth2` (Prefix, forward auth to oauth2-proxy), so signing out left the
Keycloak session open.

For every host, each path is probed (itself, and below it for a prefix) and the Ingress
nginx picks must be the one Traefik's rule length picks. Paths of Ingresses using regex
(`use-regex`) are left out: nginx orders regex locations by their order in the file.

    task test:ingress-routing

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override that installs onboarding's frontend.
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
HELMFILE = "0050-celine-services.yaml.gotmpl"
RELEASES = [
    "frontend-webapp",
    "frontend-roi",
    "frontend-grid",
    "frontend-community",
    "frontend-assistant",
    "frontend-onboarding",
]
OVERRIDE = "onboarding:\n  enabled: true\n"
REGEX = "nginx.ingress.kubernetes.io/use-regex"


def render(release: str) -> list[dict]:
    """Render `release` over the dev values plus OVERRIDE; return its documents."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(OVERRIDE)
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(
            r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*",
            "",
            source,
            flags=re.MULTILINE,
        )
        header = (
            f"environments:\n  dev:\n    values:\n"
            f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n"
        )
        (tmp / "helmfile.yaml.gotmpl").write_text(
            header + source.replace("../", f"{REPO}/")
        )
        environ = {
            **os.environ,
            "HELM_CONFIG_HOME": str(tmp / "hc"),
            "HELM_CACHE_HOME": str(tmp / "hcache"),
        }
        result = subprocess.run(
            [
                "helmfile",
                "-f",
                str(tmp / "helmfile.yaml.gotmpl"),
                "-e",
                "dev",
                "template",
                "-q",
                "-l",
                f"name={release}",
            ],
            capture_output=True,
            text=True,
            env=environ,
            timeout=600,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def traefik_rule(host: str, path: str, path_type: str) -> str:
    """The rule Traefik's NGINX provider writes (translator.go buildRule, buildPrefixRule)."""
    host_rule = f'Host("{host}")'
    if path_type == "Exact":
        return f'{host_rule} && Path("{path}")'
    if path == "/":
        return f'{host_rule} && PathPrefix("/")'
    path = path.rstrip("/")
    return f'{host_rule} && (Path("{path}") || PathPrefix("{path}/"))'


def matches(path: str, path_type: str, request: str) -> bool:
    if path_type == "Exact":
        return request == path
    base = path.rstrip("/")
    return path == "/" or request == base or request.startswith(base + "/")


def nginx_pick(routes: list[tuple], request: str) -> str | None:
    exact = [r for r in routes if r[2] == "Exact" and matches(r[1], r[2], request)]
    if exact:
        return exact[0][0]
    prefixes = [r for r in routes if r[2] != "Exact" and matches(r[1], r[2], request)]
    return max(prefixes, key=lambda r: len(r[1]))[0] if prefixes else None


def traefik_pick(routes: list[tuple], request: str, host: str) -> str | None:
    candidates = [r for r in routes if matches(r[1], r[2], request)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: len(traefik_rule(host, r[1], r[2])))[0]


def host_routes() -> dict[str, list[tuple]]:
    """host -> [(ingress/path, path, pathType)] over every release, regex Ingresses left out."""
    routes = defaultdict(list)
    for release in RELEASES:
        for doc in render(release):
            if doc["kind"] != "Ingress":
                continue
            if (doc["metadata"].get("annotations") or {}).get(REGEX) == "true":
                continue
            for rule in doc["spec"]["rules"]:
                for p in rule["http"]["paths"]:
                    path_type = p.get("pathType", "Prefix")
                    if path_type == "ImplementationSpecific":
                        path_type = "Prefix"
                    name = f"{doc['metadata']['name']}{p['path']}"
                    routes[rule["host"]].append((name, p["path"], path_type))
    return routes


ROUTES = host_routes()
PROBES = [
    (host, request)
    for host, routes in ROUTES.items()
    for _, path, path_type in routes
    for request in (
        [path] if path_type == "Exact" else [path, path.rstrip("/") + "/probe"]
    )
]


def test_the_frontends_render_ingresses():
    assert len(ROUTES) == len(RELEASES)


@pytest.mark.parametrize(
    ("host", "request_path"), PROBES, ids=[f"{h}{r}" for h, r in PROBES]
)
def test_traefik_picks_the_path_nginx_picks(host, request_path):
    routes = ROUTES[host]
    assert traefik_pick(routes, request_path, host) == nginx_pick(routes, request_path)
