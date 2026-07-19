"""Profile-driven DICOMweb (QIDO-RS/WADO-RS/STOW-RS/WADO-URI). API-only — no HTML/cookies/CSP.

Reuses the DIMSE Repository, its quarantine jail (WADO refuses quarantined bytes, STOW
quarantines uploads), and the interaction log (channel=DICOMWEB). Routes/ports come only
from profile.dicomweb, so profiles stay isolated.
"""

import copy
import fnmatch
import functools
import hashlib
import html
import json
import logging
import math
import re
import uuid
from email.message import Message
from email.parser import BytesFeedParser
from email.policy import default as email_policy
from io import BytesIO
from logging import Logger
from xml.etree import ElementTree as ET

import numpy as np
from flask import Flask, Response, current_app, request, stream_with_context
from PIL import Image
from pydicom import dcmread
from pydicom.datadict import dictionary_VR, keyword_for_tag, tag_for_keyword
from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.tag import BaseTag, Tag
from pydicom.uid import ImplicitVRLittleEndian, UID
from pynetdicom.apps.qrscp import db as qr_db
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from dicomhawk.bus import InteractionEvent, _BULK_DATA_KEYWORDS
from dicomhawk.handlers import _FIND_LEVEL_UID
from dicomhawk.repository import Repository
from profiles.profile import DicomWebService, ProfileConfig

logger = logging.getLogger(__name__)

_LOG_FIELD_LIMIT = 4096
_QIDO_SKIP = frozenset({"limit", "offset", "fuzzymatching", "includefield"})
_DICOM_XML = "http://dicom.nema.org/PS3.19/models/NativeDICOM"
_SESSION_SALT = uuid.uuid4().bytes
_STUDY_KEYS = (
    "StudyInstanceUID",
    "StudyDate",
    "StudyTime",
    "AccessionNumber",
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientSex",
    "StudyDescription",
    "ModalitiesInStudy",
    "StudyID",
)
_SERIES_KEYS = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "Modality",
    "SeriesNumber",
    "SeriesDescription",
)
_INSTANCE_KEYS = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "SOPClassUID",
    "InstanceNumber",
    "Modality",
)
# Per-level dedup column on the qrscp Instance row; IMAGE stays per-instance (SOPInstanceUID).
_DEDUP = {**_FIND_LEVEL_UID, "IMAGE": "sop_instance_uid"}
_FIND = StudyRootQueryRetrieveInformationModelFind


def _bounded(value) -> str:
    value = str(value or "")
    return (
        value
        if len(value) <= _LOG_FIELD_LIMIT
        else value[:_LOG_FIELD_LIMIT] + "...[truncated]"
    )


def _session_id() -> str:
    peer = "|".join(
        (
            request.remote_addr or "unknown",
            str(request.environ.get("REMOTE_PORT") or ""),
            str(request.environ.get("SERVER_PORT") or ""),
        )
    ).encode("utf-8", "replace")
    return (
        "dicomweb-"
        + hashlib.blake2s(peer, key=_SESSION_SALT, digest_size=12).hexdigest()
    )


def _log(request_type, *, level="INFO", matches=None, params=None) -> None:
    bus: Logger = current_app.config["BUS"]
    event = InteractionEvent.from_http(
        "DICOMWEB",
        request_type,
        session_id=_session_id(),
        ip=request.remote_addr,
        port=request.environ.get("REMOTE_PORT"),
        local_port=_server_port(),
        session_parameters=params,
        matches=matches,
        log_level=level,
        method=request.method,
        path=_bounded(request.full_path.rstrip("?")),
        user_agent=_bounded(request.headers.get("User-Agent", "")),
    )
    {"WARNING": bus.warning, "ERROR": bus.error}.get(level, bus.info)(event)


def _server_port() -> int | None:
    configured = current_app.config.get("DICOMWEB_PORT")
    if configured is not None:
        return int(configured)
    try:
        return int(request.environ.get("SERVER_PORT"))
    except (TypeError, ValueError):
        return None


def _json_response(
    payload, *, status=200, content_type="application/dicom+json"
) -> Response:
    return Response(json.dumps(payload), status=status, content_type=content_type)


def _problem(status, detail) -> Response:
    return Response(detail + "\n", status=status, content_type="text/plain")


def _int_arg(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _parse_content_type(value: str) -> tuple[str, dict[str, str]]:
    message = Message()
    message["content-type"] = value
    params = {
        str(key).lower(): str(val) for key, val in message.get_params(failobj=[])[1:]
    }
    return message.get_content_type().lower(), params


def _accept_values() -> list[tuple[str, dict[str, str]]]:
    raw = request.headers.get("Accept", "").strip()
    if not raw:
        return []
    ranked = []
    for order, item in enumerate(raw.split(",")):
        media, params = _parse_content_type(item.strip())
        try:
            quality = float(params.pop("q", "1"))
        except ValueError:
            quality = 0
        if media and quality > 0:
            ranked.append((quality, -order, media, params))
    ranked.sort(reverse=True)
    return [(media, params) for _quality, _order, media, params in ranked]


def _native_xml_element(ds: Dataset) -> ET.Element:
    root = ET.Element("NativeDicomModel", xmlns=_DICOM_XML)

    def add_dataset(parent: ET.Element, current: Dataset) -> None:
        for elem in current:
            attr = ET.SubElement(
                parent,
                "DicomAttribute",
                tag=f"{int(elem.tag):08X}",
                vr=elem.VR,
                keyword=elem.keyword or "",
            )
            if elem.VR == "SQ":
                for number, item_ds in enumerate(elem.value or [], 1):
                    item = ET.SubElement(attr, "Item", number=str(number))
                    add_dataset(item, item_ds)
                continue
            values = (
                elem.value
                if isinstance(elem.value, (MultiValue, list, tuple))
                else [elem.value]
            )
            for number, value in enumerate(values, 1):
                if value is None or isinstance(value, bytes):
                    continue
                if elem.VR == "PN":
                    pn = ET.SubElement(attr, "PersonName", number=str(number))
                    groups = str(value).split("=")
                    for group_name, group_value in zip(
                        ("Alphabetic", "Ideographic", "Phonetic"), groups
                    ):
                        group = ET.SubElement(pn, group_name)
                        for component_name, component_value in zip(
                            (
                                "FamilyName",
                                "GivenName",
                                "MiddleName",
                                "NamePrefix",
                                "NameSuffix",
                            ),
                            group_value.split("^"),
                        ):
                            ET.SubElement(group, component_name).text = component_value
                else:
                    ET.SubElement(attr, "Value", number=str(number)).text = str(value)

    add_dataset(root, ds)
    return root


def _native_xml(ds: Dataset) -> bytes:
    return ET.tostring(_native_xml_element(ds), encoding="utf-8", xml_declaration=True)


def _multipart_xml(datasets: list[Dataset]) -> Response:
    boundary = uuid.uuid4().hex

    def generate():
        for ds in datasets:
            yield f"--{boundary}\r\nContent-Type: application/dicom+xml\r\n\r\n".encode()
            yield _native_xml(ds)
            yield b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    content_type = (
        f'multipart/related; type="application/dicom+xml"; boundary={boundary}'
    )
    return Response(stream_with_context(generate()), content_type=content_type)


# --- QIDO-RS ---


def _query_tag(name: str) -> BaseTag | None:
    tag = tag_for_keyword(name)
    if tag is not None:
        return Tag(tag)
    compact = name.replace(",", "")
    if re.fullmatch(r"[0-9A-Fa-f]{8}", compact):
        candidate = Tag(int(compact, 16))
        try:
            dictionary_VR(candidate)
        except KeyError:
            return None
        return candidate
    return None


def _query_value(name: str) -> str:
    values = request.args.getlist(name)
    return "\\".join(values)


def _query_element(tag: BaseTag, value: str) -> DataElement:
    vr = dictionary_VR(tag)
    values = value.split("\\")
    if vr == "IS":
        for item in values:
            if item:
                int(item)
    elif vr in {"DS", "FL", "FD"}:
        for item in values:
            if item:
                float(item)
    elif vr == "UI":
        for item in values:
            if item and not UID(item).is_valid:
                raise ValueError(f"Invalid UID value for {tag}")
    return DataElement(tag, vr, value)


def _requested_return_tags(default_keywords) -> tuple[set[BaseTag], bool]:
    tags = {Tag(tag_for_keyword(keyword)) for keyword in default_keywords}
    include_all = False
    for raw in request.args.getlist("includefield"):
        for value in raw.split(","):
            value = value.strip()
            if not value:
                continue
            if value.lower() == "all":
                include_all = True
                continue
            tag = _query_tag(value)
            if tag is None:
                raise ValueError(f"Unknown includefield attribute: {value}")
            tags.add(tag)
    return tags, include_all


def _build_query(level, path_uids, return_keys):
    ds = Dataset()
    ds.QueryRetrieveLevel = level
    for keyword, value in path_uids.items():
        setattr(ds, keyword, value)
    header_filters: list[DataElement] = []
    for name in request.args:
        if name in _QIDO_SKIP or name == "QueryRetrieveLevel":
            continue
        tag = _query_tag(name)
        if tag is None:
            raise ValueError(f"Unknown query attribute: {name}")
        elem = _query_element(tag, _query_value(name))
        keyword = keyword_for_tag(tag)
        if keyword in qr_db._ATTRIBUTES:
            ds.add(elem)
        else:
            header_filters.append(elem)

    return_tags, include_all = _requested_return_tags(return_keys)
    for tag in return_tags:
        keyword = keyword_for_tag(tag)
        if keyword in qr_db._ATTRIBUTES and tag not in ds:
            ds.add_new(tag, dictionary_VR(tag), "")
    return ds, header_filters, return_tags, include_all


def _value_matches(candidate, wanted, vr: str) -> bool:
    wanted_values = list(wanted) if isinstance(wanted, MultiValue) else [wanted]
    candidate_values = (
        list(candidate) if isinstance(candidate, MultiValue) else [candidate]
    )
    if not wanted_values or all(str(value) == "" for value in wanted_values):
        return True
    for wanted_value in wanted_values:
        pattern = str(wanted_value)
        for candidate_value in candidate_values:
            actual = str(candidate_value)
            if vr in {"DA", "DT", "TM"} and "-" in pattern:
                start, end = pattern.split("-", 1)
                if (not start or actual >= start) and (not end or actual <= end):
                    return True
            elif "*" in pattern or "?" in pattern:
                if vr == "PN":
                    if fnmatch.fnmatch(actual.casefold(), pattern.casefold()):
                        return True
                elif fnmatch.fnmatchcase(actual, pattern):
                    return True
            elif (
                actual.casefold() == pattern.casefold()
                if vr == "PN"
                else actual == pattern
            ):
                return True
    return False


def _matches_header(ds: Dataset, filters: list[DataElement]) -> bool:
    for query_elem in filters:
        candidate = ds.get(query_elem.tag)
        if candidate is None or not _value_matches(
            candidate.value, query_elem.value, query_elem.VR
        ):
            return False
    return True


def _safe_dataset(repo: Repository, match) -> Dataset | None:
    result = repo.find_instance(match)
    return result.dataset if result.error is None else None


def _qido_media_type() -> str | None:
    default_type = current_app.config["QIDO_DEFAULT_MEDIA_TYPE"]
    accepts = _accept_values()
    if not accepts:
        return default_type if not request.headers.get("Accept", "").strip() else None
    if any(media in {"*/*", "application/*"} for media, _params in accepts):
        return default_type
    for media, params in accepts:
        if media in {"application/json", "application/dicom+json"}:
            return media
        if (
            media == "multipart/related"
            and params.get("type", "").lower() == "application/dicom+xml"
        ):
            return "application/dicom+xml"
    return None


def _qido_response(rows: list[Dataset], warning: bool) -> Response:
    media = _qido_media_type()
    if media is None:
        return _problem(406, "Requested QIDO representation is not supported")
    if not rows:
        response = Response(status=204)
    elif media == "application/dicom+xml":
        response = _multipart_xml(rows)
    else:
        response = _json_response(
            [ds.to_json_dict(bulk_data_element_handler=lambda _e: "") for ds in rows],
            content_type=media,
        )
    if warning:
        agent = current_app.config["QIDO_WARNING_AGENT"]
        response.headers["Warning"] = (
            f'299 {agent} "fuzzymatching is not supported and was ignored"'
        )
    return response


def _run_qido(level, path_uids, return_keys, request_type) -> Response:
    repo: Repository = current_app.config["REPO"]
    try:
        offset = _int_arg("offset") or 0
        limit = _int_arg("limit")
        query, header_filters, return_tags, include_all = _build_query(
            level, path_uids, return_keys
        )
    except (TypeError, ValueError) as exc:
        _log(request_type, level="WARNING", matches=0, params=[_bounded(exc)])
        return _problem(400, "Invalid query parameters")
    result = repo.find(query, _FIND)
    if result.error is not None:
        _log(request_type, level="ERROR", matches=0, params=[result.error.error])
        return _problem(400, "Invalid query")

    dedup_attr = _DEDUP[level]
    seen, rows, patient_ids = set(), [], set()
    for match in result.matches:
        safe_ds = None
        if (
            header_filters
            or include_all
            or any(keyword_for_tag(tag) not in qr_db._ATTRIBUTES for tag in return_tags)
        ):
            safe_ds = _safe_dataset(repo, match)
        if header_filters and (
            safe_ds is None or not _matches_header(safe_ds, header_filters)
        ):
            continue
        uid = getattr(match, dedup_attr, None)
        if uid in seen:
            continue
        seen.add(uid)
        row = match.as_identifier(query, _FIND)
        if "QueryRetrieveLevel" in row:  # a query key, not a QIDO result attribute
            del row.QueryRetrieveLevel
        if safe_ds is not None:
            if include_all:
                row = _without_bulk_data(safe_ds)
            else:
                for tag in return_tags:
                    if tag not in row and tag in safe_ds:
                        row.add(copy.deepcopy(safe_ds[tag]))
        rows.append(row)
        patient_ids.add(str(getattr(match, "patient_id", "")))

    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    if len(patient_ids) != 1:
        rows = rows[: current_app.config["QIDO_MAX"]]

    _log(request_type, matches=len(rows))
    fuzzy = request.args.get("fuzzymatching", "").lower() == "true"
    return _qido_response(rows, fuzzy)


def qido_studies():
    return _run_qido("STUDY", {}, _STUDY_KEYS, "DICOMWEB_QIDO_STUDIES")


def qido_series(study=None):
    path = {"StudyInstanceUID": study} if study is not None else {}
    return _run_qido("SERIES", path, _SERIES_KEYS, "DICOMWEB_QIDO_SERIES")


def qido_instances(study=None, series=None):
    path = {}
    if study is not None:
        path["StudyInstanceUID"] = study
    if series is not None:
        path["SeriesInstanceUID"] = series
    return _run_qido(
        "IMAGE",
        path,
        _INSTANCE_KEYS,
        "DICOMWEB_QIDO_INSTANCES",
    )


# --- WADO-RS / WADO-URI (retrieval; storage jail refuses quarantined bytes) ---


def _find_matches(level: str, path_uids: dict[str, str]):
    query = Dataset()
    query.QueryRetrieveLevel = level
    hierarchy = {
        "STUDY": ("StudyInstanceUID",),
        "SERIES": ("StudyInstanceUID", "SeriesInstanceUID"),
        "IMAGE": ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"),
    }
    for keyword in hierarchy[level]:
        setattr(query, keyword, "")
    for keyword, value in path_uids.items():
        setattr(query, keyword, value)
    return current_app.config["REPO"].find(query, _FIND)


def _locate_instance(study, series, instance):
    result = _find_matches(
        "IMAGE",
        {
            "StudyInstanceUID": study,
            "SeriesInstanceUID": series,
            "SOPInstanceUID": instance,
        },
    )
    if result.error is not None or not result.matches:
        return None
    return result.matches[0]


def _is_bulk_element(elem: DataElement) -> bool:
    tag = elem.tag
    overlay = (
        0x6000 <= tag.group <= 0x60FF and tag.group % 2 == 0 and tag.element == 0x3000
    )
    return elem.keyword in _BULK_DATA_KEYWORDS or overlay


def _without_bulk_data(ds: Dataset) -> Dataset:
    cleaned = copy.deepcopy(ds)

    def strip(current: Dataset) -> None:
        for elem in list(current):
            if _is_bulk_element(elem):
                del current[elem.tag]
            elif elem.VR == "SQ":
                for item in elem.value or []:
                    strip(item)

    strip(cleaned)
    return cleaned


def _part10_bytes(ds: Dataset, transfer_syntax: str | UID | None = None) -> bytes:
    target = UID(str(transfer_syntax or current_app.config["DEFAULT_TRANSFER_SYNTAX"]))
    if not target.is_transfer_syntax:
        raise ValueError("Requested transfer syntax is not a transfer syntax UID")
    encoded = copy.deepcopy(ds)
    if not getattr(encoded, "file_meta", None):
        encoded.file_meta = Dataset()
    original = UID(
        str(getattr(encoded.file_meta, "TransferSyntaxUID", ImplicitVRLittleEndian))
    )

    if target != original:
        if target.is_compressed:
            if original.is_compressed:
                encoded.decompress()
            encoded.compress(target)
        else:
            if original.is_compressed:
                encoded.decompress()
            encoded.file_meta.TransferSyntaxUID = target
    else:
        encoded.file_meta.TransferSyntaxUID = target

    buf = BytesIO()
    encoded.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def _multipart_dicom(datasets: list[Dataset], transfer_syntax: UID) -> Response:
    boundary = uuid.uuid4().hex

    def generate():
        for ds in datasets:
            yield (
                f"--{boundary}\r\nContent-Type: application/dicom; "
                f"transfer-syntax={transfer_syntax}\r\n\r\n"
            ).encode()
            yield _part10_bytes(ds, transfer_syntax)
            yield b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'
    return Response(
        stream_with_context(generate()), status=200, content_type=content_type
    )


def _wado_accept(*, metadata: bool, single: bool) -> tuple[str, UID | None] | None:
    accepts = _accept_values()
    if not accepts:
        if request.headers.get("Accept", "").strip():
            return None
        return (
            ("application/dicom+json", None)
            if metadata
            else (
                "multipart/related",
                UID(current_app.config["DEFAULT_TRANSFER_SYNTAX"]),
            )
        )
    if any(media in {"*/*", "application/*"} for media, _params in accepts):
        return (
            ("application/dicom+json", None)
            if metadata
            else (
                "multipart/related",
                UID(current_app.config["DEFAULT_TRANSFER_SYNTAX"]),
            )
        )
    for media, params in accepts:
        if metadata:
            if media == "application/dicom+json":
                return media, None
            if (
                media == "multipart/related"
                and params.get("type", "").lower() == "application/dicom+xml"
            ):
                return "application/dicom+xml", None
            continue
        if media == "application/dicom" and single:
            transfer_syntax = params.get(
                "transfer-syntax", current_app.config["DEFAULT_TRANSFER_SYNTAX"]
            )
            return media, UID(transfer_syntax)
        if (
            media == "multipart/related"
            and params.get("type", "application/dicom").lower() == "application/dicom"
        ):
            transfer_syntax = params.get(
                "transfer-syntax", current_app.config["DEFAULT_TRANSFER_SYNTAX"]
            )
            return media, UID(transfer_syntax)
    return None


def _retrievable_datasets(level: str, path_uids: dict[str, str]) -> list[Dataset]:
    result = _find_matches(level, path_uids)
    if result.error is not None:
        return []
    repo: Repository = current_app.config["REPO"]
    datasets, seen = [], set()
    for match in result.matches:
        sop = getattr(match, "sop_instance_uid", None)
        if sop in seen:
            continue
        seen.add(sop)
        located = repo.find_instance(match)
        if located.error is None:
            datasets.append(located.dataset)
    return datasets


def _wado_retrieve(level: str, path_uids: dict[str, str]) -> Response:
    datasets = _retrievable_datasets(level, path_uids)
    if not datasets:
        _log("DICOMWEB_WADO_RETRIEVE", matches=0)
        return _problem(404, "Requested DICOM object was not found")
    negotiated = _wado_accept(metadata=False, single=len(datasets) == 1)
    if negotiated is None:
        return _problem(406, "Requested WADO representation is not supported")
    media, transfer_syntax = negotiated
    try:
        # Preflight catches unsupported transcoding before response headers are sent.
        _part10_bytes(datasets[0], transfer_syntax)
    except Exception as exc:
        _log(
            "DICOMWEB_WADO_RETRIEVE", level="WARNING", matches=0, params=[_bounded(exc)]
        )
        return _problem(406, "Requested transfer syntax cannot be produced")
    _log("DICOMWEB_WADO_RETRIEVE", matches=len(datasets))
    if media == "application/dicom":
        return Response(
            _part10_bytes(datasets[0], transfer_syntax),
            content_type=f"application/dicom; transfer-syntax={transfer_syntax}",
        )
    return _multipart_dicom(datasets, transfer_syntax)


def wado_study(study):
    return _wado_retrieve("IMAGE", {"StudyInstanceUID": study})


def wado_series(study, series):
    return _wado_retrieve(
        "IMAGE", {"StudyInstanceUID": study, "SeriesInstanceUID": series}
    )


def wado_instance(study, series, instance):
    return _wado_retrieve(
        "IMAGE",
        {
            "StudyInstanceUID": study,
            "SeriesInstanceUID": series,
            "SOPInstanceUID": instance,
        },
    )


def _wado_metadata(level: str, path_uids: dict[str, str]) -> Response:
    datasets = [
        _without_bulk_data(ds) for ds in _retrievable_datasets(level, path_uids)
    ]
    if not datasets:
        _log("DICOMWEB_WADO_METADATA", matches=0)
        return _problem(404, "Metadata not found")
    negotiated = _wado_accept(metadata=True, single=len(datasets) == 1)
    if negotiated is None:
        return _problem(406, "Requested metadata representation is not supported")
    media, _transfer_syntax = negotiated
    _log("DICOMWEB_WADO_METADATA", matches=len(datasets))
    if media == "application/dicom+xml":
        return _multipart_xml(datasets)
    return _json_response(
        [ds.to_json_dict(bulk_data_element_handler=lambda _e: "") for ds in datasets]
    )


def wado_study_metadata(study):
    return _wado_metadata("IMAGE", {"StudyInstanceUID": study})


def wado_series_metadata(study, series):
    return _wado_metadata(
        "IMAGE", {"StudyInstanceUID": study, "SeriesInstanceUID": series}
    )


def wado_metadata(study, series, instance):
    return _wado_metadata(
        "IMAGE",
        {
            "StudyInstanceUID": study,
            "SeriesInstanceUID": series,
            "SOPInstanceUID": instance,
        },
    )


def _positive_int_parameter(name: str, default=None):
    raw = request.args.get(name)
    if raw is None:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _render_jpeg(ds: Dataset) -> bytes:
    array = ds.pixel_array
    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
    frame_number = _positive_int_parameter("frameNumber", 1)
    if frame_number > frames:
        raise ValueError("frameNumber is outside the object")
    if frames > 1:
        array = array[frame_number - 1]

    if array.ndim == 2:
        pixels = array.astype(np.float64)
        slope = float(getattr(ds, "RescaleSlope", 1) or 1)
        intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
        pixels = pixels * slope + intercept
        center = request.args.get("windowCenter", getattr(ds, "WindowCenter", None))
        width = request.args.get("windowWidth", getattr(ds, "WindowWidth", None))
        if isinstance(center, MultiValue):
            center = center[0]
        if isinstance(width, MultiValue):
            width = width[0]
        if center is not None and width is not None and float(width) > 0:
            low = float(center) - float(width) / 2
            high = float(center) + float(width) / 2
        else:
            low, high = float(np.nanmin(pixels)), float(np.nanmax(pixels))
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            high = low + 1
        pixels = np.clip((pixels - low) * 255 / (high - low), 0, 255).astype(np.uint8)
        if str(getattr(ds, "PhotometricInterpretation", "")) == "MONOCHROME1":
            pixels = 255 - pixels
        image = Image.fromarray(pixels, mode="L")
    else:
        pixels = np.asarray(array)
        if pixels.dtype != np.uint8:
            low, high = float(np.nanmin(pixels)), float(np.nanmax(pixels))
            high = high if high > low else low + 1
            pixels = np.clip((pixels - low) * 255 / (high - low), 0, 255).astype(
                np.uint8
            )
        image = Image.fromarray(pixels).convert("RGB")

    region = request.args.get("region")
    if region:
        x, y, width, height = (float(part) for part in region.split(","))
        if (
            not all(0 <= value <= 1 for value in (x, y, width, height))
            or width <= 0
            or height <= 0
        ):
            raise ValueError("region must contain four normalized coordinates")
        left, top = int(x * image.width), int(y * image.height)
        right, bottom = int((x + width) * image.width), int((y + height) * image.height)
        if right > image.width or bottom > image.height:
            raise ValueError("region lies outside the image")
        image = image.crop((left, top, right, bottom))

    rows = _positive_int_parameter("rows")
    columns = _positive_int_parameter("columns")
    if rows or columns:
        target_width = columns or max(1, round(image.width * rows / image.height))
        target_height = rows or max(1, round(image.height * columns / image.width))
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    quality = _positive_int_parameter("imageQuality", 90)
    if quality > 100:
        raise ValueError("imageQuality must be 1-100")
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality)
    return output.getvalue()


def wado_uri():
    args = request.args
    required = ("studyUID", "seriesUID", "objectUID")
    if args.get("requestType") != "WADO" or not all(args.get(k) for k in required):
        _log(
            "DICOMWEB_WADO_URI",
            level="WARNING",
            params=["Missing required WADO parameters"],
        )
        return _problem(
            406, "requestType=WADO and studyUID/seriesUID/objectUID are required"
        )
    match = _locate_instance(args["studyUID"], args["seriesUID"], args["objectUID"])
    if match is None:
        _log("DICOMWEB_WADO_URI", matches=0)
        return _problem(404, "Object not found")
    res = current_app.config["REPO"].find_instance(match)
    if res.error is not None:
        _log("DICOMWEB_WADO_URI", level="WARNING", matches=0, params=[res.error.error])
        return _problem(404, "Object not retrievable")
    ds = res.dataset
    requested_type = args.get("contentType")
    if not requested_type:
        requested_type = "image/jpeg" if "PixelData" in ds else "application/dicom"
    try:
        if requested_type == "application/dicom":
            transfer_syntax = UID(
                args.get(
                    "transferSyntax", current_app.config["DEFAULT_TRANSFER_SYNTAX"]
                )
            )
            response = Response(
                _part10_bytes(ds, transfer_syntax),
                content_type=f"application/dicom; transfer-syntax={transfer_syntax}",
            )
        elif requested_type == "image/jpeg":
            response = Response(_render_jpeg(ds), content_type="image/jpeg")
        elif requested_type == "text/xml":
            response = Response(
                _native_xml(_without_bulk_data(ds)),
                content_type="text/xml; charset=utf-8",
            )
        elif requested_type == "text/plain":
            response = Response(
                str(_without_bulk_data(ds)) + "\n",
                content_type="text/plain; charset=utf-8",
            )
        elif requested_type == "text/html":
            response = Response(
                "<!doctype html><html><body><pre>"
                + html.escape(str(_without_bulk_data(ds)))
                + "</pre></body></html>",
                content_type="text/html; charset=utf-8",
            )
        else:
            raise ValueError("Unsupported WADO-URI contentType")
    except Exception as exc:
        _log("DICOMWEB_WADO_URI", level="WARNING", matches=0, params=[_bounded(exc)])
        return _problem(406, "Requested WADO-URI representation cannot be produced")
    _log(
        "DICOMWEB_WADO_URI",
        matches=1,
        params=[f"objectUID: {args['objectUID']}", f"Content-Type: {requested_type}"],
    )
    return response


# --- STOW-RS (routes to Repository.store() -> quarantine, same as C-STORE) ---


def _multipart_parts(content_type: str, repo: Repository):
    parser = BytesFeedParser(policy=email_policy)
    parser.feed(f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode())
    with repo.storage.capture_stream(suffix=".stow-request") as raw_capture:
        while chunk := request.stream.read(1024 * 1024):
            raw_capture.write(chunk)
            parser.feed(chunk)
    message = parser.close()
    if not message.is_multipart():
        raise ValueError("Malformed multipart body")
    parts = list(message.iter_parts())
    if len(parts) > current_app.config["MAX_STOW_PARTS"]:
        raise ValueError("Too many multipart items")
    return parts


def _stow_response(stored, failed) -> dict:
    ds = Dataset()
    if stored:
        refs = []
        for sop_class, sop_instance in stored:
            item = Dataset()
            item.ReferencedSOPClassUID = sop_class
            item.ReferencedSOPInstanceUID = sop_instance
            refs.append(item)
        ds.ReferencedSOPSequence = refs
    if failed:
        fails = []
        for sop_instance, failure_code, _reason in failed:
            item = Dataset()
            if sop_instance != "?":
                item.ReferencedSOPInstanceUID = sop_instance
            item.FailureReason = failure_code
            fails.append(item)
        ds.FailedSOPSequence = fails
    return ds.to_json_dict(bulk_data_element_handler=lambda _e: "")


def _validate_stow_dataset(ds: Dataset, study: str | None) -> tuple[str, str]:
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
    allowed = current_app.config["STORAGE_CLASSES"].get(sop_class)
    if allowed is None:
        raise LookupError("SOP Class is not supported")
    if transfer_syntax not in allowed:
        raise LookupError("Transfer Syntax is not supported for this SOP Class")
    if study is not None and str(getattr(ds, "StudyInstanceUID", "")) != study:
        raise RuntimeError("Object StudyInstanceUID does not match the request URI")
    return sop_class, sop_instance


def _capture_rejected(repo: Repository, raw: bytes) -> None:
    try:
        repo.storage.capture(raw, suffix=".stow-part")
    except Exception as exc:
        _log("DICOMWEB_STOW_CAPTURE_FAILURE", level="ERROR", params=[_bounded(exc)])


def stow_studies(study=None):
    repo: Repository = current_app.config["REPO"]
    content_type = request.headers.get("Content-Type", "")
    media, params = _parse_content_type(content_type)
    if (
        media != "multipart/related"
        or params.get("type", "").lower() != "application/dicom"
    ):
        raw = request.get_data(cache=False)
        if raw:
            try:
                repo.storage.capture(raw, suffix=".stow-request")
            except Exception:
                logger.exception("Failed capturing a rejected STOW request")
        _log(
            "DICOMWEB_STOW_STORE",
            level="WARNING",
            params=[f"Bad Content-Type: {_bounded(content_type)}"],
        )
        return _problem(
            415, 'Content-Type must be multipart/related; type="application/dicom"'
        )
    if not params.get("boundary"):
        raw = request.get_data(cache=False)
        if raw:
            try:
                repo.storage.capture(raw, suffix=".stow-request")
            except Exception:
                logger.exception("Failed capturing a boundary-less STOW request")
        _log(
            "DICOMWEB_STOW_STORE",
            level="WARNING",
            params=["Missing multipart boundary"],
        )
        return _problem(400, "Missing multipart boundary")

    stored, failed = [], []
    try:
        parts = _multipart_parts(content_type, repo)
    except Exception as exc:
        _log("DICOMWEB_STOW_STORE", level="WARNING", params=[_bounded(exc)])
        return _problem(400, "Malformed multipart request")
    if not parts:
        _log("DICOMWEB_STOW_STORE", level="WARNING", params=["No DICOM parts"])
        return _problem(400, "At least one application/dicom part is required")

    seen_sops = set()
    for part in parts:
        if part.get_content_type().lower() != "application/dicom":
            raw = part.get_payload(decode=True) or b""
            _capture_rejected(repo, raw)
            failed.append(("?", 0x0110, "Part Content-Type is not application/dicom"))
            continue
        transfer_encoding = str(part.get("Content-Transfer-Encoding", "binary")).lower()
        if transfer_encoding not in {"binary", "8bit"}:
            raw = part.get_payload(decode=True) or b""
            _capture_rejected(repo, raw)
            failed.append(("?", 0x0110, "Unsupported Content-Transfer-Encoding"))
            continue
        raw = part.get_payload(decode=True) or b""
        digest = hashlib.sha256(raw).hexdigest()
        _log(
            "DICOMWEB_STOW_PAYLOAD_RECEIVED",
            params=[f"Bytes: {len(raw)}", f"SHA256: {digest}"],
        )
        if len(raw) > current_app.config["MAX_STORE_INSTANCE_BYTES"]:
            _capture_rejected(repo, raw)
            failed.append(("?", 0xA700, "DICOM instance exceeds the configured limit"))
            continue
        try:
            ds = dcmread(BytesIO(raw))
        except Exception as exc:
            _capture_rejected(repo, raw)
            failed.append(("?", 0x0110, f"Undecodable Part-10 object: {exc}"))
            continue
        sop = str(getattr(ds, "SOPInstanceUID", "?"))
        try:
            sop_class, sop = _validate_stow_dataset(ds, study)
        except LookupError as exc:
            _capture_rejected(repo, raw)
            failed.append((sop, 0x0122, str(exc)))
            continue
        except RuntimeError as exc:
            _capture_rejected(repo, raw)
            failed.append((sop, 0xA900, str(exc)))
            continue
        except ValueError as exc:
            _capture_rejected(repo, raw)
            failed.append((sop, 0x0117, str(exc)))
            continue
        if sop in seen_sops:
            _capture_rejected(repo, raw)
            failed.append((sop, 0x0111, "Duplicate SOP Instance in one request"))
            continue
        seen_sops.add(sop)
        err = repo.store(ds, raw_bytes=raw)  # exact raw trace + quarantine/index
        if err is not None:
            failed.append((sop, 0x0110, err.error))
        else:
            stored.append((sop_class, sop))
            _log(
                "DICOMWEB_STOW_PAYLOAD",
                matches=1,
                params=[
                    f"SOPInstanceUID: {sop}",
                    f"Bytes: {len(raw)}",
                    f"SHA256: {digest}",
                ],
            )

    params = [f"Stored: {len(stored)}", f"Failed: {len(failed)}"]
    params += [f"SOPInstanceUID: {sop}" for _cls, sop in stored[:20]]
    params += [f"Failure: {_bounded(reason)}" for _sop, _code, reason in failed[:20]]
    _log(
        "DICOMWEB_STOW_STORE",
        level="WARNING" if failed else "INFO",
        matches=len(stored),
        params=params,
    )
    return _json_response(_stow_response(stored, failed), status=202 if failed else 200)


# --- Auth: capture creds via a WinAuth 401 challenge, then proceed (honeypot maximizes capture) ---


def _challenge(kind) -> Response:
    _log("DICOMWEB_AUTH_CHALLENGE", params=[f"Service: {kind}"])
    resp = _problem(401, "Authentication required")
    realm = _bounded(request.host)[:255].replace("\\", "\\\\").replace('"', '\\"')
    for scheme in current_app.config["AUTH_SCHEMES"]:
        value = f'{scheme} realm="{realm}"' if scheme == "Basic" else scheme
        resp.headers.add("WWW-Authenticate", value)
    return resp


def _check_auth(kind):
    """None -> proceed. A Response -> short-circuit (401 challenge when no creds presented yet)."""
    raw = request.headers.get("Authorization", "")
    if not raw or " " not in raw:
        return _challenge(kind)
    scheme, token = raw.split(" ", 1)
    canonical = next(
        (
            item
            for item in current_app.config["AUTH_SCHEMES"]
            if item.lower() == scheme.lower()
        ),
        None,
    )
    if canonical is None or not token.strip():
        return _challenge(kind)
    auth = request.authorization if canonical == "Basic" else None
    if canonical == "Basic" and (auth is None or auth.type != "basic"):
        return _challenge(kind)
    auth_params = [f"Service: {kind}", f"Scheme: {canonical}"]
    if auth is not None:
        auth_params.extend(
            [
                f"Username: {_bounded(auth.username or '')}",
                f"Password: {_bounded(auth.password or '')}",
            ]
        )
    else:
        auth_params.append(f"Token: {_bounded(token.strip())}")
    _log(
        "DICOMWEB_AUTH_ATTEMPT",
        level="WARNING",
        params=auth_params,
    )
    return (
        None  # creds captured; let the request proceed so uploads/queries are seen too
    )


_ROUTES = {
    "qido": [
        ("/studies", "studies", qido_studies, ["GET"]),
        ("/series", "root_series", qido_series, ["GET"]),
        ("/instances", "root_instances", qido_instances, ["GET"]),
        ("/studies/<study>/series", "series", qido_series, ["GET"]),
        (
            "/studies/<study>/series/<series>/instances",
            "instances",
            qido_instances,
            ["GET"],
        ),
    ],
    "wado_rs": [
        ("/studies/<study>", "study", wado_study, ["GET"]),
        ("/studies/<study>/metadata", "study_metadata", wado_study_metadata, ["GET"]),
        ("/studies/<study>/series/<series>", "series", wado_series, ["GET"]),
        (
            "/studies/<study>/series/<series>/metadata",
            "series_metadata",
            wado_series_metadata,
            ["GET"],
        ),
        (
            "/studies/<study>/series/<series>/instances/<instance>",
            "instance",
            wado_instance,
            ["GET"],
        ),
        (
            "/studies/<study>/series/<series>/instances/<instance>/metadata",
            "metadata",
            wado_metadata,
            ["GET"],
        ),
    ],
    "stow": [
        ("/studies", "store", stow_studies, ["POST"]),
        ("/studies/<study>", "store_study", stow_studies, ["POST"]),
    ],
    "wado_uri": [
        ("", "uri", wado_uri, ["GET"]),
        ("/", "uri_slash", wado_uri, ["GET"]),
    ],
}


def _guarded(view, kind, needs_auth):
    if not needs_auth:
        return view

    @functools.wraps(view)
    def wrapped(**kwargs):
        challenge = _check_auth(kind)
        return challenge if challenge is not None else view(**kwargs)

    return wrapped


def _register(app: Flask, service: DicomWebService, needs_auth: bool) -> None:
    base = service.base_path.rstrip("/")
    for suffix, name, view, methods in _ROUTES[service.kind]:
        app.add_url_rule(
            base + suffix or "/",
            f"{service.kind}_{name}",
            _guarded(view, service.kind, needs_auth),
            methods=methods,
        )


def _apply_identity_headers(resp):
    # Reuse the profile's web identity headers (Server banner, etc.); NOT CSP/HTML-cache headers.
    for key, value in current_app.config["WEB_HEADERS"].items():
        resp.headers[key] = value
    return resp


def _not_found(_err):
    _log("DICOMWEB_NOT_FOUND")
    return _problem(404, "Not found")


def _too_large(_err):
    _log("DICOMWEB_REQUEST_TOO_LARGE", level="WARNING")
    return _problem(413, "Request entity too large")


def _unexpected_error(err):
    _log("DICOMWEB_INTERNAL_ERROR", level="ERROR", params=[_bounded(err)])
    return _problem(500, "DICOMweb request processing failed")


def _reject_unexpected_body():
    if request.method in {"GET", "HEAD"} and (request.content_length or 0) > 0:
        _log(
            "DICOMWEB_UNEXPECTED_BODY",
            level="WARNING",
            params=[f"Content-Length: {request.content_length}"],
        )
        return _problem(413, "Request body is not accepted for retrieval or query")
    return None


def new_dicomweb(
    profile: ProfileConfig, repo: Repository, bus: Logger
) -> dict[int, Flask]:
    """One Flask app per bound port (per-port isolation), keyed by port."""
    by_port: dict[int, list[DicomWebService]] = {}
    for service in profile.dicomweb.services:
        by_port.setdefault(service.port, []).append(service)

    auth_kinds = set(profile.dicomweb.require_auth)
    apps: dict[int, Flask] = {}
    for port, services in by_port.items():
        app = Flask(f"dicomweb_{port}")
        app.config["REPO"] = repo
        app.config["BUS"] = bus
        app.config["PROFILE"] = profile
        app.config["DICOMWEB_PORT"] = port
        app.config["WEB_HEADERS"] = profile.web.headers
        app.config["QIDO_MAX"] = profile.dicomweb.qido_max_results
        app.config["QIDO_DEFAULT_MEDIA_TYPE"] = profile.dicomweb.qido_default_media_type
        app.config["QIDO_WARNING_AGENT"] = profile.dicomweb.qido_warning_agent
        app.config["DEFAULT_TRANSFER_SYNTAX"] = profile.dicomweb.default_transfer_syntax
        app.config["MAX_STOW_PARTS"] = profile.dicomweb.max_stow_parts
        app.config["MAX_STORE_INSTANCE_BYTES"] = (
            profile.dicom.max_store_bytes or profile.dicomweb.max_request_bytes
        )
        app.config["STORAGE_CLASSES"] = {
            sop: set(syntaxes) for sop, syntaxes in profile.dicom.storage_classes
        }
        app.config["AUTH_SCHEMES"] = profile.dicomweb.auth_schemes
        app.config["MAX_CONTENT_LENGTH"] = (
            profile.dicomweb.max_request_bytes
            if any(service.kind == "stow" for service in services)
            else profile.dicomweb.max_non_stow_request_bytes
        )
        app.before_request(_reject_unexpected_body)
        app.after_request(_apply_identity_headers)
        app.register_error_handler(404, _not_found)
        app.register_error_handler(413, _too_large)
        app.register_error_handler(500, _unexpected_error)
        for service in services:
            _register(app, service, needs_auth=service.kind in auth_kinds)
            if service.kind == "wado_uri" and service.base_path.lower().endswith(
                "/wado"
            ):
                case_alias = service.base_path[:-4] + "Wado"
                app.add_url_rule(
                    case_alias,
                    "wado_uri_observed_case",
                    _guarded(wado_uri, service.kind, service.kind in auth_kinds),
                    methods=["GET"],
                )
                app.add_url_rule(
                    case_alias + "/",
                    "wado_uri_observed_case_slash",
                    _guarded(wado_uri, service.kind, service.kind in auth_kinds),
                    methods=["GET"],
                )
        apps[port] = app
    return apps
