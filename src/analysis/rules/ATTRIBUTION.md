These YARA rules are original, written for DICOMHawk. They are not copied from a
third-party ruleset. Licensed MIT, same as DICOMHawk itself — see each rule's `license`
metadata field. Operators may add their own `.yar` files via `--analysis-rules`; those
compile under a separate `operator/` namespace and never overwrite these.

`dicom_polyglots.yar`, `orthanc_cve_2026.yar`, `gdcm_cve_2025_11266.yar`,
`dicom_encapsulated_document.yar`, `dicom_metadata_xss.yar` and
`archive_resource_exhaustion.yar` encode structural detections and published PoC byte
sequences from public vulnerability disclosures (Orthanc security advisories at
kb.cert.org/vuls/id/536588, the GDCM CVE-2025-11266 patch, the PEDICOM research at
github.com/d00rt/pedicom, CVE-2023-33466, CVE-2023-7238). The rule text itself was
authored for this project; only the underlying byte constants are drawn from those
public disclosures. Two report-suggested detections were deliberately left out because
DICOMHawk doesn't currently capture the artifact type they need: raw HTTP request
headers (Content-Length-based exhaustion, CVE-2026-5440) and raw DICOM Upper Layer
association PDUs (the Horos 2.1.0 PoC) — both are envelope/PDU-level, not payload-level.

`orthanc_cve_2026.yar` ships Big Endian siblings (`..._BigEndian`) of the Rows/Columns,
CVE-2026-5442, CVE-2026-5443, and PMSCT_RLE1 rules, because Fujifilm's storage classes
accept Explicit VR Big Endian (`1.2.840.10008.1.2.2`) and its tag/length byte order is the
mirror image of Little Endian, so the LE-only patterns would miss the identical exploit
re-encoded that way. Two rules deliberately have no Big Endian sibling: GDCM's encapsulated
(fragmented) Pixel Data structure and the File Meta Information group targeted by
CVE-2026-5437 are always Explicit VR Little Endian by DICOM specification (PS3.5 §A.4 and
PS3.10 §7.1 respectively), regardless of the transfer syntax negotiated for the rest of the
object — there is no Big Endian encoding of either to evade into. Implicit VR Little Endian
has no sibling either, for a different reason: it carries no VR bytes at all, so "Rows
encoded as UL instead of US" has no direct byte-level analog there — an equivalent attack
under Implicit VR would need real length/dictionary-aware parsing to detect, which is
tracked as the deferred structural-validation item (`A-1`) in the roadmap, not a YARA rule.
