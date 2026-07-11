# Quick start

This guide assumes [installation](./installation.md) is complete. Two run modes are documented: venv and Docker.

## Run locally (venv)

From the repository root, with the venv activated:

```bash
dicomhawk serve \
    -p 11112 \
    -ae ORTHANC \
    -t ./traces \
    -l ./data/dicomhawk.log
```

Flags used:

- `-p 11112` — listen on a non-privileged port; using `-p 104` requires root or `CAP_NET_BIND_SERVICE`.
- `-ae ORTHANC` — the AE title the honeypot advertises.
- `-t ./traces` — directory where received DICOM files and quarantined uploads land.
- `-l ./data/dicomhawk.log` — JSON event log path (one event per line).

The server logs `Listening in [11112]` to stdout once the bus is up.

To impersonate a specific device, add `--profile fujifilm` (bundled Fujifilm Synapse PACS) or a path to a custom profile YAML — see [commands](./commands.md#dicomhawk-serve). Without it, a generic default is used. To build your own profile (identity + optional web login/worklist), see [Adding a profile](./profiles.md).

## Run via Docker

After `docker compose up -d`, the service is listening on the host ports defined in your `.env`. Set `DICOMHAWK_PROFILE=fujifilm` in `.env` to run the honeypot as Synapse PACS (empty = generic default):

```bash
docker compose ps
docker compose logs -f dicomhawk
```

Inside the container, the trace directory is `/opt/dicomhawk/storage` (a named Docker volume) and the event log is `/var/log/dicomhawk/dicomhawk.log` (also a named volume).

## Connect with a DICOM client

`pynetdicom` ships client tools as a transitive dependency. From the same venv (in another shell):

```bash
# Send a DICOM file (C-STORE)
python -m pynetdicom storescu 127.0.0.1 11112 ./some-test.dcm \
    -aet TESTSCU -aec ORTHANC
```

A successful C-STORE produces:

- a `.dcm` file under `./traces/storage/`
- a JSON line in `./data/dicomhawk.log` describing the request and the SCP response

## Seed the database

Before starting the server, populate it with realistic DICOM data from [TCIA](https://www.cancerimagingarchive.net/). The `--database` and `--traces` paths **must match** the values you pass to `dicomhawk serve`.

```bash
# one-shot seed from TCGA-LUAD (requires internet)
dicomhawk seed \
    --database ./data/dicomhawk.db \
    --traces ./traces

# start the server against the seeded database
dicomhawk serve \
    -p 11112 -ae ORTHANC \
    --database ./data/dicomhawk.db \
    -t ./traces \
    -l ./data/dicomhawk.log
```

Seed with real hospital names from OpenStreetMap (falls back to built-in list if Overpass is unreachable). Pass `--osm-country` alongside `--osm-city` so the city resolves to the right country:

```bash
dicomhawk seed --database ./data/dicomhawk.db --traces ./traces \
    --osm-city "Boston" --osm-country "US"
```

Re-seed automatically every 60 minutes (blocks until Ctrl-C or SIGTERM):

```bash
dicomhawk seed --database ./data/dicomhawk.db --traces ./traces \
    --interval 60
```

Generate patient/physician names in a different locale:

```bash
dicomhawk seed --database ./data/dicomhawk.db --traces ./traces \
    --locale de_DE
```

Provide your own location list (`[{"institution": "...", "address": "..."}]`):

```bash
dicomhawk seed --database ./data/dicomhawk.db --traces ./traces \
    --locations ./my-locations.json
```

If TCIA is unreachable the command logs a warning and exits cleanly with 0 instances stored. With `--interval` it retries automatically on the next tick.

## Where to look

| What | venv | Docker |
|---|---|---|
| Received DICOM files | `./traces/storage/` | `/opt/dicomhawk/storage/` in the container (named volume `dicom_storage`) |
| Quarantined uploads | `./traces/quarantine/` | `/opt/dicomhawk/storage/quarantine/` (same volume) |
| JSON event log | `./data/dicomhawk.log` | `/var/log/dicomhawk/dicomhawk.log` (named volume `logs`) |
| Internal Python logs | stdout / `docker compose logs` | same |
