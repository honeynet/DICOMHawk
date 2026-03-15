from copy import copy
from dataclasses import dataclass
from itertools import chain

from pydicom.uid import UID
from pynetdicom.presentation import (
    AllStoragePresentationContexts,
    build_context,
)
from pynetdicom.sop_class import (
    _QR_CLASSES, 
    _VERIFICATION_CLASSES,
    CTImageStorage,
    MRImageStorage,
    PositronEmissionTomographyImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalMammographyXRayImageStorageForPresentation,
    SecondaryCaptureImageStorage,
    MultiFrameTrueColorSecondaryCaptureImageStorage,
    GrayscaleSoftcopyPresentationStateStorage,
    XRayAngiographicImageStorage,
    NuclearMedicineImageStorage,
    RTImageStorage,
    RTDoseStorage,
    RTStructureSetStorage,
    RTPlanStorage,
)

# Standard SOPs used by various profiles
ALL_STORAGE_SOP_CLASSES = [
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalMammographyXRayImageStorageForPresentation,
    CTImageStorage,
    MRImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    SecondaryCaptureImageStorage,
    MultiFrameTrueColorSecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
    NuclearMedicineImageStorage,
    PositronEmissionTomographyImageStorage,
    GrayscaleSoftcopyPresentationStateStorage,
    RTImageStorage,
    RTDoseStorage,
    RTStructureSetStorage,
    RTPlanStorage,
]


@dataclass(frozen=True)
class Profile:
    display_name: str
    ae_title: str
    implementation_class_uid: str
    implementation_version_name: str
    presentation_contexts: list
    sop_uids: list

def get_default_contexts():
    """Curated default contexts (Siemens station style)."""
    sops = [
        ComputedRadiographyImageStorage,
        DigitalXRayImageStorageForPresentation,
        CTImageStorage,
        MRImageStorage,
        UltrasoundImageStorage,
        SecondaryCaptureImageStorage,
        GrayscaleSoftcopyPresentationStateStorage,
        XRayAngiographicImageStorage,
    ]
    ctx = [build_context(s) for s in sops]
    for c in chain(_QR_CLASSES.values(), _VERIFICATION_CLASSES.values()):
        ctx.append(build_context(c))
    return ctx

def get_sop_uids_from_contexts(contexts):
    out = []
    for ctx in contexts:
        uid = getattr(ctx, "abstract_syntax", None)
        if uid is not None:
            out.append(UID(uid) if not isinstance(uid, UID) else uid)
    return out

DEFAULT_CONTEXTS = get_default_contexts()
DEFAULT_SOP_UIDS = get_sop_uids_from_contexts(DEFAULT_CONTEXTS)
