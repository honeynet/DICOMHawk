import logging
from pathlib import Path
from typing import Callable
from uuid import uuid4

from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (
    ExplicitVRBigEndian,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    UID,
)

from pynetdicom.events import Event
from pynetdicom.apps.qrscp import db
from pynetdicom.presentation import QueryRetrievePresentationContexts

from sqlalchemy import Column, Engine, String, create_engine, func
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import MetaData
from sqlalchemy.orm import sessionmaker, scoped_session, Session

from .status import QRStatus
from .storage import Capture, Storage

logger = logging.getLogger(__name__)

# Identity keys db.add_instance needs; uploads omitting them are quarantined but not indexed.
INDEX_REQUIRED_KEYS: tuple[str, ...] = (
    "PatientID",
    "StudyInstanceUID",
    "SeriesInstanceUID",
)

# Rollup queries take a caller-supplied UID list; these cap an oversized or hostile caller.
_ROLLUP_MAX_STUDIES = 500
_ROLLUP_MAX_MODALITIES = 8

# Our own base, so the extra table never lands in pynetdicom's Q/R schema.
DetailBase = declarative_base()


class StudyDetail(DetailBase):
    """Per-study attributes the Q/R index does not carry, so display never reads files."""

    __tablename__ = "study_detail"

    study_instance_uid = Column(String, primary_key=True)
    patient_sex = Column(String)
    patient_birth_date = Column(String)
    study_description = Column(String)
    body_part = Column(String)
    series_description = Column(String)
    institution_name = Column(String)
    station_name = Column(String)
    referring_physician = Column(String)
    manufacturer = Column(String)
    model = Column(String)


# Detail column -> the DICOM keyword it is populated from.
_DETAIL_KEYWORDS: dict[str, str] = {
    "patient_sex": "PatientSex",
    "patient_birth_date": "PatientBirthDate",
    "study_description": "StudyDescription",
    "body_part": "BodyPartExamined",
    "series_description": "SeriesDescription",
    "institution_name": "InstitutionName",
    "station_name": "StationName",
    "referring_physician": "ReferringPhysicianName",
    "manufacturer": "Manufacturer",
    "model": "ManufacturerModelName",
}

# Attacker-supplied values reach the operator UI; cap them at the DICOM long-string bound.
_DETAIL_MAX_LEN = 64


class QRError:
    def __init__(self, error: str, status: QRStatus = QRStatus.FAILURE):
        self.error: str = error
        self.status: QRStatus = status


class QRResult:
    def __init__(
        self, matches: list[Dataset] | None = None, error: "QRError | None" = None
    ):
        self.matches: list[Dataset] = [] if matches is None else matches
        self.error: QRError | None = error


class FindResult:
    def __init__(self, dataset: Dataset | None = None, error: "QRError | None" = None):
        self.dataset: Dataset | None = dataset
        self.error: QRError | None = error


class Repository:

    def __init__(self, location: str | None, storage: Storage):
        self.location: str = location or ":memory:"
        self.storage: Storage = storage
        self.engine: Engine | None = None
        self.session: Session | None = None
        self.supported_sop: list[UID] = [
            ctx.abstract_syntax
            for ctx in QueryRetrievePresentationContexts
            if ctx.abstract_syntax is not None
        ]

    def _new_connection(self) -> Engine:
        url = f"sqlite:///{self.location}"
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if self.location == ":memory:":
            kwargs["poolclass"] = (
                StaticPool  # required so all threads share one in-memory DB
            )
        else:
            Path(self.location).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, **kwargs)
        db.Base.metadata.create_all(engine)
        DetailBase.metadata.create_all(engine)
        meta = MetaData()
        meta.reflect(bind=engine)
        return engine

    def _new_session(self):
        if not self.engine:
            self.engine = self._new_connection()
        return scoped_session(sessionmaker(bind=self.engine))

    def _connect(self):
        if not self.session:
            self.session = self._new_session()
        return self.session

    @property
    def conn(self):
        return self._connect()

    def start(self):
        self._connect()
        return self

    def stop(self):
        if self.session:
            self.session.close()
        if self.engine:
            self.engine.dispose()

    def supports(self, sop: UID | None) -> bool:
        return sop in self.supported_sop

    def eval_qr(
        self, event: Event, missing_level_status: QRStatus = QRStatus.SOP_CLASS_INVALID
    ) -> QRError | None:
        if not self.supports(sop := event.request.AffectedSOPClassUID):
            return QRError(
                f"SOP Class not supported: {sop}", QRStatus.SOP_CLASS_NOT_SUPPORTED
            )

        try:
            ds = event.identifier
        except Exception as exc:
            return QRError(
                f"Undecodable query identifier: {exc}", QRStatus.SOP_CLASS_INVALID
            )
        if not ds.get("QueryRetrieveLevel"):
            return QRError(
                f"request identifier not supported: {ds}", missing_level_status
            )
        return None

    def find(self, ds: Dataset, model) -> QRResult:
        # qrscp treats "" as a literal; DICOM defines it as universal matching.
        for elem in ds:
            if (
                elem.keyword != "QueryRetrieveLevel"
                and elem.value is not None
                and str(elem.value) == ""
            ):
                elem.value = None
        conn = self.conn
        try:
            matches = db.search(model, ds, conn)
        except db.InvalidIdentifier as exc:
            conn.rollback()
            return QRResult(
                error=QRError(
                    f"Invalid C-FIND Identifier received: {exc}",
                    QRStatus.SOP_CLASS_INVALID,
                )
            )
        except Exception as exc:
            conn.rollback()
            return QRResult(
                error=QRError(f"Exception occurred while querying database: {exc}")
            )

        return QRResult(matches=matches)

    def find_page(
        self,
        ds: Dataset,
        model,
        *,
        dedup_col: str,
        offset: int,
        limit: int,
        order_col: str | None = None,
        descending: bool = False,
    ) -> QRResult:
        """Return one bounded page of unique Q/R entities without materializing all rows."""
        for elem in ds:
            if (
                elem.keyword != "QueryRetrieveLevel"
                and elem.value is not None
                and str(elem.value) == ""
            ):
                elem.value = None

        conn = self.conn
        try:
            db._check_identifier(ds, model)
            attr = db._STUDY_ROOT[model]
            query = None
            for level, keywords in attr.items():
                level_ds = Dataset()
                for keyword in (kw for kw in keywords if kw in ds):
                    setattr(level_ds, keyword, getattr(ds, keyword))
                query = db.build_query(level_ds, conn, query)
                if level == ds.QueryRetrieveLevel:
                    break

            column = getattr(db.Instance, dedup_col)
            ordering = getattr(db.Instance, order_col) if order_col else column
            matches = (
                query.group_by(column)
                .order_by(ordering.desc() if descending else ordering)
                .offset(offset)
                .limit(limit)
                .all()
            )
        except db.InvalidIdentifier as exc:
            conn.rollback()
            return QRResult(
                error=QRError(
                    f"Invalid C-FIND Identifier received: {exc}",
                    QRStatus.SOP_CLASS_INVALID,
                )
            )
        except Exception as exc:
            conn.rollback()
            return QRResult(
                error=QRError(f"Exception occurred while querying database: {exc}")
            )

        return QRResult(matches=matches)

    def count_studies(self) -> int:
        """Distinct indexed studies; a display total, so a failure degrades to zero."""
        try:
            total = self.conn.query(
                func.count(func.distinct(db.Instance.study_instance_uid))
            ).scalar()
        except Exception as exc:
            self.conn.rollback()
            logger.warning(f"Failed counting studies: {exc}")
            return 0
        return int(total or 0)

    def count_instances(self, study_uids: list[str]) -> dict[str, int]:
        """Instance count per study for a bounded UID list; the list itself is the bound."""
        uids = list(study_uids)[:_ROLLUP_MAX_STUDIES]
        if not uids:
            return {}
        try:
            rows = (
                self.conn.query(
                    db.Instance.study_instance_uid,
                    func.count(db.Instance.sop_instance_uid),
                )
                .filter(db.Instance.study_instance_uid.in_(uids))
                .group_by(db.Instance.study_instance_uid)
                .all()
            )
        except Exception as exc:
            self.conn.rollback()
            logger.warning(f"Failed counting instances: {exc}")
            return {}
        return {str(uid): int(count) for uid, count in rows}

    def study_modalities(self, study_uids: list[str]) -> dict[str, list[str]]:
        """Modality is a SERIES attribute, so a STUDY-level find never returns it."""
        uids = list(study_uids)[:_ROLLUP_MAX_STUDIES]
        if not uids:
            return {}
        try:
            rows = (
                self.conn.query(db.Instance.study_instance_uid, db.Instance.modality)
                .filter(db.Instance.study_instance_uid.in_(uids))
                .distinct()
                .limit(len(uids) * _ROLLUP_MAX_MODALITIES)
                .all()
            )
        except Exception as exc:
            self.conn.rollback()
            logger.warning(f"Failed reading study modalities: {exc}")
            return {}
        found: dict[str, set[str]] = {}
        for uid, modality in rows:
            if modality:
                found.setdefault(str(uid), set()).add(str(modality))
        return {uid: sorted(values) for uid, values in found.items()}

    def study_details(self, study_uids: list[str]) -> dict[str, dict]:
        """Attributes the Q/R index lacks, read from our own table instead of the files."""
        uids = list(study_uids)[:_ROLLUP_MAX_STUDIES]
        if not uids:
            return {}
        try:
            rows = (
                self.conn.query(StudyDetail)
                .filter(StudyDetail.study_instance_uid.in_(uids))
                .all()
            )
        except Exception as exc:
            self.conn.rollback()
            logger.warning(f"Failed reading study details: {exc}")
            return {}
        return {
            str(row.study_instance_uid): {
                col: getattr(row, col) or "" for col in _DETAIL_KEYWORDS
            }
            for row in rows
        }

    def _index_detail(self, ds: Dataset) -> None:
        """Upsert the display-only attributes for this study; last write per study wins."""
        uid = str(ds.StudyInstanceUID)
        values = {}
        for column, keyword in _DETAIL_KEYWORDS.items():
            value = str(getattr(ds, keyword, "") or "")
            values[column] = value[:_DETAIL_MAX_LEN]
        row = self.conn.get(StudyDetail, uid)
        if row is None:
            self.conn.add(StudyDetail(study_instance_uid=uid, **values))
            return
        for column, value in values.items():
            setattr(row, column, value)

    def store(
        self,
        ds: Dataset,
        safe: bool = False,
        *,
        raw_bytes: bytes | None = None,
        capture: bool = True,
        on_captured: Callable[[Capture], None] | None = None,
    ) -> QRError | None:
        # Capture before validation so failed attacker payloads remain available.
        if not safe and capture:
            try:
                if raw_bytes is not None:
                    captured = self.storage.capture(raw_bytes)
                    if on_captured is not None:
                        on_captured(captured)
                else:
                    with self.storage.temp() as tf:
                        ds.save_as(tf, enforce_file_format=False)
                        self.storage.compress(tf)
            except Exception as exc:
                logger.warning(f"Failed to quarantine C-STORE payload: {exc}")
                return QRError(
                    f"Failed to quarantine incoming payload: {exc}",
                    QRStatus.STORE_ERROR,
                )

        try:
            fname = str(ds.SOPInstanceUID)
        except AttributeError:
            return QRError(
                "C-STORE dataset missing SOPInstanceUID", QRStatus.STORE_ERROR
            )
        try:
            sop_class_uid = ds.SOPClassUID
        except AttributeError:
            return QRError("C-STORE dataset missing SOPClassUID", QRStatus.STORE_ERROR)

        try:
            fpath = self.storage.path_for(safe, fname)
        except ValueError as exc:
            logger.warning(f"Path traversal attempt blocked: {fname}: {exc}")
            return QRError(
                f"Dangerous SOPInstanceUID rejected: {fname}", QRStatus.STORE_ERROR
            )

        if fpath.exists():
            logger.warning(
                f"Instance already exists in storage directory: {fname}; overwriting"
            )

        file_meta = getattr(ds, "file_meta", None)
        if not isinstance(file_meta, FileMetaDataset):
            file_meta = FileMetaDataset()
            ds.file_meta = file_meta
        file_meta.MediaStorageSOPClassUID = sop_class_uid
        file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        if not getattr(file_meta, "TransferSyntaxUID", None):
            if getattr(ds, "is_implicit_VR", False):
                file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
            elif getattr(ds, "is_little_endian", True) is False:
                file_meta.TransferSyntaxUID = ExplicitVRBigEndian
            else:
                file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        temporary = fpath.with_name(f".{fpath.name}.{uuid4().hex}.tmp")
        try:
            ds.save_as(temporary, enforce_file_format=True)
            temporary.replace(fpath)
        except Exception as exc:
            return QRError(
                f"Failed writing instance to storage directory: {exc}",
                QRStatus.STORE_ERROR,
            )
        finally:
            temporary.unlink(missing_ok=True)

        # Missing identity prevents indexing, but the raw payload is already quarantined.
        missing = [kw for kw in INDEX_REQUIRED_KEYS if kw not in ds]
        if missing:
            logger.warning(
                f"Not indexing C-STORE dataset missing required keys: {', '.join(missing)}"
            )
            return None

        try:
            # Path is relative to the database file
            matches = (
                self.conn.query(db.Instance)
                .filter(db.Instance.sop_instance_uid == ds.SOPInstanceUID)
                .all()
            )

            db.add_instance(ds, self.conn, str(fpath.resolve()))
            self._index_detail(ds)
            self.conn.commit()
            if not matches:
                logger.info("Instance added to database")
            else:
                logger.info("Database entry for instance updated")
        except Exception as exc:
            self.conn.rollback()
            logger.error("Unable to add instance to the database")
            logger.exception(exc)
            return QRError(
                f"Unable to index stored instance: {exc}", QRStatus.STORE_ERROR
            )

    def find_instance(self, match: Dataset, decompress: bool = False) -> FindResult:
        if self.storage.is_quarantined(match.filename):
            return FindResult(
                error=QRError(
                    f"Refusing to serve quarantined instance: {match.filename}",
                    QRStatus.STORE_ERROR,
                )
            )

        try:
            instance = dcmread(match.filename)
        except Exception as exc:
            return FindResult(
                error=QRError(
                    f"Error reading file: {match.filename}\n{exc}", QRStatus.READ_ERROR
                )
            )

        if decompress and instance.file_meta.TransferSyntaxUID.is_compressed:
            try:
                instance.decompress()
            except Exception as exc:
                return FindResult(
                    error=QRError(
                        f"Failed to decompress instance: {match.filename}\n{exc}",
                        QRStatus.READ_ERROR,
                    )
                )

        return FindResult(dataset=instance)


def new_repo(db: str | None, store: Storage) -> Repository:
    return Repository(db, store)
