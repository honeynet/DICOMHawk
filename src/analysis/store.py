import logging
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, create_engine, update
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

from dicomhawk.storage import SubmittedArtifact

logger = logging.getLogger(__name__)

Base = declarative_base()

# A job whose worker died this many times is a poison pill; stop feeding it to fresh workers.
MAX_ATTEMPTS = 3

_BUSY_TIMEOUT_SECONDS = 5.0  # how long a writer waits for SQLite's single write lock


class AnalysisState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    MISSING = "missing"


class ArtifactRecord(Base):
    """One captured payload's durable analysis job; the interaction log is the audit copy, this is the job store."""

    __tablename__ = "artifacts"

    artifact_id = Column(String, primary_key=True)
    capture_path = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    request_type = Column(String, nullable=False)
    disposition = Column(String, nullable=False)
    source_encoding = Column(String, nullable=False)
    session_id = Column(String, nullable=True)
    ip = Column(String, nullable=True)
    local_port = Column(Integer, nullable=True)
    sop_class_uid = Column(String, nullable=True)
    sop_instance_uid = Column(String, nullable=True)
    state = Column(String, nullable=False, default=AnalysisState.PENDING, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    analyzer_version = Column(String, nullable=True)
    ruleset_version = Column(String, nullable=True)
    matched_rules = Column(Text, nullable=True)  # comma-joined rule names, for cheap API filtering
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, index=True)
    claimed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AnalysisStore:
    """The durable artifact/job table. Own engine — must work from a separate worker process."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.engine = None
        self.session = None

    def start(self) -> "AnalysisStore":
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            poolclass=NullPool,  # a fixed pool would block C-STORE on checkout once saturated
            connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_SECONDS},
        )
        with self.engine.begin() as conn:
            # WAL lets the main process and the worker process both hold the file open concurrently.
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

    def enqueue_pending(self, artifact: SubmittedArtifact) -> str:
        record = ArtifactRecord(
            artifact_id=artifact.capture.artifact_id,
            capture_path=str(artifact.capture.path),
            size=artifact.capture.size,
            sha256=artifact.capture.sha256,
            channel=artifact.channel,
            request_type=artifact.request_type,
            disposition=artifact.disposition,
            source_encoding=artifact.source_encoding,
            session_id=artifact.session_id,
            ip=artifact.ip,
            local_port=artifact.local_port,
            sop_class_uid=artifact.sop_class_uid,
            sop_instance_uid=artifact.sop_instance_uid,
            state=AnalysisState.PENDING,
            attempts=0,
            created_at=_now(),
        )
        self.session.add(record)
        self._commit()
        return record.artifact_id

    def claim(self, artifact_id: str) -> ArtifactRecord | None:
        """Atomically move one job pending->running. None if already claimed/missing."""
        result = self.session.execute(
            update(ArtifactRecord)
            .where(
                ArtifactRecord.artifact_id == artifact_id,
                ArtifactRecord.state == AnalysisState.PENDING,
            )
            .values(
                state=AnalysisState.RUNNING,
                claimed_at=_now(),
                attempts=ArtifactRecord.attempts + 1,
            )
        )
        self._commit()
        if result.rowcount == 0:
            return None
        return self.session.get(ArtifactRecord, artifact_id)

    def pending_ids(self, limit: int = 100) -> list[str]:
        rows = (
            self.session.query(ArtifactRecord.artifact_id)
            .filter(ArtifactRecord.state == AnalysisState.PENDING)
            .order_by(ArtifactRecord.created_at)
            .limit(limit)
            .all()
        )
        return [row[0] for row in rows]

    def recover_stale(self) -> int:
        """A `running` row means a worker died mid-job; requeue it, or fail it if it keeps killing workers."""
        poisoned = self.session.execute(
            update(ArtifactRecord)
            .where(
                ArtifactRecord.state == AnalysisState.RUNNING,
                ArtifactRecord.attempts >= MAX_ATTEMPTS,
            )
            .values(
                state=AnalysisState.FAILED,
                error=f"Worker exited during analysis {MAX_ATTEMPTS} times; not retried",
                completed_at=_now(),
            )
        )
        requeued = self.session.execute(
            update(ArtifactRecord)
            .where(ArtifactRecord.state == AnalysisState.RUNNING)
            .values(state=AnalysisState.PENDING)
        )
        self._commit()
        if poisoned.rowcount:
            logger.warning(
                "Gave up on %s artifact(s) that repeatedly killed the analysis worker",
                poisoned.rowcount,
            )
        return requeued.rowcount

    def complete(
        self,
        artifact_id: str,
        *,
        result: dict,
        analyzer_version: str,
        ruleset_version: str | None,
        matched_rules: list[str] | None = None,
    ) -> None:
        self.session.execute(
            update(ArtifactRecord)
            .where(ArtifactRecord.artifact_id == artifact_id)
            .values(
                state=AnalysisState.COMPLETED,
                result=result,
                analyzer_version=analyzer_version,
                ruleset_version=ruleset_version,
                matched_rules=",".join(matched_rules) if matched_rules else None,
                completed_at=_now(),
            )
        )
        self._commit()

    def fail(
        self, artifact_id: str, error: str, *, state: AnalysisState = AnalysisState.FAILED
    ) -> None:
        self.session.execute(
            update(ArtifactRecord)
            .where(ArtifactRecord.artifact_id == artifact_id)
            .values(state=state, error=error, completed_at=_now())
        )
        self._commit()

    def mark_missing(self, artifact_id: str) -> None:
        self.fail(
            artifact_id, "Capture file missing or unreadable", state=AnalysisState.MISSING
        )

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self.session.get(ArtifactRecord, artifact_id)

    def list_artifacts(
        self,
        *,
        state: str | None = None,
        channel: str | None = None,
        ip: str | None = None,
        sha256: str | None = None,
        rule: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ArtifactRecord], int]:
        query = self.session.query(ArtifactRecord)
        if state:
            query = query.filter(ArtifactRecord.state == state)
        if channel:
            query = query.filter(ArtifactRecord.channel == channel)
        if ip:
            query = query.filter(ArtifactRecord.ip == ip)
        if sha256:
            query = query.filter(ArtifactRecord.sha256 == sha256)
        if rule:
            # Escape LIKE metacharacters: real rule names contain '_', and a bare '%' would match all.
            escaped = rule.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.filter(
                ArtifactRecord.matched_rules.like(f"%{escaped}%", escape="\\")
            )
        total = query.count()
        rows = (
            query.order_by(ArtifactRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total


def new_analysis_store(db_path: str) -> AnalysisStore:
    return AnalysisStore(db_path)
