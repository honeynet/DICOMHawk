# Adding a profile

A profile is a YAML file that tells DICOMHawk what device to impersonate: its DICOM
identity (AE title, implementation UID, supported SOP classes) and, optionally, a web
login/worklist surface. Two are bundled — `fujifilm` (high-fidelity Fujifilm Synapse
PACS) and `generic-pacs` (vendor-neutral, minimal). This guide covers building your own.

## Before you write any YAML: research

A profile is only as convincing as the real device it claims to be. Before adding a
new profile:

1. **Get the vendor's DICOM conformance statement.** Most PACS vendors publish one as a
   public PDF. It gives you the AE title, Implementation Class UID and Version Name,
   the supported Storage/Query-Retrieve SOP classes, and the transfer syntaxes for
   each — all fingerprint-locked values (see [the schema](#dicom-identity) below).
2. **Never invent an Implementation Class UID.** A real vendor UID with a placeholder
   or default `pynetdicom` version name is an instant giveaway — either source both
   from the conformance statement or leave both unset.
3. **For the web surface, use passive reconnaissance only** — Shodan/Censys cached
   banners, public screenshots, Wayback Machine archives. Don't actively probe a real
   deployment. Mark anything you couldn't verify with `# inferred` in the YAML so the
   next person maintaining the profile knows which values are guesses.
4. **If you can't verify something, don't fabricate it.** A generic, honest fallback
   (see [`default_profile()`'s values](#what-you-can-leave-out)) is safer than an
   invented detail that doesn't match the real product — an attacker who's used the
   real device will notice a wrong header or a login form field name that's off.

If you're not mimicking one specific real product, base your profile on
`src/profiles/generic-pacs/` instead — it's built for exactly that case.

## File layout

```
src/profiles/<name>/
├── <name>.yaml              # required
└── web/                     # only if your profile has kind: pacs
    ├── templates/           # required if web.enabled: true — see below
    │   ├── login.html
    │   ├── forgot_password.html
    │   ├── error.html
    │   ├── winauth_unable.html
    │   └── worklist.html
    └── static/               # your CSS/JS/images/favicon
```

For a bundled profile, `<name>` normally matches `web.templates_dir` in the YAML and is how you select
the profile: `dicomhawk serve --profile <name>`. `--profile` also accepts a filesystem
path to a YAML file outside this layout. For an external profile, place `web/` beside
the YAML (or `<templates_dir>/web/` below it); the loader resolves and validates those
assets instead of silently serving a bundled profile with the same name.

## DICOM identity

```yaml
meta:
  name: my-vendor
  kind: pacs          # or "dicom" for a DICOM-only profile with no web surface
identity:
  ae_title: MYVENDORSCP
  implementation_class_uid: 1.2.3.4.5.6   # from the conformance statement — real or omit
  implementation_version_name: "MyApp 3.2.1"
  manufacturer: My Vendor Inc.
  model_name: MyPACS
dicom:
  operations: [echo, find, get, move, store]
  max_associations: 16
  max_pdu_size: 16384
  acse_timeout: 10     # seconds; null -> pynetdicom's own default (30)
  network_timeout: 15  # seconds; null -> pynetdicom's own default (60)
  max_store_bytes: 536870912  # per-instance C-STORE cap; null disables it
  storage_classes:
    - uid: 1.2.840.10008.5.1.4.1.1.2   # CT Image Storage
      transfer_syntaxes: [1.2.840.10008.1.2, 1.2.840.10008.1.2.1]
  qr_classes:
    find: [...]
    move: [...]
    get: [...]
```

## What you can leave out

Any key you omit falls back to a generic, working default (from `default_profile()`) —
plain `ORTHANC` AE title, a broad storage/QR class set, generic Apache-style web
headers, empty license/identity/oidc, **generic `/portal/*` routes with
`portal.xsrf`-style cookie names** — never another profile's identity — and
`acse_timeout: 10` / `network_timeout: 15`, tighter than pynetdicom's own 30s/60s
defaults (a raw TCP connection that never sends a valid PDU still occupies one of
`max_associations` slots until these expire, so the fallback is deliberately tight
rather than just carried over from the library). A `WARNING` at
startup lists exactly which keys fell back, so nothing is silently wrong. **The only
field with no fallback** is `web.templates_dir` when `web.enabled: true` — there's no
generic template directory to serve, so a profile missing it fails fast at load time
with a clear error instead of crashing on the first request.

This is genuinely how `generic-pacs` is built: its YAML sets almost nothing beyond
`kind`, `web.enabled`, and `web.templates_dir`. Start from an empty profile, run it, and
add YAML keys only for the things your specific vendor actually needs to differ.

## The web surface

If `kind: pacs` and `web.enabled: true`, `dicomhawk serve` starts two Flask apps for
your profile automatically — you write templates and config, not routes:

- **Attacker-facing** (`--web-port`, default 8080) — your profile's login/worklist.
- **Operator API** (`--operator-port`, default 8081, loopback-only) — `/api/sessions`,
  `/api/events`, `/api/profiles`, read-only, for whoever operates the honeypot.

Both are built by the shared engine (`src/web/app.py`/`operator_api.py`/`component.py`)
— you don't touch that code. It reuses the same DICOM database your profile's DIMSE
side sees, so a study seeded via `dicomhawk seed` shows up in both the worklist and a
C-FIND response.

### Required templates

All five must exist under `web/templates/`; the profile fails at startup if one is missing:

| Template | Rendered for | Key context variables |
|---|---|---|
| `login.html` | GET/POST sign-on | `login_url`, `error_message`, `honey_hint`, `copyright` |
| `forgot_password.html` | Forgot-password flow | `submitted` (bool) |
| `error.html` | STS error page, 500 handler | `request_id`, `error_message` |
| `winauth_unable.html` | Windows-auth 401 body | `sts_authorize_url` — use it, not a hardcoded path, for the "Log in directly" link |
| `worklist.html` | Post-login study list | `studies` — list of dicts with `patient_name`, `description`, `study_datetime`, `modality`, `status`, `age` |

If you use the `unauthorized_page` honeytrap response (below), also add
`web/templates/unauthorized.html`; it receives `entry_url` — use it for any
"redirecting you back to login" link/script, never a hardcoded path.

Every template also receives `csp_nonce` (put it on any inline `<script>` tag — the CSP
is nonce-based) and `fingerprint_seam` (put `{{ fingerprint_seam|safe }}` before
`</head>`; it's empty unless you set `web.fingerprint_script`, the seam a future browser
fingerprinting pass will use — nothing to build here yet).

### `web:` config reference

```yaml
web:
  enabled: true
  templates_dir: my-vendor
  grant_access: false        # false = every login attempt is logged and denied
  favicon: myvendor/favicon.ico
  headers:                   # emitted on every response
    Server: MyWebServer/1.0
  html_cache_headers: {...}  # HTML responses only; static assets stay cacheable
  content_security_policy: "default-src 'self'; script-src 'nonce-{nonce}' 'self'"
  legacy_csp_header: false  # emit X-Content-Security-Policy only when the target does
  secure_cookies: true      # for targets deployed behind HTTPS
  max_request_bytes: 1048576
  identity: {version: "1.0", copyright: "..."}
  license: {issued: "...", lines: [...]}
  oidc: {client_id: "...", client_name: "...", redirect_path: "...", scopes: "..."}
```

Dict fields (`headers`, `oidc`, etc.) overlay per-key onto the generic default, so you
can override just one header and keep the rest.

### Routes and cookies — profile isolation

This is a hard project invariant, not just a suggestion: no profile may ever leak into
another, even though every profile shares the same engine code.

**Every URL path and cookie name is per-profile data, never a fixed engine value.**
If you don't set `web.routes`/`web.cookies`, your profile gets generic, non-branded
defaults (`/portal`, `/portal/login`, a `portal.xsrf` cookie, etc.) — never another
profile's paths or cookie names, even though they share the same engine code. This is
what stops a `my-vendor` page from ever showing `/SynapseSignOn/...` in the address
bar or an `idsrv.xsrf` cookie just because the code happens to be shared.

Only override these if you're mimicking a *real* product's actual observed paths and
cookie names (research these the same way as the DICOM identity — passive recon, mark
guesses as inferred):

```yaml
web:
  routes:
    entry: /MyPortal
    worklist: /MyPortal/worklist
    login: /MyPortal/signin
    winauth: /MyPortal/winauth
    forgot_password: /MyPortal/forgot-password
    sts_error: /MyPortal/error
    sts_authorize: /MyPortal/authorize
    csp_report: /MyPortal/csp-report
    translated_items: /MyPortal/translations
  cookies:
    antiforgery: myportal.xsrf
    session: myportal_authed
    signin_message_prefix: "MyPortalSignIn."
    nonce_prefix: "MyPortalNonce."
    idp: MyPortalIdp
    idp_token: MyPortalIdpToken
    winlogin_origurl: MyPortalWinOrigUrl
  winauth_messages:
    text1: My Portal Log On
    text2: Unable to log in using Windows Authentication.
    text3: Log in directly
```

**In your templates, always use the passed-in URL variables** (`login_url`,
`forgot_url`, `sts_authorize_url`, `entry_url` — see the templates table above) instead
of hardcoding a path. A hardcoded `href="/Synapse"` or `href="/SynapseSignOn/..."` in your
own profile's template defeats the point of `web.routes`/`.cookies` — it silently breaks
if the operator overrides that route, and on a shared profile it becomes a literal
cross-profile leak (another vendor's branded path showing up on your page).

### Honeytraps

Bait paths are declared as data, not code — the engine registers routes only for what
your profile lists, and picks one of a small set of reusable response behaviors:

```yaml
  honeytraps:
    - path: /admin/
      response: unauthorized_page   # a static "401 - Unauthorized" bait page
    - path: /some-secondary-app/
      response: login_redirect      # bounces to your profile's own sign-on page
    - path: /api/SomeLegacyApi/
      response: api_404             # mimics a stock ASP.NET Web API "no matching action" 404
```

A profile that declares no `honeytraps` gets none — it won't inherit another profile's
bait paths just because they share the same engine. `/robots.txt` publishes the same
list as `Disallow:` entries. `unauthorized_page` needs your own `web/templates/
unauthorized.html`; the other two kinds don't need a template.

**Coherence matters here as much as with DICOM identity.** If you're mimicking a real
vendor, only add a bait path if you've actually seen evidence it exists on the real
product (a public admin panel path, an API route mentioned in vendor docs, etc.) — an
invented path on a high-fidelity profile is as much a tell as a wrong AE title. If
you're not mimicking a specific real vendor (like `generic-pacs`), a plausible
fabricated bait path is fine.

### Honey credentials

A credential pair that isn't a real account — using it always grants access (bypassing
`grant_access`) and logs a distinct `WEB_HONEY_CREDENTIAL_USED` event, so any use of it
is a high-confidence signal rather than a guess:

```yaml
  honey_credentials:
    - username: test
      password: test
```

`login.html`'s `honey_hint` context variable renders to `"test / test"` when this is
set (empty otherwise) — put it somewhere discoverable, like an HTML comment near the
login form, so an attacker can actually find it.

**Only do this if your login page isn't a verbatim capture of a real product's page.**
If it is, an invented hint comment is itself a diff-able tell against the real page —
`fujifilm.yaml` deliberately leaves this commented out for exactly that reason. Plant
it on a profile where there's no real page to stay faithful to, or find a different
disclosure channel (a decoy config file, a leaked note) instead of the login page.

## Running and testing your profile

```bash
dicomhawk serve --profile my-vendor
curl -I http://localhost:8080/portal        # or your web.routes.entry — check headers/redirect
curl http://localhost:8080/robots.txt       # check honeytrap Disallow entries
curl http://localhost:8081/api/profiles     # confirm the loaded config, loopback-only
```

See [`tests/test_profile.py`](../tests/test_profile.py) and
[`tests/test_web.py`](../tests/test_web.py) for the kind of test coverage expected —
profile-loader fallback behavior and Flask `test_client` checks against your new
templates/routes.
