"""Redis requires a password where `redis.password` is set, and prod refuses to render
without one (store plan infra/plans/redis-requires-a-password.md, R1, R4, R5).

Redis holds the oauth2-proxy sessions: the access and refresh tokens of every signed-in
user. The password lives in one Secret, `redis-auth`, rendered by the redis release from
`redis.password`; Redis reads it through the chart's `auth.existingSecret`, oauth2-proxy
through `sessionStorage.redis.existingSecret` (env OAUTH2_PROXY_REDIS_PASSWORD by
secretKeyRef). It never appears in a ConfigMap or a pod spec. A changed password changes
a `checksum/redis-auth` annotation on both pods, so a rotation restarts them.

Without `redis.password` outside prod nothing changes: staging's Prefect and Superset still
reach Redis without one until their own clients are wired (plan, phase 1).

    task test:redis

It needs `helmfile`, `helm` and network access (both charts are remote). No secrets: it renders over `envs/dev/values.yaml` plus an
override with a made-up password.
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
SECRET = "redis-auth"
KEY = "password"
PASSWORD = "made-up-redis-password-0123456789"
WITH_PASSWORD = f"redis:\n  password: {PASSWORD}\n"


def render(helmfile: str, release: str, override: str, env: str = "example") -> subprocess.CompletedProcess:
    """Render `release` of `helmfile` over the dev values plus `override`, as environment `env`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / helmfile).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = (f"environments:\n  {env}:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", env, "template", "-q",
             "-l", f"name={release}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


def docs(result: subprocess.CompletedProcess) -> tuple[str, list[dict]]:
    assert result.returncode == 0, result.stderr
    # The redis chart (0.6.3) ends a volumeClaimTemplates line with a tab, which PyYAML refuses.
    text = re.sub(r"[ \t]+$", "", result.stdout, flags=re.MULTILINE)
    return text, [d for d in yaml.safe_load_all(text) if d]


def redis(override: str, env: str = "example") -> tuple[str, list[dict]]:
    return docs(render("0010-core.yaml.gotmpl", "redis", override, env))


def oauth2_proxy(override: str) -> tuple[str, list[dict]]:
    return docs(render("0020-auth.yaml.gotmpl", "oauth2-proxy", override))


def one(items: list[dict], kind: str, name: str | None = None) -> dict:
    (doc,) = [d for d in items if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    return doc


def containers(workload: dict) -> list[dict]:
    spec = workload["spec"]["template"]["spec"]
    return spec.get("containers", []) + spec.get("initContainers", [])


def secret_refs(workload: dict, variable: str) -> list[dict]:
    return [e["valueFrom"]["secretKeyRef"] for c in containers(workload) for e in c.get("env", [])
            if e["name"] == variable and "valueFrom" in e]


def annotation(workload: dict) -> str | None:
    return (workload["spec"]["template"]["metadata"].get("annotations") or {}).get("checksum/redis-auth")


@pytest.fixture(scope="module")
def with_password() -> tuple[str, list[dict]]:
    return redis(WITH_PASSWORD)


@pytest.fixture(scope="module")
def proxy_with_password() -> tuple[str, list[dict]]:
    return oauth2_proxy(WITH_PASSWORD)


def test_the_password_is_in_one_secret(with_password):
    _, items = with_password
    secret = one(items, "Secret", SECRET)
    assert (secret.get("stringData") or {}).get(KEY) == PASSWORD


def test_redis_reads_the_password_from_that_secret(with_password):
    _, items = with_password
    refs = secret_refs(one(items, "StatefulSet"), "REDIS_PASSWORD")
    assert refs and all(r == {"name": SECRET, "key": KEY} for r in refs)


def test_redis_does_not_generate_a_secret_of_its_own(with_password):
    _, items = with_password
    assert [d["metadata"]["name"] for d in items if d.get("kind") == "Secret"] == [SECRET]


def test_oauth2_proxy_reads_the_password_from_that_secret(proxy_with_password):
    _, items = proxy_with_password
    refs = secret_refs(one(items, "Deployment"), "OAUTH2_PROXY_REDIS_PASSWORD")
    assert refs == [{"name": SECRET, "key": KEY}]


def test_the_password_is_only_in_the_secret(with_password, proxy_with_password):
    for _, items in (with_password, proxy_with_password):
        for doc in items:
            if doc.get("kind") != "Secret":
                assert PASSWORD not in yaml.safe_dump(doc), f"{doc['kind']} {doc['metadata']['name']}"


def test_a_rotation_restarts_both_pods(with_password, proxy_with_password):
    rotated = "redis:\n  password: another-made-up-password-9876543210\n"
    before = (annotation(one(with_password[1], "StatefulSet")), annotation(one(proxy_with_password[1], "Deployment")))
    after = (annotation(one(redis(rotated)[1], "StatefulSet")), annotation(one(oauth2_proxy(rotated)[1], "Deployment")))
    assert all(before) and all(after)
    assert before[0] != after[0] and before[1] != after[1]


def test_without_a_password_outside_prod_nothing_changes():
    _, items = redis("{}\n")
    assert not [d for d in items if d.get("kind") == "Secret"]
    assert not secret_refs(one(items, "StatefulSet"), "REDIS_PASSWORD")
    assert annotation(one(items, "StatefulSet")) is None
    _, proxy = oauth2_proxy("{}\n")
    assert not secret_refs(one(proxy, "Deployment"), "OAUTH2_PROXY_REDIS_PASSWORD")
    assert annotation(one(proxy, "Deployment")) is None


def test_prod_refuses_to_render_without_a_password():
    result = render("0010-core.yaml.gotmpl", "redis", "{}\n", env="prod")
    assert result.returncode != 0
    assert "redis.password" in result.stderr
