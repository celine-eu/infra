"""A flow run reads its secret env by reference: the Prefect deployment stores the
`secretKeyRef`, never the value.

The deploy Job writes `/config/prefect.yaml` and registers it with `prefect deploy`. Its
`job_variables.env` used to carry each secret as `"$NAME"` in an unquoted heredoc, so the
shell wrote the value into the deployment (kept in Prefect's database and copied into
every flow run) and the Job printed it to its log. Now `env` is a Kubernetes env list:
a secret entry is `valueFrom.secretKeyRef`, resolved by the kubelet when the run's pod
starts. The work pool's base job template (`defaults/0040-pipelines/prefect-worker`)
accepts that list.

    task test:prefect-pipelines

It needs `helm`. No secrets: the values are made up.
"""

from __future__ import annotations

import re

import yaml
from test_flow_secrets_come_from_the_secret import (
    FLOW,
    KEY,
    SECRET,
    VALUES,
    container_env,
    flow_workloads,
    render,
    with_extra_secret,
)

HEREDOC = re.compile(r"cat > /config/prefect\.yaml <<'EOF'\n(.*?)\nEOF\n", re.DOTALL)


def deploy_job(docs: list[dict]) -> dict:
    [job] = [d for d in flow_workloads(docs) if d["kind"] == "Job"]
    return job


def job_script(docs: list[dict]) -> str:
    [container] = deploy_job(docs)["spec"]["template"]["spec"]["containers"]
    return container["command"][-1]


def prefect_yaml(docs: list[dict]) -> dict:
    match = HEREDOC.search(job_script(docs))
    assert match, "the deploy Job writes prefect.yaml through a quoted heredoc"
    return yaml.safe_load(match.group(1))


def run_env(docs: list[dict]) -> list[dict]:
    [deployment] = prefect_yaml(docs)["deployments"]
    return deployment["work_pool"]["job_variables"]["env"]


def with_plain_env() -> dict:
    values = with_extra_secret()
    flow = {**values["flows"][0], "env": [
        {"name": "S3_BUCKET", "value": "made-up-bucket"},
        *values["flows"][0]["env"],
    ]}
    return {**values, "flows": [flow]}


def test_the_heredoc_is_quoted_so_the_shell_expands_nothing():
    _, docs = render(with_extra_secret())
    assert "<<'EOF'" in job_script(docs)
    assert "$CELINE_OIDC_CLIENT_SECRET" not in job_script(docs)


def test_a_secret_reaches_the_run_as_a_secret_key_ref():
    _, docs = render(with_extra_secret())
    [entry] = [e for e in run_env(docs) if e["name"] == "CELINE_OIDC_CLIENT_SECRET"]
    assert entry == {
        "name": "CELINE_OIDC_CLIENT_SECRET",
        "valueFrom": {"secretKeyRef": {"name": SECRET, "key": KEY}},
    }


def test_the_flow_entry_replaces_the_global_one_of_the_same_name():
    _, docs = render(with_extra_secret())
    names = [e["name"] for e in run_env(docs)]
    assert names.count("CELINE_OIDC_CLIENT_SECRET") == 1


def test_a_plain_value_stays_a_value():
    _, docs = render(with_plain_env())
    [entry] = [e for e in run_env(docs) if e["name"] == "S3_BUCKET"]
    assert entry == {"name": "S3_BUCKET", "value": "made-up-bucket"}


def test_the_global_env_is_kept_when_no_flow_entry_overrides_it():
    values = with_extra_secret()
    values = {**values, "flows": [{**values["flows"][0], "env": []}]}
    _, docs = render(values)
    [entry] = [e for e in run_env(docs) if e["name"] == "CELINE_OIDC_CLIENT_SECRET"]
    assert entry["valueFrom"]["secretKeyRef"] == {"name": SECRET, "key": "CELINE_OIDC_CLIENT_SECRET"}


def test_the_deploy_job_never_prints_prefect_yaml():
    _, docs = render(with_extra_secret())
    assert "cat /config/prefect.yaml" not in job_script(docs)


def test_the_deploy_job_holds_secrets_only_by_reference():
    # `prefect deploy` imports the flow module, which may read its env: the Job keeps the
    # secretKeyRefs, which put no value in the pod spec.
    _, docs = render(with_extra_secret())
    secret_like = [e for e in container_env(deploy_job(docs)) if e["name"] == "CELINE_OIDC_CLIENT_SECRET"]
    assert secret_like and all("value" not in e for e in secret_like)


def test_an_empty_value_stays_an_empty_value():
    values = with_extra_secret()
    flow = {**values["flows"][0], "env": [{"name": "OPTIONAL_FLAG", "value": ""}]}
    _, docs = render({**values, "flows": [flow]})
    [entry] = [e for e in run_env(docs) if e["name"] == "OPTIONAL_FLAG"]
    assert entry == {"name": "OPTIONAL_FLAG", "value": ""}


def test_without_extra_secrets_the_flow_still_renders_by_reference():
    _, docs = render(VALUES)
    assert all("value" not in e for e in run_env(docs) if e["name"] == "CELINE_OIDC_CLIENT_SECRET")


def test_the_flow_name_is_in_the_registered_deployment():
    _, docs = render(with_extra_secret())
    [deployment] = prefect_yaml(docs)["deployments"]
    assert deployment["name"] == f"{FLOW}-flow"
