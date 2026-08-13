"""Handler unit tests and real loopback DIMSE integration tests."""

import io
import gzip
import hashlib
import json
import logging
import re
import socket
import tempfile
import time

import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE, evt
from pynetdicom.pdu_primitives import A_ASSOCIATE, ImplementationVersionNameNotification
from pynetdicom.presentation import build_role
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
    Verification,
)

from dicomhawk.bus import SessionCache
from dicomhawk.handlers import (
    _ascii_preview,
    _associate_request_details,
    _strip_sublevel_tags,
    handle_abort,
    handle_associate,
    handle_close,
    handle_connect,
    handle_reject,
    handle_release,
    new_dimse_factory,
)
from dicomhawk.repository import new_repo
from dicomhawk.status import QRStatus
from dicomhawk.storage import new_store

# --- fakes for the small ACSE handlers (pure logic, no real socket needed) ---


class _FakeRequestor:
    def __init__(self, address="10.0.0.5", port=5000):
        self.address = address
        self.port = port


class _FakeAssoc:
    def __init__(self):
        self.requestor = _FakeRequestor()
        self.acceptor = None


class _FakeEvent:
    def __init__(self, primitive=None, assoc=None, address=None, data=None):
        self.primitive = primitive
        self.assoc = assoc if assoc is not None else _FakeAssoc()
        if address is not None:
            self.address = address
        if data is not None:
            self.data = data


@pytest.fixture
def cache():
    return SessionCache()


@pytest.fixture
def acse_bus():
    logger = logging.getLogger("test-acse-bus")
    logger.setLevel(logging.INFO)
    return logger


def test_handle_connect_skips_loopback_addresses(acse_bus, cache, caplog):
    with caplog.at_level(logging.INFO, logger="test-acse-bus"):
        handle_connect(None, acse_bus, cache, _FakeEvent(address=("127.0.0.1", 555)))
    assert "Connection Opened" not in caplog.text


def test_handle_connect_logs_non_loopback_addresses(acse_bus, cache, caplog):
    with caplog.at_level(logging.INFO, logger="test-acse-bus"):
        handle_connect(None, acse_bus, cache, _FakeEvent(address=("10.0.0.9", 555)))
    assert "Connection Opened" in caplog.text


def _associate_rq(called=b"SCPTEST", calling=b"SCUTEST"):
    implementation_uid = b"1.2.826.0.1.3680043.8.498.1"
    implementation_version = b"EVIL_TOOL_1"
    subitems = (
        b"\x52\x00"
        + len(implementation_uid).to_bytes(2, "big")
        + implementation_uid
        + b"\x55\x00"
        + len(implementation_version).to_bytes(2, "big")
        + implementation_version
    )
    user_info = b"\x50\x00" + len(subitems).to_bytes(2, "big") + subitems
    pdu = bytearray(74)
    pdu[0] = 0x01
    pdu[6:8] = b"\x00\x01"
    pdu[10:26] = called.ljust(16)
    pdu[26:42] = calling.ljust(16)
    pdu.extend(user_info)
    pdu[2:6] = (len(pdu) - 6).to_bytes(4, "big")
    return bytes(pdu)


def test_malformed_associate_fields_are_recovered_from_bounded_wire_prefix():
    details = _associate_request_details(
        _associate_rq(called=b"BAD\x1bAE", calling=b"ATTACKER")
    )

    assert "Called: BAD\x1bAE" in details
    assert "Calling: ATTACKER" in details
    assert any(
        value.startswith("Implementation Class UID: 1.2.826") for value in details
    )
    assert "Implementation Version: EVIL_TOOL_1" in details


def _probe_connection(loopback, caplog, payload: bytes) -> str:
    """Send raw bytes at the real listener and return the Connection Closed log text."""
    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        with socket.create_connection(("127.0.0.1", loopback.port), timeout=2) as peer:
            if payload:
                peer.sendall(payload)
            peer.shutdown(socket.SHUT_WR)
            try:
                peer.recv(1024)
            except (ConnectionError, TimeoutError, OSError):
                pass
        deadline = time.monotonic() + 2
        while "Connection Closed" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
    return caplog.text


@pytest.mark.parametrize(
    ("payload", "protocol"),
    [
        (b"GET /admin HTTP/1.1\r\nHost: x\r\n\r\n", "HTTP"),
        (b"\x16\x03\x01\x00\x50" + b"\x00" * 80, "TLS"),
        (b"SSH-2.0-OpenSSH_9.6\r\n", "SSH"),
        (b"\x99garbage-not-a-pdu", "unknown"),
    ],
)
def test_non_dicom_probes_are_classified_and_counted_on_a_real_connection(
    loopback, caplog, monkeypatch, payload, protocol
):
    """Without the socket tap every one of these is 'unknown' with 0 bytes."""
    monkeypatch.setattr("dicomhawk.handlers._LOOPBACK", frozenset())

    text = _probe_connection(loopback, caplog, payload)

    assert "Connection Closed" in text
    assert f"Protocol: {protocol}" in text
    assert "Association decoded: no" in text
    # The DUL aborts on the first bad PDU type, so the drained remainder is a race.
    counted = int(re.search(r"Bytes received: (\d+)", text).group(1))
    assert 0 < counted <= len(payload)


def test_connection_close_previews_a_probe_as_readable_text(
    loopback, caplog, monkeypatch
):
    """The hex dump alone makes the operator decode bytes to see what was asked for."""
    monkeypatch.setattr("dicomhawk.handlers._LOOPBACK", frozenset())

    text = _probe_connection(loopback, caplog, b"GET /admin HTTP/1.1\r\nHost: pacs\r\n")

    assert "Preview: GET /admin HTTP/1.1..Host: pacs.." in text


def test_connection_preview_strips_control_bytes_before_they_are_logged():
    preview = _ascii_preview(b"\x1b[2J\x07GET /x\x00\xff")

    assert preview == ".[2J.GET /x.."
    assert not any(ord(character) < 32 for character in preview)


def test_connection_close_counts_bytes_once_for_a_decoded_association(
    loopback, caplog, monkeypatch
):
    """The socket tap replaced the EVT_DATA_RECV handler; running both double-counted."""
    monkeypatch.setattr("dicomhawk.handlers._LOOPBACK", frozenset())
    payload = _associate_rq(called=b"SCPTEST", calling=b"ATTACKER")

    text = _probe_connection(loopback, caplog, payload)

    assert f"Bytes received: {len(payload)}" in text
    assert "Protocol: DICOM" in text


def test_connection_close_reports_duration_for_a_silent_peer(
    loopback, caplog, monkeypatch
):
    monkeypatch.setattr("dicomhawk.handlers._LOOPBACK", frozenset())

    text = _probe_connection(loopback, caplog, b"")

    assert "Bytes received: 0" in text
    assert "Duration: " in text


def test_handle_associate_caches_version_and_logs_called_calling(
    acse_bus, cache, caplog
):
    prim = A_ASSOCIATE()
    prim.called_ae_title = "CALLEDAE"
    prim.calling_ae_title = "CALLINGAE"
    notif = ImplementationVersionNameNotification()
    notif.implementation_version_name = "PEER_1.0"
    prim.user_information = [notif]
    assoc = _FakeAssoc()

    with caplog.at_level(logging.INFO, logger="test-acse-bus"):
        handle_associate(None, acse_bus, cache, _FakeEvent(primitive=prim, assoc=assoc))

    assert "Association Requested" in caplog.text
    assert "CALLEDAE" in caplog.text and "CALLINGAE" in caplog.text
    assert cache.get_version(assoc) == "PEER_1.0"


def test_handle_associate_ignores_non_associate_rq_primitives(acse_bus, cache, caplog):
    # EVT_ACSE_RECV also fires for A-RELEASE-RQ/A-ABORT primitives; only A-ASSOCIATE-RQ logs.
    with caplog.at_level(logging.INFO, logger="test-acse-bus"):
        handle_associate(None, acse_bus, cache, _FakeEvent(primitive=object()))
    assert caplog.text == ""


def test_handle_release_logs_and_clears_session_cache(acse_bus, cache, caplog):
    assoc = _FakeAssoc()
    cache.cache_version(assoc, "SOME_VERSION")

    with caplog.at_level(logging.INFO, logger="test-acse-bus"):
        handle_release(None, acse_bus, cache, _FakeEvent(assoc=assoc))

    assert "Association Released" in caplog.text
    assert cache.get_version(assoc) is None


def test_handle_abort_logs_warning_and_clears_session_cache(acse_bus, cache, caplog):
    assoc = _FakeAssoc()
    cache.cache_version(assoc, "SOME_VERSION")

    with caplog.at_level(logging.WARNING, logger="test-acse-bus"):
        handle_abort(None, acse_bus, cache, _FakeEvent(assoc=assoc))

    assert "Association Aborted" in caplog.text
    assert cache.get_version(assoc) is None


def test_handle_reject_ignores_accepted_associations(acse_bus, cache, caplog):
    accepted = A_ASSOCIATE()
    accepted.result = 0x00
    with caplog.at_level(logging.WARNING, logger="test-acse-bus"):
        handle_reject(None, acse_bus, cache, _FakeEvent(primitive=accepted))
    assert caplog.text == ""


def test_handle_reject_logs_actual_rejections(acse_bus, cache, caplog):
    rejected = A_ASSOCIATE()
    rejected.result = 0x01
    rejected.result_source = 1
    rejected.diagnostic = 3
    with caplog.at_level(logging.WARNING, logger="test-acse-bus"):
        handle_reject(None, acse_bus, cache, _FakeEvent(primitive=rejected))
    assert "Association Rejected" in caplog.text
    assert "Rejected Permanent" in caplog.text


def test_strip_sublevel_tags_removes_attrs_below_query_level():
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = "1.2.3"
    ds.SeriesInstanceUID = "1.2.3.4"  # SERIES-level, below STUDY
    ds.Modality = "CT"  # also SERIES-level

    filtered, stripped = _strip_sublevel_tags(
        ds, StudyRootQueryRetrieveInformationModelFind
    )

    assert "SeriesInstanceUID" not in filtered
    assert "Modality" not in filtered
    assert filtered.StudyInstanceUID == "1.2.3"
    assert set(stripped) == {"SeriesInstanceUID", "Modality"}


def test_strip_sublevel_tags_no_op_at_deepest_level():
    ds = Dataset()
    ds.QueryRetrieveLevel = "IMAGE"
    ds.SOPInstanceUID = "1.2.3.4.5"

    filtered, stripped = _strip_sublevel_tags(
        ds, StudyRootQueryRetrieveInformationModelFind
    )

    assert stripped == []
    assert filtered is ds


# --- real loopback DIMSE tests: our own handlers behind a real pynetdicom AE ---


def _ct_dataset(patient_id="TESTPAT", study_uid=None, series_uid=None):
    """Build a file-backed CT dataset readable by dcmread()."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid or generate_uid()
    ds.SeriesInstanceUID = series_uid or generate_uid()
    ds.PatientID = patient_id
    ds.PatientName = "Test^Pat"
    ds.Modality = "CT"
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    buf.seek(0)
    return dcmread(buf)


class _Loopback:
    def __init__(self, repo, bus, port):
        self.repo = repo
        self.bus = bus
        self.port = port

    def associate(
        self,
        calling_ae_title="SCUTEST",
        store_handler=None,
        implementation_version_name=None,
        **kwargs,
    ):
        scu = AE(ae_title=calling_ae_title)
        if implementation_version_name:
            scu.implementation_version_name = implementation_version_name
        scu.add_requested_context(Verification)
        scu.add_requested_context(CTImageStorage)
        scu.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        scu.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        scu.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        roles = [build_role(CTImageStorage, scu_role=True, scp_role=True)]
        evt_handlers = [(evt.EVT_C_STORE, store_handler)] if store_handler else []
        return scu.associate(
            "127.0.0.1", self.port, ext_neg=roles, evt_handlers=evt_handlers, **kwargs
        )


@pytest.fixture
def loopback(tmp_path):
    bus = logging.getLogger(f"test-loopback-{tmp_path.name}")
    bus.setLevel(logging.INFO)

    repo = new_repo(None, new_store(str(tmp_path / "traces")))
    repo.start()

    scp = AE(ae_title="SCPTEST")
    scp.add_supported_context(Verification)
    scp.add_supported_context(CTImageStorage, scu_role=True, scp_role=True)
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    scp.add_supported_context(
        StudyRootQueryRetrieveInformationModelGet, scu_role=True, scp_role=True
    )
    scp.add_supported_context(
        StudyRootQueryRetrieveInformationModelMove, scu_role=True, scp_role=True
    )

    handlers = list(new_dimse_factory(repo, bus, max_store_bytes=4096).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    port = server.socket.getsockname()[1]

    yield _Loopback(repo, bus, port)

    server.shutdown()
    repo.stop()


def _find_query(study_uid):
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = study_uid
    ds.PatientID = ""
    return ds


def test_c_echo_returns_success(loopback):
    assoc = loopback.associate()
    status = assoc.send_c_echo()
    assert status.Status == 0x0000
    assoc.release()


def test_healthcheck_echo_succeeds_but_is_not_logged(loopback, caplog):
    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        assoc = loopback.associate(
            calling_ae_title="HEALTHCHK",
            implementation_version_name="DICOMHAWK_HC",
        )
        status = assoc.send_c_echo()
        assoc.release()
    assert status.Status == 0x0000
    assert not any(
        '"request_type":"C-ECHO"' in r.getMessage()
        or '"request_type":"Association Requested"' in r.getMessage()
        or '"request_type":"Association Released"' in r.getMessage()
        for r in caplog.records
    )


def test_ordinary_loopback_echo_is_still_logged(loopback, caplog):
    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        assoc = loopback.associate(calling_ae_title="SCUTEST")
        assoc.send_c_echo()
        assoc.release()
    assert any('"request_type":"C-ECHO"' in r.getMessage() for r in caplog.records)


def test_healthcheck_honors_called_and_calling_aet_policy(tmp_path, caplog):
    bus = logging.getLogger(f"test-health-auth-{tmp_path.name}")
    bus.setLevel(logging.INFO)
    repo = new_repo(None, new_store(str(tmp_path / "traces"))).start()
    scp = AE(ae_title="LOCKEDPACS")
    scp.require_called_aet = True
    scp.require_calling_aet = ["MONITOR"]
    scp.add_supported_context(Verification)
    handlers = list(new_dimse_factory(repo, bus).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    try:
        scu = AE(ae_title="MONITOR")
        scu.implementation_version_name = "DICOMHAWK_HC"
        scu.add_requested_context(Verification)
        with caplog.at_level(logging.INFO, logger=bus.name):
            assoc = scu.associate(
                "127.0.0.1", server.socket.getsockname()[1], ae_title="LOCKEDPACS"
            )
            assert assoc.is_established
            assert assoc.send_c_echo().Status == 0x0000
            assoc.release()
        assert not any("HEALTHCHK" in record.getMessage() for record in caplog.records)
        assert not any(
            '"request_type":"C-ECHO"' in record.getMessage()
            for record in caplog.records
        )
    finally:
        server.shutdown()
        repo.stop()


def test_c_store_quarantines_visible_in_find_but_blocked_on_get(loopback, caplog):
    assoc = loopback.associate()
    ds = _ct_dataset()

    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        store_status = assoc.send_c_store(ds)
    assert store_status.Status == 0x0000
    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if '"request_type":"C-STORE"' in record.getMessage()
    )
    assert event["artifact"]["sop_instance_uid"] == ds.SOPInstanceUID
    assert event["artifact"]["sop_class_uid"] == ds.SOPClassUID
    assert event["artifact"]["captured"] is True
    capture = loopback.repo.storage.traces_dir / event["artifact"]["filename"]
    raw = gzip.decompress(capture.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == event["artifact"]["sha256"]

    canonical = loopback.repo.storage.quarantine_dir / str(ds.SOPInstanceUID)
    assert canonical.read_bytes()[128:132] == b"DICM"
    stored = dcmread(canonical)
    assert stored.SOPInstanceUID == ds.SOPInstanceUID
    assert stored.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian

    find_results = list(
        assoc.send_c_find(
            _find_query(ds.StudyInstanceUID), StudyRootQueryRetrieveInformationModelFind
        )
    )
    pending = [r for r in find_results if r[0].Status == 0xFF00]
    assert len(pending) == 1
    assert pending[0][1].PatientID == "TESTPAT"

    get_results = list(
        assoc.send_c_get(
            _find_query(ds.StudyInstanceUID), StudyRootQueryRetrieveInformationModelGet
        )
    )
    assert any(status.Status == 0xA700 for status, _ in get_results)
    assoc.release()


def test_c_store_rejects_instance_over_configured_size(loopback):
    ds = _ct_dataset(patient_id="TOOBIG")
    ds.Rows = 100
    ds.Columns = 100
    ds.SamplesPerPixel = 1
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.PixelData = b"\0" * 10_000

    assoc = loopback.associate()
    status = assoc.send_c_store(ds)
    assoc.release()

    assert status.Status == int(QRStatus.STORE_ERROR)
    assert not (loopback.repo.storage.quarantine_dir / str(ds.SOPInstanceUID)).exists()


def test_c_store_submits_accepted_artifact_to_sink(tmp_path):
    """The injected ArtifactSink receives one SubmittedArtifact for an accepted C-STORE."""
    submitted = []
    bus = logging.getLogger(f"test-sink-{tmp_path.name}")
    bus.setLevel(logging.INFO)
    repo = new_repo(None, new_store(str(tmp_path / "traces"))).start()

    scp = AE(ae_title="SCPTEST")
    scp.add_supported_context(CTImageStorage, scu_role=True, scp_role=True)
    handlers = list(new_dimse_factory(repo, bus, sink=submitted.append).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    port = server.socket.getsockname()[1]
    try:
        scu = AE(ae_title="SCUTEST")
        scu.add_requested_context(CTImageStorage)
        assoc = scu.associate("127.0.0.1", port)
        ds = _ct_dataset()
        status = assoc.send_c_store(ds)
        assoc.release()
    finally:
        server.shutdown()
        repo.stop()

    assert status.Status == 0x0000
    assert len(submitted) == 1
    artifact = submitted[0]
    assert artifact.channel == "DIMSE"
    assert artifact.request_type == "C-STORE"
    assert artifact.disposition == "stored"
    assert artifact.source_encoding == "dimse-dataset"
    assert artifact.sop_instance_uid == str(ds.SOPInstanceUID)
    assert (
        artifact.transfer_syntax_uid
    )  # the association's actual negotiated syntax, not a guess
    assert artifact.capture.sha256


def test_c_store_submitted_artifact_carries_the_actual_negotiated_transfer_syntax(
    tmp_path,
):
    """Regression guard: the analyzer must not have to guess a DIMSE dataset's encoding."""
    from pydicom.uid import ExplicitVRBigEndian

    submitted = []
    bus = logging.getLogger(f"test-ts-{tmp_path.name}")
    repo = new_repo(None, new_store(str(tmp_path / "traces"))).start()
    scp = AE(ae_title="SCPTEST")
    scp.add_supported_context(
        CTImageStorage,
        transfer_syntax=[ExplicitVRBigEndian],
        scu_role=True,
        scp_role=True,
    )
    handlers = list(new_dimse_factory(repo, bus, sink=submitted.append).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    port = server.socket.getsockname()[1]
    try:
        scu = AE(ae_title="SCUTEST")
        scu.add_requested_context(CTImageStorage, transfer_syntax=[ExplicitVRBigEndian])
        assoc = scu.associate("127.0.0.1", port)
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRBigEndian
        ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
        uid = generate_uid()
        ds.file_meta.MediaStorageSOPInstanceUID = uid
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = uid
        ds.PatientID = "TESTPAT"
        ds.StudyInstanceUID = generate_uid()
        ds.SeriesInstanceUID = generate_uid()
        assoc.send_c_store(ds)
        assoc.release()
    finally:
        server.shutdown()
        repo.stop()

    assert len(submitted) == 1
    assert submitted[0].transfer_syntax_uid == str(ExplicitVRBigEndian)


def test_c_store_succeeds_even_when_the_artifact_sink_raises(tmp_path):
    """Analysis failures must never change what the peer sees; the payload is already captured."""

    def exploding_sink(_artifact):
        raise RuntimeError("analysis store unavailable")

    bus = logging.getLogger(f"test-sink-raise-{tmp_path.name}")
    repo = new_repo(None, new_store(str(tmp_path / "traces"))).start()
    scp = AE(ae_title="SCPTEST")
    scp.add_supported_context(CTImageStorage, scu_role=True, scp_role=True)
    handlers = list(new_dimse_factory(repo, bus, sink=exploding_sink).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    port = server.socket.getsockname()[1]
    try:
        scu = AE(ae_title="SCUTEST")
        scu.add_requested_context(CTImageStorage)
        assoc = scu.associate("127.0.0.1", port)
        status = assoc.send_c_store(_ct_dataset())
        assoc.release()
    finally:
        server.shutdown()
        repo.stop()

    assert status.Status == 0x0000


def test_c_get_retrieves_a_safely_seeded_instance(loopback):
    ds = _ct_dataset(patient_id="SAFEPAT")
    assert loopback.repo.store(ds, safe=True) is None

    received = []
    assoc = loopback.associate(
        store_handler=lambda e: (received.append(e.dataset), 0x0000)[1]
    )

    get_results = list(
        assoc.send_c_get(
            _find_query(ds.StudyInstanceUID), StudyRootQueryRetrieveInformationModelGet
        )
    )
    assert all(status.Status in (0xFF00, 0x0000) for status, _ in get_results)
    assert len(received) == 1
    assert received[0].SOPInstanceUID == ds.SOPInstanceUID
    assert str(received[0].PatientName) == "Test^Pat"
    assoc.release()


def test_c_find_dedups_to_one_row_per_study(loopback):
    study_uid = generate_uid()
    loopback.repo.store(_ct_dataset(study_uid=study_uid), safe=True)
    loopback.repo.store(_ct_dataset(study_uid=study_uid), safe=True)

    assoc = loopback.associate()
    results = list(
        assoc.send_c_find(
            _find_query(study_uid), StudyRootQueryRetrieveInformationModelFind
        )
    )
    pending = [r for r in results if r[0].Status == 0xFF00]
    assert len(pending) == 1
    assoc.release()


def test_c_find_ignores_a_private_creator_over_dimse(loopback):
    ds = _ct_dataset()
    loopback.repo.store(ds, safe=True)
    query = _find_query(ds.StudyInstanceUID)
    query.add_new((0x0009, 0x0010), "LO", "ACME")

    assoc = loopback.associate()
    results = list(assoc.send_c_find(query, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    assert all(status.Status in (0xFF00, 0x0000) for status, _ in results)
    assert (
        len([identifier for status, identifier in results if status.Status == 0xFF00])
        == 1
    )


def test_malformed_associate_is_logged_on_real_connection_close(
    loopback, caplog, monkeypatch
):
    monkeypatch.setattr("dicomhawk.handlers._LOOPBACK", frozenset())
    payload = _associate_rq(called=b"BAD\x1bAE", calling=b"ATTACKER")

    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        with socket.create_connection(("127.0.0.1", loopback.port), timeout=2) as peer:
            peer.sendall(payload)
            peer.shutdown(socket.SHUT_WR)
            try:
                peer.recv(1024)
            except (ConnectionError, TimeoutError):
                pass
        deadline = time.monotonic() + 2
        while "Connection Closed" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)

    assert "Connection Closed" in caplog.text
    assert "Association decoded: no" in caplog.text
    assert "Calling: ATTACKER" in caplog.text


def test_c_move_always_captures_and_rejects(loopback):
    ds = _ct_dataset()
    loopback.repo.store(ds, safe=True)

    assoc = loopback.associate()
    results = list(
        assoc.send_c_move(
            _find_query(ds.StudyInstanceUID),
            "SOMEWHERE",
            StudyRootQueryRetrieveInformationModelMove,
        )
    )
    assert results[-1][0].Status == 0xA801
    assoc.release()


def test_interaction_log_captures_association_lifecycle(loopback, caplog):
    with caplog.at_level(logging.INFO, logger=loopback.bus.name):
        assoc = loopback.associate(calling_ae_title="MYCALLER")
        assoc.send_c_echo()
        assoc.release()

    assert "Association Requested" in caplog.text
    assert "MYCALLER" in caplog.text
    assert "Association Released" in caplog.text


def test_association_rejected_for_disallowed_calling_aet(tmp_path, caplog):
    bus = logging.getLogger(f"test-reject-{tmp_path.name}")
    bus.setLevel(logging.WARNING)
    repo = new_repo(None, new_store(str(tmp_path / "traces")))
    repo.start()

    scp = AE(ae_title="SCPTEST")
    scp.add_supported_context(Verification)
    scp.require_calling_aet = ["ALLOWEDCALLER"]
    handlers = list(new_dimse_factory(repo, bus).values())
    server = scp.start_server(("127.0.0.1", 0), evt_handlers=handlers, block=False)
    port = server.socket.getsockname()[1]

    scu = AE(ae_title="NOTALLOWED")
    scu.add_requested_context(Verification)

    with caplog.at_level(logging.WARNING, logger=bus.name):
        assoc = scu.associate("127.0.0.1", port)

    assert not assoc.is_established
    assert assoc.is_rejected
    assert "Association Rejected" in caplog.text

    server.shutdown()
    repo.stop()


# --- C-FIND returns the attributes the Q/R index cannot hold ---


def _seed_study(loopback):
    """Seed one study so the detail index and the rollups both have something to answer with."""
    from seeding.seeder import new_seeder

    seeder = new_seeder(loopback.repo)
    assert seeder._seed_fallback(seeder._locations[0], "CT", "find-epoch") > 0
    (uid,) = loopback.repo.conn.execute(
        __import__("sqlalchemy").text("select study_instance_uid from instance limit 1")
    ).fetchone()
    return str(uid)


def _enriched_query(**keys):
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.PatientName = ""
    for keyword, value in keys.items():
        setattr(ds, keyword, value)
    return ds


def _find(assoc, query):
    return [
        ds
        for status, ds in assoc.send_c_find(
            query, StudyRootQueryRetrieveInformationModelFind
        )
        if ds is not None
    ]


def test_c_find_answers_attributes_the_index_has_no_column_for(loopback):
    """The web worklist shows these; a blank C-FIND would contradict it."""
    _seed_study(loopback)
    assoc = loopback.associate()

    responses = _find(
        assoc,
        _enriched_query(
            PatientSex="",
            PatientBirthDate="",
            StudyDescription="",
            InstitutionName="",
            BodyPartExamined="",
            ReferringPhysicianName="",
        ),
    )
    assoc.release()

    assert responses
    answer = responses[0]
    assert answer.PatientSex in ("M", "F")
    assert answer.PatientBirthDate
    assert answer.StudyDescription
    assert answer.InstitutionName
    assert answer.BodyPartExamined
    assert answer.ReferringPhysicianName


def test_c_find_answers_modalities_and_instance_count(loopback):
    _seed_study(loopback)
    assoc = loopback.associate()

    responses = _find(
        assoc, _enriched_query(ModalitiesInStudy="", NumberOfStudyRelatedInstances="")
    )
    assoc.release()

    assert responses
    assert "CT" in str(responses[0].ModalitiesInStudy)
    assert int(responses[0].NumberOfStudyRelatedInstances) > 0


def test_c_find_matches_the_web_worklist_field_for_field(loopback, tmp_path):
    """The two surfaces must agree; a difference is exactly what an attacker looks for."""
    from profiles.profile import load_profile
    from web.app import new_web

    _seed_study(loopback)
    client = new_web(
        load_profile("fujifilm"), loopback.repo, loopback.bus
    ).test_client()
    client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": "svc_dicom", "password": "svc_dicom"},
    )
    page = client.get("/WorkflowUI/").get_data(as_text=True)

    assoc = loopback.associate()
    answer = _find(
        assoc,
        _enriched_query(
            PatientSex="", PatientBirthDate="", StudyDescription="", InstitutionName=""
        ),
    )[0]
    assoc.release()

    for value in (
        str(answer.StudyDescription),
        str(answer.InstitutionName),
        str(answer.PatientBirthDate),
    ):
        assert value in page


@pytest.mark.parametrize(
    "keyword,matching,non_matching",
    [
        ("StudyDescription", "*CHEST*", "*KNEE*"),
        ("BodyPartExamined", "CHEST", "ABDOMEN"),
        ("StudyDescription", "CT*", "MR*"),
    ],
)
def test_c_find_filters_on_an_enriched_key(loopback, keyword, matching, non_matching):
    """Returning every study for a filtered query would be its own tell."""
    _seed_study(loopback)
    assoc = loopback.associate()

    hits = _find(assoc, _enriched_query(**{keyword: matching}))
    misses = _find(assoc, _enriched_query(**{keyword: non_matching}))
    assoc.release()

    assert len(hits) == 1
    assert misses == []


def test_c_find_without_enriched_keys_is_unchanged(loopback):
    """A query that asks for none of them must cost nothing and gain no extra elements."""
    study_uid = _seed_study(loopback)
    assoc = loopback.associate()

    responses = _find(assoc, _find_query(study_uid))
    assoc.release()

    assert len(responses) == 1
    assert sorted(e.keyword for e in responses[0]) == [
        "PatientID",
        "QueryRetrieveLevel",
        "RetrieveAETitle",
        "StudyInstanceUID",
    ]


def test_c_find_enrichment_survives_a_dead_detail_index(loopback, monkeypatch):
    """A failed rollup must degrade to blank values, never change the C-FIND status."""
    _seed_study(loopback)
    monkeypatch.setattr(loopback.repo, "study_details", lambda uids: {})
    assoc = loopback.associate()

    responses = _find(assoc, _enriched_query(PatientSex="", StudyDescription=""))
    assoc.release()

    assert len(responses) == 1
    assert str(responses[0].PatientSex) == ""


@pytest.mark.parametrize(
    "value,query,expected",
    [
        ("CT CHEST", "", True),
        ("CT CHEST", None, True),
        ("CT CHEST", "CT CHEST", True),
        ("CT CHEST", "ct chest", True),
        ("CT CHEST", "MR BRAIN", False),
        ("CT CHEST", "*CHEST*", True),
        ("CT CHEST", "CT*", True),
        ("CT CHEST", "*BRAIN*", False),
        ("", "CT", False),
    ],
)
def test_enriched_match_cases(value, query, expected):
    from dicomhawk.handlers import _enriched_match

    assert _enriched_match(value, query) is expected
