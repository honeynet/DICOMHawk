from abc import ABC, abstractmethod


class ISessionCollector(ABC):

    @abstractmethod
    def session_started(self, ip, port, version_name) -> None:
        """Start the DICOM session on association request recieved"""
        pass

    @abstractmethod
    def collect_session_info(self, params, sub_process_finished: bool = False) -> None:
        """Collect session information within multiple dicom requests"""
        pass

    @abstractmethod
    def session_ended(self) -> None:
        """Finalize a DICOM session on release or abort request"""
        pass

    @abstractmethod
    def reset_session(self) -> None:
        """Reset session_data object to default values"""
        pass

    @abstractmethod
    def set_session_lock(self, value) -> None:
        """Lock changing session info until it ends"""
        pass

    @abstractmethod
    def session_locked(self) -> bool:
        """Check the status of the current session"""
        pass

    @abstractmethod
    def set_session_id(self, session_id) -> None:
        """Set an identifier to the current session"""
        pass
