"""sharing-offers-vocabulary serves the data-sharing offers vocabulary (`GET
/ns/sharing-offers`) while an environment has no dataspace connector.

Onboarding renders its data-sharing consent step from `{DS_NS_URL}/ns/sharing-offers` and
refuses to start when a community declares `consent.data_sharing` with no vocabulary URL.
Where the dataspace is not installed yet (prod, 2026-10-22), this release serves a copy of
the connector's published vocabulary, byte for byte, so the consent is asked and recorded
on the application (DATASPACE_ENABLED stays false) and the text hash means the same thing
it will mean when the connector serves it. Off by default.

- The bytes: `content` reaches the pod unchanged (a ConfigMap's binaryData), served with
  `application/json` at exactly `/ns/sharing-offers`; `/healthz` for the probes; nothing else.
- Pod Security restricted: non-root, read-only root filesystem (an emptyDir for nginx's
  temporary files), no privilege escalation, every capability dropped, RuntimeDefault seccomp.
- Only onboarding's pods reach it (NetworkPolicy), no Ingress, no egress.
- The image is pinned by digest through the environment's `image_digests`.

    task test:sharing-offers-vocabulary

Renders the chart directly (`helm template`) and through helmfile over `envs/dev`: no
secrets, no cluster.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CHART = REPO / "charts" / "sharing-offers-vocabulary"
HELMFILE = "0050-celine-services.yaml.gotmpl"
RELEASE = "sharing-offers-vocabulary"
# Not JSON on purpose at one point: the chart must not parse, reformat or re-encode it.
CONTENT = '[{"id":"incentive-estimation","label":"Stima \\u00e8 incentivi"}]\n'
DIGEST = "sha256:" + "a" * 64


def chart(*sets: str, content: str = CONTENT) -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "offers.json"
        f.write_bytes(content.encode())
        args = ["helm", "template", RELEASE, str(CHART), "--namespace", "celine-example",
                "--set-file", f"content={f}"]
        for s in sets:
            args += ["--set", s]
        out = subprocess.run(args, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def helmfile(override: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
        header = (f"environments:\n  example:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "example", "template", "-q",
             "--skip-deps", "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


@pytest.fixture(scope="module")
def objs() -> dict[str, dict]:
    return chart()


def pod(objs: dict[str, dict]) -> dict:
    return objs[f"Deployment/{RELEASE}"]["spec"]["template"]


def test_the_bytes_reach_the_pod_unchanged(objs) -> None:
    cm = objs[f"ConfigMap/{RELEASE}"]
    assert base64.b64decode(cm["binaryData"]["sharing-offers.json"]) == CONTENT.encode()


def test_only_the_vocabulary_and_the_health_check_are_served(objs) -> None:
    conf = objs[f"ConfigMap/{RELEASE}-nginx"]["data"]["nginx.conf"]
    assert "location = /ns/sharing-offers" in conf
    assert "default_type application/json" in conf
    assert "location = /healthz" in conf
    assert re.search(r"location / \{\s*return 404;", conf)


def test_a_content_change_restarts_the_pod(objs) -> None:
    before = pod(objs)["metadata"]["annotations"]["checksum/content"]
    after = pod(chart(content=CONTENT + " "))["metadata"]["annotations"]["checksum/content"]
    assert before != after


def test_pod_security_restricted(objs) -> None:
    spec = pod(objs)["spec"]
    psc = spec["securityContext"]
    assert psc["runAsNonRoot"] is True and psc["seccompProfile"]["type"] == "RuntimeDefault"
    for c in spec["containers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        assert sc["readOnlyRootFilesystem"] is True
    assert spec["automountServiceAccountToken"] is False
    assert any(v.get("emptyDir") is not None and v["name"] == "tmp" for v in spec["volumes"])


def test_requests_are_set(objs) -> None:
    (c,) = pod(objs)["spec"]["containers"]
    assert c["resources"]["requests"]["cpu"] and c["resources"]["requests"]["memory"]


def test_probes_use_healthz(objs) -> None:
    (c,) = pod(objs)["spec"]["containers"]
    assert c["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert c["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_only_onboarding_reaches_it_and_it_reaches_nothing(objs) -> None:
    np = objs[f"NetworkPolicy/{RELEASE}"]["spec"]
    assert set(np["policyTypes"]) == {"Ingress", "Egress"}
    assert np.get("egress", []) == []
    (rule,) = np["ingress"]
    (peer,) = rule["from"]
    assert peer == {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "celine-onboarding"}}}


def test_a_cluster_ip_service_and_no_ingress(objs) -> None:
    assert objs[f"Service/{RELEASE}"]["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert not [k for k in objs if k.startswith("Ingress/")]


def test_the_image_is_pinned_when_the_environment_lists_it() -> None:
    (c,) = pod(chart(f"imageDigests.docker\\.io/nginxinc/nginx-unprivileged.1\\.29\\.2-alpine={DIGEST}"))["spec"]["containers"]
    assert c["image"] == f"docker.io/nginxinc/nginx-unprivileged:1.29.2-alpine@{DIGEST}"


def test_empty_content_is_refused() -> None:
    out = subprocess.run(["helm", "template", RELEASE, str(CHART)], capture_output=True, text=True, check=False)
    assert out.returncode != 0 and "content" in out.stderr


def test_off_by_default_in_an_environment() -> None:
    out = helmfile("{}\n")
    assert out.returncode == 0, out.stderr
    assert "kind: Deployment" not in out.stdout


def test_an_environment_turns_it_on_with_its_digest() -> None:
    out = helmfile(
        "sharing_offers_vocabulary:\n  enabled: true\n  content: '[]'\n"
        "image_digests:\n  docker.io/nginxinc/nginx-unprivileged:\n"
        f"    1.29.2-alpine: {DIGEST}\n"
    )
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"].endswith(DIGEST)
    (cm,) = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == RELEASE]
    assert base64.b64decode(cm["binaryData"]["sharing-offers.json"]) == b"[]"
