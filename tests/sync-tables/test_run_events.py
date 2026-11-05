"""Run events (sync_tables 0.8.0): relays, the sink's events and edges, the push's config.

Every new key is opt-in, so a release on an older image renders as before:

- `pushSource.edges` / `pushTargets.<key>.edges`: the push mounts a config.yaml with
  `source_db` and those `edges` and passes `--config` (0.8.0 refuses a push without them).
- `sink.acceptFrom`: SYNC_ACCEPT_FROM; `sink.events`: `--events`, the broker and the OIDC
  client (its secret from an existing Secret), and egress to the broker and the issuer.
- `serve.serveTo`: SYNC_SERVE_TO.
- `relays.<key>`: one relay per sink (Deployment, ClusterIP Service, no Ingress), one config
  shared by all, each relay mounting only its own sink's TLS material. Its NetworkPolicy
  admits only the flows' namespace: the relay takes no token. A relay replaces the push
  CronJob of the same key: that CronJob is not rendered.

    task test:sync-tables
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/sync-tables"
TLS = {"ca": "CA", "clientCert": "CERT", "clientKey": "KEY", "token": "t" * 32}
PROFILE = "sources:\n  - {schema: gold, table: readings, date_column: ts, lookback: 2d}\n"
BASE = {"image": "ghcr.io/example/sync-tables:0.8.0",
        "pushSource": {"sourceSecret": {"name": "db", "key": "uri"},
                       "networkPolicy": {"enabled": True, "databasePodSelector": {"cnpg.io/cluster": "wh"}}}}
SINK = {"enabled": True, "host": "sync.example.org", "ingressFlavor": "nginx",
        "dsnSecret": {"name": "mirror", "key": "uri"}, "tokens": {"warehouse": "w" * 32},
        "ca": "CA", "serverCert": "CERT", "serverKey": "KEY", "allowSchemas": ["gold"]}
EVENTS = {"enabled": True, "mqtt": {"host": "mqtt.example.org", "port": 8883, "useTls": True},
          "oidc": {"baseUrl": "https://auth.example.org/realms/celine", "clientId": "svc-sync-tables-sink",
                   "clientSecret": {"name": "sink-oidc", "key": "secret"}}}


def render(values: dict) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values, f)
        f.flush()
        return subprocess.run(["helm", "template", "st", str(CHART), "-f", f.name],
                              capture_output=True, text=True, timeout=60, check=False)


def docs(values: dict) -> list[dict]:
    r = render(values)
    assert r.returncode == 0, r.stderr
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def by(ds: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in ds if d["kind"] == kind}


def container(workload: dict) -> dict:
    spec = workload["spec"]
    pod = spec["jobTemplate"]["spec"]["template"]["spec"] if workload["kind"] == "CronJob" else spec["template"]["spec"]
    [c] = pod["containers"]
    return c


def env(c: dict) -> dict[str, dict]:
    return {e["name"]: e for e in c.get("env", [])}


def target(to: str, **extra) -> dict:
    return {"to": to, "sinkUrl": f"wss://sync.{to}.example.org/sync/v1", "profile": PROFILE, **TLS, **extra}


EDGES = [{"from": "dev", "to": "prod", "transports": ["push"]}]


# --- the push: config.yaml with edges, opt-in ---------------------------------------------


def test_a_push_without_edges_renders_as_before():
    ds = docs({**BASE, "pushTargets": {"prod": target("prod")}})
    c = container(by(ds, "CronJob")["st-push-prod"])
    assert c["args"] == ["--from", "dev", "--to", "prod", "--profile", "/etc/sync/profile.yaml"]
    assert "config.yaml" not in by(ds, "ConfigMap")["st-push-prod-profile"]["data"]


def test_a_push_with_edges_mounts_a_config_and_passes_it():
    ds = docs({**BASE, "pushTargets": {"prod": target("prod", edges=EDGES)}})
    c = container(by(ds, "CronJob")["st-push-prod"])
    assert c["args"] == ["--from", "dev", "--to", "prod", "--profile", "/etc/sync/profile.yaml",
                         "--config", "/etc/sync/config.yaml"]
    cfg = yaml.safe_load(by(ds, "ConfigMap")["st-push-prod-profile"]["data"]["config.yaml"])
    assert cfg == {"source_db": {"url": "${DB_DEV}"}, "edges": EDGES}


def test_push_source_edges_are_the_targets_default():
    ds = docs({**BASE, "pushSource": {**BASE["pushSource"], "edges": EDGES},
               "pushTargets": {"prod": target("prod")}})
    assert "--config" in container(by(ds, "CronJob")["st-push-prod"])["args"]


# --- the sink: accepted sources and events -------------------------------------------------


def test_a_sink_without_the_new_keys_renders_as_before():
    c = container(by(docs({**BASE, "sink": SINK}), "Deployment")["st-sink"])
    assert "--events" not in c["args"]
    assert "SYNC_ACCEPT_FROM" not in env(c)
    assert not any(k.startswith("CELINE_") for k in env(c))


def test_the_sink_names_the_sources_it_accepts():
    c = container(by(docs({**BASE, "sink": {**SINK, "acceptFrom": ["dev"]}}), "Deployment")["st-sink"])
    assert env(c)["SYNC_ACCEPT_FROM"]["value"] == "dev"


def test_the_sink_publishes_events_with_its_own_client():
    ds = docs({**BASE, "sink": {**SINK, "acceptFrom": ["dev"], "events": EVENTS}})
    c = container(by(ds, "Deployment")["st-sink"])
    e = env(c)
    assert "--events" in c["args"]
    assert e["CELINE_MQTT_HOST"]["value"] == "mqtt.example.org"
    assert e["CELINE_MQTT_PORT"]["value"] == "8883"
    assert e["CELINE_MQTT_USE_TLS"]["value"] == "true"
    assert e["CELINE_OIDC_BASE_URL"]["value"] == "https://auth.example.org/realms/celine"
    assert e["CELINE_OIDC_CLIENT_ID"]["value"] == "svc-sync-tables-sink"
    assert e["CELINE_OIDC_CLIENT_SECRET"]["valueFrom"] == {"secretKeyRef": {"name": "sink-oidc", "key": "secret"}}


def test_events_open_the_sinks_egress_to_the_broker_and_the_issuer():
    np = by(docs({**BASE, "sink": {**SINK, "events": EVENTS}}), "NetworkPolicy")["st-sink"]
    ports = {p["port"] for rule in np["spec"]["egress"] for p in rule.get("ports", [])}
    assert {8883, 443} <= ports


def test_events_without_a_client_secret_stop_the_render():
    bad = {**EVENTS, "oidc": {**EVENTS["oidc"], "clientSecret": {"name": "", "key": "secret"}}}
    r = render({**BASE, "sink": {**SINK, "events": bad}})
    assert r.returncode != 0 and "clientSecret" in r.stderr


# --- the serving source: the targets it serves ---------------------------------------------


def test_the_serving_source_names_its_targets():
    serve = {"enabled": True, "host": "src.example.org", "dsnSecret": {"name": "ro", "key": "uri"},
             "tokens": {"dev1": "d" * 32}, "ca": "CA", "serverCert": "CERT", "serverKey": "KEY",
             "allowSchemas": ["gold"], "serveTo": ["local"]}
    c = container(by(docs({**BASE, "serve": serve}), "Deployment")["st-serve"])
    assert env(c)["SYNC_SERVE_TO"]["value"] == "local"


# --- relays ---------------------------------------------------------------------------------

RELAY_BASE = {**BASE, "relay": {"sourceEnv": "dev", "flowsNamespace": "pipelines"}}


def relay(to: str, **extra) -> dict:
    return {"sinkUrl": f"wss://sync.{to}.example.org/sync/v1", "profile": PROFILE, **TLS, **extra}


def test_no_relay_by_default():
    ds = docs(BASE)
    assert not [d for d in ds if d["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "relay"]


def test_each_relay_is_a_deployment_and_a_cluster_ip_service_without_ingress():
    ds = docs({**RELAY_BASE, "relays": {"staging": relay("staging"), "prod": relay("prod")}})
    deps, svcs = by(ds, "Deployment"), by(ds, "Service")
    assert {"st-relay-staging", "st-relay-prod"} <= set(deps)
    for key in ("staging", "prod"):
        d = deps[f"st-relay-{key}"]
        assert d["spec"]["replicas"] == 1 and d["spec"]["strategy"]["type"] == "Recreate"
        c = container(d)
        assert c["args"] == ["relay", "--config", "/etc/sync/config.yaml", "--sink", key, "--listen", "0.0.0.0:8080"]
        assert env(c)["DB_DEV"]["valueFrom"] == {"secretKeyRef": {"name": "db", "key": "uri"}}
        assert c["readinessProbe"]["httpGet"]["path"] == "/healthz"
        s = svcs[f"st-relay-{key}"]
        assert s["spec"].get("type", "ClusterIP") == "ClusterIP"
        assert s["spec"]["ports"][0]["port"] == 8080
    assert not [d for d in ds if d["kind"] in ("Ingress", "IngressRoute")]


def test_one_config_names_the_source_the_edges_and_every_sink():
    ds = docs({**RELAY_BASE, "relays": {"staging": relay("staging", minInterval="20m"), "prod": relay("prod")}})
    cfg = yaml.safe_load(by(ds, "ConfigMap")["st-relay-config"]["data"]["config.yaml"])
    assert cfg["source"] == {"env": "dev", "db": {"url": "${DB_DEV}"}}
    assert sorted(e["to"] for e in cfg["edges"]) == ["prod", "staging"]
    assert all(e["from"] == "dev" and e["transports"] == ["push"] for e in cfg["edges"])
    stg = cfg["sinks"]["staging"]
    assert stg["url"] == "wss://sync.staging.example.org/sync/v1"
    assert stg["ca"] == "/run/sync/staging/ca.crt" and stg["token_file"] == "/run/sync/staging/token"
    assert stg["sources"] == [{"schema": "gold", "table": "readings", "date_column": "ts", "lookback": "2d"}]
    assert stg["min_interval"] == "20m"
    assert "min_interval" not in cfg["sinks"]["prod"]


def test_a_relay_mounts_only_its_own_sinks_material():
    ds = docs({**RELAY_BASE, "relays": {"staging": relay("staging"), "prod": relay("prod")}})
    d = by(ds, "Deployment")["st-relay-staging"]
    secrets = {v["secret"]["secretName"] for v in d["spec"]["template"]["spec"]["volumes"] if "secret" in v}
    assert secrets == {"st-relay-staging-tls"}
    sec = by(ds, "Secret")["st-relay-staging-tls"]
    assert set(sec["stringData"]) == {"ca.crt", "tls.crt", "tls.key", "token"}


def test_a_relays_network_policy_admits_only_the_flows_namespace():
    np = by(docs({**RELAY_BASE, "relays": {"prod": relay("prod")}}), "NetworkPolicy")["st-relay-prod"]
    [rule] = np["spec"]["ingress"]
    assert rule["from"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "pipelines"}}}]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    egress_ports = {p["port"] for r in np["spec"]["egress"] for p in r.get("ports", [])}
    assert {5432, 443} <= egress_ports


def test_a_relay_replaces_the_push_cronjob_of_the_same_key():
    ds = docs({**RELAY_BASE, "pushTargets": {"prod": target("prod"), "staging": target("staging")},
               "relays": {"staging": relay("staging")}})
    assert set(by(ds, "CronJob")) == {"st-push-prod"}


def test_a_disabled_relay_leaves_its_push_cronjob():
    ds = docs({**RELAY_BASE, "pushTargets": {"staging": target("staging")},
               "relays": {"staging": relay("staging", enabled=False)}})
    assert set(by(ds, "CronJob")) == {"st-push-staging"}
    assert "st-relay-staging" not in by(ds, "Deployment")


@pytest.mark.parametrize("missing", ["sourceEnv", "flowsNamespace"])
def test_relays_need_the_source_env_and_the_flows_namespace(missing):
    values = {**RELAY_BASE, "relay": {k: v for k, v in RELAY_BASE["relay"].items() if k != missing},
              "relays": {"prod": relay("prod")}}
    r = render(values)
    assert r.returncode != 0 and missing in r.stderr
