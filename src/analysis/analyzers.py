"""Pure, bounded, non-executing analyzers. No network, no execution of the payload itself."""

import gzip
import hashlib
import logging
import math
import re
from collections import Counter
from io import BytesIO
from pathlib import Path

import magic
from pydicom import dcmread
from pydicom.filereader import read_dataset
from pydicom.uid import UID

logger = logging.getLogger(__name__)

_URL_RE = re.compile(rb"https?://[\w\-.:/%?=&#~+]{4,2048}")
# Lookaround excludes a match embedded in a longer run (e.g. a DICOM UID), not a real IP.
_IPV4_RE = re.compile(
    rb"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"
)
_EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")

_IOC_MAX_COUNT = 50
_IOC_MAX_LEN = 253

# Every field below is a short VR by spec (UI<=64, CS<=16, LO<=64); longer means attacker data.
_METADATA_MAX_LEN = 256


def read_capture(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Decompress the gzip trace, bounded to max_bytes. Returns (data, truncated)."""
    with gzip.open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        return data[:max_bytes], True
    return data, False


def compute_hashes(data: bytes) -> dict:
    """MD5/SHA-1 for IOC/threat-feed compatibility only — capture.sha256 is the integrity record."""
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
    }


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    length = len(data)
    return -sum(
        (n / length) * math.log2(n / length) for n in Counter(data).values()
    )


def identify_type(data: bytes) -> dict:
    try:
        return {
            "mime": magic.from_buffer(data, mime=True),
            "description": magic.from_buffer(data),
        }
    except Exception as exc:
        logger.warning("libmagic identification failed: %s", exc)
        return {"mime": None, "description": None}


def _decode_candidates(data: bytes) -> list[bytes]:
    candidates = [data]
    try:
        candidates.append(data.decode("utf-16-le", errors="ignore").encode("utf-8"))
    except Exception:
        pass
    return candidates


def extract_iocs(data: bytes) -> dict:
    """Bounded ASCII + UTF-16LE URL/IP/email extraction; deduplicated, capped count and length."""
    urls: set[str] = set()
    ips: set[str] = set()
    emails: set[str] = set()
    for blob in _decode_candidates(data):
        for pattern, bucket in ((_URL_RE, urls), (_IPV4_RE, ips), (_EMAIL_RE, emails)):
            if len(bucket) >= _IOC_MAX_COUNT:
                continue
            for match in pattern.finditer(blob):
                if len(bucket) >= _IOC_MAX_COUNT:
                    break
                bucket.add(match.group().decode("ascii", errors="ignore")[:_IOC_MAX_LEN])
    return {"urls": sorted(urls), "ips": sorted(ips), "emails": sorted(emails)}


def _bounded(ds, keyword: str) -> str | None:
    """Attacker-controlled values reach both the log and the artifact DB; cap them like bus.py does."""
    value = str(getattr(ds, keyword, ""))
    if not value:
        return None
    if len(value) <= _METADATA_MAX_LEN:
        return value
    return value[:_METADATA_MAX_LEN] + "...[truncated]"


def _declared_length(ds, keyword: str) -> int | None:
    if keyword not in ds:
        return None
    try:
        return len(ds[keyword].value)
    except Exception:
        return None


def _read_dataset(data: bytes, source_encoding: str, transfer_syntax_uid: str | None = None):
    """part10 is self-describing; a raw DIMSE dataset uses the negotiated transfer syntax if known, else dcmread's own heuristic."""
    try:
        if source_encoding == "part10":
            return dcmread(BytesIO(data))
        if transfer_syntax_uid:
            ts = UID(transfer_syntax_uid)
            return read_dataset(BytesIO(data), ts.is_implicit_VR, ts.is_little_endian)
        return dcmread(BytesIO(data), force=True)
    except Exception:
        return None


def _content_conflicts_with_declared_mime(declared: str | None, detected: str | None):
    """Only judges the unambiguous case — other types legitimately detect as a generic container."""
    if not declared or not detected:
        return None
    if declared.strip().lower() != "application/pdf":
        return None
    return detected.strip().lower() != "application/pdf"


def extract_encapsulated_document(
    data: bytes, source_encoding: str, max_bytes: int, transfer_syntax_uid: str | None = None
) -> tuple[dict, bytes] | None:
    """Unwrap (0042,0011) so offset/filesize-anchored rules see the real file, not the DICOM wrapper."""
    ds = _read_dataset(data, source_encoding, transfer_syntax_uid)
    if ds is None or "EncapsulatedDocument" not in ds:
        return None
    try:
        document = bytes(ds.EncapsulatedDocument or b"")
    except Exception:
        return None
    if not document:
        return None

    stored_bytes = len(document)
    declared = ds.get("EncapsulatedDocumentLength")
    if isinstance(declared, int) and 0 < declared <= stored_bytes:
        document = document[:declared]
    elif document.endswith(b"\x00"):
        document = document[:-1]  # the single pad byte Part 10 allows for an odd-length value

    truncated = len(document) > max_bytes
    if truncated:
        document = document[:max_bytes]

    declared_mime = _bounded(ds, "MIMETypeOfEncapsulatedDocument")
    file_type = identify_type(document)
    metadata = {
        "declared_mime": declared_mime,
        "size": len(document),
        "padding_bytes_removed": stored_bytes - len(document) if not truncated else 0,
        "truncated": truncated,
        "sha256": hashlib.sha256(document).hexdigest(),
        "file_type": file_type,
        "content_conflicts_with_declared_mime": _content_conflicts_with_declared_mime(
            declared_mime, file_type["mime"]
        ),
    }
    return metadata, document


def _parse_assumption(source_encoding: str, transfer_syntax_uid: str | None) -> str | None:
    if source_encoding == "part10":
        return None
    if transfer_syntax_uid:
        return f"Parsed using the transfer syntax negotiated for this association ({transfer_syntax_uid})"
    return "Transfer syntax unknown; guessed from the bytes themselves (pydicom's VR/endian heuristic)"


def extract_dicom_metadata(
    data: bytes, source_encoding: str, transfer_syntax_uid: str | None = None
) -> dict | None:
    """Bounded, non-PHI DICOM metadata, or None if unparseable; see parse_assumption for how a raw DIMSE dataset was decoded."""
    ds = _read_dataset(data, source_encoding, transfer_syntax_uid)
    if ds is None:
        return None

    file_meta = getattr(ds, "file_meta", None)
    return {
        "sop_class_uid": _bounded(ds, "SOPClassUID"),
        "sop_instance_uid": _bounded(ds, "SOPInstanceUID"),
        "transfer_syntax_uid": (
            _bounded(file_meta, "TransferSyntaxUID")
            if file_meta is not None
            else transfer_syntax_uid
        ),
        "modality": _bounded(ds, "Modality"),
        "encapsulated_document_mime": _bounded(ds, "MIMETypeOfEncapsulatedDocument"),
        "has_pixel_data": "PixelData" in ds,
        "has_encapsulated_document": "EncapsulatedDocument" in ds,
        "declared_pixel_data_bytes": _declared_length(ds, "PixelData"),
        "declared_encapsulated_document_bytes": _declared_length(
            ds, "EncapsulatedDocument"
        ),
        "parse_assumption": _parse_assumption(source_encoding, transfer_syntax_uid),
    }
