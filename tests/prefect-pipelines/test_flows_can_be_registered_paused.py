"""`global.paused`: every flow registered paused, its schedule inactive.

For a new side that must not run while the old one still does (a migration, a restored Prefect
database): `prefect deploy` with a `cron` and no `active` registers the schedule active
(Prefect 3, `cli/deploy/_schedules.py`), so re-registering would undo a pause made in the
database. With `global.paused: true` the deploy Job writes `paused: true` on the deployment
and `active: false` on its schedule. Unset or false: prefect.yaml as before.

    task test:prefect-pipelines
"""

from __future__ import annotations

from test_flow_runs_read_secrets_by_reference import prefect_yaml
from test_flow_secrets_come_from_the_secret import VALUES, render


def deployment(values: dict) -> dict:
    _, docs = render(values)
    [dep] = prefect_yaml(docs)["deployments"]
    return dep


def test_by_default_the_schedule_is_as_before():
    dep = deployment(VALUES)
    assert "paused" not in dep
    assert "active" not in dep["schedule"]


def test_paused_registers_the_deployment_paused_and_its_schedule_inactive():
    dep = deployment({**VALUES, "global": {**VALUES["global"], "paused": True}})
    assert dep["paused"] is True
    assert dep["schedule"]["active"] is False
    assert dep["schedule"]["cron"]


def test_false_is_the_default():
    assert deployment({**VALUES, "global": {**VALUES["global"], "paused": False}}) == deployment(VALUES)
