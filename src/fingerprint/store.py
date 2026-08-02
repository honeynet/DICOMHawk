import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, String, create_engine, func
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

Base = declarative_base()

_BUSY_TIMEOUT_SECONDS = 5.0  # how long a writer waits for SQLite's single write lock


class FingerprintRecord(Base):
    """One collector submission; `signals` holds the sanitized raw components, never a bare verdict."""

    __tablename__ = "fingerprints"

    fingerprint_id = Column(String, primary_key=True)
    fingerprint_hash = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    ip = Column(String, nullable=True, index=True)
    local_port = Column(Integer, nullable=True)
    path = Column(String, nullable=True)
    # The header UA, kept separate from the JS-reported one so the two can be compared.
    user_agent = Column(String, nullable=True)
    signals = Column(JSON, nullable=False)
    bot_checks = Column(JSON, nullable=True)
    bot_verdict = Column(String, nullable=True, index=True)
    source_errors = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, index=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FingerprintStore:
    """The durable fingerprint table. Own engine and own SQLite file, separate from every other store."""

    def __init__(self, db_path: str, max_per_session: int = 20):
        self.db_path = db_path
        self.max_per_session = max_per_session
        self.engine = None
        self.session = None

    def start(self) -> "FingerprintStore":
        # Needs a real path: under NullPool a new ":memory:" connection is a fresh empty database.
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            poolclass=NullPool,  # a fixed pool would block request threads on checkout once saturated
            connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_SECONDS},
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(self.engine)
        self.session = scoped_session(sessionmaker(bind=self.engine))
        return self

    def stop(self) -> None:
        if self.session:
            self.session.remove()
        if self.engine:
            self.engine.dispose()

    def _commit(self) -> None:
        """Roll back on failure so one transient error can't poison this thread's session."""
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def ready(self) -> bool:
        """False when the store never opened; callers degrade instead of raising."""
        return self.session is not None

    def session_count(self, session_id: str | None) -> int:
        if session_id is None or not self.ready():
            return 0
        return (
            self.session.query(func.count(FingerprintRecord.fingerprint_id))
            .filter(FingerprintRecord.session_id == session_id)
            .scalar()
            or 0
        )

    def record(
        self,
        *,
        fingerprint_id: str,
        fingerprint_hash: str,
        session_id: str | None,
        ip: str | None,
        local_port: int | None,
        path: str | None,
        user_agent: str | None,
        signals: dict,
        bot_checks: list | None,
        bot_verdict: str | None,
        source_errors: int,
    ) -> bool:
        """Insert one submission. False when the store is closed or the per-session cap is reached."""
        # Counting then inserting can over-admit slightly under concurrency; this is a bound, not a quota.
        if not self.ready() or self.session_count(session_id) >= self.max_per_session:
            return False
        self.session.add(
            FingerprintRecord(
                fingerprint_id=fingerprint_id,
                fingerprint_hash=fingerprint_hash,
                session_id=session_id,
                ip=ip,
                local_port=local_port,
                path=path,
                user_agent=user_agent,
                signals=signals,
                bot_checks=bot_checks,
                bot_verdict=bot_verdict,
                source_errors=source_errors,
                created_at=_now(),
            )
        )
        self._commit()
        return True

    def get(self, fingerprint_id: str) -> FingerprintRecord | None:
        return self.session.get(FingerprintRecord, fingerprint_id)

    def list_fingerprints(
        self,
        *,
        fingerprint_hash: str | None = None,
        session_id: str | None = None,
        ip: str | None = None,
        verdict: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[FingerprintRecord], int]:
        if not self.ready():
            return [], 0
        query = self.session.query(FingerprintRecord)
        if fingerprint_hash:
            query = query.filter(FingerprintRecord.fingerprint_hash == fingerprint_hash)
        if session_id:
            query = query.filter(FingerprintRecord.session_id == session_id)
        if ip:
            query = query.filter(FingerprintRecord.ip == ip)
        if verdict:
            query = query.filter(FingerprintRecord.bot_verdict == verdict)
        total = query.count()
        rows = (
            query.order_by(FingerprintRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total


def new_fingerprint_store(db_path: str, max_per_session: int = 20) -> FingerprintStore:
    return FingerprintStore(db_path, max_per_session)
