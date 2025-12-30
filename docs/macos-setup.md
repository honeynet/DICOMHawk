# macOS Setup & Troubleshooting

## Prerequisites

- macOS 12+
- Docker Desktop for Mac
  https://www.docker.com/products/docker-desktop/

Verify Docker:

docker --version
docker compose version

---

## Error: zsh: command not found: docker

Install Docker Desktop and ensure the whale icon shows “Docker is running”.

---

## Error: bind 0.0.0.0:5000: address already in use

On macOS, the system Control Center automatically binds to TCP port 5000
and restarts when killed. This prevents DICOMHawk logserver from starting.

### Fix

Edit `docker-compose.yml`:

Change:

"5000:5000"

to:

"5500:5000"

Then restart:

docker compose down
docker compose up -d

Access logserver at:

http://localhost:5500

