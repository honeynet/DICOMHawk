private rule DICOM_Part10
{
    meta:
        description = "Part 10 preamble present: DICM at offset 128"

    condition:
        filesize >= 132 and
        uint32(128) == 0x4d434944
}


rule DICOM_PE_Polyglot_Active
{
    meta:
        description = "Valid PE loader coexists with the DICOM Part 10 prefix in the same file"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        cve = "CVE-2019-11687"
        reference = "https://github.com/d00rt/pedicom"

    condition:
        DICOM_Part10 and
        uint16(0) == 0x5a4d and
        // e_lfanew must clear the DOS header and stay in-file with room for a COFF header
        uint32(0x3c) >= 0x40 and
        uint32(0x3c) <= filesize - 24 and
        uint32(uint32(0x3c)) == 0x00004550 and
        uint16(uint32(0x3c) + 6) >= 1 and
        uint16(uint32(0x3c) + 6) <= 96
}


rule DICOM_PE_Polyglot_PE_Header_After_DICM
{
    meta:
        description = "PE/DICOM polyglot whose PE header sits inside the DICOM element region (the PEDICOM pattern)"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        reference = "https://github.com/d00rt/pedicom"

    condition:
        DICOM_PE_Polyglot_Active and
        uint32(0x3c) >= 132
}


rule DICOM_Orthanc_Config_Preamble_CVE_2023_33466
{
    meta:
        description = "Orthanc REST configuration JSON embedded in the DICOM preamble (config/DICOM polyglot)"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "critical"
        cve = "CVE-2023-33466"
        reference = "https://www.shielder.com/blog/2023/10/cve-2023-33466-exploiting-healthcare-servers-with-polyglot-files/"

    strings:
        $execute_lua = "\"ExecuteLuaEnabled\"" ascii
        $remote_access = "\"RemoteAccessAllowed\"" ascii
        $json_nul = { 7d 00 }

    condition:
        DICOM_Part10 and
        $execute_lua in (0..127) and
        $remote_access in (0..127) and
        $json_nul in (0..127)
}
