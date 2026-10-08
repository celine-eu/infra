"""The ROI host's Ingresses, rendered through helmfile as a staging-like environment.

- The page and the calculator are public: no Ingress on the host that routes to them
  carries auth-url, and none redirects to sign-in (no auth-signin anywhere on the host).
- The feedback paths (`/api/v1/feedback` and below, `ingress.authPaths`) go through
  oauth2-proxy's auth check without auth-signin: an existing SSO session reaches roi
  as the user's token (auth-response-headers), no session gets a plain 401, not a
  redirect. They keep the API's rate limit and Content-Security-Policy.
- No `/oauth2` route on the host: nobody signs in on roi.<domain>.
- oauth2-proxy's session cookie and redirect whitelist cover the roi host.

    task test:frontend-roi

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml`
without `security_headers`, with a placeholder domain (`example.org`).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DOMAIN = "example.org"
HOST = f"roi.{DOMAIN}"
AUTH_URL = "nginx.ingress.kubernetes.io/auth-url"
AUTH_SIGNIN = "nginx.ingress.kubernetes.io/auth-signin"
AUTH_HEADERS = "nginx.ingress.kubernetes.io/auth-response-headers"
CUSTOM_HEADERS = "nginx.ingress.kubernetes.io/custom-headers"
FEEDBACK = "/api/v1/feedback"
CALCULATOR = ["/api/v1/production", "/api/v1/energy", "/api/v1/incentives", "/api/v1/finance",
              "/api/v1/validate", "/api/v1/scenario", "/api/v1/capex-estimate", "/api/v1/compare"]


def render(helmfile: str, release: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
        values.pop("security_headers", None)
        values["domain"] = DOMAIN
        (tmp / "env.yaml").write_text(yaml.safe_dump(values))
        source = (REPO / "helmfile.d" / helmfile).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = f"environments:\n  staging:\n    values:\n      - {tmp / 'env.yaml'}\n"
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "staging", "template", "-q",
             "-l", f"name={release}", "--skip-tests"],
            capture_output=True, text=True, env=environ, timeout=900, check=False,
        )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.fixture(scope="module")
def ingresses() -> dict[str, dict]:
    docs = render("0050-celine-services.yaml.gotmpl", "frontend-roi")
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "Ingress"}


def routes(ingresses: dict[str, dict]) -> dict[str, tuple[str, dict, str]]:
    """path -> (Ingress name, its annotations, backend service) for every path on the roi host."""
    out = {}
    for name, ing in ingresses.items():
        for rule in ing["spec"]["rules"]:
            assert rule["host"] == HOST, name
            for p in rule["http"]["paths"]:
                assert p["path"] not in out, f"{p['path']} routed twice"
                out[p["path"]] = (name, ing["metadata"].get("annotations") or {}, p["backend"]["service"]["name"])
    return out


def test_the_page_and_calculator_are_public(ingresses):
    r = routes(ingresses)
    for path in ["/", *CALCULATOR]:
        name, annotations, _ = r[path]
        assert AUTH_URL not in annotations, (path, name)
    assert r["/"][2] == "frontend-roi"
    assert {r[p][2] for p in CALCULATOR} == {"roi"}


def test_the_feedback_paths_need_an_existing_session(ingresses):
    name, annotations, backend = routes(ingresses)[FEEDBACK]
    assert name == "frontend-roi-api-auth" and backend == "roi"
    assert ingresses[name]["spec"]["rules"][0]["http"]["paths"][0]["pathType"] == "Prefix"  # and below
    assert annotations[AUTH_URL] == f"https://sso.{DOMAIN}/oauth2/auth"
    headers = {h.strip() for h in annotations[AUTH_HEADERS].split(",")}
    # roi reads X-Auth-Request-Access-Token first, then Authorization: both from the proxy
    assert {"Authorization", "X-Auth-Request-Access-Token"} <= headers


def test_nothing_on_the_host_redirects_to_sign_in(ingresses):
    for name, ing in ingresses.items():
        assert AUTH_SIGNIN not in (ing["metadata"].get("annotations") or {}), name
    assert not [p for p in routes(ingresses) if p.startswith("/oauth2")]


def test_every_api_path_keeps_the_rate_limit_and_the_api_policy(ingresses):
    r = routes(ingresses)
    for path in [FEEDBACK, *CALCULATOR]:
        name, annotations, _ = r[path]
        assert annotations.get("nginx.ingress.kubernetes.io/limit-rps") == "2", path
        assert annotations.get("nginx.ingress.kubernetes.io/limit-connections") == "10", path
        assert annotations.get(CUSTOM_HEADERS) == "celine-staging/frontend-roi-api-security-headers", path
    assert r["/"][1].get(CUSTOM_HEADERS) == "celine-staging/frontend-roi-security-headers"


def test_the_estimates_never_reach_roi_through_the_host(ingresses):
    assert not [p for p in routes(ingresses) if p.startswith("/api/v1/estimates") or p == "/api"]


def test_the_session_cookie_and_redirects_cover_the_roi_host():
    docs = render("0020-auth.yaml.gotmpl", "auth-setup")
    cfg = next(d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "oauth2-proxy-config")
    text = cfg["data"]["oauth2_proxy.cfg"]
    assert f'cookie_domains = [ ".{DOMAIN}" ]' in text
    assert f'whitelist_domains = [ ".{DOMAIN}" ]' in text
    assert 'cookie_samesite = "lax"' in text  # same-site fetch from roi.<domain> carries it
    assert "pass_access_token = true" in text and "set_xauthrequest = true" in text
