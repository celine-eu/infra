"""The environment keys of run events (sync_tables 0.8.0) reach the sync-tables chart.

`sync_tables.push_source.edges` / `push_targets.<key>.edges`, `sink.accept_from`,
`sink.events`, `serve.serve_to`, `relay` and `relays.<key>` map to the chart's keys
(defaults/0050-celine-services/sync-tables/values.yaml.gotmpl), and the release is
installed where only relays are on. Rendered through helmfile over `envs/dev/values.yaml`
plus an override with throwaway material, as test_sink_stays_through_a_push does.

    task test:sync-tables
"""

from __future__ import annotations

import pytest
import yaml

from test_sink_stays_through_a_push import render

TOKEN = "0123456789abcdef0123456789abcdef"
PROFILE = "sources:\n  - {schema: gold, table: readings}\n"
TLS = {"ca": "CA", "client_cert": "CERT", "client_key": "KEY", "token": TOKEN}


def override(st: dict) -> str:
    return yaml.safe_dump({"sync_tables": {"image": "ghcr.io/celine-eu/sync-tables:0.8.0", **st}})


SINK = {"enabled": True, "host": "sync.example.org", "ingress_flavor": "nginx",
        "dsn_secret": {"name": "mirror", "key": "uri"}, "allow_schemas": ["gold"],
        "tokens": {"warehouse": TOKEN}, "ca": "CA", "server_cert": "CERT", "server_key": "KEY"}
SOURCE = {"source_secret": {"name": "wh-ro", "key": "uri"},
          "database_pod_selector": {"cnpg.io/cluster": "wh"}}


def env_of(objects: dict, name: str) -> dict:
    spec = objects[name]["spec"]
    pod = spec["jobTemplate"]["spec"]["template"]["spec"] if name.startswith("CronJob/") else spec["template"]["spec"]
    [c] = pod["containers"]
    return {"args": c["args"], **{e["name"]: e for e in c.get("env", [])}}


@pytest.fixture(scope="module")
def sink_events() -> dict:
    return render(override({"sink": {**SINK, "accept_from": ["dev"], "events": {
        "enabled": True, "env": "staging",
        "mqtt": {"host": "mqtt.example.org", "port": 8883, "use_tls": True},
        "oidc": {"base_url": "https://auth.example.org/realms/celine",
                 "client_secret": {"name": "sink-oidc", "key": "secret"}}}}}))


def test_accept_from_and_events_reach_the_sink(sink_events):
    e = env_of(sink_events, "Deployment/sync-tables-sink")
    assert "--events" in e["args"]
    assert e["SYNC_ACCEPT_FROM"]["value"] == "dev"
    assert e["CELINE_ENV"]["value"] == "staging"
    assert e["CELINE_MQTT_HOST"]["value"] == "mqtt.example.org"
    assert e["CELINE_MQTT_USE_TLS"]["value"] == "true"
    assert e["CELINE_OIDC_CLIENT_ID"]["value"] == "svc-sync-tables-sink"
    assert e["CELINE_OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {"name": "sink-oidc", "key": "secret"}


def test_push_target_edges_reach_the_push():
    objects = render(override({"push_source": SOURCE, "push_targets": {"prod": {
        "to": "prod", "sink_url": "wss://sync.prod.example.org/sync/v1", "profile": PROFILE, **TLS,
        "edges": [{"from": "dev", "to": "prod", "transports": ["push"]}]}}}))
    assert "--config" in env_of(objects, "CronJob/sync-tables-push-prod")["args"]


@pytest.fixture(scope="module")
def relays_only() -> dict:
    return render(override({"push_source": SOURCE,
                            "relay": {"source_env": "dev", "flows_namespace": "pipelines", "min_interval": "20m"},
                            "relays": {"staging": {"sink_url": "wss://sync.staging.example.org/sync/v1",
                                                   "profile": PROFILE, **TLS}}}))


def test_relays_alone_install_the_release(relays_only):
    assert "Deployment/sync-tables-relay-staging" in relays_only
    assert "Service/sync-tables-relay-staging" in relays_only


def test_relay_keys_reach_the_config(relays_only):
    cfg = yaml.safe_load(relays_only["ConfigMap/sync-tables-relay-config"]["data"]["config.yaml"])
    assert cfg["source"]["env"] == "dev"
    assert cfg["defaults"]["min_interval"] == "20m"
    assert cfg["sinks"]["staging"]["url"] == "wss://sync.staging.example.org/sync/v1"
    np = relays_only["NetworkPolicy/sync-tables-relay-staging"]
    assert np["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "pipelines"}


def test_an_unknown_relay_key_stops_the_render():
    with pytest.raises(AssertionError, match="unknown key"):
        render(override({"push_source": SOURCE, "relay": {"source_env": "dev", "flows_namespace": "p"},
                         "relays": {"staging": {"sink_url": "wss://x.example.org/s", "profile": PROFILE,
                                                **TLS, "typo": 1}}}))
