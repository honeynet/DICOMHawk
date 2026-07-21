from contextlib import contextmanager
import tempfile
import gzip
import shutil
from io import BytesIO

from pathlib import Path
from datetime import datetime
from uuid import uuid4


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

    def capture(self, payload: bytes, suffix: str = ".dcm") -> Path:
        """Preserve an exact attacker-supplied payload as a uniquely named gzip trace."""
        date_name = datetime.now().strftime("%YY%mm%dd_%HH%MM%SS")
        filename = f"{date_name}_{uuid4().hex}{suffix}.gz"
        captured = self.traces_dir / filename
        with gzip.open(captured, "wb") as output:
            shutil.copyfileobj(BytesIO(payload), output)
        return captured

    @contextmanager
    def capture_stream(self, suffix: str = ".bin"):
        """Yield a gzip writer that preserves a request incrementally without buffering it."""
        date_name = datetime.now().strftime("%YY%mm%dd_%HH%MM%SS")
        filename = f"{date_name}_{uuid4().hex}{suffix}.gz"
        captured = self.traces_dir / filename
        with gzip.open(captured, "wb") as output:
            yield output


def new_store(traces: str) -> Storage:
    return Storage(traces)
