from itertools import chain
from pynetdicom.presentation import build_context
from .base import (
    Profile,
    get_sop_uids_from_contexts,
    ALL_STORAGE_SOP_CLASSES,
)
from pynetdicom.sop_class import _QR_CLASSES, _VERIFICATION_CLASSES

__all__ = ["get_osirix_profile"]

def _get_osirix_contexts():
    """Curated list of SOPs typically supported by an OsiriX workstation."""
    ctx = [build_context(s) for s in ALL_STORAGE_SOP_CLASSES]
    for c in chain(_QR_CLASSES.values(), _VERIFICATION_CLASSES.values()):
        ctx.append(build_context(c))
    return ctx

def get_osirix_profile() -> Profile:
    """Return a new OsiriX Profile instance."""
    contexts = _get_osirix_contexts()
    return Profile(
        display_name="OsiriX",
        ae_title="OSIRIX",
        implementation_class_uid="1.2.276.0.7230010.3.0.3.6.1",
        implementation_version_name="OSIRIX001",
        presentation_contexts=contexts,
        sop_uids=get_sop_uids_from_contexts(contexts),
    )
