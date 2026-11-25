"""`auth_setup.cookie_name`: the name of oauth2-proxy's session cookie.

oauth2-proxy sets its cookies on `.<domain>`. Where one environment's domain sits under
another's (staging's `staging.dev.celine.spindoxlabs.it` under prod's `celine.spindoxlabs.it`),
the browser sends the outer environment's session cookie to the inner one's hosts too, under
the same default name `_oauth2_proxy`. The inner proxy then reads a cookie it cannot validate,
drops it and signs in again, in a loop (staging, 2026-11-25). A name of its own per environment
keeps the two apart; the CSRF cookie follows (`<name>_csrf`). Unset, the config is as before.

    task test:auth-setup
"""

from __future__ import annotations

import pytest

from test_allowed_groups import cfg_of
from test_admin_mfa import render


def test_unset_the_default_name():
    assert "cookie_name" not in cfg_of()


def test_set_the_session_cookie_has_that_name():
    cfg = cfg_of("auth_setup:\n  cookie_name: _oauth2_proxy_staging\n")
    assert 'cookie_name = "_oauth2_proxy_staging"' in cfg


@pytest.mark.parametrize("name", ["''", "'has space'", "'semi;colon'", "'__Host-x'", "[a]"])
def test_a_name_a_browser_would_refuse_or_mangle_stops_the_render(name):
    result = render("true", f"auth_setup:\n  cookie_name: {name}\n")
    assert result.returncode != 0
    assert "auth_setup.cookie_name" in result.stderr
