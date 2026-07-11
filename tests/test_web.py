import base64
import logging

import pytest

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.app import new_web


@pytest.fixture
def bus():
    logger = logging.getLogger("bus")
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
    assert profile.web.grant_access is False  # the point: bait works even though real logins don't
    client = new_web(profile, repo, bus).test_client()

    with caplog.at_level(logging.WARNING, logger="bus"):
        resp = client.post(
            "/portal/login?signin=x", data={"username": "test", "password": "test"}
        )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/portal"
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text


def test_honey_hint_visible_on_generic_pacs_not_fujifilm(repo, bus):
    generic_body = new_web(load_profile("generic-pacs"), repo, bus).test_client().get(
        "/portal/login?signin=x"
    ).get_data(as_text=True)
    assert "test / test" in generic_body

    fuji_body = new_web(load_profile("fujifilm"), repo, bus).test_client().get(
        "/SynapseSignOn/sts/login?signin=x"
    ).get_data(as_text=True)
    assert "test / test" not in fuji_body


def test_random_guess_still_denied_when_grant_access_false(repo, bus):
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()
    resp = client.post(
        "/portal/login?signin=x", data={"username": "attacker", "password": "guess"}
    )
    assert resp.status_code == 200
    assert b"incorrect" in resp.data


def test_generic_pacs_routes_and_cookies_are_not_synapse_shaped(repo, bus):
    """The direct ask: nothing in generic-pacs's address bar, forms, or cookies says 'Synapse'."""
    client = new_web(load_profile("generic-pacs"), repo, bus).test_client()

    resp = client.get("/portal")
    assert resp.status_code == 302
    assert "SynapseSignOn" not in resp.headers["Location"]

    login_resp = client.get("/portal/login?signin=x")
    body = login_resp.get_data(as_text=True)
    assert "Synapse" not in body
    assert "SynapseSignOn" not in body
    cookie_names = [c.split("=")[0] for c in login_resp.headers.getlist("Set-Cookie")]
    assert not any("idsrv" in c or "SignInMessage" in c or "OpenIdConnect" in c for c in cookie_names)

    winauth_resp = client.get("/portal/winauth")
    assert winauth_resp.status_code == 401
    assert "SynapseSignOn" not in winauth_resp.get_data(as_text=True)

    # Old Synapse-specific paths simply don't exist on this profile.
    assert client.get("/Synapse").status_code == 404
    assert client.get("/SynapseSignOn/sts/login").status_code == 404


def test_error_and_forgot_password_pages_use_overridden_routes(tmp_path, repo, bus):
    """Regression: error.html/forgot_password.html used to hardcode Fujifilm's real
    paths instead of reading profile.web.routes, so overriding routes.login/
    .forgot_password silently left the rendered link/form pointing at a route
    that no longer existed."""
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

    error_body = client.get("/portal/error").get_data(as_text=True)  # sts_error kept its default route
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
        resp = client.get("/portal/winauth", headers={"Authorization": f"Basic {creds}"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/portal"
    assert "WEB_HONEY_CREDENTIAL_USED" in caplog.text


def test_translated_items_uses_pascal_case_keys_the_client_js_expects(client):
    """Regression: translation.js (real Synapse asset) reads data['Text1']/['Text2']/
    ['Text3'] — PascalCase, ASP.NET's wire convention. The route used to return the
    config's own lowercase text1/text2/text3 keys verbatim, so every key lookup in the
    browser came back undefined and the WinAuth-cancelled page showed literal
    "undefined" for both the title and the button — only visible in a real browser
    (curl never runs the JS), which is how it slipped past testing."""
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
    assert resp.headers["Location"] == "/Synapse"  # engine's own entry point, not a hardcoded URL
    assert "WEB_HONEYTRAP_LOGIN_REDIRECT" in caplog.text

    resp = client.get("/Swat/api/sso/signoff")  # nested paths fold into the same catch-all
    assert resp.status_code == 302


def test_workflow_engine_probe_returns_stock_style_json(client, caplog):
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.post("/api/WorkflowEngine/Help/Api/Study/UnreserveStudy")
    assert resp.status_code == 404
    assert "No HTTP resource was found" in resp.get_json()["Message"]
    assert "WEB_HONEYTRAP_API_404" in caplog.text


def test_profile_without_honeytraps_gets_none(tmp_path, repo, bus):
    """The core ask: a new profile that declares no honeytraps must not inherit another
    vendor's bait routes just because it reuses the same engine."""
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
    """The other half of the ask: any profile can plug into the same generic responses."""
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
    assert resp.headers["Location"] == "/portal"  # this custom profile didn't override web.routes either


def test_unmapped_path_gets_spoofed_headers_not_werkzeug_default(client, caplog):
    # This is the actual regression: blueprint-scoped hooks never fire for a path that
    # matches no route at all, so headers/404 body must be registered at the app level.
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
    """A profile that only sets web.enabled + templates_dir must not crash on real requests."""
    sparse = tmp_path / "sparse.yaml"
    sparse.write_text(
        "meta:\n  name: sparse\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
    )
    profile = load_profile(str(sparse))
    app = new_web(profile, repo, bus)
    client = app.test_client()

    assert client.get("/portal").status_code == 302  # no routes override -> generic default, not /Synapse
    assert client.get("/portal/login?signin=x").status_code == 200
    assert client.get("/favicon.ico").status_code == 404  # no favicon configured -> 404, not a crash
    assert client.post(
        "/portal/login?signin=x", data={"username": "a", "password": "b"}
    ).status_code == 200


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

    client.set_cookie("sw_authed", "1")
    with caplog.at_level(logging.INFO, logger="bus"):
        resp = client.get("/Synapse")
    assert resp.status_code == 200
    assert b"worklist-table" in resp.data
    assert b"No studies." not in resp.data
    assert "WEB_WORKLIST_VIEW" in caplog.text


def test_generic_pacs_profile_serves_all_pages(repo, bus):
    """161-11: a second profile, built from almost no YAML, must render every page
    the shared engine drives it through — the actual proof the architecture works."""
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
    """generic-pacs demonstrates the unauthorized_page response kind (v2.0 design ref)."""
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

    assert fuji_client.get("/SynapseSignOn/sts/login?signin=x").headers["Server"] == "Microsoft-IIS/10.0"
    assert generic_client.get("/SynapseSignOn/sts/login?signin=x").headers["Server"] == "Apache"
