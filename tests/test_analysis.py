import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from analysis import analyzers, worker, yara_engine
from analysis.config import AnalysisConfig
from analysis.store import MAX_ATTEMPTS, AnalysisState, new_analysis_store
from dicomhawk.storage import SubmittedArtifact, new_store


def _ct_dataset():
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID = "P1"
    ds.Modality = "CT"
    return ds


def _part10_bytes(ds) -> bytes:
    buf = BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


# --- analyzers ---


def test_compute_hashes_matches_known_values():
    data = b"hello world"
    result = analyzers.compute_hashes(data)
    assert result["md5"] == hashlib.md5(data).hexdigest()
    assert result["sha1"] == hashlib.sha1(data).hexdigest()


def test_shannon_entropy_zero_for_constant_data():
    assert analyzers.shannon_entropy(b"\x00" * 100) == 0.0


def test_shannon_entropy_positive_for_varied_data():
    assert analyzers.shannon_entropy(bytes(range(256))) > 7.9


def test_shannon_entropy_empty_data():
    assert analyzers.shannon_entropy(b"") == 0.0


def test_extract_iocs_finds_url_ip_email_ascii():
    blob = b"contact admin@example.com via http://10.0.0.5/panel or 8.8.8.8"
    iocs = analyzers.extract_iocs(blob)
    assert "http://10.0.0.5/panel" in iocs["urls"]
    assert "8.8.8.8" in iocs["ips"]
    assert "admin@example.com" in iocs["emails"]


def test_extract_iocs_finds_utf16le_encoded_values():
    blob = "http://evil.example/x".encode("utf-16-le")
    iocs = analyzers.extract_iocs(blob)
    assert any("evil.example" in url for url in iocs["urls"])


def test_extract_iocs_caps_count():
    blob = b" ".join(f"1.2.3.{i}".encode() for i in range(1, 200))
    iocs = analyzers.extract_iocs(blob)
    assert len(iocs["ips"]) <= 50


def test_extract_iocs_does_not_flag_dicom_uids_as_ips():
    """A DICOM UID is a long dotted-decimal run and must not be mistaken for an embedded IPv4."""
    uid = b"1.2.840.10008.5.1.4.1.1.104.1 and transfer syntax 1.2.840.10008.1.2.1"
    iocs = analyzers.extract_iocs(uid)
    assert iocs["ips"] == []


def test_extract_dicom_metadata_part10_reports_fields():
    ds = _ct_dataset()
    meta = analyzers.extract_dicom_metadata(_part10_bytes(ds), "part10")
    assert meta["sop_class_uid"] == str(CTImageStorage)
    assert meta["modality"] == "CT"
    assert meta["transfer_syntax_uid"] == str(ExplicitVRLittleEndian)
    assert meta["has_pixel_data"] is False
    assert meta["parse_assumption"] is None


def test_extract_dicom_metadata_dimse_dataset_best_effort():
    ds = _ct_dataset()
    del ds.file_meta
    ds.is_implicit_VR = False
    ds.is_little_endian = True
    raw = BytesIO()
    ds.save_as(raw, enforce_file_format=False)
    meta = analyzers.extract_dicom_metadata(raw.getvalue(), "dimse-dataset")
    assert meta is not None
    assert meta["sop_class_uid"] == str(CTImageStorage)
    assert meta["parse_assumption"] is not None


def test_extract_dicom_metadata_returns_none_for_non_dicom():
    assert (
        analyzers.extract_dicom_metadata(b"not a dicom file at all", "part10") is None
    )


def test_read_capture_truncates_at_max_bytes(tmp_path):
    storage = new_store(str(tmp_path / "traces"))
    payload = b"x" * 1000
    capture = storage.capture(payload)
    data, truncated = analyzers.read_capture(capture.path, max_bytes=100)
    assert truncated is True
    assert len(data) == 100


def test_read_capture_not_truncated_when_under_cap(tmp_path):
    storage = new_store(str(tmp_path / "traces"))
    capture = storage.capture(b"short")
    data, truncated = analyzers.read_capture(capture.path, max_bytes=100)
    assert truncated is False
    assert data == b"short"


# --- yara_engine ---


def test_yara_compile_shipped_rules_succeeds():
    rules, ruleset_hash, problems = yara_engine.compile_rules(worker.RULES_DIR)
    assert rules is not None
    assert ruleset_hash is not None
    assert problems == []


def test_yara_scan_detects_eicar_string():
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(
        rules, b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    assert state is None
    assert any(m["rule"] == "EICAR_Test_String" for m in matches)


def test_yara_scan_clean_data_no_matches():
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(
        rules, b"ordinary dicom-ish bytes with nothing suspicious"
    )
    assert matches == []
    assert state is None


def _bare_part10(payload: bytes) -> bytes:
    return b"\x00" * 128 + b"DICM" + payload


def test_yara_detects_pe_dicom_polyglot():
    pe = bytearray(b"\x00" * 512)
    pe[0:2] = b"MZ"
    pe[0x3C:0x40] = (0x90).to_bytes(4, "little")  # e_lfanew -> past the DICM region
    pe[0x90:0x94] = b"PE\x00\x00"
    pe[0x90 + 6 : 0x90 + 8] = (1).to_bytes(2, "little")  # NumberOfSections
    pe[128:132] = b"DICM"
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(rules, bytes(pe))
    assert state is None
    assert {"DICOM_PE_Polyglot_Active", "DICOM_PE_Polyglot_PE_Header_After_DICM"} <= {
        m["rule"] for m in matches
    }


def test_yara_detects_orthanc_config_preamble_polyglot():
    preamble = b'{"ExecuteLuaEnabled":true,"RemoteAccessAllowed":true}\x00'
    data = preamble.ljust(128, b"\x00") + b"DICM" + b"\x00" * 8
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(rules, data)
    assert state is None
    assert any(
        m["rule"] == "DICOM_Orthanc_Config_Preamble_CVE_2023_33466" for m in matches
    )


def test_yara_detects_orthanc_cve_2026_5442_exact_dimensions():
    rows = bytes(
        [0x28, 0x00, 0x10, 0x00, 0x55, 0x4C, 0x04, 0x00, 0x00, 0x00, 0x01, 0x00]
    )
    columns = bytes(
        [0x28, 0x00, 0x11, 0x00, 0x55, 0x4C, 0x04, 0x00, 0x00, 0x00, 0x01, 0x00]
    )
    uid = b"1.2.840.10008.1.2.1"  # Explicit VR Little Endian, required by the generic VR-anomaly rule
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(rules, _bare_part10(uid + rows + columns))
    assert state is None
    assert {
        "DICOM_Orthanc_CVE_2026_5442_Known_Test",
        "DICOM_Rows_Columns_Encoded_As_UL",
    } <= {m["rule"] for m in matches}


def test_yara_detects_zip_declared_size_exhaustion():
    header = bytearray(b"\x00" * 40)
    header[0:4] = bytes([0x50, 0x4B, 0x03, 0x04])
    header[18:22] = (100).to_bytes(4, "little")  # compressed size
    header[22:26] = (0x40000000).to_bytes(4, "little")  # uncompressed size, 1 GiB
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(rules, bytes(header))
    assert state is None
    assert any(
        m["rule"] == "Orthanc_ZIP_Declared_Size_Exhaustion_CVE_2026_5439"
        for m in matches
    )


def test_yara_dicom_uid_text_alone_does_not_trip_structural_rules():
    """Regression guard alongside the IOC-regex fix: a plain UID is not exploit content."""
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(
        rules, _bare_part10(b"1.2.840.10008.5.1.4.1.1.104.1")
    )
    assert state is None
    assert matches == []


def test_yara_invalid_operator_rule_is_skipped_not_fatal(tmp_path):
    bad = tmp_path / "bad.yar"
    bad.write_text("this is not valid yara syntax {{{")
    rules, ruleset_hash, problems = yara_engine.compile_rules(
        worker.RULES_DIR, str(tmp_path)
    )
    assert rules is not None  # shipped rules still compiled
    assert any("bad.yar" in p for p in problems)


def test_yara_scan_with_no_rules_returns_empty():
    matches, state = yara_engine.scan(None, b"anything")
    assert matches == []
    assert state is None


# --- AnalysisStore ---


def _artifact(tmp_path, payload=b"payload bytes", **kwargs) -> SubmittedArtifact:
    storage = new_store(str(tmp_path / "traces"))
    capture = storage.capture(payload)
    defaults = dict(
        channel="DIMSE",
        request_type="C-STORE",
        disposition="stored",
        source_encoding="dimse-dataset",
        session_id="sess-1",
        ip="10.0.0.1",
        local_port=104,
    )
    defaults.update(kwargs)
    return SubmittedArtifact(capture, **defaults)


@pytest.fixture
def store(tmp_path):
    s = new_analysis_store(str(tmp_path / "analysis.db")).start()
    yield s
    s.stop()


def test_enqueue_then_claim_moves_pending_to_running(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    record = store.get(artifact_id)
    assert record.state == AnalysisState.PENDING

    claimed = store.claim(artifact_id)
    assert claimed.state == AnalysisState.RUNNING
    assert claimed.attempts == 1


def test_claim_twice_second_returns_none(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    assert store.claim(artifact_id) is not None
    assert store.claim(artifact_id) is None


def test_complete_records_result_and_matched_rules(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    store.claim(artifact_id)
    store.complete(
        artifact_id,
        result={"entropy": 1.0},
        analyzer_version="1",
        ruleset_version="abc123",
        matched_rules=["Embedded_Windows_PE"],
    )
    record = store.get(artifact_id)
    assert record.state == AnalysisState.COMPLETED
    assert record.result == {"entropy": 1.0}
    assert record.matched_rules == "Embedded_Windows_PE"


def test_fail_records_error_state(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    store.claim(artifact_id)
    store.fail(artifact_id, "boom")
    record = store.get(artifact_id)
    assert record.state == AnalysisState.FAILED
    assert record.error == "boom"


def test_recover_stale_moves_running_back_to_pending(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    store.claim(artifact_id)
    assert store.get(artifact_id).state == AnalysisState.RUNNING

    recovered = store.recover_stale()
    assert recovered == 1
    assert store.get(artifact_id).state == AnalysisState.PENDING


def test_list_artifacts_filters_by_state_and_channel(store, tmp_path):
    a = store.enqueue_pending(_artifact(tmp_path, channel="DIMSE"))
    b = store.enqueue_pending(_artifact(tmp_path, channel="WEB"))
    store.claim(a)
    store.complete(a, result={}, analyzer_version="1", ruleset_version=None)

    completed, total = store.list_artifacts(state="completed")
    assert total == 1
    assert completed[0].artifact_id == a

    web_only, total = store.list_artifacts(channel="WEB")
    assert total == 1
    assert web_only[0].artifact_id == b


# --- worker (direct function calls, no subprocess) ---


def test_run_job_completes_end_to_end(store, tmp_path):
    from analysis.config import new_analysis_config

    config = new_analysis_config(max_bytes=1024, timeout=5.0)
    artifact = _artifact(tmp_path, payload=b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
    artifact_id = store.enqueue_pending(artifact)
    rules, ruleset_hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)

    class _FakeBus:
        def info(self, _event):
            pass

        def warning(self, _event):
            pass

        def error(self, _event):
            pass

    worker._run_job(store, _FakeBus(), config, rules, ruleset_hash, artifact_id)

    record = store.get(artifact_id)
    assert record.state == AnalysisState.COMPLETED
    assert "EICAR_Test_String" in (record.matched_rules or "")
    assert record.analyzer_version == worker.ANALYZER_VERSION


def test_run_job_marks_missing_when_capture_file_gone(store, tmp_path):
    from analysis.config import new_analysis_config

    config = new_analysis_config()
    artifact = _artifact(tmp_path)
    artifact_id = store.enqueue_pending(artifact)
    Path(artifact.capture.path).unlink()

    class _FakeBus:
        def info(self, _event):
            pass

        def warning(self, _event):
            pass

        def error(self, _event):
            pass

    worker._run_job(store, _FakeBus(), config, None, None, artifact_id)

    record = store.get(artifact_id)
    assert record.state == AnalysisState.MISSING


def test_never_analyzes_safe_seeded_objects(tmp_path):
    """repo.store(safe=True) must never invoke on_captured; the sink contract's core guarantee."""
    from dicomhawk.repository import new_repo

    storage = new_store(str(tmp_path / "traces"))
    repo = new_repo(None, storage).start()
    captured = []
    err = repo.store(_ct_dataset(), safe=True, on_captured=captured.append)
    assert err is None
    assert captured == []
    repo.stop()


# --- regression guards for the 2026-07-31 hardening pass ---


def test_recover_stale_gives_up_on_a_payload_that_keeps_killing_the_worker(
    store, tmp_path
):
    """Without a cap, a payload that crashes the worker is fed back to every fresh one forever."""
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    for _ in range(MAX_ATTEMPTS - 1):
        store.claim(artifact_id)
        assert store.recover_stale() == 1
    store.claim(artifact_id)

    assert store.recover_stale() == 0
    record = store.get(artifact_id)
    assert record.state == AnalysisState.FAILED
    assert str(MAX_ATTEMPTS) in record.error


def test_rule_filter_treats_like_metacharacters_literally(store, tmp_path):
    artifact_id = store.enqueue_pending(_artifact(tmp_path))
    store.complete(
        artifact_id,
        result={},
        analyzer_version="1",
        ruleset_version="r",
        matched_rules=["EICAR_Test_String"],
    )

    assert store.list_artifacts(rule="EICAR_Test_String")[1] == 1
    assert store.list_artifacts(rule="EICAR")[1] == 1
    assert (
        store.list_artifacts(rule="%")[1] == 0
    )  # would match everything as a raw LIKE
    assert (
        store.list_artifacts(rule="E_C_R_Test_String")[1] == 0
    )  # '_' is not a wildcard


def test_failed_commit_does_not_poison_the_session(store, tmp_path):
    """One transient DB error must not break every later enqueue on the same thread."""
    artifact = _artifact(tmp_path)
    store.enqueue_pending(artifact)
    with pytest.raises(Exception):
        store.enqueue_pending(artifact)  # duplicate primary key

    assert store.enqueue_pending(_artifact(tmp_path, payload=b"another")) is not None


def test_extract_dicom_metadata_bounds_attacker_controlled_values():
    ds = _ct_dataset()
    ds.Modality = "M" * 100_000
    raw = _part10_bytes(ds)

    metadata = analyzers.extract_dicom_metadata(raw, "part10")

    assert len(metadata["modality"]) < 300
    assert metadata["modality"].endswith("...[truncated]")
    assert metadata["sop_class_uid"] == str(
        CTImageStorage
    )  # normal-length values untouched


# --- encapsulated document extraction ---


ENCAPSULATED_PDF_STORAGE = "1.2.840.10008.5.1.4.1.1.104.1"


def _encapsulated(document: bytes, mime: str, declare_length: bool = True) -> bytes:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = ENCAPSULATED_PDF_STORAGE
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = ENCAPSULATED_PDF_STORAGE
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID = "P1"
    ds.Modality = "DOC"
    ds.MIMETypeOfEncapsulatedDocument = mime
    if declare_length:
        ds.EncapsulatedDocumentLength = len(document)
    ds.EncapsulatedDocument = document + (b"\x00" if len(document) % 2 else b"")
    return _part10_bytes(ds)


def test_extract_encapsulated_document_returns_none_without_one():
    assert (
        analyzers.extract_encapsulated_document(
            _part10_bytes(_ct_dataset()), "part10", 1000
        )
        is None
    )


def test_extract_encapsulated_document_identifies_real_type_not_declared_type():
    raw = _encapsulated(b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n", "application/pdf")
    metadata, document = analyzers.extract_encapsulated_document(
        raw, "part10", 1_000_000
    )
    assert document.startswith(b"%PDF-")
    assert metadata["file_type"]["mime"] == "application/pdf"
    assert metadata["content_conflicts_with_declared_mime"] is False


def test_extract_encapsulated_document_flags_pdf_declaration_over_other_content():
    raw = _encapsulated(b"MZ\x90\x00" + b"\x00" * 40, "application/pdf")
    metadata, _document = analyzers.extract_encapsulated_document(
        raw, "part10", 1_000_000
    )
    assert metadata["content_conflicts_with_declared_mime"] is True


def test_extract_encapsulated_document_does_not_judge_non_pdf_declarations():
    """STL/CDA legitimately identify as something generic; only a PDF claim is unambiguous."""
    raw = _encapsulated(b"solid mesh\nendsolid mesh\n", "model/stl")
    metadata, _document = analyzers.extract_encapsulated_document(
        raw, "part10", 1_000_000
    )
    assert metadata["content_conflicts_with_declared_mime"] is None


def test_extract_encapsulated_document_strips_the_part10_pad_byte():
    body = b"%PDF-1.4 od"  # odd length, so Part 10 appends one pad byte
    raw = _encapsulated(body, "application/pdf", declare_length=False)
    metadata, document = analyzers.extract_encapsulated_document(
        raw, "part10", 1_000_000
    )
    assert document == body
    assert metadata["padding_bytes_removed"] == 1


def test_extract_encapsulated_document_is_bounded():
    raw = _encapsulated(b"%PDF-" + b"A" * 5000, "application/pdf")
    metadata, document = analyzers.extract_encapsulated_document(raw, "part10", 100)
    assert len(document) == 100
    assert metadata["truncated"] is True


def test_inner_document_scan_catches_what_the_wrapper_scan_cannot():
    """A filesize/offset-anchored rule can only match when the inner file is the scanned buffer."""
    bomb = bytearray(bytes(20))
    bomb[0:2] = bytes([0x1F, 0x8B])
    bomb[-4:] = (0x40000000).to_bytes(4, "little")
    raw = _encapsulated(bytes(bomb), "application/pdf")
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)

    outer, _state = yara_engine.scan(rules, raw)
    _metadata, document = analyzers.extract_encapsulated_document(
        raw, "part10", 1_000_000
    )
    inner, _state = yara_engine.scan(rules, document)

    assert outer == []
    assert any(m["rule"] == "Orthanc_GZIP_Large_ISIZE_CVE_2026_5438" for m in inner)


def test_matched_rule_names_merges_inner_document_hits_for_api_filtering():
    result = {
        "yara": {"matches": [{"rule": "Outer_Rule"}]},
        "encapsulated_document": {
            "yara": {"matches": [{"rule": "Inner_Rule"}, {"rule": "Outer_Rule"}]}
        },
    }
    assert worker._matched_rule_names(result) == ["Outer_Rule", "Inner_Rule"]


# --- Big Endian rule siblings (2026-08-02): fujifilm's storage classes accept Explicit VR BE too ---


def test_yara_detects_orthanc_cve_2026_5442_exact_dimensions_big_endian():
    rows_be = bytes(
        [0x00, 0x28, 0x00, 0x10, 0x55, 0x4C, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00]
    )
    columns_be = bytes(
        [0x00, 0x28, 0x00, 0x11, 0x55, 0x4C, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00]
    )
    uid = b"1.2.840.10008.1.2.2"  # Explicit VR Big Endian
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(rules, _bare_part10(uid + rows_be + columns_be))
    assert state is None
    assert {
        "DICOM_Orthanc_CVE_2026_5442_Known_Test_BigEndian",
        "DICOM_Rows_Columns_Encoded_As_UL_BigEndian",
    } <= {m["rule"] for m in matches}


def test_yara_detects_orthanc_cve_2026_5443_exact_dimensions_big_endian():
    palette = b"PALETTE COLOR"
    rows_3_be = bytes(
        [0x00, 0x28, 0x00, 0x10, 0x55, 0x4C, 0x00, 0x04, 0x00, 0x00, 0x00, 0x03]
    )
    columns_wrap_be = bytes(
        [0x00, 0x28, 0x00, 0x11, 0x55, 0x4C, 0x00, 0x04, 0x55, 0x55, 0x55, 0x56]
    )
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(
        rules, _bare_part10(palette + rows_3_be + columns_wrap_be)
    )
    assert state is None
    assert any(
        m["rule"] == "DICOM_Orthanc_CVE_2026_5443_Known_Test_BigEndian" for m in matches
    )


def test_yara_detects_pmsct_rle1_big_endian():
    codec_tag_be = bytes([0x07, 0xA1, 0x10, 0x11])
    codec_name = b"PMSCT_RLE1"
    compressed_tag_be = bytes([0x07, 0xA1, 0x10, 0x0A])
    bad_value = bytes(22) + bytes([0xA5, 0xFF])
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, state = yara_engine.scan(
        rules, _bare_part10(codec_tag_be + codec_name + compressed_tag_be + bad_value)
    )
    assert state is None
    assert any(
        m["rule"] == "DICOM_Orthanc_PMSCT_RLE1_CVE_2026_5441_Known_Test_BigEndian"
        for m in matches
    )


def test_yara_little_endian_only_rules_do_not_fire_on_big_endian_encoding():
    """Regression guard: LE-specific rules must not already cover BE, or this fix was unnecessary."""
    rows_be = bytes(
        [0x00, 0x28, 0x00, 0x10, 0x55, 0x4C, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00]
    )
    columns_be = bytes(
        [0x00, 0x28, 0x00, 0x11, 0x55, 0x4C, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00]
    )
    uid = b"1.2.840.10008.1.2.2"
    rules, _hash, _problems = yara_engine.compile_rules(worker.RULES_DIR)
    matches, _state = yara_engine.scan(rules, _bare_part10(uid + rows_be + columns_be))
    le_only = {
        "DICOM_Orthanc_CVE_2026_5442_Known_Test",
        "DICOM_Rows_Columns_Encoded_As_UL",
    }
    assert not (le_only & {m["rule"] for m in matches})


# --- negotiated transfer syntax threading (2026-08-02) ---


def test_extract_dicom_metadata_uses_negotiated_transfer_syntax_not_a_guess():
    """A raw DIMSE dataset given its actual negotiated transfer syntax must not go through dcmread's guessing heuristic."""
    from pydicom.uid import ExplicitVRBigEndian

    ds = _ct_dataset()
    del ds.file_meta
    ds.is_implicit_VR = ExplicitVRBigEndian.is_implicit_VR
    ds.is_little_endian = ExplicitVRBigEndian.is_little_endian
    raw = BytesIO()
    ds.save_as(raw, enforce_file_format=False)

    meta = analyzers.extract_dicom_metadata(
        raw.getvalue(), "dimse-dataset", str(ExplicitVRBigEndian)
    )

    assert meta is not None
    assert meta["sop_class_uid"] == str(CTImageStorage)
    assert meta["modality"] == "CT"
    assert str(ExplicitVRBigEndian) in meta["parse_assumption"]
    assert "guessed" not in meta["parse_assumption"]


def test_extract_dicom_metadata_without_negotiated_transfer_syntax_falls_back_to_guessing():
    ds = _ct_dataset()
    del ds.file_meta
    ds.is_implicit_VR = False
    ds.is_little_endian = True
    raw = BytesIO()
    ds.save_as(raw, enforce_file_format=False)

    meta = analyzers.extract_dicom_metadata(raw.getvalue(), "dimse-dataset", None)

    assert meta is not None
    assert "guessed" in meta["parse_assumption"]


# --- RLIMIT_CPU backstop (2026-08-02): must not be a small per-job-conflated value ---


def test_worker_cpu_backstop_is_a_generous_lifetime_value_not_a_per_job_timeout():
    """RLIMIT_CPU is cumulative for the process's whole life, so a small value kills a healthy worker eventually."""
    assert worker._WORKER_CPU_BACKSTOP_SECONDS >= 3600
    assert worker._WORKER_CPU_BACKSTOP_SECONDS != int(AnalysisConfig().TIMEOUT)


def test_set_resource_limits_applies_the_cpu_backstop(tmp_path):
    import multiprocessing

    def child(result_path):
        import resource

        worker._set_resource_limits()
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        Path(result_path).write_text(f"{soft},{hard}")

    result_path = tmp_path / "rlimit.txt"
    p = multiprocessing.Process(target=child, args=(str(result_path),))
    p.start()
    p.join(timeout=10)

    soft, hard = (int(x) for x in result_path.read_text().split(","))
    assert soft == worker._WORKER_CPU_BACKSTOP_SECONDS
    assert hard == worker._WORKER_CPU_BACKSTOP_SECONDS


def _dead_component(tmp_path):
    from analysis.component import new_analysis_component
    from analysis.config import new_analysis_config
    import logging

    comp = new_analysis_component(
        new_analysis_config(db_path=str(tmp_path / "missing" / "\0bad" / "a.db")),
        logging.getLogger("bus"),
    )
    comp.start()
    return comp


def test_unopenable_store_disables_analysis_without_killing_the_process(tmp_path):
    comp = _dead_component(
        tmp_path
    )  # must not raise: an optional feature can't take the honeypot down
    assert comp.store.ready() is False
    assert comp._process is None and comp._supervisor is None
    assert comp.store.list_artifacts() == ([], 0)
    comp.stop()


def test_sink_is_safe_when_the_analysis_store_never_opened(tmp_path):
    comp = _dead_component(tmp_path)
    storage = new_store(str(tmp_path / "traces"))
    capture = storage.capture(b"x" * 32, suffix=".dcm")

    comp.sink(
        SubmittedArtifact(
            capture,
            channel="DIMSE",
            request_type="C-STORE",
            disposition="stored",
            source_encoding="dimse-dataset",
            session_id="1",
            ip="1.2.3.4",
            local_port=104,
        )
    )
    comp.stop()


def test_operator_api_artifacts_survive_a_store_that_never_opened(tmp_path):
    import logging

    from profiles.profile import load_profile
    from web.operator_api import new_operator_api

    comp = _dead_component(tmp_path)
    client = new_operator_api(
        load_profile("generic-pacs"), logging.getLogger("bus"), None, comp.store
    ).test_client()

    response = client.get("/api/artifacts")
    assert response.status_code == 200
    assert response.get_json() == []
    comp.stop()
