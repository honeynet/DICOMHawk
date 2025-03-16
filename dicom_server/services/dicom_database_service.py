from contextlib import contextmanager
from typing import List, Any
from pynetdicom.apps.qrscp import db
from abc import ABC, abstractmethod


class IDicomDatabase(ABC):

    def __init__(self, engine: Any, storage_directory: str, database_file: str) -> None:
        """Initialize the database."""
        pass

    @abstractmethod
    @contextmanager
    def session_scope(self):
        """Yields a session."""
        yield

    @abstractmethod
    def initialize_database(self) -> None:
        """Clears and fill the database from DICOM files."""
        pass

    @abstractmethod
    def fill_database_tables_from_dicom_files(self) -> None:
        """Reads DICOM files and adds data to the database."""
        pass

    @abstractmethod
    def delete_database(self) -> None:
        """Deletes all data from the database."""
        pass

    @abstractmethod
    def query_all_studies(self) -> List[db.Study]:
        """Returns all Study data."""
        pass

    @abstractmethod
    def query_all_series(self) -> List[db.Series]:
        """Returns all Series data."""
        pass

    @abstractmethod
    def query_all_patients(self) -> List[db.Patient]:
        """Returns all Patient data."""
        pass

    @abstractmethod
    def query_study_level(self, identifier: Any) -> List[db.Study]:
        """Returns studies matching the identifier."""
        pass

    @abstractmethod
    def query_series_level(self, identifier: Any) -> List[db.Series]:
        """Returns series matching the identifier."""
        pass

    @abstractmethod
    def query_patient_level(self, identifier: Any) -> List[db.Patient]:
        """Returns patients matching th identifier."""
        pass

    @abstractmethod
    def get_unique_studies(self, instances: List[db.Study]) -> List[db.Study]:
        """Returns unique study_instance_uid."""
        pass

    # Pynetdicom does not support retrieval on SERIES level that is why retrieving series depends on the STUDY level
    @abstractmethod
    def get_uniqueSeries(self, instances: List[db.Study], identifier: Any) -> List[str]:
        """Returns unique series_instance_uid."""
        pass

    @abstractmethod
    def get_unique_patients(self, instances: List[db.Patient]) -> List[str]:
        """Returns unique patient_id."""
        pass

    @abstractmethod
    def get_other_levels_tags(
        self, level: str, required_tag: str, query_identifier: Any
    ) -> Any:
        """
        Retrieves the value of the specified 'required_tag' while building datasets'.

        Return:  The requested tag value, or None if not found.
        """
        pass
