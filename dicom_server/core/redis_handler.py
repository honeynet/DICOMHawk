import json

from services.redis_service import IRedisService

try:
    import redis

    _REDIS_ERROR = redis.exceptions.RedisError
except Exception:  # pragma: no cover
    redis = None
    _REDIS_ERROR = Exception


class RedisClient(IRedisService):

    def __init__(self, app_logger, exceptions_logger, redis_client):
        self.redis_client = redis_client
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger

    def close(self):
        """Best-effort close for redis-py clients."""
        try:
            if self.redis_client is None:
                return
            close_fn = getattr(self.redis_client, "close", None)
            if callable(close_fn):
                close_fn()
            pool = getattr(self.redis_client, "connection_pool", None)
            disconnect_fn = getattr(pool, "disconnect", None)
            if callable(disconnect_fn):
                disconnect_fn()
        except Exception:
            # Don't raise during shutdown paths.
            self.exceptions_logger.exception("Unexpected error while closing Redis client")

    def __del__(self):
        self.close()

    def is_ip_scanned(self, ip):
        try:
            return ip.encode() in self.redis_client.lrange("scannedIPs", 0, -1)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while retrieving IPs list from Redis"
            )

    def add_scanned_ip(self, ip):
        try:
            self.redis_client.rpush("scannedIPs", ip)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a scanned IP to the scanned list"
            )

    def add_reputation_data(self, rep_dat):
        try:
            self.redis_client.rpush("reputation", json.dumps(rep_dat))
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while pushing repution object"
            )

    def add_request_data(self, redis_log_data):
        try:
            self.redis_client.rpush("requests", redis_log_data)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a request information to Redis"
            )

    def get_TCI_existing_studies(
        self,
    ):
        try:
            return set(self.redis_client.lrange("TCIA_studies", 0, -1))
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while checking TCIA studies"
            )

    def add_TCI_study(self, study_uid):
        try:
            self.redis_client.rpush("TCIA_studies", study_uid)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a TCIA studyInstanceUID"
            )

    def add_injected_file(self, patient_name, modality):
        try:
            self.redis_client.rpush(
                "injected_files",
                str({"patient_name": patient_name, "modality": modality}),
            )
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding injected file identifiers to redis"
            )

    def get_honey_url(
        self,
    ):
        try:
            return self.redis_client.get("webhook")
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while getting webhook key"
            )

    def update_files_integrity_state(self, changed_files):
        try:
            self.redis_client.rpush("fileChange", json.dumps(changed_files))
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding integrity check identifier"
            )
