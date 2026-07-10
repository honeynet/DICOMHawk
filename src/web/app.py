"""Generic, profile-driven Flask engine serving a profile's web assets from src/profiles/<name>/web/; captures credentials and always fails auth unless grant_access is set."""
import base64
import json
import logging
import os
import secrets
import uuid
from urllib.parse import quote

from flask import (
    Flask, g, redirect, render_template, request, make_response,
    send_from_directory,
)
from werkzeug.serving import WSGIRequestHandler

from dicomhawk.bus import InteractionEvent
from dicomhawk.config import overlay_config

# Shared interaction logger (serve wires it to dicomhawk.log); web lines are tagged channel=WEB.
bus = logging.getLogger("bus")

# Each profile's web assets live in src/profiles/<name>/web/; the engine is generic.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.environ.get("SYNAPSE_PROFILE", "fujifilm")
PROFILE_DIR = os.path.join(_SRC, "profiles", PROFILE, "web")

app = Flask(
    __name__,
    template_folder=os.path.join(PROFILE_DIR, "templates"),
    static_folder=os.path.join(PROFILE_DIR, "static"),
)

# Operator-tunable values live in the profile's config.yaml; DEFAULTS are the fallback + schema.
CONFIG_PATH = os.path.join(PROFILE_DIR, "config.yaml")

DEFAULTS = {
    "behavior": {"grant_access": False},
    "headers": {
        "Server": "Microsoft-IIS/10.0",
        "X-Powered-By": "ASP.NET",
        "X-AspNet-Version": "4.0.30319",
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "X-Backendserver": "SYNWEB01",
    },
    "html_cache_headers": {
        "Cache-Control": "no-store, no-cache, max-age=0, private",
        "Pragma": "no-cache",
    },
    "content_security_policy": (
        "default-src 'self'; script-src 'nonce-{nonce}' 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src *; "
        "report-uri /SynapseSignOn/sts/csp/report"
    ),
    "identity": {
        "version": "7.4.220",
        "copyright": "Copyright © 2024-2025 FUJIFILM Healthcare Americas "
                     "Corporation. All rights reserved.",
    },
    "license": {
        "issued": "01/01/2025",
        "lines": ["SYNAPSE PACS", "RADIOLOGY DEPARTMENT", "1 HOSPITAL WAY",
                  "SPRINGFIELD, 00000"],
    },
    "oidc": {
        "client_id": "synapsebaseclient",
        "client_name": "SynapseBase",
        "redirect_path": "/Synapse/",
        "scopes": ("openid offline_access profile roles privileges usersession "
                   "culture dbuser auth_mode full_name APService DcmApi "
                   "ProxyService RPEngine StdApi ThinkLogService ViewerService "
                   "WorkflowEngine SubscriptionService ExtApiService "
                   "XDSConsumerApi FHIRCastHub"),
    },
}


def _load_config(path=CONFIG_PATH):
    """Overlay the profile's web config.yaml onto DEFAULTS so partial files work."""
    return overlay_config(DEFAULTS, path)


CONFIG = _load_config()

GRANT_ACCESS = bool(CONFIG["behavior"]["grant_access"])
SPOOF_HEADERS = dict(CONFIG["headers"])
HTML_CACHE_HEADERS = dict(CONFIG["html_cache_headers"])
CSP_TEMPLATE = CONFIG["content_security_policy"]

SYNAPSE_VERSION = str(CONFIG["identity"]["version"])
SYNAPSE_COPYRIGHT = CONFIG["identity"]["copyright"]
LICENSE_LINES = CONFIG["license"]["lines"]
LICENSE_ISSUED = str(CONFIG["license"]["issued"])
OIDC_CLIENT_ID = CONFIG["oidc"]["client_id"]
OIDC_CLIENT_NAME = CONFIG["oidc"]["client_name"]
OIDC_REDIRECT_PATH = CONFIG["oidc"]["redirect_path"]
_OIDC_SCOPES = " ".join(CONFIG["oidc"]["scopes"].split())


def _synapse_nonce():
    """Reproduce Synapse's '<digits>.<base64-of-two-guids>' OIDC nonce shape."""
    num = secrets.randbelow(9 * 10**17) + 10**17
    payload = (str(uuid.uuid4()) + str(uuid.uuid4())).encode()
    b64 = base64.b64encode(payload).decode().rstrip("=")
    return f"{num}.{b64}"


def _winlogin_url():
    """The WinAuth/Login.aspx OIDC URL the 'Log in with Windows instead' link points to."""
    redirect_uri = request.host_url.rstrip("/") + "/" + OIDC_REDIRECT_PATH.strip("/") + "/"
    # Lowercase %-encoding to match observed Synapse.
    enc_redirect = quote(redirect_uri, safe="").replace("%3A", "%3a").replace("%2F", "%2f")
    scope = "+".join(_OIDC_SCOPES.split())
    state = "OpenIdConnect.AuthenticationProperties%3d" + secrets.token_urlsafe(120)
    return (
        "/SynapseSignOn/WinAuth/Login.aspx"
        f"?client_id={OIDC_CLIENT_ID}"
        f"&redirect_uri={enc_redirect}"
        "&response_mode=form_post"
        "&response_type=id_token+token"
        f"&scope={scope}"
        f"&state={state}"
        f"&nonce={_synapse_nonce()}"
    )


def _login_context(signin, error_message=""):
    """Render context for login.html, including the hydration modelJson blob."""
    login_url = f"/SynapseSignOn/sts/login?signin={signin}"
    antiforgery = secrets.token_urlsafe(72)
    request_id = str(uuid.uuid4())
    model = {
        "loginUrl": login_url,
        "antiForgery": {"name": "idsrv.xsrf", "value": antiforgery},
        "allowRememberMe": False,
        "rememberMe": False,
        "username": "",
        "externalProviders": [],
        "additionalLinks": None,
        "clientName": OIDC_CLIENT_NAME,
        "clientUrl": None,
        "clientLogoUrl": None,
        "errorMessage": error_message,
        "requestId": request_id,
        "siteUrl": "/SynapseSignOn/sts/",
        "siteName": "Synapse Sign-On",
        "currentUser": None,
        "logoutUrl": "/SynapseSignOn/sts/logout",
        "custom": None,
        "synapseBtnDisplay": None,
    }
    return {
        "login_url": login_url,
        "antiforgery_token": antiforgery,
        "model_json": json.dumps(model),
        "synapse_version": SYNAPSE_VERSION,
        "copyright": SYNAPSE_COPYRIGHT,
        "license_lines": LICENSE_LINES,
        "license_issued": LICENSE_ISSUED,
        "renew_url": "/SSOMgr/License/Renew",
        "winlogin_url": _winlogin_url(),
        "forgot_url": "/ssomgr/password/forgotpassword",
    }


def _protected_blob(n=64):
    """An opaque base64 value standing in for a server-protected cookie payload."""
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")


def _set_synapse_cookies(resp, antiforgery):
    """Set the cookies a real Synapse sign-on drops (Secure/SameSite=None as on HTTPS)."""
    resp.set_cookie("idsrv.xsrf", antiforgery, secure=True, samesite="None", path="/")
    sim_token = secrets.token_urlsafe(12)
    resp.set_cookie(f"SignInMessage.{sim_token}", _protected_blob(),
                    secure=True, httponly=True, samesite="None", path="/")
    nonce_id = base64.urlsafe_b64encode(secrets.token_bytes(8)).decode().rstrip("=")
    resp.set_cookie(f"OpenIdConnect.nonce.{nonce_id}", _protected_blob(),
                    secure=True, httponly=True, samesite="None", path="/")
    # Cleared with epoch-expiry on the login GET, as real Synapse does.
    for name in ("IdpCookie", "IdpTokenCookie"):
        resp.set_cookie(name, "", expires=0, secure=True, httponly=True,
                        samesite="None", path="/")


def _capture(username, password, request_type="WEB_LOGIN_ATTEMPT"):
    """Log the credential attempt to the shared interaction log (channel=WEB)."""
    params = [f"Username: {username}"]
    if password:
        params.append(f"Password: {password}")
    bus.warning(InteractionEvent.from_http(
        "WEB", request_type,
        session_id="web-" + (request.remote_addr or "unknown"),
        ip=request.remote_addr,
        port=request.environ.get("REMOTE_PORT"),
        session_parameters=params,
        log_level="WARNING",
        method=request.method,
        path=request.path,
        user_agent=request.headers.get("User-Agent", ""),
    ))


def _grant():
    """Grant response: redirect into the decoy worklist with the session cookie set."""
    resp = make_response(redirect("/Synapse", code=302))
    resp.set_cookie("sw_authed", "1", httponly=True, samesite="Lax")
    return resp


@app.before_request
def _make_nonce():
    # Per-request CSP nonce, shared with the inline <script> tags.
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def _inject_nonce():
    return {"csp_nonce": g.get("csp_nonce", "")}


@app.after_request
def _spoof(resp):
    for k, v in SPOOF_HEADERS.items():
        resp.headers[k] = v
    csp = CSP_TEMPLATE.replace("{nonce}", g.get("csp_nonce", ""))
    resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Content-Security-Policy"] = csp  # legacy header real Synapse also sends
    if resp.mimetype == "text/html":  # HTML no-store; static assets stay cacheable
        for k, v in HTML_CACHE_HEADERS.items():
            resp.headers[k] = v
    return resp


@app.route("/favicon.ico")
def favicon():
    # Real Synapse PACS favicon, served at root.
    return send_from_directory(app.static_folder, "synapse/favicon.ico",
                               mimetype="image/x-icon")


@app.route("/")
def root():
    return redirect("/Synapse", code=302)


@app.route("/Synapse")
def synapse_entry():
    # Authenticated session -> worklist; otherwise bounce to sign-on (real behavior).
    if request.cookies.get("sw_authed") == "1":
        return render_template("worklist.html", studies=_placeholder_studies())
    signin = secrets.token_hex(16)
    return redirect(f"/SynapseSignOn/sts/login?signin={signin}", code=302)


@app.route("/SynapseSignOn/sts/login", methods=["GET"])
def login_get():
    signin = request.args.get("signin") or secrets.token_hex(16)
    error = "Username or password is incorrect" if request.args.get("error") else ""
    ctx = _login_context(signin, error)
    resp = make_response(render_template("login.html", **ctx))
    _set_synapse_cookies(resp, ctx["antiforgery_token"])
    return resp


@app.route("/SynapseSignOn/sts/login", methods=["POST"])
def login_post():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    _capture(username, password)

    signin = request.args.get("signin") or secrets.token_hex(16)
    if GRANT_ACCESS:
        return _grant()
    # Deny: re-render the sign-on page with the real error banner.
    return render_template(
        "login.html", **_login_context(signin, "Username or password is incorrect")
    )


def _winauth_challenge():
    """401 challenge (native Sign in dialog); its body is the 'Unable to log in' cancel page."""
    resp = make_response(render_template("winauth_unable.html"), 401)
    resp.headers["WWW-Authenticate"] = f'Basic realm="{request.host}"'
    # Real Synapse WinAuth stores the originally-requested URL in this cookie.
    resp.set_cookie("WinLogin.OrigRequestUrlCookie", _protected_blob(32),
                    secure=True, httponly=True, samesite="None", path="/")
    return resp


@app.route("/SynapseSignOn/sts/csp/report", methods=["POST"])
def csp_report():
    # CSP violation reports (report-uri) land here; a real endpoint just absorbs them.
    return ("", 204)


@app.route("/synapse/error/TranslatedItems/<int:item_id>", methods=["POST", "GET"])
def translated_items(item_id):
    # translation.js on the 'Unable to log in' page POSTs here for localized strings.
    return {
        "Text1": "Synapse Log On",
        "Text2": "Unable to log in using Windows Authentication.",
        "Text3": "Log in directly",
    }


@app.route("/SynapseSignOn/sts/error")
def sts_error():
    # Distinctive Synapse STS error page (heading "Error" + "Request Id:").
    return render_template(
        "error.html",
        request_id=str(uuid.uuid4()),
        error_message=("There is an error determining which application you are "
                       "attempting to sign into. Return to the application and try again."),
    )


@app.errorhandler(500)
def _synapse_500(err):
    # Unhandled errors render the branded page, never a Werkzeug traceback.
    return render_template(
        "error.html", request_id=str(uuid.uuid4()),
        error_message="An error occurred while processing your request.",
    ), 500


@app.route("/SynapseSignOn/sts/connect/authorize")
def sts_authorize():
    # 'Log in directly' on the 'Unable to log in' page lands here -> back to sign-on.
    signin = secrets.token_hex(16)
    return redirect(f"/SynapseSignOn/sts/login?signin={signin}", code=302)


@app.route("/ssomgr/password/forgotpassword", methods=["GET"])
def forgot_password_get():
    return render_template("forgot_password.html", submitted=False)


@app.route("/ssomgr/password/forgotpassword", methods=["POST"])
def forgot_password_post():
    # Capture the probed username; always return the generic success (anti-enumeration).
    _capture(request.form.get("username", ""), "", request_type="WEB_FORGOT_PASSWORD")
    return render_template("forgot_password.html", submitted=True)


@app.route("/SynapseSignOn/WinAuth/Login.aspx")
def winauth_login():
    # No credentials -> 401 (native dialog); submitted creds arrive in the Authorization header.
    auth = request.authorization
    if auth is None or auth.type != "basic":
        return _winauth_challenge()
    _capture(auth.username or "", auth.password or "", request_type="WEB_WINAUTH_ATTEMPT")
    if GRANT_ACCESS:
        return _grant()
    return _winauth_challenge()  # deny -> dialog reappears, as if credentials were wrong


def _placeholder_studies():
    """Placeholder rows; to be replaced by repo.find() on the seeded DB."""
    return [
        {"patient_name": "—", "description": "(worklist reads the seeded DICOM DB)",
         "study_datetime": "—", "modality": "—", "status": "—", "age": "—"},
    ]


class _SpoofHandler(WSGIRequestHandler):
    """Drop the dev server's auto Server/Date so only the spoofed Server header ships (no Werkzeug leak)."""

    def send_response(self, code, message=None):
        self.log_request(code)
        self.send_response_only(code, message)


if __name__ == "__main__":
    # Standalone: configure the bus logger; host/port/log path are deployment -> env.
    from dicomhawk.bus import new_bus
    new_bus(stdout=os.environ.get("SYNAPSE_LOG", "dicomhawk.log"))
    app.run(host=os.environ.get("SYNAPSE_HOST", "127.0.0.1"),
            port=int(os.environ.get("SYNAPSE_PORT", "8080")),
            debug=False, request_handler=_SpoofHandler)
