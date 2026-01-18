from abc import ABC
from logging import Logger

# Dont know if this is a component, a bus, a plugin, or something different
class Component(ABC):
    logger: Logger
    config: dict
    
    def start(self): ...
    def stop(self): ...