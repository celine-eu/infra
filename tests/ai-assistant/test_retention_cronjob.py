"""ai-assistant can run its retention job daily (celine-ai-assistant REQ-0058).

`celine-assistant retention run` deletes members' conversations and own uploads idle for
RETENTION_MONTHS (12, what the published privacy notice promises). It deletes stored
files, which live on the `data` claim (ReadWriteOnce, no UPLOADS_URI): the job mounts that
claim and is scheduled on the node the service pod runs on, the only node that can mount
it beside the service. Its pods must not carry the service's selector labels, or the
Service would route requests to them.

Off by default: the command exists only from the assistant release carrying REQ-0058.

    task test:ai-assistant
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "celine-ai-assistant"


def render(*sets: str) -> list[dict]:
    args = ["helm", "template", "ai-assistant", str(CHART), "--set", "image.tag=v0"]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def cronjob(*sets: str) -> dict:
    (job,) = [d for d in render("retention.enabled=true", *sets) if d["kind"] == "CronJob"]
    return job


def pod(job: dict) -> dict:
    return job["spec"]["jobTemplate"]["spec"]["template"]


def test_off_by_default() -> None:
    assert [d for d in render() if d["kind"] == "CronJob"] == []


def test_runs_retention_daily_one_at_a_time() -> None:
    job = cronjob()
    (container,) = pod(job)["spec"]["containers"]

    assert job["metadata"]["name"] == "ai-assistant-retention"
    assert job["spec"]["schedule"] == "17 3 * * *"
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    assert container["command"] == ["celine-assistant", "retention", "run"]
    assert container.get("args", []) == []
    assert container["image"].endswith(":v0")
    assert pod(job)["spec"]["restartPolicy"] == "Never"


def test_dry_run_and_months_are_passed_on() -> None:
    (container,) = pod(cronjob("retention.dryRun=true", "retention.months=18"))["spec"][
        "containers"
    ]
    assert container["args"] == ["--dry-run", "--months", "18"]


def test_has_the_service_environment() -> None:
    (container,) = pod(cronjob("extraEnv[0].name=QDRANT_URL", "extraEnv[0].value=x"))[
        "spec"
    ]["containers"]
    names = {e["name"] for e in container["env"]}
    assert {"DATABASE_URL", "QDRANT_URL", "LOG_LEVEL"} <= names


def test_mounts_the_uploads_claim_on_the_service_node() -> None:
    spec = pod(cronjob())["spec"]
    (container,) = spec["containers"]

    assert {"name": "data", "mountPath": "/app/data"} in container["volumeMounts"]
    assert {
        "name": "data",
        "persistentVolumeClaim": {"claimName": "ai-assistant-data"},
    } in spec["volumes"]
    (term,) = spec["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert term["topologyKey"] == "kubernetes.io/hostname"
    assert term["labelSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "celine-ai-assistant",
        "app.kubernetes.io/instance": "ai-assistant",
    }


def test_without_the_claim_nothing_is_mounted_or_pinned() -> None:
    spec = pod(cronjob("persistence.data.enabled=false"))["spec"]
    assert "affinity" not in spec
    assert not spec.get("volumes")


def test_its_pods_are_not_the_service_s() -> None:
    labels = pod(cronjob())["metadata"]["labels"]
    selector = {"app.kubernetes.io/name": "celine-ai-assistant", "app.kubernetes.io/instance": "ai-assistant"}
    assert not selector.items() <= labels.items()


def test_runs_unprivileged() -> None:
    spec = pod(cronjob())["spec"]
    (container,) = spec["containers"]
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
