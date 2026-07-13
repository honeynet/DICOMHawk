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

Compose publishes:

- container port `104` → host `104`
- attacker web port `8080` → host `8080`
- operator port `8081` → host loopback `127.0.0.1:8081`

`DICOMHAWK_PORTS` in `.env` controls which ports the server process listens on *inside* the container. The host-side publishing above is fixed in `docker-compose.yml`; edit it directly if you need different host ports.

Port 104 is privileged on many hosts. Docker normally has the capability needed to
publish it; rootless installations may need a higher host port or host configuration.
For an Internet-facing vendor profile, place a TLS reverse proxy in front of the web
listener rather than presenting port 8080 as the final endpoint.

To stop and clean up:

```bash
docker compose down -v
```
