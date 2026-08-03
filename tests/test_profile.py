import pytest

from profiles.profile import default_profile, load_profile


def test_default_profile_is_generic():
    prof = default_profile()
    assert prof.kind == "dicom"
    assert prof.ae_title == "ORTHANC"
    assert prof.web.enabled is False
    assert "echo" in prof.dicom.operations
    # Silent peers should release association slots before pynetdicom's defaults.
    assert prof.dicom.acse_timeout == 10
    assert prof.dicom.network_timeout == 15
    assert prof.dicom.dimse_timeout == 20
    assert prof.dicom.max_associations == 16
    assert prof.dicom.max_store_bytes == 64 * 1024 * 1024
    assert prof.dicomweb.max_request_bytes == 64 * 1024 * 1024


def test_load_profile_none_matches_default():
    assert load_profile(None).ae_title == default_profile().ae_title
    assert load_profile("").kind == default_profile().kind


def test_load_profile_generic_pacs_reuses_default_fallbacks():
    prof = load_profile("generic-pacs")
    assert prof.kind == "pacs"
    assert prof.web.enabled is True
    assert prof.web.templates_dir == "generic-pacs"
    assert prof.ae_title == default_profile().ae_title == "ORTHANC"
    assert prof.dicom.storage_classes == default_profile().dicom.storage_classes
    assert prof.web.headers == default_profile().web.headers
    assert prof.web.honeytraps == [("/admin/", "unauthorized_page")]
    assert prof.web.honey_credentials == [("test", "test")]
    assert prof.web.browse is True
    assert prof.web.max_request_bytes == 1024 * 1024
    assert prof.web.upload_max_request_bytes == 50 * 1024 * 1024
    assert (
        prof.web.routes == default_profile().web.routes
    )  # /portal/*, not /Synapse — the actual isolation fix
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
    assert prof.web.honeytraps == [
        ("/Swat/", "login_redirect"),
        ("/api/WorkflowEngine/", "api_404"),
    ]
    # Bait accounts are set, but the verbatim-captured sign-on page still discloses no hint.
    assert prof.web.honey_credentials == [
        ("svc_dicom", "svc_dicom"),
        ("pacsadmin", "Password1"),
    ]
    assert prof.web.routes["entry"] == "/Synapse"
    assert prof.web.routes["worklist"] == "/WorkflowUI/"
    assert prof.web.routes["login"] == "/SynapseSignOn/sts/login"
    assert prof.web.cookies["antiforgery"] == "idsrv.xsrf"
    assert prof.web.cookies["session"] == "IdpCookie"
    assert prof.web.secure_cookies is True
    assert prof.web.identity["version"] == "7.4.300"
    assert prof.web.oidc["redirect_path"] == "/WorkflowUI/"
    assert "user_domain" in prof.web.oidc["scopes"]
    assert prof.web.legacy_csp_header is True
    assert len(prof.dicom.storage_classes) == 77
    assert prof.dicomweb.enabled is True
    assert prof.dicomweb.qido_default_media_type == "application/json"
    assert prof.dicomweb.default_transfer_syntax == "1.2.840.10008.1.2.1"
    assert prof.dicomweb.auth_schemes == ["Negotiate", "NTLM", "Basic"]


def test_profile_can_override_timeouts(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: dicom\n"
        "dicom:\n  acse_timeout: 5\n  network_timeout: null\n  dimse_timeout: 8\n"
    )
    prof = load_profile(str(custom))
    assert prof.dicom.acse_timeout == 5
    assert (
        prof.dicom.network_timeout is None
    )  # explicit null -> pynetdicom's own default, not the fallback
    assert prof.dicom.dimse_timeout == 8


@pytest.mark.parametrize("value", [".inf", ".nan", "0", "-1"])
def test_profile_rejects_non_finite_or_non_positive_timeouts(tmp_path, value):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: dicom\n" f"dicom:\n  dimse_timeout: {value}\n"
    )
    with pytest.raises(ValueError, match="dimse_timeout.*positive"):
        load_profile(str(custom))


def test_profile_allows_null_dimse_timeout(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: dicom\ndicom:\n  dimse_timeout: null\n"
    )
    assert load_profile(str(custom)).dicom.dimse_timeout is None


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
    sparse = tmp_path / "sparse.yaml"
    sparse.write_text(
        "meta:\n  name: sparse\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: fujifilm\n"
    )

    prof = load_profile(str(sparse))

    assert prof.web.enabled is True
    assert (
        prof.web.content_security_policy
    )  # not None -> _spoof() won't crash on .replace()
    assert prof.web.identity["version"]  # not {} -> _login_context() won't KeyError
    assert prof.web.license["lines"] == []  # present key, empty is fine, missing is not
    assert prof.web.oidc["redirect_path"]  # not {} -> _winlogin_url() won't KeyError


def test_web_enabled_without_templates_dir_raises():
    import yaml
    from profiles.profile import _parse_profile

    data = yaml.safe_load(
        "meta:\n  name: notemplates\n  kind: pacs\nweb:\n  enabled: true\n"
    )
    with pytest.raises(ValueError, match="templates_dir"):
        _parse_profile(data)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[]\n", "top-level mapping"),
        ("dicom:\n  operations: [echo, shell]\n", "unknown DICOM operations"),
        ("dicom:\n  max_associations: {}\n", "must be numeric"),
        ("dicom:\n  max_store_bytes: 0\n", "must be positive"),
        ("web:\n  enabled: 'yes'\n", "web.enabled"),
        ("web:\n  upload_max_files: 0\n", "upload_max_files"),
        ("web:\n  browse_page_size: 501\n", "browse_page_size"),
        ("web:\n  honeytraps: [broken]\n", "must be a mapping"),
        ("web:\n  headers:\n    Server: 10\n", "single-line strings"),
        ("identity:\n  implementation_version_name: this-is-far-too-long\n", "1-16"),
        (
            "dicomweb:\n  enabled: true\n  services:\n"
            "    - {service: qido, base_path: //bad, port: 8042}\n",
            "absolute URL path",
        ),
        (
            "dicomweb:\n  auth_schemes: [Basic, Digest]\n",
            "unsupported scheme",
        ),
        (
            "dicomweb:\n  default_transfer_syntax: 1.2.3\n",
            "transfer syntax UID",
        ),
        (
            "dicomweb:\n  max_stow_parts: 0\n",
            "limits must be positive",
        ),
    ],
)
def test_profile_rejects_malformed_or_dangerous_values(text, match):
    import yaml
    from profiles.profile import _parse_profile

    with pytest.raises(ValueError, match=match):
        _parse_profile(yaml.safe_load(text))


def test_profile_allows_explicitly_disabling_csp(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "meta:\n  name: custom\n  kind: pacs\n"
        "web:\n  enabled: true\n  templates_dir: generic-pacs\n  content_security_policy: null\n"
    )
    assert load_profile(str(custom)).web.content_security_policy is None


# --- worklist shell config ---


def test_default_worklist_config_is_vendor_neutral():
    """A sparse profile must never inherit another vendor's folder or menu names."""
    worklist = default_profile().web.worklist

    assert worklist["sidebar"] == []
    assert worklist["context_menu"] == []
    assert worklist["header_links"] == []
    assert not any(
        token in repr(worklist).lower()
        for token in ("synapse", "fujifilm", "workflowui")
    )


def test_default_worklist_placeholders_match_the_historical_literals():
    # generic-pacs renders these, so changing them would silently alter its page.
    assert default_profile().web.worklist["placeholders"] == {
        "description": "—",
        "status": "Unread",
        "empty": "—",
    }


def test_generic_pacs_inherits_the_default_worklist():
    assert load_profile("generic-pacs").web.worklist == default_profile().web.worklist
    assert load_profile("generic-pacs").web.worklist_page_size == 100


def test_fujifilm_worklist_config_is_parsed():
    worklist = load_profile("fujifilm").web.worklist
    sections = [section["label"] for section in worklist["sidebar"]]
    folders = [
        item["label"]
        for section in worklist["sidebar"]
        for item in section.get("items", [])
    ]

    assert worklist["title"] == "All Studies with Images"
    assert worklist["placeholders"]["description"] == "UNKNOWN"
    assert "Global Worklists" in sections
    assert "Public Collections" in sections
    assert "All Studies Global" in folders
    assert {"label": "Study Information", "result": "detail"} in worklist[
        "context_menu"
    ]


def test_fujifilm_worklist_ships_no_sidebar_counts():
    # A static count beside a live study table is a cheap tell; operators opt in instead.
    worklist = load_profile("fujifilm").web.worklist

    assert not any(
        "count" in item
        for section in worklist["sidebar"]
        for item in section.get("items", [])
    )


@pytest.mark.parametrize(
    "worklist,match",
    [
        ({"columns": [{"key": "nope", "label": "X"}]}, "columns\\[0\\].key"),
        ({"columns": []}, "must be a non-empty list"),
        ({"columns": [{"key": "patient_name"}]}, "missing required key 'label'"),
        (
            {"sidebar": [{"label": "S", "items": [{"label": "F", "count": "many"}]}]},
            "count' must be a non-negative integer",
        ),
        (
            {"sidebar": [{"label": "S", "items": [{"label": "F", "count": True}]}]},
            "count' must be a non-negative integer",
        ),
        (
            {
                "sidebar": [
                    {
                        "label": "S",
                        "items": [{"label": "F", "filter": {"patient_id": "1"}}],
                    }
                ]
            },
            "filter keys: patient_id",
        ),
        (
            {"context_menu": [{"label": "M", "result": "launch"}]},
            "context_menu\\[0\\].result",
        ),
        ({"title": 42}, "title' must be a string"),
        ({"header_links": "Home"}, "header_links' must be a list of strings"),
        ({"messages": {"action_failed": 7}}, "action_failed' must be a string"),
    ],
)
def test_profile_rejects_a_malformed_worklist(worklist, match):
    from profiles.profile import _parse_profile

    with pytest.raises(ValueError, match=match):
        _parse_profile(
            {
                "meta": {"name": "t", "kind": "pacs"},
                "web": {"enabled": False, "worklist": worklist},
            }
        )


@pytest.mark.parametrize("size", [0, 501])
def test_profile_rejects_an_out_of_range_worklist_page_size(size):
    from profiles.profile import _parse_profile

    with pytest.raises(ValueError, match="web.worklist_page_size' must be 1-500"):
        _parse_profile(
            {
                "meta": {"name": "t", "kind": "pacs"},
                "web": {"enabled": False, "worklist_page_size": size},
            }
        )
