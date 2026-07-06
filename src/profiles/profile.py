from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml
from pynetdicom import DEFAULT_TRANSFER_SYNTAXES
from pynetdicom.presentation import AllStoragePresentationContexts
from pynetdicom.sop_class import _QR_CLASSES, _VERIFICATION_CLASSES

_DATA_PKG = "profiles.builtin"

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


@dataclass
class OperatorConfig:
    honey_url: str | None = None
    canary_pdf: str | None = None


@dataclass
class WebConfig:
    enabled: bool = False
    templates_dir: str | None = None
    server_header: str | None = None
    x_powered_by: str | None = None
    x_aspnet_version: str | None = None
    title: str | None = None
    favicon: str | None = None       # filename under profiles/builtin/, served by the #161 web component


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
    operator: OperatorConfig
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
        ),
        operator=OperatorConfig(),
        web=WebConfig(),
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
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must be a mapping at the top level")

    meta = _require(data, "meta", "top-level")
    identity = _require(data, "identity", "top-level")
    dicom_raw = _require(data, "dicom", "top-level")
    operator_raw = data.get("operator", {})
    web_raw = data.get("web", {})

    verification = _parse_sop_class(_require(dicom_raw, "verification", "dicom"), "dicom.verification")

    storage_classes = [
        _parse_sop_class(e, "dicom.storage_classes")
        for e in _require(dicom_raw, "storage_classes", "dicom")
    ]

    qr_classes = {
        op: [_parse_sop_class(e, f"dicom.qr_classes.{op}") for e in entries]
        for op, entries in _require(dicom_raw, "qr_classes", "dicom").items()
    }

    ae_auth_raw = dicom_raw.get("ae_auth", {})
    ae_auth = AEAuthConfig(
        require_called_aet=bool(ae_auth_raw.get("require_called_aet", False)),
        require_calling_aet=ae_auth_raw.get("require_calling_aet"),
    )

    dicom = DicomConfig(
        operations=list(_require(dicom_raw, "operations", "dicom")),
        verification=verification,
        storage_classes=storage_classes,
        qr_classes=qr_classes,
        max_associations=int(_require(dicom_raw, "max_associations", "dicom")),
        max_pdu_size=(v if (v := _require(dicom_raw, "max_pdu_size", "dicom")) is None else int(v)),
        ae_auth=ae_auth,
    )

    return ProfileConfig(
        name=_require(meta, "name", "meta"),
        kind=_require(meta, "kind", "meta"),
        ae_title=_require(identity, "ae_title", "identity"),
        implementation_class_uid=identity.get("implementation_class_uid"),
        implementation_version_name=identity.get("implementation_version_name"),
        manufacturer=identity.get("manufacturer"),
        model_name=identity.get("model_name"),
        dicom=dicom,
        operator=OperatorConfig(
            honey_url=operator_raw.get("honey_url"),
            canary_pdf=operator_raw.get("canary_pdf"),
        ),
        web=WebConfig(
            enabled=bool(web_raw.get("enabled", False)),
            templates_dir=web_raw.get("templates_dir"),
            server_header=web_raw.get("server_header"),
            x_powered_by=web_raw.get("x_powered_by"),
            x_aspnet_version=web_raw.get("x_aspnet_version"),
            title=web_raw.get("title"),
            favicon=web_raw.get("favicon"),
        ),
    )


def load_profile(source: str | None) -> ProfileConfig:
    """None/"" -> default_profile(); a file path -> load it; else -> bundled profiles/builtin/<source>.yaml."""
    if not source:  # None or "" (e.g. an unset DICOMHAWK_PROFILE env in compose)
        return default_profile()

    path = Path(source)
    if path.is_file():
        text = path.read_text()
    else:
        try:
            text = files(_DATA_PKG).joinpath(f"{source}.yaml").read_text()
        except FileNotFoundError:
            raise FileNotFoundError(
                f"No such profile: '{source}' (not a file, and no bundled {_DATA_PKG}/{source}.yaml)"
            )

    return _parse_profile(yaml.safe_load(text))
