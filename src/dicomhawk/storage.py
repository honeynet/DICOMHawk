from contextlib import contextmanager
import tempfile
import gzip
import shutil

from pathlib import Path
from datetime import datetime
from uuid import uuid4


def _jailed_path(root: Path, filename: str) -> Path:
    """Resolve path under root. Raises ValueError if it would escape the jail."""
    if Path(filename).is_absolute():
        raise ValueError("filename must be relative")
    root_resolved = root.resolve()
    full = (root / filename).resolve()
    if not full.is_relative_to(root_resolved):
        raise ValueError("path escapes jail")
    return full


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

    def path_for(self, safe: bool, filename: str) -> Path:
        """Path inside the jail for filename. Raises ValueError if path would escape."""
        root = self.storage_dir if safe else self.quarantine_dir
        return _jailed_path(root, filename)
    
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
        compressed_path = (
            self.traces_dir / path.name
        ).with_suffix(path.suffix + compress_suffix)

        with path.open("rb") as f_in, gzip.open(compressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        return compressed_path
    

def new_store(traces: str) -> Storage:
    return Storage(traces)