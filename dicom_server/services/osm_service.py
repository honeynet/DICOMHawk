from abc import ABC, abstractmethod
from typing import List


class IOSMService(ABC):
  
    @abstractmethod
    def get_medical_institutions(self) -> List[str]:
        pass

    @abstractmethod
    def refresh_cache(self) -> bool:
        pass

    @abstractmethod
    def is_cache_valid(self) -> bool:
        pass 