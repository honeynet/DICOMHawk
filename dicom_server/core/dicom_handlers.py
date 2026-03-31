"""Implementation of the DICOM handlers"""

import traceback, utilities.dicom_util as dicom_util
from dependency_injector.wiring import inject
from services.dicom_session_service import ISessionCollector
from services.dicom_database_service import IDicomDatabase
from pydicom.dataset import Dataset
import traceback
from pydicom.pixel_data_handlers.util import apply_modality_lut
from typing import Generator, Tuple, Optional
from enums.dicom_session_keys import Sessionkeys as session_keys


class DICOMHandlers:
    @inject
    def __init__(
        self,
        app_logger,
        exceptions_logger=None,
        event_collector: ISessionCollector = None,
        dicomdb: IDicomDatabase = None,
    ):
        self.event_collector = event_collector or ISessionCollector()
        self.dicomdb = dicomdb or IDicomDatabase()
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger
        dicomdb.initialize_database()

    def handle_assoc(self, event):
        try:

            version_name = (
                str(event.assoc.requestor.implementation_version_name)
                if event.assoc.requestor.implementation_version_name
                else "N/A"
            )
            ip = str(event.assoc.requestor.address)
            port = event.assoc.requestor.port
            self.event_collector.session_started(ip, port, version_name)
        except Exception as e:
            self.exceptions_logger.exception(
                "Unexpected error while handling association"
            )

    def handle_echo(self, event):
        try:
            self.event_collector.collect_session_info(
                {
                    session_keys.LOG_LEVEL.key: "Info",
                    session_keys.REQUEST_TYPE.key: "C_ECHO",
                    session_keys.SESSION_MAIN_OPERATION.key: "C_ECHO",
                },
                True,
            )
            return 0x0000
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while handling ECHO operation"
            )
            return 0xC000

    def handle_find(
        self, event
    ) -> Generator[Tuple[int, Optional[Dataset]], None, None]:
        matches = []

        try:
            self.event_collector.collect_session_info(
                {
                    session_keys.LOG_LEVEL.key: "Info",
                    session_keys.REQUEST_TYPE.key: "C_FIND",
                    session_keys.SESSION_MAIN_OPERATION.key: "C_FIND",
                }
            )
            sop_class_uid = event.request.AffectedSOPClassUID
            identifier = event.identifier

            # Validate the SOPClassUID
            if dicom_util.is_sopclassuid_valid(sop_class_uid):
                self.logger.info("Request dataset has invaild model")
                yield (0xA900, None)  # Identifier does not match SOP Class
                return

            # Validate Identifier
            if dicom_util.identifier_invalid(identifier):
                self.logger.info("Request dataset has invaild identifier")
                yield (0xC006, None)  # Invalid attribute value
                return

            dicom_util.filter_identifier_tags(identifier)
            query_level = dicom_util.get_query_level(identifier)
            self.event_collector.collect_session_info(
                {session_keys.QUERY_LEVEL.key: query_level}
            )

            # Determine if it's a "all" request
            if dicom_util.all_requested(identifier):
                if query_level == "STUDY":
                    matches = self.dicomdb.query_all_studies()
                elif query_level == "SERIES":

                    # The pynetdicom ORM lacks query/retrieve model on SERIES level, that is why we filter here based on studies and not on series.
                    # This add limitition in quering all series otherwise, we used the studyinstanceuid (STUDY model) as an identifier tag to then filter specific serie based on the SeriesInstanceUID tag

                    matches = self.dicomdb.query_all_studies()
                elif query_level == "PATIENT":
                    matches = self.dicomdb.query_all_patients()
                self.event_collector.collect_session_info(
                    {
                        session_keys.SESSION_PARAMETERS.key: "ALL " + query_level,
                        session_keys.MATCHES.key: len(matches),
                    },
                    True,
                )
            else:
                query_parameters = dicom_util.get_query_parameters(identifier)
                self.event_collector.collect_session_info(
                    {session_keys.SESSION_PARAMETERS.key: query_parameters}
                )
                # Hierarchical query level determination
                if dicom_util.is_patient_level(identifier):
                    matches = self.dicomdb.query_patient_level(identifier)
                elif dicom_util.is_study_level(identifier):
                    matches = self.dicomdb.query_study_level(identifier)
                elif dicom_util.is_series_level(identifier):
                    matches = self.dicomdb.query_series_level(identifier)

                self.event_collector.collect_session_info(
                    {session_keys.MATCHES.key: len(matches)}, True
                )

            # Send Matching Results
            for instance in matches:
                response_dataset = Dataset()
                try:
                    self.dicomdb.get_response_data(
                        identifier, instance, response_dataset
                    )
                    yield (0xFF00, response_dataset)  # Pending response
                except Exception:
                    self.exceptions_logger.exception(
                        "Exception in building response set"
                    )
                    yield (0xC001, None)

            # Final success response
            yield (0x0000, None)

        except Exception:
            self.exceptions_logger.exception("Exception in handling C-FIND operation")
            yield (0xC001, None)  # Unable to process

    def handle_get(self, event) -> Generator[Tuple[int, Optional[Dataset]], None, None]:
        try:
            assoc = event.assoc
            identifier = event.identifier

            if dicom_util.identifier_invalid(identifier):
                yield 0xC000, None
                return
            instances = dicom_util.get_instances()
            matching = []
            query_level = dicom_util.get_query_level(identifier)
            matching = self.get_matching_instances(event, instances)
            self.event_collector.collect_session_info(
                {
                    session_keys.QUERY_LEVEL.key: query_level,
                    session_keys.LOG_LEVEL.key: "Info",
                    session_keys.SESSION_MAIN_OPERATION.key: "C_GET",
                    session_keys.REQUEST_TYPE.key: "C_GET",
                    session_keys.MATCHES.key: len(matching),
                },
                True,
            )
            yield len(matching)
            for instance in matching:
                if event.is_cancelled:
                    yield 0xFE00, None
                # Ensure the accepted contexts act as a SCP
                dicom_util.assign_runtime_contexts_support(assoc)
                if dicom_util.file_compressed(instance):
                    instance.decompress()
                    apply_modality_lut(instance.pixel_array, instance)
                yield 0xFF00, instance

            yield 0x0000, None
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while handling C-GET operation"
            )
            yield (0xC001, None)

    def handle_store(self, event):

        self.event_collector.collect_session_info(
            {
                session_keys.LOG_LEVEL.key: "Info",
                session_keys.REQUEST_TYPE.key: "C_STORE",
                session_keys.SESSION_MAIN_OPERATION.key: "C_STORE",
            },
            True,
        )
        dicom_util.store_received_file(event)
        return 0x0000

    def handle_move(
        self, event
    ) -> Generator[Tuple[int, Optional[Dataset]], None, None]:

        assoc = event.assoc
        identifier = event.identifier
        addr = assoc.requestor.address
        port = assoc.requestor.port
        yield (str(addr), port)

        if dicom_util.identifier_invalid(identifier):
            yield 0xC000, None
            return
        instances = dicom_util.get_instances()
        matching = []
        query_level = dicom_util.get_query_level(identifier)
        matching = self.get_matching_instances(event, instances)
        self.event_collector.collect_session_info(
            {
                session_keys.QUERY_LEVEL.key: query_level,
                session_keys.LOG_LEVEL.key: "Info",
                session_keys.REQUEST_TYPE.key: "C_MOVE",
                session_keys.MATCHES.key: len(matching),
            },
            True,
        )
        yield len(matching)
        for instance in matching:
            if event.is_cancelled:
                yield 0xFE00, None
            dicom_util.assign_runtime_contexts_support(assoc)
            if dicom_util.file_compressed(instance):
                instance.decompress()
                apply_modality_lut(instance.pixel_array, instance)
            yield 0xFF00, instance

        yield 0x0000, None

    def handle_release(self, event):
        self.event_collector.collect_session_info(
            {
                session_keys.LOG_LEVEL.key: "Warning",
                session_keys.REQUEST_TYPE.key: "Association Released",
            },
            True,
        )
        self.event_collector.session_ended()

    def handle_abort(self, event):

        if event.assoc.requestor.implementation_version_name:

            self.event_collector.collect_session_info(
                {
                    session_keys.LOG_LEVEL.key: "Warning",
                    session_keys.REQUEST_TYPE.key: "Association Aborted",
                    session_keys.VERSION.key: str(
                        event.assoc.requestor.implementation_version_name
                    ),
                },
                True,
            )
        else:
            self.event_collector.collect_session_info(
                {
                    session_keys.LOG_LEVEL.key: "Warning",
                    session_keys.REQUEST_TYPE.key: "Association Aborted",
                },
                True,
            )

        self.event_collector.session_ended()

    def get_matching_instances(self, event, instances):
        matching = []

        if dicom_util.is_study_level(event.identifier):
            if hasattr(event.identifier, "StudyInstanceUID"):
                study_uid = event.identifier.StudyInstanceUID
                self.event_collector.collect_session_info(
                    {
                        session_keys.SESSION_PARAMETERS.key: "StudyInstanceUID: "
                        + str(study_uid)
                    }
                )
                matching = [
                    instance
                    for instance in instances
                    if instance.StudyInstanceUID == study_uid
                ]

        elif dicom_util.is_series_level(event.identifier):
            if hasattr(event.identifier, "SeriesInstanceUID"):
                series_uid = event.identifier.SeriesInstanceUID
                self.event_collector.collect_session_info(
                    {
                        session_keys.SESSION_PARAMETERS.key: "SeriesInstanceUID: "
                        + str(series_uid)
                    }
                )

                matching = [
                    instance
                    for instance in instances
                    if instance.SeriesInstanceUID == series_uid
                ]

        return matching
