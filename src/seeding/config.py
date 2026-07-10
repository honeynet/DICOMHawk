import os

from dicomhawk.config import overlay_config

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")

_DEFAULTS = {"honeytoken": {"honey_url": None, "canary_pdf": None}}


def load_seeding_config(path: str = _CONFIG_PATH) -> dict:
    """Load seeding/config.yaml (honeytoken bait), defaulting any missing keys."""
    return overlay_config(_DEFAULTS, path)
