import gc
import json
import logging
import multiprocessing
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from dicomhawk.bus import (
    _ConsoleFormatter,
    InteractionEvent,
    MultiprocessFileHandler,
    MultiprocessRotatingFileHandler,
    RecentEventsHandler,
    SessionCache,
    _extract_params,
    new_bus,
    new_dev_log,
    recent_events,
    worker_bus_config,
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
    """Dropped peers must not depend on ACSE cleanup."""
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
        artifact={"sha256": "abc", "captured": True},
    )

    data = json.loads(str(ie))

    assert data["channel"] == "WEB"
    assert data["request_type"] == "WEB_LOGIN_ATTEMPT"
    assert data["session_id"] == "web-1.2.3.4"
    assert data["session_parameters"] == ["Username: attacker"]
    assert data["method"] == "POST"
    assert data["path"] == "/login"
    assert data["status"] is None  # DIMSE-only field, None for HTTP
    assert data["artifact"] == {"sha256": "abc", "captured": True}


def test_background_event_carries_artifact_id_and_analysis_with_no_network_context():
    ie = InteractionEvent.background(
        "ANALYSIS",
        "ANALYSIS_RESULT",
        session_id="1785516989802",
        artifact_id="9d1a2d9ca0f0",
        analysis={"entropy": 5.2},
        session_parameters=["Matched: EICAR_Test_String"],
    )
    data = json.loads(str(ie))

    assert data["channel"] == "ANALYSIS"
    assert data["artifact_id"] == "9d1a2d9ca0f0"
    assert data["analysis"] == {"entropy": 5.2}
    assert data["ip"] is None and data["port"] is None and data["local_port"] is None


def test_console_formatter_shows_session_not_none_for_background_events():
    ie = InteractionEvent.background(
        "ANALYSIS",
        "ANALYSIS_RESULT",
        session_id="1785516989802",
        artifact_id="9d1a2d9ca0f0",
        session_parameters=["Matched: EICAR_Test_String"],
    )
    record = logging.LogRecord("bus", logging.INFO, __file__, 0, ie, None, None)
    line = _ConsoleFormatter(use_color=False).format(record)

    assert "None:None" not in line
    assert "session=1785516989802" in line
    assert "artifact=9d1a2d9ca0f0" in line
    assert "Matched: EICAR_Test_String" in line


@pytest.mark.parametrize(
    "hostile",
    ["\x1b[2J\x1b[H", "\x1b]0;PWNED\x07", "x\nFAKE EVENT", "real\rFAKE"],
)
def test_console_formatter_escapes_attacker_control_characters(hostile):
    event = InteractionEvent.from_http(
        "WEB",
        "WEB_LOGIN_ATTEMPT",
        session_id="web-1",
        ip="1.2.3.4",
        port=1,
        session_parameters=[f"Username: {hostile}"],
    )
    record = logging.LogRecord("bus", logging.WARNING, __file__, 0, event, (), None)

    line = _ConsoleFormatter(use_color=False).format(record)

    assert all(character.isprintable() for character in line)
    assert hostile not in line


def test_dev_log_escapes_message_controls_but_keeps_traceback_lines(tmp_path):
    path = tmp_path / "dev.log"
    new_dev_log(str(path))
    logger = logging.getLogger("control-test")
    try:
        raise ValueError("bad")
    except ValueError:
        logger.exception("attacker=\x1b[2J\nforged")

    content = path.read_text()
    assert "\\x1b[2J\\nforged" in content
    assert "Traceback (most recent call last):\n" in content


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


def _write_bus_events(count, prefix):
    logger = logging.getLogger("bus")
    for index in range(count):
        logger.info(
            InteractionEvent.background(
                "ANALYSIS",
                "ROTATION_TEST",
                session_id=f"{prefix}-{index}",
            )
        )


def test_rotating_bus_preserves_events_written_by_forked_processes(tmp_path):
    path = tmp_path / "bus.log"
    logger = new_bus(str(path), size=700, backups=200, verbose=False)
    assert any(
        isinstance(handler, MultiprocessRotatingFileHandler)
        for handler in logger.handlers
    )

    context = multiprocessing.get_context("fork")
    workers = [
        context.Process(target=_write_bus_events, args=(30, f"worker-{index}"))
        for index in range(3)
    ]
    for worker in workers:
        worker.start()
    _write_bus_events(30, "parent")
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    records = []
    for logfile in tmp_path.glob("bus.log*"):
        if logfile.name.endswith(".lock"):
            continue
        records.extend(json.loads(line) for line in logfile.read_text().splitlines())
    assert len(records) == 120
    assert len({record["session_id"] for record in records}) == 120


def test_unrotated_bus_still_uses_the_multiprocess_lock(tmp_path):
    path = tmp_path / "bus.log"
    logger = new_bus(str(path), size=None, verbose=False)

    handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
    assert isinstance(handler, MultiprocessFileHandler)
    assert worker_bus_config(logger) == {
        "stdout": str(path),
        "when": None,
        "interval": 1,
        "size": None,
        "backups": 5,
        "verbose": False,
    }


def test_bus_lock_open_failure_never_escapes_logging(tmp_path, monkeypatch):
    handler = MultiprocessFileHandler(str(tmp_path / "bus.log"), delay=True)
    errors = []
    handler.handleError = errors.append

    def fail_open(*_args, **_kwargs):
        raise OSError("read-only volume")

    monkeypatch.setattr("builtins.open", fail_open)
    record = logging.LogRecord("bus", logging.INFO, __file__, 1, "event", (), None)

    handler.emit(record)

    assert errors == [record]
    handler.close()


def test_new_bus_silences_pynetdicoms_own_exception_tracebacks(tmp_path, capsys):
    new_bus(str(tmp_path / "bus.log"), verbose=False)
    logging.getLogger("pynetdicom.association").exception(ValueError("boom"))
    assert "boom" not in capsys.readouterr().err


def test_new_dev_log_reinstates_pynetdicom_detail_over_new_bus_default(
    tmp_path, capsys
):
    new_bus(str(tmp_path / "bus.log"), verbose=False)
    new_dev_log(str(tmp_path / "dev.log"))
    logging.getLogger("pynetdicom.association").exception(ValueError("boom"))
    assert "boom" in Path(tmp_path / "dev.log").read_text()
