"""postgres-db's mirror for sync_tables (`postgres_db.mirror`), rendered through helmfile
(release `postgres-db`, group `core`).

The sync sink writes copied tables as `mirror_writer`, a role that owns the mirror schemas
and nothing else: no CREATE on the database, no other schema. So the schemas are created
for it, by the database owner, who becomes a member of `mirror_writer` for that purpose
(`CREATE SCHEMA ... AUTHORIZATION` needs it). The same Job gives the readers (dataset-api's
`datasets_ro`) USAGE, SELECT and the default for the tables the sink creates later. Off by
default: an environment that does not set the key renders unchanged.

    task test:postgres-db

The render tests need `helmfile` and `helm`, no secrets: they render over
`envs/dev/values.yaml` plus an override. The behaviour test runs the rendered Job's SQL in
a throwaway Postgres 17 container (`docker`), and is skipped without docker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HELMFILE = "0010-core.yaml.gotmpl"
RELEASE = "postgres-db"
JOB = "postgis1-mirror-schemas"
MIRROR = """postgres_db:
  mirror_writer: writer-pass
  mirror:
    enabled: true
    schemas: [ds_dev_gold, ds_dev_silver]
"""


def run_render(override: str) -> subprocess.CompletedProcess:
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


def render(override: str = "") -> list[dict]:
    result = run_render(override)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def find(docs: list[dict], kind: str, name: str) -> dict | None:
    hits = [d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name]
    return hits[0] if hits else None


def managed_roles(docs: list[dict]) -> dict[str, dict]:
    (cluster,) = [d for d in docs if d.get("kind") == "Cluster"]
    return {r["name"]: r for r in cluster["spec"]["managed"]["roles"]}


def job_script(docs: list[dict]) -> str:
    job = find(docs, "Job", JOB)
    assert job is not None
    return job["spec"]["template"]["spec"]["containers"][0]["command"][2]


# --- off by default --------------------------------------------------------------------


def test_unset_renders_no_mirror_role_job_or_secret():
    docs = render()
    roles = managed_roles(docs)
    assert "mirror_writer" not in roles
    assert "inRoles" not in roles["datasets_owner"]
    assert find(docs, "Job", JOB) is None
    assert find(docs, "Secret", "postgis1-mirror-writer-connection-string") is None
    assert find(docs, "Secret", "mirror-writer-db-secret") is None


# --- on --------------------------------------------------------------------------------


def test_on_declares_a_plain_login_role_and_its_connection_string():
    docs = render(MIRROR)
    role = managed_roles(docs)["mirror_writer"]
    assert role["login"] is True and role["superuser"] is False
    for flag in ("createdb", "createrole", "replication", "bypassrls"):
        assert not role.get(flag), flag
    cs = find(docs, "Secret", "postgis1-mirror-writer-connection-string")
    assert cs is not None
    assert cs["stringData"]["username"] == "mirror_writer"
    assert cs["stringData"]["dbname"] == "datasets"
    assert cs["stringData"]["host"] == "postgis1-rw"


def test_on_makes_the_owner_a_member_of_the_writer_and_nobody_else():
    roles = managed_roles(render(MIRROR))
    assert roles["datasets_owner"]["inRoles"] == ["mirror_writer"]
    assert [n for n, r in roles.items() if r.get("inRoles")] == ["datasets_owner"]


def test_on_renders_the_job_as_the_owner_over_the_listed_schemas_and_readers():
    docs = render(MIRROR)
    job = find(docs, "Job", JOB)
    (vol,) = job["spec"]["template"]["spec"]["volumes"]
    assert vol["secret"]["secretName"] == "postgis1-datasets-connection-string"
    script = job_script(docs)
    assert "ARRAY['ds_dev_gold', 'ds_dev_silver']" in script
    assert "ARRAY['datasets_ro']" in script
    assert "w text := 'mirror_writer'" in script
    assert "writer-pass" not in yaml.safe_dump(job)


def test_on_without_the_password_refuses_to_render():
    result = run_render(MIRROR.replace("  mirror_writer: writer-pass\n", ""))
    assert result.returncode != 0
    assert "postgres_db.mirror_writer" in result.stderr


@pytest.mark.parametrize("schemas", ["[]", "[ds_dev_gold, \"Bad-Name\"]", "[public]", "ds_dev_gold"])
def test_on_refuses_a_bad_schema_list(schemas):
    result = run_render(MIRROR.replace("[ds_dev_gold, ds_dev_silver]", schemas))
    assert result.returncode != 0
    assert "postgres_db.mirror.schemas" in result.stderr


def test_enabled_must_be_a_boolean():
    result = run_render(MIRROR.replace("enabled: true", "enabled: \"yes\""))
    assert result.returncode != 0
    assert "postgres_db.mirror.enabled" in result.stderr


# --- behaviour on a real Postgres ------------------------------------------------------

SETUP = """
CREATE ROLE datasets_owner LOGIN PASSWORD 'o';
CREATE ROLE datasets_ro LOGIN PASSWORD 'r';
CREATE ROLE mirror_writer LOGIN PASSWORD 'w';
GRANT mirror_writer TO datasets_owner;          -- what CNPG does for inRoles
CREATE DATABASE datasets OWNER datasets_owner;
"""


def psql(container: str, sql: str, user: str = "postgres", db: str = "datasets",
         check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=x", container, "psql", "-h", "127.0.0.1",
         "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-tAX"],
        input=sql, capture_output=True, text=True, timeout=60, check=False,
    )
    if check:
        assert r.returncode == 0, r.stderr
    return r


def job_sql(script: str) -> str:
    """The SQL between the heredoc markers of the Job's script."""
    return script.split("<<'SQL'\n", 1)[1].split("\nSQL\n", 1)[0]


@pytest.fixture(scope="module")
def pg():
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True, check=False).returncode:
        pytest.skip("docker not available")
    name = f"mirror-writer-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
         "postgres:17"], check=True, capture_output=True,
    )
    try:
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1"],
                              capture_output=True, check=False).returncode == 0:
                break
            time.sleep(1)
        psql(name, SETUP, db="postgres")
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture(scope="module")
def sql():
    return job_sql(job_script(render(MIRROR)))


def test_job_creates_the_schemas_owned_by_the_writer_and_runs_twice(pg, sql):
    psql(pg, sql, user="datasets_owner")
    psql(pg, sql, user="datasets_owner")  # idempotent
    owners = psql(pg, "SELECT nspname || '=' || nspowner::regrole FROM pg_namespace "
                      "WHERE nspname LIKE 'ds_dev_%' ORDER BY 1;").stdout.split()
    assert owners == ["ds_dev_gold=mirror_writer", "ds_dev_silver=mirror_writer"]


def test_writer_has_no_create_on_the_database_and_no_other_schema(pg, sql):
    psql(pg, sql, user="datasets_owner")
    r = psql(pg, "CREATE SCHEMA elsewhere;", user="mirror_writer", check=False)
    assert r.returncode != 0 and "permission denied" in r.stderr
    r = psql(pg, "CREATE TABLE public.t (i int);", user="mirror_writer", check=False)
    assert r.returncode != 0 and "permission denied" in r.stderr


def test_reader_reads_tables_and_views_the_writer_creates_afterwards(pg, sql):
    psql(pg, sql, user="datasets_owner")
    psql(pg, "CREATE TABLE ds_dev_silver.later (i int); INSERT INTO ds_dev_silver.later VALUES (7);"
             "CREATE VIEW ds_dev_gold.later_view AS SELECT i FROM ds_dev_silver.later;",
         user="mirror_writer")
    assert psql(pg, "SELECT i FROM ds_dev_gold.later_view;", user="datasets_ro").stdout.strip() == "7"
    r = psql(pg, "INSERT INTO ds_dev_silver.later VALUES (8);", user="datasets_ro", check=False)
    assert r.returncode != 0 and "permission denied" in r.stderr


def test_reader_reads_tables_that_existed_before_the_job(pg, sql):
    psql(pg, sql, user="datasets_owner")
    psql(pg, "CREATE TABLE ds_dev_gold.before (i int);", user="mirror_writer")
    psql(pg, "REVOKE SELECT ON ds_dev_gold.before FROM datasets_ro;", user="mirror_writer")
    psql(pg, sql, user="datasets_owner")
    assert psql(pg, "SELECT has_table_privilege('datasets_ro', 'ds_dev_gold.before', 'SELECT');"
                ).stdout.strip() == "t"


def test_job_refuses_a_schema_someone_else_owns(pg, sql):
    psql(pg, "CREATE SCHEMA ds_dev_silver_taken; ", user="datasets_owner")
    taken = sql.replace("'ds_dev_silver'", "'ds_dev_silver_taken'")
    r = psql(pg, taken, user="datasets_owner", check=False)
    assert r.returncode != 0
    assert "ds_dev_silver_taken" in r.stderr and "owned by datasets_owner" in r.stderr


def test_job_fails_until_the_membership_exists(pg, sql):
    psql(pg, "CREATE ROLE lone_owner LOGIN PASSWORD 'l'; GRANT CREATE ON DATABASE datasets TO lone_owner;")
    r = psql(pg, sql, user="lone_owner", check=False)
    assert r.returncode != 0 and "cannot act as mirror_writer" in r.stderr
