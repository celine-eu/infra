"""A flow's secret env comes from the `prefect-pipelines-secrets` Secret, never from a
pod spec (demo3.deployment store plan prod-on-gke, P7: staging's registry and commitments
mirrors read prod as prod's `svc-pipelines`).

`global.extraSecrets` adds keys to that Secret beside `CELINE_OIDC_CLIENT_SECRET`; a flow
reads one with `{name, secret: prefect-pipelines-secrets, key}`. The value must appear
only in the Secret: the shell Deployment and the deploy Job reference it by
secretKeyRef, after the global env, so the flow's value wins.

    task test:prefect-pipelines

It needs `helm`. No secrets: the values are made up.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "prefect-pipelines"
SECRET = "prefect-pipelines-secrets"
KEY = "CELINE_PROD_OIDC_CLIENT_SECRET"
VALUE = "made-up-prod-client-secret-0123456789"
FLOW = "mirror"

VALUES = {
    "global": {
        "namespace": "celine-test",
        "oidcClientSecret": "made-up-own-client-secret",
        "env": [
            {"name": "CELINE_OIDC_CLIENT_SECRET", "secret": SECRET, "key": "CELINE_OIDC_CLIENT_SECRET"},
        ],
    },
    "flows": [
        {
            "name": FLOW,
            "path": "/pipelines/apps/mirror/flows/pipeline.py",
            "callback": "mirror_flow",
            "image": "example/mirror:1",
            "env": [
                {"name": "CELINE_OIDC_CLIENT_SECRET", "secret": SECRET, "key": KEY},
            ],
        }
    ],
}


def render(values: dict) -> tuple[str, list[dict]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "values.yaml"
        path.write_text(yaml.safe_dump(values))
        result = subprocess.run(
            ["helm", "template", "prefect-pipelines", str(CHART), "-f", str(path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout, [d for d in yaml.safe_load_all(result.stdout) if d]


def with_extra_secret() -> dict:
    return {**VALUES, "global": {**VALUES["global"], "extraSecrets": {KEY: VALUE}}}


def the_secret(docs: list[dict]) -> dict:
    [secret] = [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == SECRET]
    return secret


def container_env(doc: dict) -> list[dict]:
    [container] = doc["spec"]["template"]["spec"]["containers"]
    return container["env"]


def flow_workloads(docs: list[dict]) -> list[dict]:
    found = [d for d in docs if d["kind"] in ("Deployment", "Job") and FLOW in d["metadata"]["name"]]
    assert {d["kind"] for d in found} == {"Deployment", "Job"}
    return found


def test_the_extra_key_is_in_the_secret():
    _, docs = render(with_extra_secret())
    assert the_secret(docs)["stringData"][KEY] == VALUE


def test_the_value_appears_only_in_the_secret():
    _, docs = render(with_extra_secret())
    others = [d for d in docs if d is not the_secret(docs)]
    assert VALUE not in yaml.safe_dump_all(others)


@pytest.mark.parametrize("kind", ["Deployment", "Job"])
def test_the_flow_reads_it_by_reference_after_the_global_env(kind):
    _, docs = render(with_extra_secret())
    [doc] = [d for d in flow_workloads(docs) if d["kind"] == kind]
    refs = [e for e in container_env(doc) if e["name"] == "CELINE_OIDC_CLIENT_SECRET"]
    assert [e.get("valueFrom", {}).get("secretKeyRef") for e in refs] == [
        {"name": SECRET, "key": "CELINE_OIDC_CLIENT_SECRET"},
        {"name": SECRET, "key": KEY},
    ]
    assert all("value" not in e for e in refs)


def test_without_extra_secrets_the_secret_is_unchanged():
    _, docs = render(VALUES)
    assert the_secret(docs)["stringData"] == {"CELINE_OIDC_CLIENT_SECRET": "made-up-own-client-secret"}
