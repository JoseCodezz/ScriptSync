// ScriptSync Pulse dashboard. Talks to the assistant API (see assistant/server.py).
// Every string that comes from the API goes through esc() before it reaches innerHTML.
const API = "http://127.0.0.1:8080";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// The four identity checks the assistant reports, in display order.
const CK = [["dns", "Domain record"], ["cert", "Certificate / key"], ["log", "Public log"], ["current", "Current, not revoked"]];
// Other reasons a message can be blocked after identity passes.
const EXTRA_CHECKS = { freshness: "Message under 24 hours old", signature: "Signature matches", identity: "Signed as the verified name" };
const ATTACKS = [["lookalike", "Lookalike domain"], ["expired", "Expired identity"], ["replay", "Replayed message"], ["revoked", "Revoked version"]];
const EVENT_LABELS = {
  ask: "Question received", analyze: "Question read", verify: "Identity check",
  verify_failed: "Blocked: identity", signature_failed: "Blocked: signature",
  freshness_failed: "Blocked: stale message", identity_mismatch: "Blocked: name mismatch",
  answer: "Verified answer delivered", refused: "Agent declined", agent_error: "Agent unreachable",
  skipped: "Skipped", attack: "Impostor test started", rules_changed: "Consent updated",
  handoff_draft: "Handoff drafted", phi_blocked: "Patient identifiers refused",
};
const BLOCK_EVENTS = new Set(["verify_failed", "signature_failed", "freshness_failed", "identity_mismatch"]);

let journalLimit = 40;   // newest events shown first; "Show older events" adds 40 more
const state = { online: false, health: null, agents: [], rules: null, log: [], last: null, sawAnswers: false, sawPlaceholder: false };

// ---------- API ----------
async function api(path, options) {
  let res;
  try { res = await fetch(API + path, options); }
  catch { throw new Error(`Can't reach the assistant at ${API}. Is it running?`); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep statusText */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}
const send = (method, path, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---------- trust pulse ----------
const BEAT = "M0 27H50l6-3 5 3h14l5-20 8 42 5-22h34l6-3 5 3h20l5-20 8 42 5-22h20", FLAT = "M0 27H220";
let pulseTimer;
function pulse(bad, holdMs) {
  $("#ecg").classList.toggle("bad", bad);
  $("#ep").setAttribute("d", bad ? FLAT : BEAT);
  clearTimeout(pulseTimer);
  if (bad && holdMs) pulseTimer = setTimeout(() => pulse(false), holdMs);
}

// ---------- navigation ----------
const ICON = {
  today: '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>', consult: '<path d="M4 5h16v11H9l-5 4z"/>',
  chart: '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7"/>',
  immune: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  consent: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  journal: '<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2zM9 9h6M9 13h6"/>',
  clear: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16v.5"/>',
};
const NAV = [["today", "Today"], ["consult", "Consult"], ["chart", "Chart"], ["immune", "Immunity"], ["consent", "Consent"], ["journal", "Journal"], ["clear", "Transparency"]];
$("#rail").innerHTML = NAV.map((n) => `<button data-nav="${n[0]}"><svg viewBox="0 0 24 24" aria-hidden="true">${ICON[n[0]]}</svg>${n[1]}</button>`).join("");

function show(v) {
  document.querySelectorAll(".view").forEach((e) => e.classList.toggle("on", e.id === "v-" + v));
  document.querySelectorAll("#rail button").forEach((b) => b.classList.toggle("on", b.dataset.nav === v));
  if (v === "today") loadAgents();
  if (v === "consent") loadConsent();
  if (v === "journal" || v === "clear") refreshLog();
}
$("#rail").onclick = (e) => { const b = e.target.closest("[data-nav]"); if (b) show(b.dataset.nav); };

// ---------- shared renderers ----------
// One <li> per check. reveal=true leaves the row neutral (data-s) so the consult
// animation can light it up; otherwise the row is colored straight away.
function checkRows(v, failedIds = [], reveal = false) {
  const checks = v?.checks || [];
  const rows = CK.map(([id, label]) => {
    const c = checks.find((x) => x.id === id);
    return { label, pass: c ? !!c.pass : false, msg: c?.message || "" };
  });
  for (const id of failedIds) if (!CK.some((c) => c[0] === id)) rows.push({ label: EXTRA_CHECKS[id] || id, pass: false, msg: "" });
  return rows.map((r) => {
    const s = r.pass ? "done" : "bad";
    return `<li ${reveal ? `data-s="${s}"` : `class="${s}"`}>${esc(r.label)}${!r.pass && r.msg ? `<span class="mono">${esc(r.msg)}</span>` : ""}</li>`;
  }).join("");
}

// Impostor / blocked result. The blocked text stays inside a closed <details>.
function alertCard(title, r) {
  if (r.status !== "blocked") {
    return `<div class="alert"><span class="chip bad">NOT BLOCKED (status: ${esc(r.status)})</span><p style="margin-top:8px;font-weight:600">The impostor got through. That is a bug in the assistant.</p></div>`;
  }
  const hidden = (r.hiddenContent || []).map((a) => esc(a.text)).join("\n\n") || "The blocked agent returned no text.";
  const warnings = (r.verification?.warnings || []).map((w) => `<div class="mono">⚠ ${esc(w)}</div>`).join("");
  return `<div class="alert"><span class="chip bad">Flatline · blocked before display</span>
    <h4 style="margin-top:6px;font-size:20px">${esc(title)}</h4>
    <div class="mono">claims to be: ${esc(r.ansName)}</div>
    <p style="margin-top:8px;font-weight:600">${esc(r.reason)}</p>
    <ul class="ck">${checkRows(r.verification, r.failedChecks || [])}</ul>${warnings}
    <details><summary>Show what it tried to say</summary><div class="hid"><b>UNVERIFIED. Do not rely on this text.</b>\n${hidden}</div></details></div>`;
}

// ---------- status, agents, rules, log ----------
async function loadHealth() {
  try { state.health = await api("/health"); state.online = true; }
  catch { state.health = null; state.online = false; }
  $("#kick").classList.toggle("off", !state.online);
  $("#modeText").textContent = state.online ? `Demo mode · verification ${state.health.verification}` : "Assistant offline";
  renderTransparency();
}

async function loadAgents() {
  try { state.agents = (await api("/agents")).filter((a) => a.role !== "attacker"); state.online = true; }
  catch (err) { state.agents = []; $("#agents").innerHTML = `<div class="note err">${esc(err.message)}</div>`; return; }
  $("#agents").innerHTML = state.agents.length ? state.agents.map((a) => {
    const v = a.verification;
    return `<div class="card"><h4>${esc(a.brand)} label agent</h4><div class="mono">${esc(a.ansName)}</div>
      <span class="chip ${v.ok ? "ok" : "bad"}">${v.ok ? "✓ identity ok" : "✕ identity failed"} (${esc(v.mode)})</span><span class="chip v">demo agent</span><span class="chip">not run by the manufacturer</span>
      <ul class="ck">${checkRows(v)}</ul></div>`;
  }).join("") : '<div class="empty">No agents are configured.</div>';
}

const brandList = () => [...new Set(state.agents.map((a) => a.brand))];

async function loadConsent() {
  if (!state.agents.length) await loadAgents();
  try { state.rules = await api("/rules"); } catch (err) { $("#savemsg").textContent = err.message; }
  const r = state.rules || {}, allowed = new Set(r.allowedBrands || []);
  $("#rl").innerHTML = brandList().map((b) => `<div class="line"><span>${esc(b)} label agent</span><input type="checkbox" class="sw" data-b="${esc(b)}" ${allowed.has(b) ? "checked" : ""} aria-label="Allow ${esc(b)}"></div>`).join("")
    + '<div class="line"><span>Verified agents only<small>Always on</small></span><input type="checkbox" class="sw" checked disabled aria-label="Verified agents only"></div>'
    + '<div class="line"><span>Urgent safety alerts always come through<small>Always on</small></span><input type="checkbox" class="sw" checked disabled aria-label="Urgent alerts"></div>';
  $("#qs").value = r.clinicHours?.[0] || "08:00";
  $("#qe").value = r.clinicHours?.[1] || "17:00";
  $("#hold").checked = !!r.holdRoutineDuringClinicHours;
}

$("#save").onclick = async () => {
  const msg = $("#savemsg");
  if (!state.rules) { msg.textContent = "Couldn't load your current rules, so nothing was saved."; return; }
  if (!$("#qs").value || !$("#qe").value) { msg.textContent = "Pick both a start and an end time."; return; }
  const body = {
    ...state.rules,
    allowedBrands: [...document.querySelectorAll("#rl [data-b]")].filter((i) => i.checked).map((i) => i.dataset.b),
    clinicHours: [$("#qs").value, $("#qe").value],
    holdRoutineDuringClinicHours: $("#hold").checked,
    verifiedOnly: true,
  };
  const btn = $("#save"); btn.disabled = true;
  try {
    state.rules = await send("PUT", "/rules", body);
    msg.textContent = "Saved. Allowed brands apply to your next consult. Quiet hours are stored, but not enforced yet.";
  } catch (err) { msg.textContent = err.message; }
  btn.disabled = false;
  refreshLog();
};

async function refreshLog() {
  try { state.log = await api("/log"); } catch { /* keep the last log we had */ }
  renderJournal();
}

function renderJournal() {
  const log = state.log, n = (fn) => log.filter(fn).length;
  $("#nv").textContent = n((e) => e.event === "answer");
  $("#nb").textContent = n((e) => BLOCK_EVENTS.has(e.event));
  $("#nq").textContent = n((e) => e.event === "ask");
  const last = log[log.length - 1];
  $("#last").textContent = last ? `Last: ${EVENT_LABELS[last.event] || last.event}` : "Waiting for the first consult.";
  const rows = log.slice(-journalLimit).reverse().map((e) => {
    const detail = e.event === "rules_changed" ? "Allowed brands, hours and switches were saved." : e.detail;
    return `<div><b>${esc(EVENT_LABELS[e.event] || e.event)}</b>${esc(detail)}<br><span>${esc(String(e.time || "").slice(11, 19))} UTC${e.result ? " · " + esc(e.result) : ""}</span></div>`;
  }).join("");
  const older = log.length - journalLimit;
  $("#tl").innerHTML = log.length
    ? rows + (older > 0 ? `<button type="button" class="btn ghost" data-more style="margin:4px 0 12px">Show older events (${older} more)</button>` : "")
    : '<div class="empty">Nothing yet. Start a consult or test the immunity lab.</div>';
}
$("#tl").addEventListener("click", (e) => {
  if (e.target.closest("[data-more]")) { journalLimit += 40; renderJournal(); }
});

$("#dl").onclick = async () => {
  await refreshLog();
  const report = { generated: new Date().toISOString(), verificationMode: state.health?.verification, signingMode: state.health?.signing, rules: state.rules, events: state.log };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  a.download = "scriptsync-journal.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

// ---------- consult ----------
function pendingCard(a) {
  return `<div class="card"><h4>${esc(a.brand)} label agent</h4><ul class="ck">${CK.map((c) => `<li>${c[1]}</li>`).join("")}</ul><div><span class="chip">Contacting…</span></div></div>`;
}

// One card per agent, in config order. Rows are neutral until reveal() lights them.
function resultCard(kind, r) {
  const v = r.verification;
  const chip = {
    verified: `<span class="chip ok">Healthy · verified (${esc(v?.mode || "simulated")})</span>`,
    refused: '<span class="chip ok">Verified · declined to answer</span>',
    blocked: '<span class="chip bad">Flatline · blocked</span>',
    unreachable: '<span class="chip">Verified · did not respond</span>',
    skipped: '<span class="chip">Skipped · not on your consent list</span>',
  }[kind];
  const rows = kind === "skipped" ? "" : `<ul class="ck">${checkRows(v, r.failedChecks || [], true)}</ul>`;
  const why = kind === "blocked" ? `<p class="mono" style="margin-top:8px">${esc(r.reason)}</p>` : "";
  return `<div class="card"><h4>${esc(r.brand)} label agent</h4>${rows}<div class="st" data-chip="${esc(chip)}"></div>${why}</div>`;
}

async function consult() {
  const q = $("#q").value.trim();
  if (!q) return;
  const btn = $("#go");
  btn.disabled = true; btn.textContent = "Checking vitals…";
  pulse(false);
  const allowed = state.rules?.allowedBrands;
  $("#prog").innerHTML = (state.agents.length ? state.agents.filter((a) => !allowed || allowed.includes(a.brand)) : [{ brand: "Assistant" }]).map(pendingCard).join("");
  try {
    const data = await send("POST", "/ask", { question: q });
    const byAgent = new Map();
    for (const [kind, list] of [["verified", data.sources], ["refused", data.refused], ["blocked", data.blocked], ["unreachable", data.unreachable], ["skipped", data.skipped]])
      list.forEach((r) => byAgent.set(r.agent, { kind, r }));
    const order = [...state.agents.map((a) => a.id).filter((id) => byAgent.has(id)), ...[...byAgent.keys()].filter((id) => !state.agents.some((a) => a.id === id))];
    $("#prog").innerHTML = order.map((id) => resultCard(byAgent.get(id).kind, byAgent.get(id).r)).join("");
    // Light the real results up one check at a time.
    const cards = [...document.querySelectorAll("#prog .card")];
    const steps = Math.max(0, ...cards.map((c) => c.querySelectorAll("li").length));
    for (let i = 0; i < steps; i++) {
      await wait(260);
      cards.forEach((c) => { const li = c.querySelectorAll("li")[i]; if (li) li.classList.add(li.dataset.s); });
    }
    cards.forEach((c) => { const st = c.querySelector(".st"); st.innerHTML = st.dataset.chip; });
    if (data.blocked.length) pulse(true, 2600);
    await wait(700);
    renderChart(data);
    show("chart");
  } catch (err) {
    $("#prog").innerHTML = `<div class="note err" style="grid-column:1/-1">${esc(err.message)}</div>`;
  }
  btn.disabled = false; btn.textContent = "Check vitals";
  refreshLog();
}
$("#go").onclick = consult;
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") consult(); });
$("#ex").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-q]");
  if (b) { $("#q").value = b.dataset.q; consult(); }
});

// ---------- chart ----------
function passageCard(a) {
  const meta = [`<b>Section ${esc(a.section)}</b>`, a.title && esc(a.title), a.labelVersion && esc(a.labelVersion), a.labelDate && esc(a.labelDate)].filter(Boolean).join(" · ");
  const cut = a.source?.truncated ? '<span class="chip">excerpt (cut short)</span>' : "";
  const long = String(a.text || "").length > 450;   // long passages start folded, with a visible toggle
  return `<div class="card"><div class="passage-meta">${meta}</div><blockquote${long ? ' class="clamp"' : ""}>${esc(a.text)}</blockquote>${long ? '<button type="button" class="more">Show full passage</button><br>' : ""}${(a.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}${cut}</div>`;
}

$("#cards").addEventListener("click", (e) => {
  const b = e.target.closest(".more");
  if (!b) return;
  const open = b.previousElementSibling.classList.toggle("clamp") === false;
  b.textContent = open ? "Fold passage" : "Show full passage";
});

function sourceColumn(s) {
  return `<div class="src"><div class="srchead"><h4>${esc(s.brand)} label agent</h4>
    <span class="chip ok">✓ Verified (${esc(s.verification?.mode || "simulated")})</span><span class="chip v">signed · ${esc(s.signature?.mode || "")}</span>
    <div class="mono">${esc(s.ansName)} · signed ${esc(s.timestamp || "?")}</div></div>${(s.answers || []).map(passageCard).join("")}</div>`;
}

function readLine(an) {
  const bits = [
    ...(an?.drugsMentioned || []).map((d) => `<span class="chip v">drug · ${esc(d.drug)}</span>`),
    ...(an?.topics || []).map((t) => `<span class="chip">topic · ${esc(t)}</span>`),
  ];
  return bits.length ? `<div class="readline">Read your question as:${bits.join("")}</div>` : "";
}

function renderChart(d) {
  const brands = [...new Set([...d.sources, ...d.refused].map((s) => s.brand))];
  state.last = { question: d.question, brands };
  const texts = d.sources.flatMap((s) => (s.answers || []).map((a) => a.text || ""));
  if (texts.length) state.sawAnswers = true;
  if (texts.some((t) => t.includes("[PLACEHOLDER"))) state.sawPlaceholder = true;
  renderTransparency();

  $("#cq").textContent = "Question: " + d.question;
  $("#anote").innerHTML = (d.notices || []).map((n) => `<div class="note adv">${esc(n.message)}</div>`).join("") + readLine(d.analysis);
  $("#cards").innerHTML = d.sources.length ? d.sources.map(sourceColumn).join("")
    : '<div class="empty">No verified source had a passage for this question, so nothing is shown. ScriptSync does not guess.</div>';

  $("#notes").innerHTML =
    d.overlaps.map((o) => `<div class="note ov"><b>Possible overlap · ${esc(o.tag)}</b><br>${esc(o.note)}<div>${o.statements.map((s) => `<span class="chip v">${esc(s.source)} · §${esc(s.section)}</span>`).join("")}</div></div>`).join("")
    + d.gaps.map((g) => `<div class="note gap">${esc(g.message)}${String(g.topic).startsWith("drug:") ? "" : `<button data-t="${esc(g.topic)}">Draft handoff note</button>`}<div class="draft"></div></div>`).join("");

  $("#others").innerHTML =
    d.refused.map((r) => `<div class="note gap">${esc(r.brand)} label agent is verified but declined: ${esc(r.reason)}</div>`).join("")
    + d.blocked.map((b) => alertCard(`${b.brand} label agent`, b)).join("")
    + d.unreachable.map((u) => `<div class="note err">${esc(u.brand)} label agent did not respond. ${esc(u.reason)}</div>`).join("")
    + d.skipped.map((k) => `<div class="mono" style="margin-top:10px">${esc(k.brand)} skipped: ${esc(k.reason)}</div>`).join("");

  $("#disc").textContent = d.disclaimer || "Cross-references are for clinician review. Not medical advice.";
}

// Drafts a note to the manufacturers' medical information teams. Nothing is sent.
$("#notes").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-t]");
  if (!btn || !state.last) return;
  const box = btn.closest(".note").querySelector(".draft");
  btn.disabled = true; btn.textContent = "Drafting…";
  try {
    const r = await send("POST", "/handoff", { question: state.last.question, topics: [btn.dataset.t], brands: state.last.brands });
    btn.textContent = "Draft ready (not sent)";
    box.innerHTML = `<div class="hid">${esc(r.draft)}</div>`;
  } catch (err) {
    btn.disabled = false; btn.textContent = "Draft handoff note";
    box.innerHTML = `<div class="note err">${esc(err.message)}</div>`;
  }
  refreshLog();
});

// ---------- immunity lab ----------
$("#atk").innerHTML = ATTACKS.map(([k, l]) => `<button class="atk" data-k="${k}">${l}</button>`).join("");
$("#atk").onclick = async (e) => {
  const b = e.target.closest("[data-k]");
  if (!b) return;
  const title = ATTACKS.find((a) => a[0] === b.dataset.k)[1];
  b.disabled = true;
  $("#out").innerHTML = '<div class="empty">Sending impostor…</div>';
  try {
    const r = await send("POST", `/attack/${encodeURIComponent(b.dataset.k)}`, {});
    $("#out").innerHTML = alertCard(title, r);
    if (r.status === "blocked") pulse(true, 2600);
  } catch (err) { $("#out").innerHTML = `<div class="note err">${esc(err.message)}</div>`; }
  b.disabled = false;
  refreshLog();
};

// ---------- transparency ----------
function renderTransparency() {
  const h = state.health, on = state.online;
  const label = state.sawPlaceholder ? ["bad", "Placeholder"] : state.sawAnswers ? ["ok", "Cached snapshot"] : ["", "Not seen yet"];
  const ver = h?.verification || "simulated";
  const rows = [
    ["This page", "Reads the assistant service running on this machine", on ? ["ok", "Live · local"] : ["bad", "Assistant offline"]],
    ["ANS identity checks", "Read from a config file. A live DNS check exists but is not wired into the assistant yet", !on || ver === "simulated" ? ["bad", "Simulated"] : ["ok", ver]],
    ["Signatures", "HMAC-SHA256 with a shared demo key", ["bad", "Demo key"]],
    ["Label text", "Verbatim passages from public DailyMed / openFDA labels, saved ahead of time, with section and version", label],
    ["Overlap and gaps", "Tag matching, no language model", ["ok", "Deterministic"]],
    ["Contact rules", "Allowed brands are enforced. Quiet hours and urgent-alert rules are stored but not enforced yet", ["bad", "Partly built"]],
    ["Patient data", "Questions that look like patient identifiers are refused and never logged", ["ok", "Guarded"]],
  ];
  $("#clear").innerHTML = rows.map(([t, d, [cls, txt]]) => `<div class="line"><div>${esc(t)}<small>${esc(d)}</small></div><span class="chip ${cls}">${esc(txt)}</span></div>`).join("");
}

// ---------- start ----------
async function init() {
  show("consult");
  renderTransparency();
  renderJournal();
  await Promise.all([loadHealth(), refreshLog(), loadAgents().then(loadConsent)]);
}
init();
