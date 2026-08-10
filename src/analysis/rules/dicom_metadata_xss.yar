private rule DICOM_Part10
{
    meta:
        description = "Part 10 preamble present: DICM at offset 128"

    condition:
        filesize >= 132 and
        uint32(128) == 0x4d434944
}


rule DICOM_Metadata_Stored_XSS
{
    meta:
        description = "DICOM object carries a paired HTML/script markup combination, the pattern behind viewer-side stored XSS"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2023-7238"
        reference = "https://nvd.nist.gov/vuln/detail/CVE-2023-7238"

    strings:
        $script_open = "<script" ascii nocase
        $script_close = "</script" ascii nocase
        $img = "<img" ascii nocase
        $onerror = "onerror=" ascii nocase
        $svg = "<svg" ascii nocase
        $onload = "onload=" ascii nocase
        $javascript = "javascript:" ascii nocase
        $href = "href=" ascii nocase

    condition:
        DICOM_Part10 and
        (
            ($script_open and $script_close) or
            ($img and $onerror) or
            ($svg and $onload) or
            ($javascript and $href)
        )
}
