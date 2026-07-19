import io

import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom.apps.qrscp import db as qrdb
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from dicomhawk.repository import new_repo
from dicomhawk.status import QRStatus
from dicomhawk.storage import new_store


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


@pytest.fixture
def repo(tmp_path):
    r = new_repo(None, new_store(str(tmp_path / "traces")))
    r.start()
    return r


class _FakeRequest:
    def __init__(self, sop_class_uid):
        self.AffectedSOPClassUID = sop_class_uid


class _FakeQREvent:
    def __init__(self, sop_class_uid, identifier):
        self.request = _FakeRequest(sop_class_uid)
        self.identifier = identifier


# --- store() ---


def test_store_safe_writes_under_storage_dir(repo):
    ds = _ct_dataset()
    assert repo.store(ds, safe=True) is None
    assert (repo.storage.storage_dir / str(ds.SOPInstanceUID)).exists()


def test_store_unsafe_quarantines_and_keeps_a_raw_capture(repo):
    ds = _ct_dataset()
    assert repo.store(ds, safe=False) is None
    assert (repo.storage.quarantine_dir / str(ds.SOPInstanceUID)).exists()
    assert any(repo.storage.traces_dir.glob("*.dcm.gz"))  # raw pre-parse forensic copy


def test_store_does_not_report_success_when_forensic_capture_fails(repo, monkeypatch):
    def fail_capture(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(repo.storage, "capture", fail_capture)
    err = repo.store(_ct_dataset(), raw_bytes=b"exact incoming bytes")
    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "quarantine" in err.error


def test_store_rejects_missing_sop_instance_uid(repo):
    ds = Dataset()
    ds.PatientID = "X"
    err = repo.store(ds, safe=True)
    assert err is not None
    assert err.status == QRStatus.STORE_ERROR


def test_store_blocks_path_traversal_in_sop_instance_uid(repo):
    ds = _ct_dataset()
    ds.SOPInstanceUID = "../../../etc/passwd"
    err = repo.store(ds, safe=True)
    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "Dangerous" in err.error


def test_store_with_missing_identity_keys_writes_file_but_skips_indexing(repo):
    ds = _ct_dataset()
    del ds.PatientID  # one of INDEX_REQUIRED_KEYS
    assert repo.store(ds, safe=True) is None
    assert (repo.storage.storage_dir / str(ds.SOPInstanceUID)).exists()
    indexed = (
        repo.conn.query(qrdb.Instance)
        .filter(qrdb.Instance.sop_instance_uid == str(ds.SOPInstanceUID))
        .count()
    )
    assert indexed == 0


def test_store_reports_database_index_failure(repo, monkeypatch):
    ds = _ct_dataset()

    def fail_index(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(qrdb, "add_instance", fail_index)
    err = repo.store(ds, safe=True)

    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "database unavailable" in err.error


# --- find() ---


def test_find_universal_matching_returns_all_when_keys_empty(repo):
    a, b = _ct_dataset(), _ct_dataset()
    repo.store(a, safe=True)
    repo.store(b, safe=True)

    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = ""
    ds.PatientID = ""
    result = repo.find(ds, StudyRootQueryRetrieveInformationModelFind)

    assert result.error is None
    uids = {m.study_instance_uid for m in result.matches}
    assert {str(a.StudyInstanceUID), str(b.StudyInstanceUID)} <= uids


def test_find_filters_by_a_specific_key(repo):
    a, b = _ct_dataset(), _ct_dataset()
    repo.store(a, safe=True)
    repo.store(b, safe=True)

    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = str(a.StudyInstanceUID)
    result = repo.find(ds, StudyRootQueryRetrieveInformationModelFind)

    assert result.error is None
    assert {m.study_instance_uid for m in result.matches} == {str(a.StudyInstanceUID)}


def test_find_page_limits_at_the_database_and_deduplicates(repo):
    study_uid = generate_uid()
    first = _ct_dataset(study_uid=study_uid)
    second = _ct_dataset(study_uid=study_uid)
    repo.store(first, safe=True)
    repo.store(second, safe=True)

    ds = Dataset()
    ds.QueryRetrieveLevel = "SERIES"
    ds.StudyInstanceUID = ""
    ds.SeriesInstanceUID = ""
    result = repo.find_page(
        ds,
        StudyRootQueryRetrieveInformationModelFind,
        dedup_col="series_instance_uid",
        offset=0,
        limit=1,
    )

    assert result.error is None
    assert len(result.matches) == 1


# --- eval_qr() ---


def test_eval_qr_rejects_unsupported_sop_class(repo):
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    err = repo.eval_qr(_FakeQREvent("1.2.9.9.9.not.supported", ds))
    assert err is not None
    assert err.status == QRStatus.SOP_CLASS_NOT_SUPPORTED


def test_eval_qr_rejects_missing_query_retrieve_level(repo):
    ds = Dataset()
    err = repo.eval_qr(_FakeQREvent(StudyRootQueryRetrieveInformationModelFind, ds))
    assert err is not None
    assert err.status == QRStatus.SOP_CLASS_INVALID


def test_eval_qr_honors_custom_missing_level_status(repo):
    ds = Dataset()
    err = repo.eval_qr(
        _FakeQREvent(StudyRootQueryRetrieveInformationModelFind, ds),
        missing_level_status=QRStatus.INVALID_REQUEST,
    )
    assert err is not None
    assert err.status == QRStatus.INVALID_REQUEST


def test_eval_qr_accepts_a_valid_request(repo):
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    assert (
        repo.eval_qr(_FakeQREvent(StudyRootQueryRetrieveInformationModelFind, ds))
        is None
    )


# --- find_instance() ---


def test_find_instance_blocks_quarantined_instances(repo):
    ds = _ct_dataset()
    repo.store(ds, safe=False)
    match = Dataset()
    match.filename = str(repo.storage.quarantine_dir / str(ds.SOPInstanceUID))

    result = repo.find_instance(match)

    assert result.error is not None
    assert result.error.status == QRStatus.STORE_ERROR


def test_find_instance_reads_back_a_safe_instance(repo):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    match = Dataset()
    match.filename = str(repo.storage.storage_dir / str(ds.SOPInstanceUID))

    result = repo.find_instance(match)

    assert result.error is None
    assert result.dataset.SOPInstanceUID == ds.SOPInstanceUID
