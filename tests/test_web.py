import base64
import gzip
import io
import json
import logging
import shutil
from pathlib import Path

import pytest

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.app import new_web


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
    return new_repo(None, new_store(str(tmp_path / "traces")))


@pytest.fixture
def client(repo, bus):
    profile = load_profile("fujifilm")
    app = new_web(profile, repo, bus)
    app.config["TESTING"] = True
    return app.test_client()


def _login_generic(client):
    response = client.post(
        "/portal/login?signin=test", data={"username": "test", "password": "test"}
    )
    assert response.status_code == 302
    return response


def test_synapse_entry_redirects_to_login(client):
    resp = client.get("/Synapse")
    assert resp.status_code == 302
    assert "/SynapseSignOn/sts/login" in resp.headers["Location"]


def test_fingerprint_seam_empty_by_default(client):
    resp = client.get("/SynapseSignOn/sts/login?signin=abc")
    assert "probe.js" not in resp.get_data(as_text=True)


def test_fingerprint_seam_injects_configured_script(repo, bus):
    profile = load_profile("fujifilm")
    profile.web.fingerprint_script = "synapse/probe.js"
    client = new_web(profile, repo, bus).test_client()

    resp = client.get("/SynapseSignOn/sts/login?signin=abc")
    body = resp.get_data(as_text=True)
    assert '<script nonce="' in body and "synapse/probe.js" in body


def test_honey_credential_grants_unconditionally_and_logs_distinctly(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    assert (
        profile.web.grant_access is False
    )  # the point: bait works even though real logins don't
    client = new_web(profile, repo, bus).test_client()

    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.post(
            "/portal/login?signin=x", data={"username": "test", "password": "test"}
        )
    assert resp.status_code == 302
    assert (
        resp.headers["Location"] == "/portal/console"
    )  # browse on -> console is the landing
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text


def test_honey_hint_visible_on_generic_pacs_not_fujifilm(repo, bus):
    generic_body = (
        new_web(load_profile("generic-pacs"), repo, bus)
        .test_client()
        .get("/portal/login?signin=x")
        .get_data(as_text=True)
    )
    assert "test / test" in generic_body

    fuji_body = (
        new_web(load_profile("fujifilm"), repo, bus)
        .test_client()
        .get("/SynapseSignOn/sts/login?signin=x")
        .get_data(as_text=True)
    )
    assert "test / test" not in fuji_body


def test_random_guess_still_denied_when_grant_access_false(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    resp = client.post(
        "/portal/login?signin=x", data={"username": "attacker", "password": "guess"}
    )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data


def test_generic_pacs_routes_and_cookies_are_not_synapse_shaped(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()

    resp = client.get("/portal")
    assert resp.status_code == 302
    assert "SynapseSignOn" not in resp.headers["Location"]

    login_resp = client.get("/portal/login?signin=x")
    body = login_resp.get_data(as_text=True)
    assert "Synapse" not in body
    assert "SynapseSignOn" not in body
    cookie_names = [c.split("=")[0] for c in login_resp.headers.getlist("Set-Cookie")]
    assert not any(
        "idsrv" in c or "SignInMessage" in c or "OpenIdConnect" in c
        for c in cookie_names
    )

    winauth_resp = client.get("/portal/winauth")
    assert winauth_resp.status_code == 401
    assert "SynapseSignOn" not in winauth_resp.get_data(as_text=True)

    # Old Synapse-specific paths simply don't exist on this profile.
    assert client.get("/Synapse").status_code == 404
    assert client.get("/SynapseSignOn/sts/login").status_code == 404


def test_error_and_forgot_password_pages_use_overridden_routes(tmp_path, repo, bus):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
        "  routes:\n"
        "    login: /custom/login\n"
        "    forgot_password: /custom/forgot\n"
    )
    profile = load_profile(str(custom))
    client = new_web(profile, repo, bus).test_client()

    error_body = client.get("/portal/error").get_data(
        as_text=True
    )  # sts_error kept its default route
    assert 'href="/custom/login"' in error_body
    assert "/SynapseSignOn/sts/login" not in error_body

    forgot_body = client.get("/custom/forgot").get_data(as_text=True)
    assert 'action="/custom/forgot"' in forgot_body
    assert "/ssomgr/password/forgotpassword" not in forgot_body


def test_login_get_spoofs_iis_headers(client):
    resp = client.get("/SynapseSignOn/sts/login?signin=abc")
    assert resp.status_code == 200
    assert resp.headers["Server"] == "Microsoft-IIS/10.0"
    assert resp.headers["X-Powered-By"] == "ASP.NET"
    assert "nonce-" in resp.headers["Content-Security-Policy"]
    assert (
        resp.headers["X-Content-Security-Policy"]
        == resp.headers["Content-Security-Policy"]
    )


def test_generic_profile_does_not_leak_synapse_legacy_csp(repo, bus):
    resp = (
        new_web(load_profile("generic-pacs"), repo, bus)
        .test_client()
        .get("/portal/login?signin=abc")
    )
    assert "X-Content-Security-Policy" not in resp.headers


def test_signin_token_is_script_safe_and_cookie_matches_flow(client):
    resp = client.get("/SynapseSignOn/sts/login?signin=flow-123")
    assert "SignInMessage.flow-123=" in "\n".join(resp.headers.getlist("Set-Cookie"))

    hostile = client.get(
        "/SynapseSignOn/sts/login?signin=%3C/script%3E%3Cscript%3Eboom%3C/script%3E"
    )
    assert b"</script><script>boom</script>" not in hostile.data
    assert b"boom" not in hostile.data


def test_oversized_login_is_rejected_and_logged(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    profile.web.max_request_bytes = 32
    client = new_web(profile, repo, bus).test_client()
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.post("/portal/login", data={"username": "x" * 100})
    assert resp.status_code == 413
    assert b"Werkzeug" not in resp.data
    assert "WEB_REQUEST_TOO_LARGE" in caplog.text


def test_http_login_javascript_does_not_assume_missing_rsa_library(client):
    script = client.get("/static/synapse/login.js").get_data(as_text=True)
    assert 'typeof JSEncrypt !== "undefined"' in script


def test_public_base_url_keeps_oidc_redirect_on_external_https(repo, bus):
    profile = load_profile("fujifilm")
    profile.web.public_base_url = "https://pacs.example.org"
    body = (
        new_web(profile, repo, bus)
        .test_client()
        .get("/SynapseSignOn/sts/login?signin=abc")
        .get_data(as_text=True)
    )
    assert "https%3a%2f%2fpacs.example.org%2fWorkflowUI%2f" in body
    assert "http%3a%2f%2flocalhost%2fWorkflowUI%2f" not in body


def test_login_post_denies_and_captures(client, caplog):
    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.post(
            "/SynapseSignOn/sts/login?signin=abc",
            data={"username": "attacker", "password": "hunter2"},
        )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data
    assert "attacker" in caplog.text


def test_winauth_challenges_without_credentials(client):
    resp = client.get("/SynapseSignOn/WinAuth/Login.aspx")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"].startswith("Basic")


def test_winauth_honey_credential_grants_and_logs_distinctly(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    client = new_web(profile, repo, bus).test_client()
    creds = base64.b64encode(b"test:test").decode()

    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.get(
            "/portal/winauth", headers={"Authorization": f"Basic {creds}"}
        )

    assert resp.status_code == 302
    assert (
        resp.headers["Location"] == "/portal/console"
    )  # browse on -> console is the landing
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text


def test_translated_items_uses_pascal_case_keys_the_client_js_expects(client):
    resp = client.post("/synapse/error/TranslatedItems/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["Text1"] == "Synapse Log On"
    assert data["Text2"] == "Unable to log in using Windows Authentication."
    assert data["Text3"] == "Log in directly"
    assert "text1" not in data


def test_forgot_password_is_anti_enumeration(client):
    # Same generic success message regardless of whether "nobody" is a real account.
    resp = client.post("/ssomgr/password/forgotpassword", data={"username": "nobody"})
    assert resp.status_code == 200
    assert b"password reset email has been sent" in resp.data


def test_robots_txt_lists_honeytrap_paths(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Disallow: /Swat/" in resp.get_data(as_text=True)
    assert "Disallow: /api/WorkflowEngine/" in resp.get_data(as_text=True)


def test_swat_probe_bounces_toward_signon_and_logs(client, caplog):
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/Swat/anything/here")
    assert resp.status_code == 302
    assert (
        resp.headers["Location"] == "/Synapse"
    )  # engine's own entry point, not a hardcoded URL
    assert "WEB_HONEYTRAP_LOGIN_REDIRECT" in caplog.text

    resp = client.get(
        "/Swat/api/sso/signoff"
    )  # nested paths fold into the same catch-all
    assert resp.status_code == 302


def test_workflow_engine_probe_returns_stock_style_json(client, caplog):
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.post("/api/WorkflowEngine/Help/Api/Study/UnreserveStudy")
    assert resp.status_code == 404
    assert "No HTTP resource was found" in resp.get_json()["Message"]
    assert "WEB_HONEYTRAP_API_404" in caplog.text


def test_profile_without_honeytraps_gets_none(tmp_path, repo, bus):
    sparse = tmp_path / "sparse.yaml"
    sparse.write_text(
        "meta:\n  name: sparse\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
    )
    profile = load_profile(str(sparse))
    assert profile.web.honeytraps == []

    app = new_web(profile, repo, bus)
    client = app.test_client()

    resp = client.get("/Swat/")
    assert resp.status_code == 404  # not a Fujifilm-branded redirect

    resp = client.get("/robots.txt")
    assert resp.get_data(as_text=True).strip() == "User-agent: *"


def test_new_profile_can_declare_its_own_honeytrap(tmp_path, repo, bus):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
        "  honeytraps:\n"
        "    - path: /osirixAdmin/\n"
        "      response: login_redirect\n"
    )
    profile = load_profile(str(custom))
    assert profile.web.honeytraps == [("/osirixAdmin/", "login_redirect")]

    app = new_web(profile, repo, bus)
    client = app.test_client()

    resp = client.get("/osirixAdmin/anything")
    assert resp.status_code == 302
    assert (
        resp.headers["Location"] == "/portal"
    )  # this custom profile didn't override web.routes either


def test_unmapped_path_gets_spoofed_headers_not_werkzeug_default(client, caplog):
    # Unmatched routes bypass blueprint-scoped hooks.
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/totally/made/up/path")
    assert resp.status_code == 404
    assert resp.headers["Server"] == "Microsoft-IIS/10.0"
    assert b"Werkzeug" not in resp.data
    assert b"traceback" not in resp.data.lower()
    assert "WEB_404" in caplog.text


def test_favicon_served(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.mimetype == "image/x-icon"


def test_sparse_profile_serves_without_crashing(tmp_path, repo, bus):
    sparse = tmp_path / "sparse.yaml"
    sparse.write_text(
        "meta:\n  name: sparse\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
    )
    profile = load_profile(str(sparse))
    app = new_web(profile, repo, bus)
    client = app.test_client()

    assert (
        client.get("/portal").status_code == 302
    )  # no routes override -> generic default, not /Synapse
    assert client.get("/portal/login?signin=x").status_code == 200
    assert (
        client.get("/favicon.ico").status_code == 404
    )  # no favicon configured -> 404, not a crash
    assert (
        client.post(
            "/portal/login?signin=x", data={"username": "a", "password": "b"}
        ).status_code
        == 200
    )


def test_worklist_reads_seeded_studies(repo, bus, caplog):
    from seeding.seeder import new_seeder

    seeder = new_seeder(repo)
    loc = seeder._locations[0]
    assert seeder._seed_fallback(loc, "CT", "test-epoch") > 0

    profile = load_profile("fujifilm")
    profile.web.grant_access = True
    app = new_web(profile, repo, bus)
    client = app.test_client()

    login = client.post(
        "/SynapseSignOn/sts/login?signin=abc",
        data={"username": "a", "password": "b"},
    )
    assert login.status_code == 302
    login_cookies = "\n".join(login.headers.getlist("Set-Cookie"))
    assert "IdpCookie=" in login_cookies
    assert "Secure" in login_cookies
    assert "sw_authed" not in login_cookies

    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/WorkflowUI/")
    assert resp.status_code == 200
    assert b"worklist-table" in resp.data
    assert b"No studies." not in resp.data
    assert "WEB_WORKLIST_VIEW" in caplog.text

    deep = client.get("/WorkflowUI/PowerJacket/?PJType=POWERJACKET")
    assert deep.status_code == 200


def test_generic_pacs_profile_serves_all_pages(repo, bus):
    profile = load_profile("generic-pacs")
    client = new_web(profile, repo, bus).test_client()

    assert client.get("/portal").status_code == 302
    assert client.get("/portal/login?signin=x").status_code == 200
    assert client.get("/portal/forgot-password").status_code == 200
    assert client.get("/portal/winauth").status_code == 401
    assert client.get("/portal/error").status_code == 200

    resp = client.post(
        "/portal/login?signin=x", data={"username": "a", "password": "b"}
    )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data
    assert resp.headers["Server"] == "Apache"  # generic fallback, not Fujifilm's IIS


def test_generic_pacs_unauthorized_honeytrap(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    client = new_web(profile, repo, bus).test_client()

    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/admin/")
    assert resp.status_code == 401
    assert b"401 - Unauthorized" in resp.data
    assert "WEB_HONEYTRAP_UNAUTHORIZED_PAGE" in caplog.text

    assert "Disallow: /admin/" in client.get("/robots.txt").get_data(as_text=True)


def test_fujifilm_and_generic_pacs_dont_leak_into_each_other(repo, bus):
    fuji_client = new_web(load_profile("fujifilm"), repo, bus).test_client()
    generic_client = new_web(load_profile("generic-pacs"), repo, bus).test_client()

    assert (
        fuji_client.get("/SynapseSignOn/sts/login?signin=x").headers["Server"]
        == "Microsoft-IIS/10.0"
    )
    assert (
        generic_client.get("/SynapseSignOn/sts/login?signin=x").headers["Server"]
        == "Apache"
    )


# --- Browse console (generic-pacs; fujifilm must never expose it) ---


def _seed_one(repo):
    from seeding.seeder import new_seeder

    seeder = new_seeder(repo)
    assert seeder._seed_fallback(seeder._locations[0], "CT", "epoch") > 0


def _upload_dicom():
    import io

    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID = "EVIL"
    ds.PatientName = "A^B"
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    buf.seek(0)
    return ds, buf


def test_browse_console_requires_session(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    resp = client.get("/portal/console")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/portal"


def test_browse_console_rejects_forged_session_cookie(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    client.set_cookie("portal_authed", "forged")
    assert client.get("/portal/console").status_code == 302


def test_browse_landing_after_honey_login(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    login = client.post("/portal/login", data={"username": "test", "password": "test"})
    assert login.status_code == 302
    assert login.headers["Location"] == "/portal/console"


def test_browse_levels_show_seeded_data(repo, bus):
    _seed_one(repo)
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    for page in ("patients", "studies", "series", "instances"):
        resp = client.get(f"/portal/{page}")
        assert resp.status_code == 200
        assert b"No records." not in resp.data


def test_browse_search_logs_query(repo, bus, caplog):
    _seed_one(repo)
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/portal/search?searchType=name&q=ZZZ")
    assert resp.status_code == 200
    assert "WEB_SEARCH" in caplog.text and "Query: ZZZ" in caplog.text


def test_web_upload_quarantines_and_is_not_retrievable(repo, bus, caplog):
    from pydicom.dataset import Dataset
    from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

    ds, buf = _upload_dicom()
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.post(
            "/portal/upload",
            data={"dicomFiles": (buf, "evil.dcm")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200 and b"received" in resp.data
    assert "WEB_UPLOAD" in caplog.text

    q = Dataset()
    q.QueryRetrieveLevel = "IMAGE"
    q.StudyInstanceUID = ds.StudyInstanceUID
    q.SeriesInstanceUID = ds.SeriesInstanceUID
    q.SOPInstanceUID = ds.SOPInstanceUID
    matches = repo.find(q, StudyRootQueryRetrieveInformationModelFind).matches
    assert matches  # indexed and visible
    assert (
        repo.find_instance(matches[0]).error is not None
    )  # but quarantined -> jail refuses it


def test_web_upload_rejects_non_dicom(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    before = set(repo.storage.traces_dir.glob("*.gz"))
    resp = client.post(
        "/portal/upload",
        data={"dicomFiles": (io.BytesIO(b"not a dicom"), "x.dcm")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200 and b"1 rejected" in resp.data
    captured = set(repo.storage.traces_dir.glob("*.gz")) - before
    assert len(captured) == 1
    with gzip.open(captured.pop(), "rb") as source:
        assert source.read() == b"not a dicom"


def test_web_upload_preserves_exact_valid_bytes(repo, bus):
    _ds, buf = _upload_dicom()
    raw = buf.getvalue() + b"X"
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    before = set(repo.storage.traces_dir.glob("*.gz"))

    response = client.post(
        "/portal/upload",
        data={"dicomFiles": (io.BytesIO(raw), "trailing.dcm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200 and b"1 file(s) received" in response.data
    captured = set(repo.storage.traces_dir.glob("*.gz")) - before
    assert len(captured) == 1
    with gzip.open(captured.pop(), "rb") as source:
        assert source.read() == raw


def test_web_upload_submits_accepted_artifact_to_sink(repo, bus):
    submitted = []
    ds, buf = _upload_dicom()
    client = new_web(load_profile("generic-pacs"), repo, bus, sink=submitted.append).test_client()
    _login_generic(client)

    response = client.post(
        "/portal/upload",
        data={"dicomFiles": (buf, "ok.dcm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert len(submitted) == 1
    artifact = submitted[0]
    assert artifact.channel == "WEB"
    assert artifact.request_type == "WEB_UPLOAD"
    assert artifact.disposition == "stored"
    assert artifact.source_encoding == "part10"
    assert artifact.sop_instance_uid == str(ds.SOPInstanceUID)


def test_web_upload_succeeds_even_when_the_artifact_sink_raises(repo, bus):
    """Analysis failures must never change what the peer sees; the payload is already captured."""
    def exploding_sink(_artifact):
        raise RuntimeError("analysis store unavailable")

    _ds, buf = _upload_dicom()
    client = new_web(load_profile("generic-pacs"), repo, bus, sink=exploding_sink).test_client()
    _login_generic(client)

    response = client.post(
        "/portal/upload",
        data={"dicomFiles": (buf, "ok.dcm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "defect", ["unsupported-sop", "mismatched-meta", "missing-patient"]
)
def test_web_upload_rejects_invalid_dicom_identity(repo, bus, defect):
    from pydicom.uid import generate_uid

    ds, _buf = _upload_dicom()
    if defect == "unsupported-sop":
        ds.SOPClassUID = "1.2.840.10008.1.1"
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    elif defect == "missing-patient":
        del ds.PatientID
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    if defect == "mismatched-meta":
        raw = buf.getvalue()
        original = str(ds.SOPInstanceUID).encode()
        replacement = generate_uid(prefix="1.2.826.0.1.3680043.9.9.").encode()
        replacement = replacement[: len(original)].ljust(len(original), b"0")
        buf = io.BytesIO(raw.replace(original, replacement, 1))
    buf.seek(0)

    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    response = client.post(
        "/portal/upload",
        data={"dicomFiles": (buf, f"{defect}.dcm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200 and b"1 rejected" in response.data


def test_web_upload_reports_files_over_limit(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    files = []
    for index in range(11):
        _ds, buf = _upload_dicom()
        files.append((buf, f"{index}.dcm"))

    before = set(repo.storage.traces_dir.glob("*.gz"))
    response = client.post(
        "/portal/upload",
        data={"dicomFiles": files},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"10 file(s) received, 1 rejected" in response.data
    assert len(set(repo.storage.traces_dir.glob("*.gz")) - before) == 11


def test_web_upload_view_and_rejection_are_logged(repo, bus, caplog):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    with caplog.at_level(logging.INFO, logger="bus"):
        assert client.get("/portal/upload").status_code == 200
        client.post(
            "/portal/upload",
            data={"dicomFiles": (io.BytesIO(b"bad"), "bad.dcm")},
            content_type="multipart/form-data",
        )
    assert "WEB_UPLOAD_VIEW" in caplog.text
    assert "Rejected:" in caplog.text and "SHA256:" in caplog.text
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "bus"
    ]
    upload = next(
        event for event in events if event.get("request_type") == "WEB_UPLOAD"
    )
    assert upload["artifact"]["disposition"] == "rejected"
    assert upload["artifact"]["captured"] is True
    assert upload["artifact"]["bytes"] == 3


def test_browse_pages_are_bounded_and_keep_search_parameters(repo, bus):
    _seed_one(repo)
    profile = load_profile("generic-pacs")
    profile.web.browse_page_size = 1
    client = new_web(profile, repo, bus).test_client()
    _login_generic(client)

    first = client.get("/portal/instances")
    assert first.status_code == 200 and b"Next" in first.data
    assert b"Page 1" in first.data
    second = client.get("/portal/instances?page=2")
    assert second.status_code == 200 and b"Previous" in second.data
    search = client.get("/portal/search?searchType=name&q=*&page=1")
    assert b"searchType=name" in search.data and b"q=%2A" in search.data


def test_authenticated_entry_returns_to_console_and_logout_revokes(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    entry = client.get("/portal")
    assert entry.status_code == 302 and entry.headers["Location"] == "/portal/console"

    logout = client.post("/portal/logout")
    assert logout.status_code == 302 and logout.headers["Location"] == "/portal"
    assert client.get("/portal/console").status_code == 302


def test_authenticated_logs_do_not_expose_bearer_cookie(repo, bus, caplog):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    _login_generic(client)
    token = client.get_cookie("portal_authed").value
    with caplog.at_level(logging.INFO, logger="bus"):
        assert client.get("/portal/console").status_code == 200
    assert token not in caplog.text


def test_upload_has_a_route_specific_body_limit(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.max_request_bytes = 32
    profile.web.upload_max_request_bytes = 1024
    client = new_web(profile, repo, bus).test_client()
    _login_generic(client)
    _ds, buf = _upload_dicom()
    response = client.post(
        "/portal/upload",
        data={"dicomFiles": (buf, "ok.dcm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200 and b"1 file(s) received" in response.data


def test_browse_profile_requires_browse_templates(tmp_path):
    source = tmp_path / "web" / "templates"
    source.mkdir(parents=True)
    bundled = Path(__file__).parents[1] / "src/profiles/generic-pacs/web/templates"
    for name in (
        "login.html",
        "forgot_password.html",
        "error.html",
        "winauth_unable.html",
        "worklist.html",
    ):
        shutil.copy2(bundled / name, source / name)
    custom = tmp_path / "missing.yaml"
    custom.write_text(
        "meta:\n  name: missing\n  kind: pacs\n"
        f"web:\n  enabled: true\n  templates_dir: {tmp_path / 'web'}\n  browse: true\n"
    )
    with pytest.raises(ValueError, match="browse.html"):
        load_profile(str(custom))


def test_fujifilm_does_not_expose_browse(repo, bus):
    client = new_web(load_profile("fujifilm"), repo, bus).test_client()
    for path in (
        "/portal/console",
        "/portal/patients",
        "/portal/studies",
        "/portal/series",
        "/portal/instances",
        "/portal/upload",
    ):
        assert client.get(path).status_code == 404
    assert client.post("/portal/logout").status_code == 404
