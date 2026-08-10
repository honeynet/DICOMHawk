import json
import logging
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

# Modality -> body part -> candidate StudyDescription values; "_default" is the fallback key.
type Procedures = dict[str, dict[str, tuple[str, ...]]]

_DEFAULT_FILE = "procedures.json"
# Keys are upper-cased on load, so the constant is too; a custom file may write it either way.
_FALLBACK_KEY = "_DEFAULT"


def _read_procedures(source) -> Procedures:
    data = json.loads(source.read_text())
    procedures: Procedures = {}
    for modality, parts in data.items():
        pools = {
            str(part).upper(): tuple(values) for part, values in parts.items() if values
        }
        if pools:
            procedures[str(modality).upper()] = pools
    if not procedures.get(_FALLBACK_KEY, {}).get(_FALLBACK_KEY):
        raise ValueError("procedures file needs a '_default' modality and body part")
    return procedures


def load_procedures(path: str | None) -> Procedures:
    """Load a procedure file, falling back to packaged defaults."""
    defaults = files("seeding").joinpath(_DEFAULT_FILE)
    if path is None:
        return _read_procedures(defaults)
    try:
        return _read_procedures(Path(path))
    except Exception as exc:
        logger.warning(
            f"Failed to load procedures from '{path}': {exc}; using built-in defaults"
        )
        return _read_procedures(defaults)


def procedure_pool(
    procedures: Procedures, modality: str, body_part: str
) -> tuple[str, ...]:
    """Narrowest pool that still matches, so a description never contradicts the body part."""
    by_part = procedures.get(str(modality or "").upper()) or procedures[_FALLBACK_KEY]
    pool = by_part.get(str(body_part or "").upper()) or by_part.get(_FALLBACK_KEY)
    return pool or procedures[_FALLBACK_KEY][_FALLBACK_KEY]
