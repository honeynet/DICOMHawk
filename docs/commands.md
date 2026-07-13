# Commands

## `dicomhawk serve`

Start the DICOM honeypot server.

| Flag | Short | Default | Description |
|---|---|---|---|
| `--profile` | | *(generic default)* | Deception profile: `fujifilm`, `generic-pacs`, or a path to a custom YAML. Drives identity, advertised SOP classes, enabled DIMSE ops, and (for `pacs`-kind profiles) the web surface below. |
| `--host` | `-h` | `0.0.0.0` | Bind address |
| `--ports` | `-p` | `104,11112` | Comma-separated listen ports |
| `--ae_title` | `-ae` | *(profile's)* | Override the AE title the profile advertises |
| `--max-associations` | `-ma` | *(profile's)* | Override the max simultaneous associations |
| `--database` | `-db` | *(in-memory)* | Path to SQLite database file — **must match `seed --database`** |
| `--traces` | `-t` | `traces` | Directory for received DICOM files and quarantined uploads |
| `--log-path` | `-l` | `data/dicomhawk.log` | JSON interaction event log (one record per line) |
| `--log-max-bytes` | | `52428800` | Rotate the event log after this many bytes; `0` disables size rotation |
| `--log-backups` | | `5` | Number of rotated event logs to keep |
| `--dev-log` | | | Path for Python-level warnings, errors, and pynetdicom protocol events |
| `--web-port` | | `8080` | Port for the attacker-facing web UI (`pacs`-kind profiles with `web.enabled` only) |
| `--operator-port` | | `8081` | Port for the read-only operator API (`/api/sessions`, `/api/events`, `/api/profiles`) |
| `--operator-host` | | `127.0.0.1` | Bind address for the operator API. Keep the default for a bare-metal deployment — it's what makes the API loopback-only. In Docker, override to `0.0.0.0` and enforce loopback-only via the host-side port mapping instead (a container's own `127.0.0.1` is a separate network namespace host port publishing can't reach) — see `docker-compose.yml`. |
| `--backend-server` | | *(profile value)* | Per-deployment `X-Backendserver` value; also read from `DICOMHAWK_BACKEND_SERVER` |
| `--public-base-url` | | *(request origin)* | External HTTP(S) origin for generated OIDC redirect URIs; also read from `DICOMHAWK_PUBLIC_BASE_URL` |
| `--verbose` | `-v` | | Print a compact colored event summary to stdout; auto-enabled when stdout is a TTY |

**Profiles** decide which device the honeypot impersonates. With no `--profile`, it runs a generic default (AE title `ORTHANC`, all storage classes, no web surface). `--profile fujifilm` makes it present as a Fujifilm Synapse PACS — that device's identity, supported SOP classes, status codes, and a matching web login/worklist. `--profile generic-pacs` is a vendor-neutral second profile with the same web surface but plain, unbranded pages and generic headers — useful as a starting point for a custom profile. To impersonate a different device, write a profile YAML in the same format and pass its path — see [Adding a profile](./profiles.md) for the full schema and how to build the optional web surface.

**Note:** with the default in-memory database, seeded data does not persist between restarts. Pass `--database` for any deployment intended to survive a restart.

The built-in web listener is HTTP/1.1. A public high-fidelity deployment should terminate
TLS at a reverse proxy on the target product's observed port (normally 443, and DICOM TLS
on 2762 where configured), set `--public-base-url` to that external origin, preserve the original `Host`, and keep the operator
API unpublished. Do not expose plaintext port 8080 as the only web surface for a profile
that is normally seen over HTTPS; protocol and port mismatches are easy fingerprints.

Incoming, untrusted C-STORE objects are deliberately quarantined. Their metadata may be
indexed for C-FIND realism, but C-GET will not send the quarantined bytes back. This is a
safety boundary and therefore a known round-trip difference from a production PACS—not a
claim of perfect emulation. Seeded objects (`safe=True`) remain retrievable. Profiles also
cap each incoming instance with `dicom.max_store_bytes` (512 MiB by default); use a finite
filesystem/volume quota as the aggregate bound against many smaller stores.

---

## `dicomhawk seed`

Populate the honeypot database with realistic DICOM data from [TCIA](https://www.cancerimagingarchive.net/).

| Flag | Short | Default | Description |
|---|---|---|---|
| `--collection` | `-c` | `TCGA-LUAD` | TCIA collection name(s), comma-separated; with `--rotate` one is chosen per ISO week |
| `--max-series` | `-s` | `3` | Max series to download |
| `--max-images` | `-n` | `30` | Images per series. Higher = more realistic IMAGE-level C-FIND responses (real CT series have 100s of slices); lower = less storage/bandwidth |
| `--database` | `-db` | *(in-memory)* | SQLite database path — **must match `serve --database`** |
| `--traces` | `-t` | `traces` | Traces directory — **must match `serve --traces`** |
| `--locations` | `-L` | *(built-in)* | JSON file of institutions/addresses — see [Custom data files](#custom-data-files) |
| `--locale` | | `en_US` | Faker locale for patient/physician name generation (e.g. `de_DE`, `ja_JP`) |
| `--names` | `-N` | *(Faker)* | JSON file of patient/physician name pools — overrides `--locale`; see [Custom data files](#custom-data-files) |
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

**OSM scoping:** pair `--osm-city` with `--osm-country` — the city alone matches same-named cities in every country (there is more than one "Berlin"), and the country code pins it to the one you mean. Up to `--osm-max` names are kept; results are cached for 24 h per city/country pair.

**TCIA fallback:** if TCIA is unreachable the seeder falls back to a small DICOM set bundled with the package, so the honeypot still looks populated offline. With `--interval` it also retries the live API on the next tick.

**Honeytoken bait:** when `--honey-url`/`--canary-pdf` (or `seeding/config.yaml`) is set, exactly one instance stored by each `seed` run is tagged with the honey `RetrieveURL` and/or canary PDF — everything else stays a real, untouched image. The tag surfaces when that specific instance is later retrieved via C-GET; it never appears in C-FIND results.

**The tag is not durable across an untagged reseed.** A later `seed` run that reuses the same instance's SOP Instance UID (which happens routinely — seeding is deterministic per collection/epoch) overwrites the file, and if that later run didn't itself have honeytoken bait configured, the tag is gone with it. For bait that survives a recurring `--interval` schedule, set `honey_url`/`canary_pdf` in `seeding/config.yaml` rather than passing them as a one-off CLI flag — the scheduler resolves the config once and reapplies it on every tick, so each reseed replants fresh bait instead of relying on you remembering the flag.

### Custom data files

The institutions and patient/physician names attached to seeded studies are generated from
built-in defaults, but you can supply your own so the honeypot mirrors a specific site.
Both flags apply to live TCIA data **and** the offline fallback set — one config surface for
either path. Neither requires editing the source; a bad or missing file logs a warning and
falls back (institutions → built-in defaults, names → generated), so a typo never aborts a seed.

**Institutions — `--locations` (`-L`)**

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

**Names — `--names` (`-N`)**

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

### Keeping data fresh (weekly rotation)

The core `serve` process never seeds — run `seed` on a schedule with rotation enabled (the default) so the data doesn't go stale. With `--rotate`, patient identities are re-derived each ISO week (the same images appear as different patients over time; re-runs within a week are idempotent), and a comma-separated `--collection`/`--modality` list rotates its source by week. Pick whichever scheduler the host already uses.

**cron**

```cron
# Re-seed every Monday at 03:00, rotating across collections/modalities.
0 3 * * 1 DICOMHAWK_DB=/var/lib/dicomhawk/dicom.db DICOMHAWK_TRACES=/var/lib/dicomhawk/traces \
  dicomhawk seed --collection TCGA-LUAD,TCGA-BRCA,CPTAC-PDA --modality CT,MR --rotate
```

**systemd timer** — a `oneshot` `dicomhawk-seed.service` running the same command, driven by a `dicomhawk-seed.timer` with `OnCalendar=weekly` and `Persistent=true`, enabled via `systemctl enable --now dicomhawk-seed.timer`.

**in-process** — if no system scheduler is available, `dicomhawk seed --interval 10080` runs its own weekly loop (rotation still applies each cycle), but keeps a long-lived process up.
