""" This module tests the functionality of the dicom_handlers handle_get() function for retrieving both uncompressed and compressed DICOM files.
Asseretations: 

- The correct number of datasets is returned.

- The returned files contain the expected patient names.

- The transfer syntax is correctly handled (compressed or uncompressed)

"""

import sys
import os

sys.path.append(os.path.abspath("../core"))
sys.path.append(os.path.abspath("../pydicom_and_pynetdicom_libs/"))

import pytest
from pydicom import Dataset
from pydicom.uid import UID
from unittest.mock import Mock
from pynetdicom.sop_class import CTImageStorage
import global_patcher
import app_container

test_container = app_container.ApplicationContainer()
dicom_handlers = test_container.dicom_handlers()


@pytest.fixture
def event_uncompress_retrieve():

    event = Mock()
    event.event = Mock()
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = "1.3.6.1.4.1.9328.50.4.1240"
    event.identifier = ds

    event.context = {
        "abstract_syntax": CTImageStorage,
        "transfer_syntax": "1.2.840.10008.1.2.1",  # Little Endian
    }
    return event


@pytest.fixture
def event_compress_retrieve():

    event = Mock()
    event.event = Mock()
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = (
        "1.2.826.0.1.3680043.8.1055.1.20111102150758591.92402465.76075429"
    )
    event.identifier = ds

    event.context = {
        "abstract_syntax": "1.2.840.10008.5.1.4.1.1.77.1.5.4",
        "transfer_syntax": "1.2.840.10008.1.2.4.91",
    }
    return event


def test_get_uncompressed_file_using_little_Endian(event_uncompress_retrieve: Mock):
    """
    Tests the retrieval of an uncompressed
    DICOM file using Little Endian transfer syntax

    """

    gen = test_container.dicom_handlers().handle_get(event_uncompress_retrieve)
    result = list(gen)
    print("Reees", result[3])

    EXCPECTED_RETURNED_SETS = 1
    EXCPECTED_PATIONT_NAME_FOR_THIS_STUDY = "Oliver Møller"
    # Assert returning the excpected number of set
    assert result[0] == EXCPECTED_RETURNED_SETS
    RETURNED_FILE = result[2][1]
    # Assert the handle_get returns a dataset value
    assert not RETURNED_FILE == None
    # Assert the Excpected file's patient name based on the mock studyInstanceUID
    assert RETURNED_FILE.PatientName == EXCPECTED_PATIONT_NAME_FOR_THIS_STUDY
    # Assert the file is not commpressed
    assert not RETURNED_FILE.file_meta.TransferSyntaxUID.is_compressed


def test_get_compressed_file_using_JPEG2000(event_compress_retrieve: Mock):
    """
    Tests the retrieval of a compressed DICOM
    file using JPEG 2000 transfer syntax

    """

    # Assert the use of compressed transfer syntax to retrieve the file
    assert UID(event_compress_retrieve.context["transfer_syntax"]).is_compressed
    gen = dicom_handlers.handle_get(event_compress_retrieve)
    result = list(gen)
    EXCPECTED_RETURNED_SETS = 1
    EXCPECTED_PATIONT_NAME_FOR_THIS_STUDY = "Ronnie Nikolajsen"
    # Assert returning the excpected number of set
    assert result[0] == EXCPECTED_RETURNED_SETS
    print(result[1])
    RETURNED_FILE = result[2][1]
    # Assert the handle_get returns a dataset value
    assert not RETURNED_FILE == None
    # Assert the Excpected file's patient name based on the mock studyInstanceUID
    assert RETURNED_FILE.PatientName == EXCPECTED_PATIONT_NAME_FOR_THIS_STUDY
    # Assert the file returned is decommpressed
    assert not RETURNED_FILE.file_meta.TransferSyntaxUID.is_compressed


pytest.main(["-v", "test_handle_get.py"])
