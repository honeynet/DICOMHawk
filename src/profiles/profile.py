import logging
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import yaml
from pynetdicom import DEFAULT_TRANSFER_SYNTAXES
from pynetdicom.presentation import AllStoragePresentationContexts
from pynetdicom.sop_class import _QR_CLASSES, _VERIFICATION_CLASSES

logger = logging.getLogger(__name__)

_DATA_PKG = "profiles"

# (abstract_syntax_uid, [transfer_syntax_uids]) — plain tuple so core dicomhawk/ never imports this package.
type SopClass = tuple[str, list[str]]

# _QR_CLASSES class-name suffix -> operation name in `operations`/`qr_classes`.
_QR_SUFFIX_TO_OP: dict[str, str] = {"Find": "find", "Move": "move", "Get": "get"}


@dataclass
class AEAuthConfig:
    require_called_aet: bool = False
    require_calling_aet: list[str] | None = None


@dataclass
class DicomConfig:
    operations: list[str]
    verification: SopClass
    storage_classes: list[SopClass]
    qr_classes: dict[str, list[SopClass]]
    max_associations: int
    max_pdu_size: int | None # None -> pynetdicom's own default; no real value to mimic
    ae_auth: AEAuthConfig
    # Tighter than pynetdicom's 30s/60s defaults — a raw connection with no valid PDU
    # still holds a max_associations slot until these expire (DoS window).
    acse_timeout: float | None
    network_timeout: float | None


@dataclass
class WebConfig:
    enabled: bool = False
    templates_dir: str | None = None
    grant_access: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    html_cache_headers: dict[str, str] = field(default_factory=dict)
    content_security_policy: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    license: dict = field(default_factory=dict)
    oidc: dict[str, str] = field(default_factory=dict)
    favicon: str | None = None       # filename under the profile's web/static/, served by the web component
    # (path, response_kind); a profile with none declared gets no honeytrap routes at all.
    honeytraps: list[tuple[str, str]] = field(default_factory=list)
    fingerprint_script: str | None = None  # static-asset filename; Weeks 5-6 injection seam only, no collector yet
    # (username, password) bait pairs; using one grants access unconditionally (see login_post).
    honey_credentials: list[tuple[str, str]] = field(default_factory=list)
    # URL paths for every route the engine serves; keeps one profile's identity out of another's address bar.
    routes: dict[str, str] = field(default_factory=dict)
    # Cookie names the engine sets; same isolation reasoning as routes.
    cookies: dict[str, str] = field(default_factory=dict)
    winauth_messages: dict[str, str] = field(default_factory=dict)  # text1/text2/text3 for the WinAuth translation fetch


@dataclass
class ProfileConfig:
    name: str
    kind: str
    ae_title: str
    implementation_class_uid: str | None
    implementation_version_name: str | None
    manufacturer: str | None
    model_name: str | None
    dicom: DicomConfig
    web: WebConfig


def default_profile() -> ProfileConfig:
    """Today's pre-profile behavior, expressed as a ProfileConfig."""
    storage_classes: list[SopClass] = [
        (ctx.abstract_syntax, DEFAULT_TRANSFER_SYNTAXES)
        for ctx in AllStoragePresentationContexts
        if ctx.abstract_syntax is not None
    ]

    qr_classes: dict[str, list[SopClass]] = {"find": [], "move": [], "get": []}
    for name, uid in _QR_CLASSES.items():
        for suffix, op in _QR_SUFFIX_TO_OP.items():
            if name.endswith(suffix):
                qr_classes[op].append((uid, DEFAULT_TRANSFER_SYNTAXES))
                break
        # else: RepositoryQuery has no Find/Move/Get suffix — deliberately excluded.

    return ProfileConfig(
        name="default",
        kind="dicom",
        ae_title="ORTHANC",
        implementation_class_uid=None,
        implementation_version_name=None,
        manufacturer=None,
        model_name=None,
        dicom=DicomConfig(
            operations=["echo", "find", "get", "move", "store"],
            verification=(_VERIFICATION_CLASSES["Verification"], DEFAULT_TRANSFER_SYNTAXES),
            storage_classes=storage_classes,
            qr_classes=qr_classes,
            max_associations=100,
            max_pdu_size=65536, # not pynetdicom's DEFAULT_MAX_LENGTH=16382 — avoids rejecting large-PDU clients
            ae_auth=AEAuthConfig(),
            acse_timeout=10, # tighter than pynetdicom's 30s default — shrinks a garbage connection's DoS window
            network_timeout=15, # tighter than pynetdicom's 60s default, same reason
        ),
        web=WebConfig(
            # Generic fallback content — real values for any pacs profile that omits
            # its own web.* keys, so a sparse custom profile can't crash the engine.
            headers={
                "Server": "Apache",
                "X-Frame-Options": "SAMEORIGIN",
                "X-Content-Type-Options": "nosniff",
            },
            html_cache_headers={
                "Cache-Control": "no-store, no-cache, max-age=0, private",
                "Pragma": "no-cache",
            },
            content_security_policy="default-src 'self'; script-src 'nonce-{nonce}' 'self'; style-src 'self' 'unsafe-inline'",
            identity={"version": "1.0", "copyright": ""},
            license={"issued": "", "lines": []},
            oidc={"client_id": "", "client_name": "", "redirect_path": "/", "scopes": ""},
            # Generic, non-vendor-specific route/cookie names — never "Synapse"-shaped,
            # so a profile that doesn't override these can't leak Fujifilm's identity.
            routes={
                "entry": "/portal",
                "login": "/portal/login",
                "winauth": "/portal/winauth",
                "forgot_password": "/portal/forgot-password",
                "sts_error": "/portal/error",
                "sts_authorize": "/portal/authorize",
                "csp_report": "/portal/csp-report",
                "translated_items": "/portal/translations",
            },
            cookies={
                "antiforgery": "portal.xsrf",
                "session": "portal_authed",
                "signin_message_prefix": "PortalSignIn.",
                "nonce_prefix": "PortalNonce.",
                "idp": "PortalIdp",
                "idp_token": "PortalIdpToken",
                "winlogin_origurl": "PortalWinOrigUrl",
            },
            winauth_messages={
                "text1": "Portal Log On",
                "text2": "Unable to log in using Windows Authentication.",
                "text3": "Log in directly",
            },
        ),
    )


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ValueError(f"Profile missing required key '{key}' in {where}")
    return d[key]


def _parse_sop_class(entry: dict, where: str) -> SopClass:
    uid = _require(entry, "uid", where)
    ts = _require(entry, "transfer_syntaxes", where)
    return (str(uid), [str(t) for t in ts])


def _parse_profile(data: dict) -> ProfileConfig:
    """Overlay a partial profile YAML onto default_profile(); missing keys use defaults, malformed entries raise."""
    d = default_profile()
    if not isinstance(data, dict):
        data = {}
    meta = data.get("meta") or {}
    identity = data.get("identity") or {}
    dicom_raw = data.get("dicom") or {}
    web_raw = data.get("web") or {}

    fell_back: list[str] = []

    def dget(section: dict, key: str, default, label: str):
        if key in section:
            return section[key]
        fell_back.append(label)
        return default

    ae_title = dget(identity, "ae_title", d.ae_title, "identity.ae_title")
    operations = list(dget(dicom_raw, "operations", d.dicom.operations, "dicom.operations"))
    max_associations = int(dicom_raw.get("max_associations", d.dicom.max_associations))

    if "verification" in dicom_raw:
        verification = _parse_sop_class(dicom_raw["verification"], "dicom.verification")
    else:
        verification = d.dicom.verification
        fell_back.append("dicom.verification")

    if "storage_classes" in dicom_raw:
        storage_classes = [_parse_sop_class(e, "dicom.storage_classes")
                           for e in dicom_raw["storage_classes"]]
    else:
        storage_classes = d.dicom.storage_classes
        fell_back.append("dicom.storage_classes")

    if "qr_classes" in dicom_raw:
        qr_classes = {op: [_parse_sop_class(e, f"dicom.qr_classes.{op}") for e in entries]
                      for op, entries in dicom_raw["qr_classes"].items()}
    else:
        qr_classes = d.dicom.qr_classes
        fell_back.append("dicom.qr_classes")

    if "max_pdu_size" in dicom_raw:
        v = dicom_raw["max_pdu_size"]
        max_pdu_size = None if v is None else int(v)
    else:
        max_pdu_size = d.dicom.max_pdu_size

    if "acse_timeout" in dicom_raw:
        v = dicom_raw["acse_timeout"]
        acse_timeout = None if v is None else float(v)
    else:
        acse_timeout = d.dicom.acse_timeout

    if "network_timeout" in dicom_raw:
        v = dicom_raw["network_timeout"]
        network_timeout = None if v is None else float(v)
    else:
        network_timeout = d.dicom.network_timeout

    ae_auth_raw = dicom_raw.get("ae_auth") or {}
    ae_auth = AEAuthConfig(
        require_called_aet=bool(ae_auth_raw.get("require_called_aet", False)),
        require_calling_aet=ae_auth_raw.get("require_calling_aet"),
    )

    name = meta.get("name", d.name)

    web_enabled = bool(web_raw.get("enabled", False))
    if web_enabled and not web_raw.get("templates_dir"):
        raise ValueError(
            f"Profile '{name}' has web.enabled=true but no web.templates_dir "
            "(there's no generic fallback for a template directory that doesn't exist)"
        )
    if web_enabled:
        # Only worth reporting if the web component will actually run with these values.
        for key in ("headers", "html_cache_headers", "content_security_policy",
                    "identity", "license", "oidc", "routes", "cookies", "winauth_messages"):
            if key not in web_raw:
                fell_back.append(f"web.{key}")

    if fell_back:
        logger.warning("Profile '%s' missing keys; using defaults for: %s",
                       name, ", ".join(fell_back))

    if "honeytraps" in web_raw:
        honeytraps = [
            (str(_require(h, "path", "web.honeytraps")), str(_require(h, "response", "web.honeytraps")))
            for h in web_raw["honeytraps"]
        ]
    else:
        honeytraps = d.web.honeytraps

    if "honey_credentials" in web_raw:
        honey_credentials = [
            (str(_require(c, "username", "web.honey_credentials")),
             str(_require(c, "password", "web.honey_credentials")))
            for c in web_raw["honey_credentials"]
        ]
    else:
        honey_credentials = d.web.honey_credentials

    return ProfileConfig(
        name=name,
        kind=meta.get("kind", d.kind),
        ae_title=ae_title,
        implementation_class_uid=identity.get("implementation_class_uid", d.implementation_class_uid),
        implementation_version_name=identity.get("implementation_version_name", d.implementation_version_name),
        manufacturer=identity.get("manufacturer", d.manufacturer),
        model_name=identity.get("model_name", d.model_name),
        dicom=DicomConfig(
            operations=operations,
            verification=verification,
            storage_classes=storage_classes,
            qr_classes=qr_classes,
            max_associations=max_associations,
            max_pdu_size=max_pdu_size,
            ae_auth=ae_auth,
            acse_timeout=acse_timeout,
            network_timeout=network_timeout,
        ),
        web=WebConfig(
            enabled=web_enabled,
            templates_dir=web_raw.get("templates_dir"),
            grant_access=bool(web_raw.get("grant_access", False)),
            # Per-key overlay (a profile can override just one header/oidc key), like overlay_config().
            headers={**d.web.headers, **(web_raw.get("headers") or {})},
            html_cache_headers={**d.web.html_cache_headers, **(web_raw.get("html_cache_headers") or {})},
            content_security_policy=web_raw.get("content_security_policy", d.web.content_security_policy),
            identity={**d.web.identity, **(web_raw.get("identity") or {})},
            license={**d.web.license, **(web_raw.get("license") or {})},
            oidc={**d.web.oidc, **(web_raw.get("oidc") or {})},
            favicon=web_raw.get("favicon"),
            honeytraps=honeytraps,
            fingerprint_script=web_raw.get("fingerprint_script"),
            honey_credentials=honey_credentials,
            routes={**d.web.routes, **(web_raw.get("routes") or {})},
            cookies={**d.web.cookies, **(web_raw.get("cookies") or {})},
            winauth_messages={**d.web.winauth_messages, **(web_raw.get("winauth_messages") or {})},
        ),
    )


def load_profile(source: str | None) -> ProfileConfig:
    """None/"" -> default_profile(); a file path -> load it; else -> bundled profiles/<source>/<source>.yaml."""
    if not source:  # None or "" (e.g. an unset DICOMHAWK_PROFILE env in compose)
        return default_profile()

    path = Path(source)
    if path.is_file():
        text = path.read_text()
    else:
        try:
            text = files(_DATA_PKG).joinpath(source, f"{source}.yaml").read_text()
        except (FileNotFoundError, ModuleNotFoundError):
            raise FileNotFoundError(
                f"No such profile: '{source}' (not a file, and no bundled {_DATA_PKG}/{source}/{source}.yaml)"
            )

    return _parse_profile(yaml.safe_load(text))
