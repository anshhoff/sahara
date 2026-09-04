"""Public demo mode — fail closed, or do not start (task 6.2).

The failure this exists to prevent is a deployment that is *mostly* a demo: read-only
routes, seeded data, and one live credential somebody added to test something and left
behind. A flag that merely disables writes would not catch that. Refusing to boot does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


# ------------------------------------------------------------ the refusal
def test_a_normal_process_has_nothing_to_refuse(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_DEMO", False)
    assert config.demo_violations() == []
    config.assert_demo_safe()          # must not raise


@pytest.mark.parametrize("name", [
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
    "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN",
])
def test_any_provider_credential_refuses_the_boot(monkeypatch, name):
    """Presence, not validity. Checking whether a key works would mean using it, which
    is precisely what must not happen in a demo."""
    for n in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", False)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    monkeypatch.setenv(name, "anything-at-all")

    with pytest.raises(RuntimeError, match="Refusing to start"):
        config.assert_demo_safe()


def test_a_real_send_path_refuses_the_boot(monkeypatch):
    for n in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", True)
    with pytest.raises(RuntimeError, match="VOICE_REAL_SEND_ENABLED"):
        config.assert_demo_safe()


def test_an_allowlisted_number_refuses_the_boot(monkeypatch):
    """Nothing to dial is the demo's whole safety story. A populated allowlist
    contradicts it even with the send path off — the next env var would turn it on."""
    for n in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", False)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset({"+919999999999"}))
    with pytest.raises(RuntimeError, match="VERIFIED_RECIPIENTS"):
        config.assert_demo_safe()


def test_the_control_room_refuses_the_boot(monkeypatch):
    """It runs the test suite and the batch as subprocesses. Not on a public host."""
    for n in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", False)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    monkeypatch.setattr(config, "CONTROL_ENABLED", True)
    with pytest.raises(RuntimeError, match="CONTROL_API_ENABLED"):
        config.assert_demo_safe()


def test_the_refusal_names_every_reason_not_just_the_first(monkeypatch):
    """An operator who fixes one violation and restarts into the next one learns the
    wrong lesson about how close they were."""
    for n in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "CONTROL_ENABLED", True)
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", True)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    assert len(config.demo_violations()) == 3


# ------------------------------------------------------- reads pass, writes 404
@pytest.fixture()
def demo_client(fresh_db, monkeypatch):
    """A process that is genuinely a demo.

    The credentials are cleared explicitly rather than assumed absent, because
    `load_dotenv()` reads the project's `.env` from wherever the suite is run — so on a
    developer machine that has one, `assert_demo_safe()` correctly refuses. That refusal
    is the subject of the tests above; this fixture is for the tests below it, which are
    about what a *valid* demo serves.
    """
    for name in config._DEMO_FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", False)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    from app.main import app

    with TestClient(app) as client:
        yield client


def test_reads_still_work_in_demo_mode(demo_client):
    assert demo_client.get("/api/summary").status_code == 200
    assert demo_client.get("/healthz").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/api/suppression/cust_x"),
    ("post", "/api/cases/case_x/opt-out"),
    ("post", "/webhooks/razorpay"),
    ("post", "/api/control/tick"),
])
def test_every_write_is_a_404_not_a_403(demo_client, method, path):
    """The difference matters. A 403 announces there is an endpoint here and you are
    not allowed to use it, which is an invitation to hunt for the misconfigured one. A
    404 says there is nothing here — which, in demo mode, is true."""
    response = getattr(demo_client, method)(path, json={})
    assert response.status_code == 404


def test_writes_are_untouched_when_the_demo_flag_is_off(fresh_db, monkeypatch):
    """The middleware must be inert outside demo mode, not merely quiet."""
    monkeypatch.setattr(config, "PUBLIC_DEMO", False)
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/suppression/cust_y", params={"reason": "manual"})
    assert response.status_code == 200
