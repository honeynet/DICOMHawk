"""
DICOM C-FIND Handler Test Suite

This test module aims to validate the behavior of the handle_find() method 
within multiple scenarios. To ensure predictable and isolated testing, the following
were used:

1. ** Mock Data Hardcoded**:
   - A mock DICOM files storages with 5 pre-defined DICOM files was created.
   - A mock database was initialized from the mock files storage.
   - Mock events were created using pytest.fixture.

2. **Mocked Events**:
   - DICOM C-FIND events were simulated using `pydicom.Dataset` objects.
   - Events cover various scenarios:
     - Finding all studies
     - finding specific study by `StudyInstanceUID`
     - Finding all Patients
     - finding specific Patient by `PatientID`
     - finding specific serie by `SerieInstanceUID`
     - Invalid SOP Class UID
     - Invalid identifier attributes

3. **Behavioral Expectations**:
   - Tests validate both the response status codes (e.g., 0xFF00 for pending, 0x0000 for success)
     and the structure of returned datasets.
   - Assertions are based on the expected behavior of the DICOM protocol and the application logic.

4. **Isolation**:
   - Database sessions are mocked to avoid run-time mismatching.
   - Logging is suppressed focus on functional validation (Other tests will take the logging) .
"""

import sys
import os

sys.path.append(os.path.abspath("../core/"))
sys.path.append(os.path.abspath("../pydicom_and_pynetdicom_libs/"))
import pytest
from unittest.mock import Mock, patch
from pydicom import Dataset
import global_patcher


@pytest.fixture
def event_retreive_all_studies():
    event = Mock()
    request = Mock()
    event.request = request
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    event.identifier = ds
    return event


@pytest.fixture
def event_retrieve_specific_study():
    event = Mock()
    request = Mock()
    event.request = request
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = (
        "1.3.6.1.4.1.14519.5.2.1.3344.4004.189845485896538626080129650333"
    )
    event.identifier = ds
    return event


@pytest.fixture
def event_retreive_all_patients():
    event = Mock()
    request = Mock()
    event.request = request
    ds = Dataset()
    ds.QueryRetrieveLevel = "PATIENT"
    event.identifier = ds
    return event


@pytest.fixture
def event_retrieve_specific_patient():
    event = Mock()
    request = Mock()
    event.request = request
    ds = Dataset()
    ds.QueryRetrieveLevel = "PATIENT"
    ds.PatientID = "6767"
    event.identifier = ds
    return event


@pytest.fixture
def event_retrieve_series_for_specific_study():
    event = Mock()
    request = Mock()
    event.request = request
    ds = Dataset()
    ds.QueryRetrieveLevel = "SERIES"
    ds.StudyInstanceUID = (
        "1.3.6.1.4.1.14519.5.2.1.3344.4004.189845485896538626080129650333"
    )
    event.identifier = ds
    return event


"""
Before all tests we patch the real database by initialize mock one with four studies for four different patients from a mock dicom files storage
(Oliver Møller, Amanda Larsen, Agnete Østergaard, Ronnie Nikolajsen and Jarl Frederiksen)

"""


@pytest.fixture
def mock_dicomdb():
    return Mock()


import app_container

test_container = app_container.ApplicationContainer()
dicom_handlers = test_container.dicom_handlers()


def test_invalid_sop_class(event_retrieve_specific_study):
    "" " Test handling of invalid  SOP Class UID" ""
    with patch.multiple(
        "utilities.dicom_util",
        is_sopclassuid_valid=Mock(return_value=True),
        identifier_invalid=Mock(return_value=False),
    ):
        gen = dicom_handlers.handle_find(event_retrieve_specific_study)
        results = list(gen)

    assert len(results) == 1
    assert results[0][0] == 0xA900


def test_invalid_identifier(event_retrieve_specific_study):
    """handling of invalid identifier attributes"""
    with patch.multiple(
        "utilities.dicom_util",
        is_sopclassuid_valid=Mock(return_value=False),
        identifier_invalid=Mock(return_value=True),
    ):
        gen = dicom_handlers.handle_find(event_retrieve_specific_study)
        results = list(gen)

    assert len(results) == 1
    assert results[0][0] == 0xC006


def test_find_all_studies(event_retreive_all_studies):

    gen = dicom_handlers.handle_find(event_retreive_all_studies)
    results = list(gen)
    EXPECTED_MATCHES = 5
    EXPECTED_TOTAL = EXPECTED_MATCHES + 1
    assert len(results) == EXPECTED_TOTAL
    assert all(r[0] == 0xFF00 for r in results[:-1])
    assert results[-1][0] == 0x0000


def test_find_specific_study(event_retrieve_specific_study):

    gen = dicom_handlers.handle_find(event_retrieve_specific_study)
    results = list(gen)
    EXPECTED_MATCHES = 1
    EXPECTED_TOTAL = EXPECTED_MATCHES + 1
    for status, dataset in results[:-1]:
        assert status == 0xFF00
        assert isinstance(dataset, Dataset)
        assert dataset.StudyInstanceUID
    assert len(results) == EXPECTED_TOTAL
    assert all(r[0] == 0xFF00 for r in results[:-1])
    assert results[-1][0] == 0x0000


def test_find_all_patients(event_retreive_all_patients):

    EXPECTED_PATIENTS = 5
    EXPECTED_TOTAL = EXPECTED_PATIENTS + 1
    gen = dicom_handlers.handle_find(event_retreive_all_patients)
    results = list(gen)
    assert len(results) == EXPECTED_TOTAL
    assert all(r[0] == 0xFF00 for r in results[:-1])
    assert results[-1][0] == 0x0000


# The event for this test includes a query with the PatientID  for patient "Amanda Larsen"
# the test pass if one dataset with a patient name of "Amanda Larsen" is returned in the retrieved dataset
def test_find_specific_patient(event_retrieve_specific_patient):
    gen = dicom_handlers.handle_find(event_retrieve_specific_patient)
    results = list(gen)
    EXPECTED_MATCHES = 1
    EXPECTED_TOTAL = EXPECTED_MATCHES + 1
    EXCPECTED_PATIENT_NAME = "Amanda Larsen"
    for status, dataset in results[:-1]:
        assert status == 0xFF00
        assert isinstance(dataset, Dataset)
        assert dataset.PatientName
        # Assert that Patient level returns no attributes from other levels
        assert not hasattr(dataset, "StudyInstanceUID")
        assert not hasattr(dataset, "SeriesInstanceUID")
    assert results[0][1].PatientName == EXCPECTED_PATIENT_NAME
    assert len(results) == EXPECTED_TOTAL
    assert all(r[0] == 0xFF00 for r in results[:-1])
    assert results[-1][0] == 0x0000


# The event for this test includes a query with the studyinstanceUID for a study for patient "Agnete Østergaard"
# the test pass if one dataset with a seriesInstanceUID of "1.3.6.1.4.1.14519.5.2.1.3344.4004.838622281675929509435013026326" and series number of "6" is returned in the retrieved dataset
def test_find_specific_serie(event_retrieve_series_for_specific_study):

    gen = dicom_handlers.handle_find(event_retrieve_series_for_specific_study)
    results = list(gen)
    EXPECTED_MATCHES = 1
    EXPECTED_TOTAL = EXPECTED_MATCHES + 1
    for status, dataset in results[:-1]:
        assert status == 0xFF00
        assert isinstance(dataset, Dataset)
        assert dataset.PatientName
    assert (
        results[0][1].SeriesInstanceUID
        == "1.3.6.1.4.1.14519.5.2.1.3344.4004.838622281675929509435013026326"
    )
    assert results[0][1].SeriesNumber == "6"
    assert len(results) == EXPECTED_TOTAL
    assert all(r[0] == 0xFF00 for r in results[:-1])
    assert results[-1][0] == 0x0000


def test_response_generation_failure(event_retreive_all_patients, mock_dicomdb):
    # Here we build two empty datasets simulating the files in the storage are corrupted
    test_matches = [Dataset(), Dataset()]
    mock_dicomdb.get_response_data.side_effect = Exception("Test error")

    with patch.multiple(
        "utilities.dicom_util",
        is_sopclassuid_valid=Mock(return_value=False),
        identifier_invalid=Mock(return_value=False),
        all_requested=Mock(return_value=True),
        get_query_level=Mock(return_value="PATIENT"),
    ), patch.multiple(
        "dicomdb.DicomDatabase", query_all_patients=Mock(return_value=test_matches)
    ):
        gen = dicom_handlers.handle_find(event_retreive_all_patients)
        results = list(gen)

    assert (
        len(results) == 3
    )  #  the failure state indicate thatt handle_find yilds (generate) three failures responses one on each returned dataset and a failure response
    assert results[0][0] == 0xC001


pytest.main(["-v", "test_handle_find.py"])
