"""Operator-facing Flask app (loopback-only) — read-only session/event/profile inspection."""
import dataclasses
from logging import Logger

from flask import Blueprint, Flask, current_app, jsonify

from dicomhawk.bus import recent_events
from dicomhawk.repository import Repository
from profiles.profile import ProfileConfig

bp = Blueprint("operator", __name__)


@bp.route("/api/profiles")
def profiles():
    return jsonify(dataclasses.asdict(current_app.config["PROFILE"]))


@bp.route("/api/events")
def events():
    handler = current_app.config["EVENTS"]
    return jsonify([dict(e.__dict__) for e in handler.events] if handler else [])


@bp.route("/api/sessions")
def sessions():
    handler = current_app.config["EVENTS"]
    seen: dict[str, dict] = {}
    for e in (handler.events if handler else []):
        # Iterating oldest -> newest and overwriting by session_id leaves the last-seen state.
        seen[e.session_id] = {
            "session_id": e.session_id,
            "channel": e.channel,
            "ip": e.ip,
            "port": e.port,
            "last_seen": e.timestamp,
        }
    return jsonify(list(seen.values()))


def new_operator_api(profile: ProfileConfig, repo: Repository, bus: Logger) -> Flask:
    app = Flask(__name__)
    app.config["PROFILE"] = profile
    app.config["REPO"] = repo
    app.config["BUS"] = bus
    app.config["EVENTS"] = recent_events(bus)
    app.register_blueprint(bp)
    return app
