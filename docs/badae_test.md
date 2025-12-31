### BADAE rejection test

BADAE must be rejected:

python - << 'EOF'
from pynetdicom import AE
from pynetdicom.sop_class import Verification

ae = AE(ae_title=b"BADAE")
ae.add_requested_context(Verification)
assoc = ae.associate("127.0.0.1", 104, ae_title=b"DICOMHAWK")
print("BADAE:", assoc.is_established)
EOF

Expected:
BADAE: False

---

### DICOMHAWK acceptance test

python - << 'EOF'
from pynetdicom import AE
from pynetdicom.sop_class import Verification

ae = AE(ae_title=b"DICOMHAWK")
ae.add_requested_context(Verification)
assoc = ae.associate("127.0.0.1", 104, ae_title=b"DICOMHAWK")
print("DICOMHAWK:", assoc.is_established)
if assoc.is_established:
    assoc.release()
EOF

Expected:
DICOMHAWK: True
