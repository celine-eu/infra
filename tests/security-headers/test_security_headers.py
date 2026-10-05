"""Security headers of the public hosts, rendered through helmfile.

- Outside dev every public host - the frontends, the legal host, the API gateway - gets
  its response headers from ingress-nginx: a `<release>-security-headers` ConfigMap and
  the `custom-headers` annotation naming it on each Ingress of the host.
- The headers: X-Content-Type-Options, Referrer-Policy, X-Frame-Options,
  Permissions-Policy, Strict-Transport-Security where the Ingress terminates TLS, and a
  Content-Security-Policy whose origins come from `domain`.
- An Ingress whose backend sets its own headers (the assistant, community and
  onboarding APIs) carries no annotation, so the controller does not replace them.
- The SvelteKit frontends send their own Content-Security-Policy, with a per-request
  nonce for the inline bootstrap: the controller would replace it, so their ConfigMap
  carries no enforced policy - webapp, assistant, community and onboarding none at all,
  grid and roi a Report-Only trial of the other directives, without script-src or
  default-src. No rendered policy allows 'unsafe-inline' scripts.
- `security_headers.enabled: false` (envs/dev) renders neither; any other environment
  refuses it. `security_headers.csp.<release>` switches a host to Report-Only and
  overrides directives.
- Every value passes the controller's check for custom header values.

    task test:security-headers

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml`, or a
copy without `security_headers` for the staging-like environment, plus an override. The
values are placeholders (`example.org`), never a deployment's.
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
HELMFILE = "0050-celine-services.yaml.gotmpl"
DOMAIN = "example.org"
ENABLE = (
    "onboarding:\n  enabled: true\n"
    "legal:\n  enabled: true\n  image: registry.example.org/legal-site\n"
)

# release -> the Ingresses of its host that must NOT carry the gateway's headers
RELEASES = {
    "frontend-webapp": {"frontend-webapp-assistant"},
    "frontend-assistant": {"frontend-assistant-api"},
    "frontend-community": {"frontend-community-api"},
    "frontend-grid": set(),
    "frontend-roi": set(),
    "frontend-onboarding": {"frontend-onboarding-api", "frontend-onboarding-api-auth"},
    "legal": set(),
    "api-gateway": {"api-gateway-own-headers"},
}
# releases whose app sends its own Content-Security-Policy -> the ingress's Report-Only trial
SENT_BY_APP = {
    "frontend-webapp": False,
    "frontend-assistant": False,
    "frontend-community": False,
    "frontend-onboarding": False,
    "frontend-grid": True,
    "frontend-roi": True,
}
# Ingress -> the ConfigMap of its own, for an API Ingress whose policy is not the page's
OWN_CONFIGMAP = {"frontend-roi-api": "frontend-roi-api-security-headers"}
CONFIGMAP = {r: f"{'legal' if r == 'legal' else r}-security-headers" for r in RELEASES}

# ingress-nginx internal/ingress/annotations/customheaders: what a header value may hold
VALUE_OK = re.compile(r"""^[a-zA-Z\d_ :;.,\\/"'?!(){}\[\]@<>=\-+*#$&`|~^%]+$""")


def render(env: str, override: str = "", drop_security_headers: bool = False) -> subprocess.CompletedProcess:
    """`helmfile template` the public hosts' releases as environment `env`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
        if drop_security_headers:
            values.pop("security_headers", None)
        (tmp / "env.yaml").write_text(yaml.safe_dump(values))
        (tmp / "override.yaml").write_text(ENABLE + (override or ""))
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = (f"environments:\n  {env}:\n    values:\n"
                  f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        selectors = [arg for r in RELEASES for arg in ("-l", f"name={r}")]
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", env, "template", "-q",
             *selectors, "--skip-tests"],
            capture_output=True, text=True, env=environ, timeout=900, check=False,
        )


def docs_of(result: subprocess.CompletedProcess) -> list[dict]:
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def of_kind(docs: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == kind}


def host_of(ingress: dict) -> str:
    return ingress["spec"]["rules"][0]["host"]


def csp_of(data: dict) -> dict[str, str]:
    value = data.get("Content-Security-Policy") or data.get("Content-Security-Policy-Report-Only")
    out = {}
    for part in value.split(";"):
        name, _, sources = part.strip().partition(" ")
        out[name] = sources
    return out


@pytest.fixture(scope="module")
def staging() -> list[dict]:
    return docs_of(render("staging", f"domain: {DOMAIN}\n", drop_security_headers=True))


@pytest.fixture(scope="module")
def dev() -> list[dict]:
    return docs_of(render("dev"))


# ---------------------------------------------------------------------------
# outside dev: on by default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("release", RELEASES)
def test_every_public_host_gets_its_headers_configmap(release, staging):
    data = of_kind(staging, "ConfigMap")[CONFIGMAP[release]]["data"]
    assert data["X-Content-Type-Options"] == "nosniff"
    assert data["Referrer-Policy"] in {"strict-origin-when-cross-origin", "no-referrer"}
    assert data["X-Frame-Options"] == "DENY"
    assert "camera=" in data["Permissions-Policy"] and "microphone=()" in data["Permissions-Policy"]
    assert data["Strict-Transport-Security"].startswith("max-age=31536000")
    if release not in SENT_BY_APP:
        assert csp_of(data)["frame-ancestors"] == "'none'"


@pytest.mark.parametrize("release", SENT_BY_APP)
def test_a_host_whose_app_sends_its_policy_gets_no_enforced_one(release, staging):
    data = of_kind(staging, "ConfigMap")[CONFIGMAP[release]]["data"]
    assert "Content-Security-Policy" not in data
    if SENT_BY_APP[release]:
        csp = csp_of(data)
        assert "script-src" not in csp and "default-src" not in csp
        assert csp["frame-ancestors"] == "'none'"
    else:
        assert "Content-Security-Policy-Report-Only" not in data


@pytest.mark.parametrize("ingress", OWN_CONFIGMAP)
def test_an_api_ingress_beside_an_app_sent_policy_keeps_an_enforced_one(ingress, staging):
    data = of_kind(staging, "ConfigMap")[OWN_CONFIGMAP[ingress]]["data"]
    assert "Content-Security-Policy-Report-Only" not in data
    assert csp_of(data) == {"default-src": "'none'", "frame-ancestors": "'none'"}
    assert data["X-Frame-Options"] == "DENY" and data["X-Content-Type-Options"] == "nosniff"
    assert data["Strict-Transport-Security"].startswith("max-age=31536000")


@pytest.mark.parametrize("release", RELEASES)
def test_no_policy_allows_inline_scripts(release, staging):
    data = of_kind(staging, "ConfigMap")[CONFIGMAP[release]]["data"]
    if "Content-Security-Policy" in data or "Content-Security-Policy-Report-Only" in data:
        csp = csp_of(data)
        for name in ("script-src", "script-src-elem", "default-src"):
            assert "'unsafe-inline'" not in csp.get(name, ""), (release, name)


@pytest.mark.parametrize("release", RELEASES)
def test_header_values_are_ones_the_controller_accepts(release, staging):
    for name, value in of_kind(staging, "ConfigMap")[CONFIGMAP[release]]["data"].items():
        assert VALUE_OK.match(value), (name, value)
        assert re.match(r"^[a-zA-Z\d\-_]+$", name)


@pytest.mark.parametrize("release", RELEASES)
def test_every_ingress_of_the_host_names_the_configmap_except_self_managed_backends(release, staging):
    ingresses = of_kind(staging, "Ingress")
    cm = of_kind(staging, "ConfigMap")[CONFIGMAP[release]]
    host = next(host_of(i) for n, i in ingresses.items() if n.startswith(CONFIGMAP[release].removesuffix("-security-headers")))
    on_host = {n: i for n, i in ingresses.items() if host_of(i) == host}
    assert set(RELEASES[release]) <= set(on_host)
    for name, ingress in on_host.items():
        annotation = ingress["metadata"]["annotations"].get("nginx.ingress.kubernetes.io/custom-headers")
        if name in RELEASES[release]:
            assert annotation is None, name
        elif name.startswith(CONFIGMAP[release].removesuffix("-security-headers")):
            expected = OWN_CONFIGMAP.get(name, cm["metadata"]["name"])
            assert annotation == f"celine-staging/{expected}", name


def test_the_api_gateway_keeps_every_route_across_its_two_ingresses(staging):
    ingresses = of_kind(staging, "Ingress")
    paths = [p["path"] for n in ("api-gateway", "api-gateway-own-headers")
             for p in ingresses[n]["spec"]["rules"][0]["http"]["paths"]]
    assert "/ai-assistant(/|$)(.*)" in [p["path"] for p in ingresses["api-gateway-own-headers"]["spec"]["rules"][0]["http"]["paths"]]
    assert len(paths) == len(set(paths)) == 7


def test_csp_origins_come_from_the_domain(staging):
    data = of_kind(staging, "ConfigMap")["frontend-grid-security-headers"]["data"]
    assert csp_of(data)["form-action"] == f"'self' https://sso.{DOMAIN} https://keycloak.{DOMAIN}"
    assert "{{" not in yaml.safe_dump([d for d in staging if d.get("kind") == "ConfigMap"])


# ---------------------------------------------------------------------------
# dev: off; elsewhere it cannot be turned off
# ---------------------------------------------------------------------------


def test_dev_renders_no_headers(dev):
    assert not [n for n in of_kind(dev, "ConfigMap") if n.endswith("-security-headers")]
    for name, ingress in of_kind(dev, "Ingress").items():
        assert "nginx.ingress.kubernetes.io/custom-headers" not in (ingress["metadata"].get("annotations") or {}), name
    assert "api-gateway-own-headers" not in of_kind(dev, "Ingress")


def test_turning_them_off_outside_dev_stops_the_render():
    result = render("staging", f"domain: {DOMAIN}\nsecurity_headers:\n  enabled: false\n")
    assert result.returncode != 0
    assert "only the dev environment may serve the public hosts without security headers" in result.stderr


# ---------------------------------------------------------------------------
# per host: report-only and directive overrides
# ---------------------------------------------------------------------------


def test_a_host_can_report_only_and_override_directives():
    docs = docs_of(render("staging", f"domain: {DOMAIN}\nsecurity_headers:\n  csp:\n    legal:\n"
                                     "      report_only: true\n      directives:\n"
                                     "        img-src: \"'self' https://img.{{ .Values.ingress.host }}\"\n"
                                     "        form-action: null\n",
                          drop_security_headers=True))
    data = of_kind(docs, "ConfigMap")["legal-security-headers"]["data"]
    assert "Content-Security-Policy" not in data
    csp = csp_of(data)
    assert csp["img-src"] == f"'self' https://img.legal.{DOMAIN}"
    assert "form-action" not in csp
    assert csp["default-src"] == "'none'"  # the chart's other directives stay
    other = of_kind(docs, "ConfigMap")["api-gateway-security-headers"]["data"]
    assert "Content-Security-Policy" in other  # the other hosts keep enforcing


@pytest.mark.parametrize("override, message", [
    ("    frontend-webapp:\n      directives:\n        img-src: \"'self'\"\n",
     "only a Report-Only one may be set here"),
    ("    frontend-grid:\n      report_only: false\n",
     "only a Report-Only one may be set here"),
    ("    frontend-roi:\n      directives:\n        script-src: \"'self' 'unsafe-inline'\"\n",
     "may not set script-src"),
])
def test_a_host_whose_app_sends_its_policy_refuses_one_that_would_replace_or_report_it(override, message):
    result = render("staging", f"domain: {DOMAIN}\nsecurity_headers:\n  csp:\n" + override,
                    drop_security_headers=True)
    assert result.returncode != 0
    assert message in result.stderr
