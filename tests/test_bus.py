import gc
import json
import logging
from logging.handlers import RotatingFileHandler

from dicomhawk.bus import (
    InteractionEvent,
    RecentEventsHandler,
    SessionCache,
    _extract_params,
    hash_request,
    new_bus,
    recent_events,
)
from dicomhawk.status import QRStatus
from pydicom.dataset import Dataset


class _FakeAssoc:
    pass


def test_session_cache_same_assoc_gets_same_id_twice():
    cache = SessionCache()
    assoc = _FakeAssoc()
    assert cache.get_session_id(assoc) == cache.get_session_id(assoc)


def test_session_cache_different_assocs_get_different_ids():
    cache = SessionCache()
    a, b = _FakeAssoc(), _FakeAssoc()
    assert cache.get_session_id(a) != cache.get_session_id(b)


def test_session_cache_clear_removes_version_and_session():
    cache = SessionCache()
    assoc = _FakeAssoc()
    cache.get_session_id(assoc)
    cache.cache_version(assoc, "1.0")

    cache.clear(assoc)

    assert cache.get_version(assoc) is None


def test_session_cache_purges_on_gc_without_explicit_clear():
    """weakref.finalize must clean up even when an attacker drops the connection
    without EVT_RELEASED/EVT_ABORTED ever firing clear()."""
    cache = SessionCache()
    assoc = _FakeAssoc()
    key = id(assoc)
    cache.get_session_id(assoc)
    assert key in cache._sessions

    del assoc
    gc.collect()

    assert key not in cache._sessions


def test_extract_params_excludes_bulk_data_and_query_level():
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.PatientID = "P1"
    ds.PixelData = b"\x00\x01"

    params = _extract_params(ds)

    assert any("PatientID: P1" in p for p in params)
    assert not any("PixelData" in p for p in params)
    assert not any("QueryRetrieveLevel" in p for p in params)


def test_extract_params_summarizes_universal_matching_keys():
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyDate = ""  # universal match -> "requested" summary, not "Key: "

    params = _extract_params(ds)

    assert any(p.startswith("Requested:") and "StudyDate" in p for p in params)


def test_extract_params_returns_none_when_nothing_to_report():
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    assert _extract_params(ds) is None


def test_extract_params_bounds_attacker_controlled_values():
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.PatientID = "x" * 10000
    params = _extract_params(ds)
    assert len(params[0]) < 4200
    assert params[0].endswith("...[truncated]")


def test_hash_request_matches_sha256_and_preserves_stream_position():
    import hashlib
    from io import BytesIO

    class _FakeRequest:
        DataSet = BytesIO(b"raw c-store bytes")

    class _FakeEvt:
        request = _FakeRequest()

    _FakeEvt.request.DataSet.seek(5)  # arbitrary position before hashing
    digest = hash_request(_FakeEvt())

    assert digest == hashlib.sha256(b"raw c-store bytes").hexdigest()
    assert _FakeEvt.request.DataSet.tell() == 5  # position restored


def test_interaction_event_from_http_round_trips_through_json():
    ie = InteractionEvent.from_http(
        "WEB",
        "WEB_LOGIN_ATTEMPT",
        session_id="web-1.2.3.4",
        ip="1.2.3.4",
        port=5555,
        session_parameters=["Username: attacker"],
        log_level="WARNING",
        method="POST",
        path="/login",
        user_agent="curl/8.0",
    )

    data = json.loads(str(ie))

    assert data["channel"] == "WEB"
    assert data["request_type"] == "WEB_LOGIN_ATTEMPT"
    assert data["session_id"] == "web-1.2.3.4"
    assert data["session_parameters"] == ["Username: attacker"]
    assert data["method"] == "POST"
    assert data["path"] == "/login"
    assert data["status"] is None  # DIMSE-only field, None for HTTP


class _FakeRequestorAddr:
    address = "1.2.3.4"
    port = 5000


class _FakeAcceptorAddr:
    port = 104


class _FakeAssocForEvent:
    requestor = _FakeRequestorAddr()
    acceptor = _FakeAcceptorAddr()


class _FakeDimseEvent:
    assoc = _FakeAssocForEvent()
    # no .address attribute -> falls back to assoc.requestor, as real ACSE/DIMSE events do


def test_interaction_event_dimse_constructor_formats_status_with_hex_code():
    ie = InteractionEvent(
        _FakeDimseEvent(), SessionCache(), "C-ECHO", status=QRStatus.SUCCESS
    )
    assert ie.channel == "DIMSE"
    assert ie.ip == "1.2.3.4"
    assert ie.port == 5000
    assert ie.local_port == 104
    assert ie.status == "SUCCESS (0x0000)"


def test_recent_events_handler_only_keeps_interaction_events_and_respects_maxlen():
    handler = RecentEventsHandler(maxlen=2)
    logger = logging.getLogger("test-recent-events")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("a plain string, not an InteractionEvent")
    for i in range(3):
        logger.info(
            InteractionEvent.from_http(
                "WEB", f"EVT{i}", session_id="s", ip="1.2.3.4", port=1
            )
        )

    assert len(handler.events) == 2  # bounded by maxlen
    assert [e.request_type for e in handler.events] == ["EVT1", "EVT2"]


def test_recent_events_finds_attached_handler_or_none():
    logger = logging.getLogger("test-recent-events-lookup")
    assert recent_events(logger) is None

    handler = RecentEventsHandler()
    logger.addHandler(handler)
    assert recent_events(logger) is handler


def test_new_bus_replaces_its_handlers_instead_of_duplicating(tmp_path):
    logger = new_bus(str(tmp_path / "one.log"), verbose=False)
    logger = new_bus(str(tmp_path / "two.log"), verbose=False)
    assert sum(isinstance(h, RecentEventsHandler) for h in logger.handlers) == 1
    assert sum(getattr(h, "_dicomhawk_owned", False) for h in logger.handlers) == 2
    rotating = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
    assert rotating.maxBytes == 50 * 1024 * 1024
    assert rotating.backupCount == 5
