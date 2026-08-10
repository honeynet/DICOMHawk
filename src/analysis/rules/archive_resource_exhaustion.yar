rule Orthanc_ZIP_Declared_Size_Exhaustion_CVE_2026_5439
{
    meta:
        description = "ZIP member declares gigabyte-scale expansion from a small compressed payload"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2026-5439"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $local_file_header = { 50 4b 03 04 }

    condition:
        $local_file_header and
        for any i in (1..#local_file_header) : (
            @local_file_header[i] + 30 <= filesize and
            uint32(@local_file_header[i] + 22) >= 0x40000000 and
            uint32(@local_file_header[i] + 18) < 0x01000000
        )
}


rule Orthanc_GZIP_Large_ISIZE_CVE_2026_5438
{
    meta:
        description = "Small gzip body advertises an uncompressed ISIZE above 1 GiB"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2026-5438"
        reference = "https://kb.cert.org/vuls/id/536588"

    condition:
        filesize >= 18 and
        filesize < 16MB and
        uint16(0) == 0x8b1f and
        uint32(filesize - 4) >= 0x40000000
}
