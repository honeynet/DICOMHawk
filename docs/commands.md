# Commands

## `dicomhawk serve`

Start the DICOM honeypot server.

| Flag | Short | Default | Description |
|---|---|---|---|
| `--host` | `-h` | `0.0.0.0` | Bind address |
| `--ports` | `-p` | `104,11112` | Comma-separated listen ports |
| `--ae_title` | `-ae` | `ORTHANC` | AE title advertised to peers |
| `--impl_uid` | `-uid` | `1.2.3.4` | Implementation class UID |
| `--impl_name` | `-name` | `ORTHANC` | Implementation version name |
| `--dimse` | `-d` | `associate,echo,get,find,move,store,release,abort` | Enabled DIMSE operations (comma-separated) |
| `--database` | `-db` | *(in-memory)* | Path to SQLite database file — **must match `seed --database`** |
| `--traces` | `-t` | `traces` | Directory for received DICOM files and quarantined uploads |
| `--log-path` | `-l` | `data/dicomhawk.log` | JSON interaction event log (one record per line) |
| `--honey-url` | | | URL injected as `RetrieveURL` in outbound datasets |
| `--canary-pdf` | | | Path to a PDF canary token injected as `EncapsulatedDocument` |
| `--dev-log` | | | Path for Python-level warnings, errors, and pynetdicom protocol events |
| `--verbose` | `-v` | | Print a compact colored event summary to stdout; auto-enabled when stdout is a TTY |

**Note:** with the default in-memory database, seeded data does not persist between restarts. Pass `--database` for any deployment intended to survive a restart.

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

**Location resolution order:** OSM query → `--locations` file → built-in 6-entry defaults. Each level falls back to the next if it returns nothing or fails.

**OSM scoping:** pair `--osm-city` with `--osm-country` — the city alone matches same-named cities in every country (there is more than one "Berlin"), and the country code pins it to the one you mean. Up to `--osm-max` names are kept; results are cached for 24 h per city/country pair.

**TCIA fallback:** if TCIA is unreachable the seeder falls back to a small DICOM set bundled with the package, so the honeypot still looks populated offline. With `--interval` it also retries the live API on the next tick.

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
