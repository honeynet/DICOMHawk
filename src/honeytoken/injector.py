import logging
import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import EncapsulatedPDFStorage, ExplicitVRLittleEndian, generate_uid

logger = logging.getLogger(__name__)

type Middleware = Callable[[Dataset], Dataset]


def new_honeytoken_injector(
    honey_url: str | None = None, pdf_path: str | None = None
) -> Middleware:
    canary_data = None
    if pdf_path:
        try:
            canary_data = Path(pdf_path).read_bytes()
            logger.info("Loaded Canary PDF")
        except OSError as exc:
            raise ValueError(f"Failed to read canary PDF '{pdf_path}': {exc}") from exc

    def as_encapsulated_pdf(source: Dataset) -> Dataset:
        """Build a coherent Encapsulated PDF instance while retaining patient/study context."""
        file_meta = FileMetaDataset()
        ds = FileDataset("", {}, file_meta=file_meta, preamble=b"\0" * 128)
        for keyword in (
            "SpecificCharacterSet",
            "PatientName",
            "PatientID",
            "PatientBirthDate",
            "PatientSex",
            "StudyInstanceUID",
            "StudyDate",
            "StudyTime",
            "StudyID",
            "AccessionNumber",
            "ReferringPhysicianName",
            "InstitutionName",
            "InstitutionAddress",
        ):
            if keyword in source:
                setattr(ds, keyword, deepcopy(getattr(source, keyword)))

        now = datetime.now()
        source_uid = str(
            source.get("SOPInstanceUID", source.get("StudyInstanceUID", "unknown"))
        )
        canary_digest = hashlib.sha256(canary_data).hexdigest()
        ds.SOPClassUID = EncapsulatedPDFStorage
        ds.SOPInstanceUID = generate_uid(
            entropy_srcs=[source_uid, canary_digest, "document"]
        )
        ds.SeriesInstanceUID = generate_uid(
            entropy_srcs=[source_uid, canary_digest, "series"]
        )
        ds.Modality = "DOC"
        ds.SeriesNumber = 999
        ds.InstanceNumber = 1
        ds.ContentDate = str(source.get("StudyDate") or now.strftime("%Y%m%d"))
        ds.ContentTime = str(source.get("StudyTime") or now.strftime("%H%M%S.%f"))
        ds.BurnedInAnnotation = "YES"
        ds.DocumentTitle = "Clinical Report"
        ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
        ds.EncapsulatedDocument = canary_data
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        return ds

    def inject_honeytoken(ds: Dataset) -> Dataset:
        if canary_data:
            ds = as_encapsulated_pdf(ds)

        if honey_url:
            study_uid = ds.get("StudyInstanceUID", "UNKNOWN_STUDY")
            try:
                ds.RetrieveURL = f"{str(honey_url).rstrip('/')}/{study_uid}"
            except Exception as exc:
                logger.error(f"Failed to inject Honey URL: {exc}")

        return ds

    return inject_honeytoken
