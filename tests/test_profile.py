import pytest

from profiles.profile import default_profile, load_profile


def test_default_profile_is_generic():
    prof = default_profile()
    assert prof.kind == "dicom"
    assert prof.ae_title == "ORTHANC"
    assert prof.web.enabled is False
    assert "echo" in prof.dicom.operations
    # Tighter than pynetdicom's own 30s/60s defaults — shrinks how long a raw garbage
    # connection can occupy a max_associations slot without ever sending a valid PDU.
    assert prof.dicom.acse_timeout == 10
    assert prof.dicom.network_timeout == 15


def test_load_profile_none_matches_default():
    assert load_profile(None).ae_title == default_profile().ae_title
    assert load_profile("").kind == default_profile().kind


def test_load_profile_generic_pacs_reuses_default_fallbacks():
    """The extensibility proof: generic-pacs declares almost nothing and gets a
    working, vendor-neutral identity purely from default_profile()'s fallbacks."""
    prof = load_profile("generic-pacs")
    assert prof.kind == "pacs"
    assert prof.web.enabled is True
    assert prof.web.templates_dir == "generic-pacs"
    assert prof.ae_title == default_profile().ae_title == "ORTHANC"
    assert prof.dicom.storage_classes == default_profile().dicom.storage_classes
    assert prof.web.headers == default_profile().web.headers
    assert prof.web.honeytraps == [("/admin/", "unauthorized_page")]
    assert prof.web.honey_credentials == [("test", "test")]
    assert prof.web.routes == default_profile().web.routes  # /portal/*, not /Synapse — the actual isolation fix
    assert prof.web.cookies == default_profile().web.cookies


def test_load_profile_fujifilm():
    prof = load_profile("fujifilm")
    assert prof.kind == "pacs"
    assert prof.ae_title == "SYNAPSEDICOMSCP"
    assert prof.implementation_class_uid == "1.2.840.113845.1.1"
    assert prof.web.enabled is True
    assert prof.web.templates_dir == "fujifilm"
    assert prof.web.headers["Server"] == "Microsoft-IIS/10.0"
    assert prof.web.oidc["client_id"] == "synapsebaseclient"
    assert prof.web.honeytraps == [("/Swat/", "login_redirect"), ("/api/WorkflowEngine/", "api_404")]
    assert prof.web.honey_credentials == []  # left commented out: no disclosure channel on a verbatim-captured page
    assert prof.web.routes["entry"] == "/Synapse"
    assert prof.web.routes["login"] == "/SynapseSignOn/sts/login"
    assert prof.web.cookies["antiforgery"] == "idsrv.xsrf"
    assert prof.web.cookies["session"] == "sw_authed"
    assert len(prof.dicom.storage_classes) == 77


def test_profile_can_override_timeouts(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: dicom\n"
        "dicom:\n  acse_timeout: 5\n  network_timeout: null\n"
    )
    prof = load_profile(str(custom))
    assert prof.dicom.acse_timeout == 5
    assert prof.dicom.network_timeout is None  # explicit null -> pynetdicom's own default, not the fallback


def test_load_profile_unknown_name_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("not-a-real-profile")


def test_partial_profile_falls_back_to_defaults(tmp_path, caplog):
    partial = tmp_path / "partial.yaml"
    partial.write_text("meta:\n  name: partial\n  kind: pacs\n")

    prof = load_profile(str(partial))

    assert prof.name == "partial"
    assert prof.kind == "pacs"
    assert prof.dicom.operations == default_profile().dicom.operations
    assert "using defaults for" in caplog.text


def test_malformed_profile_raises():
    partial_bad = {
        "dicom": {"storage_classes": [{"uid": "1.2.3"}]},  # missing transfer_syntaxes
    }
    import yaml
    from profiles.profile import _parse_profile

    with pytest.raises(ValueError):
        _parse_profile(yaml.safe_load(yaml.dump(partial_bad)))


def test_sparse_web_profile_falls_back_not_crashes(tmp_path):
    """web.enabled=true with no other web.* keys must get real, working defaults, not empty dicts."""
    sparse = tmp_path / "sparse.yaml"
    sparse.write_text(
        "meta:\n  name: sparse\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
    )

    prof = load_profile(str(sparse))

    assert prof.web.enabled is True
    assert prof.web.content_security_policy  # not None -> _spoof() won't crash on .replace()
    assert prof.web.identity["version"]      # not {} -> _login_context() won't KeyError
    assert prof.web.license["lines"] == []   # present key, empty is fine, missing is not
    assert prof.web.oidc["redirect_path"]    # not {} -> _winlogin_url() won't KeyError


def test_web_enabled_without_templates_dir_raises():
    import yaml
    from profiles.profile import _parse_profile

    data = yaml.safe_load("meta:\n  name: notemplates\n  kind: pacs\nweb:\n  enabled: true\n")
    with pytest.raises(ValueError, match="templates_dir"):
        _parse_profile(data)
