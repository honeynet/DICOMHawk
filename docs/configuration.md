# Configuration

DICOMHawk has two configuration layers, and which layer a setting belongs to follows one rule.

**A profile describes the device.** AE title, implementation class UID, supported services,
page templates, cookie names, DICOMweb ports and base paths. Everyone running that profile
gets the same values, because these are what the impersonated product looks like. Profiles are
YAML files. See [Adding a profile](./profiles.md).

**Environment and flags describe the deployment.** Ports, storage paths, log locations, the
operator token, the reverse proxy address, the external origin. These differ per installation.
Two operators sharing them would share a fingerprint, and hard-coding them in a profile would
break anyone who deploys it differently. See [Commands](./commands.md).

When a setting exists in both places, the deployment layer wins.

## The `.env` file

The Docker deployment reads its settings from `.env` in the repository root. `./setup.sh`
writes this file for you; `.env.example` documents every variable and can be copied by hand
instead.

| Variable | Purpose |
|---|---|
| `DICOMHAWK_PROFILE` | Profile name, or a path to a profile YAML. Empty runs the generic default. |
| `DICOMHAWK_AE_TITLE` | Overrides the profile's AE title. Leave empty unless you have a reason. |
| `DICOMHAWK_PORTS` | DIMSE ports to listen on, comma separated. |
| `DICOMHAWK_WEB_PORT` | Attacker-facing web port. |
| `DICOMHAWK_OPERATOR_PORT` | Operator API port, published on host loopback only. |
| `DICOMHAWK_OPERATOR_HOST` | Container-side operator bind address; Compose uses `0.0.0.0` while publishing it on host loopback. |
| `DICOMHAWK_OPERATOR_TOKEN` | Token protecting the operator API. Strongly recommended. |
| `DICOMHAWK_SECURE_COOKIES` | Overrides the profile's `Secure` session-cookie flag. |
| `DICOMHAWK_TRUSTED_PROXY` | Exact IP of the reverse proxy allowed to supply forwarded client identity. |
| `DICOMHAWK_PUBLIC_BASE_URL` | External origin used in generated redirect URIs. |
| `DICOMHAWK_BACKEND_SERVER` | Per-deployment backend header value for profiles that expose one. |
| `DICOMHAWK_ANALYSIS` | Turns payload analysis on or off. |
| `DICOMHAWK_ANALYSIS_RULES` | Optional mounted directory containing additional operator `.yar` rules. |
| `DICOMHAWK_ANALYSIS_TIMEOUT` | Hard wall-clock deadline for one analysis job, in seconds. |
| `DICOMHAWK_ANALYSIS_MAX_BYTES` | Maximum bytes read or extracted from one capture during analysis. |
| `DICOMHAWK_ANALYSIS_QUEUE_SIZE` | In-memory worker wake-up queue bound; durable jobs remain in the analysis DB. |
| `DICOMHAWK_FINGERPRINT` | Turns the browser fingerprint collector on or off. |
| `DICOMHAWK_TRACES` | Directory for captured objects. |
| `DICOMHAWK_DB` | SQLite path for the DICOM index. |
| `DICOMHAWK_ANALYSIS_DB` | SQLite path for analysis results. |
| `DICOMHAWK_FINGERPRINT_DB` | SQLite path for collected fingerprints. |
| `DICOMHAWK_FINGERPRINT_MAX_BYTES` | Maximum size of one collector submission. |
| `DICOMHAWK_FINGERPRINT_MAX_PER_SESSION` | Fingerprints retained for one web session. |
| `DICOMHAWK_FINGERPRINT_MAX_PER_IP` | Per-source-address fingerprint storage cap; must be at least the per-session cap. |
| `DICOMHAWK_TRACES_HOST_PATH` | Production bind-mount source for captures; must be a dedicated bounded filesystem. |
| `DICOMHAWK_STATE_HOST_PATH` | Production bind-mount source for the SQLite databases and cache. |
| `DICOMHAWK_LOGS_HOST_PATH` | Production bind-mount source for interaction logs and rotations. |
| `DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES` | Operator-declared trace-filesystem ceiling checked by the production preflight. |

The three database paths are deliberately separate files. A flood of captured objects filling
the traces volume must not be able to break indexing or lose analysis results.

`.env` may hold the operator token, so it is created with owner-only permissions and is not
tracked in version control.

## Published ports

The container's listening ports and the host ports Compose publishes are two different things,
and they have to agree. `docker-compose.yml` fixes the published set, so changing
`DICOMHAWK_PORTS` on its own would leave the honeypot listening where nothing is reachable.

`./setup.sh` writes a `docker-compose.override.yml` whenever your chosen ports differ from the
defaults, which republishes the set you actually picked. If you edit `.env` by hand, adjust the
published ports too.

DICOMweb ports are not in `.env`. They are part of the impersonated product's fingerprint, so
they come from the profile, and Compose publishes every layout the shipped profiles use.

## Settings that interact

A few combinations are only comprehensible together.

**Who gets in and what it costs.** `grant_access` decides which logins succeed: `none`,
`bait` for the declared pairs only, `keyword` for those plus anything containing a term from
`honey_keywords`, and `any`. Widening the gate engages more attackers and makes the gate itself
easier to infer. See [Adding a profile](./profiles.md) for the full comparison.

**Login and session cookies.** A profile modelling an HTTPS product marks its session cookie
`Secure`, and browsers discard such a cookie over plain HTTP. The decoy login then accepts a
credential and immediately loses the session, showing neither the pages behind it nor an error.
Set `DICOMHAWK_SECURE_COOKIES=false` for a plaintext deployment, or terminate TLS in front and
set `DICOMHAWK_TRUSTED_PROXY`. The server warns at startup when the combination cannot work.

**Who is allowed in.** The profile's `grant_access` decides whether any login succeeds:
`none` denies everything, `bait` admits only the declared honey credentials, `any` admits
everything. It has no effect if the session cookie cannot survive the transport.

**Proxy trust and forwarded identity.** `DICOMHAWK_TRUSTED_PROXY` names one exact address.
Without it, forwarded headers are ignored and every attacker appears to come from the proxy.
With it, the forwarded scheme is also honoured, which is what marks the session cookie `Secure`
on an HTTPS deployment without any profile change.

**Seeding and egress lockdown.** Serving makes no outbound connections, but seeding reaches
public data sources. Seed before dropping egress, or run the seed from a context that still has
network access.
