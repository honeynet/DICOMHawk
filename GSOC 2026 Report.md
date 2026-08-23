# Improving the DICOMHawk Medical Honeypot

## Profile-Driven Deception with Web Fingerprinting and Payload Analysis

**Google Summer of Code 2026 — Final Work Product**

| | |
|---|---|
| **Contributor** | Aditya Singh ([@Vib3ss](https://github.com/Vib3ss)) |
| **Organisation** | [The Honeynet Project](https://www.honeynet.org/) |
| **Project** | [DICOMHawk](https://github.com/honeynet/DICOMHawk) |
| **Working branch** | [`GSoC-2026`](https://github.com/honeynet/DICOMHawk/tree/GSoC-2026) |
| **Core contribution range** | [`93f7d86a`](https://github.com/honeynet/DICOMHawk/commit/93f7d86a) through [`f72c3335`](https://github.com/honeynet/DICOMHawk/commit/f72c3335) |
| **Core contribution diff** | [`5f23af82...f72c3335`](https://github.com/honeynet/DICOMHawk/compare/5f23af82...f72c3335) |
---

## 1. Summary

DICOMHawk is a honeypot for DICOM, the standard used to exchange medical images and related
information. At the start of the summer, the ground-up rewrite operated mainly at the core DIMSE
protocol level. It listened on port 104, presented one fixed identity, had incomplete handlers,
and had not yet carried across the web surface, threat intelligence, session correlation, or
realistic study data available in earlier versions.

Over the summer, DICOMHawk became a deployable deception system. It can impersonate a commercial
PACS using details from the vendor's conformance statement, present a captured web sign-on flow
and post-login worklist, expose DICOMweb services, fingerprint visiting browsers, statically
analyse attacker-supplied files, and install from a bare Ubuntu host through a guided script.

All five proposal workstreams are implemented and documented. Eleven planned pull requests and
the final improvements in [PR #182](https://github.com/honeynet/DICOMHawk/pull/182) are merged
upstream, and no required proposal deliverable remains open.

**By the numbers:** 12 merged pull requests; 177 files changed across the core contribution; 638
tests in the current verified suite; 13 user-facing documents; 2 shipped PACS profiles; and 22
YARA rules across 8 rule files.

---

## 2. Project objectives

The proposal committed to five areas:

1. **Profile-driven architecture and a web-facing extension.** Move the honeypot's visible
   identity from Python into YAML so deployments can impersonate different PACS products without
   modifying core code.
2. **Browser and environment fingerprinting.** Collect browser and automation signals that
   provide more useful context than an IP address alone.
3. **DIMSE handler completion and DICOMweb support.** Complete the traditional DICOM protocol
   surface and add the HTTP services exposed by modern PACS products.
4. **Structured logging and session correlation.** Use one event schema across protocols and
   correlate related activity through session identifiers.
5. **Static payload analysis.** Inspect quarantined attacker uploads without executing them.

The final planned phase also covered hardened Docker deployment.

---

## 3. Work completed

### 3.1 Profile system

The YAML profile system controls the visible DICOM and web identity, supported operations, SOP
classes, transfer syntaxes, resource limits, and timeouts without adding vendor-specific logic to
the core. Deployment details—such as hosts, published ports, database paths, and trusted
proxies—remain command-line or environment settings.

The `fujifilm` profile is based on the Synapse PACS conformance statement and includes 77 Storage
classes, 10 Query/Retrieve classes, and the product's Implementation Class UID. The sparse
`generic-pacs` profile demonstrates that third-party profiles safely inherit neutral defaults.
Profiles can be loaded by bundled name or filesystem path.

**Delivered in [PR #171](https://github.com/honeynet/DICOMHawk/pull/171)** as
[`b150d1cc`](https://github.com/honeynet/DICOMHawk/commit/b150d1cc).

### 3.2 Web extension and deception surface

The attacker-facing Flask application provides profile-specific sign-on, WinAuth,
forgot-password, error, honeytrap, worklist, and static-asset behaviour. Routes, cookies, headers,
and messages are isolated per profile, preventing Fujifilm-specific details from leaking into
`generic-pacs`.

A separate, loopback-only operator application provides a dashboard and APIs for events,
sessions, credentials, uploads, attacker summaries, fingerprints, and active configuration. It
reads persistent rotated logs and tolerates malformed records. Because this defensive interface
intentionally exposes collected credentials and profile bait values, remote access requires
explicit opt-in and authentication.

**Delivered in [PR #172](https://github.com/honeynet/DICOMHawk/pull/172)** as
[`f6cd947b`](https://github.com/honeynet/DICOMHawk/commit/f6cd947b).

### 3.3 DIMSE completion and DICOMweb

Association, release, abort, C-ECHO, C-STORE, C-FIND, C-GET, and C-MOVE behaviour was completed or
corrected. C-MOVE records the requested destination but safely refuses forwarding, while storage
failures return meaningful DICOM status codes.

Both shipped profiles expose DICOMweb. Fujifilm uses the documented Synapse ports and paths for
WADO-URI, QIDO-RS, WADO-RS, and STOW-RS; `generic-pacs` exposes `/dicom-web/` on port 8042. These
services reuse the main repository and storage jail rather than creating independent data paths.

**Delivered across [PR #165](https://github.com/honeynet/DICOMHawk/pull/165) and
[PR #173](https://github.com/honeynet/DICOMHawk/pull/173).**

### 3.4 Structured logging and session correlation

One JSON Lines schema covers DIMSE, WEB, DICOMWEB, and ANALYSIS events with session correlation.
Interaction intelligence is kept separate from developer diagnostics. Rotation can be size- or
time-based, is coordinated across processes, and safely handles external rollover. Docker
independently caps each container's stdout log at five 50 MiB files.

The default Compose deployment stores the persistent interaction log at
`$DICOMHAWK_DATA_DIR/logs/dicomhawk.log`, keeps the DICOM, analysis, and fingerprint SQLite
databases on `dicom_state`, and stores seeded and captured DICOM files on `dicom_storage`. These
survive container recreation and `docker compose down`; only an explicit volume removal such as
`docker compose down -v` deletes the named-volume databases and captures. Host-mounted logs and
generated profiles remain available even after that command.

**Delivered across [PR #165](https://github.com/honeynet/DICOMHawk/pull/165) and
[PR #170](https://github.com/honeynet/DICOMHawk/pull/170), addressing
[issue #164](https://github.com/honeynet/DICOMHawk/issues/164).**

### 3.5 Browser fingerprinting

A credited JavaScript collector gathers 33 browser, rendering, hardware, engine, and automation
signals. Hashing and 18 conservative inconsistency checks run server-side while raw signals remain
available as evidence.

Fingerprinting is profile-driven, optional, bounded per session and source address, and stored in
its own SQLite database.

**Delivered in [PR #179](https://github.com/honeynet/DICOMHawk/pull/179)** as part of
[`83a671df`](https://github.com/honeynet/DICOMHawk/commit/83a671df), closing
[issue #177](https://github.com/honeynet/DICOMHawk/issues/177). The feature commit is
[`4dfa8d36`](https://github.com/honeynet/DICOMHawk/commit/4dfa8d36).

### 3.6 Static-analysis sandbox

Captured C-STORE, STOW-RS, and web-upload payloads are statically analysed for file type, hashes,
entropy, bounded metadata, indicators of compromise, and YARA matches. Nothing is executed.
Encapsulated documents are extracted and scanned separately so rules can inspect the inner file.

Analysis runs in a supervised, resource-limited process. Durable SQLite jobs survive restarts and
queue saturation. The project ships 22 YARA rules across eight files for generic threats, DICOM
polyglots, parser vulnerabilities, encapsulated-document abuse, and archive exhaustion.

**Delivered in [PR #179](https://github.com/honeynet/DICOMHawk/pull/179)** as part of
[`83a671df`](https://github.com/honeynet/DICOMHawk/commit/83a671df), closing
[issue #176](https://github.com/honeynet/DICOMHawk/issues/176). The principal feature commits are
[`6a2b4ed9`](https://github.com/honeynet/DICOMHawk/commit/6a2b4ed9) and
[`c95f1fdb`](https://github.com/honeynet/DICOMHawk/commit/c95f1fdb).

### 3.7 Hardening and deployment

The multi-stage container runs as UID/GID 999 with a read-only root filesystem, all capabilities
dropped, `no-new-privileges`, bounded memory and process counts, a restricted tmpfs, a real
C-ECHO healthcheck, and separate state, evidence, and log storage. Deployment helpers add egress
lockdown, production preflight checks, dedicated capture storage, and exact-IP proxy trust.

**Delivered in [PR #173](https://github.com/honeynet/DICOMHawk/pull/173)** as
[`3166b9a0`](https://github.com/honeynet/DICOMHawk/commit/3166b9a0), with further hardening in
[PR #181](https://github.com/honeynet/DICOMHawk/pull/181) as
[`f72c3335`](https://github.com/honeynet/DICOMHawk/commit/f72c3335).

### 3.8 Post-scope additions

#### 3.8.1 Product additions

Three additions followed the planned twelve-week scope:

- a profile-driven post-login worklist whose patient data agrees with C-FIND;
- a guided `setup.sh` installer that installs prerequisites, writes secure configuration, builds
  the image, checks health, and optionally seeds the deployment;
- realistic TCIA studies rewritten with stable Faker identities and OpenStreetMap locations, with
  a bundled offline fallback and weekly rotation.

**Delivered in [PR #179](https://github.com/honeynet/DICOMHawk/pull/179)** as part of
[`83a671df`](https://github.com/honeynet/DICOMHawk/commit/83a671df), closing
[issue #174](https://github.com/honeynet/DICOMHawk/issues/174) and
[issue #175](https://github.com/honeynet/DICOMHawk/issues/175). The worklist and installer commits
are [`48ae481f`](https://github.com/honeynet/DICOMHawk/commit/48ae481f) and
[`443a3a03`](https://github.com/honeynet/DICOMHawk/commit/443a3a03). The seeding work landed
across [PR #168](https://github.com/honeynet/DICOMHawk/pull/168),
[PR #169](https://github.com/honeynet/DICOMHawk/pull/169), and
[PR #170](https://github.com/honeynet/DICOMHawk/pull/170).

#### 3.8.2 Login access control

The decoy sign-on form and WinAuth use one four-mode access policy:

| Mode | Behaviour |
|---|---|
| `none` | Log and deny every attempt, including declared honey credentials. This creates a pure credential collector. |
| `bait` | Admit only exact pairs declared in `honey_credentials`; all other attempts receive the product's normal error. |
| `keyword` | Admit exact bait pairs and attempts containing a declared keyword. A declared bait username still requires its paired password. Both shipped profiles use this mode. |
| `any` | Admit every credential. This increases engagement but makes the deception easier to detect. |

Keyword grants emit distinct telemetry naming the matched term and field. Keywords are casefolded,
deduplicated, and required to contain at least three characters. Empty keyword lists and legacy
boolean access configuration fail at profile load instead of silently changing behaviour.

**Delivered in [PR #181](https://github.com/honeynet/DICOMHawk/pull/181)** as part of
[`f72c3335`](https://github.com/honeynet/DICOMHawk/commit/f72c3335).

#### 3.8.3 Operational and deployment improvements

The deployment was reorganised so each container has one responsibility. DIMSE, the attacker web
surface, the operator API, DICOMweb, and payload analysis now run as five services from the same
image, selected through `--service`. The original single-process behaviour remains available as
`--service all` for virtual-environment development.

Ingress services submit durable jobs to the shared analysis database; the dedicated analysis
container claims them on a periodic sweep, including work queued while it was unavailable. SQLite
WAL mode and a busy timeout support safe sharing between service processes.

The guided installer now:

- generates a complete custom PACS profile from `generic-pacs`;
- stores interaction logs and generated profiles under `~/data/dicomhawk` by default;
- keeps databases and DICOM storage in named volumes;
- seeds through a disposable management container;
- documents and preserves TCIA, Faker, OSM, honeytoken, and canary settings;
- resolves localized OSM boundaries through both `name` and `name:en`;
- adapts its dialogs to the terminal width.

Role-matrix tests pin exactly which components each service starts. The operator service is
isolated from attacker-facing services and is the only container given the operator token. The
five-container deployment, Japanese TCIA seeding, persistent logs, and dashboard interaction
events were verified against a live stack.

**Merged in [PR #182](https://github.com/honeynet/DICOMHawk/pull/182):** approximately 1,900 added
lines across the implementation, documentation, and tests, with 638 tests passing.

---

## 4. Code contributions

The maintainers squash-merge pull requests. The resulting commit is listed where it is available;
PR #182 is recorded by its final merged status.

| PR | Commit | Scope | Issues |
|---|---|---|---|
| [#165](https://github.com/honeynet/DICOMHawk/pull/165) | [`93f7d86a`](https://github.com/honeynet/DICOMHawk/commit/93f7d86a) | DIMSE and ACSE handlers; interaction-log redesign | [#158](https://github.com/honeynet/DICOMHawk/issues/158), [#164](https://github.com/honeynet/DICOMHawk/issues/164) |
| [#167](https://github.com/honeynet/DICOMHawk/pull/167) | [`97381247`](https://github.com/honeynet/DICOMHawk/commit/97381247) | Storage jail; quarantined uploads excluded from C-FIND and C-GET | [#159](https://github.com/honeynet/DICOMHawk/issues/159) |
| [#168](https://github.com/honeynet/DICOMHawk/pull/168) | [`d1cd9071`](https://github.com/honeynet/DICOMHawk/commit/d1cd9071) | TCIA seed subcommand | [#163](https://github.com/honeynet/DICOMHawk/issues/163) |
| [#169](https://github.com/honeynet/DICOMHawk/pull/169) | [`556c4941`](https://github.com/honeynet/DICOMHawk/commit/556c4941) | OSM and Faker seeding; query/retrieve fixes; documentation | — |
| [#170](https://github.com/honeynet/DICOMHawk/pull/170) | [`02f90bad`](https://github.com/honeynet/DICOMHawk/commit/02f90bad) | Logging fixes; seeding package; weekly rotation; offline fallback | [#164](https://github.com/honeynet/DICOMHawk/issues/164) |
| [#171](https://github.com/honeynet/DICOMHawk/pull/171) | [`b150d1cc`](https://github.com/honeynet/DICOMHawk/commit/b150d1cc) | Profile-driven deception system | [#160](https://github.com/honeynet/DICOMHawk/issues/160) |
| [#172](https://github.com/honeynet/DICOMHawk/pull/172) | [`f6cd947b`](https://github.com/honeynet/DICOMHawk/commit/f6cd947b) | Attacker-facing web surface; second profile; hostile-input hardening | [#161](https://github.com/honeynet/DICOMHawk/issues/161) |
| [#173](https://github.com/honeynet/DICOMHawk/pull/173) | [`3166b9a0`](https://github.com/honeynet/DICOMHawk/commit/3166b9a0) | DICOMweb; browse console; operator API; deployment hardening | [#162](https://github.com/honeynet/DICOMHawk/issues/162) |
| [#179](https://github.com/honeynet/DICOMHawk/pull/179) | [`83a671df`](https://github.com/honeynet/DICOMHawk/commit/83a671df) | Static analysis; browser fingerprinting; guided installer; worklist | [#174](https://github.com/honeynet/DICOMHawk/issues/174), [#175](https://github.com/honeynet/DICOMHawk/issues/175), [#176](https://github.com/honeynet/DICOMHawk/issues/176), [#177](https://github.com/honeynet/DICOMHawk/issues/177) |
| [#180](https://github.com/honeynet/DICOMHawk/pull/180) | [`6c2ce134`](https://github.com/honeynet/DICOMHawk/commit/6c2ce134) | Profile-shipped worklist icons and working sign-out | — |
| [#181](https://github.com/honeynet/DICOMHawk/pull/181) | [`f72c3335`](https://github.com/honeynet/DICOMHawk/commit/f72c3335) | Unhappy-path hardening; multiprocess-safe logging; keyword login bait | — |
| [#182](https://github.com/honeynet/DICOMHawk/pull/182) | Merged | One service per container; persistent logs; custom installer profiles; seeding fixes | — |

All proposal deliverables and the final operational improvements in PR #182 are merged upstream.

### Documentation

Thirteen user-facing documents under `docs/` cover installation, quick start, commands,
configuration, profiles, features, deployment, analysis, fingerprinting, verification, seeding
values, frequently asked questions, and contact information. The verification guide gives
operators post-install checks and expected results for every major surface.

---

## 5. Current state and validation

Every required proposal workstream and the final operational improvements are implemented,
tested, documented, and merged upstream.

| Workstream | Planned weeks | Status |
|---|---:|---|
| Profile system and core refactor | 1–2 | Merged |
| Web extension and initial profiles | 3–4 | Merged |
| Browser fingerprinting and structured logging | 5–6 | Merged |
| DIMSE completion and DICOMweb | 7–8 | Merged |
| Static-analysis sandbox | 9–10 | Merged |
| Hardening and polish | 11–12 | Merged |
| Post-scope worklist icons and sign-out | — | Merged |
| Post-scope unhappy-path hardening and keyword bait | — | Merged |
| Post-scope service split, persistence, and installer expansion | — | Merged |

Validation included:

- **638 automated tests**, including DIMSE and web loopback integration tests;
- DCMTK and pynetdicom clients;
- Sante DICOM Viewer Pro 14.4.1.0 as an independent PACS client;

---

## 6. Future work

No required proposal work remains. Optional future improvements include:

- response-timing jitter, local GeoIP enrichment, Prometheus metrics, and stronger log-integrity
  controls;
- deeper structural DICOM validation, fuzzy hashing, and streaming STOW multipart parsing;
- additional C-FIND matching realism and a decision on TLS port 2762;
- additional post-login Fujifilm functionality;
- more vendor and generic PACS profiles.

---

## 7. Acknowledgements

My thanks to **Karina Elzer**. I could not have asked for a better mentor. She was clear from the
outset about what the project needed and which deliverables mattered most, so I was never left
guessing at priorities.

My thanks to **Ricardo Yaben**. The volume and specificity of his comments on my first substantial
pull request did more for the quality of my code than anything else this summer.

My thanks to **Dr. Emmanouil Vasilomanolakis** for being willing to take the chance in the first
place. Thanks also to **The Honeynet Project** for hosting the project and to **Google Summer of
Code** for making the work possible.