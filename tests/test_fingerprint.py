import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dicomhawk.bus import InteractionEvent
from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from fingerprint.component import new_fingerprint_component
from fingerprint.config import new_fingerprint_config
from fingerprint.signals import evaluate, sanitize, stable_hash
from profiles.profile import load_profile
from web.app import new_web
from web.operator_api import new_operator_api

CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"


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
def component(tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(db_path=str(tmp_path / "fingerprint.db"))
    )
    comp.start()
    yield comp
    comp.stop()


def _payload(**signals):
    return json.dumps(
        {"v": 1, "signals": {k: {"value": v} for k, v in signals.items()}}
    )


def _event(caplog, request_type):
    """Other loggers emit plain strings into caplog, so only interaction events are considered."""
    return next(
        record.msg
        for record in caplog.records
        if isinstance(record.msg, InteractionEvent)
        and record.msg.request_type == request_type
    )


def _client(profile, repo, bus, sink=None):
    app = new_web(profile, repo, bus, None, sink)
    app.config["TESTING"] = True
    return app.test_client()


def test_source_address_cap_cannot_be_bypassed_by_rotating_sessions(tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(
            db_path=str(tmp_path / "fingerprint.db"), max_per_session=2, max_per_ip=3
        )
    )
    comp.start()
    try:
        stored = [
            comp.sink(
                _payload(platform="Linux").encode(),
                session_id=f"web-signin-{index}",
                ip="203.0.113.10",
                local_port=8080,
                path="/portal/telemetry",
                user_agent=CHROME,
            )
            for index in range(8)
        ]
        # One submission per session, so only the source-address cap can stop this.
        assert sum(value is not None for value in stored) == 3
    finally:
        comp.stop()


def test_source_address_cap_is_looser_than_the_session_cap(tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(
            db_path=str(tmp_path / "fingerprint.db"), max_per_session=2, max_per_ip=6
        )
    )
    comp.start()
    try:
        # A returning visitor on one address is the traffic worth keeping, so rotating
        # sessions must stay collectable well past a single session's cap.
        stored = [
            comp.sink(
                _payload(platform="Linux").encode(),
                session_id=f"web-signin-{index}",
                ip="203.0.113.11",
                local_port=8080,
                path="/portal/telemetry",
                user_agent=CHROME,
            )
            for index in range(5)
        ]
        assert sum(value is not None for value in stored) == 5
    finally:
        comp.stop()


def test_concurrent_submissions_cannot_overrun_the_source_address_cap(tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(
            db_path=str(tmp_path / "fingerprint.db"),
            max_per_session=100,
            max_per_ip=3,
        )
    )
    comp.start()
    try:

        def submit(index):
            return comp.sink(
                _payload(platform="Linux").encode(),
                session_id=f"web-{index}",
                ip="203.0.113.12",
                local_port=8080,
                path="/portal/telemetry",
                user_agent=CHROME,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            stored = list(executor.map(submit, range(20)))

        assert sum(value is not None for value in stored) == 3
        assert comp.store.ip_count("203.0.113.12") == 3
    finally:
        comp.stop()


# --- profile schema and fallbacks ---


def test_both_shipped_profiles_opt_in():
    for name in ("fujifilm", "generic-pacs"):
        assert load_profile(name).web.fingerprint.enabled is True


def test_sparse_profile_inherits_generic_routes_and_all_signals():
    generic = load_profile("generic-pacs")
    assert generic.web.routes["fingerprint_script"] == "/portal/static/telemetry.js"
    assert generic.web.routes["fingerprint_ingest"] == "/portal/telemetry"
    assert sorted(generic.web.fingerprint.signals) == [
        "bot",
        "browser",
        "math",
        "rendering",
        "screen",
    ]


def test_empty_signal_list_disables_collection(tmp_path):
    profile = tmp_path / "p.yaml"
    profile.write_text(
        "meta: {name: p, kind: pacs}\n"
        "web: {enabled: true, templates_dir: generic-pacs,"
        " fingerprint: {enabled: true, signals: []}}\n"
    )
    loaded = load_profile(str(profile))
    assert loaded.web.fingerprint.enabled is False


def test_unknown_signal_name_fails_fast(tmp_path):
    profile = tmp_path / "p.yaml"
    profile.write_text(
        "meta: {name: p, kind: pacs}\n"
        "web: {enabled: true, templates_dir: generic-pacs,"
        " fingerprint: {enabled: true, signals: [math, telepathy]}}\n"
    )
    with pytest.raises(ValueError, match="telepathy"):
        load_profile(str(profile))


def test_generic_profile_never_serves_synapse_fingerprint_paths(repo, bus):
    fujifilm = load_profile("fujifilm")
    generic = load_profile("generic-pacs")
    client = _client(generic, repo, bus)

    body = client.get(generic.web.routes["login"] + "?signin=abc").get_data(
        as_text=True
    )
    assert fujifilm.web.routes["fingerprint_script"] not in body
    assert fujifilm.web.routes["fingerprint_ingest"] not in body
    assert client.get(fujifilm.web.routes["fingerprint_script"]).status_code == 404


# --- seam and routing ---


def test_seam_supplies_every_attribute_the_collector_reads(repo, bus):
    """The collector bails silently on a missing attribute, so the contract needs asserting."""
    collector = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "fingerprint"
        / "static"
        / "collector.js"
    ).read_text()
    required = set(re.findall(r"getAttribute\('(data-[\w-]+)'\)", collector))
    assert required, "collector.js exposes no data-* contract to assert against"

    for name in ("fujifilm", "generic-pacs"):
        profile = load_profile(name)
        body = (
            _client(profile, repo, bus)
            .get(profile.web.routes["login"] + "?signin=abc")
            .get_data(as_text=True)
        )
        tag = re.search(
            r"<script[^>]*collector[^>]*>|<script[^>]*data-signals[^>]*>", body
        )
        assert tag, f"{name} rendered no collector script tag"
        for attribute in required:
            assert f'{attribute}="' in tag.group(
                0
            ), f"{name} seam is missing {attribute}"


def test_seam_ingest_attribute_points_at_the_registered_route(repo, bus):
    profile = load_profile("fujifilm")
    client = _client(profile, repo, bus)
    body = client.get(profile.web.routes["login"] + "?signin=abc").get_data(
        as_text=True
    )

    ingest = re.search(r'data-ingest="([^"]+)"', body).group(1)
    assert ingest == profile.web.routes["fingerprint_ingest"]
    # The advertised path must actually accept a POST, not 404 or 405.
    assert client.post(ingest, data=_payload(platform="Linux")).status_code == 204


def test_seam_lists_only_enabled_categories(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.fingerprint.signals = ["math", "bot"]
    client = _client(profile, repo, bus)

    body = client.get(profile.web.routes["login"] + "?signin=abc").get_data(
        as_text=True
    )
    assert 'data-signals="math,bot"' in body


def test_disabled_profile_has_no_collector_and_no_ingest(repo, bus):
    profile = load_profile("generic-pacs")
    profile.web.fingerprint.enabled = False
    client = _client(profile, repo, bus)

    assert client.get(profile.web.routes["fingerprint_script"]).status_code == 404
    ingest = client.post(profile.web.routes["fingerprint_ingest"], data=_payload())
    assert ingest.status_code == 404
    # The 404 is the profile's own scan page, not a Werkzeug default.
    assert ingest.get_data(as_text=True) == "404 - Not Found"


def test_cli_override_removes_the_collector_and_the_route(repo, bus):
    """--no-fingerprint clears the profile flag, which must strip the surface, not just the store."""
    profile = load_profile("fujifilm")
    profile.web.fingerprint.enabled = False
    client = _client(profile, repo, bus)

    assert client.get(profile.web.routes["fingerprint_script"]).status_code == 404
    assert (
        client.post(profile.web.routes["fingerprint_ingest"], data="{}").status_code
        == 404
    )
    body = client.get(profile.web.routes["login"] + "?signin=abc").get_data(
        as_text=True
    )
    assert "data-signals" not in body


def test_unopenable_store_disables_the_feature_without_killing_the_process(tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(db_path=str(tmp_path / "missing" / "\0bad" / "f.db"))
    )
    comp.start()  # must not raise: an optional feature cannot take the honeypot down
    assert comp.store.ready() is False
    assert (
        comp.sink(
            b'{"signals":{"platform":{"value":"L"}}}',
            session_id="s",
            ip=None,
            local_port=None,
            path=None,
            user_agent=None,
        )
        is None
    )
    assert comp.store.list_fingerprints() == ([], 0)
    comp.stop()


def test_operator_api_survives_a_store_that_never_opened(bus, tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(db_path=str(tmp_path / "missing" / "\0bad" / "f.db"))
    )
    comp.start()
    operator = new_operator_api(
        load_profile("generic-pacs"), bus, None, None, comp.store
    ).test_client()

    response = operator.get("/api/fingerprints")
    assert response.status_code == 200
    assert response.get_json() == []
    comp.stop()


def test_collector_is_served_from_the_package_not_per_profile(repo, bus):
    served = set()
    for name in ("fujifilm", "generic-pacs"):
        profile = load_profile(name)
        response = _client(profile, repo, bus).get(
            profile.web.routes["fingerprint_script"]
        )
        assert response.status_code == 200
        assert "javascript" in response.headers["Content-Type"]
        served.add(response.get_data())
    assert len(served) == 1


# --- ingest behaviour ---


def test_submission_is_stored_and_hash_reaches_the_log(repo, bus, component, caplog):
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, component.sink)

    with caplog.at_level(logging.INFO, logger="bus"):
        response = client.post(
            profile.web.routes["fingerprint_ingest"],
            data=_payload(userAgent=CHROME, platform="Linux x86_64"),
            content_type="application/json",
            headers={"User-Agent": CHROME},
        )
    assert response.status_code == 204

    rows, total = component.store.list_fingerprints()
    assert total == 1
    event = _event(caplog, "WEB_FINGERPRINT")
    assert event.fingerprint_hash == rows[0].fingerprint_hash
    assert json.loads(str(event))["fingerprint_hash"] == rows[0].fingerprint_hash


def test_event_schema_carries_null_fingerprint_hash_without_the_feature(
    repo, bus, caplog
):
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus)

    with caplog.at_level(logging.INFO, logger="bus"):
        client.get(profile.web.routes["login"] + "?signin=abc")
        client.get("/nope")
    assert json.loads(str(_event(caplog, "WEB_404")))["fingerprint_hash"] is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json at all",
        b"[]",
        b'{"signals": "a string"}',
        b'{"signals": {"platform": "not a dict"}}',
        json.dumps({"signals": {"evil": {"value": "x"}}}).encode(),
        json.dumps({"signals": {"platform": {"value": "A" * 200_000}}}).encode(),
        b"\xff\xfe\x00binary",
    ],
)
def test_hostile_payloads_never_produce_a_server_error(repo, bus, component, body):
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, component.sink)

    response = client.post(profile.web.routes["fingerprint_ingest"], data=body)
    assert response.status_code == 204


def test_oversized_body_is_dropped_by_the_package_cap(repo, bus, tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(db_path=str(tmp_path / "f.db"), max_body_bytes=256)
    )
    comp.start()
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, comp.sink)

    response = client.post(
        profile.web.routes["fingerprint_ingest"],
        data=_payload(userAgent=CHROME, platform="L" * 4096),
        content_type="application/json",
    )
    assert response.status_code == 204
    assert comp.store.list_fingerprints()[1] == 0
    comp.stop()


def test_a_raising_sink_cannot_change_the_response(repo, bus):
    def exploding_sink(_body, **_kwargs):
        raise RuntimeError("store is down")

    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, exploding_sink)

    response = client.post(
        profile.web.routes["fingerprint_ingest"],
        data=_payload(userAgent=CHROME),
        content_type="application/json",
    )
    assert response.status_code == 204
    assert response.get_data() == b""


def test_store_failure_inside_the_component_is_swallowed(repo, bus, component):
    def explode(**_kwargs):
        raise RuntimeError("disk full")

    component.store.record = explode
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, component.sink)

    response = client.post(
        profile.web.routes["fingerprint_ingest"],
        data=_payload(userAgent=CHROME),
        content_type="application/json",
    )
    assert response.status_code == 204


def test_per_session_cap_bounds_one_attacker(repo, bus, tmp_path):
    comp = new_fingerprint_component(
        new_fingerprint_config(db_path=str(tmp_path / "f.db"), max_per_session=3)
    )
    comp.start()
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, comp.sink)

    for _ in range(10):
        response = client.post(
            profile.web.routes["fingerprint_ingest"] + "?signin=flood",
            data=_payload(userAgent=CHROME),
            content_type="application/json",
        )
        assert response.status_code == 204
    assert comp.store.list_fingerprints()[1] == 3
    comp.stop()


# --- sanitizing and hashing ---


def test_unknown_sources_are_dropped_and_strings_are_capped():
    payload = {
        "signals": {
            "evil": {"value": "drop me"},
            "platform": {"value": "A" * 100_000},
            "webdriver": "not a dict",
        }
    }
    signals, errors = sanitize(payload, 512)
    assert sorted(signals) == ["platform"]
    assert len(signals["platform"]["value"]) == 512
    assert errors == 0


def test_nesting_is_bounded():
    payload = {"signals": {"canvas": {"value": {"a": {"b": {"c": {"d": {"e": 1}}}}}}}}
    signals, _ = sanitize(payload, 512)
    assert signals["canvas"]["value"] == {"a": {"b": {"c": {"d": None}}}}


def test_failed_sources_are_counted_but_excluded_from_the_hash():
    with_error, errors = sanitize(
        {"signals": {"platform": {"value": "Linux"}, "canvas": {"error": "blocked"}}},
        512,
    )
    without, _ = sanitize({"signals": {"platform": {"value": "Linux"}}}, 512)
    assert errors == 1
    assert stable_hash(with_error) == stable_hash(without)


def test_hash_changes_when_a_signal_changes():
    a, _ = sanitize({"signals": {"platform": {"value": "Linux"}}}, 512)
    b, _ = sanitize({"signals": {"platform": {"value": "Win32"}}}, 512)
    assert stable_hash(a) != stable_hash(b)


# --- ported bot checks ---


def _chrome_signals(**extra):
    """A genuine desktop Chrome: the engine is probed, the browser kind claimed."""
    base = {
        "userAgent": {"value": CHROME},
        "browserEngineKind": {"value": "Chromium"},
        "browserKind": {"value": "Chrome"},
        "android": {"value": False},
        "documentFocus": {"value": True},
        "productSub": {"value": "20030107"},
        "evalLength": {"value": 33},
        "pluginsLength": {"value": 5},
        "webdriver": {"value": False},
    }
    base.update({k: {"value": v} for k, v in extra.items()})
    return sanitize({"signals": base}, 512)[0]


def test_ordinary_browser_fires_nothing():
    checks, verdict = evaluate(_chrome_signals(), CHROME)
    assert checks == []
    assert verdict is None


def test_spoofed_user_agent_is_caught_by_probed_engine():
    # Claims Chrome in the User-Agent, but the feature probe says Gecko, so the UA is a lie.
    signals, _ = sanitize(
        {
            "signals": {
                "userAgent": {"value": CHROME},
                "browserKind": {"value": "Chrome"},
                "browserEngineKind": {"value": "Gecko"},
                "productSub": {"value": "20100101"},
                "evalLength": {"value": 33},
            }
        },
        512,
    )
    fired = {c["check"] for c in evaluate(signals, CHROME)[0]}
    assert "product_sub" in fired and "eval_length" in fired


def test_genuine_firefox_is_not_flagged():
    signals, _ = sanitize(
        {
            "signals": {
                "userAgent": {"value": FIREFOX},
                "browserKind": {"value": "Firefox"},
                "browserEngineKind": {"value": "Gecko"},
                "evalLength": {"value": 37},
                "productSub": {"value": "20100101"},
            }
        },
        512,
    )
    assert evaluate(signals, FIREFOX)[0] == []


def test_unknown_eval_length_is_not_flagged():
    # Upstream only treats 33/37/39 as known values; anything else must not fire.
    assert evaluate(_chrome_signals(evalLength=51), CHROME)[0] == []


def test_android_chrome_is_not_flagged_for_zero_plugins_or_zero_rtt():
    signals = _chrome_signals(android=True, pluginsLength=0, rtt=0)
    assert evaluate(signals, CHROME)[0] == []


def test_unfocused_tab_is_not_flagged_for_zero_window_size():
    signals = _chrome_signals(
        documentFocus=False, windowSize={"outerWidth": 0, "outerHeight": 0}
    )
    assert evaluate(signals, CHROME)[0] == []


def test_missing_function_bind_is_read_as_a_failed_source():
    signals, _ = sanitize(
        {
            "signals": {
                "userAgent": {"value": CHROME},
                "functionBind": {"error": "Function.prototype.bind is undefined"},
            }
        },
        512,
    )
    checks, verdict = evaluate(signals, CHROME)
    assert {c["check"] for c in checks} == {"function_bind"}
    assert verdict == "PhantomJS"


@pytest.mark.parametrize(
    "source,value,check",
    [
        ("webdriver", True, "webdriver"),
        ("rtt", 0, "rtt"),
        ("mimeTypesConsistent", False, "mime_types"),
        ("notificationPermissions", True, "notification_permissions"),
        ("pluginsLength", 0, "plugins_length"),
        ("languages", [], "languages"),
        ("documentElementKeys", ["lang", "webdriver"], "document_element_keys"),
        ("errorTrace", "at PhantomJS://x", "error_trace"),
        ("windowExternal", "Sequentum agent", "window_external"),
        ("appVersion", "5.0 HeadlessChrome/120", "app_version"),
        ("windowSize", {"outerWidth": 0, "outerHeight": 0}, "window_size"),
    ],
)
def test_each_bot_marker_fires(source, value, check):
    checks, verdict = evaluate(_chrome_signals(**{source: value}), CHROME)
    assert check in {c["check"] for c in checks}
    assert verdict is not None


def test_mesa_offscreen_renderer_is_flagged():
    signals = _chrome_signals(
        webgl={"vendor": "Brian Paul", "renderer": "Mesa OffScreen"}
    )
    assert evaluate(signals, CHROME)[1] == "HeadlessChrome"


def test_distinctive_property_names_the_specific_bot():
    signals = _chrome_signals(distinctiveProps={"Selenium": True, "PhantomJS": False})
    assert evaluate(signals, CHROME)[1] == "Selenium"


# --- operator API ---


def test_operator_api_lists_and_filters_fingerprints(repo, bus, component):
    profile = load_profile("generic-pacs")
    client = _client(profile, repo, bus, component.sink)
    client.post(
        profile.web.routes["fingerprint_ingest"],
        data=_payload(userAgent=CHROME, webdriver=True),
        content_type="application/json",
        headers={"User-Agent": CHROME},
    )
    operator = new_operator_api(profile, bus, None, None, component.store).test_client()

    listed = operator.get("/api/fingerprints")
    assert listed.status_code == 200
    assert listed.headers["X-Total-Count"] == "1"
    record = listed.get_json()[0]
    assert record["bot_verdict"] == "WebDriver"
    assert record["signals"]["userAgent"]["value"] == CHROME

    assert (
        operator.get("/api/fingerprints?verdict=WebDriver").headers["X-Total-Count"]
        == "1"
    )
    assert (
        operator.get("/api/fingerprints?verdict=Electron").headers["X-Total-Count"]
        == "0"
    )
    by_hash = operator.get(f"/api/fingerprints?hash={record['fingerprint_hash']}")
    assert by_hash.headers["X-Total-Count"] == "1"


def test_operator_api_returns_empty_without_a_store(bus):
    profile = load_profile("generic-pacs")
    operator = new_operator_api(profile, bus, None, None, None).test_client()
    assert operator.get("/api/fingerprints").get_json() == []
