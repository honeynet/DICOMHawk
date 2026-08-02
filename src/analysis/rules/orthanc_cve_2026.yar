private rule DICOM_Part10
{
    meta:
        description = "Part 10 preamble present: DICM at offset 128"

    condition:
        filesize >= 132 and
        uint32(128) == 0x4d434944
}


rule DICOM_Orthanc_Undersized_MetaHeader_CVE_2026_5437
{
    meta:
        description = "File Meta Information Group Length excludes a later Transfer Syntax UID element"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2026-5437"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $group_length = { 02 00 00 00 55 4c 04 00 }
        $transfer_syntax = { 02 00 10 00 55 49 }

    condition:
        DICOM_Part10 and
        $group_length at 132 and
        $transfer_syntax and
        uint32(140) < 4096 and
        // group 0002 ends at 144 + group_length; a group-0002 element can't start past that
        @transfer_syntax >= 144 + uint32(140)
}


rule DICOM_Rows_Columns_Encoded_As_UL
{
    meta:
        description = "Rows or Columns (normally US) encoded as Explicit VR UL, the prerequisite for the Orthanc dimension-wrap CVEs"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2026-5442,CVE-2026-5443"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $evrle_uid = "1.2.840.10008.1.2.1" ascii
        $rows_ul = { 28 00 10 00 55 4c 04 00 }
        $columns_ul = { 28 00 11 00 55 4c 04 00 }

    condition:
        DICOM_Part10 and
        $evrle_uid and
        1 of ($rows_ul, $columns_ul)
}


rule DICOM_Orthanc_CVE_2026_5442_Known_Test
{
    meta:
        description = "Exact published Orthanc Rows x Columns = 65536 x 65536 integer-wrap test case"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        cve = "CVE-2026-5442"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $rows_65536 = { 28 00 10 00 55 4c 04 00 00 00 01 00 }
        $columns_65536 = { 28 00 11 00 55 4c 04 00 00 00 01 00 }

    condition:
        DICOM_Part10 and
        all of them
}


rule DICOM_Orthanc_CVE_2026_5443_Known_Test
{
    meta:
        description = "Exact published Orthanc PALETTE COLOR Rows=3 Columns=1431655766 arithmetic-wrap test case"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        cve = "CVE-2026-5443"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $palette = "PALETTE COLOR" ascii
        $rows_3 = { 28 00 10 00 55 4c 04 00 03 00 00 00 }
        $columns_1431655766 = { 28 00 11 00 55 4c 04 00 56 55 55 55 }

    condition:
        DICOM_Part10 and
        all of them
}


rule DICOM_Orthanc_PMSCT_RLE1_CVE_2026_5441_Known_Test
{
    meta:
        description = "Published Philips PMSCT_RLE1 trailing RLE-escape out-of-bounds test value"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        cve = "CVE-2026-5441"
        reference = "https://kb.cert.org/vuls/id/536588"

    strings:
        $codec_tag = { a1 07 11 10 }
        $codec_name = "PMSCT_RLE1" ascii
        $compressed_data_tag = { a1 07 0a 10 }
        $known_bad_value = {
            00 00 00 00 00 00 00 00
            00 00 00 00 00 00 00 00
            00 00 00 00 00 00
            a5 ff
        }

    condition:
        DICOM_Part10 and
        all of them and
        @codec_name > @codec_tag and
        @codec_name < @codec_tag + 96 and
        @known_bad_value > @compressed_data_tag
}
