"""The sink's run-event client secret: one environment key for the sink and the realm.

`sync_tables.sink.events.client_secret` (secret) is the secret of celine-policies'
`svc-sync-tables-sink` in this environment's realm. Two releases read it:

- sync-tables: the sink's publisher, as a Secret of the chart's own
  (`<release>-sink-oidc`, key `client-secret`), unless the environment names an existing
  Secret (`events.oidc.client_secret: {name, key}`). Both together stop the render.
- policies-shell: `SVC_SYNC_TABLES_SINK_SECRET`, which clients.yaml sets on the realm. Outside
  dev there is no guessable default: unset, the variable is not set (as svc-member-mirror).

    task test:sync-tables
"""

from __future__ import annotations

import base64

import pytest
import yaml
from test_sink_stays_through_a_push import render

SECRET = "sink-client-secret-0123456789abcdef"
TOKEN = "0123456789abcdef0123456789abcdef"
SINK = {"enabled": True, "host": "sync.example.org", "ingress_flavor": "nginx",
        "dsn_secret": {"name": "mirror", "key": "uri"}, "allow_schemas": ["gold"],
        "tokens": {"warehouse": TOKEN}, "ca": "CA", "server_cert": "CERT", "server_key": "KEY",
        "accept_from": ["dev"]}
EVENTS = {"enabled": True, "env": "staging", "mqtt": {"host": "mqtt.example.org"},
          "oidc": {"base_url": "https://auth.example.org/realms/celine"}}


def sink_override(events: dict) -> str:
    return yaml.safe_dump({"sync_tables": {"image": "ghcr.io/celine-eu/sync-tables:0.8.0",
                                           "sink": {**SINK, "events": events}}})


def sink_env(objects: dict) -> dict:
    [c] = objects["Deployment/sync-tables-sink"]["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e for e in c["env"]}


def test_the_value_becomes_a_secret_of_the_chart():
    objects = render(sink_override({**EVENTS, "client_secret": SECRET}))
    secret = objects["Secret/sync-tables-sink-oidc"]
    data = secret.get("stringData") or {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}
    assert data == {"client-secret": SECRET}
    ref = sink_env(objects)["CELINE_OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "sync-tables-sink-oidc", "key": "client-secret"}


def test_an_existing_secret_still_works_and_no_secret_is_rendered():
    objects = render(sink_override({**EVENTS, "oidc": {**EVENTS["oidc"],
                                                       "client_secret": {"name": "own", "key": "s"}}}))
    assert "Secret/sync-tables-sink-oidc" not in objects
    ref = sink_env(objects)["CELINE_OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "own", "key": "s"}


def test_a_value_and_an_existing_secret_together_stop_the_render():
    with pytest.raises(AssertionError, match="not both"):
        render(sink_override({**EVENTS, "client_secret": SECRET,
                              "oidc": {**EVENTS["oidc"], "client_secret": {"name": "own", "key": "s"}}}))


def test_events_without_any_secret_stop_the_render():
    with pytest.raises(AssertionError, match="client_secret"):
        render(sink_override(EVENTS))


def test_without_events_no_secret_is_rendered():
    objects = render(sink_override({"enabled": False, "client_secret": SECRET}))
    assert "Secret/sync-tables-sink-oidc" not in objects
    assert "CELINE_OIDC_CLIENT_SECRET" not in sink_env(objects)


# --- the realm side: policies-shell ------------------------------------------------------

POLICIES_SHELL = {"policies_shell": {"image_tag": "v9.9.9",
                                     "bootstrap": {"client_secret": TOKEN}}}


def shell_env(extra: dict, env: str) -> dict:
    objects = render(yaml.safe_dump({**POLICIES_SHELL, **extra}), env=env, release="policies-shell")
    (shell,) = [c for c in objects["Deployment/policies-shell"]["spec"]["template"]["spec"]["containers"]
                if c["name"] == "shell"]
    return {e["name"]: e for e in shell["env"]}


def test_policies_shell_gets_the_sink_client_secret():
    env = shell_env({"sync_tables": {"sink": {"events": {"client_secret": SECRET}}}}, "staging")
    assert env["SVC_SYNC_TABLES_SINK_SECRET"]["value"] == SECRET


def test_policies_shell_outside_dev_has_no_guessable_default():
    assert "SVC_SYNC_TABLES_SINK_SECRET" not in shell_env({}, "prod")


def test_policies_shell_in_dev_takes_the_dev_default():
    assert shell_env({}, "dev")["SVC_SYNC_TABLES_SINK_SECRET"]["value"] == "svc-sync-tables-sink"


def test_a_dataspace_client_cannot_set_the_sink_secret_twice():
    result_env = {"dataspace": {"enabled": True,
                                "policies_shell": {"overlays": [{"name": "x.yaml", "content": "clients: []"}],
                                                   "clients": ["svc-sync-tables-sink"]},
                                "client_secrets": {"svc-sync-tables-sink": SECRET}}}
    with pytest.raises(AssertionError, match="already set"):
        shell_env(result_env, "staging")
