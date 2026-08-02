"""Profile-driven attacker-facing web application."""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from io import BytesIO
from logging import Logger
from urllib.parse import quote, urlencode

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
from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import UID
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind
from werkzeug.serving import WSGIRequestHandler

from dicomhawk.bus import InteractionEvent
from dicomhawk.handlers import _FIND_LEVEL_UID
from dicomhawk.repository import Repository
from dicomhawk.storage import ArtifactSink, SubmittedArtifact
from profiles.profile import ProfileConfig

logger = logging.getLogger(__name__)

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FIELD_LIMIT = 4096
_WEB_SESSION_TTL_SECONDS = 8 * 60 * 60
_WEB_MAX_SESSIONS = 10_000
_BROWSE_MAX_OFFSET = 20_000


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
        # Prevent a hostile signin value from closing the script element.
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
        # Plain templates use this instead of parsing Fujifilm's model_json.
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
    signin = request.args.get("signin")
    session = request.cookies.get(web.cookies["session"])
    # Correlate authenticated requests without writing the bearer token itself to logs.
    token = signin or (
        hashlib.sha256(session.encode()).hexdigest()[:24] if session else None
    )
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
            local_port=request.environ.get("SERVER_PORT"),
            session_parameters=params,
            log_level="WARNING",
            method=request.method,
            path=_bounded(request.full_path.rstrip("?")),
            user_agent=_bounded(request.headers.get("User-Agent", "")),
        )
    )


def _log_probe(
    request_type,
    params=None,
    *,
    matches=None,
    level="INFO",
    artifact=None,
    fingerprint_hash=None,
):
    """Log a bare honeytrap/scan/browse hit (no credentials), same channel=WEB path as _capture."""
    log = current_app.config["BUS"]
    emit = log.warning if level == "WARNING" else log.info
    emit(
        InteractionEvent.from_http(
            "WEB",
            request_type,
            session_id=_http_session_id(),
            ip=request.remote_addr,
            port=request.environ.get("REMOTE_PORT"),
            local_port=request.environ.get("SERVER_PORT"),
            session_parameters=params,
            matches=matches,
            log_level=level,
            method=request.method,
            path=_bounded(request.full_path.rstrip("?")),
            user_agent=_bounded(request.headers.get("User-Agent", "")),
            artifact=artifact,
            fingerprint_hash=fingerprint_hash,
        )
    )


def _local_port() -> int | None:
    try:
        return int(request.environ.get("SERVER_PORT"))
    except (TypeError, ValueError):
        return None


def _submit_artifact(
    capture,
    *,
    request_type: str,
    disposition: str,
    sop_class_uid: str | None = None,
    sop_instance_uid: str | None = None,
) -> None:
    sink: ArtifactSink = current_app.config["ARTIFACT_SINK"]
    try:
        sink(
            SubmittedArtifact(
                capture,
                channel="WEB",
                request_type=request_type,
                disposition=disposition,
                source_encoding="part10",  # the browse upload only ever accepts .dcm Part-10 files
                session_id=_http_session_id(),
                ip=request.remote_addr,
                local_port=_local_port(),
                sop_class_uid=sop_class_uid,
                sop_instance_uid=sop_instance_uid,
            )
        )
    except Exception:
        # Analysis must never change what the peer sees; the payload is already captured.
        logger.exception("Artifact sink failed for %s", capture.artifact_id)


def _issue_session() -> str:
    token = _protected_blob(32)
    now = time.monotonic()
    sessions: dict[str, float] = current_app.config["WEB_SESSIONS"]
    lock: threading.Lock = current_app.config["WEB_SESSIONS_LOCK"]
    with lock:
        for expired in [key for key, deadline in sessions.items() if deadline <= now]:
            sessions.pop(expired, None)
        while len(sessions) >= _WEB_MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))
        sessions[token] = now + _WEB_SESSION_TTL_SECONDS
    return token


def _session_ok() -> bool:
    token = request.cookies.get(_web().cookies["session"])
    if not token:
        return False
    sessions: dict[str, float] = current_app.config["WEB_SESSIONS"]
    lock: threading.Lock = current_app.config["WEB_SESSIONS_LOCK"]
    with lock:
        deadline = sessions.get(token)
        if deadline is None:
            return False
        if deadline <= time.monotonic():
            sessions.pop(token, None)
            return False
    return True


def _revoke_session() -> None:
    token = request.cookies.get(_web().cookies["session"])
    if not token:
        return
    with current_app.config["WEB_SESSIONS_LOCK"]:
        current_app.config["WEB_SESSIONS"].pop(token, None)


def _grant():
    """Grant response: redirect into the decoy landing (browse console if enabled) with the session cookie set."""
    web = _web()
    landing = web.routes["console"] if web.browse else web.routes["worklist"]
    resp = make_response(redirect(landing, code=302))
    secure = web.secure_cookies or request.is_secure
    resp.set_cookie(
        web.cookies["session"],
        _issue_session(),
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


def _apply_request_limit():
    web = _web()
    if web.browse and request.method == "POST" and request.path == web.routes["upload"]:
        request.max_content_length = web.upload_max_request_bytes


def _inject_context():
    # The collector reads its enabled categories from data-signals, so the asset itself stays static.
    nonce = g.get("csp_nonce", "")
    web = _web()
    seam = ""
    if web.fingerprint.enabled:
        seam = (
            f'<script nonce="{nonce}" src="{web.routes["fingerprint_script"]}" '
            f'data-signals="{",".join(web.fingerprint.signals)}" '
            f'data-ingest="{web.routes["fingerprint_ingest"]}" defer></script>'
        )
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


def fingerprint_script():
    # Served from the fingerprint package, so one collector covers every profile that opts in.
    return send_from_directory(
        os.path.join(_SRC, "fingerprint", "static"),
        "collector.js",
        mimetype="application/javascript",
    )


def fingerprint_ingest():
    """Absorb one collector submission. Always answers 204, whatever the store does."""
    fingerprint_hash = None
    try:
        sink = current_app.config["FINGERPRINT_SINK"]
        fingerprint_hash = sink(
            request.get_data(cache=False),
            session_id=_http_session_id(),
            ip=request.remote_addr,
            local_port=_local_port(),
            path=_bounded(request.path),
            user_agent=_bounded(request.headers.get("User-Agent", "")) or None,
        )
    except Exception:
        # Fingerprinting must never change what the peer sees; the response below is unconditional.
        logger.exception("Fingerprint sink failed")
    _log_probe(
        "WEB_FINGERPRINT",
        params=["Stored: yes" if fingerprint_hash else "Stored: no"],
        fingerprint_hash=fingerprint_hash,
    )
    return ("", 204)


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
    # Log scans and hide Werkzeug's default page behind the spoofed identity.
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
    # Fujifilm launches the authenticated shell under WorkflowUI.
    web = _web()
    if _session_ok():
        landing = web.routes["console"] if web.browse else web.routes["worklist"]
        return redirect(landing, code=302)
    signin = secrets.token_hex(16)
    return redirect(f"{web.routes['login']}?signin={signin}", code=302)


def worklist(subpath=None):
    web = _web()
    if not _session_ok():
        return redirect(web.routes["entry"], code=302)
    _log_probe("WEB_WORKLIST_VIEW")
    return render_template(
        "worklist.html",
        studies=_worklist_studies(),
        routes=web.routes,
        browse=web.browse,
    )


# --- Browse console (profiles with web.browse; every view is session-gated) ---

# Study Root has no PATIENT level, so patients query STUDY and deduplicate by patient.
_BROWSE_LEVELS = {
    "patients": ("STUDY", ("PatientID", "PatientName"), "patient_id"),
    "studies": (
        "STUDY",
        (
            "PatientID",
            "PatientName",
            "StudyInstanceUID",
            "StudyDate",
            "StudyTime",
            "AccessionNumber",
            "StudyID",
        ),
        "study_instance_uid",
    ),
    "series": (
        "SERIES",
        (
            "PatientName",
            "StudyInstanceUID",
            "SeriesInstanceUID",
            "Modality",
            "SeriesNumber",
        ),
        "series_instance_uid",
    ),
    "instances": (
        "IMAGE",
        (
            "PatientID",
            "PatientName",
            "StudyInstanceUID",
            "SeriesInstanceUID",
            "SOPInstanceUID",
            "SOPClassUID",
            "Modality",
            "InstanceNumber",
        ),
        "sop_instance_uid",
    ),
}


def _page_number() -> int:
    try:
        max_page = (_BROWSE_MAX_OFFSET // _web().browse_page_size) + 1
        return max(1, min(int(request.args.get("page", "1")), max_page))
    except (TypeError, ValueError):
        return 1


def _page_url(page: int) -> str:
    args = request.args.to_dict(flat=True)
    args["page"] = str(page)
    return f"{request.path}?{urlencode(args)}"


def _browse_rows(level, keys, dedup_col, page, match=None):
    """Rows for a browse level via repo.find(), deduped by the level's UID (as handle_find does)."""
    repo: Repository = current_app.config["REPO"]
    model = StudyRootQueryRetrieveInformationModelFind
    ds = Dataset()
    ds.QueryRetrieveLevel = level
    for kw in keys:
        setattr(ds, kw, "")
    for kw, val in (match or {}).items():
        setattr(ds, kw, val)
    page_size = _web().browse_page_size
    result = repo.find_page(
        ds,
        model,
        dedup_col=dedup_col,
        offset=(page - 1) * page_size,
        limit=page_size + 1,
    )
    if result.error is not None:
        return [], False, result.error.error
    seen, rows = set(), []
    for m in result.matches[:page_size]:
        uid = getattr(m, dedup_col, None)
        if uid in seen:
            continue
        seen.add(uid)
        idt = m.as_identifier(ds, model)
        rows.append({kw: str(getattr(idt, kw, "") or "") for kw in keys})
    return rows, len(result.matches) > page_size, None


def _render_browse(title, keys, rows, page, has_next, *, query=None):
    return render_template(
        "browse.html",
        title=title,
        columns=keys,
        rows=rows,
        routes=_web().routes,
        query=query,
        page=page,
        prev_url=_page_url(page - 1) if page > 1 else None,
        next_url=_page_url(page + 1) if has_next else None,
    )


def console():
    if not _session_ok():
        return redirect(_web().routes["entry"], code=302)
    _log_probe("WEB_CONSOLE_VIEW")
    return render_template("console.html", routes=_web().routes)


def browse_level(level):
    if not _session_ok():
        return redirect(_web().routes["entry"], code=302)
    qr_level, keys, dedup = _BROWSE_LEVELS[level]
    page = _page_number()
    rows, has_next, error = _browse_rows(qr_level, keys, dedup, page)
    params = [f"Page: {page}"]
    if error:
        params.append(f"Error: {_bounded(error)}")
    _log_probe(
        f"WEB_BROWSE_{level.upper()}",
        params=params,
        matches=len(rows),
        level="WARNING" if error else "INFO",
    )
    return _render_browse(level.capitalize(), keys, rows, page, has_next)


def patients():
    return browse_level("patients")


def studies():
    return browse_level("studies")


def series():
    return browse_level("series")


def instances():
    return browse_level("instances")


def search():
    if not _session_ok():
        return redirect(_web().routes["entry"], code=302)
    field = "id" if request.args.get("searchType") == "id" else "name"
    query = _bounded(request.args.get("q", "").strip())
    qr_level, keys, dedup = _BROWSE_LEVELS["instances"]
    match = {"PatientID" if field == "id" else "PatientName": query} if query else None
    page = _page_number()
    rows, has_next, error = (
        _browse_rows(qr_level, keys, dedup, page, match=match)
        if query
        else ([], False, None)
    )
    params = [f"By: {field}", f"Query: {query}", f"Page: {page}"]
    if error:
        params.append(f"Error: {_bounded(error)}")
    _log_probe(
        "WEB_SEARCH",
        params=params,
        matches=len(rows),
        level="WARNING" if error else "INFO",
    )
    return _render_browse("Search results", keys, rows, page, has_next, query=query)


def upload_get():
    if not _session_ok():
        return redirect(_web().routes["entry"], code=302)
    _log_probe("WEB_UPLOAD_VIEW")
    return render_template(
        "upload.html",
        message=None,
        routes=_web().routes,
        max_files=_web().upload_max_files,
    )


def _capture_rejected_upload(repo: Repository, raw: bytes):
    try:
        return repo.storage.capture(raw, suffix=".web-upload"), None
    except Exception as exc:
        return None, str(exc)


def _validate_upload_dataset(ds: Dataset) -> str:
    try:
        sop_class = str(ds.SOPClassUID)
        sop_instance = str(ds.SOPInstanceUID)
        file_class = str(ds.file_meta.MediaStorageSOPClassUID)
        file_instance = str(ds.file_meta.MediaStorageSOPInstanceUID)
        transfer_syntax = str(ds.file_meta.TransferSyntaxUID)
    except (AttributeError, TypeError) as exc:
        raise ValueError("Part-10 identity or transfer syntax is missing") from exc
    if sop_class != file_class or sop_instance != file_instance:
        raise ValueError("File-meta and dataset SOP identity do not match")
    required = ("PatientID", "StudyInstanceUID", "SeriesInstanceUID")
    if any(not str(getattr(ds, keyword, "")) for keyword in required):
        raise ValueError("Required patient/study/series identity is missing")
    uid_values = (
        sop_class,
        sop_instance,
        str(ds.StudyInstanceUID),
        str(ds.SeriesInstanceUID),
        transfer_syntax,
    )
    if any(not UID(value).is_valid for value in uid_values):
        raise ValueError("Object contains an invalid DICOM UID")
    allowed = current_app.config["WEB_STORAGE_CLASSES"].get(sop_class)
    if allowed is None:
        raise LookupError("SOP Class is not supported")
    if transfer_syntax not in allowed:
        raise LookupError("Transfer Syntax is not supported for this SOP Class")
    return sop_instance


def upload_post():
    if not _session_ok():
        return redirect(_web().routes["entry"], code=302)
    repo: Repository = current_app.config["REPO"]
    files = request.files.getlist("dicomFiles")
    max_files = _web().upload_max_files
    omitted = max(0, len(files) - max_files)
    stored, failed = 0, omitted
    if omitted:
        _log_probe(
            "WEB_UPLOAD_LIMIT",
            params=[f"Submitted: {len(files)}", f"Rejected: {omitted}"],
            matches=0,
            level="WARNING",
        )
        for f in files[max_files:]:
            raw = f.stream.read()
            digest = hashlib.sha256(raw).hexdigest()
            capture, capture_error = _capture_rejected_upload(repo, raw)
            params = [
                f"File: {_bounded(f.filename)}",
                f"Bytes: {len(raw)}",
                f"SHA256: {digest}",
                "Rejected: file-count limit",
            ]
            if capture_error:
                params.append(f"Capture failure: {_bounded(capture_error)}")
            _log_probe(
                "WEB_UPLOAD",
                params=params,
                matches=0,
                level="WARNING",
                artifact={
                    "filename": _bounded(f.filename),
                    "bytes": len(raw),
                    "sha256": digest,
                    "artifact_id": capture.artifact_id if capture else None,
                    "sop_instance_uid": None,
                    "sop_class_uid": None,
                    "captured": capture is not None,
                    "disposition": "rejected",
                    "reject_reason": "file-count limit",
                },
            )
            if capture is not None:
                _submit_artifact(
                    capture, request_type="WEB_UPLOAD", disposition="rejected"
                )
    seen_sops = set()
    for f in files[:max_files]:
        raw = f.stream.read()
        digest = hashlib.sha256(raw).hexdigest()
        base_params = [
            f"File: {_bounded(f.filename)}",
            f"Bytes: {len(raw)}",
            f"SHA256: {digest}",
        ]
        try:
            ds = dcmread(BytesIO(raw))
            sop = _validate_upload_dataset(ds)
            if sop in seen_sops:
                raise ValueError("Duplicate SOP Instance in one request")
            seen_sops.add(sop)
        except Exception as exc:
            failed += 1
            capture, capture_error = _capture_rejected_upload(repo, raw)
            params = base_params + [f"Rejected: {_bounded(exc)}"]
            if capture_error:
                params.append(f"Capture failure: {_bounded(capture_error)}")
            _log_probe(
                "WEB_UPLOAD",
                params=params,
                matches=0,
                level="WARNING",
                artifact={
                    "filename": _bounded(f.filename),
                    "bytes": len(raw),
                    "sha256": digest,
                    "artifact_id": capture.artifact_id if capture else None,
                    "sop_instance_uid": None,
                    "sop_class_uid": None,
                    "captured": capture is not None,
                    "disposition": "rejected",
                    "reject_reason": _bounded(exc),
                },
            )
            if capture is not None:
                _submit_artifact(
                    capture, request_type="WEB_UPLOAD", disposition="rejected"
                )
            continue
        # safe=False -> quarantined; raw_bytes preserves the exact attacker payload.
        part_captures = []
        err = repo.store(ds, raw_bytes=raw, on_captured=part_captures.append)
        part_capture = part_captures[0] if part_captures else None
        if err is None:
            stored += 1
        else:
            failed += 1
        _log_probe(
            "WEB_UPLOAD",
            params=base_params
            + [f"SOPInstanceUID: {_bounded(sop)}"]
            + ([f"Rejected: {_bounded(err.error)}"] if err else []),
            matches=1 if err is None else 0,
            level="WARNING" if err else "INFO",
            artifact={
                "filename": _bounded(f.filename),
                "bytes": len(raw),
                "sha256": digest,
                "artifact_id": part_capture.artifact_id if part_capture else None,
                "sop_instance_uid": _bounded(sop),
                "sop_class_uid": _bounded(ds.SOPClassUID),
                "captured": part_capture is not None,
                "disposition": "rejected" if err else "stored",
                "reject_reason": _bounded(err.error) if err else None,
            },
        )
        if part_capture is not None:
            _submit_artifact(
                part_capture,
                request_type="WEB_UPLOAD",
                disposition="rejected" if err else "stored",
                sop_class_uid=_bounded(ds.SOPClassUID),
                sop_instance_uid=_bounded(sop),
            )
    message = (
        f"{stored} file(s) received, {failed} rejected."
        if (stored or failed)
        else "No files received."
    )
    return render_template(
        "upload.html",
        message=message,
        routes=_web().routes,
        max_files=max_files,
    )


def logout():
    _log_probe("WEB_LOGOUT")
    _revoke_session()
    resp = make_response(redirect(_web().routes["entry"], code=302))
    resp.delete_cookie(_web().cookies["session"], path="/")
    return resp


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


def new_web(
    profile: ProfileConfig,
    repo: Repository,
    bus: Logger,
    sink: ArtifactSink | None = None,
    fingerprint_sink=None,
) -> Flask:
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
    app.config["ARTIFACT_SINK"] = sink or (lambda _artifact: None)
    app.config["FINGERPRINT_SINK"] = fingerprint_sink or (lambda _body, **_kwargs: None)
    app.config["WEB_SESSIONS"] = {}
    app.config["WEB_SESSIONS_LOCK"] = threading.Lock()
    app.config["WEB_STORAGE_CLASSES"] = {
        sop: set(syntaxes) for sop, syntaxes in profile.dicom.storage_classes
    }
    app.config["MAX_CONTENT_LENGTH"] = profile.web.max_request_bytes
    app.config["MAX_FORM_PARTS"] = profile.web.upload_max_files + 10
    app.before_request(_apply_request_limit)
    app.before_request(_make_nonce)
    app.context_processor(_inject_context)
    app.after_request(_spoof)
    app.register_error_handler(404, _iis_404)
    app.register_error_handler(413, _request_too_large)
    app.register_error_handler(500, _synapse_500)

    routes = profile.web.routes
    # Profile-owned paths prevent vendor identities leaking between profiles.
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

    # A profile that never opts in gets no collector asset and no ingest endpoint at all.
    if profile.web.fingerprint.enabled:
        app.add_url_rule(
            routes["fingerprint_script"], "fingerprint_script", fingerprint_script
        )
        app.add_url_rule(
            routes["fingerprint_ingest"],
            "fingerprint_ingest",
            fingerprint_ingest,
            methods=["POST"],
        )

    # Capture-only profiles do not register browse routes.
    if profile.web.browse:
        app.add_url_rule(routes["console"], "console", console)
        app.add_url_rule(routes["patients"], "patients", patients)
        app.add_url_rule(routes["studies"], "studies", studies)
        app.add_url_rule(routes["series"], "series", series)
        app.add_url_rule(routes["instances"], "instances", instances)
        app.add_url_rule(routes["search"], "search", search, methods=["GET"])
        app.add_url_rule(routes["upload"], "upload_get", upload_get, methods=["GET"])
        app.add_url_rule(routes["upload"], "upload_post", upload_post, methods=["POST"])
        app.add_url_rule(routes["logout"], "logout", logout, methods=["POST"])

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
