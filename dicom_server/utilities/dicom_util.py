from pynetdicom.apps.qrscp import db
from pydicom import dcmread
import config, os
from datetime import datetime
import logging


exceptions_logger = logging.getLogger("exceptions")


def is_patient_level(identifier):
    return identifier.QueryRetrieveLevel == "PATIENT"


def is_series_level(identifier):
    return identifier.QueryRetrieveLevel == "SERIES"


def is_study_level(identifier):
    return identifier.QueryRetrieveLevel == "STUDY"


def get_query_level(identifier):
    return identifier.QueryRetrieveLevel


def get_query_parameters(identifier):

    return [
        f"{raw.keyword}: {raw.value}"
        for raw in identifier
        if raw.keyword != "QueryRetrieveLevel"
    ]


def filter_identifier_tags(identifier):
    if identifier.QueryRetrieveLevel == "STUDY":
        attr = db._STUDY_ROOT_ATTRIBUTES

        for raw in identifier:
            if (
                raw.keyword in attr["SERIES"]
                or raw.keyword in attr["IMAGE"]
                or not identifier[raw.keyword].value
            ):
                delattr(identifier, raw.keyword)

    elif identifier.QueryRetrieveLevel == "SERIES":

        attr = db._STUDY_ROOT_ATTRIBUTES
        for raw in identifier:
            if raw.keyword in attr["IMAGE"] or not identifier[raw.keyword].value:
                delattr(identifier, raw.keyword)
    elif identifier.QueryRetrieveLevel == "PATIENT":
        attr = db._PATIENT_ROOT_ATTRIBUTES
        for raw in identifier:
            if (
                raw.keyword in attr["SERIES"]
                or raw.keyword in attr["IMAGE"]
                or raw.keyword in attr["STUDY"]
                or not identifier[raw.keyword].value
            ):
                delattr(identifier, raw.keyword)


def is_sopclassuid_valid(sop_class_uid):
    return sop_class_uid.keyword in (
        "UnifiedProcedureStepPull",
        "ModalityWorklistInformationModelFind",
    )


def get_instances():
    instances = []
    for path in os.listdir(config.DICOM_STORAGE_DIR):
        instances.append(dcmread(os.path.join(config.DICOM_STORAGE_DIR, path)))
    return instances


def identifier_invalid(identifier):
    return not "QueryRetrieveLevel" in identifier


def all_requested(identifier):
    return len(identifier) == 1


def file_compressed(instance):
    return instance.file_meta.TransferSyntaxUID.is_compressed


_UID_SAFE_RE = __import__("re").compile(r"^[0-9.]+$")


def _sanitize_uid(uid):
    """Return *uid* as a string if it contains only digits and dots, else None."""
    s = str(uid).strip() if uid else ""
    return s if (s and _UID_SAFE_RE.match(s)) else None


def store_received_file(event):
    """Persist the C-STORE dataset to disk.

    Storage layout (when all UIDs are present):
        <C_STORE_STORAGE>/<StudyInstanceUID>/<SeriesInstanceUID>/<SOPInstanceUID>.dcm

    Falls back to a flat timestamp-based filename when any UID is missing or
    contains unexpected characters.

    Raises on I/O failure so the caller can return an appropriate DICOM error
    status instead of silently claiming success.
    """
    ds = event.dataset

    study_uid  = _sanitize_uid(getattr(ds, "StudyInstanceUID",  None))
    series_uid = _sanitize_uid(getattr(ds, "SeriesInstanceUID", None))
    sop_uid    = _sanitize_uid(getattr(ds, "SOPInstanceUID",    None))

    # Derive caller identity for logging (best-effort)
    try:
        remote_ip = str(event.assoc.requestor.address)
        remote_ae = str(event.assoc.requestor.ae_title).strip()
    except Exception:
        remote_ip, remote_ae = "unknown", "unknown"

    if study_uid and series_uid and sop_uid:
        dest_dir  = os.path.join(config.C_STORE_STORAGE, study_uid, series_uid)
        file_path = os.path.join(dest_dir, sop_uid + ".dcm")
    else:
        dest_dir  = config.C_STORE_STORAGE
        file_path = os.path.join(
            dest_dir,
            "received_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f") + ".dcm",
        )

    exceptions_logger.debug(
        "C-STORE incoming | remote=%s AE=%s | study=%s series=%s sop=%s",
        remote_ip, remote_ae, study_uid, series_uid, sop_uid,
    )

    os.makedirs(dest_dir, exist_ok=True)
    ds.file_meta = event.file_meta
    ds.save_as(file_path, write_like_original=False)

    exceptions_logger.info(
        "C-STORE saved | path=%s | remote=%s AE=%s | study=%s series=%s sop=%s",
        file_path, remote_ip, remote_ae, study_uid, series_uid, sop_uid,
    )


def assign_runtime_contexts_support(assoc):
    for context in assoc.accepted_contexts:
        context._as_scp = True
        context._as_scu = True
        context.scu_role = True
        context.scp_role = True


def is_known_scanner(ip):
    with open(config.BLACKHOLE_FILE_PATH, "r") as file:
        for line in file:
            if ip in line:
                return True

    return False


def format_log_entry(entry):
    return (
        entry.replace("True", "true")
        .replace("False", "false")
        .replace("None", "null")
        .replace("'", '"')
    )
