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
| `--collection` | `-c` | `TCGA-LUAD` | TCIA collection name |
| `--max-series` | `-s` | `3` | Max series to download |
| `--max-images` | `-n` | `5` | Max images per series |
| `--database` | `-db` | *(in-memory)* | SQLite database path — **must match `serve --database`** |
| `--traces` | `-t` | `traces` | Traces directory — **must match `serve --traces`** |
| `--locations` | `-L` | *(built-in)* | JSON file of `[{"institution": "...", "address": "..."}]` |
| `--locale` | | `en_US` | Faker locale for patient/physician name generation (e.g. `de_DE`, `ja_JP`) |
| `--modality` | `-m` | `CT` | DICOM modality to request from TCIA (e.g. `CT`, `MR`, `US`, `DX`) |
| `--osm-city` | | | Query OpenStreetMap for real hospital names in this city |
| `--osm-country` | | | ISO 3166-1 alpha-2 country code for OSM query (e.g. `US`, `DE`) |
| `--osm-cache` | | `~/.cache/dicomhawk/osm.json` | Path for the OSM institution cache (TTL: 24 h) |
| `--osm-max` | | `50` | Maximum number of institutions to fetch from OpenStreetMap |
| `--interval` | `-i` | `0` | Re-seed every N minutes in the background; `0` = run once and exit |

**Location resolution order:** OSM query → `--locations` file → built-in 6-entry defaults. Each level falls back to the next if it returns nothing or fails.

**OSM scoping:** pair `--osm-city` with `--osm-country` — the city alone matches same-named cities in every country (there is more than one "Berlin"), and the country code pins it to the one you mean. Up to `--osm-max` names are kept; results are cached for 24 h per city/country pair.

**TCIA fallback:** if TCIA is unreachable the command logs a warning and exits with 0 instances stored. With `--interval` it retries on the next tick automatically.
