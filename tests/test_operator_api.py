import logging

import pytest

from dicomhawk.bus import RecentEventsHandler
from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.app import new_web
from web.operator_api import new_operator_api


@pytest.fixture
def bus():
    # A plain Logger() instance, not getLogger("bus") — the latter is a process-wide
    # singleton that new_bus() would mutate (propagate=False, accumulating handlers),
    # leaking state across test files. A fresh, uncached Logger avoids that entirely.
    logger = logging.Logger("test-operator-bus")
    logger.addHandler(RecentEventsHandler())
    return logger


@pytest.fixture
def repo(tmp_path):
    return new_repo(None, new_store(str(tmp_path / "traces")))


@pytest.fixture
def profile():
    return load_profile("fujifilm")


@pytest.fixture
def operator_client(profile, repo, bus):
    return new_operator_api(profile, repo, bus).test_client()


def test_profiles_endpoint_reflects_active_profile(operator_client):
    resp = operator_client.get("/api/profiles")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == "fujifilm"
    assert body["ae_title"] == "SYNAPSEDICOMSCP"
    assert body["web"]["templates_dir"] == "fujifilm"
    assert body["dicomweb"]["qido_default_media_type"] == "application/json"


def test_events_and_sessions_reflect_real_web_activity(
    profile, repo, bus, operator_client
):
    web_client = new_web(profile, repo, bus).test_client()
    web_client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": "attacker", "password": "hunter2"},
    )

    events = operator_client.get("/api/events").get_json()
    assert any(e["request_type"] == "WEB_LOGIN_ATTEMPT" for e in events)
    assert any("Username: attacker" in (e["session_parameters"] or []) for e in events)

    sessions = operator_client.get("/api/sessions").get_json()
    assert any(s["ip"] == "127.0.0.1" and s["channel"] == "WEB" for s in sessions)


def test_events_and_sessions_empty_without_activity(operator_client):
    assert operator_client.get("/api/events").get_json() == []
    assert operator_client.get("/api/sessions").get_json() == []


def test_operator_api_never_crashes_without_recent_events_handler(profile, repo):
    import logging

    bare_logger = logging.getLogger("bare-bus-for-test")
    client = new_operator_api(profile, repo, bare_logger).test_client()
    assert client.get("/api/events").get_json() == []
    assert client.get("/api/sessions").get_json() == []
