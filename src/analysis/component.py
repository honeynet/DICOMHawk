import logging
import multiprocessing
import queue
import threading
import time

from dicomhawk.bus import InteractionEvent
from dicomhawk.component import Component
from dicomhawk.storage import SubmittedArtifact

from .config import AnalysisConfig
from .store import AnalysisStore, new_analysis_store
from .worker import run_worker

logger = logging.getLogger(__name__)

_STOP_GRACE_SECONDS = 15.0
_RESTART_BACKOFF_CAP_SECONDS = 60.0
_RESTART_HEALTHY_SECONDS = 60.0  # a worker that lasted this long counts as a one-off, not a crash loop


class AnalysisComponent(Component):
    """Supervises a bounded worker PROCESS running static payload analysis; isolates crashes, not the filesystem."""

    def __init__(self, config: AnalysisConfig, bus: logging.Logger):
        self.config = config
        self.bus = bus
        # Constructed (not started) so other components can hold this reference before start().
        self.store: AnalysisStore = new_analysis_store(config.DB_PATH)
        self._queue: multiprocessing.Queue | None = None
        self._process: multiprocessing.Process | None = None
        self._spawned_at = 0.0
        self._supervisor: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self.store.start()
            recovered = self.store.recover_stale()
        except Exception:
            # An optional analysis feature must never take the honeypot down with it.
            logger.exception("Analysis disabled: could not open %s", self.config.DB_PATH)
            return
        self._queue = multiprocessing.Queue(maxsize=self.config.QUEUE_SIZE)
        self._stopping.clear()
        self._spawn()
        self._supervisor = threading.Thread(
            target=self._supervise, daemon=True, name="dicomhawk-analysis-supervisor"
        )
        self._supervisor.start()
        logger.info(
            "Analysis: enabled, db=%s timeout=%ss max_bytes=%s queue_size=%s recovered=%s",
            self.config.DB_PATH,
            self.config.TIMEOUT,
            self.config.MAX_BYTES,
            self.config.QUEUE_SIZE,
            recovered,
        )

    def _spawn(self) -> None:
        self._process = multiprocessing.Process(
            target=run_worker,
            args=(self.config, self._queue),
            daemon=True,
            name="dicomhawk-analysis-worker",
        )
        self._process.start()
        self._spawned_at = time.monotonic()

    def _supervise(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            process = self._process
            if process is None:
                self._stopping.wait(1.0)
                continue
            process.join(timeout=1.0)
            if self._stopping.is_set():
                return
            if process.is_alive():
                continue
            if time.monotonic() - self._spawned_at >= _RESTART_HEALTHY_SECONDS:
                failures = 0
            failures += 1
            # Back off, or a worker that dies on startup becomes an unbounded fork loop.
            delay = min(_RESTART_BACKOFF_CAP_SECONDS, 2.0**min(failures, 6))
            logger.warning(
                "Analysis worker exited (code=%s); restarting in %.0fs",
                process.exitcode,
                delay,
            )
            if self._stopping.wait(delay):
                return
            self._spawn()

    def stop(self) -> None:
        self._stopping.set()
        # Join the supervisor first so only this thread touches the worker process below.
        if self._supervisor is not None:
            self._supervisor.join(timeout=_RESTART_BACKOFF_CAP_SECONDS + 5.0)
            self._supervisor = None
        if self._queue is not None:
            try:
                self._queue.put(None, timeout=1.0)
            except (queue.Full, ValueError, OSError):
                pass
        if self._process is not None:
            self._process.join(timeout=_STOP_GRACE_SECONDS)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=5.0)
            self._process = None
        self.store.stop()

    def sink(self, artifact: SubmittedArtifact) -> None:
        """The ArtifactSink for DIMSE/web/DICOMweb ingestion; must never raise or block into a response."""
        if not self.store.ready():
            return  # store never opened; the capture itself is unaffected
        try:
            artifact_id = self.store.enqueue_pending(artifact)
        except Exception as exc:
            logger.warning("Could not record artifact for analysis: %s", exc)
            self.bus.warning(
                InteractionEvent.background(
                    "ANALYSIS",
                    "ANALYSIS_ENQUEUE_FAILED",
                    session_id=artifact.session_id,
                    analysis={"error": str(exc)},
                    session_parameters=[f"Artifact not queued for analysis: {exc}"],
                )
            )
            return
        if self._queue is None:
            return  # not started; the durable pending row is picked up by the next startup sweep
        try:
            self._queue.put_nowait(artifact_id)
        except (queue.Full, ValueError, OSError):
            self.bus.warning(
                InteractionEvent.background(
                    "ANALYSIS",
                    "ANALYSIS_BACKLOG",
                    session_id=artifact.session_id,
                    artifact_id=artifact_id,
                    session_parameters=["Queue full; job stays pending for the recovery sweep"],
                )
            )


def new_analysis_component(config: AnalysisConfig, bus: logging.Logger) -> AnalysisComponent:
    return AnalysisComponent(config, bus)
