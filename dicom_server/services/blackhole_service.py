from abc import ABC, abstractmethod


class IBlackhole(ABC):

    @abstractmethod
    def block_scanners() -> None:
        pass

    @abstractmethod
    def allow_scanners() -> None:
        pass
