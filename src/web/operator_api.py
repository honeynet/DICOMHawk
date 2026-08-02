"""Read-only operator intelligence API and dashboard over the durable interaction log."""

import dataclasses
import heapq
import secrets
from collections import OrderedDict
from datetime import datetime, timezone
from logging import FileHandler, Logger
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator

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

if TYPE_CHECKING:  # annotation only — the operator API must not hard-depend on the analysis package
    from analysis.store import AnalysisStore

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
_MAX_AGGREGATE_KEYS = 10_000
_MAX_SOURCES_PER_CREDENTIAL = 1_000


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


def _valid_event(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    # An interaction record without either discriminator cannot contribute to any view.
    if not _text(value.get("request_type")) and not _text(value.get("channel")):
        return None
    return value


class _EventSource:
    """One-pass event reader that never retains the full rotated log set."""

    def __init__(self, bus: Logger, fallback) -> None:
        self.paths = _event_paths(bus)
        self.fallback = fallback
        self.skipped = 0

    def __iter__(self) -> Iterator[dict]:
        if not self.paths:
            if self.fallback is None:
                return
            for item in self.fallback.snapshot():
                event = _valid_event(dict(item.__dict__))
                if event is None:
                    self.skipped += 1
                else:
                    yield event
            return

        for path in self.paths:
            try:
                stream = path.open(encoding="utf-8", errors="replace")
            except OSError:
                self.skipped += 1
                continue
            with stream:
                for line in stream:
                    if len(line) > _MAX_LOG_LINE_BYTES:
                        self.skipped += 1
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = _valid_event(ujson.loads(line))
                    except (TypeError, ValueError):
                        event = None
                    if event is None:
                        self.skipped += 1
                    else:
                        yield event


def _log_events() -> _EventSource:
    return _EventSource(current_app.config["BUS"], current_app.config["EVENTS"])


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


def _upload_record(event: dict) -> dict | None:
    artifact = _artifact(event)
    if artifact is None:
        return None
    return {
        "channel": _text(event.get("channel")),
        "ip": _text(event.get("ip")),
        "timestamp": _text(event.get("timestamp")),
        "request_type": _text(event.get("request_type")),
        **artifact,
    }


def extract_uploads(events: Iterable[dict]) -> list[dict]:
    out = []
    for event in events:
        if isinstance(event, dict) and (upload := _upload_record(event)) is not None:
            out.append(upload)
    out.sort(
        key=lambda upload: (upload.get("timestamp") or "", upload.get("sha256") or ""),
        reverse=True,
    )
    return out


class _StatsAccumulator:
    def __init__(self) -> None:
        self.total = 0
        self.by_channel: dict[str, int] = {}
        self.by_request_type: dict[str, int] = {}
        self.ips: set[str] = set()
        self.ips_truncated = False
        self.credentials_captured = 0
        self.credential_pairs: set[tuple[str, str]] = set()
        self.credentials_truncated = False
        self.first_event: str | None = None
        self.last_event: str | None = None
        self.upload_attempts = 0
        self.uploads_captured = 0
        self.uploads_rejected = 0
        self.skipped = 0

    def add(self, event: dict) -> None:
        if not isinstance(event, dict):
            self.skipped += 1
            return
        self.total += 1
        channel = _text(event.get("channel")) or "UNKNOWN"
        request_type = _text(event.get("request_type")) or "UNKNOWN"
        self.by_channel[channel] = self.by_channel.get(channel, 0) + 1
        self.by_request_type[request_type] = (
            self.by_request_type.get(request_type, 0) + 1
        )
        if ip := _text(event.get("ip")):
            if ip in self.ips or len(self.ips) < _MAX_AGGREGATE_KEYS:
                self.ips.add(ip)
            else:
                self.ips_truncated = True
        if _has_credentials(event):
            self.credentials_captured += 1
            params = _parse_params(event)
            pair = (
                _param_first(params, "Username") or "",
                _param_first(params, "Password") or "",
            )
            if (
                pair in self.credential_pairs
                or len(self.credential_pairs) < _MAX_AGGREGATE_KEYS
            ):
                self.credential_pairs.add(pair)
            else:
                self.credentials_truncated = True
        if timestamp := _text(event.get("timestamp")):
            self.first_event = (
                timestamp
                if self.first_event is None
                else min(self.first_event, timestamp)
            )
            self.last_event = (
                timestamp
                if self.last_event is None
                else max(self.last_event, timestamp)
            )
        if artifact := _artifact(event):
            self.upload_attempts += 1
            self.uploads_captured += int(artifact["captured"])
            self.uploads_rejected += int(artifact["disposition"] == "rejected")

    def result(self, *, skipped_records: int = 0) -> dict:
        return {
            "total_events": self.total,
            "by_channel": self.by_channel,
            "by_request_type": self.by_request_type,
            "unique_source_ips": len(self.ips),
            "unique_source_ips_truncated": self.ips_truncated,
            "credentials_captured": self.credentials_captured,
            "unique_credentials": len(self.credential_pairs),
            "unique_credentials_truncated": self.credentials_truncated,
            "upload_attempts": self.upload_attempts,
            "uploads_captured": self.uploads_captured,
            "uploads_rejected": self.uploads_rejected,
            "first_event": self.first_event,
            "last_event": self.last_event,
            "skipped_records": skipped_records + self.skipped,
        }


def summarize(events: Iterable[dict], *, skipped_records: int = 0) -> dict:
    accumulator = _StatsAccumulator()
    for event in events:
        accumulator.add(event)
    return accumulator.result(skipped_records=skipped_records)


def _add_attacker(agg: dict[str, dict], event: dict) -> bool:
    if not isinstance(event, dict) or not (ip := _text(event.get("ip"))):
        return False
    attacker = agg.get(ip)
    if attacker is None:
        if len(agg) >= _MAX_AGGREGATE_KEYS:
            return True
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
    return False


def _finish_attackers(agg: dict[str, dict]) -> list[dict]:
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


def group_attackers(events: Iterable[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for event in events:
        _add_attacker(agg, event)
    return _finish_attackers(agg)


def _add_credential(
    agg: dict[tuple[str, str], dict], event: dict, honey: set[tuple[str, str]]
) -> bool:
    if not isinstance(event, dict) or not _has_credentials(event):
        return False
    params = _parse_params(event)
    key = (
        _param_first(params, "Username") or "",
        _param_first(params, "Password") or "",
    )
    credential = agg.get(key)
    if credential is None:
        if len(agg) >= _MAX_AGGREGATE_KEYS:
            if key not in honey:
                return True
            evict = next(
                (
                    candidate
                    for candidate, value in agg.items()
                    if not value["honey_hit"]
                ),
                None,
            )
            if evict is None:
                return True
            del agg[evict]
        credential = agg[key] = {
            "username": key[0],
            "password": key[1],
            "count": 0,
            "source_ips": set(),
            "source_ips_truncated": False,
            "channels": set(),
            "honey_hit": key in honey,
            "first_seen": None,
            "last_seen": None,
        }
    credential["count"] += 1
    if ip := _text(event.get("ip")):
        sources = credential["source_ips"]
        if ip in sources or len(sources) < _MAX_SOURCES_PER_CREDENTIAL:
            sources.add(ip)
        else:
            credential["source_ips_truncated"] = True
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
    return False


def _finish_credentials(agg: dict[tuple[str, str], dict]) -> list[dict]:
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


def extract_credentials(events: Iterable[dict], honey_credentials=()) -> list[dict]:
    honey = {tuple(pair) for pair in honey_credentials}
    agg: dict[tuple[str, str], dict] = {}
    for event in events:
        _add_credential(agg, event, honey)
    return _finish_credentials(agg)


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


def _request_event_filter() -> Callable[[dict], bool]:
    channel = request.args.get("channel")
    ip = request.args.get("ip")
    request_type = request.args.get("type")
    since_raw = request.args.get("since")
    since = _parse_timestamp(since_raw, argument=True) if since_raw else None

    def matches(event: dict) -> bool:
        if channel and event.get("channel") != channel:
            return False
        if ip and event.get("ip") != ip:
            return False
        if request_type and event.get("request_type") != request_type:
            return False
        if since:
            timestamp = _text(event.get("timestamp"))
            parsed = _parse_timestamp(timestamp) if timestamp else None
            if parsed is None or parsed < since:
                return False
        return True

    return matches


def filter_events(events: Iterable[dict]) -> list[dict]:
    matches = _request_event_filter()
    out = []
    for event in events:
        if matches(event):
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
    offset = _int_arg("offset", 0, minimum=0, maximum=10_000)
    response = jsonify(items[offset : offset + limit])
    response.headers["X-Total-Count"] = str(len(items))
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    return response


def _push_latest(
    heap: list[tuple[str, int, dict]],
    item: dict,
    sequence: int,
    capacity: int,
) -> None:
    entry = (_text(item.get("timestamp")) or "", sequence, item)
    if len(heap) < capacity:
        heapq.heappush(heap, entry)
    elif entry[:2] > heap[0][:2]:
        heapq.heapreplace(heap, entry)


def _stream_page(
    events: Iterable[dict],
    transform: Callable[[dict], dict | None],
    *,
    predicate: Callable[[dict], bool] | None = None,
    default: int = 100,
    maximum: int = 500,
):
    limit = _int_arg("limit", default, minimum=1, maximum=maximum)
    offset = _int_arg("offset", 0, minimum=0, maximum=10_000)
    capacity = offset + limit
    heap: list[tuple[str, int, dict]] = []
    total = 0
    for sequence, event in enumerate(events):
        if predicate is not None and not predicate(event):
            continue
        item = transform(event)
        if item is None:
            continue
        total += 1
        _push_latest(heap, item, sequence, capacity)
    ordered = [entry[2] for entry in sorted(heap, reverse=True)]
    response = jsonify(ordered[offset : offset + limit])
    response.headers["X-Total-Count"] = str(total)
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


def _sessions(events: Iterable[dict]) -> tuple[list[dict], bool]:
    seen: OrderedDict[str, dict] = OrderedDict()
    truncated = False
    for event in events:
        session_id = _text(event.get("session_id"))
        if not session_id:
            continue
        timestamp = _text(event.get("timestamp"))
        previous = seen.get(session_id)
        if previous and (previous["last_seen"] or "") > (timestamp or ""):
            continue
        if previous is None and len(seen) >= _MAX_AGGREGATE_KEYS:
            seen.popitem(last=False)
            truncated = True
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
        seen.move_to_end(session_id)
    return (
        sorted(
            seen.values(), key=lambda session: session["last_seen"] or "", reverse=True
        ),
        truncated,
    )


def _overview(source: _EventSource, predicate: Callable[[dict], bool]) -> dict:
    stats = _StatsAccumulator()
    attacker_agg: dict[str, dict] = {}
    credential_agg: dict[tuple[str, str], dict] = {}
    honey = {
        tuple(pair) for pair in current_app.config["PROFILE"].web.honey_credentials
    }
    upload_heap: list[tuple[str, int, dict]] = []
    event_heap: list[tuple[str, int, dict]] = []
    attackers_truncated = False
    credentials_truncated = False

    for sequence, event in enumerate(source):
        if not predicate(event):
            continue
        stats.add(event)
        attackers_truncated |= _add_attacker(attacker_agg, event)
        credentials_truncated |= _add_credential(credential_agg, event, honey)
        if upload := _upload_record(event):
            _push_latest(upload_heap, upload, sequence, 20)
        _push_latest(event_heap, event, sequence, 50)

    return {
        "stats": stats.result(skipped_records=source.skipped),
        "attackers": _finish_attackers(attacker_agg)[:10],
        "credentials": _finish_credentials(credential_agg)[:10],
        "uploads": [entry[2] for entry in sorted(upload_heap, reverse=True)],
        "events": [entry[2] for entry in sorted(event_heap, reverse=True)],
        "truncated": {
            "attackers": attackers_truncated,
            "credentials": credentials_truncated,
        },
    }


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
    return _stream_page(
        _log_events(),
        lambda event: event,
        predicate=_request_event_filter(),
        default=200,
        maximum=5000,
    )


@bp.route("/api/stats")
def stats():
    source = _log_events()
    result = summarize(source)
    result["skipped_records"] += source.skipped
    return jsonify(result)


@bp.route("/api/attackers")
def attackers():
    agg: dict[str, dict] = {}
    truncated = False
    for event in _log_events():
        truncated |= _add_attacker(agg, event)
    response = _page_response(_finish_attackers(agg))
    response.headers["X-Aggregation-Truncated"] = str(truncated).lower()
    return response


@bp.route("/api/credentials")
def credentials():
    honey = {
        tuple(pair) for pair in current_app.config["PROFILE"].web.honey_credentials
    }
    agg: dict[tuple[str, str], dict] = {}
    truncated = False
    for event in _log_events():
        truncated |= _add_credential(agg, event, honey)
    response = _page_response(_finish_credentials(agg))
    response.headers["X-Aggregation-Truncated"] = str(truncated).lower()
    return response


@bp.route("/api/uploads")
def uploads():
    return _stream_page(_log_events(), _upload_record)


@bp.route("/api/sessions")
def sessions():
    items, truncated = _sessions(_log_events())
    response = _page_response(items)
    response.headers["X-Aggregation-Truncated"] = str(truncated).lower()
    return response


def _artifact_record(record) -> dict:
    # Never includes capture_path — the operator API exposes findings, not a download endpoint.
    return {
        "artifact_id": record.artifact_id,
        "size": record.size,
        "sha256": record.sha256,
        "channel": record.channel,
        "request_type": record.request_type,
        "disposition": record.disposition,
        "source_encoding": record.source_encoding,
        "session_id": record.session_id,
        "ip": record.ip,
        "local_port": record.local_port,
        "sop_class_uid": record.sop_class_uid,
        "sop_instance_uid": record.sop_instance_uid,
        "state": record.state,
        "attempts": record.attempts,
        "analyzer_version": record.analyzer_version,
        "ruleset_version": record.ruleset_version,
        "matched_rules": record.matched_rules.split(",") if record.matched_rules else [],
        "result": record.result,
        "error": record.error,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


@bp.route("/api/artifacts")
def artifacts():
    store: "AnalysisStore | None" = current_app.config.get("ANALYSIS_STORE")
    if store is None:
        return jsonify([])
    limit = _int_arg("limit", 50, minimum=1, maximum=500)
    offset = _int_arg("offset", 0, minimum=0, maximum=10_000)
    rows, total = store.list_artifacts(
        state=request.args.get("state"),
        channel=request.args.get("channel"),
        ip=request.args.get("ip"),
        sha256=request.args.get("sha256"),
        rule=request.args.get("rule"),
        offset=offset,
        limit=limit,
    )
    response = jsonify([_artifact_record(row) for row in rows])
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    return response


@bp.route("/api/overview")
def overview():
    source = _log_events()
    return jsonify(_overview(source, _request_event_filter()))


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
    analysis_store: "AnalysisStore | None" = None,
) -> Flask:
    app = Flask(__name__)
    app.config["PROFILE"] = profile
    app.config["BUS"] = bus
    app.config["EVENTS"] = recent_events(bus)
    app.config["OPERATOR_TOKEN"] = operator_token or None
    app.config["ANALYSIS_STORE"] = analysis_store
    app.before_request(_authenticate_operator)
    app.after_request(_operator_headers)
    app.register_blueprint(bp)
    return app
