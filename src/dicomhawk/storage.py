from contextlib import contextmanager
import tempfile
import gzip
import shutil

from pathlib import Path
from datetime import datetime
from uuid import uuid4

from path_jail import Jail


class Storage:
    def __init__(self, traces: str) -> None:
        self.traces_dir = Path(traces)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir = self.traces_dir / "storage"
        self.quarantine_dir = self.traces_dir / "quarantine"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._jail_storage = Jail(self.storage_dir)
        self._jail_quarantine = Jail(self.quarantine_dir)

    def jail(self, safe: bool = False) -> str:
        if safe:
            return str(self.storage_dir)
        return str(self.quarantine_dir)

    def path_for(self, safe: bool, filename: str) -> Path:
        """Path inside the jail for filename. Raises ValueError if path would escape."""
        j = self._jail_storage if safe else self._jail_quarantine
        return Path(j.join(filename))
    
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