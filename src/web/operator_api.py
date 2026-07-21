"""Read-only operator intelligence API and dashboard over the durable interaction log."""

import dataclasses
import secrets
import threading
from datetime import datetime, timezone
from logging import FileHandler, Logger
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

import ujson
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
)

from dicomhawk.bus import recent_events
from profiles.profile import ProfileConfig

bp = Blueprint("operator", __name__)

_CRED_EVENTS = frozenset(
    {
        "WEB_LOGIN_ATTEMPT",
        "WEB_HONEY_CREDENTIAL_USED",
        "WEB_WINAUTH_ATTEMPT",
        "DICOMWEB_AUTH_ATTEMPT",
    }
)
_UPLOAD_EVENTS = frozenset(
    {"WEB_UPLOAD", "DICOMWEB_STOW_PAYLOAD", "DICOMWEB_STOW_REQUEST", "C-STORE"}
)
_MAX_LOG_LINE_BYTES = 1024 * 1024


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_params(evt: dict) -> dict[str, list[str]]:
    """Fold legacy display parameters into lists without losing repeated keys."""
    out: dict[str, list[str]] = {}
    params = evt.get("session_parameters")
    if not isinstance(params, (list, tuple)):
        return out
    for item in params:
        if not isinstance(item, str):
            continue
        key, sep, value = item.partition(": ")
        if sep and key:
            out.setdefault(key, []).append(value)
    return out


def _param_first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _has_credentials(evt: dict) -> bool:
    params = _parse_params(evt)
    return _text(evt.get("request_type")) in _CRED_EVENTS and bool(
        params.get("Password") or params.get("Username")
    )


def _event_paths(bus: Logger) -> list[Path]:
    """Return retained interaction logs oldest first, including non-rotating FileHandler."""
    handler = next((h for h in bus.handlers if isinstance(h, FileHandler)), None)
    if handler is None:
        return []
    base = Path(handler.baseFilename)
    if isinstance(handler, RotatingFileHandler):
        paths = [Path(f"{base}.{index}") for index in range(handler.backupCount, 0, -1)]
    elif isinstance(handler, TimedRotatingFileHandler):
        paths = sorted(base.parent.glob(base.name + ".*"))
    else:
        paths = []
    paths.append(base)
    return [path for path in paths if path.is_file()]


def _signature(paths: list[Path]) -> tuple:
    signature = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append(
            (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        )
    return tuple(signature)


def _valid_event(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    # An interaction record without either discriminator cannot contribute to any view.
    if not _text(value.get("request_type")) and not _text(value.get("channel")):
        return None
    return value


def _read_paths(paths: list[Path]) -> tuple[list[dict], int]:
    events: list[dict] = []
    skipped = 0
    for path in paths:
        try:
            stream = path.open(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        with stream:
            for line in stream:
                if len(line) > _MAX_LOG_LINE_BYTES:
                    skipped += 1
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    event = _valid_event(ujson.loads(line))
                except (TypeError, ValueError):
                    event = None
                if event is None:
                    skipped += 1
                else:
                    events.append(event)
    return events, skipped


def _log_snapshot() -> tuple[list[dict], int]:
    """Read retained logs once per file state; fall back to the bounded live deque."""
    bus: Logger = current_app.config["BUS"]
    paths = _event_paths(bus)
    if not paths:
        handler = current_app.config["EVENTS"]
        if handler is None:
            return [], 0
        events = []
        skipped = 0
        for item in handler.snapshot():
            event = _valid_event(dict(item.__dict__))
            if event is None:
                skipped += 1
            else:
                events.append(event)
        return events, skipped

    signature = _signature(paths)
    lock: threading.Lock = current_app.config["EVENT_CACHE_LOCK"]
    cache = current_app.config["EVENT_CACHE"]
    with lock:
        if cache.get("signature") == signature:
            return cache["events"], cache["skipped"]
        events, skipped = _read_paths(paths)
        cache.update(signature=signature, events=events, skipped=skipped)
        return events, skipped


def _log_events() -> list[dict]:
    """Return validated retained events."""
    return _log_snapshot()[0]


def _legacy_artifact(evt: dict) -> dict | None:
    request_type = _text(evt.get("request_type"))
    if request_type not in _UPLOAD_EVENTS:
        return None
    params = _parse_params(evt)
    raw_bytes = _param_first(params, "Bytes")
    reject_reason = _param_first(params, "Rejected") or _param_first(params, "Error")
    captured = not bool(params.get("Capture failure"))
    if request_type == "C-STORE" and reject_reason:
        captured = False
    return {
        "filename": _param_first(params, "File"),
        "bytes": int(raw_bytes) if raw_bytes and raw_bytes.isdigit() else None,
        "sha256": _param_first(params, "SHA256"),
        "sop_instance_uid": _param_first(params, "SOPInstanceUID"),
        "sop_class_uid": _param_first(params, "SOPClassUID"),
        "captured": captured,
        "disposition": "rejected" if reject_reason else "stored",
        "reject_reason": reject_reason,
    }


def _artifact(evt: dict) -> dict | None:
    if _text(evt.get("request_type")) not in _UPLOAD_EVENTS:
        return None
    artifact = evt.get("artifact")
    if not isinstance(artifact, dict):
        return _legacy_artifact(evt)
    raw_bytes = artifact.get("bytes")
    return {
        "filename": _text(artifact.get("filename")),
        "bytes": raw_bytes if isinstance(raw_bytes, int) and raw_bytes >= 0 else None,
        "sha256": _text(artifact.get("sha256")),
        "sop_instance_uid": _text(artifact.get("sop_instance_uid")),
        "sop_class_uid": _text(artifact.get("sop_class_uid")),
        "captured": artifact.get("captured") is True,
        "disposition": _text(artifact.get("disposition")) or "unknown",
        "reject_reason": _text(artifact.get("reject_reason")),
    }


def extract_uploads(events: list[dict]) -> list[dict]:
    out = []
    for event in events:
        if not isinstance(event, dict) or (artifact := _artifact(event)) is None:
            continue
        out.append(
            {
                "channel": _text(event.get("channel")),
                "ip": _text(event.get("ip")),
                "timestamp": _text(event.get("timestamp")),
                "request_type": _text(event.get("request_type")),
                **artifact,
            }
        )
    out.sort(
        key=lambda upload: (upload.get("timestamp") or "", upload.get("sha256") or ""),
        reverse=True,
    )
    return out


def summarize(events: list[dict], *, skipped_records: int = 0) -> dict:
    by_channel: dict[str, int] = {}
    by_request_type: dict[str, int] = {}
    ips: set[str] = set()
    credentials_captured = 0
    credential_pairs: set[tuple[str, str]] = set()
    stamps = []
    for event in events:
        if not isinstance(event, dict):
            skipped_records += 1
            continue
        channel = _text(event.get("channel")) or "UNKNOWN"
        request_type = _text(event.get("request_type")) or "UNKNOWN"
        by_channel[channel] = by_channel.get(channel, 0) + 1
        by_request_type[request_type] = by_request_type.get(request_type, 0) + 1
        if ip := _text(event.get("ip")):
            ips.add(ip)
        if _has_credentials(event):
            credentials_captured += 1
            params = _parse_params(event)
            credential_pairs.add(
                (
                    _param_first(params, "Username") or "",
                    _param_first(params, "Password") or "",
                )
            )
        if timestamp := _text(event.get("timestamp")):
            stamps.append(timestamp)
    uploads = extract_uploads(events)
    return {
        "total_events": len(events),
        "by_channel": by_channel,
        "by_request_type": by_request_type,
        "unique_source_ips": len(ips),
        "credentials_captured": credentials_captured,
        "unique_credentials": len(credential_pairs),
        "upload_attempts": len(uploads),
        "uploads_captured": sum(upload["captured"] for upload in uploads),
        "uploads_rejected": sum(
            upload["disposition"] == "rejected" for upload in uploads
        ),
        "first_event": min(stamps) if stamps else None,
        "last_event": max(stamps) if stamps else None,
        "skipped_records": skipped_records,
    }


def group_attackers(events: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict) or not (ip := _text(event.get("ip"))):
            continue
        attacker = agg.get(ip)
        if attacker is None:
            attacker = agg[ip] = {
                "ip": ip,
                "events": 0,
                "channels": set(),
                "request_types": set(),
                "credentials_tried": 0,
                "uploads": 0,
                "uploads_captured": 0,
                "first_seen": None,
                "last_seen": None,
            }
        attacker["events"] += 1
        if channel := _text(event.get("channel")):
            attacker["channels"].add(channel)
        if request_type := _text(event.get("request_type")):
            attacker["request_types"].add(request_type)
        if _has_credentials(event):
            attacker["credentials_tried"] += 1
        if artifact := _artifact(event):
            attacker["uploads"] += 1
            attacker["uploads_captured"] += int(artifact["captured"])
        timestamp = _text(event.get("timestamp"))
        if timestamp:
            attacker["first_seen"] = (
                timestamp
                if attacker["first_seen"] is None
                else min(attacker["first_seen"], timestamp)
            )
            attacker["last_seen"] = (
                timestamp
                if attacker["last_seen"] is None
                else max(attacker["last_seen"], timestamp)
            )
    out = []
    for attacker in agg.values():
        attacker["channels"] = sorted(attacker["channels"])
        attacker["request_types"] = sorted(attacker["request_types"])
        tactics = []
        if attacker["events"] > attacker["credentials_tried"] + attacker["uploads"]:
            tactics.append("reconnaissance")
        if attacker["credentials_tried"]:
            tactics.append("credential-access")
        if attacker["uploads"]:
            tactics.append("storage-abuse")
        attacker["tactics"] = tactics or ["reconnaissance"]
        if attacker["uploads"]:
            attacker["classification"] = "storage-abuse"
        elif attacker["credentials_tried"]:
            attacker["classification"] = "credential-access"
        else:
            attacker["classification"] = "reconnaissance"
        out.append(attacker)
    out.sort(key=lambda attacker: (-attacker["events"], attacker["ip"]))
    return out


def extract_credentials(events: list[dict], honey_credentials=()) -> list[dict]:
    honey = {tuple(pair) for pair in honey_credentials}
    agg: dict[tuple[str, str], dict] = {}
    for event in events:
        if not isinstance(event, dict) or not _has_credentials(event):
            continue
        params = _parse_params(event)
        key = (
            _param_first(params, "Username") or "",
            _param_first(params, "Password") or "",
        )
        credential = agg.get(key)
        if credential is None:
            credential = agg[key] = {
                "username": key[0],
                "password": key[1],
                "count": 0,
                "source_ips": set(),
                "channels": set(),
                "honey_hit": key in honey,
                "first_seen": None,
                "last_seen": None,
            }
        credential["count"] += 1
        if ip := _text(event.get("ip")):
            credential["source_ips"].add(ip)
        if channel := _text(event.get("channel")):
            credential["channels"].add(channel)
        if event.get("request_type") == "WEB_HONEY_CREDENTIAL_USED":
            credential["honey_hit"] = True
        timestamp = _text(event.get("timestamp"))
        if timestamp:
            credential["first_seen"] = (
                timestamp
                if credential["first_seen"] is None
                else min(credential["first_seen"], timestamp)
            )
            credential["last_seen"] = (
                timestamp
                if credential["last_seen"] is None
                else max(credential["last_seen"], timestamp)
            )
    out = []
    for credential in agg.values():
        credential["source_ips"] = sorted(credential["source_ips"])
        credential["channels"] = sorted(credential["channels"])
        out.append(credential)
    out.sort(
        key=lambda credential: (
            -credential["count"],
            credential["username"],
            credential["password"],
        )
    )
    return out


def _parse_timestamp(value: str, *, argument: bool = False) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        if argument:
            abort(400, description="since must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def filter_events(events: list[dict]) -> list[dict]:
    channel = request.args.get("channel")
    ip = request.args.get("ip")
    request_type = request.args.get("type")
    since_raw = request.args.get("since")
    since = _parse_timestamp(since_raw, argument=True) if since_raw else None
    out = []
    for event in events:
        if channel and event.get("channel") != channel:
            continue
        if ip and event.get("ip") != ip:
            continue
        if request_type and event.get("request_type") != request_type:
            continue
        if since:
            timestamp = _text(event.get("timestamp"))
            parsed = _parse_timestamp(timestamp) if timestamp else None
            if parsed is None or parsed < since:
                continue
        out.append(event)
    out.sort(key=lambda event: _text(event.get("timestamp")) or "", reverse=True)
    return out


def _int_arg(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, description=f"{name} must be an integer")
    if value < minimum or value > maximum:
        abort(400, description=f"{name} must be between {minimum} and {maximum}")
    return value


def _page_response(items: list, *, default: int = 100, maximum: int = 500):
    limit = _int_arg("limit", default, minimum=1, maximum=maximum)
    offset = _int_arg("offset", 0, minimum=0, maximum=1_000_000)
    response = jsonify(items[offset : offset + limit])
    response.headers["X-Total-Count"] = str(len(items))
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    return response


def _redacted_profile(profile: ProfileConfig) -> dict:
    payload = dataclasses.asdict(profile)
    credentials = payload.get("web", {}).get("honey_credentials", [])
    payload["web"]["honey_credentials"] = [
        [pair[0], "********"] for pair in credentials if pair
    ]
    return payload


def _credential_view(events: list[dict]) -> list[dict]:
    # Shown in full: this loopback-only defensive surface exists to expose the plaintext.
    return extract_credentials(
        events, current_app.config["PROFILE"].web.honey_credentials
    )


def _sessions(events: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for event in events:
        session_id = _text(event.get("session_id"))
        if not session_id:
            continue
        timestamp = _text(event.get("timestamp"))
        previous = seen.get(session_id)
        if previous and (previous["last_seen"] or "") > (timestamp or ""):
            continue
        seen[session_id] = {
            "session_id": session_id,
            "channel": _text(event.get("channel")),
            "ip": _text(event.get("ip")),
            "port": event.get("port") if isinstance(event.get("port"), int) else None,
            "local_port": (
                event.get("local_port")
                if isinstance(event.get("local_port"), int)
                else None
            ),
            "last_seen": timestamp,
        }
    return sorted(
        seen.values(), key=lambda session: session["last_seen"] or "", reverse=True
    )


@bp.route("/")
def dashboard():
    return render_template(
        "operator_dashboard.html", profile=current_app.config["PROFILE"]
    )


@bp.route("/api/profiles")
def profiles():
    return jsonify(_redacted_profile(current_app.config["PROFILE"]))


@bp.route("/api/events")
def events():
    return _page_response(filter_events(_log_events()), default=200, maximum=5000)


@bp.route("/api/stats")
def stats():
    events, skipped = _log_snapshot()
    return jsonify(summarize(events, skipped_records=skipped))


@bp.route("/api/attackers")
def attackers():
    return _page_response(group_attackers(_log_events()))


@bp.route("/api/credentials")
def credentials():
    return _page_response(_credential_view(_log_events()))


@bp.route("/api/uploads")
def uploads():
    return _page_response(extract_uploads(_log_events()))


@bp.route("/api/sessions")
def sessions():
    return _page_response(_sessions(_log_events()))


@bp.route("/api/overview")
def overview():
    events, skipped = _log_snapshot()
    filtered = filter_events(events)
    return jsonify(
        {
            "stats": summarize(filtered, skipped_records=skipped),
            "attackers": group_attackers(filtered)[:10],
            "credentials": _credential_view(filtered)[:10],
            "uploads": extract_uploads(filtered)[:20],
            "events": filtered[:50],
        }
    )


def _authenticate_operator():
    token = current_app.config.get("OPERATOR_TOKEN")
    if not token:
        return None
    auth = request.authorization
    supplied = None
    if auth and auth.type == "basic":
        supplied = auth.password
    elif request.headers.get("Authorization", "").startswith("Bearer "):
        supplied = request.headers["Authorization"][7:]
    if supplied is not None and secrets.compare_digest(supplied, token):
        return None
    return (
        jsonify({"error": "operator authentication required"}),
        401,
        {"WWW-Authenticate": 'Basic realm="DICOMHawk Operator"'},
    )


def _operator_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def new_operator_api(
    profile: ProfileConfig,
    bus: Logger,
    operator_token: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["PROFILE"] = profile
    app.config["BUS"] = bus
    app.config["EVENTS"] = recent_events(bus)
    app.config["EVENT_CACHE"] = {}
    app.config["EVENT_CACHE_LOCK"] = threading.Lock()
    app.config["OPERATOR_TOKEN"] = operator_token or None
    app.before_request(_authenticate_operator)
    app.after_request(_operator_headers)
    app.register_blueprint(bp)
    return app
