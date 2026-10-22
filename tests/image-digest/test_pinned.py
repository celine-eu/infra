"""Images pinned by digest where an environment lists one, rendered through helmfile.

A tag can be moved to another image; a digest cannot. An environment lists the digests it
runs in one map, by repository and tag:

    image_digests:
      ghcr.io/celine-eu/dataset-api:
        v1.11.0: sha256:<64 hex>

A container whose `repository:tag` is listed renders as `repository:tag@sha256:…`, so the
tag stays readable and the kubelet pulls the digest. The `app.kubernetes.io/version` label
keeps the bare tag. Keyed by tag on purpose: a tag bumped without its digest renders
unpinned, never the old image under the new tag's name. A listed value that is not a
sha256 digest stops the render. No map, nothing changes.

Third-party charts use their own knobs: keycloakx `image.digest` (renders
`repository@digest`), Traefik `image.tag` as `tag@digest` (the chart strips the digest for
its version checks). Their tags are pinned only where the environment names them
(`ingress_controller.image_tag`, `oauth2_proxy.image_tag`), since the chart's default tag
is not visible here. postgres-db pins its Jobs from the map, the Cluster's image only with
`postgres_db.pin_cluster_image: true` (changing it restarts Postgres), and the pooler's
pgbouncer where `postgres_db.pgbouncer_image_tag` is set.

Not pinnable here: qdrant 1.17.0 (no digest value, and `tag@digest` breaks its
`semverCompare`); the CNPG operator and the pooler's init container (the operator image,
whose change upgrades the instance manager); redis is pinned by its chart already.

    task test:image-digest

It needs `helmfile`, `helm` and network access (keycloak, traefik and oauth2-proxy come
from their repositories). No secrets: it renders over `envs/dev/values.yaml` plus an
override.
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
D = "sha256:" + "a" * 64
E = "sha256:" + "b" * 64
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}


def _state(tmp: Path, helmfile: str, env: str, override: str) -> Path:
    values = yaml.safe_load((REPO / "envs/dev/values.yaml").read_text())
    (tmp / "env.yaml").write_text(yaml.safe_dump(values))
    # Outside dev, the public hosts refuse to render without security headers.
    (tmp / "override.yaml").write_text("security_headers:\n  enabled: true\n" + override)
    source = (REPO / "helmfile.d" / helmfile).read_text()
    source = re.sub(r"^environments:\n(?:  .*\n|    .*\n|      .*\n)*", "", source, flags=re.MULTILINE)
    header = (f"environments:\n  {env}:\n    values:\n"
              f"      - {tmp / 'env.yaml'}\n      - {tmp / 'override.yaml'}\n")
    path = tmp / "helmfile.yaml.gotmpl"
    path.write_text(header + source.replace("../", f"{REPO}/"))
    return path


def run(helmfile: str, release: str, override: str, env: str = "example") -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = _state(tmp, helmfile, env, override)
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        return subprocess.run(
            ["helmfile", "-f", str(state), "-e", env, "template", "-q", "-l", f"name={release}"],
            capture_output=True, text=True, env=environ, timeout=600, check=False,
        )


def render(helmfile: str, release: str, override: str) -> list[dict]:
    result = run(helmfile, release, override)
    assert result.returncode == 0, result.stderr
    text = re.sub(r"[ \t]+$", "", result.stdout, flags=re.MULTILINE)
    return [d for d in yaml.safe_load_all(text) if d]


def images(objects: list[dict]) -> dict[str, str]:
    """`<kind>/<name>/<container>` → image, init containers included."""
    found = {}
    for o in objects:
        if o.get("kind") == "Cluster":
            found[f"Cluster/{o['metadata']['name']}"] = o["spec"]["imageName"]
            continue
        if o.get("kind") == "Pooler":
            for c in ((o["spec"].get("template") or {}).get("spec") or {}).get("containers") or []:
                if c.get("image"):
                    found[f"Pooler/{o['metadata']['name']}/{c['name']}"] = c["image"]
            continue
        if o.get("kind") not in POD_KINDS:
            continue
        spec = o["spec"]
        if o["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        spec = spec["template"]["spec"]
        for c in [*(spec.get("initContainers") or []), *spec["containers"]]:
            found[f"{o['kind']}/{o['metadata']['name']}/{c['name']}"] = c["image"]
    return found


def digests(repository: str, tag: str, digest: str = D) -> str:
    return f"image_digests:\n  {repository}:\n    \"{tag}\": {digest}\n"


# ---------------------------------------------------------------------------
# celine services: the library chart (Deployment and the migrate init container)
# ---------------------------------------------------------------------------

DATASET_API = ("0050-celine-services.yaml.gotmpl", "dataset-api")
DS_REPO = "ghcr.io/celine-eu/dataset-api"
DS_TAG = "dataset_api:\n  image_tag: v9.9.9\n"


def test_library_chart_pins_the_service_and_its_migration():
    objects = render(*DATASET_API, DS_TAG + digests(DS_REPO, "v9.9.9"))
    found = images(objects)
    assert found["Deployment/dataset-api/celine-dataset-api"] == f"{DS_REPO}:v9.9.9@{D}"
    assert found["Deployment/dataset-api/migrate"] == f"{DS_REPO}:v9.9.9@{D}"


def test_version_label_keeps_the_bare_tag():
    objects = render(*DATASET_API, DS_TAG + digests(DS_REPO, "v9.9.9"))
    (deploy,) = [o for o in objects if o["kind"] == "Deployment"]
    assert deploy["metadata"]["labels"]["app.kubernetes.io/version"] == "v9.9.9"


def test_no_map_renders_the_tag_alone():
    found = images(render(*DATASET_API, DS_TAG))
    assert found["Deployment/dataset-api/celine-dataset-api"] == f"{DS_REPO}:v9.9.9"


def test_a_digest_for_another_tag_is_not_used():
    found = images(render(*DATASET_API, DS_TAG + digests(DS_REPO, "v9.9.8")))
    assert found["Deployment/dataset-api/celine-dataset-api"] == f"{DS_REPO}:v9.9.9"


def test_a_value_that_is_not_a_digest_stops_the_render():
    result = run(*DATASET_API, DS_TAG + digests(DS_REPO, "v9.9.9", "latest"))
    assert result.returncode != 0
    assert "image_digests" in result.stderr


# ---------------------------------------------------------------------------
# charts with their own image line
# ---------------------------------------------------------------------------

POLICIES_SHELL = """policies_shell:
  image_tag: v9.9.9
  bootstrap:
    client_secret: 0123456789abcdef0123456789abcdef
"""


@pytest.mark.parametrize("helmfile,release,override,repository,tag,keys", [
    ("0050-celine-services.yaml.gotmpl", "dataset-api-shell", DS_TAG, DS_REPO, "v9.9.9",
     ["Deployment/dataset-api-shell/shell"]),
    ("0050-celine-services.yaml.gotmpl", "rec-registry-shell", "rec_registry:\n  image_tag: v9.9.9\n",
     "ghcr.io/celine-eu/rec-registry", "v9.9.9", ["Deployment/rec-registry-shell/shell"]),
    ("0050-celine-services.yaml.gotmpl", "ai-assistant-shell", "ai_assistant:\n  image_tag: v9.9.9\n",
     "ghcr.io/celine-eu/celine-ai-assistant", "v9.9.9", ["Deployment/ai-assistant-shell/shell"]),
    ("0050-celine-services.yaml.gotmpl", "frontend-webapp", "frontend_webapp:\n  image_tag: 9.9.9\n",
     "ghcr.io/celine-eu/celine-frontend-webapp", "9.9.9", ["Deployment/frontend-webapp/celine-frontend-webapp"]),
    ("0050-celine-services.yaml.gotmpl", "frontend-onboarding",
     "onboarding:\n  enabled: true\n  image_tag: v9.9.9\n",
     "ghcr.io/celine-eu/onboarding-ui", "v9.9.9", ["Deployment/frontend-onboarding/celine-frontend-onboarding"]),
    ("0050-celine-services.yaml.gotmpl", "policies-shell", POLICIES_SHELL,
     "ghcr.io/celine-eu/celine-policies", "v9.9.9",
     ["Deployment/policies-shell/bootstrap", "Deployment/policies-shell/shell"]),
    ("0050-celine-services.yaml.gotmpl", "policies-shell", POLICIES_SHELL,
     "curlimages/curl", "8.21.0", ["Deployment/policies-shell/bootstrap-record"]),
    ("0030-apps.yaml.gotmpl", "mosquitto-go-auth", "",
     "ghcr.io/lhns/mosquitto-go-auth", "3.3.0-mosquitto_2.0.22", ["Deployment/mqtt-mosquitto/mosquitto"]),
])
def test_chart_pins_its_own_image(helmfile, release, override, repository, tag, keys):
    found = images(render(helmfile, release, override + digests(repository, tag)))
    for key in keys:
        assert found[key] == f"{repository}:{tag}@{D}", key


def test_legal_pins_its_image():
    """legal through its chart alone: its environment file (the deployment's legal data)
    is not in this repository; the defaults pass `image_digests` as for the frontends."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "values.yaml").write_text(
            "image:\n  tag: v9.9.9\n" + digests("ghcr.io/celine-eu/celine-legal", "v9.9.9").replace(
                "image_digests", "imageDigests")
            + "data:\n  files:\n    register.yaml: \"documents: []\\n\"\n")
        environ = {**os.environ, "HELM_CONFIG_HOME": str(tmp / "hc"), "HELM_CACHE_HOME": str(tmp / "hcache")}
        chart = REPO / "charts/celine-legal"
        subprocess.run(["helm", "dependency", "build", str(chart)], capture_output=True, env=environ, check=True)
        result = subprocess.run(["helm", "template", "legal", str(chart), "-f", str(tmp / "values.yaml")],
                                capture_output=True, text=True, env=environ, check=False)
    assert result.returncode == 0, result.stderr
    found = images([d for d in yaml.safe_load_all(result.stdout) if d])
    assert found["Deployment/legal/legal"] == f"ghcr.io/celine-eu/celine-legal:v9.9.9@{D}"


def test_unlisted_images_of_the_same_pod_stay_on_their_tag():
    found = images(render("0050-celine-services.yaml.gotmpl", "policies-shell",
                          POLICIES_SHELL + digests("curlimages/curl", "8.21.0")))
    assert "@" not in found["Deployment/policies-shell/shell"]


# ---------------------------------------------------------------------------
# third-party charts
# ---------------------------------------------------------------------------

KEYCLOAK = """keycloak:
  theme:
    login: rec
  image_tag: 9.9.9-1.0.0
"""


def test_keycloak_and_its_dbchecker_pinned():
    override = (KEYCLOAK + "image_digests:\n  ghcr.io/celine-eu/keycloak:\n    \"9.9.9-1.0.0\": " + D
                + "\n  docker.io/busybox:\n    \"1.32\": " + E + "\n")
    found = images(render("0020-auth.yaml.gotmpl", "keycloak", override))
    assert found["StatefulSet/keycloak-keycloakx/keycloak"] == f"ghcr.io/celine-eu/keycloak@{D}"
    assert found["StatefulSet/keycloak-keycloakx/dbchecker"] == f"docker.io/busybox@{E}"


def test_keycloak_unpinned_without_the_map():
    found = images(render("0020-auth.yaml.gotmpl", "keycloak", KEYCLOAK))
    assert found["StatefulSet/keycloak-keycloakx/keycloak"] == "ghcr.io/celine-eu/keycloak:9.9.9-1.0.0"
    assert found["StatefulSet/keycloak-keycloakx/dbchecker"] == "docker.io/busybox:1.32"


def test_traefik_pinned_where_its_tag_is_named():
    override = ("ingress_controller:\n  enabled: true\n  image_tag: v3.7.14\n"
                + digests("docker.io/traefik", "v3.7.14"))
    found = images(render("0010-core.yaml.gotmpl", "traefik", override))
    assert found["Deployment/traefik/traefik"] == f"docker.io/traefik:v3.7.14@{D}"


def test_oauth2_proxy_pinned_where_its_tag_is_named():
    override = ("oauth2_proxy:\n  image_tag: v7.12.0\n"
                + digests("quay.io/oauth2-proxy/oauth2-proxy", "v7.12.0"))
    found = images(render("0020-auth.yaml.gotmpl", "oauth2-proxy", override))
    assert found["Deployment/oauth2-proxy/oauth2-proxy"] == f"quay.io/oauth2-proxy/oauth2-proxy:v7.12.0@{D}"


POSTGRES = """postgres_db:
  sync_environment: prod
  warehouse_tools: [prefect]
  prefect: prefect-pass
  mirror_writer: writer-pass
  mirror:
    enabled: true
    schemas: [ds_dev_gold]
"""
POSTGIS = "ghcr.io/cloudnative-pg/postgis"


def test_postgres_jobs_pinned_cluster_left_alone():
    found = images(render("0010-core.yaml.gotmpl", "postgres-db", POSTGRES + digests(POSTGIS, "17")))
    jobs = {k: v for k, v in found.items() if k.startswith("Job/")}
    assert len(jobs) == 4
    assert set(jobs.values()) == {f"{POSTGIS}:17@{D}"}
    assert found["Cluster/postgis1"] == f"{POSTGIS}:17"


def test_postgres_cluster_pinned_only_when_asked():
    override = POSTGRES + "  pin_cluster_image: true\n" + digests(POSTGIS, "17")
    found = images(render("0010-core.yaml.gotmpl", "postgres-db", override))
    assert found["Cluster/postgis1"] == f"{POSTGIS}:17@{D}"


def test_pooler_pgbouncer_pinned_where_its_tag_is_named():
    override = (POSTGRES + "  pgbouncer_image_tag: 1.24.1\n"
                + digests("ghcr.io/cloudnative-pg/pgbouncer", "1.24.1"))
    found = images(render("0010-core.yaml.gotmpl", "postgres-db", override))
    assert found["Pooler/postgis1-pooler-rw/pgbouncer"] == f"ghcr.io/cloudnative-pg/pgbouncer:1.24.1@{D}"


def test_pooler_has_no_image_without_a_tag():
    found = images(render("0010-core.yaml.gotmpl", "postgres-db", POSTGRES))
    assert not [k for k in found if k.startswith("Pooler/")]
