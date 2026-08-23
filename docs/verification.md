# Verify the installation

Eleven checks that confirm a fresh install actually works. They take a few minutes and need
nothing beyond `curl` and the running Compose stack, apart from check 9 which needs a browser. Run
them once after the first install, and again after changing the profile or the ports.

Every sample below is real output from a working deployment. Values such as UIDs, hashes,
and timestamps will differ on yours.

| Check | Confirms |
|---|---|
| 1 | The service containers are running and the DICOM listener answers |
| 2 | The ports you chose are published to the host |
| 3 | Seeded studies are queryable over the wire |
| 4 | The web surface presents the profile's identity |
| 5 | The decoy login captures credentials |
| 6 | DICOMweb services answer |
| 7 | Attacker uploads are quarantined |
| 8 | The payload sandbox scans uploads and matches its rules |
| 9 | Browser fingerprinting records a real visit |
| 10 | The operator console shows the captured intelligence |
| 11 | Active and rotated event logs are valid JSONL |

Checks 4, 5, and 6 use the paths of the bundled `fujifilm` profile. If you run
`generic-pacs`, substitute `/portal` for `/Synapse` and `/dicom-web/` for the four
Synapse DICOMweb paths. A profile with `web.enabled` unset serves no web surface at all,
so skip those checks.

## 1. The service containers are running

```bash
docker compose ps
```

```
NAME                 SERVICE    STATUS
dicomhawk-dimse      dimse      Up 10 minutes (healthy)
dicomhawk-web        web        Up 10 minutes
dicomhawk-operator   operator   Up 10 minutes
dicomhawk-dicomweb   dicomweb   Up 10 minutes
dicomhawk-analysis   analysis   Up 10 minutes
```

`healthy` is stronger than `running`. The health probe is a real C-ECHO against the
honeypot's own DICOM port, so a healthy container has already proven the listener
answers DIMSE. The probe is excluded from the event log and never appears as attacker
traffic.

If the status stays `starting` for more than a minute, or flips to `unhealthy`, read
`docker compose logs`.

## 2. The ports reach the host

```bash
docker compose port dimse 104
docker compose port web 8080
docker compose port operator 8081
```

```
0.0.0.0:104
0.0.0.0:8080
127.0.0.1:8081
```

Use the ports from your own `.env` if you changed them. Two things to look for: the DICOM
and web ports are reachable from anywhere, and the operator port is bound to `127.0.0.1`
only. An operator API answering on `0.0.0.0` is a serious misconfiguration, since it
serves captured intelligence with no vendor disguise.

A port that is missing here fails silently. The honeypot listens inside the container and
nothing outside can reach it, with no error in any log.

## 3. Seeded studies are queryable

```bash
docker compose exec -T dimse python -m pynetdicom findscu 127.0.0.1 104 \
    -aec SYNAPSEDICOMSCP -S -k QueryRetrieveLevel=STUDY -k PatientName= >/dev/null 2>&1
docker compose logs --tail 5 dimse | grep C-FIND
```

```
12:04:11  DIMSE  127.0.0.1:41836  :104  C-FIND  STUDY  matches=4  -> SUCCESS (0x0000)
```

`matches=0` means the database is empty. Seed it:

```bash
docker compose run --rm --no-deps dimse dicomhawk seed -c TCGA-LUAD -s 2 -n 5 -m CT
```

```
Downloading up to 2 series x 5 images from 'TCGA-LUAD' (CT); this can take several minutes.
  series 1/2 (0 instances stored)
  series 2/2 (5 instances stored)
Seeded 6 instances from 'TCGA-LUAD' (CT)
```

An empty archive is itself a tell, so do not leave a honeypot exposed without data. If
TCIA is unreachable the command says so and seeds a bundled offline set instead.

## 4. The web surface presents the profile's identity

```bash
curl -sI http://localhost:8080/Synapse
```

```
HTTP/1.1 302 FOUND
Location: /SynapseSignOn/sts/login?signin=df2bb02fe63fdcecf671cc1818f19e90
Server: Microsoft-IIS/10.0
X-Powered-By: ASP.NET
X-Aspnet-Version: 4.0.30319
Content-Security-Policy: default-src 'self'; script-src 'nonce-A6DLuHG0qH69J-WGxpyOXQ' 'self'; ...
```

The entry path redirects to a sign-on page with a fresh flow token. Exactly one `Server`
header ships, carrying the profile's value rather than the real stack.

Open `http://<host>:8080/Synapse` in a browser as well. Headers are only half of the
deception; the rendered page is what an analyst actually looks at.

## 5. The decoy login captures credentials

Wrong credentials are refused and recorded:

```bash
curl -s -X POST 'http://localhost:8080/SynapseSignOn/sts/login?signin=x' \
    -d 'username=administrator&password=administrator' | grep -o 'incorrect'
```

```
incorrect
```

A credential from the profile's `honey_credentials` is accepted:

```bash
curl -si -X POST 'http://localhost:8080/SynapseSignOn/sts/login?signin=x' \
    -d 'username=svc_dicom&password=svc_dicom' | sed -n '1p;/^[Ll]ocation:/p'
```

```
HTTP/1.1 302 FOUND
Location: /WorkflowUI/?path=
```

Both shipped profiles also run `grant_access: keyword`, so a credential containing one of
their `honey_keywords` is accepted even though it was never declared as a pair:

```bash
curl -si -X POST 'http://localhost:8080/SynapseSignOn/sts/login?signin=x' \
    -d 'username=bob&password=MyPacsPass' | sed -n '1p;/^[Ll]ocation:/p'
```

```
HTTP/1.1 302 FOUND
Location: /WorkflowUI/?path=
```

Both attempts are written to the event log either way, which is the point of the surface.
A keyword match is logged as `WEB_HONEY_KEYWORD_USED` naming the term that matched, and a
declared pair as `WEB_HONEY_CREDENTIAL_USED`.

If the bait credential returns `302` but the worklist still bounces you back to the
sign-on page in a browser, the session cookie is marked `Secure` while the site is served
over plain HTTP. Set `DICOMHAWK_SECURE_COOKIES=false` in `.env` and recreate the
container, or terminate TLS in front of it. The server logs a warning at startup when
this combination is detected.

## 6. DICOMweb services answer

```bash
curl -s http://localhost:10080/qido-rs/studies | head -c 120; echo
```

```
[{"0020000D": {"vr": "UI", "Value": ["1.3.6.1.4.1.14519.5.2.1.7777.9002.26693003338259067064154827918
```

DICOM JSON, keyed by tag. An empty array means check 3 found nothing either.

The other three Synapse services are on their own ports: WADO-URI `9080`, WADO-RS `12080`,
STOW-RS `13080`. WADO-URI and STOW-RS answer an unauthenticated request with a Windows
authentication challenge, which is how they collect credentials.

## 7. Attacker uploads are quarantined

Send an object the honeypot has never seen:

```bash
docker compose exec -T dimse python3 -c "
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid
ds = Dataset(); ds.file_meta = FileMetaDataset()
ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
uid = generate_uid(); ds.file_meta.MediaStorageSOPInstanceUID = uid
ds.SOPClassUID = CTImageStorage; ds.SOPInstanceUID = uid
ds.StudyInstanceUID = generate_uid(); ds.SeriesInstanceUID = generate_uid()
ds.PatientName = 'Probe^Upload'; ds.PatientID = 'PROBE1'
ds.save_as('/tmp/probe.dcm', enforce_file_format=True)"
docker compose exec -T dimse python -m pynetdicom storescu 127.0.0.1 104 /tmp/probe.dcm \
    -aec SYNAPSEDICOMSCP
sleep 3
docker compose logs --tail 6 dimse analysis | grep -E 'C-STORE|ANALYSIS'
```

```
12:10:45  DIMSE  127.0.0.1:33597  :104  C-STORE  -> SUCCESS (0x0000)  SHA256: 72885cb9f477...  SOPInstanceUID: 1.2.826.0.1...
12:10:45  ANALYSIS  session=1785845445137  ANALYSIS_RESULT  artifact=16794d4170b9  No YARA matches  Entropy: 4.16
```

Two things happened. The upload was accepted and written under `quarantine/`, separate from
the seeded archive, and the sandbox scanned it without being asked. The attacker sees an
ordinary success status.

Quarantined bytes are never returned by C-GET or WADO. They are visible as metadata,
because a PACS that accepted a file and then denied all knowledge of it would be
suspicious, but the file itself stays in the jail.

## 8. The payload sandbox scans uploads and matches its rules

Check 7 proved a scan ran. `No YARA matches` on a harmless file does not prove the rules
work, so send something a rule should catch. This uses the EICAR test string, the standard
harmless stand-in for malware:

```bash
docker compose exec -T dimse python3 -c "
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid
EICAR = rb'X5O!P%@AP[4\PZX54(P^)7CC)7}\$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!\$H+H*'
ds = Dataset(); ds.file_meta = FileMetaDataset()
ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
uid = generate_uid(); ds.file_meta.MediaStorageSOPInstanceUID = uid
ds.SOPClassUID = CTImageStorage; ds.SOPInstanceUID = uid
ds.StudyInstanceUID = generate_uid(); ds.SeriesInstanceUID = generate_uid()
ds.PatientName = 'Probe^Eicar'; ds.PatientID = 'PROBE2'
ds.Rows = 1; ds.Columns = len(EICAR) // 2; ds.SamplesPerPixel = 1
ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15
ds.PhotometricInterpretation = 'MONOCHROME2'; ds.PixelRepresentation = 0
ds.PixelData = EICAR
ds.save_as('/tmp/eicar.dcm', enforce_file_format=True)"
docker compose exec -T dimse python -m pynetdicom storescu 127.0.0.1 104 /tmp/eicar.dcm \
    -aec SYNAPSEDICOMSCP
sleep 4
curl -s 'http://localhost:8081/api/artifacts?rule=EICAR_Test_String&limit=1' | python3 -c "
import sys, json
a = json.load(sys.stdin)
print('records:', len(a))
if a: print('matched_rules:', a[0]['matched_rules'], '| state:', a[0]['state'], '| channel:', a[0]['channel'])"
```

```
records: 1
matched_rules: ['EICAR_Test_String'] | state: completed | channel: DIMSE
```

`state: completed` means the job finished rather than stalling, and `channel: DIMSE` records
which surface the object arrived on. Uploads over STOW-RS and the web form are scanned the
same way and tagged with their own channel.

The scan runs in a separate worker process with its own job store, so it cannot block a
DIMSE response or lose queued work across a restart. Results carry the file type, entropy,
hashes, any IOCs found, and DICOM metadata alongside the rule matches.

To see the full record for one artifact:

```bash
curl -s 'http://localhost:8081/api/artifacts?rule=EICAR_Test_String&limit=1' | python3 -m json.tool
```

If `records: 0`, confirm the worker started:

```bash
docker compose logs analysis | grep 'Analysis worker ready'
```

```
INFO analysis.worker: Analysis worker ready: ruleset=59f45563b131... requeued=0 pending=0
```

A missing line means analysis is switched off. Set `DICOMHAWK_ANALYSIS=true` in `.env` and
recreate the analysis container.

## 9. Browser fingerprinting records a real visit

The collector runs in the browser, so no command line tool can trigger it. First confirm the
page actually carries it:

```bash
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' \
    http://localhost:8080/static/synapse/ClientCapabilities.min.js
curl -s "http://localhost:8080/SynapseSignOn/sts/login?signin=abc" \
    | grep -o 'ClientCapabilities.min.js" data-signals="[^"]*" data-ingest="[^"]*"'
docker compose logs web | grep 'Fingerprinting:'
```

```
200 application/javascript; charset=utf-8
ClientCapabilities.min.js" data-signals="bot,browser,math,rendering,screen" data-ingest="/synapse/error/ClientInfo"
INFO commands.serve: Fingerprinting: signals=bot,browser,math,rendering,screen
INFO fingerprint.component: Fingerprinting: enabled, db=/opt/dicomhawk/state/fingerprint.db max_body=65536 max_per_session=20 max_per_ip=500
```

Both `data-signals` and `data-ingest` must be present. The collector reads both and exits
silently if either is missing, so a script tag with only one loads, runs, and does nothing at
all. Two log lines are expected: the second is missing if the store failed to open and the
feature disabled itself.

Now visit the sign-on page in a real browser. Use another machine on the same network if the
honeypot runs on a VM, since a fingerprint is more interesting when it is not from the host
itself:

```
http://<host>:8080/Synapse
```

Then read what was recorded:

```bash
curl -s 'http://localhost:8081/api/fingerprints?limit=1' | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('records:', len(r))
if r: print('verdict:', r[0]['bot_verdict'], '| checks:', [c['check'] for c in r[0]['bot_checks']])"
```

```
records: 1
verdict: None | checks: []
```

A real browser should produce a `None` verdict and no fired checks. That is the correct
result: the honeypot is telling you this visitor looks human. The signals themselves are
stored in full either way, including canvas rendering, screen geometry, and the browser
engine.

To see automation being caught, load the same URL with a headless browser:

```bash
chrome --headless=new --disable-gpu --dump-dom "http://<host>:8080/Synapse" >/dev/null
```

```
records: 1
verdict: HeadlessChrome | checks: ['user_agent', 'app_version']
```

Two checks firing is the expected result, not weak detection. Modern headless Chrome hides
most of the obvious traces, so the user agent is what gives it away. A session driven by
Selenium or Puppeteer trips more.

`records: 0` after a browser visit usually means the page was loaded from a tool that runs no
JavaScript, or the profile has fingerprinting switched off.

## 10. The operator console shows the captured intelligence

```bash
curl -s http://localhost:8081/api/stats
```

```json
{
  "by_channel": {"ANALYSIS": 227, "DICOMWEB": 1080, "DIMSE": 6713, "WEB": 1101},
  "total_events": 9121,
  "credentials_captured": 1033,
  "unique_source_ips": 3,
  "uploads_captured": 227
}
```

Your numbers will be far smaller after a first install. What matters is that the channels
you exercised above are present and the counts are not zero.

Then open `http://localhost:8081/` in a browser on the host. The console lists attackers,
captured credentials in full, uploads, analysis results, and browser fingerprints. It is
the one page that wears no disguise, because it is an internal tool and confusing it with
a deception surface would be worse than plain styling.

Everything from checks 3 through 9 should be visible here in one place. That is the point of
the console: the surfaces collect separately, the operator reads them together.

If these endpoints return `401`, an operator token is set. Read it from `.env` and pass it
as a bearer token, or as the password in the browser's authentication prompt with the
username left blank:

```bash
curl -s -H "Authorization: Bearer $(grep '^DICOMHAWK_OPERATOR_TOKEN=' .env | cut -d= -f2-)" \
    http://localhost:8081/api/stats
```

## 11. Active and rotated event logs are valid JSONL

```bash
docker compose exec dimse sh -c \
    'python3 -c "import glob,json,os;p=\"/var/log/dicomhawk/dicomhawk.log\";fs=[f for f in glob.glob(p+\"*\") if f==p or f[len(p)+1:].isdigit()];n=sum(sum(1 for line in open(f) if json.loads(line) is not None) for f in fs);print(f\"{len(fs)} files, {n} valid JSON records\")"'
```

```
1 files, 42 valid JSON records
```

Every surface writes one JSON object per line into the active file, tagged with a `channel` of
`DIMSE`, `WEB`, `DICOMWEB`, or `ANALYSIS`. At 50 MiB the file rotates to numbered backups, with
five retained by default. A sidecar `dicomhawk.log.lock` coordinates writers across all service
containers and the analysis worker during rollover; it is a lock file, not JSONL, and the command
deliberately excludes it.
Attacker-controlled values are stored as data, so quotes or newlines cannot forge extra records.
The compact terminal view and developer log additionally render control characters as escaped
text, preventing ANSI sequences, carriage returns, or newlines from changing the operator's
terminal; the JSON record retains the original value as evidence.

This check proves that every retained line is parseable; it cannot prove that an external log
shipper received every event. Monitor the rotated-file count and ship files before the configured
retention window expires when completeness is an operational requirement.

Dashboard worklist clicks are interaction evidence too. Open a profile worklist, select actions
such as **Open Viewer**, **Documents**, or **Study Information**, then compare the compact and full
representations:

```bash
docker compose logs --tail=20 web
tail -n 20 "${DICOMHAWK_DATA_DIR:-$HOME/data/dicomhawk}/logs/dicomhawk.log"
```

The compact stream contains `WEB_WORKLIST_VIEW`, `Studies: N`, and the resolved `Action`. The JSONL
record additionally contains the selected study UID when applicable, session ID, source address,
method, path, user agent, and timestamp. Framework diagnostics such as `waitress.queue` appear only
in Docker stdout and intentionally do not contaminate the structured interaction log.

## 12. Non-DICOM probes on the DICOM port are classified

```bash
printf 'GET /admin HTTP/1.1\r\nHost: pacs\r\n\r\n' | nc -w 1 localhost 104
docker compose exec dimse sh -c \
    'grep "Connection Closed" /var/log/dicomhawk/dicomhawk.log | tail -1'
```

```
{"session_id":"...","channel":"DIMSE","request_type":"Connection Closed",
 "session_parameters":["Bytes received: 36","Duration: 0.004s","Protocol: HTTP",
 "Association decoded: no","First bytes":"474554202f61646d696e...",
 "Preview: GET /admin HTTP/1.1..Host: pacs.."],...}
```

Most traffic an internet-facing DICOM port receives never speaks DICOM. `Protocol` names what
the peer appeared to be doing, `Preview` renders the same bytes the hex covers with
non-printable values replaced by `.`, and `Duration` separates a peer that connected and said
nothing from one that sent a payload. Expect `Protocol: unknown` for traffic none of the
signatures match; the hex and preview still show what arrived.

Probes from the container's own loopback address are not recorded, so run this from the host
against the published port rather than through `docker compose exec`.

## If a check fails

Start with the logs, which carry the reason more often than not:

```bash
docker compose logs --tail 50
```

| Symptom | Likely cause |
|---|---|
| Container never reaches `healthy` | The profile has no `echo` operation, or the DICOM port is already bound on the host |
| `docker compose port` prints nothing for a port | The port is not published; re-run `./setup.sh --reconfigure` |
| Web checks return `404` | The profile is `generic-pacs`, or has no web surface; see the note at the top |
| Login redirects but the worklist never loads | `Secure` cookie over plain HTTP; see check 5 |
| Operator endpoints return `401` | An operator token is set; see check 10 |
| C-FIND returns `matches=0` | The database is empty; seed it as in check 3 |
| No analysis artifacts | Analysis is off; set `DICOMHAWK_ANALYSIS=true` and recreate |
| No fingerprints after a browser visit | Fingerprinting is off for the profile, or the page was loaded by a tool that runs no JavaScript |

Before putting the honeypot on a public address, work through
[Deployment](./deployment.md). Egress lockdown and storage quotas are host-level steps
that this verification does not cover.
