import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import generate_uid
import numpy as np
import datetime

def create_dummy_dicom(filename):
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.ImplementationClassUID = pydicom.uid.PYDICOM_IMPLEMENTATION_UID
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    ds = FileDataset(filename, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = "Test^Patient"
    ds.PatientID = "123456"
    ds.Modality = "CT"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.StudyDate = datetime.datetime.now().strftime('%Y%m%d')
    ds.StudyTime = datetime.datetime.now().strftime('%H%M%S')
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime

    # Set some values for the image
    ds.Rows = 512
    ds.Columns = 512
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = (np.random.rand(512, 512) * 4095).astype(np.uint16).tobytes()

    # Calculate group lengths (if needed)
    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # Save the file
    pydicom.filewriter.dcmwrite(filename, ds, write_like_original=False)
    print(f"DICOM file '{filename}' created successfully.")

create_dummy_dicom("test_file.dcm")
