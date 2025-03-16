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


def store_received_file(event):
    file_name = "received_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
    event.dataset.file_meta = event.file_meta
    event.dataset.save_as(
        os.path.join(config.C_STORE_STORAGE, file_name), write_like_original=False
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
