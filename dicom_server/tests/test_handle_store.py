"""
C-STORE Handler Test Suite

Validates store_received_file() and handle_store() behavior:
- UID-based directory layout when all UIDs are present
- Timestamp-based fallback when UIDs are missing or unsafe
- Directory auto-creation (os.makedirs)
- handle_store() returns 0x0000 on success
- handle_store() returns 0xA700 when the save fails
- UID sanitization rejects path-traversal / unexpected characters
"""

import sys
import os

sys.path.append(os.path.abspath("../"))
sys.path.append(os.path.abspath("../pydicom_and_pynetdicom_libs/"))

import pytest
from unittest.mock import MagicMock, patch, call
from pydicom import Dataset
from pydicom.dataset import FileMetaDataset
from pydicom.uid import UID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(study=None, series=None, sop=None, remote_ip="127.0.0.1", ae="SCU"):
    """Build a minimal mock event as pynetdicom would pass to a C-STORE handler."""
    ds = Dataset()
    if study:
        ds.StudyInstanceUID = study
    if series:
        ds.SeriesInstanceUID = series
    if sop:
        ds.SOPInstanceUID = sop

    file_meta = FileMetaDataset()
    file_meta.TransferSyntaxUID = UID("1.2.840.10008.1.2.1")

    assoc = MagicMock()
    assoc.requestor.address = remote_ip
    assoc.requestor.ae_title = ae

    event = MagicMock()
    event.dataset = ds
    event.file_meta = file_meta
    event.assoc = assoc
    return event


# ---------------------------------------------------------------------------
# store_received_file tests
# ---------------------------------------------------------------------------

class TestStoreReceivedFile:

    def test_uid_based_path_when_all_uids_present(self, tmp_path):
        """Files are stored under Study/Series/SOP.dcm when all UIDs are valid."""
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)

        study  = "1.2.3"
        series = "1.2.3.4"
        sop    = "1.2.3.4.5"
        event  = _make_event(study=study, series=series, sop=sop)

        try:
            from utilities.dicom_util import store_received_file
            store_received_file(event)
        finally:
            cfg.C_STORE_STORAGE = original

        expected = tmp_path / study / series / (sop + ".dcm")
        assert expected.exists(), f"Expected file at {expected}"

    def test_timestamp_fallback_when_uid_missing(self, tmp_path):
        """Falls back to flat received_<timestamp>.dcm when SOPInstanceUID is absent."""
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)

        event = _make_event(study="1.2.3", series="1.2.3.4")  # no SOP UID

        try:
            from utilities.dicom_util import store_received_file
            store_received_file(event)
        finally:
            cfg.C_STORE_STORAGE = original

        saved = list(tmp_path.glob("received_*.dcm"))
        assert len(saved) == 1, "Expected exactly one timestamp-based fallback file"

    def test_timestamp_fallback_when_uid_unsafe(self, tmp_path):
        """Rejects UIDs with path-traversal characters and uses timestamp fallback."""
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)

        event = _make_event(study="../evil", series="1.2.3", sop="1.2.3.4")

        try:
            from utilities.dicom_util import store_received_file
            store_received_file(event)
        finally:
            cfg.C_STORE_STORAGE = original

        # No subdirectory for the unsafe study UID should be created
        assert not (tmp_path / "../evil").exists()
        saved = list(tmp_path.glob("received_*.dcm"))
        assert len(saved) == 1

    def test_directory_created_automatically(self, tmp_path):
        """os.makedirs is called so missing intermediate directories are created."""
        import config as cfg
        original = cfg.C_STORE_STORAGE
        deep_dir = tmp_path / "new_root"
        cfg.C_STORE_STORAGE = str(deep_dir)

        event = _make_event(study="1.1", series="2.2", sop="3.3")

        try:
            from utilities.dicom_util import store_received_file
            store_received_file(event)
        finally:
            cfg.C_STORE_STORAGE = original

        assert (deep_dir / "1.1" / "2.2" / "3.3.dcm").exists()

    def test_raises_on_save_failure(self, tmp_path):
        """store_received_file propagates exceptions so handle_store can react."""
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)

        event = _make_event(study="1.1", series="2.2", sop="3.3")

        with patch("pydicom.Dataset.save_as", side_effect=OSError("disk full")):
            try:
                from utilities.dicom_util import store_received_file
                with pytest.raises(OSError):
                    store_received_file(event)
            finally:
                cfg.C_STORE_STORAGE = original


# ---------------------------------------------------------------------------
# handle_store tests
# ---------------------------------------------------------------------------

class TestHandleStore:

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Import app_container lazily so sys.path additions take effect first."""
        import app_container
        container = app_container.ApplicationContainer()
        self.handlers = container.dicom_handlers()

    def _make_store_event(self, **kwargs):
        return _make_event(**kwargs)

    def test_returns_success_on_save(self, tmp_path):
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)
        event = self._make_store_event(study="1.1", series="2.2", sop="3.3")
        try:
            result = self.handlers.handle_store(event)
        finally:
            cfg.C_STORE_STORAGE = original
        assert result == 0x0000

    def test_returns_out_of_resources_on_failure(self, tmp_path):
        import config as cfg
        original = cfg.C_STORE_STORAGE
        cfg.C_STORE_STORAGE = str(tmp_path)
        event = self._make_store_event(study="1.1", series="2.2", sop="3.3")
        with patch(
            "utilities.dicom_util.store_received_file",
            side_effect=OSError("cannot write"),
        ):
            try:
                result = self.handlers.handle_store(event)
            finally:
                cfg.C_STORE_STORAGE = original
        # 0xA700 = Out of Resources
        assert result == 0xA700


# ---------------------------------------------------------------------------
# _sanitize_uid unit tests
# ---------------------------------------------------------------------------

class TestSanitizeUid:

    def _fn(self):
        from utilities.dicom_util import _sanitize_uid
        return _sanitize_uid

    def test_valid_uid(self):
        assert self._fn()("1.2.840.10008.1.2") == "1.2.840.10008.1.2"

    def test_none_returns_none(self):
        assert self._fn()(None) is None

    def test_empty_string_returns_none(self):
        assert self._fn()("") is None

    def test_path_traversal_returns_none(self):
        assert self._fn()("../etc/passwd") is None

    def test_uid_with_letters_returns_none(self):
        assert self._fn()("1.abc.2") is None


pytest.main(["-v", "test_handle_store.py"])
