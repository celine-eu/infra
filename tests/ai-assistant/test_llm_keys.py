"""The AI assistant reads its LLM API keys from its Secret, never from plain `env` values,
rendered through helmfile (releases `ai-assistant` and `ai-assistant-shell`, group
`celine-services`).

A plain value sits in the Deployment, where anyone who may read Deployments sees it, and
`helmfile diff --suppress-secrets` prints it (the nudging VAPID key, prod, 2026-10-19).
`LLM_API_KEY` and `LLM_EMBED_API_KEY` go into `ai-assistant-secrets` and both containers read
them through `secretKeyRef`, like KB_GIT_TOKEN and QDRANT_API_KEY. The shell has no Secret of
its own: it reads the assistant's. Endpoints, models and limits are not secret and stay
plain values.

    task test:ai-assistant

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override with made-up keys.
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
RELEASES = ["ai-assistant", "ai-assistant-shell"]
SECRET = "ai-assistant-secrets"
CHAT_KEY = "made-up-llm-chat-key"
EMBED_KEY = "made-up-llm-embed-key"
KEYS = {"LLM_API_KEY": CHAT_KEY, "LLM_EMBED_API_KEY": EMBED_KEY}
LLM = f"""ai_assistant:
  image_tag: latest
  llm:
    base_url: https://llm.example.org/v1
    api_key: {CHAT_KEY}
    chat_model: chat
    vision_model: chat
    embed_base_url: https://llm.example.org/v1
    embed_api_key: {EMBED_KEY}
    embed_model: bge-m3
    embed_dimensions: 1024
"""


def render(release: str, override: str) -> list[dict]:
    """Render `release` over the dev values plus `override`; return its documents."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(
            r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*",
            "",
            source,
            flags=re.MULTILINE,
        )
        header = (
            f"environments:\n  example:\n    values:\n"
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
                "example",
                "template",
                "-q",
                "--skip-deps",
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


def deployment_env(docs: list[dict]) -> dict[str, dict]:
    (deployment,) = [d for d in docs if d["kind"] == "Deployment"]
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e for c in containers for e in c.get("env", [])}


def secret_data(docs: list[dict]) -> dict[str, str]:
    (secret,) = [
        d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == SECRET
    ]
    return secret.get("stringData") or {}


@pytest.mark.parametrize("release", RELEASES)
@pytest.mark.parametrize("name", sorted(KEYS))
def test_llm_keys_come_from_the_secret(release, name):
    entry = deployment_env(render(release, LLM))[name]
    assert "value" not in entry
    assert entry["valueFrom"]["secretKeyRef"] == {"name": SECRET, "key": name}


def test_the_assistants_secret_holds_the_keys():
    assert {k: secret_data(render("ai-assistant", LLM)).get(k) for k in KEYS} == KEYS


@pytest.mark.parametrize("release", RELEASES)
def test_keys_appear_only_in_the_secret(release):
    holders = [
        d["kind"]
        for d in render(release, LLM)
        if any(v in yaml.safe_dump(d) for v in KEYS.values())
    ]
    assert holders == (["Secret"] if release == "ai-assistant" else [])


@pytest.mark.parametrize("release", RELEASES)
def test_endpoints_and_models_stay_plain_values(release):
    entries = deployment_env(render(release, LLM))
    assert entries["LLM_BASE_URL"]["value"] == "https://llm.example.org/v1"
    assert entries["LLM_EMBED_BASE_URL"]["value"] == "https://llm.example.org/v1"
    assert entries["LLM_CHAT_MODEL"]["value"] == "chat"
    assert entries["LLM_EMBED_MODEL"]["value"] == "bge-m3"
    assert entries["LLM_EMBED_DIMENSIONS"]["value"] == "1024"


def test_no_keys_still_renders_through_the_secret():
    docs = render("ai-assistant", "ai_assistant:\n  image_tag: latest\n")
    assert "value" not in deployment_env(docs)["LLM_API_KEY"]
    assert secret_data(docs)["LLM_API_KEY"] == ""


def test_kb_git_token_and_qdrant_key_unchanged_when_set():
    override = (
        LLM
        + "  kb:\n    git_token: made-up-git-token\nqdrant:\n  api_key: made-up-qdrant-key\n"
    )
    docs = render("ai-assistant", override)
    entries = deployment_env(docs)
    for name in ("KB_GIT_TOKEN", "QDRANT_API_KEY"):
        assert entries[name]["valueFrom"]["secretKeyRef"] == {
            "name": SECRET,
            "key": name,
        }
    data = secret_data(docs)
    assert data["KB_GIT_TOKEN"] == "made-up-git-token"
    assert data["QDRANT_API_KEY"] == "made-up-qdrant-key"
    assert data["LLM_API_KEY"] == CHAT_KEY
