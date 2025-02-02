from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ImplicitVRLittleEndian
from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    PatientRootQueryRetrieveInformationModelGet,
    CTImageStorage,
    MRImageStorage
)

# Enable pynetdicom's debug logging
debug_logger()

# Initialize the Application Entity (AE)
ae = AE()

# Add requested presentation contexts
ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
ae.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
ae.add_requested_context(CTImageStorage)
ae.add_requested_context(MRImageStorage)

# Define the peer DICOM server
server_address = ('localhost', 11112)

# Send a C-FIND request
ds = Dataset()
ds.PatientName = '*'
ds.QueryRetrieveLevel = 'PATIENT'

assoc = ae.associate(server_address[0], server_address[1])
if assoc.is_established:
    responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)
    for (status, identifier) in responses:
        if status:
            print('C-FIND response:', identifier)
    assoc.release()
else:
    print("Association rejected, aborted or never connected")

# Send a C-STORE request
ds = Dataset()
ds.PatientName = 'Doe^John'
ds.PatientID = '12345'
ds.StudyDate = '20210101'
ds.Modality = 'CT'
ds.SOPInstanceUID = '1.2.3.4.5.6.7.8.9'
ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2'

# Create the File Meta Information
file_meta = FileMetaDataset()
file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
file_meta.ImplementationClassUID = '1.2.826.0.1.3680043.9.3811.2.0.2'

ds.file_meta = file_meta

assoc = ae.associate(server_address[0], server_address[1])
if assoc.is_established:
    status = assoc.send_c_store(ds)
    print('C-STORE response status:', status)
    assoc.release()
else:
    print("Association rejected, aborted or never connected")

# Send a C-MOVE request
ds = Dataset()
ds.QueryRetrieveLevel = 'PATIENT'
ds.PatientName = 'Doe^John'

assoc = ae.associate(server_address[0], server_address[1])
if assoc.is_established:
    responses = assoc.send_c_move(ds, 'ANY-SCP', PatientRootQueryRetrieveInformationModelMove)
    for (status, identifier) in responses:
        if status:
            print('C-MOVE response:', identifier)
    assoc.release()
else:
    print("Association rejected, aborted or never connected")

# Send a C-GET request
assoc = ae.associate(server_address[0], server_address[1])
if assoc.is_established:
    responses = assoc.send_c_get(ds, PatientRootQueryRetrieveInformationModelGet)
    for (status, identifier) in responses:
        if status:
            print('C-GET response:', identifier)
    assoc.release()
else:
    print("Association rejected, aborted or never connected")
