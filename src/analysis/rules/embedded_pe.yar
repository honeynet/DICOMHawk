rule Embedded_Windows_PE
{
    meta:
        description = "Windows PE executable (MZ+PE header pair, DOS stub) embedded in a captured object's PixelData or EncapsulatedDocument"
        author = "DICOMHawk project"
        license = "MIT"

    strings:
        $mz = { 4D 5A 90 00 }
        $pe = "PE\x00\x00"
        $dos_stub = "This program cannot be run in DOS mode"

    condition:
        $mz and $pe and $dos_stub
}
