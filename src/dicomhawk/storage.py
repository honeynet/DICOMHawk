from contextlib import contextmanager
import tempfile
import gzip
import shutil

from pathlib import Path
from datetime import datetime
from uuid import uuid4

class Storage:
    def __init__(
        self,
        traces: str,
        storage: str = None,
        quarantine: str = None,
    ) -> None:
        import os
        docker = os.getenv("DOCKER", "").lower() in ("1", "true", "yes")

        self.traces_dir = Path(traces)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        # default to the paths already used in the config
        _storage   = storage   or ("/opt/dicomhawk/storage/dicom_storage"  if docker else "./storage/dicom_storage")
        _quarantine = quarantine or ("/opt/dicomhawk/storage/c_store_files" if docker else "./storage/c_store_files")

        self.storage_dir = Path(_storage)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.quarantine_dir = Path(_quarantine)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def jail(self, safe: bool = False) -> Path:
        if safe:
            return self.storage_dir
        return self.quarantine_dir

    def is_quarantined(self, filepath: str | Path) -> bool:
        try:
            Path(filepath).resolve().relative_to(self.quarantine_dir.resolve())
            return True
        except ValueError:
            return False

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