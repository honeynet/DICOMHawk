import base64
import gzip
import io
import json
import logging

import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.tag import Tag
from pydicom.uid import (
    CTImageStorage,
    DeflatedExplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    generate_uid,
)

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.dicomweb import new_dicomweb

# fujifilm real Synapse ports
QIDO, WADO_RS, STOW, WADO_URI = 10080, 12080, 13080, 9080
GENERIC_PORT = 8042
_CREDS = {"Authorization": "Basic " + base64.b64encode(b"admin:hunter2").decode()}


def _ct_dataset(
    study_uid=None,
    series_uid=None,
    sop_uid=None,
    name="Test^Pat",
    patient_id="P1",
    pixels=False,
    transfer_syntax=ExplicitVRLittleEndian,
):
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid or generate_uid()
    ds.file_meta.TransferSyntaxUID = transfer_syntax
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid or generate_uid()
    ds.SeriesInstanceUID = series_uid or generate_uid()
    ds.PatientID = patient_id
    ds.PatientName = name
    ds.Modality = "CT"
    ds.StudyDate = "20260101"
    if pixels:
        ds.Rows = ds.Columns = 2
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelData = b"\x00\x01\x02\x03"
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    buf.seek(0)
    return dcmread(buf)


def _part10(ds):
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def _stow_body(ds, boundary="BND", part_type="application/dicom"):
    part = _part10(ds)
    return (
        f"--{boundary}\r\nContent-Type: {part_type}\r\n\r\n".encode()
        + part
        + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )


_STOW_CT = f'multipart/related; type="application/dicom"; boundary=BND'


@pytest.fixture
def bus():
    logger = logging.getLogger("bus")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def repo(tmp_path):
    return new_repo(None, new_store(str(tmp_path / "traces"))).start()


@pytest.fixture
def apps(repo, bus):
    return new_dicomweb(load_profile("fujifilm"), repo, bus)


def _client(apps, port):
    apps[port].config["TESTING"] = True
    return apps[port].test_client()


# --- port binding / structure ---


def test_fujifilm_binds_the_four_real_synapse_ports(apps):
    assert set(apps) == {QIDO, WADO_RS, STOW, WADO_URI}


# --- QIDO-RS ---


def test_qido_studies_returns_dicom_json(repo, apps):
    ds = _ct_dataset(name="Seed^One")
    repo.store(ds, safe=True)
    resp = _client(apps, QIDO).get("/qido-rs/studies")
    assert resp.status_code == 200
    assert resp.content_type == "application/json"
    body = json.loads(resp.get_data())
    assert body[0]["0020000D"]["Value"] == [ds.StudyInstanceUID]
    # QueryRetrieveLevel is a query key, never a QIDO result attribute.
    assert "00080052" not in body[0]


def test_qido_series_scoped_to_study(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    resp = _client(apps, QIDO).get(f"/qido-rs/studies/{ds.StudyInstanceUID}/series")
    assert resp.status_code == 200
    body = json.loads(resp.get_data())
    assert body[0]["0020000E"]["Value"] == [ds.SeriesInstanceUID]


def test_qido_instances_lists_sop_instances(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    resp = _client(apps, QIDO).get(
        f"/qido-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}/instances"
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data())
    assert body[0]["00080018"]["Value"] == [ds.SOPInstanceUID]  # SOPInstanceUID


def test_qido_no_match_returns_204(repo, apps):
    repo.store(_ct_dataset(), safe=True)
    resp = _client(apps, QIDO).get("/qido-rs/studies?PatientID=NOSUCH")
    assert resp.status_code == 204


def test_qido_honors_json_and_native_xml_accept(repo, apps):
    repo.store(_ct_dataset(), safe=True)
    client = _client(apps, QIDO)
    dicom_json = client.get(
        "/qido-rs/studies", headers={"Accept": "application/dicom+json"}
    )
    assert dicom_json.content_type == "application/dicom+json"
    xml = client.get(
        "/qido-rs/studies",
        headers={"Accept": "multipart/related; type=application/dicom+xml"},
    )
    assert xml.status_code == 200
    assert xml.content_type.startswith(
        'multipart/related; type="application/dicom+xml"'
    )
    assert b"NativeDicomModel" in xml.get_data()
    assert (
        client.get(
            "/qido-rs/studies", headers={"Accept": "application/json;q=0"}
        ).status_code
        == 406
    )


def test_qido_numeric_tag_and_header_fallback(repo, apps):
    ds = _ct_dataset()
    ds.BodyPartExamined = "CHEST"
    repo.store(ds, safe=True)
    client = _client(apps, QIDO)
    assert client.get("/qido-rs/studies?00100020=NO-SUCH").status_code == 204
    resp = client.get("/qido-rs/studies?00180015=CHEST&includefield=00180015")
    assert resp.status_code == 200
    assert json.loads(resp.get_data())[0]["00180015"]["Value"] == ["CHEST"]


@pytest.mark.parametrize(
    "query",
    ["SeriesNumber=not-an-int", "offset=-1", "limit=bad", "UnknownThing=x"],
)
def test_qido_malformed_parameters_are_controlled_400(apps, query):
    resp = _client(apps, QIDO).get(f"/qido-rs/studies?{query}")
    assert resp.status_code == 400
    assert resp.mimetype == "text/plain"
    assert b"Werkzeug" not in resp.get_data()


def test_qido_fuzzy_matching_is_ignored_with_warning(repo, apps):
    repo.store(_ct_dataset(), safe=True)
    resp = _client(apps, QIDO).get("/qido-rs/studies?fuzzymatching=true")
    assert resp.status_code == 200
    assert resp.headers["Warning"].startswith("299 Synapse")


def test_qido_root_series_and_instances_routes(repo, apps):
    repo.store(_ct_dataset(), safe=True)
    client = _client(apps, QIDO)
    assert client.get("/qido-rs/series").status_code == 200
    assert client.get("/qido-rs/instances").status_code == 200


def test_qido_cap_applies_only_to_multiple_patients(repo, apps):
    repo.store(_ct_dataset(patient_id="P1"), safe=True)
    repo.store(_ct_dataset(patient_id="P1"), safe=True)
    apps[QIDO].config["QIDO_MAX"] = 1
    client = _client(apps, QIDO)
    assert len(json.loads(client.get("/qido-rs/studies").get_data())) == 2
    repo.store(_ct_dataset(patient_id="P2"), safe=True)
    assert len(json.loads(client.get("/qido-rs/studies").get_data())) == 1


# --- WADO-RS ---


def test_wado_rs_retrieves_seeded_instance_as_multipart(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    resp = _client(apps, WADO_RS).get(
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}/instances/{ds.SOPInstanceUID}"
    )
    assert resp.status_code == 200
    assert resp.content_type.startswith('multipart/related; type="application/dicom"')
    assert b"application/dicom" in resp.get_data()


def test_wado_rs_metadata_excludes_pixel_data(repo, apps):
    ds = _ct_dataset(pixels=True)
    repo.store(ds, safe=True)
    resp = _client(apps, WADO_RS).get(
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}/instances/{ds.SOPInstanceUID}/metadata"
    )
    assert resp.status_code == 200
    assert resp.content_type == "application/dicom+json"
    assert "7FE00010" not in resp.get_data(
        as_text=True
    )  # PixelData never leaks into metadata


def test_wado_rs_unknown_instance_404(repo, apps):
    resp = _client(apps, WADO_RS).get("/wado-rs/studies/1/series/2/instances/3")
    assert resp.status_code == 404


def test_wado_rs_honors_single_dicom_and_explicit_vr_default(repo, apps):
    ds = _ct_dataset(transfer_syntax=ImplicitVRLittleEndian)
    repo.store(ds, safe=True)
    path = (
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}"
        f"/instances/{ds.SOPInstanceUID}"
    )
    resp = _client(apps, WADO_RS).get(path, headers={"Accept": "application/dicom"})
    assert resp.status_code == 200
    returned = dcmread(io.BytesIO(resp.get_data()))
    assert returned.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian


def test_wado_rs_rejects_unacceptable_representation(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    path = (
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}"
        f"/instances/{ds.SOPInstanceUID}"
    )
    assert (
        _client(apps, WADO_RS).get(path, headers={"Accept": "text/plain"}).status_code
        == 406
    )


def test_wado_metadata_native_xml_and_repeating_overlay_removal(repo, apps):
    ds = _ct_dataset(pixels=True)
    ds.add_new(Tag(0x6002, 0x3000), "OW", b"overlay-pixels")
    repo.store(ds, safe=True)
    path = (
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}"
        f"/instances/{ds.SOPInstanceUID}/metadata"
    )
    client = _client(apps, WADO_RS)
    json_resp = client.get(path)
    assert "60023000" not in json_resp.get_data(as_text=True)
    xml_resp = client.get(
        path, headers={"Accept": "multipart/related; type=application/dicom+xml"}
    )
    assert xml_resp.status_code == 200
    assert b"NativeDicomModel" in xml_resp.get_data()
    assert b"60023000" not in xml_resp.get_data()


def test_wado_study_and_series_retrieval_routes(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    client = _client(apps, WADO_RS)
    assert client.get(f"/wado-rs/studies/{ds.StudyInstanceUID}").status_code == 200
    assert (
        client.get(
            f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}"
        ).status_code
        == 200
    )
    assert (
        client.get(f"/wado-rs/studies/{ds.StudyInstanceUID}/metadata").status_code
        == 200
    )


# --- STOW-RS + auth + storage jail ---


def test_stow_requires_auth_challenge(apps):
    resp = _client(apps, STOW).post(
        "/stow-rs/studies", data=b"x", content_type=_STOW_CT
    )
    assert resp.status_code == 401
    challenges = resp.headers.getlist("WWW-Authenticate")
    assert challenges[:2] == ["Negotiate", "NTLM"]
    assert challenges[2].startswith("Basic ")


def test_stow_captures_credentials(apps, caplog):
    with caplog.at_level(logging.WARNING, logger="bus"):
        _client(apps, STOW).post(
            "/stow-rs/studies",
            data=_stow_body(_ct_dataset()),
            content_type=_STOW_CT,
            headers=_CREDS,
        )
    assert any("hunter2" in r.getMessage() for r in caplog.records)


def test_stow_rejects_non_multipart(apps):
    resp = _client(apps, STOW).post(
        "/stow-rs/studies", data=b"{}", content_type="application/json", headers=_CREDS
    )
    assert resp.status_code == 415


def test_stow_rejects_wrong_multipart_type_and_empty_body(apps):
    client = _client(apps, STOW)
    wrong = client.post(
        "/stow-rs/studies",
        data=b"--BND--\r\n",
        content_type="multipart/related; type=application/dicom+xml; boundary=BND",
        headers=_CREDS,
    )
    assert wrong.status_code == 415
    empty = client.post(
        "/stow-rs/studies",
        data=b"--BND--\r\n",
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert empty.status_code == 400


def test_stow_rejects_wrong_part_type_and_unsupported_sop(apps):
    client = _client(apps, STOW)
    wrong_part = client.post(
        "/stow-rs/studies",
        data=_stow_body(_ct_dataset(), part_type="application/dicom+xml"),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert wrong_part.status_code == 202
    assert "00081198" in json.loads(wrong_part.get_data())

    ds = _ct_dataset()
    ds.SOPClassUID = "1.2.3.4.5.6.7.8.9"
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    unsupported = client.post(
        "/stow-rs/studies",
        data=_stow_body(ds),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert unsupported.status_code == 202
    assert "00081198" in json.loads(unsupported.get_data())

    unsupported_ts = _ct_dataset(transfer_syntax=DeflatedExplicitVRLittleEndian)
    transfer_syntax = client.post(
        "/stow-rs/studies",
        data=_stow_body(unsupported_ts),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert transfer_syntax.status_code == 202
    assert "00081198" in json.loads(transfer_syntax.get_data())


def test_stow_study_uri_must_match_dataset(apps):
    resp = _client(apps, STOW).post(
        "/stow-rs/studies/1.2.3",
        data=_stow_body(_ct_dataset()),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert resp.status_code == 202
    assert "00081198" in json.loads(resp.get_data())


def test_stow_preserves_exact_raw_part(repo, apps):
    ds = _ct_dataset()
    raw = _part10(ds)
    resp = _client(apps, STOW).post(
        "/stow-rs/studies",
        data=_stow_body(ds),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert resp.status_code == 200
    traces = list(repo.storage.traces_dir.glob("*.dcm.gz"))
    assert len(traces) == 1
    assert gzip.decompress(traces[0].read_bytes()) == raw


def test_stow_mime_parser_does_not_split_boundary_bytes_inside_dicom(apps):
    ds = _ct_dataset(pixels=True)
    ds.PixelData = b"inside--BNDbytes"
    resp = _client(apps, STOW).post(
        "/stow-rs/studies",
        data=_stow_body(ds),
        content_type=_STOW_CT,
        headers=_CREDS,
    )
    assert resp.status_code == 200


def test_stow_limits_part_count(apps):
    apps[STOW].config["MAX_STOW_PARTS"] = 1
    first, second = _part10(_ct_dataset()), _part10(_ct_dataset())
    body = (
        b"--BND\r\nContent-Type: application/dicom\r\n\r\n"
        + first
        + b"\r\n--BND\r\nContent-Type: application/dicom\r\n\r\n"
        + second
        + b"\r\n--BND--\r\n"
    )
    resp = _client(apps, STOW).post(
        "/stow-rs/studies", data=body, content_type=_STOW_CT, headers=_CREDS
    )
    assert resp.status_code == 400


def test_stow_stores_and_returns_referenced_sequence(apps):
    ds = _ct_dataset(name="Attacker^Up")
    resp = _client(apps, STOW).post(
        "/stow-rs/studies", data=_stow_body(ds), content_type=_STOW_CT, headers=_CREDS
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data())
    assert body["00081199"]["Value"][0]["00081155"]["Value"] == [ds.SOPInstanceUID]


def test_storage_jail_stow_upload_not_retrievable_via_wado(repo, apps):
    """The storage jail must hold over DICOMweb: a STOW'd instance is never served back by WADO-RS."""
    ds = _ct_dataset(name="Attacker^Exfil")
    stow_resp = _client(apps, STOW).post(
        "/stow-rs/studies", data=_stow_body(ds), content_type=_STOW_CT, headers=_CREDS
    )
    assert stow_resp.status_code == 200  # accepted into quarantine
    wado_resp = _client(apps, WADO_RS).get(
        f"/wado-rs/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}/instances/{ds.SOPInstanceUID}"
    )
    assert wado_resp.status_code == 404  # quarantined bytes refused


# --- WADO-URI ---


def test_wado_uri_requires_params(apps):
    resp = _client(apps, WADO_URI).get(
        "/services/wado?requestType=WADO", headers=_CREDS
    )
    assert resp.status_code == 406


def test_wado_uri_serves_application_dicom(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    resp = _client(apps, WADO_URI).get(
        f"/services/wado?requestType=WADO&studyUID={ds.StudyInstanceUID}"
        f"&seriesUID={ds.SeriesInstanceUID}&objectUID={ds.SOPInstanceUID}"
        "&contentType=application/dicom",
        headers=_CREDS,
    )
    assert resp.status_code == 200
    assert resp.mimetype == "application/dicom"
    assert resp.mimetype_params["transfer-syntax"] == str(ExplicitVRLittleEndian)


def test_wado_uri_defaults_to_real_jpeg_for_image(repo, apps):
    ds = _ct_dataset(pixels=True)
    repo.store(ds, safe=True)
    path = (
        f"/services/wado/?requestType=WADO&studyUID={ds.StudyInstanceUID}"
        f"&seriesUID={ds.SeriesInstanceUID}&objectUID={ds.SOPInstanceUID}"
    )
    resp = _client(apps, WADO_URI).get(path, headers=_CREDS)
    assert resp.status_code == 200
    assert resp.content_type == "image/jpeg"
    assert resp.get_data().startswith(b"\xff\xd8")


def test_wado_uri_observed_case_alias_and_text_representation(repo, apps):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    path = (
        f"/services/Wado?requestType=WADO&studyUID={ds.StudyInstanceUID}"
        f"&seriesUID={ds.SeriesInstanceUID}&objectUID={ds.SOPInstanceUID}"
        "&contentType=text/plain"
    )
    resp = _client(apps, WADO_URI).get(path, headers=_CREDS)
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert ds.SOPInstanceUID in resp.get_data(as_text=True)


def test_get_with_body_is_rejected_before_handler(apps):
    resp = _client(apps, QIDO).get("/qido-rs/studies", data=b"unexpected")
    assert resp.status_code == 413


# --- identity headers ---


def test_reuses_web_identity_headers_not_csp(repo, apps):
    repo.store(_ct_dataset(), safe=True)
    resp = _client(apps, QIDO).get("/qido-rs/studies")
    assert resp.headers.get("Server") == "Microsoft-IIS/10.0"
    assert (
        "Content-Security-Policy" not in resp.headers
    )  # HTML-only header, not for a JSON API


def test_dicomweb_log_records_the_profile_listener_port(repo, apps, caplog):
    repo.store(_ct_dataset(), safe=True)
    with caplog.at_level(logging.INFO, logger="bus"):
        _client(apps, QIDO).get("/qido-rs/studies")
    events = [json.loads(record.getMessage()) for record in caplog.records]
    assert any(event.get("local_port") == QIDO for event in events)


# --- cross-profile / cross-port isolation ---


def test_generic_pacs_uses_single_dicomweb_port_not_synapse_ports(repo, bus):
    apps = new_dicomweb(load_profile("generic-pacs"), repo, bus)
    assert set(apps) == {GENERIC_PORT}
    assert QIDO not in apps and WADO_RS not in apps


def test_generic_pacs_serves_dicom_web_base(repo, bus):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    apps = new_dicomweb(load_profile("generic-pacs"), repo, bus)
    apps[GENERIC_PORT].config["TESTING"] = True
    client = apps[GENERIC_PORT].test_client()
    response = client.get("/dicom-web/studies")
    assert response.status_code == 200
    assert response.content_type == "application/dicom+json"
    # a Synapse-shaped path must not exist on the generic profile
    assert client.get("/qido-rs/studies").status_code == 404


def test_generic_pacs_does_not_leak_synapse_server_header(repo, bus):
    ds = _ct_dataset()
    repo.store(ds, safe=True)
    apps = new_dicomweb(load_profile("generic-pacs"), repo, bus)
    apps[GENERIC_PORT].config["TESTING"] = True
    resp = apps[GENERIC_PORT].test_client().get("/dicom-web/studies")
    assert resp.headers.get("Server") == "Apache"
    assert resp.headers.get("Server") != "Microsoft-IIS/10.0"


def test_generic_pacs_warning_header_is_not_synapse_shaped(repo, bus):
    repo.store(_ct_dataset(), safe=True)
    apps = new_dicomweb(load_profile("generic-pacs"), repo, bus)
    apps[GENERIC_PORT].config["TESTING"] = True
    resp = apps[GENERIC_PORT].test_client().get("/dicom-web/studies?fuzzymatching=true")
    assert "Synapse" not in resp.headers.get("Warning", "")
