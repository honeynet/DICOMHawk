from abc import ABC, abstractmethod
from typing import Dict, Set, Any


class IRedisService(ABC):

    @abstractmethod
    def is_ip_scanned(self, ip: str) -> bool:
        pass

    @abstractmethod
    def add_scanned_ip(self, ip: str) -> None:
        pass

    @abstractmethod
    def add_reputation_data(self, rep_dat: dict) -> None:
        pass

    @abstractmethod
    def add_request_data(self, redis_log_data: str) -> None:
        pass

    @abstractmethod
    def get_TCI_existing_studies(self) -> Set[str]:
        pass

    @abstractmethod
    def add_TCI_study(self, study_uid: str) -> None:
        pass

    @abstractmethod
    def add_injected_file(self, patient_name: str, modality: str) -> None:
        pass

    @abstractmethod
    def get_honey_url(self) -> str:
        pass

    @abstractmethod
    def update_files_integrity_state(self, changed_files: dict) -> None:
        pass
