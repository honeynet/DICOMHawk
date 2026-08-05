"""Compile + scan with YARA. Static matching only; never executes the scanned bytes."""

import hashlib
import logging
from pathlib import Path

import yara

logger = logging.getLogger(__name__)

_MAX_MATCHES = 20
_MATCH_TIMEOUT_SECONDS = 5


def _yar_files(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return sorted(directory.glob("*.yar"))


def compile_rules(
    shipped_dir: Path, operator_dir: str | None = None
) -> tuple["yara.Rules | None", str | None, list[str]]:
    """Compile shipped + operator .yar files under separate namespaces; a bad operator rule is skipped, not fatal."""
    filepaths: dict[str, str] = {}
    sources: list[tuple[str, bytes]] = []
    problems: list[str] = []

    for path in _yar_files(shipped_dir):
        filepaths[f"shipped/{path.stem}"] = str(path)
        sources.append((f"shipped/{path.name}", path.read_bytes()))

    for path in _yar_files(Path(operator_dir) if operator_dir else None):
        try:
            yara.compile(
                filepath=str(path), includes=False
            )  # validate in isolation first
        except yara.Error as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        filepaths[f"operator/{path.stem}"] = str(path)
        sources.append((f"operator/{path.name}", path.read_bytes()))

    if not filepaths:
        return None, None, problems

    try:
        rules = yara.compile(filepaths=filepaths, includes=False)
    except yara.Error as exc:
        problems.append(f"ruleset compile failed: {exc}")
        return None, None, problems

    digest = hashlib.sha256()
    for namespace, source in sorted(sources):
        encoded_name = namespace.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    ruleset_hash = digest.hexdigest()
    return rules, ruleset_hash, problems


def scan(
    rules: "yara.Rules | None", data: bytes, timeout: int = _MATCH_TIMEOUT_SECONDS
) -> tuple[list[dict], str | None]:
    """Returns (bounded rule/tag/meta matches, error state). 'timeout' is a state, not a crash."""
    if rules is None:
        return [], None
    try:
        matches = rules.match(data=data, timeout=timeout)
    except yara.TimeoutError:
        return [], "timeout"
    except yara.Error as exc:
        logger.warning("YARA scan failed: %s", exc)
        return [], "error"

    return [
        {
            "rule": m.rule,
            "namespace": m.namespace,
            "tags": list(m.tags),
            "meta": dict(m.meta),
        }
        for m in matches[:_MAX_MATCHES]
    ], None
