# Commands

Most deployments never need to assemble these by hand: `./setup.sh` asks what a first run
needs, writes `.env`, and starts the stack (see [Installation](./installation.md)). The flags
below are what it configures, and what you reach for afterwards.

## `dicomhawk serve`

Start the DICOM honeypot server.

| Flag | Short | Default | Description |
|---|---|---|---|
| `--profile` | | *(generic default)* | Deception profile: `fujifilm`, `generic-pacs`, or a path to a custom YAML. Drives identity, advertised SOP classes, enabled DIMSE ops, and (for `pacs`-kind profiles) the web surface below. |
| `--host` | `-h` | `0.0.0.0` | Bind address |
| `--ports` | `-p` | `104,11112` | Comma-separated listen ports |
| `--ae_title` | `-ae` | *(profile's)* | Override the AE title the profile advertises |
| `--max-associations` | `-ma` | *(profile's)* | Override the max simultaneous associations |
| `--database` | `-db` | *(in-memory)* | Path to SQLite database file. **Must match `seed --database`** |
| `--traces` | `-t` | `traces` | Directory for received DICOM files and quarantined uploads |
| `--log-path` | `-l` | `data/dicomhawk.log` | JSON interaction event log (one record per line) |
| `--log-max-bytes` | | `52428800` | Rotate the event log after this many bytes; `0` disables size rotation but keeps cross-process write locking |
| `--log-backups` | | `5` | Number of rotated event logs to keep |
| `--dev-log` | | | Path for Python-level warnings, errors, and pynetdicom protocol events |
| `--web-port` | | `8080` | Port for the attacker-facing web UI (`pacs`-kind profiles with `web.enabled` only) |
| `--operator-port` | | `8081` | Port for the read-only operator dashboard (`/`) and API (`/api/overview`, `/api/stats`, `/api/attackers`, `/api/credentials`, `/api/uploads`, `/api/events`, `/api/sessions`, `/api/profiles`, `/api/artifacts`, `/api/fingerprints`) |
| `--operator-host` | | `127.0.0.1` | Bind address for the operator surface. A non-loopback value also requires `--allow-remote-operator`. In Docker, use `0.0.0.0`; the supplied host mapping still publishes it only on `127.0.0.1`. |
| `--operator-token` | | *(unset)* | Optional operator password/Bearer token; also read from `DICOMHAWK_OPERATOR_TOKEN`. Browsers use Basic auth (any username), while API clients may use Basic or `Authorization: Bearer …`. |
| `--allow-remote-operator` | | off | Explicitly permit a non-loopback operator bind. The supplied Docker configuration sets this because container loopback cannot be published; it retains a host-side loopback-only port mapping. |
| `--trusted-proxy` | | *(unset)* | Exact reverse-proxy IP trusted to supply forwarded client IP, host, port, and scheme; also read from `DICOMHAWK_TRUSTED_PROXY`. Block direct access to the backend port. |
| `--backend-server` | | *(profile value)* | Per-deployment `X-Backendserver` value; also read from `DICOMHAWK_BACKEND_SERVER` |
| `--secure-cookies` / `--no-secure-cookies` | | *(profile value)* | Override the profile's `Secure` session-cookie flag; also read from `DICOMHAWK_SECURE_COOKIES`. Browsers discard `Secure` cookies over plain HTTP, so a profile modelling an HTTPS product needs `--no-secure-cookies` on a plaintext deployment or its decoy login cannot complete |
| `--public-base-url` | | *(request origin)* | External HTTP(S) origin for generated OIDC redirect URIs; also read from `DICOMHAWK_PUBLIC_BASE_URL` |
| `--verbose` | `-v` | | Print a compact colored event summary to stdout; auto-enabled when stdout is a TTY |
| `--analysis` / `--no-analysis` | | on | Run captured payloads through the static analysis pipeline (see [Payload analysis](./analysis.md)); also read from `DICOMHAWK_ANALYSIS` |
| `--analysis-db` | | `analysis.db` | SQLite path for the durable analysis job table; also read from `DICOMHAWK_ANALYSIS_DB` |
| `--analysis-rules` | | *(none)* | Directory of additional operator `.yar` files, on top of the shipped starters; also read from `DICOMHAWK_ANALYSIS_RULES` |
| `--analysis-timeout` | | `10.0` | Hard wall-clock deadline per analysis job, in seconds; also read from `DICOMHAWK_ANALYSIS_TIMEOUT` |
| `--analysis-max-bytes` | | `67108864` | Bounded read/extraction cap per analyzed capture; also read from `DICOMHAWK_ANALYSIS_MAX_BYTES` |
| `--fingerprint` / `--no-fingerprint` | | on | Serve the browser fingerprint collector on profiles whose `web.fingerprint` is enabled (see [Browser fingerprinting](./fingerprinting.md)); also read from `DICOMHAWK_FINGERPRINT` |
| `--fingerprint-db` | | `fingerprint.db` | SQLite path for collected browser fingerprints, separate from every other store; also read from `DICOMHAWK_FINGERPRINT_DB` |
| `--fingerprint-max-bytes` | | `65536` | Hard cap on one collector submission body; also read from `DICOMHAWK_FINGERPRINT_MAX_BYTES` |
| `--fingerprint-max-per-session` | | `20` | Submissions stored per web session before further ones are dropped; also read from `DICOMHAWK_FINGERPRINT_MAX_PER_SESSION` |
| `--fingerprint-max-per-ip` | | `500` | Submissions stored per source address, so rotating sessions cannot bypass the per-session cap; also read from `DICOMHAWK_FINGERPRINT_MAX_PER_IP` |
| `--analysis-queue-size` | | `256` | In-memory wake-up queue bound; the durable job table is the source of truth, not this queue; also read from `DICOMHAWK_ANALYSIS_QUEUE_SIZE` |

**Profiles** decide which device the honeypot impersonates. With no `--profile`, it runs a generic default (AE title `ORTHANC`, all storage classes, no web surface). `--profile fujifilm` makes it present as a Fujifilm Synapse PACS: that device's identity, supported SOP classes, status codes, and a matching web login and worklist. `--profile generic-pacs` is a vendor-neutral second profile with the same web surface but plain, unbranded pages and generic headers, useful as a starting point for a custom profile. To impersonate a different device, write a profile YAML in the same format and pass its path. See [Adding a profile](./profiles.md) for the full schema and how to build the optional web surface.

**DICOMweb** (QIDO-RS / WADO-RS / STOW-RS / WADO-URI) is enabled per profile via a `dicomweb:`
block, not a flag, because its ports and base paths are part of the impersonated product's fingerprint,
so they live in the profile YAML rather than on the command line (see [Adding a profile](./profiles.md#dicomweb)).
The `fujifilm` profile serves the real Synapse DICOMweb ports (9080/10080/12080/13080); publish
whichever ports your profile binds in `docker-compose.yml`. STOW uploads are quarantined exactly
like C-STORE, their exact incoming bytes are retained for analysis, and they are never served back
by WADO. QIDO/WADO content negotiation and default transfer syntax also come from the profile.

**Note:** with the default in-memory database, seeded data does not persist between restarts. Pass `--database` for any deployment intended to survive a restart.

The built-in web listener is HTTP/1.1. A public deployment should terminate TLS at a reverse
proxy on the target product's observed port: normally 443, and DICOM TLS on 2762 where
configured. Set `--public-base-url` to that external origin, configure `--trusted-proxy` for
source attribution, preserve the original `Host`, and keep the operator API unpublished.

`--trusted-proxy` also lets the forwarded scheme through, which is what marks the session
cookie `Secure` on its own. Without it, a profile modelling an HTTPS product needs
`--secure-cookies` to match, and on plaintext it needs `--no-secure-cookies` to work at all.

Do not expose plaintext port 8080 as the only web surface for a profile normally seen over
HTTPS. Protocol and port mismatches are easy fingerprints. The full internet-facing checklist
is in [Deployment](./deployment.md).

C-FIND answers with more than the Query/Retrieve index itself stores. Patient sex and
birth date, study and series description, body part, institution, station, referring
physician, modalities in study, and the study's instance count are all recorded per study
as objects are stored, and returned when a client asks for them, so a DICOM viewer and the
web worklist never disagree about the same study. Requesting one of these as a match key
filters on it rather than being ignored.

Incoming, untrusted C-STORE objects are deliberately quarantined. Their metadata may be
indexed for C-FIND realism, but C-GET will not send the quarantined bytes back. This is a
safety boundary, and therefore a known round-trip difference from a production PACS rather than a
claim of perfect emulation. Seeded objects (`safe=True`) remain retrievable. Profiles also
cap each incoming instance with `dicom.max_store_bytes` (64 MiB by default); use a finite
filesystem/volume quota as the aggregate bound against many smaller stores.

---

## `dicomhawk seed`

Populate the honeypot database with realistic DICOM data from [TCIA](https://www.cancerimagingarchive.net/).

| Flag | Short | Default | Description |
|---|---|---|---|
| `--collection` | `-c` | `TCGA-LUAD` | TCIA collection name(s), comma-separated; with `--rotate` one is chosen per ISO week |
| `--max-series` | `-s` | `3` | Max series to download |
| `--max-images` | `-n` | `30` | Images per series. Higher = more realistic IMAGE-level C-FIND responses (real CT series have 100s of slices); lower = less storage/bandwidth |
| `--database` | `-db` | *(in-memory)* | SQLite database path. **Must match `serve --database`** |
| `--traces` | `-t` | `traces` | Traces directory. **Must match `serve --traces`** |
| `--locations` | `-L` | *(built-in)* | JSON file of institutions and addresses. See [Custom data files](#custom-data-files) |
| `--locale` | | `en_US` | Faker locale for patient/physician name generation (e.g. `de_DE`, `ja_JP`) |
| `--names` | `-N` | *(Faker)* | JSON file of patient and physician name pools. Overrides `--locale`. See [Custom data files](#custom-data-files) |
| `--modality` | `-m` | `CT` | DICOM modality/modalities, comma-separated; with `--rotate` one is chosen per ISO week |
| `--rotate` / `--no-rotate` | | `--rotate` | Rotate patient identities (by ISO week) and source collection/modality for variety on repeated seeding; `--no-rotate` keeps fully deterministic output |
| `--osm-city` | | | Query OpenStreetMap for real hospital names in this city |
| `--osm-country` | | | ISO 3166-1 alpha-2 country code for OSM query (e.g. `US`, `DE`) |
| `--osm-cache` | | `~/.cache/dicomhawk/osm.json` | Path for the OSM institution cache (TTL: 24 h) |
| `--osm-max` | | `50` | Maximum number of institutions to fetch from OpenStreetMap |
| `--interval` | `-i` | `0` | Re-seed every N minutes in the background; `0` = run once and exit |
| `--honey-url` | | *(seeding/config.yaml)* | URL baked as `RetrieveURL` into one seeded instance per run |
| `--canary-pdf` | | *(seeding/config.yaml)* | Path to a PDF canary token baked as `EncapsulatedDocument` into one seeded instance per run |

**Location resolution order:** OSM query → `--locations` file → built-in 6-entry defaults. Each level falls back to the next if it returns nothing or fails.

**OSM scoping:** pair `--osm-city` with `--osm-country`. The city alone matches same-named cities in every country (there is more than one "Berlin"), and the country code pins it to the one you mean. Up to `--osm-max` names are kept; results are cached for 24 h per city/country pair.

**Progress:** the command prints what it is about to download, then one line per series as it
starts. Downloading a full `--max-series 3` × `--max-images 30` run takes several minutes with no
network activity you can see otherwise, so the per-series lines are how you tell a slow download
from a stall.

**TCIA fallback:** if TCIA is unreachable the seeder falls back to a small DICOM set bundled with the package, so the honeypot still looks populated offline. With `--interval` it also retries the live API on the next tick.

**Honeytoken bait:** when `--honey-url`/`--canary-pdf` (or `seeding/config.yaml`) is set, exactly one instance stored by each `seed` run is tagged with the honey `RetrieveURL` or canary PDF, and everything else stays a real, untouched image. The tag surfaces when that specific instance is later retrieved via C-GET; it never appears in C-FIND results.

**How long the bait lasts depends on which flag you use.** `--canary-pdf` stores the bait as its own Encapsulated PDF instance, under a UID derived from the source instance and the PDF itself. Ordinary seeding never produces that UID, so the canary is not overwritten and stays in the archive until you clear the volume. `--honey-url` on its own instead adds a `RetrieveURL` to a real image, which keeps its original SOP Instance UID; because seeding is deterministic per collection and epoch, a later untagged run rewrites that same instance and the tag goes with it.

For bait that is replanted automatically, set `honey_url` or `canary_pdf` in `seeding/config.yaml` rather than passing a one-off CLI flag. The `--interval` scheduler resolves that config once and reapplies it on every tick.

### Custom data files

The institutions and patient/physician names attached to seeded studies are generated from
built-in defaults, but you can supply your own so the honeypot mirrors a specific site.
Both flags apply to live TCIA data **and** the offline fallback set, one config surface for
either path. Neither requires editing the source; a bad or missing file logs a warning and
falls back (institutions → built-in defaults, names → generated), so a typo never aborts a seed.

**Institutions: `--locations` (`-L`)**

A JSON array of objects. `address` is optional.

```json
[
  { "institution": "St. Mary's Regional Hospital", "address": "410 Elm St, Springfield, IL 62704" },
  { "institution": "Lakeside Imaging Center",      "address": "88 Harbor Rd, Duluth, MN 55802" }
]
```

```sh
dicomhawk seed --locations ./my-hospitals.json
```

Institutions can also come from live OpenStreetMap data (`--osm-city` + `--osm-country`).
Resolution order is OSM → `--locations` → built-in defaults.

**Names: `--names` (`-N`)**

A JSON object with `male` and `female` pools (required) and an optional `physician` pool.
Each entry is a DICOM Person Name in `Family^Given` form. `physician` defaults to the
combined patient pools when omitted.

```json
{
  "male":      ["Andersen^Lars", "Okafor^Chidi", "Nguyen^Minh"],
  "female":    ["Andersen^Freja", "Okafor^Ada", "Nguyen^Linh"],
  "physician": ["House^Gregory", "Grey^Meredith"]
}
```

```sh
dicomhawk seed --names ./my-names.json
```

`--names` overrides `--locale`; use `--locale` alone (e.g. `--locale de_DE`) when you just
want locale-appropriate generated names and don't need a fixed list. The `male`/`female`
split is what keeps `PatientSex` consistent with the assigned name, so both are required.

**Procedure descriptions**

Public research collections carry `StudyDescription` only sometimes, and it is the column
a PACS worklist shows as the procedure name. Where a study already has one it is kept
verbatim; where it is missing, seeding fills it in from a built-in pool keyed by modality
and body part, so a chest CT never gets labelled as a head study. The chosen
description is stable for a given study and rotates with `--rotate`, exactly like the
patient identities. A description that changed on every page reload would give the
worklist away. A study that already carries a real `StudyDescription` is never overwritten.

Seeding also recomputes `PatientAge` from the birth date and study date it assigns, so
the three fields agree; a stale age carried over from the source data would contradict
them.

### Keeping data fresh (weekly rotation)

The core `serve` process never seeds. Run `seed` on a schedule with rotation enabled (the default) so the data doesn't go stale. With `--rotate`, patient identities are re-derived each ISO week (the same images appear as different patients over time; re-runs within a week are idempotent), and a comma-separated `--collection`/`--modality` list rotates its source by week. Pick whichever scheduler the host already uses.

**cron**

```cron
# Re-seed every Monday at 03:00, rotating across collections/modalities.
0 3 * * 1 DICOMHAWK_DB=/var/lib/dicomhawk/dicom.db DICOMHAWK_TRACES=/var/lib/dicomhawk/traces \
  dicomhawk seed --collection TCGA-LUAD,TCGA-BRCA,CPTAC-PDA --modality CT,MR --rotate
```

**systemd timer:** a `oneshot` `dicomhawk-seed.service` running the same command, driven by a `dicomhawk-seed.timer` with `OnCalendar=weekly` and `Persistent=true`, enabled via `systemctl enable --now dicomhawk-seed.timer`.

**in-process:** if no system scheduler is available, `dicomhawk seed --interval 10080` runs its own weekly loop (rotation still applies each cycle), but keeps a long-lived process up.
