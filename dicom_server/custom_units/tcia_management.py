import threading
from pydicom import uid
import schedule, requests
import json
import utilities.tcia_util as tcia_util
from services.redis_service import IRedisService
from services.dicom_database_service import IDicomDatabase
from services.tci_services import ITCIAAPI
from services.tci_services import ITCIAScheduler
from services.tci_services import ITCIAManager
from dependency_injector.wiring import inject
import logging


class TCIAScheduler(threading.Thread, ITCIAScheduler):

    @inject
    def __init__(
        self,
        app_logger,
        exceptions_logger,
        period,
        period_unit,
        tcia_manager: ITCIAManager = None,
    ):
        super().__init__(daemon=True)
        self.period = period
        self.period_unit = period_unit
        self.exceptions_logger = exceptions_logger
        self.tcia_manager = tcia_manager or ITCIAManager()
        self.logger = app_logger
        self.start()

    def run(self):
        self.schedule_files_retrieval()
        self.logger.info(
            f"TCIA retrieving schedule is started, DICOM files storage and database will be directly updated from The Cancer Imaging Archeive each {self.period} {self.period_unit}"
        )

    def schedule_files_retrieval(self):
        try:
            schedule_unit = getattr(schedule.every(self.period), str(self.period_unit))
            schedule_unit.do(self.tcia_manager.change_dicom_files)

        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while running TCIA files retrieve scheduler"
            )


class TCIAManager(ITCIAManager):
    def __init__(
        self,
        app_logger,
        exceptions_logger,
        honeytoken_url,
        storage_directory,
        tcia_dir,
        stagger_dir,
        pdf_canary_path,
        dicomdb: IDicomDatabase = None,
        redis_handler: IRedisService = None,
        tcia_api: ITCIAAPI = None,
    ):
        self.honeytoken_url = honeytoken_url
        self.tcia_dir = tcia_dir
        self.stagger_dir = stagger_dir
        self.storage_directory = storage_directory
        self.dicomdb = dicomdb or IDicomDatabase()
        self.redis_handler = redis_handler or IRedisService()
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger

        self.tcia_api = tcia_api
        self.pdf_canary_path = pdf_canary_path
        self.change_dicom_files_called = False

    def change_dicom_files(self):
        # to be used in unit testing
        self.change_dicom_files_called = True

        self.logger.info("Scheduled change of dicom files started")
        try:
            self.logger.debug("Stagging existing files")
            tcia_util.stage_old_files(
                self.storage_directory, self.tcia_dir, self.stagger_dir
            )
            self.logger.debug("Getting access token from TCIA")
            self.tcia_api.get_access_token()

            self.logger.debug("New files retrieval")
            exist_studies = self.redis_handler.get_TCI_existing_studies()

            self.tcia_api.get_new_files(exist_studies, self.tcia_dir)

            self.logger.debug("Organizing downloaded files")
            self.organize_downloaded_files()

            self.logger.debug("Cleaning up staged files")
            tcia_util.delete_staged_files(self.stagger_dir)

            self.logger.debug("Updating DICOM database")
            self.dicomdb.initialize_database()

        except Exception:
            self.exceptions_logger.exception(
                "Changing Dicom files with TCIA files failed: Rolling back changes"
            )

            self.roll_back_changes()

    def roll_back_changes(self):
        self.logger.debug("Roll-back: Deleting downloaded files")
        tcia_util.delete_downloded_files_if_exist(self.tcia_dir)
        self.logger.debug("Roll-back: restorring old files")
        tcia_util.restore_old_files(
            self.storage_directory, self.tcia_dir, self.stagger_dir
        )

    def organize_downloaded_files(self):
        self.logger.info("Organizing TCIA retrieved files")
        directory = tcia_util.initialize_dicom_directory_if_not_exist(
            self.storage_directory
        )
        series_files_counter = 0
        self.logger.debug("Parsing files based on modalities")
        self.logger.debug("Adding StudyInstanceUIDs to Redis")
        for modality in tcia_util.get_downloaded_modalitis(self.tcia_dir):
            for study_uid in tcia_util.get_studies_from_modality(
                modality, self.tcia_dir
            ):
                try:

                    self.redis_handler.add_TCI_study(study_uid)
                except Exception:
                    self.exceptions_logger.exception("Study addition failed:")

                study_files_counter = 0
                (
                    patient_name,
                    patient_id,
                    patient_sex,
                    birth_date,
                    study_id,
                    study_date,
                    accession_number,
                ) = tcia_util.generate_patient_info()
                institution = tcia_util.get_random_institution()
                for se_uid in tcia_util.get_downloaded_series_per_study(
                    modality, study_uid, self.tcia_dir
                ):
                    for file in tcia_util.get_files_per_serie(
                        modality, study_uid, se_uid, self.tcia_dir
                    ):
                        study_files_counter += 1
                        series_files_counter += 1
                        self.process_serie_file(
                            directory,
                            series_files_counter,
                            modality,
                            study_uid,
                            study_files_counter,
                            patient_name,
                            patient_id,
                            patient_sex,
                            birth_date,
                            study_id,
                            study_date,
                            accession_number,
                            institution,
                            se_uid,
                            file,
                        )

    def process_serie_file(
        self,
        directory,
        series_files_counter,
        modality,
        study_uid,
        study_files_counter,
        patient_name,
        patient_id,
        patient_sex,
        birth_date,
        study_id,
        study_date,
        accession_number,
        institution,
        se_uid,
        file,
    ):
        if not tcia_util.is_licience_file(file):

            dataset = tcia_util.build_file_dataset(
                modality,
                study_uid,
                patient_name,
                patient_id,
                patient_sex,
                birth_date,
                study_id,
                study_date,
                accession_number,
                institution,
                se_uid,
                file,
                self.tcia_dir,
            )
            if (
                series_files_counter == 4
                or series_files_counter == 12
                or series_files_counter == 18
                or series_files_counter == 24
            ):
                # Injecting 4 retrieved dicom file with canary token and honeyURL token
                self.inject_honey_url(dataset)

                self.inject_pdf_canary_token(dataset)

                self.redis_handler.add_injected_file(patient_name, modality)

            tcia_util.store_retrieved_file(
                directory,
                modality,
                study_files_counter,
                patient_name,
                dataset,
            )

    def inject_pdf_canary_token(self, dataset):
        try:
            self.logger.debug(
                f"DICOM file for Patient: {dataset.PatientName}, modality: {dataset.Modality} injected with pdf canary token"
            )
            dataset.SOPClassUID = uid.EncapsulatedPDFStorage
            dataset.MIMETypeOfEncapsulatedDocument = "application/pdf"
            dataset.EncapsulatedDocument = self.get_canary_token()
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while injecting canary token"
            )

    def inject_honey_url(self, dataset):
        try:
            dataset.RetrieveURL = (
                f"{str(self.honeytoken_url)}/{dataset.StudyInstanceUID}"
            )
            self.logger.debug(
                f"DICOM file for Patient {dataset.PatientName}, modality: {dataset.Modality}  injected with honeyURL"
            )
        except Exception:
            self.exceptions_logger.exception(
                f"Unexpected error while injecting HoneyURL for Patient {dataset.PatientName}, modality: {dataset.Modality}"
            )

    def get_canary_token(self):
        pdf_path = self.pdf_canary_path
        try:
            with open(pdf_path, "rb") as pdf_file:
                pdf_data = pdf_file.read()
                return pdf_data
        except Exception:
            self.exceptions_logger.exception("Canary token retrieval failed:")


class TCIAAPI(ITCIAAPI):

    def __init__(
        self,
        app_logger,
        exceptions_logger,
        username,
        password,
        min_series,
        max_series,
        modalities,
        studies_per_mod,
    ):
        self.username = username
        self.password = password
        self.exceptions_logger = exceptions_logger
        self.minimum_files_in_each_retrieved_serie = min_series
        self.maximum_files_in_each_retrieved_serie = max_series
        self.number_of_studies_in_each_retrieved_modality = studies_per_mod
        self.modalities = modalities
        self.logger = app_logger

    def get_new_files(self, existing_studies, tcia_dir):
        self.logger.debug("Calling TCIA API")
        try:

            _json = {}
            for mod in self.modalities:
                metadata = {}
                _json = self.get_studies_based_on_modalities(mod)
                if _json:
                    study_counter = 0
                    metadata = tcia_util.filter_retrieved_studies(
                        existing_studies,
                        _json,
                        study_counter,
                        self.number_of_studies_in_each_retrieved_modality,
                        self.minimum_files_in_each_retrieved_serie,
                        self.maximum_files_in_each_retrieved_serie,
                    )

                    for st_uid, se_uids in metadata.items():
                        for se_uid_dict in se_uids:
                            se_uid = se_uid_dict["se_uid"]
                            mod = se_uid_dict["modality"]
                            response = requests.get(
                                f"https://services.cancerimagingarchive.net/nbia-api/services/v2/getImage?SeriesInstanceUID={se_uid}",
                                headers={
                                    "Authorization": f"Bearer {self.access_token}"
                                },
                            )
                            response.raise_for_status()

                            tcia_util.extract_and_save_zip_data(
                                mod, st_uid, se_uid, response, tcia_dir
                            )
            metadata = {}
        except Exception:
            self.exceptions_logger.exception("New files retrieval failed:")

    def get_access_token(self):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=3)
        session.mount("https://", adapter)
        try:
            res = session.post(
                "https://services.cancerimagingarchive.net/nbia-api/oauth/token",
                data={
                    "username": self.username,
                    "password": self.password,
                    "client_id": "NBIA",
                    "grant_type": "password",
                },
                timeout=10,
            )
            json_object = json.loads(res.content)
            self.access_token = json_object["access_token"]
            self.refresh_token = json_object["refresh_token"]

        except Exception:
            self.exceptions_logger.exception("Access token retrieval failed:")

    def refresh_access_token(self):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=3)
        session.mount("https://", adapter)
        try:
            session.post(
                "https://services.cancerimagingarchive.net/nbia-api/oauth/token",
                data={
                    "refresh_token": self.refresh_token,
                    "client_id": "nbia",
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
        except Exception:
            self.exceptions_logger.exception("Token refresh failed:")

    def get_studies_based_on_modalities(self, mod):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=3)
        session.mount("https://", adapter)
        try:
            res = session.get(
                "https://services.cancerimagingarchive.net/nbia-api/services/v1/getSeries",
                params={f"Modality": {mod}},
            )
            _json = json.loads(res.content)
        except Exception:
            self.exceptions_logger.exception("Studies retrieval failed:")
        return _json
