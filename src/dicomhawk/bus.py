import logging
import ujson

from logging import Logger
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from pathlib import Path
from typing import Literal, Optional
from pynetdicom.events import Event
from pydicom.dataset import Dataset
from datetime import datetime

from .status import QRStatus

logger = logging.getLogger(__name__)

# Bulk-data tags excluded from log serialization to prevent OOM on large uploads.
_BULK_DATA_KEYWORDS: frozenset[str] = frozenset({
    "PixelData",
    "FloatPixelData",
    "DoubleFloatPixelData",
    "OverlayData",
    "WaveformData",
    "SpectroscopyData",
    "EncapsulatedDocument",
})


def _safe_dataset(ds: Dataset) -> dict:
    result = {}
    for elem in ds:
        if elem.keyword in _BULK_DATA_KEYWORDS or not elem.keyword:
            continue
        try:
            result[elem.keyword] = str(elem.value)
        except Exception:
            pass
    return result


class EventMessage:
    def __init__(self, evt: Event, /, direction: Literal["request", "response"], status: Optional[QRStatus] = None, data: Optional[dict] = None, error: Optional[str] = None) -> None:
        self.evt = evt
        self.status = status
        self.data = data
        self.error = error
        self.direction = direction
        self.timestamp = datetime.now()

    def __str__(self) -> str:
        tmp = {
            "saddr": self.evt.assoc.requestor.address,
            "sport": self.evt.assoc.requestor.port,
            "daddr": self.evt.assoc.acceptor.address,
            "dport": self.evt.assoc.acceptor.port,
            "pdu": str(getattr(self.evt, "pdu", None)),
            "event": self.evt._event.name,
            "direction": self.direction,
            "timestamp": self.timestamp.isoformat(),
        }

        if self.status is not None:
            tmp["status"] = self.status.name

        if self.data:
            tmp["data"] = self.data

        if self.error:
            tmp["error"] = self.error

        return ujson.dumps(tmp)


# TODO: finish this one. We want to get a summary of the event.
# As it is right now, is rather difficult with the number of different types of events
# and parameters it holds.
def parse_event(evt: Event) -> dict:
    data = {}

    # Different event types expose different attributes; guard each access.
    for attr in ("data", "message"):
        try:
            data[attr] = str(getattr(evt, attr))
        except AttributeError:
            pass

    # Q/R identifier (C-FIND, C-GET, C-MOVE)
    try:
        data["dataset"] = _safe_dataset(evt.identifier)
    except AttributeError:
        # NOTE: we don't care. probably not the right type of event
        pass

    # C-STORE received dataset — bulk-data tags excluded to prevent OOM
    try:
        data["dataset"] = _safe_dataset(evt.dataset)
    except Exception:
        pass

    return data


def new_request(evt: Event) -> EventMessage:
    return EventMessage(evt, "request", data=parse_event(evt))

def new_response(evt: Event, status: QRStatus, data: Optional[dict] = None, error: Optional[str] = None) -> EventMessage:
    return EventMessage(evt, "response", status, data, error)

def config_bus_time_rotation(bus: Logger, stdout: str, when: str = "w", interval: int = 1) -> Logger:
    h = TimedRotatingFileHandler(stdout, when=when, interval=interval)
    bus.addHandler(h)
    return bus

def config_bus_size_rotation(bus: Logger, stdout: str, size: int = 1024) -> Logger:
    h = RotatingFileHandler(stdout, maxBytes=size)
    bus.addHandler(h)
    return bus

def new_bus(stdout: Optional[str] = None, when: Optional[str] = None, interval: int = 1, size: Optional[int] = None) -> Logger:
    lg = logging.getLogger("bus")
    lg.setLevel(logging.INFO)

    if not stdout:
        return lg

    # create the directory if it does not exist
    Path(stdout).parent.mkdir(parents=True, exist_ok=True)

    if when:
        config_bus_time_rotation(lg, stdout, when, interval)
    elif size:
        config_bus_size_rotation(lg, stdout, size)
    else:
        lg.addHandler(logging.FileHandler(stdout))

    return lg
