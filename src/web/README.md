# Web engine

A generic, profile-driven Flask engine that serves a profile's web honeypot — a
faithful reproduction of the target device's web UI (sign-on, Windows-auth, forgot
password, error pages). Submitted credentials are captured to the shared interaction
log. The engine is vendor-neutral; each profile supplies its own templates, static
assets, and config.

## Layout

```
src/web/app.py                     # the engine (routes, headers, CSP, capture) — one copy
src/profiles/<name>/web/           # one folder per profile
  config.yaml                      # operator-tunable values
  templates/*.html                 # the profile's pages
  static/                          # the profile's assets (css/js/fonts/favicon)
```

`SYNAPSE_PROFILE=<name>` selects which profile's `web/` folder the engine serves
(default `fujifilm`). Adding a profile is a new folder under `src/profiles/` — no engine
changes, and it inherits credential capture and logging automatically.

## Run

```bash
cd DICOMHawk
.venv/bin/python src/web/app.py
# open http://127.0.0.1:8080/

# select a profile:  SYNAPSE_PROFILE=<name> .venv/bin/python src/web/app.py
# host/port/log:     SYNAPSE_HOST=0.0.0.0 SYNAPSE_PORT=80 SYNAPSE_LOG=dicomhawk.log .venv/bin/python src/web/app.py
```

Deployment values (host, port, log path) come from those environment variables, not from
`config.yaml`.

## Configuration (`<profile>/web/config.yaml`)

Operator-tunable values live in the profile's `config.yaml` — edit and restart, no code
changes. Partial files are fine: anything omitted falls back to the in-code `DEFAULTS`
(which also document the schema), and a missing file runs entirely on defaults.

| Section | Keys | Controls |
|---|---|---|
| `behavior` | `grant_access` | log + deny (`false`) vs grant into the decoy worklist (`true`) |
| `headers` | any header name → value | static response headers; add/rename freely. `X-Backendserver` is site-specific — set per deploy |
| `html_cache_headers` | `Cache-Control`, `Pragma` | cache headers for HTML pages (static assets stay cacheable) |
| `content_security_policy` | — | the CSP string (also emitted as legacy `X-Content-Security-Policy`); keep the `{nonce}` placeholder |
| `identity` | `version`, `copyright` | version stamp + footer copyright |
| `license` | `issued`, `lines` | the "Licensed to:" block; keep coherent with the seeded institution |
| `oidc` | `client_id`, `client_name`, `redirect_path`, `scopes` | the Windows-auth link and modelJson client name |

Structural values stay in code/templates on purpose (changing them breaks the disguise):
cookie names, route paths, page titles, the vendor's real error/success strings, and the
form markup.

## Logging

Captured credentials and web events are written to the shared interaction log
(`dicomhawk.log`) with `channel: WEB`, in the same schema as the DIMSE lines — one log,
not a separate file. The engine logs through the shared `bus` logger, so a profile needs
no logging code of its own.

## Routes

| Route | Behavior |
|---|---|
| `GET /`, `GET /Synapse` | 302 → `/SynapseSignOn/sts/login?signin=<hex>` |
| `GET /SynapseSignOn/sts/login` | the sign-on page |
| `POST /SynapseSignOn/sts/login` | captures username/password, then denies (default) or grants → worklist |
| `GET /Synapse` (authed) | the post-login worklist |
| `GET /SynapseSignOn/WinAuth/Login.aspx` | Windows auth: 401 `WWW-Authenticate` → native Sign in dialog; submitted creds captured. The 401 body is the "Unable to log in" page shown on Cancel |
| `GET/POST /ssomgr/password/forgotpassword` | captures the probed username, always returns the generic "reset email sent" message (anti-enumeration) |
| `GET /SynapseSignOn/sts/error` | the branded `Error` / `Request Id:` page (also the 500 handler, so errors never leak a stack trace) |

`grant_access` (config) is the deny model: `false` logs every credential and rejects with
the real error banner; `true` lets the client into the decoy worklist to observe deeper
behavior.

### Windows auth: Basic vs NTLM/Negotiate

The Windows-auth route challenges with HTTP **Basic**, which produces the browser's native
Sign in dialog and returns credentials in **plaintext**. Real IIS Windows Auth typically
challenges with `Negotiate`/`NTLM` (same dialog, but credentials arrive as an NTLM hash via
a multi-step handshake). Basic is used here for the plaintext capture; matching the exact
`Negotiate`/`NTLM` header is a possible future upgrade on this route.

## Fingerprint headers & cookies

Every response carries `Server`, `X-Powered-By`, `X-AspNet-Version`, `X-Frame-Options`,
`X-Content-Type-Options`, `X-Backendserver`, and a nonce-based CSP (under both
`Content-Security-Policy` and legacy `X-Content-Security-Policy`). The per-request nonce is
stamped on every inline `<script>` so the policy doesn't break the page;
`/SynapseSignOn/sts/csp/report` absorbs CSP violation reports. HTML pages also get the
no-store cache headers; static assets stay cacheable.

The sign-on drops the cookies a real deployment sets — `idsrv.xsrf` (JS-readable
double-submit token, matching the hidden form field), `SignInMessage.<token>` and
`OpenIdConnect.nonce.<b64>` (HttpOnly blobs), `IdpCookie`/`IdpTokenCookie` (cleared with
epoch-expiry on the login GET), and `WinLogin.OrigRequestUrlCookie` on the Windows-auth
challenge. All `Secure; SameSite=None`; auth cookies are set only on sign-on responses,
not on static assets.

## Templates & assets

The templates are sanitized reproductions of real captured pages: browser-extension
scripts removed, the original site's host and customer data replaced with config-driven
values, and per-request tokens (`idsrv.xsrf`, `requestId`, OIDC `nonce`/`state`)
regenerated. The real CSS/JS is served verbatim so the markup, cookie names, and version
stamp stay faithful.

Web fonts were not part of the page captures and are vendored under `static/fonts/`:
Font Awesome 4.7.0 (`fontawesome-webfont.*`, SIL OFL 1.1) for the password-visibility icon,
and Open Sans 400/600 + italics (`@fontsource/open-sans`, Apache 2.0) for the body font.
The favicon at `static/synapse/favicon.ico` must match the target product's icon (its hash
is a discovery fingerprint).
