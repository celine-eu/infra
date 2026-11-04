"""`pushTargets`: one source pushing to several sinks, one CronJob each.

`pushSource` alone renders as before (<release>-push). Each enabled `pushTargets` entry renders
its own Secret, profile ConfigMap, CronJob and NetworkPolicy as <release>-push-<key>, with the
entry's keys over `pushSource`'s; `enabled: false` leaves it out; a key that is not a DNS label
stops the render.

    task test:sync-tables
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[2] / "charts/sync-tables"
TLS = {"ca": "CA", "clientCert": "CERT", "clientKey": "KEY", "token": "t" * 32}
BASE = {"image": "ghcr.io/example/sync-tables:0.0.0",
        "pushSource": {"sourceSecret": {"name": "db", "key": "uri"},
                       "networkPolicy": {"enabled": True, "databasePodSelector": {"cnpg.io/cluster": "wh"}}}}


def render(values: dict) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values, f)
        f.flush()
        return subprocess.run(["helm", "template", "st", str(CHART), "-f", f.name],
                              capture_output=True, text=True, timeout=60)


def docs(values: dict) -> list[dict]:
    r = render(values)
    assert r.returncode == 0, r.stderr
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def target(to: str, **extra) -> dict:
    return {"to": to, "sinkUrl": f"wss://sync.{to}.example.org/sync/v1", "profile": f"tables: [{to}]",
            **TLS, **extra}


def cronjobs(ds: list[dict]) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in ds if d["kind"] == "CronJob"}


def test_none_renders_no_push():
    assert cronjobs(docs(BASE)) == {}


def test_each_target_is_its_own_push():
    ds = docs({**BASE, "pushTargets": {"prod": target("prod"), "staging": target("staging", schedule="5 * * * *")}})
    cjs = cronjobs(ds)
    assert set(cjs) == {"st-push-prod", "st-push-staging"}
    stg = cjs["st-push-staging"]
    assert stg["spec"]["schedule"] == "5 * * * *"
    assert cjs["st-push-prod"]["spec"]["schedule"] == "*/30 * * * *"
    [c] = stg["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert c["args"][:4] == ["--from", "dev", "--to", "staging"]
    assert {"name": "SYNC_SINK_URL", "value": "wss://sync.staging.example.org/sync/v1"} in c["env"]
    names = {(d["kind"], d["metadata"]["name"]) for d in ds}
    for key in ("prod", "staging"):
        assert ("Secret", f"st-push-{key}-tls") in names
        assert ("ConfigMap", f"st-push-{key}-profile") in names
        assert ("NetworkPolicy", f"st-push-{key}") in names
    profile = next(d for d in ds if d["metadata"]["name"] == "st-push-staging-profile")
    assert profile["data"]["profile.yaml"].strip() == "tables: [staging]"
    np = next(d for d in ds if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "st-push-staging")
    assert {"podSelector": {"matchLabels": {"cnpg.io/cluster": "wh"}}} in np["spec"]["egress"][1]["to"]


def test_a_disabled_target_is_left_out():
    ds = docs({**BASE, "pushTargets": {"prod": target("prod"), "staging": target("staging", enabled=False)}})
    assert set(cronjobs(ds)) == {"st-push-prod"}


def test_push_source_and_targets_coexist_by_name():
    ds = docs({**BASE, "pushSource": {**BASE["pushSource"], "enabled": True, **target("prod")},
               "pushTargets": {"staging": target("staging")}})
    assert set(cronjobs(ds)) == {"st-push", "st-push-staging"}


def test_a_key_that_is_no_dns_label_stops_the_render():
    r = render({**BASE, "pushTargets": {"Staging_1": target("staging")}})
    assert r.returncode != 0 and "DNS label" in r.stderr


def test_a_target_without_its_tls_stops_the_render():
    r = render({**BASE, "pushTargets": {"staging": {"to": "staging", "sinkUrl": "wss://x/sync/v1", "profile": "p"}}})
    assert r.returncode != 0 and "required" in r.stderr
