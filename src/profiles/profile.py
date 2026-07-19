import logging
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
import yaml
from pydicom.uid import UID
from pynetdicom import DEFAULT_TRANSFER_SYNTAXES
from pynetdicom.presentation import AllStoragePresentationContexts
from pynetdicom.sop_class import _QR_CLASSES, _VERIFICATION_CLASSES

logger = logging.getLogger(__name__)

_DATA_PKG = "profiles"
_OPERATIONS = frozenset({"echo", "find", "get", "move", "store"})
_HONEYTRAP_RESPONSES = frozenset({"login_redirect", "api_404", "unauthorized_page"})
_DICOMWEB_SERVICES = frozenset({"qido", "wado_rs", "stow", "wado_uri"})
_REQUIRED_TEMPLATES = frozenset(
    {
        "login.html",
        "forgot_password.html",
        "error.html",
        "winauth_unable.html",
        "worklist.html",
    }
)
_BROWSE_TEMPLATES = frozenset({"console.html", "browse.html", "upload.html"})

# (abstract_syntax_uid, [transfer_syntax_uids]) — plain tuple so core dicomhawk/ never imports this package.
type SopClass = tuple[str, list[str]]

# _QR_CLASSES class-name suffix -> operation name in `operations`/`qr_classes`.
_QR_SUFFIX_TO_OP: dict[str, str] = {"Find": "find", "Move": "move", "Get": "get"}
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


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
    max_pdu_size: int | None  # None -> pynetdicom's own default; no real value to mimic
    ae_auth: AEAuthConfig
    # Limit how long a silent peer occupies an association slot.
    acse_timeout: float | None
    network_timeout: float | None
    max_store_bytes: int | None


@dataclass
class WebConfig:
    enabled: bool = False
    templates_dir: str | None = None
    grant_access: bool = False
    # Post-login DICOM browse console (patients/studies/series/instances/upload).
    browse: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    html_cache_headers: dict[str, str] = field(default_factory=dict)
    content_security_policy: str | None = None
    legacy_csp_header: bool = False
    secure_cookies: bool = False
    identity: dict[str, str] = field(default_factory=dict)
    license: dict = field(default_factory=dict)
    oidc: dict[str, str] = field(default_factory=dict)
    favicon: str | None = (
        None  # filename under the profile's web/static/, served by the web component
    )
    # (path, response_kind); a profile with none declared gets no honeytrap routes at all.
    honeytraps: list[tuple[str, str]] = field(default_factory=list)
    fingerprint_script: str | None = (
        None  # static-asset filename; Weeks 5-6 injection seam only, no collector yet
    )
    # (username, password) bait pairs; using one grants access unconditionally (see login_post).
    honey_credentials: list[tuple[str, str]] = field(default_factory=list)
    # URL paths for every route the engine serves; keeps one profile's identity out of another's address bar.
    routes: dict[str, str] = field(default_factory=dict)
    # Cookie names the engine sets; same isolation reasoning as routes.
    cookies: dict[str, str] = field(default_factory=dict)
    winauth_messages: dict[str, str] = field(
        default_factory=dict
    )  # text1/text2/text3 for the WinAuth translation fetch
    max_request_bytes: int = 1_048_576
    upload_max_request_bytes: int = 50 * 1024 * 1024
    upload_max_files: int = 10
    browse_page_size: int = 100
    assets_dir: str | None = None
    # Deployment topology comes from CLI/env, never profile YAML.
    public_base_url: str | None = None


@dataclass
class DicomWebService:
    # One DICOMweb service bound to a port; several may share a port+base_path (generic /dicom-web/).
    kind: str  # qido | wado_rs | stow | wado_uri
    base_path: str
    port: int


@dataclass
class DicomWebConfig:
    enabled: bool = False
    # Per-service port/base-path is profile data, so one profile's paths never leak into another's.
    services: list[DicomWebService] = field(default_factory=list)
    require_auth: list[str] = field(
        default_factory=list
    )  # service kinds that issue a WinAuth 401 challenge before serving
    qido_max_results: int = 20000  # cap on QIDO result items
    max_request_bytes: int = 512 * 1024 * 1024  # STOW upload body cap
    max_non_stow_request_bytes: int = 1024 * 1024
    max_stow_parts: int = 128
    qido_default_media_type: str = "application/dicom+json"
    default_transfer_syntax: str = "1.2.840.10008.1.2.1"
    auth_schemes: list[str] = field(default_factory=lambda: ["Basic"])
    qido_warning_agent: str = (
        "-"  # agent token in the fuzzymatching Warning header; keep it product-neutral
    )


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
    dicomweb: DicomWebConfig


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
            verification=(
                _VERIFICATION_CLASSES["Verification"],
                DEFAULT_TRANSFER_SYNTAXES,
            ),
            storage_classes=storage_classes,
            qr_classes=qr_classes,
            max_associations=100,
            max_pdu_size=65536,  # not pynetdicom's DEFAULT_MAX_LENGTH=16382 — avoids rejecting large-PDU clients
            ae_auth=AEAuthConfig(),
            acse_timeout=10,  # tighter than pynetdicom's 30s default — shrinks a garbage connection's DoS window
            network_timeout=15,  # tighter than pynetdicom's 60s default, same reason
            max_store_bytes=512 * 1024 * 1024,
        ),
        web=WebConfig(
            # Working vendor-neutral defaults for sparse PACS profiles.
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
            oidc={
                "client_id": "",
                "client_name": "",
                "redirect_path": "/",
                "scopes": "",
            },
            # Vendor-neutral paths prevent identity leaking into sparse profiles.
            routes={
                "entry": "/portal",
                "worklist": "/portal/worklist",
                "login": "/portal/login",
                "winauth": "/portal/winauth",
                "forgot_password": "/portal/forgot-password",
                "sts_error": "/portal/error",
                "sts_authorize": "/portal/authorize",
                "csp_report": "/portal/csp-report",
                "translated_items": "/portal/translations",
                # Browse console (only registered when web.browse is on); generic /portal/* paths.
                "console": "/portal/console",
                "patients": "/portal/patients",
                "studies": "/portal/studies",
                "series": "/portal/series",
                "instances": "/portal/instances",
                "search": "/portal/search",
                "upload": "/portal/upload",
                "logout": "/portal/logout",
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
        # Generic single-port /dicom-web/ fallback; a profile opts in and inherits it.
        dicomweb=DicomWebConfig(
            enabled=False,
            services=[
                DicomWebService("qido", "/dicom-web", 8042),
                DicomWebService("wado_rs", "/dicom-web", 8042),
                DicomWebService("stow", "/dicom-web", 8042),
            ],
        ),
    )


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ValueError(f"Profile missing required key '{key}' in {where}")
    return d[key]


def _mapping(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Profile section '{where}' must be a mapping")
    return value


def _number(value, where: str, converter, *, nullable: bool = False):
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Profile '{where}' must be numeric")
    try:
        return converter(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Profile '{where}' must be numeric") from exc


def _parse_sop_class(entry: dict, where: str) -> SopClass:
    if not isinstance(entry, dict):
        raise ValueError(f"Profile entry '{where}' must be a mapping")
    uid = _require(entry, "uid", where)
    ts = _require(entry, "transfer_syntaxes", where)
    if not isinstance(ts, list) or not ts:
        raise ValueError(
            f"Profile '{where}.transfer_syntaxes' must be a non-empty list"
        )
    uid_text = str(uid)
    transfer_syntaxes = [str(t) for t in ts]
    if not UID(uid_text).is_valid or any(
        not UID(t).is_valid for t in transfer_syntaxes
    ):
        raise ValueError(f"Profile entry '{where}' contains an invalid DICOM UID")
    return (uid_text, transfer_syntaxes)


def _resolve_web_assets(
    templates_dir: str, source_dir: Path | None, honeytraps, browse: bool
) -> str:
    requested = Path(templates_dir)
    candidates: list[Path] = []
    if requested.is_absolute():
        candidates.extend((requested, requested / "web"))
    elif source_dir is not None:
        candidates.extend((source_dir / requested / "web", source_dir / "web"))
    try:
        candidates.append(Path(str(files(_DATA_PKG).joinpath(templates_dir, "web"))))
    except ModuleNotFoundError:
        pass

    required = set(_REQUIRED_TEMPLATES)
    if any(kind == "unauthorized_page" for _, kind in honeytraps):
        required.add("unauthorized.html")
    if browse:
        required.update(_BROWSE_TEMPLATES)
    for candidate in candidates:
        template_dir = candidate / "templates"
        if candidate.is_dir() and all(
            (template_dir / name).is_file() for name in required
        ):
            return str(candidate.resolve())
    raise ValueError(
        f"Profile web templates not found for '{templates_dir}' "
        f"(required: {', '.join(sorted(required))})"
    )


def _parse_profile(data: dict, source_dir: Path | None = None) -> ProfileConfig:
    """Overlay a partial profile YAML onto default_profile(); missing keys use defaults, malformed entries raise."""
    d = default_profile()
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must contain a top-level mapping")
    meta = _mapping(data.get("meta"), "meta")
    identity = _mapping(data.get("identity"), "identity")
    dicom_raw = _mapping(data.get("dicom"), "dicom")
    web_raw = _mapping(data.get("web"), "web")

    fell_back: list[str] = []

    def dget(section: dict, key: str, default, label: str):
        if key in section:
            return section[key]
        fell_back.append(label)
        return default

    ae_title = dget(identity, "ae_title", d.ae_title, "identity.ae_title")
    operations_raw = dget(
        dicom_raw, "operations", d.dicom.operations, "dicom.operations"
    )
    if not isinstance(operations_raw, list) or not operations_raw:
        raise ValueError("Profile 'dicom.operations' must be a non-empty list")
    operations = [str(op).lower() for op in operations_raw]
    unknown_operations = set(operations) - _OPERATIONS
    if unknown_operations:
        raise ValueError(
            f"Profile has unknown DICOM operations: {', '.join(sorted(unknown_operations))}"
        )
    if len(set(operations)) != len(operations):
        raise ValueError("Profile 'dicom.operations' must not contain duplicates")
    max_associations = _number(
        dicom_raw.get("max_associations", d.dicom.max_associations),
        "dicom.max_associations",
        int,
    )
    if max_associations < 1:
        raise ValueError("Profile 'dicom.max_associations' must be at least 1")

    if "verification" in dicom_raw:
        verification = _parse_sop_class(dicom_raw["verification"], "dicom.verification")
    else:
        verification = d.dicom.verification
        fell_back.append("dicom.verification")

    if "storage_classes" in dicom_raw:
        if not isinstance(dicom_raw["storage_classes"], list):
            raise ValueError("Profile 'dicom.storage_classes' must be a list")
        storage_classes = [
            _parse_sop_class(e, "dicom.storage_classes")
            for e in dicom_raw["storage_classes"]
        ]
    else:
        storage_classes = d.dicom.storage_classes
        fell_back.append("dicom.storage_classes")

    if "qr_classes" in dicom_raw:
        qr_raw = _mapping(dicom_raw["qr_classes"], "dicom.qr_classes")
        unknown_qr = set(qr_raw) - {"find", "move", "get"}
        if unknown_qr:
            raise ValueError(
                f"Profile has unknown Q/R groups: {', '.join(sorted(unknown_qr))}"
            )
        qr_classes = {**d.dicom.qr_classes}
        for op, entries in qr_raw.items():
            if not isinstance(entries, list):
                raise ValueError(f"Profile 'dicom.qr_classes.{op}' must be a list")
            qr_classes[op] = [
                _parse_sop_class(e, f"dicom.qr_classes.{op}") for e in entries
            ]
    else:
        qr_classes = d.dicom.qr_classes
        fell_back.append("dicom.qr_classes")

    if "max_pdu_size" in dicom_raw:
        v = dicom_raw["max_pdu_size"]
        max_pdu_size = _number(v, "dicom.max_pdu_size", int, nullable=True)
    else:
        max_pdu_size = d.dicom.max_pdu_size
    if max_pdu_size is not None and max_pdu_size < 1:
        raise ValueError("Profile 'dicom.max_pdu_size' must be positive or null")

    if "acse_timeout" in dicom_raw:
        v = dicom_raw["acse_timeout"]
        acse_timeout = _number(v, "dicom.acse_timeout", float, nullable=True)
    else:
        acse_timeout = d.dicom.acse_timeout
    if acse_timeout is not None and acse_timeout <= 0:
        raise ValueError("Profile 'dicom.acse_timeout' must be positive or null")

    if "network_timeout" in dicom_raw:
        v = dicom_raw["network_timeout"]
        network_timeout = _number(v, "dicom.network_timeout", float, nullable=True)
    else:
        network_timeout = d.dicom.network_timeout
    if network_timeout is not None and network_timeout <= 0:
        raise ValueError("Profile 'dicom.network_timeout' must be positive or null")

    max_store_bytes = _number(
        dicom_raw.get("max_store_bytes", d.dicom.max_store_bytes),
        "dicom.max_store_bytes",
        int,
        nullable=True,
    )
    if max_store_bytes is not None and max_store_bytes < 1:
        raise ValueError("Profile 'dicom.max_store_bytes' must be positive or null")

    ae_auth_raw = _mapping(dicom_raw.get("ae_auth"), "dicom.ae_auth")
    if "require_called_aet" in ae_auth_raw and not isinstance(
        ae_auth_raw["require_called_aet"], bool
    ):
        raise ValueError("Profile 'dicom.ae_auth.require_called_aet' must be boolean")
    require_calling_aet = ae_auth_raw.get("require_calling_aet")
    if require_calling_aet is not None and not isinstance(require_calling_aet, list):
        raise ValueError(
            "Profile 'dicom.ae_auth.require_calling_aet' must be a list or null"
        )
    if require_calling_aet and any(
        not str(aet).strip()
        or len(str(aet)) > 16
        or any(ord(ch) < 32 or ord(ch) > 126 for ch in str(aet))
        for aet in require_calling_aet
    ):
        raise ValueError("Every required calling AE title must contain 1-16 characters")
    ae_auth = AEAuthConfig(
        require_called_aet=bool(ae_auth_raw.get("require_called_aet", False)),
        require_calling_aet=(
            [str(aet) for aet in require_calling_aet] if require_calling_aet else None
        ),
    )

    name = str(meta.get("name", d.name))
    kind = str(meta.get("kind", d.kind))
    if kind not in {"dicom", "pacs"}:
        raise ValueError("Profile 'meta.kind' must be 'dicom' or 'pacs'")
    if not name.strip():
        raise ValueError("Profile 'meta.name' must not be empty")
    ae_title = str(ae_title)
    if (
        not ae_title.strip()
        or len(ae_title) > 16
        or any(ord(ch) < 32 or ord(ch) > 126 for ch in ae_title)
    ):
        raise ValueError("Profile 'identity.ae_title' must contain 1-16 characters")

    implementation_class_uid = identity.get(
        "implementation_class_uid", d.implementation_class_uid
    )
    if implementation_class_uid is not None:
        implementation_class_uid = str(implementation_class_uid)
        if not UID(implementation_class_uid).is_valid:
            raise ValueError(
                "Profile 'identity.implementation_class_uid' is not a valid DICOM UID"
            )
    implementation_version_name = identity.get(
        "implementation_version_name", d.implementation_version_name
    )
    if implementation_version_name is not None:
        implementation_version_name = str(implementation_version_name)
        if not implementation_version_name or len(implementation_version_name) > 16:
            raise ValueError(
                "Profile 'identity.implementation_version_name' must contain 1-16 characters"
            )

    for operation in operations:
        if operation in {"store", "get"} and not storage_classes:
            raise ValueError(
                f"Profile enables '{operation}' but has no storage classes"
            )
        if operation in {"find", "move", "get"} and not qr_classes[operation]:
            raise ValueError(
                f"Profile enables '{operation}' but has no Q/R classes for it"
            )

    if "enabled" in web_raw and not isinstance(web_raw["enabled"], bool):
        raise ValueError("Profile 'web.enabled' must be boolean")
    if "grant_access" in web_raw and not isinstance(web_raw["grant_access"], bool):
        raise ValueError("Profile 'web.grant_access' must be boolean")
    if "browse" in web_raw and not isinstance(web_raw["browse"], bool):
        raise ValueError("Profile 'web.browse' must be boolean")
    if "legacy_csp_header" in web_raw and not isinstance(
        web_raw["legacy_csp_header"], bool
    ):
        raise ValueError("Profile 'web.legacy_csp_header' must be boolean")
    if "secure_cookies" in web_raw and not isinstance(web_raw["secure_cookies"], bool):
        raise ValueError("Profile 'web.secure_cookies' must be boolean")
    web_enabled = web_raw.get("enabled", False)
    if web_enabled and not web_raw.get("templates_dir"):
        raise ValueError(
            f"Profile '{name}' has web.enabled=true but no web.templates_dir "
            "(there's no generic fallback for a template directory that doesn't exist)"
        )
    if web_enabled:
        # Only worth reporting if the web component will actually run with these values.
        for key in (
            "headers",
            "html_cache_headers",
            "content_security_policy",
            "identity",
            "license",
            "oidc",
            "routes",
            "cookies",
            "winauth_messages",
        ):
            if key not in web_raw:
                fell_back.append(f"web.{key}")

    dicomweb_raw = _mapping(data.get("dicomweb"), "dicomweb")
    if "enabled" in dicomweb_raw and not isinstance(dicomweb_raw["enabled"], bool):
        raise ValueError("Profile 'dicomweb.enabled' must be boolean")
    dicomweb_enabled = bool(dicomweb_raw.get("enabled", False))
    if "services" in dicomweb_raw:
        if not isinstance(dicomweb_raw["services"], list):
            raise ValueError("Profile 'dicomweb.services' must be a list")
        dicomweb_services = []
        for s in dicomweb_raw["services"]:
            item = _mapping(s, "dicomweb.services")
            port = _number(
                _require(item, "port", "dicomweb.services"),
                "dicomweb.services.port",
                int,
            )
            if not 1 <= port <= 65535:
                raise ValueError("Profile 'dicomweb.services.port' must be 1-65535")
            dicomweb_services.append(
                DicomWebService(
                    kind=str(_require(item, "service", "dicomweb.services")),
                    base_path=str(_require(item, "base_path", "dicomweb.services")),
                    port=port,
                )
            )
    else:
        dicomweb_services = d.dicomweb.services
        if dicomweb_enabled:
            fell_back.append("dicomweb.services")
    for svc in dicomweb_services:
        if svc.kind not in _DICOMWEB_SERVICES:
            raise ValueError(f"Profile has unknown dicomweb service: {svc.kind}")
        if (
            not svc.base_path.startswith("/")
            or svc.base_path.startswith("//")
            or "?" in svc.base_path
            or "#" in svc.base_path
            or "\\" in svc.base_path
            or any(part in {".", ".."} for part in svc.base_path.split("/"))
            or any(ch.isspace() for ch in svc.base_path)
            or any(ord(ch) < 32 for ch in svc.base_path)
        ):
            raise ValueError(
                f"Profile 'dicomweb.services' base_path must be an absolute URL path: {svc.base_path}"
            )
    kinds = [svc.kind for svc in dicomweb_services]
    if len(set(kinds)) != len(kinds):
        raise ValueError("Profile 'dicomweb.services' must not repeat a service kind")
    if dicomweb_enabled and not dicomweb_services:
        raise ValueError("Profile has dicomweb.enabled=true but no dicomweb.services")
    if "require_auth" in dicomweb_raw:
        if not isinstance(dicomweb_raw["require_auth"], list):
            raise ValueError("Profile 'dicomweb.require_auth' must be a list")
        dicomweb_require_auth = [str(x) for x in dicomweb_raw["require_auth"]]
    else:
        dicomweb_require_auth = d.dicomweb.require_auth
    if unknown_auth := set(dicomweb_require_auth) - set(kinds):
        raise ValueError(
            f"Profile 'dicomweb.require_auth' names services not enabled: {', '.join(sorted(unknown_auth))}"
        )
    qido_max_results = _number(
        dicomweb_raw.get("qido_max_results", d.dicomweb.qido_max_results),
        "dicomweb.qido_max_results",
        int,
    )
    dicomweb_max_request_bytes = _number(
        dicomweb_raw.get("max_request_bytes", d.dicomweb.max_request_bytes),
        "dicomweb.max_request_bytes",
        int,
    )
    dicomweb_max_non_stow_request_bytes = _number(
        dicomweb_raw.get(
            "max_non_stow_request_bytes", d.dicomweb.max_non_stow_request_bytes
        ),
        "dicomweb.max_non_stow_request_bytes",
        int,
    )
    dicomweb_max_stow_parts = _number(
        dicomweb_raw.get("max_stow_parts", d.dicomweb.max_stow_parts),
        "dicomweb.max_stow_parts",
        int,
    )
    if (
        min(
            qido_max_results,
            dicomweb_max_request_bytes,
            dicomweb_max_non_stow_request_bytes,
            dicomweb_max_stow_parts,
        )
        < 1
    ):
        raise ValueError(
            "Profile DICOMweb size, item, and part limits must be positive"
        )
    qido_default_media_type = str(
        dicomweb_raw.get("qido_default_media_type", d.dicomweb.qido_default_media_type)
    ).lower()
    if qido_default_media_type not in {"application/json", "application/dicom+json"}:
        raise ValueError(
            "Profile 'dicomweb.qido_default_media_type' must be application/json "
            "or application/dicom+json"
        )
    default_transfer_syntax = str(
        dicomweb_raw.get("default_transfer_syntax", d.dicomweb.default_transfer_syntax)
    )
    transfer_syntax_uid = UID(default_transfer_syntax)
    if not transfer_syntax_uid.is_valid or not transfer_syntax_uid.is_transfer_syntax:
        raise ValueError(
            "Profile 'dicomweb.default_transfer_syntax' must be a transfer syntax UID"
        )
    auth_schemes_raw = dicomweb_raw.get("auth_schemes", d.dicomweb.auth_schemes)
    if not isinstance(auth_schemes_raw, list) or not auth_schemes_raw:
        raise ValueError("Profile 'dicomweb.auth_schemes' must be a non-empty list")
    auth_schemes = [str(x) for x in auth_schemes_raw]
    if any(x not in {"Basic", "Negotiate", "NTLM"} for x in auth_schemes):
        raise ValueError(
            "Profile 'dicomweb.auth_schemes' contains an unsupported scheme"
        )
    if len(set(auth_schemes)) != len(auth_schemes):
        raise ValueError("Profile 'dicomweb.auth_schemes' must not contain duplicates")
    qido_warning_agent = str(
        dicomweb_raw.get("qido_warning_agent", d.dicomweb.qido_warning_agent)
    )
    if not qido_warning_agent or any(
        c.isspace() or c == '"' for c in qido_warning_agent
    ):
        raise ValueError("Profile 'dicomweb.qido_warning_agent' must be a single token")

    if fell_back:
        logger.warning(
            "Profile '%s' missing keys; using defaults for: %s",
            name,
            ", ".join(fell_back),
        )

    if "honeytraps" in web_raw:
        if not isinstance(web_raw["honeytraps"], list):
            raise ValueError("Profile 'web.honeytraps' must be a list")
        honeytraps = [
            (
                str(_require(item, "path", "web.honeytraps")),
                str(_require(item, "response", "web.honeytraps")),
            )
            for h in web_raw["honeytraps"]
            for item in [_mapping(h, "web.honeytraps")]
        ]
    else:
        honeytraps = d.web.honeytraps
    for path, response in honeytraps:
        if not path.startswith("/"):
            raise ValueError(f"Honeytrap path must start with '/': {path}")
        if response not in _HONEYTRAP_RESPONSES:
            raise ValueError(f"Unknown honeytrap response: {response}")

    if "honey_credentials" in web_raw:
        if not isinstance(web_raw["honey_credentials"], list):
            raise ValueError("Profile 'web.honey_credentials' must be a list")
        honey_credentials = [
            (
                str(_require(item, "username", "web.honey_credentials")),
                str(_require(item, "password", "web.honey_credentials")),
            )
            for c in web_raw["honey_credentials"]
            for item in [_mapping(c, "web.honey_credentials")]
        ]
    else:
        honey_credentials = d.web.honey_credentials

    def web_dict(key: str, default: dict) -> dict:
        return {**default, **_mapping(web_raw.get(key), f"web.{key}")}

    headers = web_dict("headers", d.web.headers)
    html_cache_headers = web_dict("html_cache_headers", d.web.html_cache_headers)
    web_identity = web_dict("identity", d.web.identity)
    license_data = web_dict("license", d.web.license)
    oidc = web_dict("oidc", d.web.oidc)
    routes = web_dict("routes", d.web.routes)
    cookies = web_dict("cookies", d.web.cookies)
    winauth_messages = web_dict("winauth_messages", d.web.winauth_messages)
    if any(
        not isinstance(path, str)
        or not path.startswith("/")
        or "?" in path
        or "#" in path
        or any(ord(ch) < 32 for ch in path)
        for path in routes.values()
    ):
        raise ValueError("Every 'web.routes' value must be an absolute URL path")
    if len(set(routes.values())) != len(routes):
        raise ValueError("Profile 'web.routes' values must be unique")
    if any(
        not isinstance(cookie, str) or not cookie or not _COOKIE_NAME.fullmatch(cookie)
        for cookie in cookies.values()
    ):
        raise ValueError("Every 'web.cookies' value must be a non-empty string")
    if not isinstance(license_data.get("lines"), list) or any(
        not isinstance(line, str) for line in license_data["lines"]
    ):
        raise ValueError("Profile 'web.license.lines' must be a list")
    for section_name, values in (
        ("headers", headers),
        ("html_cache_headers", html_cache_headers),
    ):
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or "\n" in key
            or "\r" in key
            or "\n" in value
            or "\r" in value
            for key, value in values.items()
        ):
            raise ValueError(
                f"Profile 'web.{section_name}' keys and values must be single-line strings"
            )

    content_security_policy = web_raw.get(
        "content_security_policy", d.web.content_security_policy
    )
    if content_security_policy is not None and not isinstance(
        content_security_policy, str
    ):
        raise ValueError(
            "Profile 'web.content_security_policy' must be a string or null"
        )
    max_request_bytes = _number(
        web_raw.get("max_request_bytes", d.web.max_request_bytes),
        "web.max_request_bytes",
        int,
    )
    if max_request_bytes < 1:
        raise ValueError("Profile 'web.max_request_bytes' must be positive")
    upload_max_request_bytes = _number(
        web_raw.get("upload_max_request_bytes", d.web.upload_max_request_bytes),
        "web.upload_max_request_bytes",
        int,
    )
    browse_page_size = _number(
        web_raw.get("browse_page_size", d.web.browse_page_size),
        "web.browse_page_size",
        int,
    )
    upload_max_files = _number(
        web_raw.get("upload_max_files", d.web.upload_max_files),
        "web.upload_max_files",
        int,
    )
    if upload_max_request_bytes < 1:
        raise ValueError("Profile 'web.upload_max_request_bytes' must be positive")
    if not 1 <= upload_max_files <= 100:
        raise ValueError("Profile 'web.upload_max_files' must be 1-100")
    if not 1 <= browse_page_size <= 500:
        raise ValueError("Profile 'web.browse_page_size' must be 1-500")
    browse = bool(web_raw.get("browse", False))
    assets_dir = (
        _resolve_web_assets(
            str(web_raw["templates_dir"]), source_dir, honeytraps, browse
        )
        if web_enabled
        else None
    )
    if assets_dir:
        static_dir = Path(assets_dir) / "static"
        for label, asset in (
            ("favicon", web_raw.get("favicon")),
            ("fingerprint_script", web_raw.get("fingerprint_script")),
        ):
            if asset is None:
                continue
            asset_path = (static_dir / str(asset)).resolve()
            if (
                not asset_path.is_relative_to(static_dir.resolve())
                or not asset_path.is_file()
            ):
                raise ValueError(
                    f"Profile 'web.{label}' does not name a file under the profile static directory"
                )

    return ProfileConfig(
        name=name,
        kind=kind,
        ae_title=ae_title,
        implementation_class_uid=implementation_class_uid,
        implementation_version_name=implementation_version_name,
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
            max_store_bytes=max_store_bytes,
        ),
        web=WebConfig(
            enabled=web_enabled,
            templates_dir=web_raw.get("templates_dir"),
            grant_access=bool(web_raw.get("grant_access", False)),
            browse=browse,
            # Per-key overlay (a profile can override just one header/oidc key), like overlay_config().
            headers=headers,
            html_cache_headers=html_cache_headers,
            content_security_policy=content_security_policy,
            legacy_csp_header=bool(
                web_raw.get("legacy_csp_header", d.web.legacy_csp_header)
            ),
            secure_cookies=bool(web_raw.get("secure_cookies", d.web.secure_cookies)),
            identity=web_identity,
            license=license_data,
            oidc=oidc,
            favicon=web_raw.get("favicon"),
            honeytraps=honeytraps,
            fingerprint_script=web_raw.get("fingerprint_script"),
            honey_credentials=honey_credentials,
            routes=routes,
            cookies=cookies,
            winauth_messages=winauth_messages,
            max_request_bytes=max_request_bytes,
            upload_max_request_bytes=upload_max_request_bytes,
            upload_max_files=upload_max_files,
            browse_page_size=browse_page_size,
            assets_dir=assets_dir,
        ),
        dicomweb=DicomWebConfig(
            enabled=dicomweb_enabled,
            services=dicomweb_services,
            require_auth=dicomweb_require_auth,
            qido_max_results=qido_max_results,
            max_request_bytes=dicomweb_max_request_bytes,
            max_non_stow_request_bytes=dicomweb_max_non_stow_request_bytes,
            max_stow_parts=dicomweb_max_stow_parts,
            qido_default_media_type=qido_default_media_type,
            default_transfer_syntax=default_transfer_syntax,
            auth_schemes=auth_schemes,
            qido_warning_agent=qido_warning_agent,
        ),
    )


def load_profile(source: str | None) -> ProfileConfig:
    """None/"" -> default_profile(); a file path -> load it; else -> bundled profiles/<source>/<source>.yaml."""
    if not source:  # None or "" (e.g. an unset DICOMHAWK_PROFILE env in compose)
        return default_profile()

    path = Path(source)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        source_dir = path.resolve().parent
    else:
        try:
            resource = files(_DATA_PKG).joinpath(source, f"{source}.yaml")
            text = resource.read_text(encoding="utf-8")
            source_dir = Path(str(resource)).parent
        except (FileNotFoundError, ModuleNotFoundError):
            raise FileNotFoundError(
                f"No such profile: '{source}' (not a file, and no bundled {_DATA_PKG}/{source}/{source}.yaml)"
            )

    return _parse_profile(yaml.safe_load(text), source_dir=source_dir)
