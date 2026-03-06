from contextlib import contextmanager
import tempfile
import gzip
import shutil

from pathlib import Path
from datetime import datetime
from uuid import uuid4

class Storage:
    storage_dir: str
    quarantine_dir: str

    def __init__(self, traces: str) -> None:
        self.traces_dir = Path(traces)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def jail(self, safe: bool = False) -> str:
        if safe:
            return self.storage_dir
        return self.quarantine_dir
    
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