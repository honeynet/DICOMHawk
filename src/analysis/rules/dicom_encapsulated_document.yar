private rule DICOM_Part10
{
    meta:
        description = "Part 10 preamble present: DICM at offset 128"

    condition:
        filesize >= 132 and
        uint32(128) == 0x4d434944
}


rule DICOM_Encapsulated_PDF_Active_Content
{
    meta:
        description = "DICOM Encapsulated PDF (0042,0011) carries an execution-oriented PDF action name"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        reference = "DICOM Encapsulated PDF Storage, tag (0042,0011)"

    strings:
        $encap_pdf_explicit_le = { 42 00 11 00 4f 42 00 00 ?? ?? ?? ?? 25 50 44 46 2d }
        $encap_pdf_implicit_le = { 42 00 11 00 ?? ?? ?? ?? 25 50 44 46 2d }
        $action_javascript = "/JavaScript" ascii
        $action_js = "/JS" ascii
        $action_open = "/OpenAction" ascii
        $action_aa = "/AA" ascii
        $action_launch = "/Launch" ascii
        $action_richmedia = "/RichMedia" ascii

    condition:
        DICOM_Part10 and
        1 of ($encap_pdf_*) and
        1 of ($action_*)
}


rule DICOM_Encapsulated_PDF_Magic_Mismatch
{
    meta:
        description = "Encapsulated document declares application/pdf but the value starts as OLE, ZIP or PE"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        reference = "DICOM tags (0042,0011) and (0042,0012)"

    strings:
        $mime_tag = { 42 00 12 00 4c 4f }
        $mime_pdf = "application/pdf" ascii nocase
        $document_ole = { 42 00 11 00 4f 42 00 00 ?? ?? ?? ?? d0 cf 11 e0 a1 b1 1a e1 }
        $document_zip = { 42 00 11 00 4f 42 00 00 ?? ?? ?? ?? 50 4b 03 04 }
        $document_mz = { 42 00 11 00 4f 42 00 00 ?? ?? ?? ?? 4d 5a }

    condition:
        DICOM_Part10 and
        $mime_tag and
        $mime_pdf and
        @mime_pdf > @mime_tag and
        @mime_pdf < @mime_tag + 96 and
        1 of ($document_*)
}


rule DICOM_Encapsulated_OOXML_With_VBA
{
    meta:
        description = "Encapsulated Document is a ZIP-based Office file carrying a VBA macro project"
        author = "DICOMHawk project"
        license = "MIT"
        severity = "high"
        reference = "DICOM tag (0042,0011)"

    strings:
        $document_zip = { 42 00 11 00 4f 42 00 00 ?? ?? ?? ?? 50 4b 03 04 }
        $vba_word = "word/vbaProject.bin" ascii nocase
        $vba_excel = "xl/vbaProject.bin" ascii nocase
        $vba_powerpoint = "ppt/vbaProject.bin" ascii nocase
        $vba_generic = "vbaProject.bin" ascii nocase

    condition:
        DICOM_Part10 and
        $document_zip and
        1 of ($vba_*)
}
