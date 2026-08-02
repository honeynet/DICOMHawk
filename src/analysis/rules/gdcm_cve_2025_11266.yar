private rule DICOM_Part10
{
    meta:
        description = "Part 10 preamble present: DICM at offset 128"

    condition:
        filesize >= 132 and
        uint32(128) == 0x4d434944
}


rule DICOM_GDCM_Short_Final_Fragment_CVE_2025_11266
{
    meta:
        description = "Encapsulated Pixel Data's final fragment is under 3 bytes, the patched GDCM out-of-bounds read"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        cve = "CVE-2025-11266"
        reference = "https://github.com/malaterre/GDCM/commit/5829c95c8ac3afa9a3a3413675e948959c28a789"

    strings:
        $pixel_ob_undefined = { e0 7f 10 00 4f 42 00 00 ff ff ff ff }
        $pixel_ow_undefined = { e0 7f 10 00 4f 57 00 00 ff ff ff ff }
        $last_fragment_len_0 = { fe ff 00 e0 00 00 00 00 fe ff dd e0 00 00 00 00 }
        $last_fragment_len_1 = { fe ff 00 e0 01 00 00 00 ?? fe ff dd e0 00 00 00 00 }
        $last_fragment_len_2 = { fe ff 00 e0 02 00 00 00 ?? ?? fe ff dd e0 00 00 00 00 }

    condition:
        DICOM_Part10 and
        1 of ($pixel_*) and
        1 of ($last_fragment_*)
}
