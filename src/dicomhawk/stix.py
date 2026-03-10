import logging
from datetime import timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import stix2
except ImportError as exc:
    raise ImportError(
        "STIX 2.1 export requires the 'stix2' package. "
        "Install it with: pip install 'dicomhawk[stix]'"
    ) from exc

# Maps pynetdicom event names to STIX AttackPattern descriptors.
_EVENT_PATTERNS: dict[str, dict] = {
    "EVT_C_STORE": {
        "name": "DICOM Unauthorized Data Ingestion (C-STORE)",
        "description": (
            "An attacker pushed a DICOM SOP instance to the honeypot via C-STORE, "
            "indicating a potential unauthorized data upload or exfiltration-staging attack vector."
        ),
    },
    "EVT_C_FIND": {
        "name": "DICOM Patient Index Enumeration (C-FIND)",
        "description": (
            "An attacker issued a C-FIND query to enumerate stored patient study identifiers, "
            "indicating reconnaissance of stored medical data."
        ),
    },
    "EVT_C_GET": {
        "name": "DICOM Patient Data Exfiltration (C-GET)",
        "description": (
            "An attacker issued a C-GET request to retrieve stored DICOM SOP instances, "
            "indicating an active patient data exfiltration attempt."
        ),
    },
    "EVT_C_MOVE": {
        "name": "DICOM Lateral Data Exfiltration (C-MOVE)",
        "description": (
            "An attacker issued a C-MOVE request to redirect stored DICOM files to a third-party AE, "
            "indicating an attempt at lateral data exfiltration."
        ),
    },
    "EVT_ACSE_RECV": {
        "name": "DICOM Server Reconnaissance (A-ASSOCIATE-RQ)",
        "description": (
            "A remote host opened a DICOM A-ASSOCIATE request without issuing a DIMSE command, "
            "consistent with automated scanner reconnaissance (e.g., Shodan, Nmap dicom-ping)."
        ),
    },
    "EVT_RELEASED": {
        "name": "DICOM Association Released",
        "description": (
            "A DICOM association was cleanly released, marking the end of an attacker session."
        ),
    },
    "EVT_ABORTED": {
        "name": "DICOM Association Aborted",
        "description": (
            "A DICOM association was abruptly aborted, which may indicate a crashed scanner, "
            "a failed attack tool, or defensive detection on the attacker side."
        ),
    },
}

_DICOM_STANDARD_REF = {
    "source_name": "DICOM Standard",
    "url": "https://www.dicomstandard.org/current/",
    "description": "NEMA DICOM PS3 — Digital Imaging and Communications in Medicine",
}


def new_honeypot_identity(ae_title: str = "DICOMHawk", impl_uid: str = "1.2.3.4") -> stix2.Identity:
    """Creates the singleton STIX 2.1 Identity SDO for this DICOMHawk deployment.
    Call once at server startup and pass to to_stix_bundle()."""
    return stix2.Identity(
        name=f"DICOMHawk Honeypot [{ae_title}]",
        identity_class="system",
        description=f"DICOMHawk DICOM honeypot server. AE Title: {ae_title}, Implementation UID: {impl_uid}.",
    )


def to_stix_bundle(msg, identity: stix2.Identity) -> stix2.Bundle:
    """Converts an EventMessage to a valid STIX 2.1 Bundle."""
    # NOTE: pynetdicom's datetime.now() is naive; STIX requires UTC-aware timestamps
    ts = msg.timestamp.replace(tzinfo=timezone.utc)

    src_ip = stix2.IPv4Address(value=msg.evt.assoc.requestor.address)
    dst_ip = stix2.IPv4Address(value=msg.evt.assoc.acceptor.address)

    traffic = stix2.NetworkTraffic(
        src_ref=src_ip.id,
        dst_ref=dst_ip.id,
        src_port=msg.evt.assoc.requestor.port,
        dst_port=msg.evt.assoc.acceptor.port,
        protocols=["tcp"],
    )

    event_name = msg.evt._event.name
    pattern_info = _EVENT_PATTERNS.get(event_name, {
        "name": f"Unknown DICOM Event ({event_name})",
        "description": f"Unclassified DICOM event: {event_name}",
    })

    attack = stix2.AttackPattern(
        name=pattern_info["name"],
        description=pattern_info["description"],
        created_by_ref=identity.id,
        external_references=[_DICOM_STANDARD_REF],
    )

    observed = stix2.ObservedData(
        first_observed=ts,
        last_observed=ts,
        number_observed=1,
        object_refs=[src_ip.id, dst_ip.id, traffic.id],
        created_by_ref=identity.id,
    )

    # Sighting ties together: what we saw, what attack it maps to, and where we saw it
    sighting = stix2.Sighting(
        sighting_of_ref=attack.id,
        observed_data_refs=[observed.id],
        where_sighted_refs=[identity.id],
        first_seen=ts,
        last_seen=ts,
        count=1,
        created_by_ref=identity.id,
    )

    return stix2.Bundle(
        objects=[identity, src_ip, dst_ip, traffic, attack, observed, sighting],
    )


class StixHandler(logging.Handler):
    """Intercepts EventMessage log records and writes STIX 2.1 Bundle JSON
    (one bundle per line / NDJSON) to a rotating output file."""

    def __init__(self, identity: stix2.Identity, stix_out: str) -> None:
        super().__init__()
        # NOTE: logging.Handler defaults to WARNING; bus.info() fires at INFO level
        self.setLevel(logging.INFO)

        from .bus import EventMessage  # local import to avoid circular dependency

        self._identity = identity
        self._EventMessage = EventMessage

        out_dir = Path(stix_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "stix.ndjson"

        self._file_handler = RotatingFileHandler(
            str(out_path),
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
        )
        self._file_handler.setFormatter(logging.Formatter("%(message)s"))  # pass message through as-is

    def emit(self, record: logging.LogRecord) -> None:
        if not isinstance(record.msg, self._EventMessage):
            return
        try:
            bundle = to_stix_bundle(record.msg, self._identity)
            # wrap in a fresh LogRecord so RotatingFileHandler runs its own
            # size check and doRollover() rather than us doing it manually
            stix_record = logging.makeLogRecord({
                "msg": bundle.serialize(),
                "args": (),
            })
            self._file_handler.emit(stix_record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self._file_handler.close()
        super().close()
