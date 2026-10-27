"""The frontends' environment, rendered through helmfile.

- Every frontend built from celine-frontend gets `PUBLIC_CELINE_ENV`, derived like
  `CELINE_ENV`: `celine_env`, else the environment's name. Its banner shows unless `prod`.
- Outside prod, with `prod_reads.domain`, it gets `PUBLIC_CELINE_PROD_URL`:
  `https://<its host>.<prod_reads.domain>`, the target of the banner's link.
- In prod, or without `prod_reads.domain`, no `PUBLIC_CELINE_PROD_URL`.
- The assistant keeps `PUBLIC_LEGAL_BASE_URL`.

    task test:frontend-env-banner

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` with a
placeholder domain (`example.org`) and prod domain (`prod.example.net`).
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
PROD_DOMAIN = "prod.example.net"
HOSTS = {
    "frontend-webapp": "webapp",
    "frontend-assistant": "assistant",
    "frontend-community": "community",
    "frontend-grid": "grid",
    "frontend-roi": "roi",
}


def render(environment: str, **overrides) -> dict[str, dict[str, str]]:
    """Release -> its container's env (name -> value), for every frontend in HOSTS."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
        values.pop("security_headers", None)
        values["domain"] = DOMAIN
        values.update(overrides)
        (tmp / "env.yaml").write_text(yaml.safe_dump(values))
        source = (REPO / "helmfile.d/0050-celine-services.yaml.gotmpl").read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = f"environments:\n  {environment}:\n    values:\n      - {tmp / 'env.yaml'}\n"
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        selectors = [arg for release in HOSTS for arg in ("-l", f"name={release}")]
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", environment, "template", "-q",
             *selectors, "--skip-tests"],
            capture_output=True, text=True, env=environ, timeout=900, check=False,
        )
    assert result.returncode == 0, result.stderr
    out = {}
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "Deployment":
            container = doc["spec"]["template"]["spec"]["containers"][0]
            out[doc["metadata"]["name"]] = {e["name"]: e.get("value") for e in container.get("env") or []}
    assert set(out) == set(HOSTS), sorted(out)
    return out


@pytest.fixture(scope="module")
def staging():
    return render("staging", prod_reads={"domain": PROD_DOMAIN},
                  legal={"base_url": "https://legal.example.org"})


def test_staging_names_itself_and_links_prod(staging):
    for release, host in HOSTS.items():
        env = staging[release]
        assert env["PUBLIC_CELINE_ENV"] == "staging", release
        assert env["PUBLIC_CELINE_PROD_URL"] == f"https://{host}.{PROD_DOMAIN}", release


def test_the_assistant_keeps_its_legal_link(staging):
    assert staging["frontend-assistant"]["PUBLIC_LEGAL_BASE_URL"] == "https://legal.example.org"


def test_prod_names_itself_and_links_nowhere():
    for release, env in render("prod", prod_reads={"domain": PROD_DOMAIN}).items():
        assert env["PUBLIC_CELINE_ENV"] == "prod", release
        assert "PUBLIC_CELINE_PROD_URL" not in env, release


def test_without_a_prod_domain_there_is_no_link():
    for release, env in render("staging").items():
        assert env["PUBLIC_CELINE_ENV"] == "staging", release
        assert "PUBLIC_CELINE_PROD_URL" not in env, release


def test_celine_env_overrides_the_name():
    for release, env in render("staging", celine_env="preprod").items():
        assert env["PUBLIC_CELINE_ENV"] == "preprod", release
