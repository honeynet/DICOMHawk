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
- generic PACS DICOMweb port `8042` → host `8042`
- Fujifilm DICOMweb ports `9080`, `10080`, `12080`, and `13080` → the same host ports

`DICOMHAWK_PORTS` controls only the DIMSE listeners. DICOMweb listeners come from the selected
profile because their ports are part of the device fingerprint. The host-side publishing above is
fixed in `docker-compose.yml`; only the selected profile's declared listeners accept connections.

The container runs as an unprivileged user and still binds port 104 via a namespaced
`net.ipv4.ip_unprivileged_port_start` sysctl (no host capability handed to the process);
rootless Docker installs may need a higher host port or additional host configuration.

For an Internet-facing deployment — TLS termination, egress lockdown, storage quotas,
resource limits, and the known safety boundaries — see [Deployment](./deployment.md).

To stop while retaining the database, logs, and captured payloads:

```bash
docker compose down
```

`docker compose down -v` is a destructive reset. It permanently deletes all three named
volumes; use it only after exporting any evidence you need.
