"""rec-registry hashes PODs with `POD_HMAC_KEY`, read from its Secret, and only where the
environment sets `rec_registry.pod_hmac_key` (rec-registry REQ-0105).

The key stays in prod: whoever holds it can rebuild every POD from its hash, so an
environment without the value renders no variable and the registry exports no hashes. The
container reads the key through `secretKeyRef`, never as a plain value in the Deployment.

    task test:rec-registry

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override with a made-up key.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HELMFILE = "0050-celine-services.yaml.gotmpl"
RELEASE = "rec-registry"
KEY = "made-up-pod-hmac-key-of-at-least-32-characters"


def render(override: str) -> tuple[str, list[dict]]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = (f"environments:\n  example:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "example", "template", "-q",
             "--skip-deps", "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout, [d for d in yaml.safe_load_all(result.stdout) if d]


def one(docs: list[dict], kind: str) -> dict:
    (doc,) = [d for d in docs if d.get("kind") == kind]
    return doc


def env_of(docs: list[dict]) -> list[dict]:
    (container,) = one(docs, "Deployment")["spec"]["template"]["spec"]["containers"]
    return container.get("env") or []


WITH_KEY = f"rec_registry:\n  image_tag: latest\n  pod_hmac_key: {KEY}\n"
WITHOUT_KEY = "rec_registry:\n  image_tag: latest\n"


def test_the_key_is_read_from_the_secret():
    text, docs = render(WITH_KEY)
    secret = one(docs, "Secret")

    (entry,) = [e for e in env_of(docs) if e["name"] == "POD_HMAC_KEY"]

    assert "value" not in entry
    assert entry["valueFrom"]["secretKeyRef"] == {
        "name": secret["metadata"]["name"],
        "key": "POD_HMAC_KEY",
    }
    assert secret["stringData"]["POD_HMAC_KEY"] == KEY
    assert text.count(KEY) == 1


def test_without_the_value_nothing_is_rendered():
    text, docs = render(WITHOUT_KEY)

    assert [e for e in env_of(docs) if e["name"] == "POD_HMAC_KEY"] == []
    assert "POD_HMAC_KEY" not in text


def test_the_chart_default_env_is_kept():
    """Setting `extraEnv` in the defaults replaces the chart's list: its entry must survive."""
    _, docs = render(WITH_KEY)

    names = [e["name"] for e in env_of(docs)]
    assert "REC_REGISTRY_URL" in names
