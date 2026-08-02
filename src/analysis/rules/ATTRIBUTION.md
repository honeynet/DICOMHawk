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
