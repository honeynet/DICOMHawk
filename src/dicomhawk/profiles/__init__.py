from .base import (
    Profile,
    DEFAULT_CONTEXTS,
    DEFAULT_SOP_UIDS,
)
from .registry import (
    register_profile,
    get_profile,
    list_profile_names,
)
from .osirix import get_osirix_profile

register_profile(get_osirix_profile())

__all__ = [
    "Profile",
    "get_profile",
    "list_profile_names",
    "get_default_presentation_contexts",
    "get_default_sop_uids",
]

def get_default_presentation_contexts():
    """Return the default DICOM storage and query/retrieve contexts."""
    return DEFAULT_CONTEXTS

def get_default_sop_uids():
    """Return the default supported SOP UIDs."""
    return DEFAULT_SOP_UIDS
