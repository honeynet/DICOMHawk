
from pydicom import dcmread
from pydicom.uid import UID
from pydicom.dataset import Dataset
from pydicom.pixel_data_handlers.util import apply_modality_lut

from pynetdicom.events import Event 
from pynetdicom.apps.qrscp import db

from sqlalchemy import Engine
from sqlalchemy.schema import MetaData
from sqlalchemy.orm import sessionmaker, Session

from .status import QRStatus
from .storage import Storage
from .honey import Middleware

import os
import logging

logger = logging.getLogger(__name__)

class QRError:
    error: str
    status: int

class QRResult:
    matches: list[Dataset]
    error: QRError

class FindResult:
    dataset: Dataset
    error: QRError

class Repository:
    supported_sop: list[UID]
    engine: Engine
    session: Session

    def __init__(self, location: str | None, storage: Storage, middlewares: list[Middleware]=[]):
        self.location = location or ":memory:"
        self.storage: Storage = storage
        self.middlewares = middlewares

    def _new_connection(self) -> Engine:
        engine = db.create(
            f"sqlite:///{self.location}"
        )
    
        meta = MetaData()
        meta.reflect(bind=engine)
        return engine

    def _new_session(self):
        if not self.engine:
            self.engine = self._new_connection()
        session = sessionmaker(bind=self.engine)()
        return session

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
        err = QRError()
        if not self.supports(sop:=event.request.AffectedSOPClassUID):
            err.error = f"SOP Class not supported: {sop}"
            err.status = QRStatus.SOP_CLASS_NOT_SUPPORTED
            return err

        ds = event.identifier
        if not ds.get("QueryRetrieveLevel"):
            err.error = f"request identifier not supported: {ds}"
            err.status = QRStatus.SOP_CLASS_INVALID 
            return err
        return
    
    def find(self, ds: Dataset, model, inject: bool = False) -> QRResult:
        conn = self.conn
        result = QRResult()
        try:
            matches = db.search(model, ds, conn) 
        except db.InvalidIdentifier as exc:
            conn.rollback()

            err = QRError()
            err.error = f"Invalid C-FIND Identifier received: {exc}"
            err.status = QRStatus.SOP_CLASS_INVALID
            result.error = err
            return result
        except Exception as exc:
            conn.rollback()

            err = QRError()
            err.error = f"Exception occurred while querying database: {exc}"
            err.status = QRStatus.FAILURE
            result.error = err
            return result

        if inject:
            matches = [self._apply_middlewares(m) for m in matches]

        result.matches = matches
        return result

    def store(self, ds: Dataset, safe: bool = False) -> QRError | None:
        # NOTE: anything not safe is zipped and quarantined
        # save a raw copy of the dataset with the current timestamp
        if not safe:
            with self.storage.temp() as tf:
                ds.save_as(tf, enforce_file_format=False)
                self.storage.compress(tf)

        fdir = self.storage.jail(safe)
        fname = ds.SOPInstanceUID # NOTE: this is dangerous
        fpath = os.path.join(fdir, fname)

        if os.path.exists(fpath):
            logger.warning(f"Instance already exists in storage directory: {fname}; overwriting")

        try:
            ds.save_as(fpath, overwrite=True)
        except Exception as exc:
            err = QRError()
            err.error = f"Failed writing instance to storage directory: {exc}"
            err.status = QRStatus.STORE_ERROR
            return err
        
        try:
            # Path is relative to the database file
            matches = (
                self.conn.query(db.Instance)
                .filter(db.Instance.sop_instance_uid == ds.SOPInstanceUID)
                .all()
            )

            db.add_instance(ds, self.conn, os.path.abspath(fpath))
            if not matches:
                logger.info("Instance added to database")
            else:
                logger.info("Database entry for instance updated")
        except Exception as exc:
            self.conn.rollback()

            logger.error("Unable to add instance to the database")
            logger.exception(exc)

    def find_instance(self, match: Dataset, decompress: bool = False, inject: bool = True) -> FindResult:
        result = FindResult()

        try:
            instance = dcmread(match.filename)
        except Exception as exc:
            err = QRError()
            err.error = f"Error reading file: {match.filename}\n{exc}"
            err.status = 0xC421
            result.error = err
            return result
        
        # NOTE: not sure this is needed tbh, why not sending files compressed?
        if decompress and instance.file_meta.TransferSyntaxUID.is_compressed:
            instance.decompress()
            apply_modality_lut(instance.pixel_array, instance)

        if inject:
            instance = self._apply_middlewares(instance)

        result.dataset = instance
        return result