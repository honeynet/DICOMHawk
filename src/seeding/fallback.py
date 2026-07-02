import io
import logging
from importlib.resources import files

from pydicom import dcmread
from pydicom.dataset import Dataset

logger = logging.getLogger(__name__)

_FALLBACK_PKG = "seeding.fallback_data"


def load_fallback_datasets(modality: str | None = None) -> list[Dataset]:
    # NOTE: importlib.resources so this resolves from a wheel install too; one folder
    # per collection. Datasets without a Modality tag pass the filter.
    datasets: list[Dataset] = []
    try:
        root = files(_FALLBACK_PKG)
    except (ModuleNotFoundError, FileNotFoundError):
        logger.warning("No bundled fallback dataset found")
        return datasets

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        for f in entry.iterdir():
            if not f.name.lower().endswith(".dcm"):
                continue
            try:
                ds = dcmread(io.BytesIO(f.read_bytes()))
            except Exception as exc:
                logger.error(f"Fallback: failed to read bundled {f.name}: {exc}")
                continue
            if modality and str(getattr(ds, "Modality", "")) not in ("", modality):
                continue
            datasets.append(ds)

    if not datasets:
        logger.warning("Bundled fallback dataset is empty; nothing to seed offline")
    return datasets
