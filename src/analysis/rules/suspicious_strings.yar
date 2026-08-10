rule Suspicious_Script_Indicators
{
    meta:
        description = "Script/shell/macro indicators uncommon in a legitimate DICOM object payload"
        author = "DICOMHawk project"
        license = "MIT"

    strings:
        $ps1 = "powershell" nocase
        $cmd = "cmd.exe" nocase
        $wscript = "wscript.shell" nocase
        $macro = "Auto_Open" nocase
        $b64_mz = "TVqQAAMAAAAEAAAA"  // base64 of the common MZ/PE header prefix

    condition:
        any of them
}

rule EICAR_Test_String
{
    meta:
        description = "Standard antivirus test string (EICAR); confirms the analysis pipeline itself detects known content"
        author = "DICOMHawk project"
        license = "MIT"
        reference = "https://www.eicar.org/download-anti-malware-testfile/"

    strings:
        $eicar = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE"

    condition:
        $eicar
}
