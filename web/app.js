// ScriptSync Pulse: a chat for the doctor's assistant. Talks to the assistant API (assistant/server.py).
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
  skipped: "Skipped", attack: "Impostor test started", rules_changed: "Rules updated",
  handoff_draft: "Handoff drafted", phi_blocked: "Patient identifiers refused",
};
const BLOCK_EVENTS = new Set(["verify_failed", "signature_failed", "freshness_failed", "identity_mismatch"]);
const SUGGESTIONS = ["Can simvastatin be taken with clarithromycin?", "What do I need to know about CYP3A interactions?", "Should I switch from simvastatin to atorvastatin?"];
const DEFAULT_Q = SUGGESTIONS[0];

let journalLimit = 40;   // newest events shown first; "Show older events" adds 40 more
const state = { online: false, health: null, agents: [], rules: null, log: [], busy: false, seq: 0, lastQuestion: null, sawAnswers: false, sawPlaceholder: false, msgs: new WeakMap() };

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

// ---------- trust pulse (header) ----------
const BEAT = "M0 27H50l6-3 5 3h14l5-20 8 42 5-22h34l6-3 5 3h20l5-20 8 42 5-22h20", FLAT = "M0 27H220";
let pulseTimer;
function pulse(bad, holdMs) {
  $("#ecg").classList.toggle("bad", bad);
  $("#ep").setAttribute("d", bad ? FLAT : BEAT);
  clearTimeout(pulseTimer);
  if (bad && holdMs) pulseTimer = setTimeout(() => pulse(false), holdMs);
}

// ---------- shared renderers ----------
// One <li> per check. reveal=true leaves the row neutral (data-s) so the progress
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

// A blocked source. Its text stays inside a closed <details> and is labelled unverified.
function alertCard(title, r) {
  if (r.status !== "blocked") {
    return `<div class="alert"><span class="chip bad">NOT BLOCKED (status: ${esc(r.status)})</span><p style="margin-top:8px;font-weight:600">That source got through. This is a bug in the assistant.</p></div>`;
  }
  const hidden = (r.hiddenContent || []).map((a) => esc(a.text)).join("\n\n") || "The blocked agent returned no text.";
  const warnings = (r.verification?.warnings || []).map((w) => `<div class="mono">⚠ ${esc(w)}</div>`).join("");
  return `<div class="alert"><span class="chip bad">Blocked before display</span>
    <h4 style="margin-top:6px;font-size:18px">${esc(title)}</h4>
    <div class="mono">claims to be: ${esc(r.ansName)}</div>
    <p style="margin-top:8px;font-weight:600">${esc(r.reason)}</p>
    <ul class="ck">${checkRows(r.verification, r.failedChecks || [])}</ul>${warnings}
    <details><summary>Show what it tried to say</summary><div class="hid"><b>UNVERIFIED. Do not rely on this text.</b>\n${hidden}</div></details></div>`;
}

// ---------- chat: messages ----------
const thread = $("#thread");
const EMPTY = `<div class="empty-state" id="empty"><svg class="ecg" viewBox="0 0 220 54" preserveAspectRatio="none" aria-hidden="true"><path d="${BEAT}"/></svg>
  <h1>Ask about a <span class="gt">drug</span></h1>
  <p>Answers come only from verified label agents, quoted word for word. A source that can't prove who it is is blocked before you see a word.</p>
  <div class="ex" id="ex">${SUGGESTIONS.map((s) => `<button type="button" data-q="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;

function scrollDown() { thread.scrollTop = thread.scrollHeight; }
function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  thread.append(el);
  return el;
}
function addBot(html) {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `<div class="avatar"><svg viewBox="0 0 26 20" aria-hidden="true"><path d="M1 10h5l3-8 5 16 3-8h8" fill="none" stroke="url(#lg)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div class="body">${html}</div>`;
  thread.append(el);
  return el;
}
function newChat() {
  if (state.busy) return;
  thread.innerHTML = EMPTY;
  state.lastQuestion = null;
  $("#q").focus();
}

// Progress cards shown while sources are being checked.
function pendingCard(a) {
  return `<div class="card"><h4>${esc(a.brand)} label agent</h4><ul class="ck">${CK.map((c) => `<li>${c[1]}</li>`).join("")}</ul><div><span class="chip">Contacting…</span></div></div>`;
}
function resultCard(kind, r) {
  const v = r.verification;
  const chip = {
    verified: `<span class="chip ok">Verified (${esc(v?.mode || "simulated")})</span>`,
    refused: '<span class="chip ok">Verified · declined to answer</span>',
    blocked: '<span class="chip bad">Blocked</span>',
    unreachable: '<span class="chip">Verified · did not respond</span>',
    skipped: '<span class="chip">Skipped · not on your list</span>',
  }[kind];
  const rows = kind === "skipped" ? "" : `<ul class="ck">${checkRows(v, r.failedChecks || [], true)}</ul>`;
  const why = kind === "blocked" ? `<p class="mono" style="margin-top:8px">${esc(r.reason)}</p>` : "";
  return `<div class="card"><h4>${esc(r.brand)} label agent</h4>${rows}<div class="st" data-chip="${esc(chip)}"></div>${why}</div>`;
}

// Display-only formatting of a verbatim quote. It wraps the label's own headings in <span class="lh">
// (bold, on their own line) and never adds, removes or reorders a character of the text.
const LABEL_HEADS = /\b(Clinical Impact|Intervention|Examples):/g;
function quoteHtml(a) {
  const t = String(a.text ?? "");
  const marks = [];
  const lead = a.section && a.title ? `${a.section} ${a.title}` : "";
  if (lead && t.startsWith(lead)) marks.push([0, lead.length]);
  else { const i = t.indexOf("Clinical Impact:"); if (i > 0 && i < 80) marks.push([0, i]); }
  for (const m of t.matchAll(LABEL_HEADS)) if (!marks.length || m.index >= marks[marks.length - 1][1]) marks.push([m.index, m.index + m[0].length]);
  let out = "", pos = 0;
  for (const [st, en] of marks) { out += esc(t.slice(pos, st)) + `<span class="lh">${esc(t.slice(st, en))}</span>`; pos = en; }
  return out + esc(t.slice(pos));
}

const shortTitle = (t) => { t = String(t || ""); return t.length > 34 ? t.slice(0, 33) + "…" : t; };

function passageCard(a, id) {
  const cut = a.source?.truncated ? '<span class="chip">excerpt (cut short)</span>' : "";
  const long = String(a.text || "").length > 320;   // long passages start folded, with a visible toggle
  const where = [a.labelVersion, a.labelDate, a.source?.field && `openFDA · ${a.source.field}`].filter(Boolean).map(esc).join(" · ");
  return `<div class="card" id="${esc(id)}"><div class="ptitle"><span class="sec">§${esc(a.section)}</span>${esc(a.title || "")}</div>
    <blockquote${long ? ' class="clamp"' : ""}>${quoteHtml(a)}</blockquote>${long ? '<button type="button" class="more">Show full passage</button><br>' : ""}
    ${(a.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}${cut}<div class="passage-meta">${where}</div></div>`;
}

function sourceColumn(s, seq, asked) {
  const ids = (s.answers || []).map((_, i) => `m${seq}-${s.agent}-${i}`);
  const glance = (s.answers || []).length > 1
    ? `<div class="glance">At a glance ${s.answers.map((a, i) => `<button type="button" data-jump="${esc(ids[i])}">§${esc(a.section)} ${esc(shortTitle(a.title))}</button>`).join("")}</div>` : "";
  return `<div class="src"><div class="srchead"><h4>${esc(s.brand)} label agent</h4>
    <span class="chip ok">✓ Verified (${esc(s.verification?.mode || "simulated")})</span><span class="chip v">signed · ${esc(s.signature?.mode || "")}</span>${asked ? '<span class="chip v">Asked about</span>' : ""}
    <div class="mono">${esc(s.ansName)} · signed ${esc(s.timestamp || "?")}</div>${glance}
    <details class="how"><summary>Identity checks</summary><ul class="ck">${checkRows(s.verification)}</ul></details></div>
    ${(s.answers || []).map((a, i) => passageCard(a, ids[i])).join("")}</div>`;
}

// Sources for a drug the doctor named come first. A source for a different drug that only
// answered because its label mentions the named drug is shown, but folded away.
function sourceSections(d, seq) {
  const named = d.analysis?.drugsMentioned || [];
  const askedAgents = new Set(named.map((x) => x.agent));
  const primary = named.length ? d.sources.filter((s) => askedAgents.has(s.agent)) : d.sources;
  const secondary = named.length && primary.length ? d.sources.filter((s) => !askedAgents.has(s.agent)) : [];
  let html = `<div class="srcs">${primary.map((s) => sourceColumn(s, seq, named.length > 0)).join("")}</div>`;
  if (secondary.length) {
    const names = named.map((x) => x.drug);
    const mentions = names.filter((n) => secondary.some((s) => (s.answers || []).some((a) => String(a.text || "").toLowerCase().includes(String(n).toLowerCase()))));
    const count = secondary.reduce((n, s) => n + (s.answers || []).length, 0);
    html += `<details class="secondary"><summary>${mentions.length ? `Also mentions ${esc(mentions.join(", "))}` : "Other sources that answered"} · ${secondary.map((s) => esc(s.brand) + " label agent").join(", ")} (${count} passage${count === 1 ? "" : "s"})</summary>
      <div class="srcs">${secondary.map((s) => sourceColumn(s, seq, false)).join("")}</div></details>`;
  }
  return html;
}

function readLine(an) {
  const bits = [
    ...(an?.drugsMentioned || []).map((d) => `<span class="chip v">drug · ${esc(d.drug)}</span>`),
    ...(an?.topics || []).map((t) => `<span class="chip">topic · ${esc(t)}</span>`),
  ];
  return bits.length ? `<div class="readline">Read your question as:${bits.join("")}</div>` : "";
}

// The whole assistant answer for one question.
function answerHtml(d) {
  const mode = d.sources[0]?.verification?.mode || state.health?.verification || "simulated";
  const strip = [
    d.sources.length ? `<span class="chip ok">✓ ${d.sources.length} verified</span>` : "",
    d.blocked.length ? `<span class="chip bad">✕ ${d.blocked.length} blocked</span>` : "",
    d.refused.length ? `<span class="chip">${d.refused.length} verified, declined</span>` : "",
    d.skipped.length ? `<span class="chip">${d.skipped.length} skipped</span>` : "",
    d.unreachable.length ? `<span class="chip bad">${d.unreachable.length} unreachable</span>` : "",
    `<span class="chip v">verification ${esc(mode)}</span>`,
  ].join("");
  const seq = ++state.seq;
  return `<div class="asked">You asked: “${esc(d.question)}”</div><div class="strip">${strip}</div>`
    + (d.notices || []).map((n) => `<div class="note adv">${esc(n.message)}</div>`).join("")
    + readLine(d.analysis)
    + (d.sources.length ? sourceSections(d, seq)
      : (d.gaps.length || d.blocked.length ? "" : '<div class="note gap">No verified source had a passage for this question, so nothing is shown. ScriptSync does not guess.</div>'))
    + d.overlaps.map((o) => `<div class="note ov"><b>Possible overlap · ${esc(o.tag)}</b><br>${esc(o.note)}<div>${o.statements.map((s) => `<span class="chip v">${esc(s.source)} · §${esc(s.section)}</span>`).join("")}</div></div>`).join("")
    + d.gaps.map((g) => `<div class="note gap">${esc(g.message)}${String(g.topic).startsWith("drug:") ? "" : `<button type="button" data-t="${esc(g.topic)}">Draft handoff note</button><div class="draft"></div>`}</div>`).join("")
    + d.refused.map((r) => `<div class="mono" style="margin-top:10px">✓ ${esc(r.brand)} label agent verified, but declined: ${esc(r.reason)}</div>`).join("")
    + d.blocked.map((b) => alertCard(`${b.brand} label agent`, b)).join("")
    + d.unreachable.map((u) => `<div class="note err">${esc(u.brand)} label agent did not respond. ${esc(u.reason)}</div>`).join("")
    + d.skipped.map((k) => `<div class="mono" style="margin-top:10px">${esc(k.brand)} skipped: ${esc(k.reason)}</div>`).join("")
    + `<p class="disc">${esc(d.disclaimer || "Cross-references are for clinician review. Not medical advice.")}</p>`;
}

// ---------- chat: asking ----------
function setBusy(b) {
  state.busy = b;
  $("#go").disabled = b; $("#q").disabled = b;
  $("#go").textContent = b ? "Checking…" : "Send";
}

// `impostor` (demo only) runs a real attack alongside the question, so the blocked
// source shows up inside a normal answer.
async function ask(question, { impostor } = {}) {
  question = question.trim();
  if (!question || state.busy) return;
  setBusy(true);
  state.lastQuestion = question;
  $("#empty")?.remove();
  const userEl = addUser(question);
  const allowed = state.rules?.allowedBrands;
  const show = state.agents.length ? state.agents.filter((a) => !allowed || allowed.includes(a.brand)) : [{ brand: "Assistant" }];
  const botEl = addBot(`<div class="strip"><span class="chip">Checking sources…</span></div><div class="g">${show.map(pendingCard).join("")}</div>`);
  const body = botEl.querySelector(".body");
  scrollDown();
  pulse(false);
  try {
    const [data, attack] = await Promise.all([
      send("POST", "/ask", { question }),
      impostor ? send("POST", `/attack/${encodeURIComponent(impostor)}`, { question }).catch(() => null) : null,
    ]);
    if (attack && attack.status === "blocked") data.blocked.push(attack);

    // Light the real results up one check at a time.
    const byAgent = new Map();
    for (const [kind, list] of [["verified", data.sources], ["refused", data.refused], ["blocked", data.blocked], ["unreachable", data.unreachable], ["skipped", data.skipped]])
      list.forEach((r) => byAgent.set(r.agent, { kind, r }));
    const order = [...state.agents.map((a) => a.id).filter((id) => byAgent.has(id)), ...[...byAgent.keys()].filter((id) => !state.agents.some((a) => a.id === id))];
    body.querySelector(".g").innerHTML = order.map((id) => resultCard(byAgent.get(id).kind, byAgent.get(id).r)).join("");
    const cards = [...body.querySelectorAll(".g .card")];
    const steps = Math.max(0, ...cards.map((c) => c.querySelectorAll("li").length));
    for (let i = 0; i < steps; i++) {
      await wait(200);
      cards.forEach((c) => { const li = c.querySelectorAll("li")[i]; if (li) li.classList.add(li.dataset.s); });
    }
    cards.forEach((c) => { const st = c.querySelector(".st"); st.innerHTML = st.dataset.chip; });
    if (data.blocked.length) pulse(true, 2600);
    await wait(500);

    state.msgs.set(botEl, { question: data.question, brands: [...new Set([...data.sources, ...data.refused].map((s) => s.brand))] });
    const texts = data.sources.flatMap((s) => (s.answers || []).map((a) => a.text || ""));
    if (texts.length) state.sawAnswers = true;
    if (texts.some((t) => t.includes("[PLACEHOLDER"))) state.sawPlaceholder = true;
    body.innerHTML = answerHtml(data);
    botEl.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (err) {
    if (/patient identifiers/i.test(err.message)) userEl.querySelector(".bubble").textContent = "Message withheld: it looked like it contained patient identifiers.";
    body.innerHTML = `<div class="note err" style="margin-top:0">${esc(err.message)}</div>`;
    scrollDown();
  }
  setBusy(false);
  if ($("#panel").hidden) $("#q").focus();
  refreshLog();
}

// composer
const q = $("#q");
q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 140) + "px"; });
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = q.value;
  if (!text.trim() || state.busy) return;
  q.value = ""; q.style.height = "auto";
  ask(text);
});
$("#newChat").onclick = newChat;

// delegated clicks inside the thread: suggestion chips, fold toggle, handoff drafts
thread.addEventListener("click", async (e) => {
  const sug = e.target.closest("#ex button[data-q]");
  if (sug) { ask(sug.dataset.q); return; }

  const jump = e.target.closest("[data-jump]");
  if (jump) {
    const el = document.getElementById(jump.dataset.jump);
    if (el) {
      const det = el.closest("details.secondary");
      if (det) det.open = true;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1400);
    }
    return;
  }

  const more = e.target.closest(".more");
  if (more) {
    const open = more.previousElementSibling.classList.toggle("clamp") === false;
    more.textContent = open ? "Fold passage" : "Show full passage";
    return;
  }

  // Drafts a note to the manufacturers' medical information teams. Nothing is sent.
  const btn = e.target.closest("button[data-t]");
  if (!btn) return;
  const ctx = state.msgs.get(btn.closest(".msg"));
  if (!ctx) return;
  const box = btn.closest(".note").querySelector(".draft");
  btn.disabled = true; btn.textContent = "Drafting…";
  try {
    const r = await send("POST", "/handoff", { question: ctx.question, topics: [btn.dataset.t], brands: ctx.brands });
    btn.textContent = "Draft ready (not sent)";
    box.innerHTML = `<div class="hid">${esc(r.draft)}</div>`;
  } catch (err) {
    btn.disabled = false; btn.textContent = "Draft handoff note";
    box.innerHTML = `<div class="note err">${esc(err.message)}</div>`;
  }
  refreshLog();
});

// ---------- slide-over panels ----------
const PANEL_TITLES = { sources: "Sources", rules: "Rules", activity: "Activity", status: "What's live and what's simulated" };
let lastFocus = null;
function openPanel(name) {
  lastFocus = document.activeElement;
  document.querySelectorAll("#panel section").forEach((s) => { s.hidden = s.id !== "p-" + name; });
  $("#panelTitle").textContent = PANEL_TITLES[name];
  $("#scrim").hidden = false; $("#panel").hidden = false;
  $("#panelClose").focus();
  if (name === "sources") loadAgents();
  if (name === "rules") loadConsent();
  if (name === "activity") refreshLog();
  if (name === "status") renderTransparency();
}
function closePanel() {
  $("#scrim").hidden = true; $("#panel").hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}
document.querySelectorAll("[data-panel]").forEach((b) => b.addEventListener("click", () => openPanel(b.dataset.panel)));
$("#panelClose").onclick = closePanel;
$("#scrim").onclick = closePanel;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#panel").hidden) closePanel(); });

// ---------- status, sources, rules, activity ----------
async function loadHealth() {
  try { state.health = await api("/health"); state.online = true; }
  catch { state.health = null; state.online = false; }
  $("#statusBtn").classList.toggle("off", !state.online);
  const v = state.health?.verification;
  $("#modeText").textContent = state.online ? `${v.charAt(0).toUpperCase() + v.slice(1)} verification` : "Assistant offline";
  renderTransparency();
}

async function loadAgents() {
  try { state.agents = (await api("/agents")).filter((a) => a.role !== "attacker"); state.online = true; }
  catch (err) { state.agents = []; $("#agents").innerHTML = `<div class="note err" style="margin-top:0">${esc(err.message)}</div>`; return; }
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
    msg.textContent = "Saved. Allowed brands apply to your next question. Quiet hours are stored, but not enforced yet.";
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
  const rows = log.slice(-journalLimit).reverse().map((e) => {
    const detail = e.event === "rules_changed" ? "Allowed brands, hours and switches were saved." : e.detail;
    return `<div><b>${esc(EVENT_LABELS[e.event] || e.event)}</b>${esc(detail)}<br><span>${esc(String(e.time || "").slice(11, 19))} UTC${e.result ? " · " + esc(e.result) : ""}</span></div>`;
  }).join("");
  const older = log.length - journalLimit;
  $("#tl").innerHTML = log.length
    ? rows + (older > 0 ? `<button type="button" class="btn ghost" data-more style="margin:4px 0 12px">Show older events (${older} more)</button>` : "")
    : '<div class="empty">Nothing yet. Ask a question to start the record.</div>';
}
$("#tl").addEventListener("click", (e) => {
  if (e.target.closest("[data-more]")) { journalLimit += 40; renderJournal(); }
});

$("#dl").onclick = async () => {
  await refreshLog();
  const report = { generated: new Date().toISOString(), verificationMode: state.health?.verification, signingMode: state.health?.signing, rules: state.rules, events: state.log };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  a.download = "scriptsync-report.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

function renderTransparency() {
  const h = state.health, on = state.online;
  const label = state.sawPlaceholder ? ["bad", "Placeholder"] : state.sawAnswers ? ["ok", "Cached snapshot"] : ["", "Not seen yet"];
  const ver = h?.verification || "simulated";
  const rows = [
    ["The assistant", "Runs on this machine; this page reads it live", on ? ["ok", "Live · local"] : ["bad", "Offline"]],
    ["ANS identity checks", "Read from a config file. A live DNS check exists but is not wired into the assistant yet", !on || ver === "simulated" ? ["bad", "Simulated"] : ["ok", ver]],
    ["Signatures", "HMAC-SHA256 with a shared demo key", ["bad", "Demo key"]],
    ["Label text", "Verbatim passages from public DailyMed / openFDA labels, saved ahead of time, with section and version. Only a hand-picked set of passages is served, so \"Not covered\" means outside those passages", label],
    ["Overlap and gaps", "Tag matching, no language model", ["ok", "Deterministic"]],
    ["Contact rules", "Allowed brands are enforced. Quiet hours and urgent-alert rules are stored but not enforced yet", ["bad", "Partly built"]],
    ["Patient data", "Questions that look like patient identifiers are refused and never logged", ["ok", "Guarded"]],
  ];
  $("#clear").innerHTML = rows.map(([t, d, [cls, txt]]) => `<div class="line"><div>${esc(t)}<small>${esc(d)}</small></div><span class="chip ${cls}">${esc(txt)}</span></div>`).join("");
}

// ---------- demo guide (presenter only; not part of the product) ----------
const STEPS = [
  { title: "Interaction question", note: "Two verified answers, quoted word for word, plus a Possible overlap.", q: "Can simvastatin be taken with clarithromycin?" },
  { title: "A gap", note: "Both agents verify but decline, so it says Not covered. That means outside the passages they serve, not that the label is silent.", q: "What does it say about pregnancy?" },
  { title: "Switching drugs", note: "Advice-seeking notice, plus Not covered for switching and for the drug with no agent.", q: "Should I switch from simvastatin to atorvastatin?" },
  { title: "An impostor arrives", note: "Pick one. It answers alongside the real agents and is blocked before any text shows.", impostors: true },
  { title: "Rules", note: "Turn a brand off and save, then ask again: it is skipped.", panel: "rules" },
  { title: "Activity and report", note: "The audit log, counters and JSON export. Question text is never stored.", panel: "activity" },
  { title: "Patient details refused", note: "A date of birth is refused, the message is withheld, and nothing is logged.", q: "Patient DOB 01/01/1970 is on simvastatin. What interacts?" },
  { title: "What's simulated", note: "Say it out loud: simulated verification, demo signing key.", panel: "status" },
];

function markDone(i) { $(`#stepn-${i}`)?.closest(".step, .step-static")?.classList.add("done"); }

function renderSteps() {
  $("#steps").innerHTML = STEPS.map((s, i) => {
    const head = `<span class="n" id="stepn-${i}">${i + 1}</span>`;
    if (s.impostors) {
      return `<li><div class="step-static">${head}<div style="flex:1"><b style="font-size:14px">${esc(s.title)}</b><small style="display:block;color:var(--mut);font-size:12px;line-height:1.4">${esc(s.note)}</small>
        <div class="imps">${ATTACKS.map(([k, l]) => `<button type="button" data-imp="${k}">${esc(l)}</button>`).join("")}</div></div></div></li>`;
    }
    return `<li><button type="button" class="step" data-step="${i}">${head}<span><b>${esc(s.title)}</b><small>${esc(s.note)}</small></span></button></li>`;
  }).join("");
}

$("#steps").addEventListener("click", (e) => {
  const imp = e.target.closest("[data-imp]");
  if (imp) {
    if (state.busy) return;
    ask(state.lastQuestion || DEFAULT_Q, { impostor: imp.dataset.imp });
    markDone(STEPS.findIndex((s) => s.impostors));
    return;
  }
  const b = e.target.closest("[data-step]");
  if (!b) return;
  const i = Number(b.dataset.step), s = STEPS[i];
  if (s.q) { if (state.busy) return; ask(s.q); } else openPanel(s.panel);
  markDone(i);
});

function setDemo(on) {
  document.body.classList.toggle("demo-on", on);
  $("#demo").hidden = !on;
  $("#demoToggle").setAttribute("aria-pressed", String(on));
}
$("#demoToggle").onclick = () => setDemo(true);
$("#demoClose").onclick = () => setDemo(false);
if (new URLSearchParams(location.search).get("demo") === "1") setDemo(true);

// ---------- start ----------
async function init() {
  thread.innerHTML = EMPTY;
  renderSteps();
  renderTransparency();
  renderJournal();
  q.focus();
  await Promise.all([loadHealth(), refreshLog(), loadAgents().then(loadConsent)]);
}
init();
