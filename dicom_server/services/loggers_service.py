from abc import ABC, abstractmethod


class ILoggers(ABC):
    @abstractmethod
    def setup_logger(name, log_directory, level, when, interval, formatter) -> None:
        pass
