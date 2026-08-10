# Browser fingerprinting

When an attacker opens the honeypot's web surface in a real browser, DICOMHawk can serve a
small JavaScript collector that reports what that browser looks like. The point is not to
identify people. It is to tell automated scanners apart from hands-on operators, and to
group visits that come from the same environment even when the IP address changes.

Fingerprinting is **off unless the active profile asks for it**, and it only ever applies to
the web surface. DICOM traffic on port 104 is never fingerprinted this way.

## Turning it on

A profile opts in with one key:

```yaml
web:
  fingerprint:
    enabled: true
```

That is the whole contract: a new profile needs no code and no extra files. The collector
ships with DICOMHawk, so every profile that opts in serves the same one.

To restrict which categories run, list them:

```yaml
web:
  fingerprint:
    enabled: true
    signals: [browser, screen, bot]
```

Omitting `signals` runs all five categories. Setting it to an empty list turns collection
off, exactly as if `enabled` were false. DICOMHawk will not serve a collector that collects
nothing. An unrecognised category name is rejected when the profile loads.

Both shipped profiles have it enabled. `fujifilm` also renames the two URLs so they sit
inside the paths its emulated product already uses; `generic-pacs` takes the neutral
defaults. If you write your own profile and care about realism, override them the same way:

```yaml
web:
  routes:
    fingerprint_script: /assets/js/client-capabilities.min.js
    fingerprint_ingest: /portal/clientinfo
```

## Signal categories

| Category | What it reports |
|---|---|
| `browser` | platform, languages, timezone, CPU cores, device memory, vendor, user agent |
| `rendering` | canvas and WebGL output, including the GPU vendor/renderer strings |
| `math` | results of transcendental functions, which differ between JavaScript engines |
| `screen` | screen resolution, colour depth, pixel ratio, window dimensions |
| `bot` | automation markers: `navigator.webdriver`, driver artefacts, headless indicators |

The collector does not request direct identifiers, read cookies, access accounts, or use
cross-site state, and it sends no data outside the honeypot. Browser characteristics can still
correlate visits and may constitute personal data under applicable privacy law; set retention
and access controls accordingly.

## What the operator sees

Fingerprints are available on the loopback operator API:

```
GET /api/fingerprints
GET /api/fingerprints?verdict=HeadlessChrome
GET /api/fingerprints?hash=<fingerprint hash>
GET /api/fingerprints?ip=203.0.113.9
GET /api/fingerprints?session_id=web-...
```

Each record holds the raw signals, the derived hash, and every automation check that fired.
The checks are evaluated on the server, so the client cannot submit a verdict field directly,
and the underlying signals are retained. All inputs still come from the visitor and can be
fabricated; treat the verdict as an indicator, not proof of a particular browser or bot.

`hash` is the useful pivot: two visits sharing a hash came from the same browser
environment, which is how repeat visits are recognised across addresses. Web events in the
interaction log carry the same `fingerprint_hash`, so a fingerprint can be tied back to the
requests that session made. Events logged when fingerprinting is off carry `null` there.

Correlating web activity with DICOM activity is done by address and time. The two protocols
share no session identifier.

## Behaviour and limits

- Collection never affects what a visitor sees. If the store is unavailable, full, or
  failing, the response is byte-for-byte the same.
- A submission larger than `--fingerprint-max-bytes` (64 KiB by default) is discarded.
- At most `--fingerprint-max-per-session` submissions (20 by default) are kept for one web
  session. A separate, looser `--fingerprint-max-per-ip` limit (500 by default) bounds one
  source address across rotating sessions without permanently cutting off an active visitor
  after 20 submissions.
- Only known signal names are stored, strings are truncated, and nested structures are
  bounded, because the whole submission is attacker-controlled input.
- A visitor that blocks JavaScript simply produces no fingerprint. Scanners that never
  execute scripts are still recorded in the interaction log as ordinary web requests.

## Storage and removal

Fingerprints live in their own SQLite database, set with `--fingerprint-db`
(`DICOMHAWK_FINGERPRINT_DB`). It is separate from the DICOM database and from the payload
analysis database, so the feature can be removed without disturbing either.

To stop collecting, start DICOMHawk with `--no-fingerprint`, or set `enabled: false` in the
profile. No collector is served and no endpoint is registered, so a request to the collector
path returns the same not-found page as any other unknown path. To discard the data as well,
delete the fingerprint database file.
