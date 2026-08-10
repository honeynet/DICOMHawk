rule Embedded_Windows_PE
{
    meta:
        description = "Windows PE with a valid DOS-header pointer embedded in a captured payload"
        author = "DICOMHawk project"
        license = "MIT"

    strings:
        $mz = { 4D 5A }

    condition:
        for any i in (1..#mz): (
            uint32(@mz[i] + 0x3c) >= 0x40 and
            uint32(@mz[i] + 0x3c) <= 0x1000000 and
            @mz[i] + uint32(@mz[i] + 0x3c) + 24 <= filesize and
            uint32(@mz[i] + uint32(@mz[i] + 0x3c)) == 0x00004550 and
            uint16(@mz[i] + uint32(@mz[i] + 0x3c) + 6) >= 1 and
            uint16(@mz[i] + uint32(@mz[i] + 0x3c) + 6) <= 96
        )
}
