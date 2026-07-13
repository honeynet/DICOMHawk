"""Generic, profile-driven Flask engine serving a profile's web assets from src/profiles/<name>/web/; captures credentials and always fails auth unless grant_access is set."""

import base64
import json
import logging
import os
import secrets
import uuid
from logging import Logger
from urllib.parse import quote

from flask import (
    Flask,
    current_app,
    g,
    redirect,
    render_template,
    request,
    make_response,
    send_from_directory,
    url_for,
)
from pydicom.dataset import Dataset
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind
from werkzeug.serving import WSGIRequestHandler

from dicomhawk.bus import InteractionEvent
from dicomhawk.handlers import _FIND_LEVEL_UID
from dicomhawk.repository import Repository
from profiles.profile import ProfileConfig

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FIELD_LIMIT = 4096


def _web():
    return current_app.config["WEB"]


def _synapse_nonce():
    """Reproduce Synapse's '<digits>.<base64-of-two-guids>' OIDC nonce shape."""
    num = secrets.randbelow(9 * 10**17) + 10**17
    payload = (str(uuid.uuid4()) + str(uuid.uuid4())).encode()
    b64 = base64.b64encode(payload).decode().rstrip("=")
    return f"{num}.{b64}"


def _winlogin_url():
    """The WinAuth login URL the 'Log in with Windows instead' link points to."""
    web = _web()
    oidc = web.oidc
    origin = web.public_base_url or request.host_url.rstrip("/")
    redirect_uri = origin + "/" + oidc["redirect_path"].strip("/") + "/"
    # Lowercase %-encoding to match observed Synapse.
    enc_redirect = (
        quote(redirect_uri, safe="").replace("%3A", "%3a").replace("%2F", "%2f")
    )
    scope = "+".join(oidc["scopes"].split())
    state = "OpenIdConnect.AuthenticationProperties%3d" + secrets.token_urlsafe(120)
    return (
        f"{web.routes['winauth']}"
        f"?client_id={oidc['client_id']}"
        f"&redirect_uri={enc_redirect}"
        "&response_mode=form_post"
        "&response_type=id_token+token"
        f"&scope={scope}"
        f"&state={state}"
        f"&nonce={_synapse_nonce()}"
    )


def _login_context(signin, error_message=""):
    """Render context for login.html, including the hydration modelJson blob."""
    web = _web()
    signin = _signin_token(signin)
    login_url = f"{web.routes['login']}?signin={quote(signin, safe='')}"
    antiforgery = secrets.token_urlsafe(72)
    request_id = str(uuid.uuid4())
    model = {
        "loginUrl": login_url,
        "antiForgery": {"name": web.cookies["antiforgery"], "value": antiforgery},
        "allowRememberMe": False,
        "rememberMe": False,
        "username": "",
        "externalProviders": [],
        "additionalLinks": None,
        "clientName": web.oidc["client_name"],
        "clientUrl": None,
        "clientLogoUrl": None,
        "errorMessage": error_message,
        "requestId": request_id,
        "siteUrl": web.routes["login"].rsplit("/", 1)[0] + "/",
        "siteName": web.identity.get("site_name", web.oidc["client_name"]),
        "currentUser": None,
        "logoutUrl": web.routes["login"].rsplit("/", 1)[0] + "/logout",
        "custom": None,
        "synapseBtnDisplay": None,
    }
    return {
        "login_url": login_url,
        "antiforgery_token": antiforgery,
        "antiforgery_name": web.cookies["antiforgery"],
        # This JSON is embedded in a script element. Escaping HTML-significant
        # characters prevents a hostile signin value from closing that element.
        "model_json": json.dumps(model)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026"),
        "synapse_version": str(web.identity["version"]),
        "copyright": web.identity["copyright"],
        "license_lines": web.license["lines"],
        "license_issued": str(web.license["issued"]),
        "winlogin_url": _winlogin_url(),
        "forgot_url": web.routes["forgot_password"],
        # Plain top-level value alongside model_json's copy — Fujifilm's AngularJS
        # login.html parses model_json client-side; a plainer template can just use this.
        "error_message": error_message,
        # Discoverable hint for the first honey credential, if any (design ref: v2.0's login.html).
        "honey_hint": (
            f"{web.honey_credentials[0][0]} / {web.honey_credentials[0][1]}"
            if web.honey_credentials
            else ""
        ),
    }


def _protected_blob(n=64):
    """An opaque base64 value standing in for a server-protected cookie payload."""
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")


def _signin_token(value):
    """Return a bounded token safe to reuse in a cookie name and query string."""
    value = str(value or "")[:128]
    if value and all(ch.isalnum() or ch in "-_" for ch in value):
        return value
    return secrets.token_hex(16)


def _set_login_cookies(resp, antiforgery, signin):
    """Set the cookies a real Synapse sign-on drops (Secure/SameSite=None as on HTTPS)."""
    web = _web()
    cookies = web.cookies
    secure = web.secure_cookies or request.is_secure
    same_site = "None" if secure else "Lax"
    resp.set_cookie(
        cookies["antiforgery"],
        antiforgery,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    resp.set_cookie(
        f"{cookies['signin_message_prefix']}{_signin_token(signin)}",
        _protected_blob(),
        secure=secure,
        httponly=True,
        samesite=same_site,
        path="/",
    )
    nonce_id = base64.urlsafe_b64encode(secrets.token_bytes(8)).decode().rstrip("=")
    resp.set_cookie(
        f"{cookies['nonce_prefix']}{nonce_id}",
        _protected_blob(),
        secure=secure,
        httponly=True,
        samesite=same_site,
        path="/",
    )
    # Cleared with epoch-expiry on the login GET, as real Synapse does.
    for name in (cookies["idp"], cookies["idp_token"]):
        resp.set_cookie(
            name,
            "",
            expires=0,
            secure=secure,
            httponly=True,
            samesite=same_site,
            path="/",
        )


def _bounded(value):
    value = str(value or "")
    if len(value) <= _LOG_FIELD_LIMIT:
        return value
    return value[:_LOG_FIELD_LIMIT] + "...[truncated]"


def _http_session_id():
    web = _web()
    token = request.args.get("signin") or request.cookies.get(web.cookies["session"])
    return "web-" + _bounded(token or request.remote_addr or "unknown")


def _capture(username, password, request_type="WEB_LOGIN_ATTEMPT"):
    """Log the credential attempt to the shared interaction log (channel=WEB)."""
    username, password = _bounded(username), _bounded(password)
    params = [f"Username: {username}"]
    if password:
        params.append(f"Password: {password}")
    current_app.config["BUS"].warning(
        InteractionEvent.from_http(
            "WEB",
            request_type,
            session_id=_http_session_id(),
            ip=request.remote_addr,
            port=request.environ.get("REMOTE_PORT"),
            session_parameters=params,
            log_level="WARNING",
            method=request.method,
            path=_bounded(request.full_path.rstrip("?")),
            user_agent=_bounded(request.headers.get("User-Agent", "")),
        )
    )


def _log_probe(request_type):
    """Log a bare honeytrap/scan hit (no credentials involved), same channel=WEB path as _capture."""
    current_app.config["BUS"].info(
        InteractionEvent.from_http(
            "WEB",
            request_type,
            session_id=_http_session_id(),
            ip=request.remote_addr,
            port=request.environ.get("REMOTE_PORT"),
            log_level="INFO",
            method=request.method,
            path=_bounded(request.full_path.rstrip("?")),
            user_agent=_bounded(request.headers.get("User-Agent", "")),
        )
    )


def _grant():
    """Grant response: redirect into the decoy worklist with the session cookie set."""
    web = _web()
    resp = make_response(redirect(web.routes["worklist"], code=302))
    secure = web.secure_cookies or request.is_secure
    resp.set_cookie(
        web.cookies["session"],
        _protected_blob(32),
        secure=secure,
        httponly=True,
        samesite="None" if secure else "Lax",
    )
    return resp


def _worklist_studies():
    """Real seeded studies via repo.find(), collapsed to one row per study (as handle_find does)."""
    repo: Repository = current_app.config["REPO"]
    model = StudyRootQueryRetrieveInformationModelFind
    ds = Dataset()
    ds.QueryRetrieveLevel = "SERIES"
    for kw in (
        "StudyInstanceUID",
        "StudyDate",
        "StudyTime",
        "PatientID",
        "PatientName",
        "SeriesInstanceUID",
        "Modality",
    ):
        setattr(ds, kw, "")

    result = repo.find(ds, model)
    if result.error is not None:
        return []

    seen, studies = set(), []
    for m in result.matches:
        uid = getattr(m, _FIND_LEVEL_UID["STUDY"], None)
        if uid in seen:
            continue
        seen.add(uid)
        idt = m.as_identifier(ds, model)
        date = str(getattr(idt, "StudyDate", "") or "")
        time = str(getattr(idt, "StudyTime", "") or "")
        studies.append(
            {
                "patient_name": str(getattr(idt, "PatientName", "") or "—"),
                "description": "—",
                "study_datetime": f"{date} {time}".strip() or "—",
                "modality": str(getattr(idt, "Modality", "") or "—"),
                "status": "Unread",
                "age": "—",
            }
        )
    return studies


def _make_nonce():
    # Per-request CSP nonce, shared with the inline <script> tags.
    g.csp_nonce = secrets.token_urlsafe(16)


def _inject_context():
    # fingerprint_seam is the Weeks 5-6 injection point; empty unless web.fingerprint_script is set.
    nonce = g.get("csp_nonce", "")
    seam = ""
    script = _web().fingerprint_script
    if script:
        seam = f'<script nonce="{nonce}" src="{url_for("static", filename=script)}"></script>'
    return {"csp_nonce": nonce, "fingerprint_seam": seam}


def _spoof(resp):
    web = _web()
    for k, v in web.headers.items():
        resp.headers[k] = v
    if web.content_security_policy is not None:
        csp = web.content_security_policy.replace("{nonce}", g.get("csp_nonce", ""))
        resp.headers["Content-Security-Policy"] = csp
        if web.legacy_csp_header:
            resp.headers["X-Content-Security-Policy"] = csp
    if resp.mimetype == "text/html":  # HTML no-store; static assets stay cacheable
        for k, v in web.html_cache_headers.items():
            resp.headers[k] = v
    return resp


def favicon():
    # A profile may ship no favicon at all; don't crash serving one that isn't there.
    if not _web().favicon:
        return ("", 404)
    return send_from_directory(
        current_app.static_folder, _web().favicon, mimetype="image/x-icon"
    )


def robots_txt():
    lines = ["User-agent: *"] + [f"Disallow: {path}" for path, _ in _web().honeytraps]
    return ("\n".join(lines) + "\n", 200, {"Content-Type": "text/plain"})


# Generic bait behaviors any profile's honeytraps can point at (see WebConfig.honeytraps).
_HONEYTRAP_RESPONSES = {
    # Mimics a session-protected secondary app by reusing the engine's own entry-point route.
    "login_redirect": lambda: redirect(url_for("entry")),
    # Mimics a stock ASP.NET Web API Help Page's default "no matching action" 404 shape.
    "api_404": lambda: (
        {
            "Message": f"No HTTP resource was found that matches the request URI '{request.url}'.",
            "MessageDetail": "No action was found on the controller that matches the request.",
        },
        404,
    ),
    # Static "401 - Unauthorized" bait page (design ref: v2.0's admin.routes.js); needs unauthorized.html.
    "unauthorized_page": lambda: (
        render_template("unauthorized.html", entry_url=_web().routes["entry"]),
        401,
    ),
}


def _honeytrap_view(response_kind):
    def view(**_kwargs):
        _log_probe(f"WEB_HONEYTRAP_{response_kind.upper()}")
        respond = _HONEYTRAP_RESPONSES.get(response_kind)
        return respond() if respond else ("", 404)

    return view


def _iis_404(err):
    # Any unmapped path is itself a signal (a scanner walking the tree); log it and
    # don't leak a Werkzeug default error page under the spoofed IIS identity.
    _log_probe("WEB_404")
    return ("404 - Not Found", 404, {"Content-Type": "text/plain"})


def _request_too_large(err):
    _log_probe("WEB_REQUEST_TOO_LARGE")
    return (
        "The request filtering module is configured to deny a request that exceeds the request content length.",
        413,
        {"Content-Type": "text/plain"},
    )


def root():
    return redirect(_web().routes["entry"], code=302)


def entry():
    # The application launch URL performs sign-on discovery; the authenticated
    # shell itself lives under WorkflowUI on the Fujifilm profile.
    web = _web()
    if request.cookies.get(web.cookies["session"]):
        return redirect(web.routes["worklist"], code=302)
    signin = secrets.token_hex(16)
    return redirect(f"{web.routes['login']}?signin={signin}", code=302)


def worklist(subpath=None):
    web = _web()
    if not request.cookies.get(web.cookies["session"]):
        return redirect(web.routes["entry"], code=302)
    _log_probe("WEB_WORKLIST_VIEW")
    return render_template("worklist.html", studies=_worklist_studies())


def login_get():
    signin = _signin_token(request.args.get("signin") or secrets.token_hex(16))
    error = "Username or password is incorrect" if request.args.get("error") else ""
    ctx = _login_context(signin, error)
    resp = make_response(render_template("login.html", **ctx))
    _set_login_cookies(resp, ctx["antiforgery_token"], signin)
    return resp


def login_post():
    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if (username, password) in _web().honey_credentials:
        # Bait, not a real account: grants unconditionally (unlike grant_access) and logs distinctly.
        _capture(username, password, request_type="WEB_HONEY_CREDENTIAL_USED")
        return _grant()

    _capture(username, password)
    signin = request.args.get("signin") or secrets.token_hex(16)
    if _web().grant_access:
        return _grant()
    # Deny: re-render the sign-on page with the real error banner.
    return render_template(
        "login.html", **_login_context(signin, "Username or password is incorrect")
    )


def _winauth_challenge():
    """401 challenge (native Sign in dialog); its body is the 'Unable to log in' cancel page."""
    web = _web()
    resp = make_response(
        render_template(
            "winauth_unable.html", sts_authorize_url=web.routes["sts_authorize"]
        ),
        401,
    )
    resp.headers["WWW-Authenticate"] = f'Basic realm="{request.host}"'
    # Real Synapse WinAuth stores the originally-requested URL in this cookie.
    secure = web.secure_cookies or request.is_secure
    resp.set_cookie(
        web.cookies["winlogin_origurl"],
        _protected_blob(32),
        secure=secure,
        httponly=True,
        samesite="None" if secure else "Lax",
        path="/",
    )
    return resp


def csp_report():
    # CSP violation reports (report-uri) land here; a real endpoint just absorbs them.
    return ("", 204)


def translated_items(item_id):
    # translation.js reads data['Text1']/['Text2']/['Text3'] — real ASP.NET PascalCase wire format.
    m = _web().winauth_messages
    return {
        "Text1": m.get("text1", ""),
        "Text2": m.get("text2", ""),
        "Text3": m.get("text3", ""),
    }


def sts_error():
    # Distinctive Synapse STS error page (heading "Error" + "Request Id:").
    return render_template(
        "error.html",
        request_id=str(uuid.uuid4()),
        login_url=_web().routes["login"],
        error_message=(
            "There is an error determining which application you are "
            "attempting to sign into. Return to the application and try again."
        ),
    )


def _synapse_500(err):
    # Unhandled errors render the branded page, never a Werkzeug traceback.
    return (
        render_template(
            "error.html",
            request_id=str(uuid.uuid4()),
            login_url=_web().routes["login"],
            error_message="An error occurred while processing your request.",
        ),
        500,
    )


def sts_authorize():
    # 'Log in directly' on the 'Unable to log in' page lands here -> back to sign-on.
    web = _web()
    signin = secrets.token_hex(16)
    return redirect(f"{web.routes['login']}?signin={signin}", code=302)


def forgot_password_get():
    return render_template(
        "forgot_password.html",
        submitted=False,
        forgot_url=_web().routes["forgot_password"],
    )


def forgot_password_post():
    # Capture the probed username; always return the generic success (anti-enumeration).
    _capture(request.form.get("username", ""), "", request_type="WEB_FORGOT_PASSWORD")
    return render_template(
        "forgot_password.html",
        submitted=True,
        forgot_url=_web().routes["forgot_password"],
    )


def winauth_login():
    # No credentials -> 401 (native dialog); submitted creds arrive in the Authorization header.
    auth = request.authorization
    if auth is None or auth.type != "basic":
        return _winauth_challenge()
    username, password = auth.username or "", auth.password or ""

    if (username, password) in _web().honey_credentials:
        _capture(username, password, request_type="WEB_HONEY_CREDENTIAL_USED")
        return _grant()

    _capture(username, password, request_type="WEB_WINAUTH_ATTEMPT")
    if _web().grant_access:
        return _grant()
    return (
        _winauth_challenge()
    )  # deny -> dialog reappears, as if credentials were wrong


class _SpoofHandler(WSGIRequestHandler):
    """Drop the dev server's auto Server/Date so only the spoofed Server header ships (no Werkzeug leak)."""

    def send_response(self, code, message=None):
        self.log_request(code)
        self.send_response_only(code, message)


def new_web(profile: ProfileConfig, repo: Repository, bus: Logger) -> Flask:
    """Build the attacker-facing Flask app for `profile` — routes/cookies come from its own web config."""
    profile_dir = profile.web.assets_dir or os.path.join(
        _SRC, "profiles", profile.web.templates_dir, "web"
    )
    app = Flask(
        __name__,
        template_folder=os.path.join(profile_dir, "templates"),
        static_folder=os.path.join(profile_dir, "static"),
    )
    app.config["WEB"] = profile.web
    app.config["BUS"] = bus
    app.config["REPO"] = repo
    app.config["MAX_CONTENT_LENGTH"] = profile.web.max_request_bytes
    app.before_request(_make_nonce)
    app.context_processor(_inject_context)
    app.after_request(_spoof)
    app.register_error_handler(404, _iis_404)
    app.register_error_handler(413, _request_too_large)
    app.register_error_handler(500, _synapse_500)

    routes = profile.web.routes
    # Every route is registered per-profile from its own web.routes — nothing here is a
    # fixed path, so one profile's identity can never leak into another's address bar.
    app.add_url_rule("/", "root", root)
    app.add_url_rule("/favicon.ico", "favicon", favicon)
    app.add_url_rule("/robots.txt", "robots_txt", robots_txt)
    app.add_url_rule(routes["entry"], "entry", entry)
    worklist_prefix = routes["worklist"].rstrip("/")
    app.add_url_rule(worklist_prefix + "/", "worklist", worklist)
    app.add_url_rule(
        worklist_prefix + "/<path:subpath>", "worklist_deep_link", worklist
    )
    app.add_url_rule(routes["login"], "login_get", login_get, methods=["GET"])
    app.add_url_rule(routes["login"], "login_post", login_post, methods=["POST"])
    app.add_url_rule(routes["winauth"], "winauth_login", winauth_login)
    app.add_url_rule(routes["csp_report"], "csp_report", csp_report, methods=["POST"])
    app.add_url_rule(
        routes["translated_items"] + "/<int:item_id>",
        "translated_items",
        translated_items,
        methods=["GET", "POST"],
    )
    app.add_url_rule(routes["sts_error"], "sts_error", sts_error)
    app.add_url_rule(routes["sts_authorize"], "sts_authorize", sts_authorize)
    app.add_url_rule(
        routes["forgot_password"],
        "forgot_password_get",
        forgot_password_get,
        methods=["GET"],
    )
    app.add_url_rule(
        routes["forgot_password"],
        "forgot_password_post",
        forgot_password_post,
        methods=["POST"],
    )

    # Per-profile data, not engine code — a profile with none stays a plain 404 via _iis_404 above.
    for i, (path, kind) in enumerate(profile.web.honeytraps):
        prefix = path.rstrip("/")
        view = _honeytrap_view(kind)
        app.add_url_rule(prefix + "/", f"honeytrap_{i}", view, methods=["GET", "POST"])
        app.add_url_rule(
            prefix + "/<path:subpath>",
            f"honeytrap_{i}_sub",
            view,
            methods=["GET", "POST"],
        )

    return app


if __name__ == "__main__":
    # Standalone: configure the bus logger; host/port/log path are deployment -> env.
    from dicomhawk.bus import new_bus
    from dicomhawk.repository import new_repo
    from dicomhawk.storage import new_store
    from profiles.profile import load_profile

    bus_logger = logging.getLogger("bus")
    new_bus(stdout=os.environ.get("SYNAPSE_LOG", "dicomhawk.log"))
    prof = load_profile(os.environ.get("SYNAPSE_PROFILE", "fujifilm"))
    dev_repo = new_repo(None, new_store("traces"))
    dev_app = new_web(prof, dev_repo, bus_logger)
    dev_app.run(
        host=os.environ.get("SYNAPSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SYNAPSE_PORT", "8080")),
        debug=False,
        request_handler=_SpoofHandler,
    )
