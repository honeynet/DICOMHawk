"""Vendor persona definitions for DICOMHawk.

This module provides configurable vendor personas that change how the DICOM
server identifies itself during association negotiation. By emulating the
fingerprint of a specific vendor's equipment, the honeypot becomes harder
to detect by automated scanners that look for generic DICOM servers
advertising AllStoragePresentationContexts with default pynetdicom identifiers.

Each persona specifies:
    - ae_title: The Application Entity title advertised by the server.
    - implementation_class_uid: The Implementation Class UID returned during
      association negotiation (real UIDs derived from public DICOM conformance
      statements).
    - implementation_version_name: The version string returned to the caller.
    - supported_sop_classes: A restricted list of SOP Class UIDs that the
      persona will advertise, matching what the real device would support.
    - transfer_syntaxes: The Transfer Syntax UIDs the persona accepts.

Usage:
    Set the VENDOR_PERSONA environment variable to one of the persona keys
    (e.g. "ge_ct", "siemens_ct", "philips_mr") or leave it unset / set to
    "default" to keep the original DICOMHawk behaviour.
"""

from pynetdicom.sop_class import (
    CTImageStorage,
    MRImageStorage,
    EnhancedCTImageStorage,
    EnhancedMRImageStorage,
    DigitalXRayImageStorageForPresentation,
    UltrasoundImageStorage,
    ComputedRadiographyImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
    NuclearMedicineImageStorage,
    Verification,
    PatientRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelMove,
)

# Common DICOM Transfer Syntax UIDs
IMPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2"
EXPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2.1"
EXPLICIT_VR_BIG_ENDIAN = "1.2.840.10008.1.2.2"
JPEG_BASELINE = "1.2.840.10008.1.2.4.50"
JPEG_LOSSLESS = "1.2.840.10008.1.2.4.70"
JPEG_2000_LOSSLESS = "1.2.840.10008.1.2.4.90"

# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------
# Each key maps to a dict describing how the AE should present itself.
# SOP classes are kept intentionally narrow to mimic a real device.
# ---------------------------------------------------------------------------

VENDOR_PERSONAS = {
    "ge_ct": {
        "description": "GE Healthcare Revolution CT scanner",
        "ae_title": "GEREVCT01",
        "implementation_class_uid": "1.2.840.113619.6.96",
        "implementation_version_name": "GE_PACS_REV_24",
        "transfer_syntaxes": [
            IMPLICIT_VR_LITTLE_ENDIAN,
            EXPLICIT_VR_LITTLE_ENDIAN,
            JPEG_BASELINE,
            JPEG_LOSSLESS,
        ],
        "supported_sop_classes": [
            CTImageStorage,
            EnhancedCTImageStorage,
            SecondaryCaptureImageStorage,
            Verification,
            PatientRootQueryRetrieveInformationModelFind,
            StudyRootQueryRetrieveInformationModelFind,
            PatientRootQueryRetrieveInformationModelGet,
            StudyRootQueryRetrieveInformationModelGet,
            PatientRootQueryRetrieveInformationModelMove,
            StudyRootQueryRetrieveInformationModelMove,
        ],
    },
    "siemens_ct": {
        "description": "Siemens Healthineers SOMATOM CT scanner",
        "ae_title": "CTAWP73129",
        "implementation_class_uid": "1.3.12.2.1107.5.2",
        "implementation_version_name": "syngo CT VA48A",
        "transfer_syntaxes": [
            IMPLICIT_VR_LITTLE_ENDIAN,
            EXPLICIT_VR_LITTLE_ENDIAN,
            JPEG_LOSSLESS,
            JPEG_2000_LOSSLESS,
        ],
        "supported_sop_classes": [
            CTImageStorage,
            EnhancedCTImageStorage,
            SecondaryCaptureImageStorage,
            Verification,
            PatientRootQueryRetrieveInformationModelFind,
            StudyRootQueryRetrieveInformationModelFind,
            PatientRootQueryRetrieveInformationModelGet,
            StudyRootQueryRetrieveInformationModelGet,
            PatientRootQueryRetrieveInformationModelMove,
            StudyRootQueryRetrieveInformationModelMove,
        ],
    },
    "philips_mr": {
        "description": "Philips Healthcare Ingenia MR scanner",
        "ae_title": "PHILIPS_MR01",
        "implementation_class_uid": "1.3.46.670589.11",
        "implementation_version_name": "PHILIPS_MR_56.1",
        "transfer_syntaxes": [
            IMPLICIT_VR_LITTLE_ENDIAN,
            EXPLICIT_VR_LITTLE_ENDIAN,
            EXPLICIT_VR_BIG_ENDIAN,
            JPEG_BASELINE,
            JPEG_LOSSLESS,
        ],
        "supported_sop_classes": [
            MRImageStorage,
            EnhancedMRImageStorage,
            SecondaryCaptureImageStorage,
            Verification,
            PatientRootQueryRetrieveInformationModelFind,
            StudyRootQueryRetrieveInformationModelFind,
            PatientRootQueryRetrieveInformationModelGet,
            StudyRootQueryRetrieveInformationModelGet,
            PatientRootQueryRetrieveInformationModelMove,
            StudyRootQueryRetrieveInformationModelMove,
        ],
    },
}


def get_persona(name):
    """Return a persona dict by name, or None for default behaviour.

    Parameters
    ----------
    name : str
        The persona key (e.g. "ge_ct").  The value "default" or an empty
        string will return None, signalling that the original (unmodified)
        AE configuration should be used.

    Returns
    -------
    dict or None
        The persona configuration dict, or None when no persona is active.

    Raises
    ------
    ValueError
        If *name* is not a recognised persona key and is not "default"/empty.
    """
    if not name or name.lower() == "default":
        return None

    key = name.lower().strip()
    if key not in VENDOR_PERSONAS:
        available = ", ".join(sorted(VENDOR_PERSONAS.keys()))
        raise ValueError(
            f"Unknown vendor persona '{name}'. "
            f"Available personas: {available}"
        )
    return VENDOR_PERSONAS[key]
