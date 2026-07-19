"""Handler unit tests and real loopback DIMSE integration tests."""

import io
import json
import logging
import tempfile

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
    _strip_sublevel_tags,
    handle_abort,
    handle_associate,
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
    def __init__(self, primitive=None, assoc=None, address=None):
        self.primitive = primitive
        self.assoc = assoc if assoc is not None else _FakeAssoc()
        if address is not None:
            self.address = address


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

    def associate(self, calling_ae_title="SCUTEST", store_handler=None, **kwargs):
        scu = AE(ae_title=calling_ae_title)
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
