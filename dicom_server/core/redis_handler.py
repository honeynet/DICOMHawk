from __future__ import annotations

import json
from typing import Any, Optional

from services.redis_service import IRedisService

try:
    import redis

    _REDIS_ERROR = redis.exceptions.RedisError
except Exception:  # pragma: no cover
    redis = None
    _REDIS_ERROR = Exception


class RedisClient(IRedisService):

    def __init__(
        self,
        app_logger: Any,
        exceptions_logger: Any,
        redis_client: Any,
    ) -> None:
        self.redis_client = redis_client
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger

    def close(self) -> None:
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

    def __del__(self) -> None:
        self.close()

    def is_ip_scanned(self, ip: str) -> Optional[bool]:
        try:
            return ip.encode() in self.redis_client.lrange("scannedIPs", 0, -1)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while retrieving IPs list from Redis"
            )
            return None

    def add_scanned_ip(self, ip: str) -> None:
        try:
            self.redis_client.rpush("scannedIPs", ip)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a scanned IP to the scanned list"
            )

    def add_reputation_data(self, rep_dat: dict[str, Any]) -> None:
        try:
            self.redis_client.rpush("reputation", json.dumps(rep_dat))
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while pushing repution object"
            )

    def add_request_data(self, redis_log_data: str) -> None:
        try:
            self.redis_client.rpush("requests", redis_log_data)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a request information to Redis"
            )

    def get_TCI_existing_studies(self) -> Optional[set[bytes]]:
        try:
            return set(self.redis_client.lrange("TCIA_studies", 0, -1))
        except _REDIS_ERROR:
            self.exceptions_logger.exception("Redis error while checking TCIA studies")
            return None

    def add_TCI_study(self, study_uid: str) -> None:
        try:
            self.redis_client.rpush("TCIA_studies", study_uid)
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding a TCIA studyInstanceUID"
            )

    def add_injected_file(self, patient_name: str, modality: str) -> None:
        try:
            self.redis_client.rpush(
                "injected_files",
                str({"patient_name": patient_name, "modality": modality}),
            )
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding injected file identifiers to redis"
            )

    def get_honey_url(self) -> Optional[Any]:
        try:
            return self.redis_client.get("webhook")
        except _REDIS_ERROR:
            self.exceptions_logger.exception("Redis error while getting webhook key")
            return None

    def update_files_integrity_state(self, changed_files: dict[str, Any]) -> None:
        try:
            self.redis_client.rpush("fileChange", json.dumps(changed_files))
        except _REDIS_ERROR:
            self.exceptions_logger.exception(
                "Redis error while adding integrity check identifier"
            )
