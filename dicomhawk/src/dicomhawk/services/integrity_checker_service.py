from abc import ABC, abstractmethod


class IIntegrityChecker(ABC):
    @abstractmethod
    def hash_file(self, filename):
        pass

    @abstractmethod
    def check_hashes(self):
        pass
