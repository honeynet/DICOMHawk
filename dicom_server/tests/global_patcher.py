import sys, os
from unittest.mock import patch, Mock

sys.path.append(os.path.abspath(".."))


patch.multiple(
    "config",
    DICOM_DATABASE="./mock_database.db",
    DICOM_STORAGE_DIR="./mock_dicom_files/",
    FLASK_ACTIVATED=False,
).start()

patch.multiple(
    "utilities.dicom_util",
    assign_runtime_contexts_support=Mock(return_value=""),
).start()
