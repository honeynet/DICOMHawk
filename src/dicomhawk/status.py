from enum import IntEnum, StrEnum

class QRStatus(IntEnum):
    SUCCESS = 0x0000
    PENDING = 0xFF00
    CANCEL =  0xFE00
    FAILURE = 0xC000

    SOP_CLASS_NOT_SUPPORTED = 0x0122
    SOP_CLASS_INVALID = 0xA900 # Identifier does not match SOP class
    
    STORE_ERROR = 0xA700

class QRLevel(StrEnum):
    STUDY = "STUDY"
    SERIES = "SERIES"
    PATIENT = "PATIENT"