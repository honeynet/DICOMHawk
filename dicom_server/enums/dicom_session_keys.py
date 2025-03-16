from aenum import Enum


class Sessionkeys(Enum):
    _init_ = "key default"

    LOCK = "lock", 0
    SESSION_ID = "session_id", 0
    REQUEST_TYPE = "request_type", "N/A"
    SESSION_MAIN_OPERATION = "main_operation", "N/A"
    QUERY_LEVEL = "query_level", "N/A"
    SESSION_PARAMETERS = "session_parameters", "N/A"
    LOG_LEVEL = "log_level", "N/A"
    VERSION = "version", "N/A"
    IP = "ip", "N/A"
    PORT = "port", "N/A"
    KNOWN_SCANNER = "known_scanner", "N/A"
    MATCHES = "matches", "N/A"
    STATUS = "status", "N/A"
    TIMESTAMP = "timestamp", "N/A"


class RequestType(Enum):
    ASSO_RQ = "Association Requested", 0
    ASSO_RE = "Association Released", 0
    C_ECHO = "C_ECHO", 0
    C_FIND = "C_FIND", 0
    C_GET = "C_GET", 0
    C_STORE = "C_STORE", 0
    C_MOVE = "C_MOVE"
