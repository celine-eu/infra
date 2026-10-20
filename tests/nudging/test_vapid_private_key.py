"""nudging reads its VAPID private key from its Secret, never from a plain `env` value,
rendered through helmfile (release `nudging`, group `celine-services`).

A plain value sits in the Deployment, where anyone who may read Deployments sees it, and
`helmfile diff --suppress-secrets` prints it (prod, 2026-10-19). The key goes into
`nudging-secrets` and the container reads it through `secretKeyRef`, like
CLICK_TRACKING_SECRET. The public key and the subject are not secret and stay plain values.

    task test:nudging

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override with made-up keys.
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
RELEASE = "nudging"
SECRET = "nudging-secrets"
PRIVATE = "made-up-vapid-private-key"
CLICK = "made-up-click-tracking-secret"
VAPID = f"""nudging:
  image_tag: latest
  vapid_public_key: made-up-vapid-public-key
  vapid_private_key: {PRIVATE}
  vapid_subject: mailto:nobody@example.org
"""


def render(override: str) -> tuple[str, list[dict]]:
    """Render nudging over the dev values plus `override`; return the text and its documents."""
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


def env(docs: list[dict]) -> dict[str, dict]:
    (container,) = one(docs, "Deployment")["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e for e in container["env"]}


def test_private_key_comes_from_the_secret():
    _, docs = render(VAPID)
    entry = env(docs)["VAPID_PRIVATE_KEY"]
    assert "value" not in entry
    assert entry["valueFrom"]["secretKeyRef"] == {"name": SECRET, "key": "VAPID_PRIVATE_KEY"}
    assert one(docs, "Secret")["stringData"]["VAPID_PRIVATE_KEY"] == PRIVATE


def test_private_key_appears_only_in_the_secret():
    _, docs = render(VAPID)
    holders = [d["kind"] for d in docs if PRIVATE in yaml.safe_dump(d)]
    assert holders == ["Secret"]


def test_public_key_and_subject_stay_plain_values():
    entries = env(render(VAPID)[1])
    assert entries["VAPID_PUBLIC_KEY"]["value"] == "made-up-vapid-public-key"
    assert entries["VAPID_SUBJECT"]["value"] == "mailto:nobody@example.org"


def test_click_tracking_secret_unchanged_when_set():
    _, docs = render(VAPID + f"  click_tracking_secret: {CLICK}\n")
    assert env(docs)["CLICK_TRACKING_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": SECRET, "key": "CLICK_TRACKING_SECRET"}
    string_data = one(docs, "Secret")["stringData"]
    assert string_data["CLICK_TRACKING_SECRET"] == CLICK
    assert string_data["VAPID_PRIVATE_KEY"] == PRIVATE


def test_click_tracking_secret_absent_when_unset():
    _, docs = render(VAPID)
    assert "CLICK_TRACKING_SECRET" not in env(docs)
    assert "CLICK_TRACKING_SECRET" not in one(docs, "Secret")["stringData"]


def test_no_vapid_keys_still_renders_through_the_secret():
    _, docs = render("nudging:\n  image_tag: latest\n")
    assert "value" not in env(docs)["VAPID_PRIVATE_KEY"]
    assert one(docs, "Secret")["stringData"]["VAPID_PRIVATE_KEY"] == ""
