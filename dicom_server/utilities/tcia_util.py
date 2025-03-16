import random
import os
from datetime import datetime, timedelta
import logging
import zipfile
import io
from faker import Faker
import random
from pydicom import dcmread
import shutil


fake = Faker("da_DK")

exceptions_logger = logging.getLogger("exceptions")


def generate_patient_info():
    try:
        name = ""
        sex = ""

        if random.choice([True, False]):
            name = fake.first_name_male() + " " + fake.last_name_male()
            sex = "M"
        else:
            name = fake.first_name_female() + " " + fake.last_name_female()
            sex = "F"

        start_birth_date = datetime(1955, 12, 10)
        end_birth_date = datetime(1999, 12, 1)
        random_birth_date = start_birth_date + timedelta(
            days=random.randint(0, (end_birth_date - start_birth_date).days)
        )
        formatted_birth_date = random_birth_date.strftime("%Y%m%d")

        start_study_date = datetime(2010, 12, 10)
        end_study_date = datetime(2024, 12, 1)
        random_study_date = start_study_date + timedelta(
            days=random.randint(0, (end_study_date - start_study_date).days)
        )
        formatted_study_date = random_study_date.strftime("%Y%m%d")
    except Exception:
        exceptions_logger.exception(
            "Unexpected error while generating random patient data"
        )
    return (
        name,
        str(random.randint(10, 7400)),
        sex,
        formatted_birth_date,
        str(random.randint(35241, 567331169)),
        formatted_study_date,
        str(random.randint(3528941, 5673331169)),
    )


def filter_retrieved_studies(
    existing_studies,
    _json,
    study_counter,
    number_of_studies_in_each_retrieved_modality,
    minimum_files,
    maximum_files,
):
    metadata = {}
    for entry in _json:
        modality = entry["Modality"]
        study_uid = entry["StudyInstanceUID"]
        series_uid = entry["SeriesInstanceUID"]
        image_count = int(entry["ImageCount"])

        if satisfies_series_count_per_study(image_count, minimum_files, maximum_files):
            if never_downloaded_study(
                existing_studies, study_uid
            ) and studies_count_satisfied(
                study_counter, number_of_studies_in_each_retrieved_modality
            ):
                if is_valid_study(metadata, study_uid):
                    study_counter += 1
                    metadata[study_uid] = []
                metadata[study_uid].append({"se_uid": series_uid, "modality": modality})
    return metadata


def extract_and_save_zip_data(mod, st_uid, se_uid, response, tcia_dir):
    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        zip_file.extractall(path=f"{tcia_dir}/{mod}/{st_uid}/{se_uid}/")


def is_valid_study(metadata, study_uid):
    return study_uid not in metadata


def never_downloaded_study(existing_studies, study_uid):
    return study_uid.encode() not in existing_studies


def studies_count_satisfied(
    study_counter, number_of_studies_in_each_retrieved_modality
):
    return study_counter < number_of_studies_in_each_retrieved_modality


def satisfies_series_count_per_study(image_count, minimum_files, maximum_files):
    return minimum_files <= image_count <= maximum_files


def get_random_institution():
    medical_institutions = [
        "Københavns Sundhedscenter",
        "Aarhus Kliniken",
        "Odense Patienthus",
        "Nordjylland Med Institut",
    ]

    return random.choice(medical_institutions)


def store_retrieved_file(
    directory, modality, study_files_counter, patient_name, dataset
):
    filename = f"{modality}_{patient_name}_{study_files_counter}.dcm"
    filepath = os.path.join(directory, filename)
    try:
        dataset.save_as(filepath)
    except Exception:
        exceptions_logger.exception("File save failed:")


def get_files_per_serie(modality, study_uid, se_uid, tcia_dir):
    return os.listdir(os.path.join(tcia_dir, modality, study_uid, se_uid))


def get_downloaded_series_per_study(modality, study_uid, tcia_dir):
    return os.listdir(os.path.join(tcia_dir, modality, study_uid))


def build_file_dataset(
    modality,
    study_uid,
    patient_name,
    patient_id,
    patient_sex,
    birth_date,
    study_id,
    study_date,
    accession_number,
    institution,
    se_uid,
    file,
    tcia_dir,
):
    dataset = dcmread(os.path.join(tcia_dir, modality, study_uid, se_uid, file))
    dataset.StudyInstanceUID = study_uid
    dataset.InstitutionName = institution
    dataset.SeriesInstanceUID = se_uid
    dataset.PatientName = patient_name
    dataset.PatientID = patient_id
    dataset.PatientSex = patient_sex
    dataset.PatientBirthDate = birth_date
    dataset.StudyID = study_id
    dataset.AccessionNumber = accession_number
    dataset.StudyDate = study_date
    dataset.SeriesDate = study_date
    return dataset


def get_downloaded_modalitis(tcia_dir):
    return os.listdir(tcia_dir)


def get_studies_from_modality(modality, tcia_dir):
    return os.listdir(os.path.join(tcia_dir, modality))


def initialize_dicom_directory_if_not_exist(storage_directory):
    directory = os.path.join(storage_directory)
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory


def is_licience_file(file):
    return file == "LICENSE"


def stage_old_files(storage_dir, tcia_dir, stagger_dir):
    try:
        for filename in os.listdir(storage_dir):
            full_file_path = os.path.join(storage_dir, filename)
            if os.path.isfile(full_file_path):

                shutil.move(full_file_path, os.path.join(stagger_dir, "DICOM"))
        for folder in os.listdir(tcia_dir):
            full_folder_path = os.path.join(tcia_dir, folder)
            if os.path.isdir(full_folder_path):
                shutil.move(full_folder_path, os.path.join(stagger_dir, "TCIA"))
    except Exception:
        exceptions_logger.exception("Unexpected error while stagging files")
        raise


def restore_old_files(storage_dir, tcia_dir, stagger_dir):
    try:
        for filename in os.listdir(os.path.join(stagger_dir, "DICOM")):

            full_file_path = os.path.join(stagger_dir, "DICOM", filename)
            if os.path.isfile(full_file_path):
                shutil.move(full_file_path, storage_dir)
        for folder in os.listdir((os.path.join(stagger_dir, "TCIA"))):
            full_folder_path = os.path.join(stagger_dir, "TCIA", folder)
            if os.path.isdir(full_folder_path):
                shutil.move(full_folder_path, tcia_dir)
    except Exception:
        exceptions_logger.exception("Unexpected error while restoring files")
        raise


def delete_staged_files(stage_dir):
    try:
        for root, dirs, files in os.walk(os.path.join(stage_dir, "DICOM")):
            for file in files:
                file_path = os.path.join(root, file)
                if not file.endswith(".py"):
                    os.remove(file_path)
        path_to_remove = os.path.join(stage_dir, "TCIA")
        if os.path.isdir(path_to_remove):
            if not file.endswith(".py"):
                shutil.rmtree(path_to_remove)

    except Exception:
        exceptions_logger.exception("Unexpected error while removing stagged files")
        raise


def delete_downloded_files_if_exist(tcia_dir):

    for dir in os.listdir(tcia_dir):
        shutil.rmtree(os.path.join(tcia_dir, dir))
