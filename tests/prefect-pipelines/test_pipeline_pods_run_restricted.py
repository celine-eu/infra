"""The deploy Jobs, the cleanup Job and the shells under Pod Security `restricted`.

`global.podSecurityContext`, `global.containerSecurityContext`, `global.volumes` and
`global.volumeMounts` reach all three; `global.deployConfigDir` moves prefect.yaml where a
non-root Job can write. All unset: the render is as before. The cleanup Job uses the
Kubernetes project's kubectl image (registry.k8s.io), pinned.

    task test:prefect-pipelines
"""

from __future__ import annotations

from test_flow_runs_read_secrets_by_reference import job_script
from test_flow_secrets_come_from_the_secret import VALUES, render

POD = {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
CTR = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
VOLS = [{"name": "tmp", "emptyDir": {}}]
MOUNTS = [{"name": "tmp", "mountPath": "/tmp"}]
RESTRICTED = {**VALUES, "global": {**VALUES["global"], "podSecurityContext": POD,
                                   "containerSecurityContext": CTR, "volumes": VOLS,
                                   "volumeMounts": MOUNTS, "deployConfigDir": "/tmp/config"}}


def pods(docs: list[dict]) -> dict[str, dict]:
    out = {}
    for d in docs:
        if d["kind"] in ("Job", "Deployment"):
            out[d["metadata"]["name"]] = d["spec"]["template"]["spec"]
    return out


def test_unset_nothing_is_added():
    _, docs = render(VALUES)
    for name, spec in pods(docs).items():
        assert "securityContext" not in spec and "volumes" not in spec, name
        for c in spec["containers"]:
            assert "securityContext" not in c and "volumeMounts" not in c, name
    assert "mkdir -p /config" in job_script(docs)


def test_set_every_pipeline_pod_gets_them():
    _, docs = render(RESTRICTED)
    specs = pods(docs)
    assert {"prefect-deploy-cleanup", "prefect-deploy-mirror", "pipeline-mirror-shell"} <= set(specs)
    for name, spec in specs.items():
        assert spec["securityContext"] == POD, name
        assert spec["volumes"] == VOLS, name
        [c] = spec["containers"]
        assert c["securityContext"] == CTR, name
        assert c["volumeMounts"] == MOUNTS, name


def test_the_deploy_job_writes_prefect_yaml_where_it_may():
    _, docs = render(RESTRICTED)
    script = job_script(docs)
    assert "mkdir -p /tmp/config" in script
    assert "prefect deploy --prefect-file /tmp/config/prefect.yaml" in script
    assert "/config/prefect.yaml" not in script.replace("/tmp/config/prefect.yaml", "")


def test_cleanup_uses_the_official_pinned_kubectl():
    _, docs = render(VALUES)
    [c] = pods(docs)["prefect-deploy-cleanup"]["containers"]
    assert c["image"].startswith("registry.k8s.io/kubectl:v")
    assert c["command"][0] == "/bin/kubectl"
