# FAQ

### Is it safe to expose this to the internet?

That is what it is for, but read [Production deployment](./deployment.md) first. The container
already runs unprivileged with a read-only root filesystem and bounded resources. The controls
Docker cannot apply for you are egress lockdown, a size-limited filesystem for captured objects,
and TLS termination. Run it somewhere you are willing to lose, on a network with nothing else
worth reaching.

### Can an attacker use it to store or serve their own files?

No. Objects an attacker uploads are quarantined on arrival. They are recorded and analysed for
you, but they are never returned by C-GET, C-MOVE, or WADO, so the honeypot cannot be used to
host or relay content. Only data written by the seeder is retrievable.

### Nothing happens when I sign in with the bait credentials.

The profile is marking its session cookie `Secure`, and browsers discard a `Secure` cookie
received over plain HTTP. The login succeeds and the session is thrown away before the next
request, so you get neither the pages behind the login nor an error.

Set `DICOMHAWK_SECURE_COOKIES=false` for a plaintext deployment, or put TLS in front and set
`DICOMHAWK_TRUSTED_PROXY`. The server logs a warning at startup when this combination cannot
work.

### Why is the login rejecting everything?

Check `grant_access` in the profile. `none` denies every attempt including the declared bait
credentials, `bait` admits only those credentials, and `any` admits everything. Both shipped
profiles use `bait`, so only the pairs listed in `honey_credentials` get in and every other
guess is denied the way the real product denies it.

### Should I set `grant_access: any`?

Usually not. It is quicker to engage an attacker, but the first deliberately wrong password
that works tells them they are in a decoy. Bait credentials give you the same engagement
without the tell.

### I changed the DIMSE port and now nothing connects.

The port the container listens on and the port Compose publishes are separate. Changing
`DICOMHAWK_PORTS` alone leaves the honeypot listening where nothing is published. Re-run
`./setup.sh`, which writes an override republishing the ports you chose, or adjust the
published ports yourself.

### The seed looks like it has hung.

Downloading a full run takes several minutes and produces no other visible activity. The
command prints one line per series as it starts, which is how you tell a slow download from a
stall. If the source is unreachable it says so and seeds a bundled offline set instead.

### The operator dashboard is asking for a username and password.

Leave the username blank and paste the operator token as the password. The token is the only
thing checked; the username is ignored. It is in `.env` as `DICOMHAWK_OPERATOR_TOKEN` if you no
longer have it.

### Can I run it without Docker?

Yes, in a virtualenv, and that is the convenient way to develop. None of the container
hardening applies to that path, so it is not a deployment option. See
[Installation](./installation.md).

### How do I impersonate a different device?

Write a profile YAML. No code changes are needed for identity, advertised services, headers,
page templates, or DICOMweb layout. Start from the generic profile and see
[Adding a profile](./profiles.md).

### Do I need real patient data?

No, and you should not use any. The seeder downloads from public de-identified imaging
collections and rewrites names, dates, institutions, and physicians so the result is internally
consistent but describes no one.

### Where does everything end up?

Captured objects go to the traces directory, the searchable index and analysis results to their
own database files, and every interaction to a JSON event log, one event per line. The exact
paths are in [Configuration](./configuration.md), and the operator API summarises all of it.

### Can I turn off analysis or fingerprinting?

Yes. Set `DICOMHAWK_ANALYSIS=false` or `DICOMHAWK_FINGERPRINT=false`. Analysis does not change
response semantics, although its bounded handoff can affect timing under load. Fingerprinting is
attacker-visible because it adds a collector asset and ingest route; disabling it removes both.
Choose that feature based on fidelity, privacy, and resource requirements.

### Does it phone home?

No. Serving makes no outbound connections at all, which is why egress can be dropped at the
firewall. Only seeding reaches out, to fetch imaging data and institution names, and only when
you run it.
