"""Keycloak's pod anti-affinity can be turned off per environment (`keycloak.pod_anti_affinity`).

The keycloakx chart ships a required anti-affinity on the node (and a preferred one on the
zone). GKE Autopilot rejects a pod that uses anti-affinity with less than 500m CPU requested,
and with one replica the rule spreads nothing. Unset, the chart's default stays, so an
environment that does not set the key renders unchanged.

    task test:keycloak-access
"""

from __future__ import annotations

from test_keycloak_access import docs_of, of_kind, render


def statefulset_affinity(override: str = "") -> dict | None:
    (sts,) = of_kind(docs_of(render("0020-auth.yaml.gotmpl", "keycloak", override=override)), "StatefulSet")
    return sts["spec"]["template"]["spec"].get("affinity")


def test_the_charts_anti_affinity_stays_by_default():
    affinity = statefulset_affinity()
    assert affinity["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]


def test_the_anti_affinity_can_be_turned_off():
    assert not statefulset_affinity("keycloak:\n  pod_anti_affinity: false\n")
