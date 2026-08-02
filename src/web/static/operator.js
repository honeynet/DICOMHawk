const byId = (id) => document.getElementById(id);

function cell(row, value, className = "") {
  const td = document.createElement("td");
  td.textContent = value == null || value === "" ? "—" : String(value);
  if (className) td.className = className;
  row.appendChild(td);
  return td;
}

function badge(parent, value, kind = "") {
  const span = document.createElement("span");
  span.className = `pill ${kind}`.trim();
  span.textContent = value;
  parent.appendChild(span);
}

function time(value) {
  if (!value) return "—";
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function renderChannels(channels) {
  const root = byId("channel-chart");
  root.replaceChildren();
  const entries = Object.entries(channels || {}).sort((a, b) => b[1] - a[1]);
  const maximum = Math.max(...entries.map((entry) => entry[1]), 1);
  if (!entries.length) { root.textContent = "No activity in this range."; return; }
  entries.forEach(([name, count]) => {
    const row = document.createElement("div"); row.className = "bar-row";
    const label = document.createElement("span"); label.textContent = name;
    const track = document.createElement("progress");
    track.max = maximum; track.value = count;
    const total = document.createElement("strong"); total.textContent = count.toLocaleString();
    row.append(label, track, total); root.appendChild(row);
  });
}

function renderAttackers(items) {
  const root = byId("attackers"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr");
    cell(row, item.ip, "mono"); cell(row, item.events);
    const tactics = cell(row, "");
    tactics.textContent = "";
    (item.tactics || []).forEach((value) => badge(tactics, value, value === "storage-abuse" ? "bad" : "warn"));
    cell(row, time(item.last_seen), "muted"); root.appendChild(row);
  });
}

function renderEvents(items) {
  const root = byId("events"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr");
    cell(row, time(item.timestamp), "muted"); cell(row, item.ip, "mono");
    const channel = cell(row, ""); channel.textContent = ""; badge(channel, item.channel || "UNKNOWN");
    cell(row, item.request_type);
    const parameters = Array.isArray(item.session_parameters) ? item.session_parameters : [];
    cell(row, parameters.join(" · "), "mono muted");
    root.appendChild(row);
  });
}

function renderUploads(items) {
  const root = byId("uploads"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr"); cell(row, item.ip, "mono"); cell(row, item.channel);
    cell(row, bytes(item.bytes));
    const disposition = cell(row, ""); disposition.textContent = "";
    badge(disposition, item.disposition, item.disposition === "rejected" ? "bad" : "good");
    disposition.title = item.reject_reason || "";
    const hash = item.sha256 ? `${item.sha256.slice(0, 12)}…` : "—";
    const hashCell = cell(row, hash, "mono"); hashCell.title = item.sha256 || "";
    root.appendChild(row);
  });
}

function renderCredentials(items) {
  const root = byId("credentials"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr"); cell(row, item.username, "mono");
    cell(row, item.password, "mono"); cell(row, item.count);
    cell(row, (item.source_ips || []).join(", "), "mono");
    const bait = cell(row, ""); bait.textContent = "";
    if (item.honey_hit) badge(bait, "matched", "warn"); else bait.textContent = "—";
    root.appendChild(row);
  });
}

function renderArtifacts(items) {
  const root = byId("artifacts"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr");
    cell(row, time(item.created_at), "muted"); cell(row, item.channel);
    const disposition = cell(row, ""); disposition.textContent = "";
    badge(disposition, item.disposition, item.disposition === "rejected" ? "bad" : "good");
    const state = cell(row, ""); state.textContent = "";
    badge(state, item.state, item.state === "completed" ? "good" : item.state === "pending" || item.state === "running" ? "warn" : "bad");
    const rules = (item.matched_rules || []);
    const rulesCell = cell(row, "");
    if (rules.length) { rulesCell.textContent = ""; rules.forEach((rule) => badge(rulesCell, rule, "bad")); }
    const hash = item.sha256 ? `${item.sha256.slice(0, 12)}…` : "—";
    const hashCell = cell(row, hash, "mono"); hashCell.title = item.sha256 || "";
    root.appendChild(row);
  });
}

function renderFingerprints(items) {
  const root = byId("fingerprints"); root.replaceChildren();
  items.forEach((item) => {
    const row = document.createElement("tr");
    cell(row, time(item.created_at), "muted"); cell(row, item.ip || "—", "mono");
    const verdict = cell(row, "");
    if (item.bot_verdict) { verdict.textContent = ""; badge(verdict, item.bot_verdict, "bad"); }
    else verdict.textContent = "—";
    const checks = (item.bot_checks || []);
    const checksCell = cell(row, checks.length ? "" : "—");
    checks.forEach((check) => badge(checksCell, check.check, "warn"));
    const agent = cell(row, item.user_agent || "—"); agent.title = item.user_agent || "";
    const hash = item.fingerprint_hash ? `${item.fingerprint_hash.slice(0, 12)}…` : "—";
    const hashCell = cell(row, hash, "mono"); hashCell.title = item.fingerprint_hash || "";
    root.appendChild(row);
  });
}

function query() {
  const params = new URLSearchParams();
  const hours = byId("range").value;
  if (hours !== "all") params.set("since", new Date(Date.now() - Number(hours) * 3600000).toISOString());
  const channel = byId("channel").value;
  if (channel) params.set("channel", channel);
  return params.toString();
}

let refreshing = false;

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  const notice = byId("notice");
  notice.className = "notice"; notice.textContent = "Refreshing retained activity…";
  try {
    const [overviewResponse, artifactsResponse, fingerprintsResponse] = await Promise.all([
      fetch(`/api/overview?${query()}`, {cache: "no-store"}),
      fetch("/api/artifacts?limit=50", {cache: "no-store"}),
      fetch("/api/fingerprints?limit=50", {cache: "no-store"}),
    ]);
    if (!overviewResponse.ok) throw new Error(`Operator API returned ${overviewResponse.status}`);
    const data = await overviewResponse.json(); const stats = data.stats;
    byId("total-events").textContent = stats.total_events.toLocaleString();
    byId("unique-sources").textContent = stats.unique_source_ips.toLocaleString();
    byId("credential-attempts").textContent = stats.credentials_captured.toLocaleString();
    byId("captured-payloads").textContent = stats.uploads_captured.toLocaleString();
    byId("rejected-payloads").textContent = stats.uploads_rejected.toLocaleString();
    renderChannels(stats.by_channel); renderAttackers(data.attackers); renderEvents(data.events);
    renderUploads(data.uploads); renderCredentials(data.credentials);
    renderArtifacts(artifactsResponse.ok ? await artifactsResponse.json() : []);
    renderFingerprints(fingerprintsResponse.ok ? await fingerprintsResponse.json() : []);
    const skipped = stats.skipped_records ? ` · ${stats.skipped_records} malformed record(s) skipped` : "";
    notice.textContent = `Updated ${new Date().toLocaleTimeString()}${skipped}`;
  } catch (error) {
    notice.className = "notice error"; notice.textContent = `Unable to load activity: ${error.message}`;
  } finally {
    refreshing = false;
  }
}

byId("refresh").addEventListener("click", refresh);
byId("range").addEventListener("change", refresh);
byId("channel").addEventListener("change", refresh);
refresh();
setInterval(refresh, 15000);
