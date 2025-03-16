"""Not finished"""

# import sys, schedule, shutil
# import os

# sys.path.append(os.path.abspath(".."))
# sys.path.append(os.path.abspath("../core"))
# sys.path.append(os.path.abspath("../custom_units"))
# import jwt
# import pytest
# from unittest.mock import patch, Mock
# from datetime import datetime, timedelta
# import time
# from faker import Faker
# from utilities import tcia_util

# patch.multiple(
#     "config",
#     INTEGRITY_CHECK=False,
#     DICOM_DATABASE="./mock_database.db",
#     FLASK_ACTIVATED=False,
#     TCIA_FILES_DIRECTORY="./mock_tcia_files",
#     DICOM_STORAGE_DIR="./mock_dicom_files_tcia",
#     TCIA_FILES_STAGGER_DIRECTORY="./stagger",
#     MAXIMUM_TCIA_FILES_IN_SERIE=1,
#     TCIA_STUDIES_PER_MODALITY=1,
#     MODALITIES=["CT"],
# ).start()

# import app_container

# test_container = app_container.ApplicationContainer()
# tcia_manager = test_container.tcia_scheduler().tcia_manager
# tcia_api = tcia_manager.tcia_api


# # def test_get_access_token():
# #     """
# #     Testing get access token from the TCIA API

# #     """

# #     tcia_api.get_access_token()
# #     token = tcia_api.access_token
# #     decoded_token = jwt.decode(token, options={"verify_signature": False})
# #     assert isinstance(token, str)
# #     assert len(token) > 0
# #     # Issuer claim
# #     assert "iss" in decoded_token
# #     # Expiration time claim
# #     assert "exp" in decoded_token
# #     # Subject claim
# #     assert "sub" in decoded_token
# #     # Assert the token is not expired
# #     current_time = int(time.time())
# #     assert decoded_token["exp"] > current_time


# def test_files_stage():
#     tcia_manager.change_dicom_files()
#     assert 1 == 0
#     # Call the staging function
#     # tcia_util.stage_old_files(
#     #     tcia_manager.storage_directory, tcia_manager.tcia_dir, tcia_manager.stagger_dir
#     # )

#     # tcia_util.restore_old_files(
#     #     tcia_manager.storage_directory, tcia_manager.tcia_dir, tcia_manager.stagger_dir
#     # )


# # def test_scheduled_job_starts_on_time():
# #     with patch.multiple(
# #         "utilities.tcia_util",
# #         delete_old_files=Mock(return_value=None),
# #         delete_temps=Mock(return_value=None),
# #     ), patch.multiple(
# #         "tcia_management.TCIAAPI",
# #         get_access_token=Mock(return_value=None),
# #         get_new_files=Mock(return_value=None),
# #     ), patch.multiple(
# #         "tcia_management.TCIAManager",
# #         organize_downloaded_files=Mock(return_value=None),
# #     ), patch.multiple(
# #         "dicomdb.DicomDatabase", initialize_database=Mock(return_value=None)
# #     ):
# #         tcia_manager.change_dicom_files()
# #         # Assert changing the dicom files job is scheduled
# #         assert "change_dicom_files()" in str(schedule.jobs)
# #         # current time
# #         now = datetime.now()
# #         # simulate two day elapased
# #         after_two_days = now + timedelta(days=2)
# #         # Patch the time in the schedule libraries to simulat time is passed
# #         with patch("schedule.datetime.datetime") as mock_datetime:
# #             mock_datetime.now.return_value = after_two_days
# #             # schedule.run_pending()
# #         # Assert the job is not executed yet
# #         assert not tcia_manager.change_dicom_files_called
# #         # simulate nine days elapsed
# #         after_nine_days = now + timedelta(days=9)
# #         with patch("schedule.datetime.datetime") as mock_datetime:
# #             mock_datetime.now.return_value = after_nine_days
# #             time.sleep(1)
# #             schedule.run_pending()
# #         # Assert the job is run after nine days elapsed
# #         assert tcia_manager.change_dicom_files_called


# # def test_files_retrieved():
# """
# simulate retrieving 4 dicom files from 4 modalities each 1 week without running the thread

# """

# #     t = get_configured_mock_tcia_object()
# #     try:
# #         shutil.rmtree(t.tcia_dir)
# #     except Exception as e:
# #         print("Temp deletion failed:", e)
# #     # Assert the folder where the files should be downloaded is empty
# #     assert not os.path.isdir(t.tcia_dir)
# #     # get tcia API access token to retrieve the files
# #     t.get_access_token()
# #     # patching the redis database to simulate that the studies have never been exist previously
# #     with patch.multiple(
# #         "redis_handler", get_TCI_existing_studies=Mock(return_value="Nothing".encode())
# #     ):
# #         # retrive the files through an API call
# #         t.get_new_files()
# #         # Assert the directory is not empty
# #         assert len(os.listdir(t.tcia_dir)) > 0


# pytest.main(["-v", "test_tcia.py"])
