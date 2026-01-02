from dicom_server.config import LOG_REJECTED_ASSOC
from pynetdicom import evt

ALLOWED_AE_TITLES = {b"DICOMHAWK", b"TCIA"}

class DICOMHandlers:

    def handle_assoc(self, event):
        assoc = event.assoc
        calling_ae = assoc.requestor.ae_title.strip()
        called_ae  = assoc.requestor.requested_ae_title.strip()
        ip_addr    = assoc.requestor.address

        if called_ae not in ALLOWED_AE_TITLES:
            reason = "INVALID_CALLED_AE"
            if LOG_REJECTED_ASSOC:
                self.event_collector.record_rejected_assoc(
                    ip_addr, calling_ae, called_ae, reason
            )
            assoc.reject(result=0x01, source=0x01, reason=0x07)
            return

        if calling_ae not in ALLOWED_AE_TITLES:
            reason = "INVALID_CALLING_AE"
            if LOG_REJECTED_ASSOC:
                self.event_collector.record_rejected_assoc(
                    ip_addr, calling_ae, called_ae, reason
                )
            assoc.reject(result=0x01, source=0x01, reason=0x03)
            return

