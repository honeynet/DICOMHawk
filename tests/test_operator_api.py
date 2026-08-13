import logging
import base64
from logging import FileHandler
from logging.handlers import RotatingFileHandler

import pytest
import ujson

from dicomhawk.bus import RecentEventsHandler
from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.app import new_web
from web.operator_api import extract_credentials, new_operator_api


@pytest.fixture
def bus():
    # Avoid leaking handlers through the process-wide bus logger.
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
def operator_client(profile, bus):
    return new_operator_api(profile, bus).test_client()


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


def test_operator_api_never_crashes_without_recent_events_handler(profile):
    import logging

    bare_logger = logging.getLogger("bare-bus-for-test")
    client = new_operator_api(profile, bare_logger).test_client()
    assert client.get("/api/events").get_json() == []
    assert client.get("/api/sessions").get_json() == []
    assert client.get("/api/stats").get_json()["total_events"] == 0
    assert client.get("/api/attackers").get_json() == []
    assert client.get("/api/credentials").get_json() == []
    assert client.get("/api/uploads").get_json() == []


def _durable_client(profile, tmp_path, lines):
    logfile = tmp_path / "dicomhawk.log"
    logfile.write_text("\n".join(ujson.dumps(line) for line in lines) + "\n")
    logger = logging.Logger("test-durable-bus")
    logger.addHandler(RotatingFileHandler(str(logfile)))
    return new_operator_api(profile, logger).test_client()


def test_derived_views_read_the_durable_log_file(profile, tmp_path):
    lines = [
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "ip": "10.0.0.1",
            "session_parameters": ["Username: admin", "Password: p@ss"],
            "timestamp": "2026-07-19T10:00:00",
        },
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "ip": "10.0.0.1",
            "session_parameters": ["Username: admin", "Password: p@ss"],
            "timestamp": "2026-07-19T10:01:00",
        },
        {
            "request_type": "WEB_UPLOAD",
            "channel": "WEB",
            "ip": "10.0.0.2",
            "session_parameters": [
                "File: x.dcm",
                "Bytes: 1024",
                "SHA256: abc",
                "SOPInstanceUID: 1.2.3",
            ],
            "timestamp": "2026-07-19T10:02:00",
        },
        {
            "request_type": "C-ECHO",
            "channel": "DIMSE",
            "ip": "10.0.0.3",
            "session_parameters": None,
            "timestamp": "2026-07-19T10:03:00",
        },
    ]
    client = _durable_client(profile, tmp_path, lines)

    stats = client.get("/api/stats").get_json()
    assert stats["total_events"] == 4
    assert stats["by_channel"]["WEB"] == 3
    assert stats["credentials_captured"] == 2
    assert stats["uploads_captured"] == 1
    assert stats["unique_source_ips"] == 3

    creds = client.get("/api/credentials").get_json()
    assert creds[0]["username"] == "admin" and creds[0]["password"] == "p@ss"
    assert creds[0]["count"] == 2
    assert creds[0]["source_ips"] == ["10.0.0.1"]

    by_ip = {a["ip"]: a for a in client.get("/api/attackers").get_json()}
    assert by_ip["10.0.0.1"]["classification"] == "credential-access"
    assert by_ip["10.0.0.2"]["classification"] == "storage-abuse"
    assert by_ip["10.0.0.3"]["classification"] == "reconnaissance"

    uploads = client.get("/api/uploads").get_json()
    assert uploads[0]["sha256"] == "abc" and uploads[0]["bytes"] == 1024

    assert [
        e["request_type"] for e in client.get("/api/events?channel=DIMSE").get_json()
    ] == ["C-ECHO"]
    assert len(client.get("/api/events?since=2026-07-19T10:02:00").get_json()) == 2


def test_credentials_flag_honey_hits():
    events = [
        {
            "request_type": "WEB_HONEY_CREDENTIAL_USED",
            "channel": "WEB",
            "ip": "10.0.0.9",
            "session_parameters": ["Username: test", "Password: test"],
            "timestamp": "2026-07-19T11:00:00",
        },
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "ip": "10.0.0.9",
            "session_parameters": ["Username: root", "Password: toor"],
            "timestamp": "2026-07-19T11:01:00",
        },
    ]
    creds = {(c["username"], c["password"]): c for c in extract_credentials(events)}
    assert creds[("test", "test")]["honey_hit"] is True
    assert creds[("root", "toor")]["honey_hit"] is False


def test_keyword_grants_are_counted_as_honey_credentials(profile, tmp_path):
    event = {
        "request_type": "WEB_HONEY_KEYWORD_USED",
        "channel": "WEB",
        "ip": "10.0.0.10",
        "session_parameters": ["Username: admin", "Password: guess"],
        "timestamp": "2026-07-19T11:00:00",
    }
    client = _durable_client(profile, tmp_path, [event])

    assert client.get("/api/stats").get_json()["credentials_captured"] == 1
    assert client.get("/api/credentials").get_json()[0]["honey_hit"] is True
    attacker = client.get("/api/attackers").get_json()[0]
    assert attacker["classification"] == "credential-access"


def test_honey_pair_matches_across_channels():
    events = [
        {
            "request_type": "DICOMWEB_AUTH_ATTEMPT",
            "channel": "DICOMWEB",
            "ip": "10.0.0.4",
            "session_parameters": ["Username: test", "Password: test"],
        }
    ]
    assert extract_credentials(events, [("test", "test")])[0]["honey_hit"] is True


def test_upload_views_use_terminal_payload_events_only(profile, tmp_path):
    lines = [
        {
            "request_type": "WEB_UPLOAD_LIMIT",
            "channel": "WEB",
            "ip": "10.0.0.8",
            "session_parameters": ["Submitted: 11", "Rejected: 1"],
        },
        {
            "request_type": "DICOMWEB_STOW_STORE",
            "channel": "DICOMWEB",
            "ip": "10.0.0.8",
            "session_parameters": ["Stored: 1", "Failed: 0"],
        },
        {
            "request_type": "DICOMWEB_STOW_PAYLOAD",
            "channel": "DICOMWEB",
            "ip": "10.0.0.8",
            "artifact": {
                "bytes": 321,
                "sha256": "deadbeef",
                "sop_instance_uid": "1.2.3",
                "sop_class_uid": "1.2.4",
                "captured": True,
                "disposition": "stored",
                "reject_reason": None,
                "filename": None,
            },
        },
    ]
    client = _durable_client(profile, tmp_path, lines)
    uploads = client.get("/api/uploads").get_json()
    assert len(uploads) == 1
    assert uploads[0]["bytes"] == 321
    assert uploads[0]["sop_class_uid"] == "1.2.4"
    assert client.get("/api/stats").get_json()["upload_attempts"] == 1
    attacker = client.get("/api/attackers").get_json()[0]
    assert attacker["uploads"] == 1


def test_non_rotating_file_handler_is_durable(profile, tmp_path):
    logfile = tmp_path / "plain.log"
    logfile.write_text(
        ujson.dumps({"request_type": "C-ECHO", "channel": "DIMSE", "ip": "1.2.3.4"})
        + "\n"
    )
    logger = logging.Logger("plain-file-bus")
    logger.addHandler(FileHandler(logfile))
    client = new_operator_api(profile, logger).test_client()
    assert client.get("/api/stats").get_json()["total_events"] == 1


def test_rotated_logs_are_read_oldest_to_newest(profile, tmp_path):
    logfile = tmp_path / "rotating.log"
    logfile.write_text(
        ujson.dumps(
            {"request_type": "NEW", "channel": "WEB", "timestamp": "2026-02-02"}
        )
        + "\n"
    )
    (tmp_path / "rotating.log.1").write_text(
        ujson.dumps(
            {"request_type": "OLD", "channel": "DIMSE", "timestamp": "2026-01-01"}
        )
        + "\n"
    )
    logger = logging.Logger("rotated-file-bus")
    logger.addHandler(RotatingFileHandler(logfile, maxBytes=10, backupCount=2))
    client = new_operator_api(profile, logger).test_client()
    stats = client.get("/api/stats").get_json()
    assert stats["total_events"] == 2
    assert stats["first_event"] == "2026-01-01"


def test_malformed_records_are_skipped_without_crashing(profile, tmp_path):
    logfile = tmp_path / "malformed.log"
    logfile.write_text(
        '"valid JSON scalar"\n{"channel":"WEB"}\nnot-json\n'
        + ujson.dumps({"request_type": "OK", "channel": "WEB"})
        + "\n"
    )
    logger = logging.Logger("malformed-file-bus")
    logger.addHandler(FileHandler(logfile))
    client = new_operator_api(profile, logger).test_client()
    stats = client.get("/api/stats").get_json()
    assert stats["total_events"] == 2
    assert stats["skipped_records"] == 2
    assert client.get("/api/attackers").status_code == 200


def test_durable_views_stream_current_file_without_retaining_event_cache(
    profile, tmp_path
):
    logfile = tmp_path / "streamed.log"
    logfile.write_text(ujson.dumps({"request_type": "ONE", "channel": "WEB"}) + "\n")
    logger = logging.Logger("streamed-file-bus")
    logger.addHandler(FileHandler(logfile))
    client = new_operator_api(profile, logger).test_client()

    assert "EVENT_CACHE" not in client.application.config
    assert client.get("/api/stats").get_json()["total_events"] == 1

    with logfile.open("a") as stream:
        stream.write(ujson.dumps({"request_type": "TWO", "channel": "DIMSE"}) + "\n")
    assert client.get("/api/stats").get_json()["total_events"] == 2


def test_filter_validation_paging_and_headers(profile, tmp_path):
    lines = [
        {
            "request_type": f"TYPE-{index}",
            "channel": "WEB",
            "timestamp": f"2026-07-19T10:0{index}:00",
        }
        for index in range(3)
    ]
    client = _durable_client(profile, tmp_path, lines)
    response = client.get("/api/events?limit=1&offset=1")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    assert response.get_json()[0]["request_type"] == "TYPE-1"
    assert client.get("/api/events?limit=0").status_code == 400
    assert client.get("/api/events?offset=10001").status_code == 400
    assert client.get("/api/events?since=not-a-time").status_code == 400


def test_attacker_controlled_aggregate_cardinality_is_bounded(
    profile, tmp_path, monkeypatch
):
    from web import operator_api

    monkeypatch.setattr(operator_api, "_MAX_AGGREGATE_KEYS", 2)
    lines = [
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "ip": f"10.0.0.{index}",
            "session_id": f"session-{index}",
            "session_parameters": [f"Username: user-{index}", "Password: p"],
            "timestamp": f"2026-07-19T10:0{index}:00",
        }
        for index in range(3)
    ]
    client = _durable_client(profile, tmp_path, lines)

    stats = client.get("/api/stats").get_json()
    assert stats["total_events"] == 3
    assert stats["unique_source_ips"] == 2
    assert stats["unique_source_ips_truncated"] is True
    assert stats["unique_credentials"] == 2
    assert stats["unique_credentials_truncated"] is True

    attackers = client.get("/api/attackers")
    assert len(attackers.get_json()) == 2
    assert attackers.headers["X-Aggregation-Truncated"] == "true"
    credentials = client.get("/api/credentials")
    assert len(credentials.get_json()) == 2
    assert credentials.headers["X-Aggregation-Truncated"] == "true"
    sessions = client.get("/api/sessions")
    assert len(sessions.get_json()) == 2
    assert sessions.headers["X-Aggregation-Truncated"] == "true"
    assert client.get("/api/overview").get_json()["truncated"] == {
        "attackers": True,
        "credentials": True,
    }


def test_credential_cap_evicts_a_non_honey_entry_to_admit_a_honey_hit(monkeypatch):
    from web import operator_api

    monkeypatch.setattr(operator_api, "_MAX_AGGREGATE_KEYS", 2)
    events = [
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "session_parameters": ["Username: user-0", "Password: p"],
            "timestamp": "2026-07-19T10:00:00",
        },
        {
            "request_type": "WEB_LOGIN_ATTEMPT",
            "channel": "WEB",
            "session_parameters": ["Username: user-1", "Password: p"],
            "timestamp": "2026-07-19T10:01:00",
        },
        {
            "request_type": "WEB_HONEY_CREDENTIAL_USED",
            "channel": "WEB",
            "session_parameters": ["Username: test", "Password: test"],
            "timestamp": "2026-07-19T10:02:00",
        },
    ]

    creds = extract_credentials(events, honey_credentials=[("test", "test")])

    assert len(creds) == 2
    assert any(c["username"] == "test" and c["honey_hit"] for c in creds)


def test_credential_cap_still_truncates_once_every_slot_is_a_honey_hit(monkeypatch):
    from web import operator_api

    monkeypatch.setattr(operator_api, "_MAX_AGGREGATE_KEYS", 1)
    events = [
        {
            "request_type": "WEB_HONEY_CREDENTIAL_USED",
            "channel": "WEB",
            "session_parameters": ["Username: test", "Password: test"],
            "timestamp": "2026-07-19T10:00:00",
        },
        {
            "request_type": "WEB_HONEY_CREDENTIAL_USED",
            "channel": "WEB",
            "session_parameters": ["Username: decoy", "Password: decoy"],
            "timestamp": "2026-07-19T10:01:00",
        },
    ]

    creds = extract_credentials(
        events, honey_credentials=[("test", "test"), ("decoy", "decoy")]
    )

    assert len(creds) == 1
    assert (
        creds[0]["username"] == "test"
    )  # first-seen honey hit kept; no non-honey slot to evict


def test_operator_security_headers_auth_and_complete_honey_configuration(bus):
    client = new_operator_api(
        load_profile("generic-pacs"), bus, "secret-token"
    ).test_client()
    assert client.get("/api/stats").status_code == 401
    basic = base64.b64encode(b"operator:secret-token").decode()
    assert (
        client.get("/", headers={"Authorization": f"Basic {basic}"}).status_code == 200
    )
    response = client.get(
        "/api/profiles", headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    web = response.get_json()["web"]
    assert web["honey_credentials"] == [["test", "test"]]
    assert web["honey_keywords"] == [
        "admin",
        "pacs",
        "dicom",
        "radiology",
        "imaging",
        "service",
    ]


def test_operator_auth_handles_unicode_tokens_without_a_500(bus):
    client = new_operator_api(
        load_profile("generic-pacs"), bus, "ünïcödé"
    ).test_client()

    assert (
        client.get(
            "/api/profiles", headers={"Authorization": "Bearer ünïcödé"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/profiles", headers={"Authorization": "Bearer wröng"}
        ).status_code
        == 401
    )


def test_dashboard_and_overview_are_available(operator_client):
    dashboard = operator_client.get("/")
    assert dashboard.status_code == 200
    assert b"Operator console" in dashboard.data
    assert operator_client.get("/static/operator.css").status_code == 200
    overview = operator_client.get("/api/overview").get_json()
    assert set(overview) == {
        "stats",
        "attackers",
        "credentials",
        "uploads",
        "events",
        "truncated",
    }
