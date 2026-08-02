"""Sanitizing, hashing and bot evaluation for collector submissions.
They run server-side so a tampered client cannot forge the verdict.
"""

import hashlib
import json
import re

# Source names the collector may submit; anything else is dropped rather than stored.
_SOURCES: frozenset[str] = frozenset(
    {
        "platform",
        "languages",
        "timezone",
        "hardwareConcurrency",
        "vendor",
        "osCpu",
        "deviceMemory",
        "userAgent",
        "appVersion",
        "productSub",
        "browserEngineKind",
        "browserKind",
        "android",
        "documentFocus",
        "canvas",
        "webgl",
        "math",
        "screenResolution",
        "colorDepth",
        "devicePixelRatio",
        "windowSize",
        "webdriver",
        "evalLength",
        "functionBind",
        "windowExternal",
        "errorTrace",
        "process",
        "documentElementKeys",
        "pluginsLength",
        "mimeTypesConsistent",
        "notificationPermissions",
        "rtt",
        "distinctiveProps",
    }
)
_MAX_SOURCES = 64
_MAX_ITEMS = 64  # per list/dict, applied at every depth
_MAX_DEPTH = 4

_AUTOMATION_UA = (
    (re.compile(r"phantomjs", re.I), "PhantomJS"),
    (re.compile(r"headless", re.I), "HeadlessChrome"),
    (re.compile(r"electron", re.I), "Electron"),
    (re.compile(r"slimerjs", re.I), "SlimerJS"),
)
_DRIVER_ATTRIBUTES = ("selenium", "webdriver", "driver")


def _bound(value, max_chars: int, depth: int = 0):
    """Recursively bound one attacker-supplied value by type, length, size and depth."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:max_chars]
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(value, list):
        return [_bound(item, max_chars, depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        return {
            str(key)[:max_chars]: _bound(item, max_chars, depth + 1)
            for key, item in list(value.items())[:_MAX_ITEMS]
        }
    return None


def sanitize(payload: dict, max_chars: int) -> tuple[dict, int]:
    """Return (known bounded sources, count of sources the collector reported as failed)."""
    if not isinstance(payload, dict):
        return {}, 0
    raw = payload.get("signals")
    if not isinstance(raw, dict):
        return {}, 0
    signals: dict = {}
    errors = 0
    for name, entry in list(raw.items())[:_MAX_SOURCES]:
        if name not in _SOURCES or not isinstance(entry, dict):
            continue
        if "error" in entry:
            errors += 1
            signals[name] = {"error": _bound(entry["error"], max_chars)}
        elif "value" in entry:
            signals[name] = {"value": _bound(entry["value"], max_chars)}
    return signals, errors


def stable_hash(signals: dict) -> str:
    """SHA-256 over the value-bearing components; failed sources are excluded so they can't shift identity."""
    values = {
        name: entry["value"] for name, entry in sorted(signals.items()) if "value" in entry
    }
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


def _value(signals: dict, name: str):
    entry = signals.get(name)
    return entry.get("value") if isinstance(entry, dict) and "value" in entry else None


def _browser_kind(user_agent: str) -> str:
    ua = user_agent.lower()
    if "edg/" in ua:
        return "Edge"
    if "trident" in ua or "msie" in ua:
        return "IE"
    if "wechat" in ua:
        return "WeChat"
    if "firefox" in ua:
        return "Firefox"
    if "opera" in ua or "opr" in ua:
        return "Opera"
    if "chrome" in ua:
        return "Chrome"
    if "safari" in ua:
        return "Safari"
    return "Unknown"


def evaluate(signals: dict, header_user_agent: str | None) -> tuple[list[dict], str | None]:
    """Run the inconsistency checks; returns every check that fired plus the leading verdict."""
    claimed_ua = str(_value(signals, "userAgent") or header_user_agent or "")
    # Engine comes from the collector's feature probing; browser kind from the User-Agent it claims.
    engine = _value(signals, "browserEngineKind")
    browser = _value(signals, "browserKind") or _browser_kind(claimed_ua)
    android = _value(signals, "android")
    checks: list[dict] = []

    def fired(name: str, bot: str, detail: str) -> None:
        checks.append({"check": name, "bot": bot, "detail": detail})

    if _value(signals, "webdriver") is True:
        fired("webdriver", "WebDriver", "navigator.webdriver is true")

    for pattern, bot in _AUTOMATION_UA:
        if pattern.search(claimed_ua):
            fired("user_agent", bot, f"User-Agent matches {bot}")
        app_version = _value(signals, "appVersion")
        if isinstance(app_version, str) and pattern.search(app_version):
            fired("app_version", bot, f"navigator.appVersion matches {bot}")

    trace = _value(signals, "errorTrace")
    if isinstance(trace, str) and re.search(r"phantomjs", trace, re.I):
        fired("error_trace", "PhantomJS", "PhantomJS frame in the error stack")

    product_sub = _value(signals, "productSub")
    if browser in ("Chrome", "Safari", "Opera", "WeChat") and isinstance(product_sub, str):
        if product_sub != "20030107":
            fired(
                "product_sub",
                "Unknown",
                f"navigator.productSub is {product_sub}, expected 20030107",
            )

    # Only these three lengths are known values; each is suspicious solely in the wrong engine.
    eval_length = _value(signals, "evalLength")
    if isinstance(eval_length, int) and engine and engine != "Unknown":
        if (
            (eval_length == 37 and engine not in ("Webkit", "Gecko"))
            or (eval_length == 39 and browser != "IE")
            or (eval_length == 33 and engine != "Chromium")
        ):
            fired(
                "eval_length",
                "Unknown",
                f"eval.toString().length is {eval_length} on a {engine} engine",
            )

    # Upstream signals this as a missing API, which arrives here as a failed source, not False.
    if isinstance(signals.get("functionBind"), dict) and "error" in signals["functionBind"]:
        fired("function_bind", "PhantomJS", "Function.prototype.bind is missing")

    external = _value(signals, "windowExternal")
    if isinstance(external, str) and re.search(r"sequentum", external, re.I):
        fired("window_external", "Sequentum", "window.external names Sequentum")

    process = _value(signals, "process")
    if isinstance(process, dict):
        versions = process.get("versions")
        if process.get("type") == "renderer" or (
            isinstance(versions, dict) and versions.get("electron")
        ):
            fired("process", "Electron", "window.process exposes an Electron renderer")

    keys = _value(signals, "documentElementKeys")
    if isinstance(keys, list):
        hits = [k for k in keys if isinstance(k, str) and k.lower() in _DRIVER_ATTRIBUTES]
        if hits:
            fired("document_element_keys", "Selenium", f"documentElement has {hits[0]}")

    props = _value(signals, "distinctiveProps")
    if isinstance(props, dict):
        for bot, present in props.items():
            if present:
                fired("distinctive_properties", str(bot), f"{bot} property present")

    # Desktop Chrome only: Android and other browsers legitimately report no plugins.
    plugins = _value(signals, "pluginsLength")
    if browser == "Chrome" and engine == "Chromium" and android is False and plugins == 0:
        fired("plugins_length", "HeadlessChrome", "desktop Chrome reporting zero plugins")

    languages = _value(signals, "languages")
    if isinstance(languages, list) and not languages:
        fired("languages", "HeadlessChrome", "navigator.languages is empty")

    # A tab opened without focus legitimately reports 0x0, so focus gates this one.
    window_size = _value(signals, "windowSize")
    if isinstance(window_size, dict) and _value(signals, "documentFocus") is True:
        if window_size.get("outerWidth") == 0 and window_size.get("outerHeight") == 0:
            fired("window_size", "HeadlessChrome", "window outer size is 0x0")

    if browser == "Chrome" and _value(signals, "notificationPermissions") is True:
        fired(
            "notification_permissions",
            "HeadlessChrome",
            "Notification denied while the permission query says prompt",
        )

    # rtt is legitimately 0 in an Android WebView.
    if _value(signals, "rtt") == 0 and android is False:
        fired("rtt", "HeadlessChrome", "navigator.connection.rtt is 0")

    if _value(signals, "mimeTypesConsistent") is False:
        fired("mime_types", "Unknown", "navigator.mimeTypes is inconsistent")

    webgl = _value(signals, "webgl")
    if isinstance(webgl, dict):
        if webgl.get("vendor") == "Brian Paul" and webgl.get("renderer") == "Mesa OffScreen":
            fired("webgl", "HeadlessChrome", "Mesa OffScreen software renderer")

    verdict = next(
        (c["bot"] for c in checks if c["bot"] not in ("Unknown", "WebDriver")),
        None,
    )
    return checks, verdict or (checks[0]["bot"] if checks else None)
