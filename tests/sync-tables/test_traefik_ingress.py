"""sync-tables on Traefik: plain Ingresses, bound to a TLSOption by annotation.

A plain Ingress is what a namespace admin may create and what host admission policies
check; an IngressRoute is neither. So the sink's Traefik flavor and the serving source's are
an Ingress carrying `traefik.ingress.kubernetes.io/router.tls.options` (and the Middlewares),
next to the TLSOption that requires the client certificate. The nginx renders do not change.

    python3 -m pytest -q tests/sync-tables/test_traefik_ingress.py

Needs `helm`; renders the chart directly with throwaway PEM strings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "sync-tables"
PEM = "-----BEGIN CERTIFICATE-----"
KEY = "-----BEGIN PRIVATE KEY-----"
TOKEN = "0123456789abcdef0123456789abcdef"


def _render(*sets: str) -> list[dict]:
    args = ["helm", "template", "st", str(CHART), "-n", "ns",
            "--set", "image=example/sync-tables:0.6.0"]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _one(docs: list[dict], kind: str, name: str) -> dict:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} found"
    return found[0]


SINK = ["sink.enabled=true", "sink.host=sync.example.org", "sink.ingressClassName=traefik",
        "sink.dsnSecret.name=mirror-dsn", "sink.allowSchemas={ds_dev_gold}",
        f"sink.tokens.push={TOKEN}", f"sink.ca={PEM}", f"sink.serverCert={PEM}",
        f"sink.serverKey={KEY}"]
SERVE = ["serve.enabled=true", "serve.host=sync-source.example.org",
         "serve.dsnSecret.name=ro-dsn", "serve.allowSchemas={ds_dev_gold}",
         f"serve.tokens.dev={TOKEN}", f"serve.ca={PEM}"]


def test_sink_traefik_is_an_ingress_bound_to_its_tls_option():
    docs = _render(*SINK, "sink.ingressFlavor=traefik", "sink.allowSourceRanges=192.0.2.1/32")
    assert not [d for d in docs if d["kind"] == "IngressRoute"]
    ing = _one(docs, "Ingress", "st-sink")
    ann = ing["metadata"]["annotations"]
    assert ann["traefik.ingress.kubernetes.io/router.tls.options"] == "ns-st-sink@kubernetescrd"
    assert ann["traefik.ingress.kubernetes.io/router.middlewares"] == (
        "ns-st-sink-allow@kubernetescrd,ns-st-sink-client-cert@kubernetescrd")
    assert ing["spec"]["ingressClassName"] == "traefik"
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["pathType"] == "Exact"
    opt = _one(docs, "TLSOption", "st-sink")
    assert opt["spec"]["clientAuth"]["clientAuthType"] == "RequireAndVerifyClientCert"
    assert opt["spec"]["clientAuth"]["secretNames"] == ["st-sink-ca"]


def test_serve_traefik_requires_a_client_certificate():
    docs = _render(*SERVE, "serve.ingressFlavor=traefik", "serve.ingressClassName=traefik",
                   f"serve.serverCert={PEM}", f"serve.serverKey={KEY}")
    ann = _one(docs, "Ingress", "st-serve")["metadata"]["annotations"]
    assert ann["traefik.ingress.kubernetes.io/router.tls.options"] == "ns-st-serve@kubernetescrd"
    assert not [k for k in ann if k.startswith("nginx.")]
    opt = _one(docs, "TLSOption", "st-serve")
    assert opt["metadata"]["name"] != "default"
    assert opt["spec"]["clientAuth"]["secretNames"] == ["st-serve-ca"]
    assert opt["spec"]["minVersion"] == "VersionTLS12"


def test_serve_traefik_without_client_certs_has_no_tls_option():
    docs = _render(*SERVE, "serve.ingressFlavor=traefik", "serve.clientCerts=false",
                   f"serve.serverCert={PEM}", f"serve.serverKey={KEY}")
    ann = _one(docs, "Ingress", "st-serve")["metadata"]["annotations"]
    assert "traefik.ingress.kubernetes.io/router.tls.options" not in ann
    assert not [d for d in docs if d["kind"] == "TLSOption"]


def test_serve_source_ranges_on_traefik_are_an_allow_list():
    docs = _render(*SERVE, "serve.ingressFlavor=traefik", f"serve.serverCert={PEM}",
                   f"serve.serverKey={KEY}", "serve.allowSourceRanges=192.0.2.0/24")
    ann = _one(docs, "Ingress", "st-serve")["metadata"]["annotations"]
    assert ann["traefik.ingress.kubernetes.io/router.middlewares"] == "ns-st-serve-allow@kubernetescrd"
    mw = _one(docs, "Middleware", "st-serve-allow")
    assert mw["spec"]["ipAllowList"]["sourceRange"] == ["192.0.2.0/24"]


def test_serve_certificate_from_cert_manager_replaces_the_tls_secret():
    docs = _render(*SERVE, "serve.ingressFlavor=traefik",
                   "serve.certManager.clusterIssuer=example-ca")
    assert not [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "st-serve-tls"]
    ing = _one(docs, "Ingress", "st-serve")
    assert ing["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "example-ca"
    assert ing["spec"]["tls"][0]["secretName"] == "st-serve-tls"


def test_serve_needs_its_certificate_without_cert_manager():
    with pytest.raises(subprocess.CalledProcessError) as e:
        _render(*SERVE)
    assert "serve.serverCert is required" in e.value.stderr


def test_serve_nginx_keeps_its_annotations():
    docs = _render(*SERVE, f"serve.serverCert={PEM}", f"serve.serverKey={KEY}")
    ann = _one(docs, "Ingress", "st-serve")["metadata"]["annotations"]
    assert ann["nginx.ingress.kubernetes.io/auth-tls-verify-client"] == "on"
    assert not [k for k in ann if k.startswith("traefik.")]
    assert not [d for d in docs if d["kind"] == "TLSOption"]


def test_serve_rejects_an_unknown_flavor():
    with pytest.raises(subprocess.CalledProcessError) as e:
        _render(*SERVE, "serve.ingressFlavor=haproxy", f"serve.serverCert={PEM}",
                f"serve.serverKey={KEY}")
    assert "serve.ingressFlavor must be traefik or nginx" in e.value.stderr
