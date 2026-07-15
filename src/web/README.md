# Web engine

`web.app` is the shared attacker-facing Flask engine. It does not contain a vendor
identity: routes, cookies, response headers, templates, assets, OIDC values, honeytraps,
and text all come from the active `ProfileConfig`. The read-only operator API is a
separate Flask app bound to loopback by default.

Use the normal command rather than running this package directly:

```bash
dicomhawk serve --profile fujifilm --web-port 8080
```

The filesystem layout and complete schema are documented in
[`docs/profiles.md`](../../docs/profiles.md). Bundled assets live at
`src/profiles/<name>/web/{templates,static}`. External profile YAML files may put a
`web/` directory beside the YAML.

## Behavior

- Unauthenticated entry requests receive a realistic sign-on redirect with a fresh
  flow token.
- Login, forgotten-password, Windows-auth, scanner 404, honeytrap, oversized-request,
  and worklist activity are recorded in the shared JSON interaction log.
- Ordinary credentials are denied unless `grant_access` is enabled. Declared honey
  credentials always enter the decoy and produce a distinct high-confidence event.
- Successful Fujifilm flows land under `/WorkflowUI/`; deep links below that prefix
  remain in the authenticated shell.
- The worklist reads trusted seeded studies from the same repository used by DIMSE.
- Request bodies are bounded by `web.max_request_bytes` (1 MiB by default), and logged
  attacker-controlled fields are truncated.

## Fingerprint boundaries

Only profiles that explicitly set `legacy_csp_header: true` emit the legacy
`X-Content-Security-Policy` header. This prevents a Synapse-specific header from
leaking into generic or future profiles. `content_security_policy: null` disables CSP
entirely when that is faithful to a target.

The Fujifilm profile sets the observed IIS/ASP.NET headers, cookie prefixes, paths,
7.4.300 version, OIDC scopes, and CSP. `X-Backendserver` is site-specific; override it
per deployment with `DICOMHAWK_BACKEND_SERVER` or `--backend-server`.

The browser assets intentionally do not attempt HTTP password encryption unless both
a public key and `JSEncrypt` are actually present. That guard prevents the copied page
from throwing a console-visible `ReferenceError` on the built-in HTTP listener.

## Deployment

Waitress provides HTTP/1.1. For a public vendor-profile deployment, terminate TLS at a
reverse proxy on the expected external port, set `DICOMHAWK_PUBLIC_BASE_URL` so OIDC
redirect URIs retain the public HTTPS origin, and do not publish the operator API.
Presenting `:8080` plaintext as the final endpoint is a protocol/port fingerprint.

The component creates both listening sockets before starting their threads. A bind
failure is therefore reported during startup and any listener already created is
closed. SIGINT/SIGTERM shuts down Waitress, DIMSE listeners, and the repository.

## Known fidelity boundary

The Windows-auth endpoint uses Basic authentication to capture submitted credentials.
Real IIS commonly uses multi-round `Negotiate`/NTLM. The native browser dialog is
similar, but the wire protocol is distinguishable; do not describe that route as
packet-perfect NTLM emulation.
