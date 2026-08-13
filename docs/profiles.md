# Adding a profile

A profile is a YAML file that tells DICOMHawk what device to impersonate: its DICOM
identity (AE title, implementation UID, supported SOP classes) and, optionally, a web
login/worklist surface. Two are bundled: `fujifilm` (high-fidelity Fujifilm Synapse
PACS) and `generic-pacs` (vendor-neutral, minimal). This guide covers building your own.

## Before you write any YAML: research

A profile is only as convincing as the real device it claims to be. Before adding a
new profile:

1. **Get the vendor's DICOM conformance statement.** Most PACS vendors publish one as a
   public PDF. It gives you the AE title, Implementation Class UID and Version Name,
   the supported Storage/Query-Retrieve SOP classes, and the transfer syntaxes for
   each. These are all fingerprint-locked values (see [the schema](#dicom-identity) below).
2. **Never invent an Implementation Class UID.** A real vendor UID with a placeholder
   or default `pynetdicom` version name is an instant giveaway. Either source both
   from the conformance statement or leave both unset.
3. **For the web surface, use passive reconnaissance only.** Shodan and Censys cached
   banners, public screenshots, Wayback Machine archives. Don't actively probe a real
   deployment. Mark anything you couldn't verify with `# inferred` in the YAML so the
   next person maintaining the profile knows which values are guesses.
4. **If you can't verify something, don't fabricate it.** A generic, honest fallback
   (see [`default_profile()`'s values](#what-you-can-leave-out)) is safer than an
   invented detail that doesn't match the real product. An attacker who has used the
   real device will notice a wrong header or a login form field name that's off.

If you're not mimicking one specific real product, base your profile on
`src/profiles/generic-pacs/` instead, which is built for exactly that case.

## File layout

```
src/profiles/<name>/
├── <name>.yaml              # required
└── web/                     # only if your profile has kind: pacs
    ├── templates/           # required if web.enabled: true, see below
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
  implementation_class_uid: 1.2.3.4.5.6   # from the conformance statement, real or omit
  implementation_version_name: "MyApp 3.2.1"
  manufacturer: My Vendor Inc.
  model_name: MyPACS
dicom:
  operations: [echo, find, get, move, store]
  max_associations: 16
  max_pdu_size: 16384
  acse_timeout: 10     # seconds; null -> pynetdicom's own default (30)
  network_timeout: 15  # seconds; null -> pynetdicom's own default (60)
  dimse_timeout: 20    # seconds; null -> pynetdicom's own default (30)
  max_store_bytes: 67108864   # per-instance C-STORE cap; null disables it
  storage_classes:
    - uid: 1.2.840.10008.5.1.4.1.1.2   # CT Image Storage
      transfer_syntaxes: [1.2.840.10008.1.2, 1.2.840.10008.1.2.1]
  qr_classes:
    find: [...]
    move: [...]
    get: [...]
```

## What you can leave out

Any key you omit falls back to a generic, working default: a plain `ORTHANC` AE title, a broad
storage and Query/Retrieve class set, Apache-style web headers, empty license, identity, and
OIDC blocks, and **generic `/portal/*` routes with `portal.xsrf`-style cookie names**. A
fallback never borrows another profile's identity.

Timeouts fall back to `acse_timeout: 10`, `network_timeout: 15`, and `dimse_timeout: 20`,
deliberately tighter than pynetdicom's own 30s/60s/30s. A raw TCP connection that never sends a
valid PDU, or a peer that stalls mid-operation, still holds one of the `max_associations` slots
until these expire.

A `WARNING` at startup lists exactly which keys fell back, so nothing is silently wrong.

**The only field with no fallback** is `web.templates_dir` when `web.enabled: true`. There is no
generic template directory to serve, so a profile missing it fails at load time with a clear
error instead of crashing on the first request.

This is genuinely how `generic-pacs` is built: its YAML sets almost nothing beyond
`kind`, `web.enabled`, and `web.templates_dir`. Start from an empty profile, run it, and
add YAML keys only for the things your specific vendor actually needs to differ.

## The web surface

If `kind: pacs` and `web.enabled: true`, `dicomhawk serve` starts two Flask apps for
your profile automatically. You write templates and config, not routes:

- **Attacker-facing** (`--web-port`, default 8080): your profile's login and worklist.
- **Operator surface** (`--operator-port`, default 8081, loopback-only): the dashboard at `/`
  and its read-only API. `/api/overview` feeds the dashboard in one request; `/api/stats` is the
  activity summary; `/api/attackers` rolls up sources and tactics; `/api/credentials` deduplicates
  captured username/password pairs; `/api/uploads` reports terminal WEB/STOW/C-STORE payload
  outcomes; `/api/events`, `/api/sessions`, and `/api/profiles` expose the underlying views.

The API reads the active interaction log and retained backups, tolerates malformed JSONL records,
and streams aggregation without retaining every parsed event. List endpoints accept `?limit=` and
`?offset=`, report `X-Total-Count`, and cap offsets at 10,000. Attacker, credential, and session
rollups are limited to 10,000 keys; truncation is reported through
`X-Aggregation-Truncated` and `/api/overview`'s `truncated` object. Events also accept exact
`?channel=`, `?ip=`, `?type=`, and ISO-8601 `?since=` filters.

The credential view contains the submitted plaintext because it is intended for incident review.
Keep this surface on loopback or behind authenticated administrative access. Responses are
non-cacheable and use a restrictive operator CSP.

Keep the default loopback bind on bare metal. Non-loopback binds fail closed unless
`--allow-remote-operator` is supplied. Set `--operator-token`/`DICOMHAWK_OPERATOR_TOKEN` whenever
anything beyond the local host can reach the listener; it accepts Basic auth (any username, token as
password) or a Bearer token. Docker needs a container-internal `0.0.0.0` bind, but the supplied
Compose file explicitly opts in and maps it only to host `127.0.0.1`.

Both surfaces use the shared engine; a profile supplies configuration, templates, and assets.
The worklist and DIMSE services read the same DICOM database, so seeded studies and their
attributes remain consistent between the web interface and C-FIND responses.

### Required templates

All five must exist under `web/templates/`; the profile fails at startup if one is missing:

| Template | Rendered for | Key context variables |
|---|---|---|
| `login.html` | GET/POST sign-on | `login_url`, `error_message`, `honey_hint`, `copyright` |
| `forgot_password.html` | Forgot-password flow | `submitted` (bool) |
| `error.html` | STS error page, 500 handler | `request_id`, `error_message` |
| `winauth_unable.html` | Windows-auth 401 body | `sts_authorize_url`. Use it, not a hardcoded path, for the "Log in directly" link |
| `worklist.html` | Post-login study list | `studies` (row dicts, keys below), `worklist` (shell config), `sidebar_counts`, `username`, `total_studies`, `folder`, `detail`, `filters`, `action_message`, `refresh_url` |

If you use the `unauthorized_page` honeytrap response (below), also add
`web/templates/unauthorized.html`; it receives `entry_url`, which you should use for any
"redirecting you back to login" link/script, never a hardcoded path.

If `web.browse: true`, three more templates are required and validated at startup:

| Template | Rendered for | Key context variables |
|---|---|---|
| `console.html` | Browse landing | `routes` |
| `browse.html` | Patient/study/series/instance pages | `columns`, `rows`, `page`, `prev_url`, `next_url` |
| `upload.html` | GET/POST upload | `routes`, `message`, `max_files` |

Every template also receives `csp_nonce` (put it on any inline `<script>` tag, since the CSP
is nonce-based) and `fingerprint_seam` (put `{{ fingerprint_seam|safe }}` before
`</head>`; it renders the browser fingerprint collector when the profile enables
`web.fingerprint`, and is empty otherwise. See
[Browser fingerprinting](./fingerprinting.md)).

### `web:` config reference

```yaml
web:
  enabled: true
  templates_dir: my-vendor
  grant_access: keyword      # none | bait | keyword | any (see Who gets in below)
  favicon: myvendor/favicon.ico
  headers:                   # emitted on every response
    Server: MyWebServer/1.0
  html_cache_headers: {...}  # HTML responses only; static assets stay cacheable
  content_security_policy: "default-src 'self'; script-src 'nonce-{nonce}' 'self'"
  legacy_csp_header: false  # emit X-Content-Security-Policy only when the target does
  secure_cookies: true      # for targets deployed behind HTTPS
  max_request_bytes: 1048576         # non-upload web request cap
  upload_max_request_bytes: 52428800 # browse upload route only
  upload_max_files: 10
  browse_page_size: 100              # 1-500; bounded in the database
  worklist_page_size: 100            # 1-500; studies listed on the worklist page
  identity: {version: "1.0", copyright: "..."}
  license: {issued: "...", lines: [...]}
  oidc: {client_id: "...", client_name: "...", redirect_path: "...", scopes: "..."}
```

Dict fields (`headers`, `oidc`, etc.) overlay per-key onto the generic default, so you
can override just one header and keep the rest.

### Routes and cookies: profile isolation

This is a hard project invariant, not just a suggestion: no profile may ever leak into
another, even though every profile shares the same engine code.

**Every URL path and cookie name is per-profile data, never a fixed engine value.**
If you don't set `web.routes`/`web.cookies`, your profile gets generic, non-branded
defaults (`/portal`, `/portal/login`, a `portal.xsrf` cookie, and so on), never another
profile's paths or cookie names, even though they share the same engine code. This is
what stops a `my-vendor` page from ever showing `/SynapseSignOn/...` in the address
bar or an `idsrv.xsrf` cookie just because the code happens to be shared.

Only override these if you're mimicking a *real* product's actual observed paths and
cookie names (research these the same way as the DICOM identity: passive recon, mark
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
`forgot_url`, `sts_authorize_url`, `entry_url`, listed in the templates table above) instead
of hardcoding a path. A hardcoded `href="/Synapse"` or `href="/SynapseSignOn/..."` in your
own profile's template defeats the point of `web.routes` and `web.cookies`, and silently breaks
if the operator overrides that route, and on a shared profile it becomes a literal
cross-profile leak (another vendor's branded path showing up on your page).

### Honeytraps

Bait paths are declared as data, not code. The engine registers routes only for what
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

A profile that declares no `honeytraps` gets none, and will not inherit another profile's
bait paths just because they share the same engine. `/robots.txt` publishes the same
list as `Disallow:` entries. `unauthorized_page` needs your own `web/templates/
unauthorized.html`; the other two kinds don't need a template.

**Coherence matters here as much as with DICOM identity.** If you're mimicking a real
vendor, only add a bait path if you've actually seen evidence it exists on the real
product (a public admin panel path, an API route mentioned in vendor docs). An
invented path on a high-fidelity profile is as much a tell as a wrong AE title. If
you're not mimicking a specific real vendor (like `generic-pacs`), a plausible
fabricated bait path is fine.

### Honey credentials

A credential pair that isn't a real account. Using it logs a distinct
`WEB_HONEY_CREDENTIAL_USED` event, so any use of it is a high-confidence signal rather
than a guess:

```yaml
  honey_credentials:
    - username: test
      password: test
```

`login.html`'s `honey_hint` context variable renders to `"test / test"` when this is
set (empty otherwise). Put it somewhere discoverable, like an HTML comment near the
login form, so an attacker can actually find it.

**Only render the hint if your login page isn't a verbatim capture of a real product's
page.** If it is, an invented hint comment is itself a diff-able tell against the real
page. The two mechanisms are independent: `fujifilm.yaml` does set honey credentials, but
its captured sign-on template never renders `honey_hint`, so the page stays byte-faithful
and the credentials are disclosed out of band instead (a decoy config file, a leaked
note). Render the hint only on a profile where there's no real page to stay faithful to.

### Who gets in

`grant_access` is the single gate in front of every login route, the sign-on form and the
WinAuth challenge alike:

| Value | Effect |
|---|---|
| `none` | Every attempt is logged and denied, including a declared honey credential. The post-login pages are unreachable; the surface is a pure credential collector. |
| `bait` | Only the pairs in `honey_credentials` get in. Anything else gets the product's real error. |
| `keyword` | The bait pairs, plus an attempt whose username or password contains one of `honey_keywords`, except that a declared bait username still requires its declared password. Both shipped profiles use this. |
| `any` | Every password works. Quicker to engage, but it tells an attacker it is a decoy the first time a deliberately wrong password succeeds. |

It is a string, not a boolean; a `true`/`false` value is rejected at load time with the
replacement named in the error.

### Keyword bait

`bait` only admits credentials you predicted exactly. `keyword` widens that to the terms
attackers actually spray, so more of them reach the pages behind the login instead of stopping
at a rejection:

```yaml
web:
  grant_access: keyword
  honey_keywords: [admin, pacs, dicom, radiology, imaging, service]
```

Matching is a case-insensitive substring against the username **or** the password, so
`admin` admits `admin`, `Administrator`, and `svc-admin`, and a password of `MyPacsPass`
admits any username. Each keyword must be at least three characters, because a shorter one
matches nearly every input and quietly turns the profile into `any`. Declaring
`grant_access: keyword` with no keywords is rejected at load rather than silently behaving
like `bait`. Keywords are casefolded and de-duplicated when the profile loads.

Declared bait usernames are exempt from keyword matching. This keeps the pair meaningful:
`svc_dicom` with a wrong password is denied even though its username contains `dicom`, while
the exact declared pair succeeds. Keyword matching still applies normally to every username
that is not declared in `honey_credentials`, and to those attempts' passwords.

A grant through a keyword is logged as `WEB_HONEY_KEYWORD_USED`, recording which keyword
matched and which field it hit, so the log tells you what is being sprayed at the surface. An
exact `honey_credentials` pair keeps its own `WEB_HONEY_CREDENTIAL_USED` event.

Keyword mode is more detectable than `bait`. An attacker who gets in with `admin` and a
nonsense password, then fails with a credential containing no keyword, can infer the rule and
narrow the list with a few attempts. Use it when engagement matters more than that, and `bait`
when it does not. The loopback-only operator profile API returns the configured honey credentials
and keyword list in full so the operator can verify the active deception configuration.

**A session cookie the browser refuses makes every level above `none` useless.** A profile
modelling an HTTPS product sets `secure_cookies: true`, and browsers discard `Secure`
cookies received over plain HTTP, so the login grants a session the browser immediately
throws away, showing neither a worklist nor an error. Set `DICOMHAWK_SECURE_COOKIES=false`
for a plaintext deployment, or terminate TLS in front and set `--trusted-proxy`. The server
logs a warning at startup when the combination cannot work.

### Worklist page

Every profile serves a post-login worklist at `web.routes.worklist`, listing the same
seeded studies the DICOM side serves. `web.worklist` supplies every visible string:
the engine ships none of them, so one profile's folder names can never appear on
another's page.

```yaml
web:
  worklist_page_size: 100
  worklist:
    title: All Studies
    header_links:
      - {label: Messages, icon: comment}
      - {label: Help, icon: question-sign}
    toolbar:
      - {label: Study Information, icon: file, result: detail}
      - {label: Open Viewer, icon: camera, result: error}
    columns:
      - {key: patient_name, label: Patient Name}
      - {key: description,  label: Proc Description}
      - {key: modality,     label: Modality}
      - {key: images,       label: Images}
    sidebar:
      - label: Worklists
        open: true
        items:
          - {label: All Studies, dynamic_count: studies}
          - {label: CT, filter: {modality: CT}}   # folders may narrow by modality
    context_menu:
      - {label: Open Viewer, result: error}
      - label: Change Priority
        result: submenu
        items:
          - {label: STAT, result: error}
      - {label: Unreserve, result: disabled}
    footer:   {item_label: items, refresh_label: Last refreshed}
    messages: {action_failed: The imaging service did not respond.}
    placeholders: {description: UNKNOWN, status: "", empty: ""}
```

`columns[].key` must be one of: `patient_name`, `patient_id`, `patient_sex`,
`patient_birth_date`, `description`, `modality`, `images`, `body_part`,
`series_description`, `institution_name`, `station_name`, `referring_physician`,
`study_date`, `study_time`, `study_datetime`, `accession_number`, `study_id`,
`study_instance_uid`, `status`, `age`. An unknown key fails at startup rather than
rendering a silently blank column.

An empty `sidebar`, `toolbar`, `context_menu`, or `header_links` renders no chrome at all, which is
the default, so a sparse profile gets a plain table, not somebody else's shell.

**Interaction.** The page is driven entirely by query parameters on its own route, so
there is no extra endpoint to fingerprint:

| Parameter | Effect |
|---|---|
| `?path=<folder>` | Selects a sidebar folder and applies its `filter` |
| `?study=<uid>` | Opens the detail panel for a study **already listed on that page** |
| `?action=<label>` | Renders `messages.action_failed` for a `result: error` entry |
| `?filter_<column>=<text>` | Case-insensitively narrows the listed rows; logged and echoed back into its own input |

Values are resolved against the profile's own labels, never trusted directly: an
unrecognised folder or action falls back to the default view and is not echoed into the
page. A `?study=` UID that isn't on the current page opens nothing, so the parameter
can't be used to probe for studies. Each view logs a `WEB_WORKLIST_VIEW` event naming the
folder, action, study, and filter terms the attacker reached for.

**Placeholders.** `placeholders.description` fills the procedure column when a study
carries no `StudyDescription`. Real products show their own marker there (Synapse shows
`UNKNOWN`), so match whatever the product you're mimicking does. Seeded studies get a
generated description, so this placeholder should only appear for studies an attacker
uploaded or for studies indexed before seeding started writing one.

**Sidebar state and counts.** `sidebar[].open: true` expands a section on first load; the
browser remembers later toggles for that profile. `count` and `urgent_count` are static
decoration, so use them only when they remain plausible. `dynamic_count: studies` renders the
current repository-wide study total and cannot be combined with a folder `filter`, because a
global badge beside a narrowed list would contradict the page. Fujifilm uses the dynamic count
and deliberately ships no copied site-specific static counts.

Actions may return `detail`, `error`, `submenu`, or `disabled`. Submenus can nest; only a
selectable leaf becomes an `?action=` value. Header and toolbar entries also require an `icon`
class so a profile cannot silently render blank controls.

**Detail columns need a re-seed.** Sex, date of birth, body part, institution, station,
and referring physician are not part of the DICOM Query/Retrieve index; they are recorded
in a separate per-study table as objects are stored, and answered from there on both the
worklist and in C-FIND responses. Studies indexed before this table existed have no row,
so on an existing database those columns render blank on the page and come back empty over
DICOM. Re-seed, or start from a clean database, to populate them.

### Browse console

Setting `web.browse: true` adds a post-login DICOM browse console: a dashboard with a
patient/study/series/instance browser, a search-by-patient box, and an upload page. Every
page is session-gated (reachable only after a successful login, for example via a honey
credential), reads the same seeded studies the DICOM side serves, and logs each view.

```yaml
web:
  browse: true
  upload_max_request_bytes: 52428800
  upload_max_files: 10
  browse_page_size: 100
```

The upload page validates Part-10 identity, profile-supported SOP Class and Transfer
Syntax, then routes accepted files to the **quarantine**. Exact incoming bytes are kept
for valid and rejected/malformed files, logged with size and SHA-256 (`WEB_UPLOAD`), and
never served back out. Files above the count limit are explicitly rejected and captured.
The Waitress listener is sized for the larger upload ceiling so it does not reject the body
before routing. Flask then applies that larger cap only to the upload POST; login and other forms
retain the smaller `max_request_bytes` limit.

Browse and search use server-side database pagination rather than loading the whole index;
deep offsets are capped at 20,000 rows to prevent deliberately expensive scans.
Session cookies are opaque, server-issued values: inventing a nonempty cookie does not
grant access. The console's paths come from `web.routes`
(`console`/`patients`/`studies`/`series`/`instances`/`search`/`upload`/`logout`), so like
every other route they stay generic unless you override them, and a profile that leaves
`web.browse` off never exposes them at all. Enable it on a vendor-neutral profile; a
high-fidelity capture profile that mimics a specific product's sign-on page should leave
it off unless that product really has a matching browser.

## DICOMweb

Modern PACS often expose HTTP DICOMweb services (QIDO-RS query, WADO-RS/WADO-URI
retrieve, STOW-RS store) alongside DICOM. Add them **only if the product you're mimicking
actually does.** Serving DICOMweb on a device that would not is itself a tell. Check the
target's conformance statement for the real service ports and base paths.

The `dicomweb:` block is a top-level key (a sibling of `dicom:` and `web:`), off unless you
enable it:

```yaml
dicomweb:
  enabled: true
  services:
    - service: qido        # qido | wado_rs | stow | wado_uri
      base_path: /qido-rs
      port: 10080
    - service: wado_rs
      base_path: /wado-rs
      port: 12080
    - service: stow
      base_path: /stow-rs
      port: 13080
  require_auth: [stow]      # service kinds that issue the configured challenges
  auth_schemes: [Negotiate, NTLM, Basic]
  qido_default_media_type: application/json
  qido_max_results: 20000   # multi-patient cap; a single patient is not truncated
  default_transfer_syntax: 1.2.840.10008.1.2.1
  max_request_bytes: 67108864        # complete STOW request cap
  max_non_stow_request_bytes: 1048576
  max_stow_parts: 128
```

**Port and path are profile data, the same isolation rule as `web.routes`.** Each service
runs on its own port with its own base path, exactly as the real product does. Some
vendors dedicate a port per service, others put everything under one base like
`/dicom-web`. If you omit `services`, an enabled profile inherits a generic single-port
`/dicom-web` layout, never another profile's ports or paths.

DICOMweb shares the same store and query as DICOM, so:

- **STOW uploads are quarantined** exactly like a C-STORE. The exact multipart request and each
  exact Part-10 item are retained as uniquely named gzip traces, including malformed/rejected
  items. Uploaded objects are **never served back** by WADO. Request size and the Waitress
  thread pool bound resource use; STOW does not expose a separate concurrency rejection oracle.
- **QIDO** returns metadata built from the same repository as DIMSE. Its default media type is
  profile-specific (`application/json` for Fujifilm; `application/dicom+json` for generic PACS),
  and clients may request DICOM JSON or multipart DICOM XML.
- **WADO** defaults DICOM retrieval to the configured transfer syntax and negotiates raw
  single-instance, multipart, JSON metadata, and XML metadata representations. WADO-URI JPEG
  rendering rejects a requested dimension above 8,192 pixels or an output above 16,777,216
  total pixels before asking Pillow to allocate the image.
- Every request is logged to the same interaction log with `channel: DICOMWEB`.
- Responses reuse your `web.headers` identity (the `Server` banner, etc.) so the DICOMweb
  ports present the same product as the web tier.

## Running and testing your profile

```bash
dicomhawk serve --profile my-vendor
curl -I http://localhost:8080/portal        # or your web.routes.entry: check headers and redirect
curl http://localhost:8080/robots.txt       # check honeytrap Disallow entries
curl http://localhost:8081/api/profiles     # confirm the loaded config, loopback-only
curl http://localhost:8081/api/stats        # activity summary across all channels
curl http://localhost:8081/api/attackers    # per-source-IP rollup with a threat label
curl http://localhost:8081/api/credentials  # captured username/password pairs, in full
curl 'http://localhost:8081/api/events?channel=WEB&limit=50&offset=0'
# With DICOMHAWK_OPERATOR_TOKEN set:
curl -H 'Authorization: Bearer YOUR_TOKEN' http://localhost:8081/api/credentials
curl http://localhost:10080/qido-rs/studies # Fujifilm QIDO-RS default -> application/json
```

See [`tests/test_profile.py`](../tests/test_profile.py) and
[`tests/test_web.py`](../tests/test_web.py) for the kind of test coverage expected:
profile-loader fallback behavior and Flask `test_client` checks against your new
templates/routes.
