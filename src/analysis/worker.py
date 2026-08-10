"""Analysis worker process entrypoint. One job at a time, bounded, static-only."""

import logging
import queue as queue_module
import signal
import time
from pathlib import Path

from dicomhawk.bus import InteractionEvent

from . import analyzers, yara_engine
from .config import AnalysisConfig
from .store import AnalysisState, AnalysisStore, new_analysis_store

logger = logging.getLogger(__name__)

ANALYZER_VERSION = "1"
RULES_DIR = Path(__file__).parent / "rules"

_RECOVERY_INTERVAL_SECONDS = 5.0  # crash-recovery sweep for jobs the queue missed

# RLIMIT_CPU is cumulative for the process's whole life, not per job, so this is a generous backstop only.
_WORKER_CPU_BACKSTOP_SECONDS = 3600


class _JobTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _JobTimeout()


def _set_resource_limits() -> None:
    """Best-effort; not every limit is settable under every container/cgroup configuration."""
    import resource

    for res, value in (
        (
            resource.RLIMIT_CPU,
            (_WORKER_CPU_BACKSTOP_SECONDS, _WORKER_CPU_BACKSTOP_SECONDS),
        ),
        (resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024)),
        (resource.RLIMIT_NOFILE, (256, 256)),
    ):
        try:
            resource.setrlimit(res, value)
        except (ValueError, OSError) as exc:
            logger.warning("Could not set worker resource limit %s: %s", res, exc)


def _analyze(record, config: AnalysisConfig, rules) -> dict:
    data, truncated = analyzers.read_capture(
        Path(record.capture_path), config.MAX_BYTES
    )
    # Keep YARA's own deadline inside the job deadline it runs under.
    timeout = max(1, int(config.TIMEOUT))
    matches, scan_state = yara_engine.scan(rules, data, timeout=timeout)
    dicom = analyzers.extract_dicom_metadata(
        data, record.source_encoding, record.transfer_syntax_uid
    )
    result = {
        "truncated": truncated,
        "size_analyzed": len(data),
        **analyzers.compute_hashes(data),
        "entropy": analyzers.shannon_entropy(data),
        "file_type": analyzers.identify_type(data),
        "iocs": analyzers.extract_iocs(data),
        "dicom": dicom,
        "yara": {"matches": matches, "state": scan_state},
    }
    if dicom and dicom["has_encapsulated_document"]:
        result["encapsulated_document"] = _analyze_encapsulated_document(
            data,
            record.source_encoding,
            record.transfer_syntax_uid,
            config,
            rules,
            timeout,
        )
    return result


def _analyze_encapsulated_document(
    data: bytes,
    source_encoding: str,
    transfer_syntax_uid: str | None,
    config: AnalysisConfig,
    rules,
    timeout: int,
) -> dict | None:
    """Scanned separately because `at 0`/`filesize` rules only hold when the inner file is the buffer."""
    extracted = analyzers.extract_encapsulated_document(
        data, source_encoding, config.MAX_BYTES, transfer_syntax_uid
    )
    if extracted is None:
        return None
    metadata, document = extracted
    matches, scan_state = yara_engine.scan(rules, document, timeout=timeout)
    metadata["yara"] = {"matches": matches, "state": scan_state}
    return metadata


def _matched_rule_names(result: dict) -> list[str]:
    """Inner-document hits must be filterable via /api/artifacts?rule= too, so merge both scans."""
    names = [m["rule"] for m in result["yara"]["matches"]]
    document = result.get("encapsulated_document")
    if document:
        names += [m["rule"] for m in document["yara"]["matches"]]
    return list(dict.fromkeys(names))


def _run_job(
    store: AnalysisStore,
    bus,
    config: AnalysisConfig,
    rules,
    ruleset_hash,
    artifact_id: str,
) -> None:
    record = store.claim(artifact_id)
    if record is None:
        return  # already handled; reachable via both the queue and a recovery sweep

    if not Path(record.capture_path).is_file():
        store.mark_missing(artifact_id)
        bus.warning(
            InteractionEvent.background(
                "ANALYSIS",
                "ANALYSIS_FAILED",
                session_id=record.session_id,
                artifact_id=artifact_id,
                analysis={"error": "capture file missing or unreadable"},
                session_parameters=["Capture file missing or unreadable"],
            )
        )
        return

    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(max(1, int(config.TIMEOUT)))
    try:
        result = _analyze(record, config, rules)
    except _JobTimeout:
        store.fail(
            artifact_id,
            "Analysis exceeded the configured timeout",
            state=AnalysisState.TIMEOUT,
        )
        bus.warning(
            InteractionEvent.background(
                "ANALYSIS",
                "ANALYSIS_TIMEOUT",
                session_id=record.session_id,
                artifact_id=artifact_id,
                session_parameters=[f"Exceeded {config.TIMEOUT}s"],
            )
        )
        return
    except Exception as exc:
        logger.exception("Analysis job %s failed", artifact_id)
        store.fail(artifact_id, str(exc))
        bus.error(
            InteractionEvent.background(
                "ANALYSIS",
                "ANALYSIS_FAILED",
                session_id=record.session_id,
                artifact_id=artifact_id,
                analysis={"error": str(exc)},
                session_parameters=[str(exc)],
            )
        )
        return
    finally:
        signal.alarm(0)

    matched_rules = _matched_rule_names(result)
    store.complete(
        artifact_id,
        result=result,
        analyzer_version=ANALYZER_VERSION,
        ruleset_version=ruleset_hash,
        matched_rules=matched_rules,
    )
    bus.info(
        InteractionEvent.background(
            "ANALYSIS",
            "ANALYSIS_RESULT",
            session_id=record.session_id,
            artifact_id=artifact_id,
            analysis=result,
            session_parameters=[
                (
                    f"Matched: {', '.join(matched_rules)}"
                    if matched_rules
                    else "No YARA matches"
                ),
                f"Entropy: {result['entropy']:.2f}",
            ],
        )
    )


def run_worker(config: AnalysisConfig, job_queue) -> None:
    """Process entrypoint (forked by AnalysisComponent). Inherits the parent's configured `bus` logger."""
    # fork() copies the parent's signal handlers too; reset so Ctrl+C doesn't hit the child.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _set_resource_limits()
    bus = logging.getLogger("bus")
    store = new_analysis_store(config.DB_PATH).start()
    # This is the only worker, so any `running` row is from a previous worker that died mid-job.
    requeued = store.recover_stale()
    rules, ruleset_hash, problems = yara_engine.compile_rules(
        RULES_DIR, config.RULES_DIR
    )
    for problem in problems:
        logger.warning("YARA rule skipped: %s", problem)
    logger.info(
        "Analysis worker ready: ruleset=%s requeued=%s pending=%s",
        ruleset_hash,
        requeued,
        len(store.pending_ids()),
    )

    last_recovery = 0.0
    try:
        while True:
            try:
                item = job_queue.get(timeout=1.0)
            except queue_module.Empty:
                item = None
            else:
                if item is None:  # stop sentinel from AnalysisComponent.stop()
                    break
                _run_job(store, bus, config, rules, ruleset_hash, item)

            now = time.monotonic()
            if now - last_recovery >= _RECOVERY_INTERVAL_SECONDS:
                last_recovery = now
                for pending_id in store.pending_ids():
                    _run_job(store, bus, config, rules, ruleset_hash, pending_id)
    finally:
        store.stop()
