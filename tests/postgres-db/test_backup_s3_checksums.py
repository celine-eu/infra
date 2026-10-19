"""postgres-db's S3-compatible backups can ask barman's S3 client for checksums only where
the server requires them (`postgres_db.backup.s3.checksums`), rendered through helmfile
(release `postgres-db`, group `core`).

Recent boto3 releases add request checksums that some S3-compatible stores refuse: GCS's
XML API answered `SignatureDoesNotMatch` on every WAL `PutObject` (prod, 2026-10-19).
`checksums: when_required` sets AWS_REQUEST_CHECKSUM_CALCULATION and
AWS_RESPONSE_CHECKSUM_VALIDATION on the Postgres pods. Unset, the Cluster has no such
environment, so an environment that does not set the key renders unchanged.

    task test:postgres-db

It needs `helmfile` and `helm`. No secrets: it renders over `envs/dev/values.yaml` plus an
override.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HELMFILE = "0010-core.yaml.gotmpl"
RELEASE = "postgres-db"
S3 = """postgres_db:
  backup:
    enabled: true
    destinationPath: s3://bucket
    s3:
      endpointURL: https://storage.example.org
      accessKey: id
      secretKey: key
"""


def render(override: str) -> dict:
    """Render postgres-db over the dev values plus `override`; return its Cluster."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "override.yaml").write_text(override)
        source = (REPO / "helmfile.d" / HELMFILE).read_text()
        source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.M)
        header = (f"environments:\n  example:\n    values:\n"
                  f"      - {REPO}/envs/dev/values.yaml\n      - {tmp / 'override.yaml'}\n")
        (tmp / "helmfile.yaml.gotmpl").write_text(header + source.replace("../", f"{REPO}/"))
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        result = subprocess.run(
            ["helmfile", "-f", str(tmp / "helmfile.yaml.gotmpl"), "-e", "example", "template", "-q",
             "--skip-deps", "-l", f"name={RELEASE}"],
            capture_output=True, text=True, env=environ, timeout=600,
        )
    assert result.returncode == 0, result.stderr
    (cluster,) = [d for d in yaml.safe_load_all(result.stdout) if d and d.get("kind") == "Cluster"]
    return cluster


def env(cluster: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in cluster["spec"].get("env") or []}


def test_unset_leaves_the_cluster_without_checksum_environment():
    assert not {k for k in env(render(S3)) if k.startswith("AWS_")}


def test_when_required_sets_both_variables():
    cluster = render(S3.replace("      secretKey: key\n", "      secretKey: key\n      checksums: when_required\n"))
    assert env(cluster)["AWS_REQUEST_CHECKSUM_CALCULATION"] == "when_required"
    assert env(cluster)["AWS_RESPONSE_CHECKSUM_VALIDATION"] == "when_required"
    assert cluster["spec"]["backup"]["barmanObjectStore"]["endpointURL"] == "https://storage.example.org"


def test_backups_off_render_no_backup_and_no_environment():
    cluster = render("postgres_db:\n  backup:\n    enabled: false\n    s3:\n      checksums: when_required\n")
    assert "backup" not in cluster["spec"] and not env(cluster)
