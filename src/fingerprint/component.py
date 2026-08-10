import json
import logging
import uuid

from dicomhawk.component import Component

from .config import FingerprintConfig
from .signals import evaluate, sanitize, stable_hash
from .store import FingerprintStore, new_fingerprint_store

logger = logging.getLogger(__name__)


class FingerprintComponent(Component):
    """Owns the fingerprint store's lifecycle and exposes the sink the web layer submits through."""

    def __init__(self, config: FingerprintConfig):
        self.config = config
        # Constructed (not started) so the web component can hold this reference before start().
        self.store: FingerprintStore = new_fingerprint_store(
            config.DB_PATH, config.MAX_PER_SESSION, config.MAX_PER_IP
        )

    def start(self) -> None:
        try:
            self.store.start()
        except Exception:
            # An optional intel feature must never take the honeypot down with it.
            logger.exception(
                "Fingerprinting disabled: could not open %s", self.config.DB_PATH
            )
            return
        logger.info(
            "Fingerprinting: enabled, db=%s max_body=%s max_per_session=%s max_per_ip=%s",
            self.config.DB_PATH,
            self.config.MAX_BODY_BYTES,
            self.config.MAX_PER_SESSION,
            self.config.MAX_PER_IP,
        )

    def stop(self) -> None:
        self.store.stop()

    def sink(
        self,
        body: bytes,
        *,
        session_id: str | None,
        ip: str | None,
        local_port: int | None,
        path: str | None,
        user_agent: str | None,
    ) -> str | None:
        """Parse, bound, hash, evaluate and store one submission; returns its hash, or None if not stored."""
        try:
            if not body or len(body) > self.config.MAX_BODY_BYTES:
                return None
            payload = json.loads(body)
            signals, source_errors = sanitize(payload, self.config.MAX_VALUE_CHARS)
            if not signals:
                return None
            fingerprint_hash = stable_hash(signals)
            checks, verdict = evaluate(signals, user_agent)
            stored = self.store.record(
                fingerprint_id=str(uuid.uuid4()),
                fingerprint_hash=fingerprint_hash,
                session_id=session_id,
                ip=ip,
                local_port=local_port,
                path=path,
                user_agent=user_agent,
                signals=signals,
                bot_checks=checks,
                bot_verdict=verdict,
                source_errors=source_errors,
            )
            return fingerprint_hash if stored else None
        except Exception:
            # Never propagate: a store problem must not change what the peer sees.
            logger.exception("Fingerprint submission could not be stored")
            return None


def new_fingerprint_component(config: FingerprintConfig) -> FingerprintComponent:
    return FingerprintComponent(config)
