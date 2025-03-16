from abc import ABC, abstractmethod


class ITCIAAPI(ABC):

    def get_studies_based_on_modalities(self, mod) -> dict:
        pass

    @abstractmethod
    def get_access_token(self) -> None:
        """Get Access token from TCIA API"""
        pass

    def refresh_access_token(self) -> None:
        pass

    @abstractmethod
    def get_new_files(self, existing_studies, tcia_dir) -> None:
        """call TCIA API, get new DICOM files and store them at tcia_dir"""

        pass


class ITCIAScheduler(ABC):

    @abstractmethod
    def schedule_files_retrieval(self) -> None:
        """Schedule retrieval process"""
        pass


class ITCIAManager(ABC):

    @abstractmethod
    def change_dicom_files(self, arg) -> None:
        pass
