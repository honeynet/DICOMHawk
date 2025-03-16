import json
from services.redis_service import IRedisService


class RedisClient(IRedisService):

    def __init__(self, app_logger, exceptions_logger, redis_client):

        self.redis_client = redis_client
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger

    def is_ip_scanned(self, ip):
        try:
            return ip.encode() in self.redis_client.lrange("scannedIPs", 0, -1)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while retrieving IPs list from Redis"
            )

    def add_scanned_ip(self, ip):
        try:
            self.redis_client.rpush("scannedIPs", ip)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while adding a scanned IP to the scanned list"
            )

    def add_reputation_data(self, rep_dat):
        try:
            self.redis_client.rpush("reputation", json.dumps(rep_dat))
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while pushing repution object"
            )

    def add_request_data(self, redis_log_data):
        try:
            self.redis_client.rpush("requests", redis_log_data)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while adding a request information to Redis"
            )

    def get_TCI_existing_studies(
        self,
    ):
        try:
            return set(self.redis_client.lrange("TCIA_studies", 0, -1))
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while checking TCIA studies"
            )

    def add_TCI_study(self, study_uid):
        try:
            self.redis_client.rpush("TCIA_studies", study_uid)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while adding a TCIA studyInstanceUID"
            )

    def add_injected_file(self, patient_name, modality):
        try:
            self.redis_client.rpush(
                "injected_files",
                str({"patient_name": patient_name, "modality": modality}),
            )
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while adding injected file identifiers to redis"
            )

    def get_honey_url(
        self,
    ):
        try:
            self.redis_client.get("webhook")
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while getting webhook key"
            )

    def update_files_integrity_state(self, changed_files):
        try:
            self.redis_client.rpush("fileChange", json.dumps(changed_files))
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while adding integrity check identifier"
            )
