# Installation

DICOMHawk targets Ubuntu (22.04 or 24.04) and Python 3.12+. Two install paths are supported:

- A local **venv** install for development and quick experimentation.
- A **Docker** install for deployment or contained testing.


## Apt dependencies

`pylibjpeg-libjpeg` and `pylibjpeg-openjpeg` need native libraries to build their wheels.

```bash
sudo apt update
sudo apt install -y \
    python3.12 python3.12-venv python3.12-dev \
    build-essential \
    libjpeg-dev zlib1g-dev libopenjp2-7
```

## Python install (venv)

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

This installs the `dicomhawk` console script into the venv. Confirm:

```bash
dicomhawk --help
dicomhawk serve --help
```

## Docker install

```bash
cp .env.example .env       # customize ports / AE title if desired
make build-docker          # builds dicomhawk:latest from the repo's Dockerfile
docker compose up -d
docker compose ps          # status should turn `healthy` within ~30 seconds
docker compose logs -f dicomhawk
```

Compose maps:

- container port `11112` → host `${DICOMHAWK_PORT_PRIMARY:-11112}`
- container port `104` → host `${DICOMHAWK_PORT_SECONDARY:-1104}` (the host side defaults to a non-privileged port to avoid needing `CAP_NET_BIND_SERVICE`)

To stop and clean up:

```bash
docker compose down -v
```

