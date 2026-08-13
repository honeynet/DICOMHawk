# Features

DICOMHawk is a medical imaging honeypot. It presents the network surface of a real PACS and
records attacker interactions while containment controls limit access to the host and other
systems.

## DICOM services

The honeypot answers C-ECHO, C-FIND, C-GET, C-MOVE, and C-STORE. Which of those it advertises,
which SOP classes it accepts, and which transfer syntaxes it negotiates are all set by the
active profile, so a device that would not support an operation does not appear to support it.

Queries are answered from a real database of DICOM objects rather than from generated
placeholders, so a study returned by C-FIND can also be retrieved, and its attributes agree
across every surface that reports them. Unsupported private or unknown optional query keys are
ignored as DICOM specifies rather than failing the complete C-FIND/C-GET/C-MOVE operation.

Association limits, timeouts, and the maximum accepted object size are bounded and configurable.
A peer that opens connections without ever sending a PDU cannot occupy every association slot.
Connection-close events record duration, total received bytes, and a protocol guess. If an
A-ASSOCIATE-RQ cannot be decoded, DICOMHawk also preserves a bounded hexadecimal prefix and
best-effort called/calling AE titles and implementation identifiers; HTTP, TLS, and random probes
on a DIMSE port are therefore distinguishable from an empty TCP connection.

C-STORE status codes distinguish malformed input from resource failure: an unreadable or invalid
dataset returns `0xC000`, an affected/dataset SOP-class mismatch returns `0xA900`, and `0xA700`
is reserved for capacity or persistence failures such as a configured size limit, capture failure,
write failure, or database-index failure.

## Deception profiles

A profile is a YAML file describing the device to impersonate: AE title, implementation class
UID and version, supported services, HTTP headers, page templates, and DICOMweb layout. Two
ship with the project, and writing another requires no code.

| Profile | Presents as |
|---|---|
| *(none)* | A plain DICOM node with no web surface |
| `generic-pacs` | An unbranded PACS with a web console, useful as a starting point |
| `fujifilm` | A Fujifilm Synapse PACS, matched against its published conformance statement |

See [Adding a profile](./profiles.md).

## Web surface

Profiles may serve a sign-on page and the pages behind it. The Fujifilm profile reproduces the
Synapse sign-on flow and a worklist populated from the same database the DICOM services answer
from, so an attacker who gets in sees consistent data rather than a stub.

Login attempts are captured whether or not they succeed. Bait credentials can be declared so
that specific plausible accounts work while every other guess fails the way the real product
fails, which avoids the tell of a honeypot that accepts anything.

Decoy paths taken from real deployments answer with the responses those paths give, so scanners
looking for known vendor endpoints find what they expect.

## DICOMweb

QIDO-RS, WADO-RS, STOW-RS, and WADO-URI are served when a profile declares them, on that
product's own ports and base paths. Uploads through STOW-RS are quarantined and analysed
exactly like uploads through C-STORE.

## Realistic data

The database is populated from public imaging collections and rewritten to be internally
consistent: patient names, birth dates, institutions, referring physicians, station names, and
procedure descriptions all agree with each other and with the study dates. Institution names
can be drawn from OpenStreetMap for a chosen city, and names can be generated in any supported
locale.

Re-seeding on a schedule rotates identities and source collections, so a returning attacker
does not see a frozen database.

See [Commands](./commands.md#dicomhawk-seed).

## Honeytokens

A canary URL can be embedded as a retrieval URL, and a canary PDF as an encapsulated document,
in one seeded instance per run. Either fires when an attacker opens the data somewhere else,
reporting activity that never touches the honeypot again.

## Payload analysis

Everything captured from C-STORE, STOW-RS, or a web upload is analysed statically: file type,
hashes, entropy, bounded DICOM metadata, string indicators, and YARA rules covering known
imaging-parser exploits and embedded executables.

Analysis runs in a separate worker process under a hard deadline and never executes the payload.
Results are attached to the session that submitted them and are visible only to the operator.
It does not change attacker-facing status codes, headers, routes, or response bodies. Queueing is
bounded and a failed handoff is logged, but exact response timing is not guaranteed.

See [Payload analysis](./analysis.md).

## Browser fingerprinting

On profiles that enable it, a collector on the attacker-facing pages records browser and device
signals and stores them in their own database, linked to the session that produced them. It
helps tell a scripted client from a human with a browser, and links visits that share a
fingerprint.

See [Browser fingerprinting](./fingerprinting.md).

## Recording and review

Every interaction becomes a structured JSON event: the peer, the session, the operation, the
outcome, and the parameters that were sent. Captured objects are written to disk with their
exact incoming bytes retained alongside the parsed copy.

Connections that never complete a DICOM handshake are recorded too. Each one closes with the
bytes it sent, how long it stayed open, a protocol guess covering DICOM, HTTP, TLS and SSH, and
a bounded hex and readable preview of what arrived, so a port scan, a web scanner pointed at the
DICOM port, and a malformed handshake are all told apart rather than collapsing into one line.
When a handshake is rejected as malformed, the calling and called AE titles and the peer's
implementation identity are still recovered from the raw bytes.

A loopback-only operator API and dashboard summarise what has been collected, including
per-attacker rollups, captured credentials, uploaded artifacts with their analysis results,
sessions, and browser fingerprints. The supplied deployment publishes it on host loopback only;
operators must preserve that restriction or protect remote access with authentication and
network controls.

## Containment

Objects an attacker uploads are quarantined. They are indexed for the operator but are never
returned by C-GET, C-MOVE, or WADO, so the honeypot cannot be used to stage, host, or relay
content to a third party. Writes are confined to a storage jail that a crafted identifier
cannot escape.

The container runs unprivileged with a read-only root filesystem, all capabilities dropped, and
memory, process, and file-descriptor limits. The honeypot itself makes no outbound connections,
so egress can be dropped at the host firewall without affecting what an attacker sees.

See [Production deployment](./deployment.md).
