# Seeding values and examples

This reference explains what to enter for TCIA collections, DICOM modalities, Faker locales,
and OpenStreetMap locations. The values are identifiers, not free-form descriptions: spelling,
case, punctuation, and underscores matter.

The guided installer shows common examples. Use the discovery commands below when you need the
complete current list instead of guessing.

## A complete example

To configure the installer non-interactively:

```bash
DICOMHAWK_SEED_COLLECTION=TCGA-LUAD \
DICOMHAWK_SEED_MODALITY=CT \
DICOMHAWK_SEED_MAX_SERIES=3 \
DICOMHAWK_SEED_MAX_IMAGES=30 \
DICOMHAWK_SEED_LOCALE=en_IN \
DICOMHAWK_SEED_OSM_CITY=Bengaluru \
DICOMHAWK_SEED_OSM_COUNTRY=IN \
DICOMHAWK_SEED_HONEY_URL=https://canary.example/token/123 \
./setup.sh --defaults
```

To run the same kind of seed against an existing Docker deployment:

```bash
docker compose run --rm --no-deps dimse dicomhawk seed \
    --collection TCGA-LUAD \
    --modality CT \
    --max-series 3 \
    --max-images 30 \
    --locale en_IN \
    --osm-city Bengaluru \
    --osm-country IN \
    --honey-url https://canary.example/token/123
```

Use quotes around a city containing spaces, for example `--osm-city "New York"`.

### Japanese Fujifilm example

This is the complete combination used to populate a Fujifilm deployment with Japanese identities
and Tokyo institutions:

```bash
DICOMHAWK_PROFILE=fujifilm \
DICOMHAWK_SEED_COLLECTION=TCGA-LUAD \
DICOMHAWK_SEED_MODALITY=CT \
DICOMHAWK_SEED_MAX_SERIES=3 \
DICOMHAWK_SEED_MAX_IMAGES=20 \
DICOMHAWK_SEED_LOCALE=ja_JP \
DICOMHAWK_SEED_OSM_CITY=Tokyo \
DICOMHAWK_SEED_OSM_COUNTRY=JP \
./setup.sh --defaults --reconfigure
```

The expected result is Japanese patient and physician names from Faker, plus an institution chosen
from the Tokyo OSM result. One location is selected for an entire seed run so its studies look as if
they belong to one PACS installation. Scanner fields such as DICOM `Manufacturer` remain authentic
TCIA acquisition metadata; the Fujifilm profile controls the PACS network and web identity instead.

## TCIA and TCGA collections

`--collection` takes the exact collection label used by The Cancer Imaging Archive (TCIA).
TCGA collections are part of TCIA and commonly begin with `TCGA-`, but the command also accepts
non-TCGA collections.

Common examples include:

| Value | Typical subject area |
|---|---|
| `TCGA-LUAD` | Lung adenocarcinoma |
| `TCGA-BRCA` | Breast invasive carcinoma |
| `TCGA-GBM` | Glioblastoma |
| `TCGA-LIHC` | Liver hepatocellular carcinoma |
| `CPTAC-PDA` | Pancreatic ductal adenocarcinoma |
| `LIDC-IDRI` | Lung CT nodule data |
| `CBIS-DDSM` | Breast mammography |

Collections available from TCIA change over time. Print every collection currently exposed by
the public TCIA API with:

```bash
curl -fsSL \
  'https://services.cancerimagingarchive.net/nbia-api/services/v1/getCollectionValues' \
  | python3 -c 'import json,sys; print("\n".join(sorted(x["Collection"] for x in json.load(sys.stdin))))'
```

The collection name is case-sensitive. Copy it exactly from this output. Some TCIA collections
have access restrictions; DICOMHawk's unauthenticated client is intended for public collections.
The official endpoint and response field are documented in the
[TCIA NBIA Search REST API guide](https://wiki.cancerimagingarchive.net/display/Public/NBIA%2BSearch%2BREST%2BAPI%2BGuide).

Multiple values may be comma-separated:

```text
TCGA-LUAD,TCGA-BRCA,CPTAC-PDA
```

With rotation enabled, one collection is selected per ISO week. The seeder does not download all
three during one run.

## DICOM modalities

`--modality` takes the short DICOM modality code stored by TCIA. The chosen collection must
actually contain that modality.

Common codes are:

| Code | Meaning |
|---|---|
| `CT` | Computed Tomography |
| `MR` | Magnetic Resonance |
| `PT` | Positron Emission Tomography |
| `CR` | Computed Radiography |
| `DX` | Digital Radiography |
| `MG` | Mammography |
| `US` | Ultrasound |
| `NM` | Nuclear Medicine |
| `RTDOSE` | Radiotherapy Dose |
| `RTPLAN` | Radiotherapy Plan |
| `RTSTRUCT` | Radiotherapy Structure Set |
| `SEG` | Segmentation |
| `SR` | Structured Report |

Do not assume a collection supports a code. List every modality in a particular collection with:

```bash
COLLECTION=TCGA-LUAD
curl -fsSL --get \
  --data-urlencode "Collection=$COLLECTION" \
  'https://services.cancerimagingarchive.net/nbia-api/services/v1/getModalityValues' \
  | python3 -c 'import json,sys; print("\n".join(sorted(x["Modality"] for x in json.load(sys.stdin) if x.get("Modality"))))'
```

To list every modality currently present across public TCIA data, omit the collection parameter:

```bash
curl -fsSL \
  'https://services.cancerimagingarchive.net/nbia-api/services/v1/getModalityValues' \
  | python3 -c 'import json,sys; print("\n".join(sorted(x["Modality"] for x in json.load(sys.stdin) if x.get("Modality"))))'
```

Multiple values can be rotated just like collections:

```text
CT,MR,PT
```

## Faker locales

`--locale` controls generated patient and physician names. It does not change TCIA image data.
Use a Faker locale identifier with an underscore, such as `en_US`; `en-US` is not the same value.

Useful examples:

| Locale | Language or region |
|---|---|
| `en_US` | English, United States |
| `en_GB` | English, United Kingdom |
| `en_IN` | English, India |
| `de_DE` | German, Germany |
| `fr_FR` | French, France |
| `es_ES` | Spanish, Spain |
| `pt_BR` | Portuguese, Brazil |
| `ja_JP` | Japanese, Japan |
| `ko_KR` | Korean, South Korea |
| `zh_CN` | Chinese, mainland China |

The installed Faker version currently provides these locales:

```text
am_ET ar_AA ar_AE ar_BH ar_DZ ar_EG ar_JO ar_PS ar_SA az_AZ bg_BG bn_BD bs_BA
cs_CZ da_DK de de_AT de_CH de_DE de_LI de_LU dk_DK el_CY el_GR en en_AU en_BD
en_CA en_GB en_IE en_IN en_KE en_MS en_NG en_NZ en_PH en_PK en_TH en_US es es_AR
es_CA es_CL es_CO es_ES es_MX et_EE fa_IR fi_FI fil_PH fr_BE fr_CA fr_CH fr_DZ
fr_FR fr_QC ga_IE gu_IN ha_NG he_IL hi_IN hr_HR hu_HU hy_AM id_ID ig_NG is_IS
it_CH it_IT ja_JP ka_GE ko_KR la lb_LU lt_LT lv_LV mt_MT ne_NP ng_NG nl_BE
nl_NL no_NO or_IN pl_PL pt_BR pt_PT ro_RO ru_RU sk_SK sl_SI sq_AL sv_SE sw
ta_IN th th_TH tl_PH tr_TR tw_GH uk_UA uz_UZ vi_VN yo_NG zh_CN zh_TW zu_ZA
```

Print the authoritative list from the installed dependency at any time:

```bash
docker compose run --rm --no-deps dimse python3 -c \
  'from faker.config import AVAILABLE_LOCALES; print("\n".join(sorted(AVAILABLE_LOCALES)))'
```

For a virtual-environment installation, use `.venv/bin/python` instead of the Docker command:

```bash
.venv/bin/python -c \
  'from faker.config import AVAILABLE_LOCALES; print("\n".join(sorted(AVAILABLE_LOCALES)))'
```

An invalid locale fails when Faker is initialized, so choose a value printed by this command.

## OpenStreetMap cities and countries

OpenStreetMap seeding looks for hospitals and similar healthcare facilities. The two inputs are:

- `--osm-city`: a real city name, such as `Bengaluru`, `Boston`, `Berlin`, or `Tokyo`.
- `--osm-country`: the two-letter uppercase ISO 3166-1 alpha-2 country code, such as `IN`,
  `US`, `DE`, or `JP`.

DICOMHawk does not have a separate county field. To query a city, enter the city and its country.
To query across an entire country, leave the city empty and provide only the country code.
City lookup matches both the boundary's local OSM `name` and `name:en`. For example, entering
`Tokyo` with `JP` resolves the relation stored locally as `東京都` without requiring Japanese input.

Examples:

```bash
# Hospitals in one city
dicomhawk seed --osm-city "New York" --osm-country US

# Hospitals across one country, capped at the default 50 results
dicomhawk seed --osm-country IN

# Keep at most 15 OSM institutions and use a specific cache file
dicomhawk seed --osm-city Berlin --osm-country DE \
    --osm-max 15 --osm-cache ./data/osm-berlin.json
```

Every current ISO country/region code can be printed on Ubuntu with:

```bash
awk -F '\t' '!/^#/ {printf "%s  %s\n", $1, $2}' /usr/share/zoneinfo/iso3166.tab
```

Search that complete list by country name:

```bash
awk -F '\t' '!/^#/ && tolower($2) ~ /india|germany|united states/ {print}' \
  /usr/share/zoneinfo/iso3166.tab
```

On a system without that file, use the
[ISO Online Browsing Platform](https://www.iso.org/obp/ui/#search/code/). OSM accepts any current
two-letter ISO code for which it has a matching country area; it is not limited to the examples
in this document.

OSM resolution order is:

1. Live OpenStreetMap result for the requested city/country.
2. A custom `--locations` JSON file, if provided.
3. DICOMHawk's bundled institution list.

An OSM timeout or empty result therefore does not leave the seeded database without institutions.
Results are cached for 24 hours per city/country pair.

`Fetched 50 institutions from OpenStreetMap` means the live lookup succeeded and filled the
selection pool; it does not mean one seed run uses all 50. If an OSM object has no `addr:*` tags,
the resulting DICOM `InstitutionAddress` is empty while `InstitutionName` is still populated.

## Quick, normal, and fuller seed sizes

| Goal | `--max-series` | `--max-images` | Notes |
|---|---:|---:|---|
| Installer smoke test | `1` | `5` | Fast and small, but visibly sparse |
| Default deployment | `3` | `30` | Installer default |
| More realistic CT browsing | `5` | `100` | More network traffic and storage |

These are upper bounds. A collection may return fewer matching series or images. Always keep the
trace filesystem bounded as described in [Production deployment](./deployment.md#storage).

## Rotation examples

Rotate collections and modalities weekly, which is the default behavior:

```bash
dicomhawk seed \
    --collection TCGA-LUAD,TCGA-BRCA,CPTAC-PDA \
    --modality CT,MR \
    --rotate
```

Keep the same generated identities and the first collection/modality on every run:

```bash
dicomhawk seed --collection TCGA-LUAD --modality CT --no-rotate
```

Run an in-process weekly schedule, expressed in minutes:

```bash
dicomhawk seed --collection TCGA-LUAD,TCGA-BRCA \
    --modality CT,MR --interval 10080
```

For Docker deployments, prefix these commands with:

```text
docker compose run --rm --no-deps dimse
```

For example:

```bash
docker compose run --rm --no-deps dimse dicomhawk seed \
    --collection TCGA-LUAD,TCGA-BRCA --modality CT,MR --rotate
```
