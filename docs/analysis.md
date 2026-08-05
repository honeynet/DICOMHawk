# Payload analysis

Every attacker payload the honeypot actually captures, whether an accepted or rejected C-STORE
object, an accepted or rejected STOW-RS part, a malformed STOW request body with no
recoverable parts, or a web browse-console upload (`generic-pacs` only), is queued for a
**static, non-executing** analysis pass: file-type identification, hashes, entropy, bounded
DICOM metadata, bounded IOC extraction, and YARA. The payload's bytes are never executed,
and the analysis never reaches the network.

A valid multipart STOW envelope and the normalized copy already indexed for C-FIND are not
separately analyzed. Each individual DICOM part inside it is. Seeded, trusted data
(`safe=True`) is never analyzed; only bytes actually supplied over the wire by a peer are.

## What gets recorded

Each analyzed capture gets a durable, immutable job record with a stable `artifact_id`, the
originating session/IP/channel/request type, and a `state`:

| State | Meaning |
|---|---|
| `pending` | Captured, waiting for the worker |
| `running` | Currently being analyzed |
| `completed` | Analysis finished; see `result` |
| `failed` | The analyzer itself errored (e.g. an unreadable file) |
| `timeout` | Analysis exceeded `--analysis-timeout` |
| `missing` | The underlying capture file was gone or unreadable when claimed |

A `pending`/`running` job that survives a restart (or a queue backlog) is picked up
automatically, so nothing is lost and nothing needs to be re-submitted.

## Result fields

- **Hashes:** `sha256` is on the underlying artifact record (the exact bytes analyzed);
  `md5`/`sha1` inside the result are for cross-referencing against third-party threat feeds
  only. Treat them as IOC-lookup convenience, never as an integrity guarantee.
- **`entropy`:** Shannon entropy of the analyzed bytes (0 to 8). High entropy alone is not proof
  of packing or encryption, so corroborate with the file-type and YARA results.
- **`file_type`:** libmagic's MIME type and description of the raw bytes.
- **`iocs`:** bounded, deduplicated URLs/IPs/emails found in the payload (ASCII and
  UTF-16LE), capped in both count and length.
- **`dicom`:** bounded, non-PHI DICOM metadata: SOP class and instance UID, transfer syntax,
  modality, and whether `PixelData`/`EncapsulatedDocument` is present (with its declared
  size only, never its bytes). A raw C-STORE dataset has no Part-10 preamble, so the analyzer
  uses the transfer syntax recorded from the accepted presentation context. Older captures
  without that field fall back to pydicom's byte-level heuristic, which is recorded in
  `parse_assumption`.
- **`encapsulated_document`:** present only when the object carries one. DICOM objects can wrap a
  whole other file (a PDF, an Office document, a CDA record) inside a single attribute. That inner
  file is unwrapped, its permitted padding byte removed, and it is identified and scanned **on its
  own**, so a rule anchored to the start of a file or to its total size works against the real
  document instead of the DICOM container around it. `content_conflicts_with_declared_mime` is set
  only for the unambiguous case, where an object declares itself a PDF but the bytes are not one;
  other declared types legitimately identify as a generic container and are not judged. Rules that
  match the inner file appear in the artifact's matched rules alongside the outer ones.
- **`yara`:** matched rule names, namespaces, tags, and metadata (never raw match offsets or
  matched string content). `state: "timeout"` means the scan itself hit its own internal
  time limit. Analysis still completes normally; this is not a failure.

Every completed result also carries an `analyzer_version` and a `ruleset_version` (a hash of
every compiled rule file), so a result can always be tied back to exactly which detections
produced it.

## Custom rules

Point `--analysis-rules` at a directory of your own `.yar` files to run alongside the shipped
starters. They compile under a separate namespace, so a custom rule can never shadow or
override a shipped one. A rule file that fails to compile is skipped and logged rather than
preventing the remaining custom rules and shipped starters from running. `YARA include`
directives are disabled for all rule files, shipped and custom alike.

The shipped rules are original and MIT-licensed. They cover two tiers of detection:

- **Generic, low-false-positive signals:** an embedded Windows PE whose DOS header points to a
  bounded PE header with a plausible section count, common script/shell indicators, and the
  standard EICAR antivirus test string.
- **DICOM-structural and CVE-specific signals**, gated on a real Part 10 preamble (`DICM` at
  offset 128) so they never fire on a raw DIMSE dataset that merely lacks one: a PE loader
  polyglotted with the DICOM prefix, an Orthanc REST config smuggled in the preamble
  (CVE-2023-33466), an undersized File Meta group length (CVE-2026-5437), Rows/Columns
  encoded with an invalid `UL` VR and the exact published dimension-wrap test cases
  (CVE-2026-5442, CVE-2026-5443), the published Philips `PMSCT_RLE1` out-of-bounds test value
  (CVE-2026-5441), a too-short final encapsulated Pixel Data fragment (GDCM CVE-2025-11266),
  an Encapsulated Document declaring PDF but carrying active PDF actions, OLE/ZIP/PE magic,
  or a macro-bearing OOXML payload, paired HTML/script markup associated with viewer-side
  stored XSS (CVE-2023-7238), and oversized declared-vs-actual ZIP/gzip expansion ratios
  (CVE-2026-5438, CVE-2026-5439).

These are a starting point, not a substitute for your own threat intelligence.

## Operation and limits

Analysis runs entirely offline. It makes no outbound network access and works under an
egress-locked deployment. It runs in its own supervised worker process, separate from the
honeypot's DICOM/web listeners, so a crash or resource spike in file-type detection, DICOM
parsing, or YARA matching cannot take down the honeypot itself; a crashed worker restarts
automatically and resumes pending work. This isolates crashes and runaway resource use, not
the filesystem. The worker still runs as the same unprivileged container user, so it should
be treated as a defense-in-depth boundary, not an airtight sandbox.

Analysis never changes response codes, headers, routes, or payloads, whether it is enabled,
disabled, caught up, or backlogged. Handing a captured payload to the worker is one bounded
database write, and if that write cannot complete the job is recorded as not queued rather
than surfaced to the sender. Exact response timing is not a guarantee an optional
asynchronous component or the host scheduler can make.

Two independent timeouts protect the worker: a per-job wall-clock deadline (`--analysis-timeout`,
what actually bounds a single analysis) and a much larger process-lifetime CPU-time backstop
(1 hour cumulative, not configurable) that only matters if a job somehow gets stuck in
non-interruptible native code the wall-clock timeout can't preempt. The backstop is
deliberately not tied to `--analysis-timeout`: it is a *cumulative* total across every job the
worker has ever run, so a small value there would eventually kill a perfectly healthy worker
after enough ordinary jobs, not just a stuck one.

## Operator API

`/api/artifacts` (loopback-only, same auth as the rest of the operator API) lists analyzed
artifacts with paging and filters (`state`, `channel`, `ip`, `sha256`, `rule`). It returns
findings only, never the raw captured bytes or the internal file path. There is no download
endpoint. The operator dashboard's "Analyzed artifacts" panel shows the same data.

`rule` is a **substring** match, not an exact one, so `rule=PMSCT` finds every
`DICOM_Orthanc_PMSCT_RLE1_...` hit without typing the full rule name, and a rule's Big Endian
sibling (e.g. `..._BigEndian`) shows up under the same query as its Little Endian counterpart.
