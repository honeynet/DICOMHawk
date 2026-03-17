from contextlib import contextmanager
import tempfile
import gzip
import shutil

from pathlib import Path
from datetime import datetime
from uuid import uuid4


class Storage:
    def __init__(self, traces: str) -> None:
        self.traces_dir = Path(traces)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        # Initialize storage and quarantine directories
        self.storage_dir = self.traces_dir / "storage"
        self.quarantine_dir = self.traces_dir / "quarantine"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def jail(self, safe: bool = False) -> str:
        if safe:
            return str(self.storage_dir)
        return str(self.quarantine_dir)

    def _jailed_path(self, root: Path, filename: str) -> Path:
        """
        Resolve a path within the jail directory.
        
        Validates that the resolved path remains within the jail boundary,
        preventing path traversal attacks via crafted filenames.
        
        Args:
            root: The jail root directory (storage_dir or quarantine_dir)
            filename: The filename to validate
            
        Returns:
            Path: The resolved path if valid
            
        Raises:
            ValueError: If filename is absolute or would escape the jail,
                       including the problematic filename and resolved path
                       in the error message for debugging.
        """
        if Path(filename).is_absolute():
            raise ValueError(
                f"Absolute paths not allowed in jail. Got: {filename}"
            )
        
        root_resolved = root.resolve()
        full = (root / filename).resolve()
        
        if not full.is_relative_to(root_resolved):
            raise ValueError(
                f"Path traversal detected: filename '{filename}' resolved to "
                f"'{full}' which escapes jail boundary '{root_resolved}'"
            )
        
        return full

    def path_for(self, safe: bool, filename: str) -> Path:
        """
        Get a jailed path for the given filename.
        
        Routes to either storage_dir (safe=True) or quarantine_dir (safe=False).
        Validates that the path remains within the jail boundary.
        
        Args:
            safe: If True, use storage_dir; if False, use quarantine_dir
            filename: The filename to jail
            
        Returns:
            Path: The validated jailed path
            
        Raises:
            ValueError: If the filename would escape the jail
        """
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
        compressed_path = (
            self.traces_dir / path.name
        ).with_suffix(path.suffix + compress_suffix)

        with path.open("rb") as f_in, gzip.open(compressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        return compressed_path
    

def new_store(traces: str) -> Storage:
    return Storage(traces)
