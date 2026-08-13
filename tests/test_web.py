import base64
import gzip
import io
import json
import logging
import re
import shutil
from pathlib import Path

import pytest

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from seeding.locations import load_locations
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


def test_fingerprint_seam_empty_when_disabled(repo, bus):
    profile = load_profile("fujifilm")
    profile.web.fingerprint.enabled = False
    client = new_web(profile, repo, bus).test_client()

    resp = client.get(profile.web.routes["login"] + "?signin=abc")
    assert "data-signals" not in resp.get_data(as_text=True)


def test_fingerprint_seam_injects_collector_with_enabled_signals(repo, bus):
    profile = load_profile("fujifilm")
    profile.web.fingerprint.enabled = True
    profile.web.fingerprint.signals = ["math", "screen"]
    client = new_web(profile, repo, bus).test_client()

    resp = client.get(profile.web.routes["login"] + "?signin=abc")
    body = resp.get_data(as_text=True)
    assert '<script nonce="' in body
    assert profile.web.routes["fingerprint_script"] in body
    assert 'data-signals="math,screen"' in body


def test_honey_credential_grants_unconditionally_and_logs_distinctly(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    # The point: the declared pair works and is logged as bait, not as an ordinary attempt.
    assert profile.web.grant_access in {"bait", "keyword"}
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


def test_random_guess_still_denied_at_the_bait_level(repo, bus):
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
    assert resp.mimetype == "text/html"
    assert b"<!doctype html>" in resp.data.lower()
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
    assert resp.mimetype == "text/html"
    assert b"<!doctype html>" in resp.data.lower()
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
    profile.web.grant_access = "any"
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


def test_worklist_missing_slash_uses_iis_shaped_301_not_werkzeug_308(repo, bus):
    client = new_web(load_profile("fujifilm"), repo, bus).test_client()

    response = client.get("/WorkflowUI?path=CT")

    assert response.status_code == 301
    assert response.headers["Location"].endswith("/WorkflowUI/?path=CT")
    assert response.mimetype == "text/html"
    assert b"Object Moved" in response.data
    assert b"Redirecting..." not in response.data


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
    client = new_web(
        load_profile("generic-pacs"), repo, bus, sink=submitted.append
    ).test_client()
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
    client = new_web(
        load_profile("generic-pacs"), repo, bus, sink=exploding_sink
    ).test_client()
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


# --- Fujifilm worklist page ---


def _worklist_client(repo, bus, seed=True):
    """Signed-in fujifilm client with one seeded CT study on the worklist."""
    if seed:
        from seeding.seeder import new_seeder

        seeder = new_seeder(repo)
        assert seeder._seed_fallback(seeder._locations[0], "CT", "worklist-epoch") > 0
    client = new_web(load_profile("fujifilm"), repo, bus).test_client()
    login = client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": "svc_dicom", "password": "svc_dicom"},
    )
    assert login.status_code == 302
    return client


def test_fujifilm_honey_credential_lands_on_the_worklist(repo, bus, caplog):
    client = new_web(load_profile("fujifilm"), repo, bus).test_client()

    with caplog.at_level(logging.INFO, logger="bus"):
        login = client.post(
            "/SynapseSignOn/sts/login?signin=x",
            data={"username": "svc_dicom", "password": "svc_dicom"},
        )

    assert login.status_code == 302
    assert login.headers["Location"] == "/WorkflowUI/?path="
    cookies = "\n".join(login.headers.getlist("Set-Cookie"))
    assert "IdpCookie=" in cookies
    assert "Secure" in cookies
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text
    assert client.get("/WorkflowUI/").status_code == 200


def test_fujifilm_wrong_password_is_still_denied(repo, bus):
    """A honeypot that accepts any password identifies itself on the first wrong guess."""
    profile = load_profile("fujifilm")
    client = new_web(profile, repo, bus).test_client()

    username = "j.okonkwo"
    assert not any(
        k in username.casefold() for k in profile.web.honey_keywords
    ), "pick a username the keyword rule does not cover, or this proves nothing"

    denied = client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": username, "password": "not-the-password"},
    )

    assert denied.status_code == 200
    assert b"incorrect" in denied.data
    assert "IdpCookie=" not in "\n".join(denied.headers.getlist("Set-Cookie"))
    assert client.get("/WorkflowUI/").status_code == 302


def test_fujifilm_login_page_never_reveals_the_honey_credential(repo, bus):
    profile = load_profile("fujifilm")
    client = new_web(profile, repo, bus).test_client()

    body = client.get("/SynapseSignOn/sts/login?signin=x").get_data(as_text=True)

    for username, password in profile.web.honey_credentials:
        assert username not in body
        assert password not in body


def test_worklist_renders_seeded_studies_with_real_image_counts(repo, bus):
    client = _worklist_client(repo, bus)
    body = client.get("/WorkflowUI/").get_data(as_text=True)
    uids = re.findall(r'<tr data-uid="([^"]+)"', body)
    expected = repo.count_instances(uids)

    assert "worklist-table" in body
    assert "No studies." not in body
    assert expected
    for count in expected.values():
        assert f"<td>{count}</td>" in body


def test_worklist_formats_person_names_without_the_dicom_caret(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    # No PACS shows raw PN; the caret would be an instant tell.
    assert "^" not in body


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Miller^Monica", "Miller, Monica"),
        ("Miller^Monica^A^Dr^MD", "Miller, Monica"),
        ("Cher", "Cher"),
        ("Miller^", "Miller"),
        ("^Monica", "Monica"),
        ("", ""),
        (None, ""),
    ],
)
def test_format_person_name_cases(value, expected):
    from web.app import _format_person_name

    assert _format_person_name(value) == expected


def test_worklist_shows_attributes_the_qr_index_cannot_hold(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    # Sex, DOB, body part and institution live in the detail index, not db.Instance.
    assert "<td>M</td>" in body or "<td>F</td>" in body
    assert "<td>CHEST</td>" in body
    assert any(loc.institution in body for loc in load_locations(None))


def test_worklist_renders_the_generated_procedure_description(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    # Seeded studies carry a real StudyDescription, so the placeholder must not appear.
    assert "UNKNOWN" not in body


def test_worklist_falls_back_to_the_profile_placeholder_without_a_description(
    repo, bus
):
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
    ds.PatientID = "NODESC"
    ds.PatientName = "Doe^Jane"
    ds.Modality = "CT"
    repo.start()
    assert repo.store(ds, safe=True) is None
    client = _worklist_client(repo, bus, seed=False)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    assert "UNKNOWN" in body
    # The literal is profile data, never a hardcoded string in the engine.
    assert "UNKNOWN" not in Path("src/web/app.py").read_text()


def test_worklist_shows_the_signed_in_username(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    assert "SVC_DICOM" in body


def test_worklist_username_is_escaped_and_bounded(repo, bus):
    from web.app import _WEB_USERNAME_LIMIT

    profile = load_profile("fujifilm")
    profile.web.grant_access = "any"
    client = new_web(profile, repo, bus).test_client()
    hostile = "<script>alert(1)</script>" + "A" * 5000
    client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": hostile, "password": "x"},
    )

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    assert "<script>alert(1)</script>" not in body
    assert "<SCRIPT>ALERT(1)</SCRIPT>" not in body
    assert "&lt;SCRIPT&gt;" in body
    assert "A" * (_WEB_USERNAME_LIMIT + 1) not in body


def test_worklist_shell_strings_come_from_the_profile(repo, bus):
    profile = load_profile("fujifilm")
    profile.web.worklist["sidebar"] = [
        {"label": "Injected Section", "items": [{"label": "Injected Folder"}]}
    ]
    client = new_web(profile, repo, bus).test_client()
    client.post(
        "/SynapseSignOn/sts/login?signin=x",
        data={"username": "svc_dicom", "password": "svc_dicom"},
    )

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    assert "Injected Section" in body
    assert "Injected Folder" in body
    assert "Global Worklists" not in body


def test_worklist_matches_the_stateful_synapse_shell(repo, bus):
    body = _worklist_client(repo, bus).get("/WorkflowUI/?path=").get_data(as_text=True)

    assert 'data-section="Worklists"' in body
    assert 'aria-expanded="true" data-section="Worklists"' in body
    assert 'aria-expanded="false" data-section="Global Worklists"' in body
    assert 'role="toolbar"' in body
    assert "fa-comment" in body
    assert "fa-camera" in body
    assert "Change Priority" in body
    assert "Reserve for Others" in body
    assert 'class="disabled"' in body
    assert "setInterval(updateClock, 1000)" in body
    total = repo.count_studies()
    assert f'<span class="wl-badge">{total}</span>' in body


def test_worklist_icons_resolve_to_a_font_the_profile_actually_ships():
    web = Path("src/profiles/fujifilm/web")
    css = (web / "static/synapse/head-third-party.min.css").read_text()
    worklist = load_profile("fujifilm").web.worklist
    icons = [
        item["icon"]
        for key in ("header_links", "toolbar")
        for item in worklist.get(key, [])
    ]
    assert icons, "profile declares no icons to check"

    for icon in icons:
        assert (
            f".fa-{icon}:before" in css
        ), f"fa-{icon} is not defined in the shipped CSS"

    face = re.search(r"@font-face\{font-family:FontAwesome;(.*?)\}", css)
    assert face, "no FontAwesome @font-face in the shipped CSS"
    sources = re.findall(r"url\('([^']+)'", face.group(1))
    resolved = [
        (web / "static/synapse" / src.split("?")[0]).resolve() for src in sources
    ]
    assert any(path.is_file() for path in resolved), (
        "every FontAwesome webfont referenced by the CSS is missing; "
        "the icons would render as blank boxes"
    )


def test_sign_out_exists_on_a_worklist_profile_at_its_own_path(repo, bus):
    profile = load_profile("fujifilm")
    logout = profile.web.routes["logout"]
    assert logout == "/SynapseSignOn/sts/logout", "must not inherit the generic path"
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/?path=").get_data(as_text=True)
    assert logout in body, "no sign-out control on the worklist"

    assert client.post(logout).status_code == 302
    assert client.get("/WorkflowUI/?path=").status_code == 302  # session really revoked
    assert client.post("/portal/logout").status_code == 404  # no cross-profile leak


def test_login_page_never_advertises_a_logout_url_that_does_not_exist(repo, bus):
    checked = 0
    for name in ("fujifilm", "generic-pacs"):
        profile = load_profile(name)
        client = new_web(profile, repo, bus).test_client()
        body = client.get(profile.web.routes["login"] + "?signin=x").get_data(
            as_text=True
        )
        advertised = re.search(r'logoutUrl["\']?\s*:\s*["\']([^"\']+)', body)
        if advertised is None:
            continue
        checked += 1
        assert advertised.group(1) == profile.web.routes["logout"], name
        assert client.post(advertised.group(1)).status_code != 404, name
    assert (
        checked
    ), "no shipped profile advertises a logoutUrl; the check proved nothing"


def test_worklist_does_not_copy_site_specific_demo_labels(repo, bus):
    body = _worklist_client(repo, bus).get("/WorkflowUI/?path=").get_data(as_text=True)

    for label in ("Matt test", "dynamic 2", "Mammo - Additional Samples"):
        assert label not in body


def test_worklist_template_hardcodes_no_folder_names():
    template = Path("src/profiles/fujifilm/web/templates/worklist.html").read_text()

    # The old stub inlined its folder list; every label is profile data now.
    for literal in ("All Studies", "Assigned to me", "STAT", "Unread"):
        assert literal not in template


def test_worklist_sidebar_folder_filters_the_study_list(repo, bus):
    client = _worklist_client(repo, bus)

    matching = client.get("/WorkflowUI/?path=CT").get_data(as_text=True)
    other = client.get("/WorkflowUI/?path=CR").get_data(as_text=True)

    assert "No studies." not in matching
    assert "No studies." in other


def test_worklist_unknown_folder_falls_back_without_reflecting_it(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/?path=../../etc/passwd").get_data(as_text=True)

    assert "No studies." not in body
    assert "etc/passwd" not in body


def test_worklist_detail_panel_opens_for_a_listed_study(repo, bus, caplog):
    client = _worklist_client(repo, bus)
    listing = client.get("/WorkflowUI/").get_data(as_text=True)
    uid = listing.split('data-uid="')[1].split('"')[0]

    with caplog.at_level(logging.INFO, logger="bus"):
        body = client.get(
            f"/WorkflowUI/?action=Study+Information&study={uid}"
        ).get_data(as_text=True)

    assert '<div class="wl-detail">' in body
    assert f"Study: {uid}" in caplog.text


def test_worklist_detail_cannot_probe_for_an_unlisted_study(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/?study=1.2.3.not.a.real.study").get_data(
        as_text=True
    )

    assert '<div class="wl-detail">' not in body
    assert "1.2.3.not.a.real.study" not in body


def test_worklist_action_renders_the_configured_error(repo, bus, caplog):
    client = _worklist_client(repo, bus)
    message = load_profile("fujifilm").web.worklist["messages"]["action_failed"]

    with caplog.at_level(logging.INFO, logger="bus"):
        body = client.get("/WorkflowUI/?action=Open+Viewer").get_data(as_text=True)

    assert "alert-danger" in body
    assert message in body
    assert "Action: Open Viewer" in caplog.text


def test_worklist_action_resolution_walks_nested_submenus():
    from web.app import _selected_action

    leaf = {"label": "Deep Action", "result": "error"}
    worklist = {
        "context_menu": [
            {
                "label": "First",
                "result": "submenu",
                "items": [
                    {
                        "label": "Second",
                        "result": "submenu",
                        "items": [leaf],
                    }
                ],
            }
        ]
    }

    assert _selected_action(worklist, "Deep Action") is leaf
    assert _selected_action(worklist, "attacker supplied") is None


@pytest.mark.parametrize(
    "action",
    ["<script>alert(1)</script>", "Open Viewer'; DROP TABLE", "A" * 5000, ""],
)
def test_worklist_unknown_action_never_reflects_raw_input(repo, bus, action):
    client = _worklist_client(repo, bus)

    resp = client.get("/WorkflowUI/", query_string={"action": action})
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    if action:
        assert action not in body
        assert "alert-danger" in body


def test_worklist_column_filter_is_bounded_in_the_log(repo, bus, caplog):
    from web.app import _WORKLIST_PARAM_LIMIT

    client = _worklist_client(repo, bus)

    with caplog.at_level(logging.INFO, logger="bus"):
        client.get("/WorkflowUI/", query_string={"filter_patient_name": "B" * 10_000})

    logged = [
        record
        for record in caplog.records
        if "WEB_WORKLIST_VIEW" in record.getMessage()
    ]
    params = json.loads(logged[-1].getMessage())["session_parameters"]
    term = next(p for p in params if p.startswith("Filter patient_name"))

    assert len(term.split(": ", 1)[1]) == _WORKLIST_PARAM_LIMIT


def test_worklist_column_filters_change_the_visible_rows(repo, bus):
    client = _worklist_client(repo, bus)
    listing = client.get("/WorkflowUI/?path=").get_data(as_text=True)
    patient = re.search(r'<tr data-uid="[^"]+"[^>]*>.*?<td>([^<]+)</td>', listing, re.S)
    assert patient is not None

    filtered = client.get(
        "/WorkflowUI/",
        query_string={"path": "", "filter_patient_name": patient.group(1)},
    ).get_data(as_text=True)
    missing = client.get(
        "/WorkflowUI/",
        query_string={"path": "", "filter_patient_name": "NO-SUCH-PATIENT"},
    ).get_data(as_text=True)

    assert patient.group(1) in filtered
    assert "No studies." in missing


def test_worklist_unknown_filter_parameter_is_ignored(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get(
        "/WorkflowUI/", query_string={"filter_nosuchcolumn": "PWNED"}
    ).get_data(as_text=True)

    assert "PWNED" not in body


def test_worklist_inline_scripts_carry_the_csp_nonce(repo, bus):
    client = _worklist_client(repo, bus)

    resp = client.get("/WorkflowUI/")
    body = resp.get_data(as_text=True)
    nonce = re.search(r"'nonce-([^']+)'", resp.headers["Content-Security-Policy"])
    inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", body)

    assert nonce is not None
    assert inline
    for tag in inline:
        assert f'nonce="{nonce.group(1)}"' in tag


def test_worklist_loads_no_external_assets(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    # A strict default-src 'self' CSP would block these anyway; catch them at authoring time.
    assert not re.findall(r'(?:src|href)="https?://', body)


def test_worklist_footer_counts_every_indexed_study(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)

    assert f"of {repo.count_studies()} items" in body


def test_generic_pacs_worklist_still_renders_without_synapse_chrome(repo, bus):
    from seeding.seeder import new_seeder

    seeder = new_seeder(repo)
    assert seeder._seed_fallback(seeder._locations[0], "CT", "epoch") > 0
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    client.post("/portal/login?signin=x", data={"username": "test", "password": "test"})

    resp = client.get("/portal/worklist/")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "No studies." not in body
    for leaked in ("Synapse", "WorkflowUI", "Global Worklists", "UNKNOWN"):
        assert leaked not in body


def test_engine_holds_no_worklist_vendor_strings():
    source = Path("src/web/app.py").read_text()

    # Login-page vendor strings predate this work; the worklist must add none of its own.
    for token in ("Proc Description", "All Studies", "Global Worklists", "UNKNOWN"):
        assert token not in source


@pytest.mark.parametrize(
    "born,studied,expected",
    [
        ("19670202", "20140809", "47Y"),
        ("19670202", "20140201", "46Y"),
        ("20140809", "20140809", "0Y"),
        ("", "20140809", ""),
        ("19670202", "", ""),
        ("notadate", "20140809", ""),
        ("20200101", "19900101", ""),
    ],
)
def test_age_at_cases(born, studied, expected):
    from web.app import _display_age

    assert _display_age(born, studied) == expected


def test_worklist_shows_the_patient_age_on_the_study_date(repo, bus):
    client = _worklist_client(repo, bus)

    body = client.get("/WorkflowUI/").get_data(as_text=True)
    ages = re.findall(r"<td>(\d{1,3}Y)</td>", body)

    # Derived from the seeded DOB and study date, not carried over from the source data.
    assert ages


def test_grant_access_none_denies_even_a_declared_honey_credential(repo, bus):
    # 'none' is the whole-surface off switch: the bait list stays declared but nothing gets in.
    profile = load_profile("generic-pacs")
    profile.web.grant_access = "none"
    client = new_web(profile, repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": "test", "password": "test"}
    )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data
    assert not resp.headers.getlist("Set-Cookie")


def test_grant_access_any_admits_a_password_that_is_not_bait(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.grant_access = "any"
    client = new_web(profile, repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": "attacker", "password": "guess"}
    )
    assert resp.status_code == 302


def test_winauth_honours_the_same_gate_as_the_form_login(repo, bus):
    # Two ways in; a gate that only covered one of them would be a hole.
    profile = load_profile("fujifilm")
    profile.web.grant_access = "none"
    client = new_web(profile, repo, bus).test_client()

    header = base64.b64encode(b"svc_dicom:svc_dicom").decode()
    resp = client.get(
        profile.web.routes["winauth"], headers={"Authorization": f"Basic {header}"}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("admin", "whatever"),  # keyword in the username
        ("bob", "pacs2024"),  # keyword in the password
        ("bob", "myRadiologyPass"),  # mid-string, not a prefix
        ("bob", "PACS"),  # case-insensitive
        ("DicomSvc", "x"),  # case-insensitive on the username too
    ],
)
def test_keyword_mode_admits_a_credential_containing_a_declared_keyword(
    repo, bus, username, password
):
    profile = load_profile("generic-pacs")
    assert profile.web.grant_access == "keyword"
    client = new_web(profile, repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": username, "password": password}
    )
    assert resp.status_code == 302


def test_a_keyword_in_a_bait_username_still_requires_the_bait_password(repo, bus):
    """Declared bait users must not become universal passwords via keyword matching."""
    profile = load_profile("fujifilm")
    baits = [u for u, _ in profile.web.honey_credentials]
    assert any(
        k in u.casefold() for u in baits for k in profile.web.honey_keywords
    ), "no bait username carries a keyword; this test proves nothing"
    client = new_web(profile, repo, bus).test_client()

    for username in baits:
        resp = client.post(
            "/SynapseSignOn/sts/login?signin=x",
            data={"username": username, "password": "not-the-password"},
        )
        assert resp.status_code == 200, username
        assert b"incorrect" in resp.data.lower(), username
        assert not resp.headers.getlist("Set-Cookie"), username


def test_keyword_mode_still_denies_a_credential_with_no_keyword(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": "bob", "password": "hunter2"}
    )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data
    assert not resp.headers.getlist("Set-Cookie")


def test_keyword_mode_still_admits_the_exact_bait_pair(repo, bus, caplog):
    profile = load_profile("generic-pacs")
    client = new_web(profile, repo, bus).test_client()

    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.post(
            "/portal/login?signin=x", data={"username": "test", "password": "test"}
        )
    assert resp.status_code == 302
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text


def test_bait_mode_ignores_declared_keywords(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.grant_access = "bait"
    client = new_web(profile, repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": "admin", "password": "whatever"}
    )
    assert resp.status_code == 200


def test_none_denies_a_keyword_match(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.grant_access = "none"
    client = new_web(profile, repo, bus).test_client()

    resp = client.post(
        "/portal/login?signin=x", data={"username": "admin", "password": "whatever"}
    )
    assert resp.status_code == 200
    assert not resp.headers.getlist("Set-Cookie")


def test_keyword_grant_logs_which_keyword_matched_and_where(repo, bus, caplog):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()

    with caplog.at_level(logging.WARNING, logger="bus"):
        client.post(
            "/portal/login?signin=x",
            data={"username": "bob", "password": "MyPacsPassword"},
        )

    assert "WEB_HONEY_KEYWORD_USED" in caplog.text
    assert "Keyword: pacs (password)" in caplog.text


def test_winauth_honours_keyword_mode_too(repo, bus, caplog):
    profile = load_profile("fujifilm")
    assert profile.web.grant_access == "keyword"
    client = new_web(profile, repo, bus).test_client()

    header = base64.b64encode(b"radiology-svc:anything").decode()
    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.get(
            profile.web.routes["winauth"], headers={"Authorization": f"Basic {header}"}
        )
    assert resp.status_code == 302
    assert "WEB_HONEY_KEYWORD_USED" in caplog.text


@pytest.mark.parametrize(
    ("keywords", "match"),
    [
        ("admin", "must be a list"),
        ([42], r"honey_keywords\[0\]' must be a string"),
        (["ab"], "at least 3 characters"),
        ([""], "at least 3 characters"),
    ],
)
def test_malformed_keywords_are_refused_at_load(repo, keywords, match):
    import yaml

    source = Path(__file__).parents[1] / "src/profiles/generic-pacs/generic-pacs.yaml"
    raw = yaml.safe_load(source.read_text())
    raw["web"]["honey_keywords"] = keywords
    broken = Path(repo.storage.storage_dir).parent / "bad-keywords.yaml"
    broken.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match=match):
        load_profile(str(broken))


def test_keyword_mode_without_keywords_is_refused_rather_than_silently_bait(repo):
    import yaml

    source = Path(__file__).parents[1] / "src/profiles/generic-pacs/generic-pacs.yaml"
    raw = yaml.safe_load(source.read_text())
    raw["web"]["honey_keywords"] = []
    broken = Path(repo.storage.storage_dir).parent / "empty-keywords.yaml"
    broken.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="honey_keywords' is empty"):
        load_profile(str(broken))


def test_keywords_are_casefolded_and_deduplicated_at_load(repo):
    import yaml

    source = Path(__file__).parents[1] / "src/profiles/generic-pacs/generic-pacs.yaml"
    raw = yaml.safe_load(source.read_text())
    raw["web"]["honey_keywords"] = ["ADMIN", "admin", " Pacs "]
    profile_path = Path(repo.storage.storage_dir).parent / "dup-keywords.yaml"
    profile_path.write_text(yaml.safe_dump(raw))

    assert load_profile(str(profile_path)).web.honey_keywords == ["admin", "pacs"]


def test_a_boolean_grant_access_is_refused_with_its_replacement_named(repo, bus):
    import yaml

    source = Path(__file__).parents[1] / "src/profiles/generic-pacs/generic-pacs.yaml"
    raw = yaml.safe_load(source.read_text())
    raw["web"]["grant_access"] = False
    broken = Path(repo.storage.storage_dir).parent / "boolean-grant.yaml"
    broken.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="no longer boolean"):
        load_profile(str(broken))


def test_the_app_server_header_is_hidden_from_waitress():
    # waitress answers an app-set Server header with "Via: waitress", naming the real stack.
    from web.component import _hide_app_server_header

    seen = []

    def app(environ, start_response):
        start_response("200 OK", [("Server", "Microsoft-IIS/10.0"), ("X-Keep", "1")])
        return [b""]

    _hide_app_server_header(app)(
        {}, lambda status, headers, exc=None: seen.append(headers)
    )

    assert seen == [[("X-Keep", "1")]]


def test_the_web_listener_hands_waitress_the_profile_server_header(repo, bus):
    # Only waitress may emit Server, and it must emit the profile's spoofed value.
    import web.component as component_module
    from web.component import WebComponent

    prof = load_profile("fujifilm")
    component = WebComponent(prof, repo, bus, "127.0.0.1", 0, 0)
    captured = []

    def fake_build(specs, trusted_proxy=None):
        captured.extend(specs)
        return [], [], None

    original = component_module._build_servers
    component_module._build_servers = fake_build
    try:
        component.start()
    finally:
        component_module._build_servers = original

    idents = {name: spec[-1] for name, *spec in ((s[0], *s[1:]) for s in captured)}
    assert idents["web"] == prof.web.headers["Server"]
    assert idents["operator"] is None
