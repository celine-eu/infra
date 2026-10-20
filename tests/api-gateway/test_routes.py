"""Every route of the API gateway (chart `api-gateway`, `api.<domain>/<name>`) points at a
port its Service exposes.

The gateway's table named ports of its own: digital-twin 8005 and roi 8018, where the
Services expose 8002 and 8000. The controller found no such port and answered with an
error on those paths, on staging and prod alike ("service port not found", prod Traefik,
2026-10-20). Each backend's Service comes from the release of the same name in
`helmfile.d/`, rendered from its chart.

    task test:api-gateway

It needs `helm`. No cluster, no secrets.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "charts/api-gateway"


def helm_template(release: str, chart: Path, *extra: str) -> list[dict]:
    result = subprocess.run(
        [
            "helm",
            "template",
            release,
            str(chart),
            "--set",
            "domain=example.org",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def release_charts() -> dict[str, Path]:
    """Release name -> chart directory, from every helmfile in helmfile.d/."""
    charts = {}
    for helmfile in (REPO / "helmfile.d").glob("*.gotmpl"):
        name = None
        for line in helmfile.read_text().splitlines():
            if m := re.match(r"\s+- name: (\S+)\s*$", line):
                name = m.group(1)
            elif (m := re.match(r"\s+chart: \.\./(charts/\S+)\s*$", line)) and name:
                charts[name] = REPO / m.group(1)
    return charts


def gateway_routes() -> list[tuple[str, str, int]]:
    """(path, service, port) of every Ingress backend the gateway renders."""
    routes = []
    for doc in helm_template("api-gateway", GATEWAY):
        if doc["kind"] != "Ingress":
            continue
        for rule in doc["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                service = path["backend"]["service"]
                routes.append(
                    (path["path"], service["name"], service["port"]["number"])
                )
    return routes


def service_ports(release: str, chart: Path) -> set[int]:
    # Some charts carry no `s3` block of their own; helmfile's defaults supply one.
    docs = helm_template(
        release,
        chart,
        "--set",
        "s3.endpointUrl=",
        "--show-only",
        "templates/service.yaml",
    )
    (service,) = [d for d in docs if d["kind"] == "Service"]
    assert service["metadata"]["name"] == release
    return {p["port"] for p in service["spec"]["ports"]}


ROUTES = gateway_routes()


def test_the_gateway_has_routes():
    assert ROUTES


@pytest.mark.parametrize(
    ("path", "service", "port"), ROUTES, ids=[r[0] for r in ROUTES]
)
def test_route_points_at_a_port_its_service_exposes(path, service, port):
    charts = release_charts()
    assert service in charts, f"{path}: no release named {service} in helmfile.d/"
    assert port in service_ports(service, charts[service]), (
        f"{path} -> {service}:{port}"
    )
