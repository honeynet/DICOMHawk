from pynetdicom import evt

ALLOWED_AE_TITLES = {b"DICOMHAWK", b"TCIA"}

class DICOMHandlers:

    def handle_assoc(self, event):
        assoc = event.assoc

        calling_ae = assoc.requestor.ae_title.strip()
        called_ae  = assoc.acceptor.ae_title.strip()

        self.app_logger.info(
            f"Incoming association | "
            f"CALLING={calling_ae.decode(errors='ignore')} "
            f"CALLED={called_ae.decode(errors='ignore')}"
        )

        # 🔐 HARD PROTOCOL GATE — Reject wrong CALLED AE
        if called_ae not in ALLOWED_AE_TITLES:
            self.exceptions_logger.warning(
                f"Association rejected: invalid CALLED AE = {called_ae}"
            )
            assoc.reject(0x01, 0x01, 0x07)
            return

        # ⚠️ Soft alert for unknown CALLING AE
        if calling_ae not in ALLOWED_AE_TITLES:
            self.exceptions_logger.warning(
                f"Suspicious CALLING AE detected: {calling_ae}"
            )
