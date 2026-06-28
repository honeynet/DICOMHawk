import logging
from pathlib import Path

from pydicom import dcmread
from pydicom.uid import UID
from pydicom.dataset import Dataset

from pynetdicom.events import Event
from pynetdicom.apps.qrscp import db
from pynetdicom.presentation import QueryRetrievePresentationContexts

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import MetaData
from sqlalchemy.orm import sessionmaker, scoped_session, Session

from .status import QRStatus
from .storage import Storage
from .middlewares import Middleware

logger = logging.getLogger(__name__)

class QRError:
    def __init__(self, error: str, status: QRStatus = QRStatus.FAILURE):
        self.error: str = error
        self.status: QRStatus = status

class QRResult:
    def __init__(self, matches: list[Dataset] | None = None, error: "QRError | None" = None):
        self.matches: list[Dataset] = [] if matches is None else matches
        self.error: QRError | None = error

class FindResult:
    def __init__(self, dataset: Dataset | None = None, error: "QRError | None" = None):
        self.dataset: Dataset | None = dataset
        self.error: QRError | None = error

class Repository:

    def __init__(self, location: str | None, storage: Storage, middlewares: list[Middleware]=[]):
        self.location: str = location or ":memory:"
        self.storage: Storage = storage
        self.middlewares: list[Middleware] = middlewares
        self.engine: Engine | None = None
        self.session: Session | None = None
        self.supported_sop: list[UID] = [
            ctx.abstract_syntax
            for ctx in QueryRetrievePresentationContexts
            if ctx.abstract_syntax is not None
        ]

    def _new_connection(self) -> Engine:
        url = f"sqlite:///{self.location}"
        # check_same_thread=False disables SQLite's own thread guard — safe
        # because SQLAlchemy manages thread safety via scoped_session.
        # StaticPool is required for :memory: so all threads share one DB;
        # file-based paths should be used in production to avoid this entirely.
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if self.location == ":memory:":
            kwargs["poolclass"] = StaticPool
        else:
            Path(self.location).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, **kwargs)
        db.Base.metadata.create_all(engine)
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
    
    def _apply_middlewares(self, instance: Dataset) -> Dataset:
        for mw in self.middlewares:
            instance = mw(instance)
        return instance
    
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

    def eval_qr(self, event: Event) -> QRError | None:
        if not self.supports(sop:=event.request.AffectedSOPClassUID):
            return QRError(f"SOP Class not supported: {sop}", QRStatus.SOP_CLASS_NOT_SUPPORTED)

        try:
            ds = event.identifier
        except Exception as exc:
            return QRError(f"Undecodable query identifier: {exc}", QRStatus.SOP_CLASS_INVALID)
        if not ds.get("QueryRetrieveLevel"):
            return QRError(f"request identifier not supported: {ds}", QRStatus.SOP_CLASS_INVALID)
        return None
    
    def find(self, ds: Dataset, model, inject: bool = False) -> QRResult:
        # Zero-length keys = Universal Matching (PS3.4 C.2.2.2.3), but decode to "" which
        # db.search single-value-matches → 0 results. Null them so empty queries match all.
        for elem in ds:
            if elem.keyword != "QueryRetrieveLevel" and elem.value is not None and str(elem.value) == "":
                elem.value = None
        conn = self.conn
        try:
            matches = db.search(model, ds, conn)
        except db.InvalidIdentifier as exc:
            conn.rollback()
            return QRResult(error=QRError(f"Invalid C-FIND Identifier received: {exc}", QRStatus.SOP_CLASS_INVALID))
        except Exception as exc:
            conn.rollback()
            return QRResult(error=QRError(f"Exception occurred while querying database: {exc}"))

        # Quarantine jail: uploaded files must never be visible to DICOM clients.
        matches = [m for m in matches if not self.storage.is_quarantined(m.filename)]

        if inject:
            matches = [self._apply_middlewares(m) for m in matches]

        return QRResult(matches=matches)

    def store(self, ds: Dataset, safe: bool = False) -> QRError | None:
        # NOTE: anything not safe is zipped and quarantined — capture the raw
        # attacker payload for forensics even if the rest of the store fails.
        if not safe:
            try:
                with self.storage.temp() as tf:
                    ds.save_as(tf, enforce_file_format=False)
                    self.storage.compress(tf)
            except Exception as exc:
                logger.warning(f"Failed to quarantine C-STORE payload: {exc}")

        try:
            fname = str(ds.SOPInstanceUID)
        except AttributeError:
            return QRError("C-STORE dataset missing SOPInstanceUID", QRStatus.STORE_ERROR)

        try:
            fpath = self.storage.path_for(safe, fname)
        except ValueError as exc:
            logger.warning(f"Path traversal attempt blocked: {fname} — {exc}")
            return QRError(f"Dangerous SOPInstanceUID rejected: {fname}", QRStatus.STORE_ERROR)

        if fpath.exists():
            logger.warning(f"Instance already exists in storage directory: {fname}; overwriting")

        try:
            ds.save_as(fpath, overwrite=True)
        except Exception as exc:
            return QRError(f"Failed writing instance to storage directory: {exc}", QRStatus.STORE_ERROR)
        
        # add_instance raises KeyError if these identity keys are missing (common in attacker
        # uploads); skip indexing — the raw payload is already quarantined.
        missing = [kw for kw in ("PatientID", "StudyInstanceUID", "SeriesInstanceUID") if kw not in ds]
        if missing:
            logger.warning(f"Not indexing C-STORE dataset missing required keys: {', '.join(missing)}")
            return None

        try:
            # Path is relative to the database file
            matches = (
                self.conn.query(db.Instance)
                .filter(db.Instance.sop_instance_uid == ds.SOPInstanceUID)
                .all()
            )

            db.add_instance(ds, self.conn, str(fpath.resolve()))
            if not matches:
                logger.info("Instance added to database")
            else:
                logger.info("Database entry for instance updated")
        except Exception as exc:
            self.conn.rollback()

            logger.error("Unable to add instance to the database")
            logger.exception(exc)

    def find_instance(self, match: Dataset, decompress: bool = False, inject: bool = True) -> FindResult:
        if self.storage.is_quarantined(match.filename):
            return FindResult(error=QRError(f"Refusing to serve quarantined instance: {match.filename}", QRStatus.STORE_ERROR))

        try:
            instance = dcmread(match.filename)
        except Exception as exc:
            return FindResult(error=QRError(f"Error reading file: {match.filename}\n{exc}", QRStatus.READ_ERROR))

        if decompress and instance.file_meta.TransferSyntaxUID.is_compressed:
            try:
                instance.decompress()
            except Exception as exc:
                return FindResult(error=QRError(
                    f"Failed to decompress instance: {match.filename}\n{exc}",
                    QRStatus.READ_ERROR,
                ))

        if inject:
            instance = self._apply_middlewares(instance)

        return FindResult(dataset=instance)
    

def new_repo(db: str | None, store: Storage, mws: list[Middleware]) -> Repository:
    return Repository(db, store, mws)