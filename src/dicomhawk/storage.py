from contextlib import contextmanager
import tempfile
import gzip
import hashlib
import os
import shutil
from io import BytesIO
from typing import BinaryIO, Callable

from pathlib import Path
from datetime import datetime
from uuid import uuid4


class Capture:
    """A completed, immutable payload capture: gzip-wrapped under traces_dir."""

    def __init__(self, artifact_id: str, path: Path, size: int, sha256: str):
        self.artifact_id: str = artifact_id
        self.path: Path = path
        self.size: int = size
        self.sha256: str = sha256


class SubmittedArtifact:
    """A completed Capture plus the request context needed to correlate and analyze it."""

    def __init__(
        self,
        capture: Capture,
        *,
        channel: str,
        request_type: str,
        disposition: str,
        source_encoding: str,
        session_id: str | None,
        ip: str | None,
        local_port: int | None,
        sop_class_uid: str | None = None,
        sop_instance_uid: str | None = None,
        transfer_syntax_uid: str | None = None,
    ):
        self.capture: Capture = capture
        self.channel: str = channel
        self.request_type: str = request_type
        self.disposition: str = disposition
        # "part10" (STOW/WEB_UPLOAD, has a DICM preamble) or "dimse-dataset" (raw C-STORE wire bytes, no preamble).
        self.source_encoding: str = source_encoding
        self.session_id: str | None = session_id
        self.ip: str | None = ip
        self.local_port: int | None = local_port
        self.sop_class_uid: str | None = sop_class_uid
        self.sop_instance_uid: str | None = sop_instance_uid
        # Set only for "dimse-dataset": the association actually negotiated this, no guessing needed.
        self.transfer_syntax_uid: str | None = transfer_syntax_uid


# Ingestion call sites submit here; core never imports the concrete (analysis) implementation.
type ArtifactSink = Callable[[SubmittedArtifact], None]


class Storage:
    def __init__(self, traces: str) -> None:
        self.traces_dir = Path(traces)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir = self.traces_dir / "storage"
        self.quarantine_dir = self.traces_dir / "quarantine"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def jail(self, safe: bool = False) -> str:
        if safe:
            return str(self.storage_dir)
        return str(self.quarantine_dir)

    def _jailed_path(self, root: Path, filename: str) -> Path:
        """Resolve filename under root, rejecting absolute paths and traversal escapes."""
        if Path(filename).is_absolute():
            raise ValueError(f"Absolute paths not allowed in jail. Got: {filename}")

        root_resolved = root.resolve()
        full = (root / filename).resolve()
        if not full.is_relative_to(root_resolved):
            raise ValueError(
                f"Path traversal detected: filename '{filename}' resolved to "
                f"'{full}' which escapes jail boundary '{root_resolved}'"
            )
        return full

    def is_quarantined(self, path: str) -> bool:
        """Return True if path is inside the quarantine directory."""
        try:
            Path(path).resolve().relative_to(self.quarantine_dir.resolve())
            return True
        except ValueError:
            return False

    def path_for(self, safe: bool, filename: str) -> Path:
        """Jailed path for filename under storage_dir (safe) or quarantine_dir."""
        root = self.storage_dir if safe else self.quarantine_dir
        return self._jailed_path(root, filename)

    @contextmanager
    def temp(self, suffix=".dcm"):
        date_name = datetime.now().strftime("%YY%mm%dd_%HH%MM%SS")
        filename = f"{date_name}_{uuid4().hex}{suffix}"

        tmp_dir = Path(tempfile.gettempdir())
        path = tmp_dir / filename

        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def compress(self, path: Path, compress_suffix=".gz") -> Path:
        compressed_path = (self.traces_dir / path.name).with_suffix(
            path.suffix + compress_suffix
        )

        with path.open("rb") as f_in, gzip.open(compressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        return compressed_path

    def _capture_path(self, artifact_id: str, suffix: str) -> Path:
        date_name = datetime.now().strftime("%YY%mm%dd_%HH%MM%SS")
        return self.traces_dir / f"{date_name}_{artifact_id}{suffix}.gz"

    def capture(self, payload: bytes, suffix: str = ".dcm") -> Capture:
        """Preserve an exact attacker-supplied payload as a uniquely named gzip trace."""
        artifact_id = uuid4().hex
        captured = self._capture_path(artifact_id, suffix)
        partial = captured.with_name(f".{captured.name}.part")
        try:
            with gzip.open(partial, "wb") as output:
                shutil.copyfileobj(BytesIO(payload), output)
            os.replace(partial, captured)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return Capture(
            artifact_id, captured, len(payload), hashlib.sha256(payload).hexdigest()
        )

    def capture_fileobj(self, source: BinaryIO, suffix: str = ".dcm") -> Capture:
        """Preserve a seekable input stream without loading it all into memory."""
        position = source.tell()
        artifact_id = uuid4().hex
        captured = self._capture_path(artifact_id, suffix)
        partial = captured.with_name(f".{captured.name}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            source.seek(0)
            with gzip.open(partial, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    output.write(chunk)
            os.replace(partial, captured)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            source.seek(position)
        return Capture(artifact_id, captured, size, digest.hexdigest())

    @contextmanager
    def capture_stream(self, suffix: str = ".bin"):
        """Yield a writer that gzip-preserves a request incrementally; call .result() after writing."""
        artifact_id = uuid4().hex
        captured = self._capture_path(artifact_id, suffix)
        partial = captured.with_name(f".{captured.name}.part")
        try:
            with gzip.open(partial, "wb") as output:
                yield _CaptureWriter(artifact_id, captured, output)
            os.replace(partial, captured)
        except Exception:
            partial.unlink(missing_ok=True)
            raise


class _CaptureWriter:
    """Tracks hash/size across incremental writes, then finalizes into a Capture."""

    def __init__(self, artifact_id: str, path: Path, output: BinaryIO) -> None:
        self._artifact_id = artifact_id
        self._path = path
        self._output = output
        self._digest = hashlib.sha256()
        self._size = 0

    def write(self, chunk: bytes) -> None:
        self._digest.update(chunk)
        self._size += len(chunk)
        self._output.write(chunk)

    def result(self) -> Capture:
        return Capture(
            self._artifact_id, self._path, self._size, self._digest.hexdigest()
        )


def new_store(traces: str) -> Storage:
    return Storage(traces)
