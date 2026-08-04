# Production deployment

This guide covers running DICOMHawk as an internet-facing honeypot. The shipped
`docker-compose.yml` and `Dockerfile` already apply most of the hardening below; the
rest are host-level controls Docker cannot express on its own.

For a first local run, see [Installation](./installation.md) and [Quick start](./quick_start.md).
For what the honeypot pretends to be, see [Profiles](./profiles.md).

## What the container already does

The shipped image and Compose file run the honeypot:

- **as an unprivileged user.** A multi-stage build installs the package, and the runtime
  stage drops to a system user with no login shell and no home directory.
- **with a read-only root filesystem.** Only the mounted volumes (traces, DB, logs) and a
  `tmpfs` for `/tmp` are writable.
- **with all Linux capabilities dropped** (`cap_drop: ALL`) and `no-new-privileges`. Binding
  the privileged DICOM port 104 as a non-root user is allowed via a namespaced
  `net.ipv4.ip_unprivileged_port_start=0` sysctl, not by handing back `CAP_NET_BIND_SERVICE`.
- **under resource limits.** The 1 GiB memory/swap cap, 128 MiB `/tmp`, `pids_limit`, `cpus`,
  and `nofile` limit bound a connection or PDU flood. The default C-STORE/STOW cap is 64 MiB,
  leaving headroom for parsing and concurrent requests.
- **with a real liveness probe.** The healthcheck loads the active profile, honors its called
  and calling AE-title policy, and opens an unlogged loopback C-ECHO. It needs `echo` enabled.
- **with graceful shutdown.** `SIGTERM` and `SIGINT` drain listeners and close the database;
  Compose allows 45 seconds before escalating to `SIGKILL`.

## Egress lockdown

A honeypot must accept inbound connections but never initiate outbound ones. `internal: true`
on the Compose network is **not** the right tool, because it also blocks the return path for the
published ports, so attackers could not reach the service. Instead, drop egress at the host
firewall while leaving inbound and its replies intact.

Create the network, install the repository's idempotent IPv4/IPv6 rules, then start the service:

```bash
docker compose create
sudo deploy/lockdown-egress.sh apply
docker compose up -d
sudo deploy/lockdown-egress.sh check
```

The rules allow established replies, drop container-originated forwarding, and block new
connections from the bridge to host services. Persist them with the host firewall manager.
Run `sudo deploy/lockdown-egress.sh remove` before deleting the Docker network.

`dicomhawk serve` makes no outbound connections, so this does not affect the honeypot. Only
`dicomhawk seed` reaches out (TCIA and OpenStreetMap). Run it from a management context with
egress allowed, or seed before locking egress down (its offline fallback still populates the
DB if it cannot reach TCIA).

## Storage

Named volumes are convenient for local use but share Docker's host filesystem and are not an
aggregate quota. Production uses the supplied bind-mount override so traces can live on a
dedicated size-limited filesystem while state and logs remain elsewhere.

Provision three directories, with the trace directory mounted from a dedicated finite
filesystem, then set:

```bash
export DICOMHAWK_TRACES_HOST_PATH=/srv/dicomhawk-traces
export DICOMHAWK_STATE_HOST_PATH=/srv/dicomhawk-state
export DICOMHAWK_LOGS_HOST_PATH=/srv/dicomhawk-logs
export DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES=10737418240  # operator-selected 10 GiB ceiling
sudo chown -R 999:999 "$DICOMHAWK_TRACES_HOST_PATH" "$DICOMHAWK_STATE_HOST_PATH" "$DICOMHAWK_LOGS_HOST_PATH"

docker compose -f docker-compose.yml -f deploy/compose.production.yml create
sudo --preserve-env=DICOMHAWK_TRACES_HOST_PATH,DICOMHAWK_STATE_HOST_PATH,DICOMHAWK_LOGS_HOST_PATH,DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES \
  deploy/check-production.sh
docker compose -f docker-compose.yml -f deploy/compose.production.yml up -d
```

Naming files with `-f` turns off Compose's automatic pickup of `docker-compose.override.yml`. If
the guided install generated one because you chose non-default ports, add it to the chain or the
deployment reverts to the default published ports:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f deploy/compose.production.yml up -d
```

The preflight rejects a trace path on the state/log filesystem and rejects a trace filesystem
larger than the operator-selected ceiling. Use a dedicated LVM logical volume, XFS project,
ZFS dataset, or equivalent. A directory on the root filesystem does not satisfy this control.

When traces are full, C-STORE fails closed and STOW reports HTTP 507; neither reports a false
store success. State and logs remain available because production preflight requires another
filesystem.

The in-memory database is lost on restart. A real deployment must pass a file-based
`--database` (the Compose file does) so seeded data and the C-FIND index survive restarts.

When upgrading volumes created by an older root-running image, retain the evidence and repair
ownership before starting the non-root image:

```bash
docker compose down
docker run --rm --user 0 \
  -v dicomhawk_dicom_storage:/opt/dicomhawk/storage \
  -v dicomhawk_dicom_state:/opt/dicomhawk/state \
  -v dicomhawk_logs:/var/log/dicomhawk \
  dicomhawk:latest chown -R 999:999 /opt/dicomhawk /var/log/dicomhawk
```

Do not use `docker compose down -v` for migration; it deletes the evidence instead of fixing it.

## Payload analysis

Static analysis of captured payloads (see [Payload analysis](./analysis.md) for what it does
and what it records) runs by default and needs no network access, so it stays inside the egress
lockdown. Its durable job table (`--analysis-db`, default `analysis.db`) is a second SQLite
file; keep it on the same state volume as `--database`, not the traces volume, for the same
reason the main database is kept off traces: a storage flood on traces must not break the
analysis job table.

The analysis worker runs as its own supervised process inside the same container, under the
same non-root user, capability drops, and `no-new-privileges` as the rest of the honeypot; a
crashed or hung worker (bounded by `--analysis-timeout`) restarts automatically and resumes
any `pending`/`running` work. If the in-memory hand-off queue ever fills up (very high
sustained upload volume), the durable job table still has every job recorded. Nothing is
silently dropped, it is just picked up on the worker's next backlog sweep instead of
immediately. `--analysis-rules` is a deployment setting like everything else on this page, not
part of a profile. Point it at a bind-mounted directory of your own `.yar` files if you want
detections beyond the shipped starters.

## Browser fingerprints

Profiles that enable `web.fingerprint` serve a small collector on the attacker-facing web
surface (see [Browser fingerprinting](./fingerprinting.md)). Its data goes to a third SQLite
file, `--fingerprint-db`, which belongs on the state volume for the same reason as the other
two: the container's root filesystem is read-only, and a traces flood must not affect it.

Collection is bounded on purpose: one submission is capped by `--fingerprint-max-bytes`, and
each web session may store at most `--fingerprint-max-per-session` of them, so a visitor
submitting in a loop cannot grow the database without limit. Storage problems never reach the
visitor, because the endpoint answers identically whether the write succeeded or failed. Run with
`--no-fingerprint` to stop collecting entirely; nothing is served and no endpoint is
registered, and the database file can then be deleted on its own.

## TLS

The built-in web listener is plain HTTP/1.1 and the DICOM listeners are plaintext. A
high-fidelity public deployment terminates TLS at a reverse proxy on the product's observed
ports, normally 443 for the web surface, and DICOM TLS on 2762 where the impersonated device
advertises it (the Fujifilm conformance statement does). Terminating externally, rather than in
process, keeps certificate handling out of the honeypot and matches how these products are
actually fronted.

### Session cookies

Profiles that model an HTTPS product mark their session cookie `Secure`, and browsers discard a
`Secure` cookie received over plain HTTP. On such a deployment the decoy login accepts the
credential, the browser throws the session away, and the attacker sees neither the post-login
pages nor an error, so the surface silently leads nowhere.

Behind a proxy that forwards `X-Forwarded-Proto` with `DICOMHAWK_TRUSTED_PROXY` set, this
resolves itself: the request is already `https`, so the cookie is marked correctly. Everywhere
else, decide explicitly with `DICOMHAWK_SECURE_COOKIES`: `false` for a plaintext deployment so
the decoy works, `true` behind a TLS terminator that does not forward the scheme. The server logs
a warning at startup when the combination cannot work.

Plaintext DICOM on 104/11112 with no 2762 listener is common in the wild and a defensible
default, but it is a **deliberate choice on record**, not an omission. If your profile
advertises 2762, front it with a TLS terminator so the open/closed port set matches the device.

For HTTP/DICOMweb, assign the proxy a stable source IP and make it overwrite `X-Forwarded-For`,
`X-Forwarded-Host`, `X-Forwarded-Port`, and `X-Forwarded-Proto`; direct access to backend
ports must be blocked. Set `DICOMHAWK_TRUSTED_PROXY` to the proxy's exact source IP; wildcard
trust is intentionally unsupported. Also preserve `Host`, set `DICOMHAWK_PUBLIC_BASE_URL`, hide
the proxy's own `Server` header, and match the target's certificate, TLS versions, ciphers, and
ALPN behavior.

DIMSE has no trusted HTTP header. A DICOM TLS terminator on 2762 must preserve the peer address
with a transparent L4 design, or export the original address to the same SIEM out of band. A
normal source-NAT TCP proxy makes every DIMSE event appear to come from the proxy and is not an
acceptable intelligence deployment.

## Operator API

The operator API and dashboard are a read-only defensive surface and must never be internet-
facing. The Compose file publishes its port to host loopback only (`127.0.0.1:8081:8081`); the
container binds `0.0.0.0` because a container's own loopback is unreachable through Docker's port
mapping. Keep that mapping loopback-only, and set `DICOMHAWK_OPERATOR_TOKEN` (Basic/Bearer) if
anything beyond the local host can reach the listener.

## Keeping data fresh

`serve` never seeds. Run `seed` on a schedule (cron / systemd timer) with rotation so the data
does not go stale. See [Commands](./commands.md#dicomhawk-seed). Give the seed run egress
(see above) and point it at the same `--database`/`--traces` as `serve`.

## Logs

The interaction log (`--log-path`) is one JSON record per line, ready to ship to your SIEM. It rotates
by size (`--log-max-bytes` / `--log-backups`), and Docker's json-file driver caps the container
stdout log separately. Keep the developer log (`--dev-log`, Python-level diagnostics) separate
from the interaction log; only the latter is attacker intelligence.

Also collect Docker health transitions, OOM events, restart counts, and filesystem usage on the
host. These are operational failures rather than attacker interaction events and should alert
without contaminating the honeypot event stream.

## Known safety boundaries

These are deliberate and documented, not gaps to hide from an operator:

- **Storage jail round-trip.** Attacker-uploaded objects are indexed and visible in C-FIND but
  C-GET/WADO never return their bytes. A store→get round trip is therefore distinguishable from a
  real PACS. This is the point of the jail (the honeypot cannot be used to exfiltrate or relay
  files), not a bug.
- **Capture-then-proceed auth.** The web and DICOMweb auth surfaces harvest submitted
  credentials and then continue; there is no real Active Directory domain behind them.
- **Aggregate storage.** Bounded by the filesystem quota you set, not by an in-process byte
  counter (see Storage above).
