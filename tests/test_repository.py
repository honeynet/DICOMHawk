import io
from concurrent.futures import ThreadPoolExecutor

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


def _part10_bytes(ds):
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


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
    assert repo.store(ds, safe=False, raw_bytes=_part10_bytes(ds)) is None
    assert (repo.storage.quarantine_dir / str(ds.SOPInstanceUID)).exists()
    assert any(repo.storage.traces_dir.glob("*.dcm.gz"))  # raw pre-parse forensic copy


def test_store_writes_part10_canonical_file_atomically(repo):
    ds = _ct_dataset()
    ds.file_meta = FileMetaDataset()

    assert repo.store(ds, safe=False, raw_bytes=b"exact incoming bytes") is None

    path = repo.storage.quarantine_dir / str(ds.SOPInstanceUID)
    assert path.read_bytes()[128:132] == b"DICM"
    stored = dcmread(path)
    assert stored.SOPInstanceUID == ds.SOPInstanceUID
    assert stored.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
    assert stored.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert not list(repo.storage.quarantine_dir.glob(".*.tmp"))


def test_store_does_not_report_success_when_forensic_capture_fails(repo, monkeypatch):
    def fail_capture(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(repo.storage, "capture", fail_capture)
    err = repo.store(_ct_dataset(), raw_bytes=b"exact incoming bytes")
    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "quarantine" in err.error


def test_store_rejects_untrusted_dataset_without_exact_wire_bytes(repo):
    ds = _ct_dataset()

    err = repo.store(ds, safe=False)

    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "exact incoming bytes" in err.error.lower()
    assert not any(repo.storage.traces_dir.glob("*.dcm.gz"))


def test_store_rejects_missing_sop_instance_uid(repo):
    ds = Dataset()
    ds.PatientID = "X"
    err = repo.store(ds, safe=True)
    assert err is not None
    assert err.status == QRStatus.FAILURE


def test_store_rejects_missing_sop_class_uid(repo):
    ds = _ct_dataset()
    del ds.SOPClassUID
    err = repo.store(ds, safe=True)
    assert err is not None
    assert err.status == QRStatus.FAILURE
    assert "SOPClassUID" in err.error


def test_store_blocks_path_traversal_in_sop_instance_uid(repo):
    ds = _ct_dataset()
    ds.SOPInstanceUID = "../../../etc/passwd"
    err = repo.store(ds, safe=True)
    assert err is not None
    assert err.status == QRStatus.FAILURE
    assert "Dangerous" in err.error


def test_store_rejects_overlong_sop_instance_uid_without_touching_filesystem(repo):
    ds = _ct_dataset()
    ds.SOPInstanceUID = "1." + ("2" * 300)

    err = repo.store(ds, safe=True)

    assert err is not None
    assert err.status == QRStatus.FAILURE
    assert "64 characters" in err.error
    assert not any(repo.storage.storage_dir.iterdir())


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


def test_store_reports_dataset_sop_class_mismatch_not_resource_exhaustion(repo):
    ds = _ct_dataset()

    err = repo.store(ds, safe=True, expected_sop_class_uid="1.2.3.4")

    assert err is not None
    assert err.status == QRStatus.SOP_CLASS_INVALID


def test_store_reports_database_index_failure(repo, monkeypatch):
    ds = _ct_dataset()

    def fail_index(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(qrdb, "add_instance", fail_index)
    err = repo.store(ds, safe=True)

    assert err is not None
    assert err.status == QRStatus.STORE_ERROR
    assert "database unavailable" in err.error


def test_in_memory_repository_serializes_concurrent_writes(repo):
    datasets = [_ct_dataset() for _ in range(24)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        errors = list(executor.map(lambda ds: repo.store(ds, safe=True), datasets))

    assert errors == [None] * len(datasets)
    assert repo.conn.query(qrdb.Instance).count() == len(datasets)


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


@pytest.mark.parametrize(
    ("tag", "vr", "value"),
    [
        ((0x0009, 0x0010), "LO", "ACME"),
        ((0x0009, 0x1001), "SQ", [Dataset()]),
        ((0x7777, 0x0010), "LO", "UNKNOWN"),
    ],
)
def test_find_ignores_private_and_unknown_optional_keys(repo, tag, vr, value):
    stored = _ct_dataset()
    repo.store(stored, safe=True)
    query = Dataset()
    query.QueryRetrieveLevel = "STUDY"
    query.StudyInstanceUID = stored.StudyInstanceUID
    query.add_new(tag, vr, value)

    result = repo.find(query, StudyRootQueryRetrieveInformationModelFind)

    assert result.error is None
    assert [match.study_instance_uid for match in result.matches] == [
        str(stored.StudyInstanceUID)
    ]


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


def test_find_page_default_ordering_is_unchanged(repo):
    """The new order_col/descending params must be additive for every existing caller."""
    for study_date in ("20200101", "20220202", "20210303"):
        ds = _ct_dataset()
        ds.StudyDate = study_date
        repo.store(ds, safe=True)

    query = Dataset()
    query.QueryRetrieveLevel = "STUDY"
    query.StudyInstanceUID = ""
    query.StudyDate = ""
    default = repo.find_page(
        query,
        StudyRootQueryRetrieveInformationModelFind,
        dedup_col="study_instance_uid",
        offset=0,
        limit=10,
    )
    explicit = repo.find_page(
        query,
        StudyRootQueryRetrieveInformationModelFind,
        dedup_col="study_instance_uid",
        offset=0,
        limit=10,
        order_col=None,
        descending=False,
    )

    assert [m.study_instance_uid for m in default.matches] == [
        m.study_instance_uid for m in explicit.matches
    ]


def test_find_page_orders_by_another_column_descending(repo):
    for study_date in ("20200101", "20220202", "20210303"):
        ds = _ct_dataset()
        ds.StudyDate = study_date
        repo.store(ds, safe=True)

    query = Dataset()
    query.QueryRetrieveLevel = "STUDY"
    query.StudyInstanceUID = ""
    query.StudyDate = ""
    result = repo.find_page(
        query,
        StudyRootQueryRetrieveInformationModelFind,
        dedup_col="study_instance_uid",
        offset=0,
        limit=10,
        order_col="study_date",
        descending=True,
    )

    assert result.error is None
    assert [m.study_date for m in result.matches] == [
        "20220202",
        "20210303",
        "20200101",
    ]


# --- worklist rollups ---


def test_count_studies_counts_distinct_studies(repo):
    study_uid = generate_uid()
    repo.store(_ct_dataset(study_uid=study_uid), safe=True)
    repo.store(_ct_dataset(study_uid=study_uid), safe=True)
    repo.store(_ct_dataset(), safe=True)

    assert repo.count_studies() == 2


def test_count_instances_groups_by_study(repo):
    busy, quiet = generate_uid(), generate_uid()
    for _ in range(3):
        repo.store(_ct_dataset(study_uid=busy), safe=True)
    repo.store(_ct_dataset(study_uid=quiet), safe=True)

    assert repo.count_instances([str(busy), str(quiet)]) == {
        str(busy): 3,
        str(quiet): 1,
    }


def test_count_instances_ignores_empty_and_unknown_uids(repo):
    repo.store(_ct_dataset(), safe=True)

    assert repo.count_instances([]) == {}
    assert repo.count_instances(["1.2.3.not.stored"]) == {}


def test_count_instances_truncates_an_oversized_uid_list(repo):
    """A real UID pushed past the cap must be dropped, proving the IN list is bounded."""
    study_uid = str(generate_uid())
    repo.store(_ct_dataset(study_uid=study_uid), safe=True)

    padding = [f"1.2.3.{i}" for i in range(5000)]
    within_cap = repo.count_instances([study_uid] + padding)
    beyond_cap = repo.count_instances(padding + [study_uid])

    assert within_cap == {study_uid: 1}
    assert beyond_cap == {}


def test_study_modalities_dedups_per_study(repo):
    study_uid = generate_uid()
    first = _ct_dataset(study_uid=study_uid)
    second = _ct_dataset(study_uid=study_uid)
    second.Modality = "MR"
    third = _ct_dataset(study_uid=study_uid)
    for ds in (first, second, third):
        repo.store(ds, safe=True)

    assert repo.study_modalities([str(study_uid)]) == {str(study_uid): ["CT", "MR"]}


def test_rollups_degrade_to_empty_when_the_query_fails(repo):
    study_uid = str(generate_uid())
    repo.store(_ct_dataset(study_uid=study_uid), safe=True)
    repo.stop()

    # A dead engine must never surface as an exception on an attacker-visible page.
    assert repo.count_studies() == 0
    assert repo.count_instances([study_uid]) == {}
    assert repo.study_modalities([study_uid]) == {}
    assert repo.study_details([study_uid]) == {}


# --- study detail index ---


def test_store_indexes_attributes_the_qr_schema_cannot_hold(repo):
    ds = _ct_dataset()
    ds.PatientSex = "F"
    ds.PatientBirthDate = "19670202"
    ds.StudyDescription = "CT CHEST W/O CONTRAST"
    ds.BodyPartExamined = "CHEST"
    ds.InstitutionName = "Riverside General Hospital"
    ds.StationName = "CT03"
    ds.ReferringPhysicianName = "Robles^Joshua"
    repo.store(ds, safe=True)

    detail = repo.study_details([str(ds.StudyInstanceUID)])[str(ds.StudyInstanceUID)]

    assert detail["patient_sex"] == "F"
    assert detail["patient_birth_date"] == "19670202"
    assert detail["study_description"] == "CT CHEST W/O CONTRAST"
    assert detail["body_part"] == "CHEST"
    assert detail["institution_name"] == "Riverside General Hospital"
    assert detail["station_name"] == "CT03"
    assert detail["referring_physician"] == "Robles^Joshua"


def test_store_upserts_the_detail_row_instead_of_duplicating_it(repo):
    study_uid = generate_uid()
    first = _ct_dataset(study_uid=study_uid)
    first.InstitutionName = "First Hospital"
    repo.store(first, safe=True)
    second = _ct_dataset(study_uid=study_uid)
    second.InstitutionName = "Second Hospital"
    repo.store(second, safe=True)

    details = repo.study_details([str(study_uid)])

    assert len(details) == 1
    assert details[str(study_uid)]["institution_name"] == "Second Hospital"


def test_store_bounds_an_oversized_detail_value(repo):
    ds = _ct_dataset()
    ds.StudyDescription = "A" * 5000
    repo.store(ds, safe=True)

    detail = repo.study_details([str(ds.StudyInstanceUID)])[str(ds.StudyInstanceUID)]

    assert len(detail["study_description"]) == 64


def test_study_details_returns_empty_strings_for_absent_attributes(repo):
    ds = _ct_dataset()
    repo.store(ds, safe=True)

    detail = repo.study_details([str(ds.StudyInstanceUID)])[str(ds.StudyInstanceUID)]

    assert detail["study_description"] == ""
    assert detail["patient_sex"] == ""


def test_study_details_ignores_empty_and_unknown_uids(repo):
    repo.store(_ct_dataset(), safe=True)

    assert repo.study_details([]) == {}
    assert repo.study_details(["1.2.3.not.stored"]) == {}


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
    repo.store(ds, safe=False, raw_bytes=_part10_bytes(ds))
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
