# Installation

DICOMHawk targets Ubuntu (22.04 or 24.04) and Python 3.12+. Two install paths are supported:

- A local **venv** install for development and quick experimentation.
- A **Docker** install for deployment or contained testing.

## Guided install

`setup.sh` is the shortest path to a running honeypot, from a machine with nothing installed on
it. It installs any missing prerequisites, asks what the deployment needs, writes the
configuration, builds the image, starts the stack, waits for the container to report healthy, and
offers to seed the database.

```bash
./setup.sh
```

**Prerequisites it installs for you**, if they are absent or too old: Docker Engine and the
Compose plugin (2.24 or newer is required), and `whiptail` for the questions. Docker comes from
Docker's own apt repository, following their documented procedure, because distribution packages
often ship a Compose too old for the port override below. You are asked before anything is
installed, and it needs `sudo`. Automatic installation is Debian/Ubuntu only; elsewhere the script
names what is missing and stops.

It also adds you to the `docker` group. That does not affect the shell you are already in, so for
the rest of that run the script falls back to `sudo docker` and tells you to log out and back in.

The questions are **keyboard-driven; whiptail has no mouse support**. `Tab` moves between the
input and the buttons, arrow keys choose, `Space` toggles a checklist entry, `Enter` confirms.
Every screen has a **Back** button that returns to the previous question with your earlier answer
still filled in, and `Esc` abandons the run without writing anything.

Useful variants:

```bash
./setup.sh --defaults      # accept every default, no questions asked
./setup.sh --no-seed       # configure and start, but skip the initial seed
./setup.sh --no-start      # only write the configuration
./setup.sh --reconfigure   # answer the questions again over an existing setup
./setup.sh --no-install    # refuse if something is missing instead of installing it
```

Any answer can be pre-set by exporting the variable it writes, which is what makes an unattended
install repeatable:

```bash
DICOMHAWK_PROFILE=fujifilm DICOMHAWK_PORTS=11112 ./setup.sh --defaults
```

The script writes two files, both of which stay out of version control:

- `.env`, the configuration, created from `.env.example` so every explanatory comment survives.
  It is mode `600` because it may hold the operator token.
- `docker-compose.override.yml`, written **only** when the chosen ports differ from the
  defaults. Compose publishes port `104` for DIMSE, `8080` for the web surface, and loopback
  `8081` for the operator API; the override republishes the set you actually chose. Without it,
  changing `DICOMHAWK_PORTS` alone would leave the honeypot listening on a port nothing publishes.

The guided installer accepts packaged profile names only. A custom profile needs a deliberate
read-only bind mount and should be configured manually as described in [Commands](./commands.md).
The script also refuses to replace a `docker-compose.override.yml` it did not generate.

Everything the script does is described below, so nothing about the result is opaque. Production
deployment (TLS, egress lockdown, storage quotas) is deliberately **not** automated. See
[Deployment](./deployment.md).


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

## Docker install by hand

These are the steps `setup.sh` performs; run them directly if you would rather not use it.

```bash
cp .env.example .env       # customize ports / AE title if desired
make build-docker          # builds dicomhawk:latest from the repo's Dockerfile
docker compose up -d
docker compose ps          # status should turn `healthy` within ~30 seconds
docker compose logs -f dicomhawk
```

Editing `DICOMHAWK_PORTS` by hand is not enough on its own: the published host ports are fixed in
`docker-compose.yml`, so a changed DIMSE port also needs a `docker-compose.override.yml` that
republishes the set (see the guided install above, which generates one).

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

For an Internet-facing deployment, covering TLS termination, egress lockdown, storage quotas,
resource limits, and the known safety boundaries, see [Deployment](./deployment.md).

To stop while retaining the database, logs, and captured payloads:

```bash
docker compose down
```

`docker compose down -v` is a destructive reset. It permanently deletes all three named
volumes; use it only after exporting any evidence you need.
