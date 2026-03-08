import logging
from typing import Callable
from pydicom.dataset import Dataset
from pydicom import uid

logger = logging.getLogger(__name__)

type Middleware = Callable[[Dataset], Dataset]

def new_honeytoken_injector(honey_url: str | None = None, pdf_path: str | None = None) -> Middleware:
    canary_data = None
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                canary_data = f.read()
            logger.info("Loaded Canary PDF")
        except Exception as e:
            logger.error(f"Failed to read Canary PDF: {e}")

    def inject_honeytoken(ds: Dataset) -> Dataset:
        if honey_url:
            study_uid = ds.get("StudyInstanceUID", "UNKNOWN_STUDY")
            try:
                ds.RetrieveURL = f"{str(honey_url).rstrip('/')}/{study_uid}"
            except Exception as e:
                logger.error(f"Failed to inject Honey URL: {e}")
            
        if canary_data:
            try:
                ds.SOPClassUID = uid.EncapsulatedPDFStorage
                ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
                ds.EncapsulatedDocument = canary_data
            except Exception as e:
                logger.error(f"Failed to inject Canary PDF: {e}")
            
        return ds

    return inject_honeytoken