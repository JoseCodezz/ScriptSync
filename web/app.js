// ScriptSync web UI. Talks to the assistant API (see assistant/server.py).
// All text from the API is inserted with textContent, never innerHTML.
const API = "http://127.0.0.1:8080";

const $ = (id) => document.getElementById(id);

// el("div", {class: "x"}, "text", childNode, ...) -> DOM node
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function api(path, options) {
  const res = await fetch(API + path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---------- status badge ----------
async function checkHealth() {
  const badge = $("status");
  try {
    const h = await api("/health");
    badge.className = "pill pill-ok";
    badge.textContent = `Assistant online · verification ${h.verification} · signing ${h.signing}`;
  } catch {
    badge.className = "pill pill-bad";
    badge.textContent = "Assistant offline (start it on port 8080)";
  }
}

// ---------- shared pieces ----------
function checkPills(verification) {
  const names = { dns: "Domain record", cert: "Certificate", log: "Public log", current: "Current version" };
  return el("div", { class: "checks" },
    (verification?.checks || []).map((c) =>
      el("span", { class: "pill " + (c.pass ? "pill-ok" : "pill-bad"), title: c.message || "" },
        (c.pass ? "✓ " : "✗ ") + (names[c.id] || c.id))));
}

function passage(a) {
  const heading = [a.title, a.section && `Section ${a.section}`].filter(Boolean).join(" · ");
  return el("blockquote", {},
    el("p", {}, a.text),
    el("div", { class: "meta" }, `${heading} · label version ${a.labelVersion}`));
}

function sourceCard(s) {
  return el("div", { class: "card" },
    el("div", { class: "source-head" },
      el("h3", {}, `${s.brand} · ${s.drug || "drug"}`),
      el("span", { class: "pill pill-ok" }, "Verified")),
    checkPills(s.verification),
    (s.answers || []).map(passage),
    el("div", { class: "sub" },
      `Signed ${s.timestamp || "?"} · signature ${s.signature?.valid ? "valid" : "invalid"}, ` +
      `age ${s.signature?.ageHours ?? "?"} h (${s.signature?.mode || "?"})`));
}

function blockedCard(b) {
  const hidden = (b.hiddenContent || []).map((a) => el("p", {}, a.text));
  const box = el("div", { class: "hidden-content", hidden: true },
    el("div", { class: "label" }, "UNVERIFIED - do not rely on this text"),
    hidden.length ? hidden : el("p", { class: "sub" }, "The blocked agent returned no text."));
  const toggle = el("button", {
    class: "btn-link", type: "button",
    onclick: () => {
      box.hidden = !box.hidden;
      toggle.textContent = box.hidden ? "Show what it tried to say" : "Hide it again";
    },
  }, "Show what it tried to say");

  return el("div", { class: "card blocked" },
    el("div", { class: "source-head" },
      el("h3", {}, `${b.brand} · claimed name`),
      el("span", { class: "pill pill-bad" }, "Blocked")),
    el("div", { class: "sub" }, b.ansName),
    el("div", { class: "notice notice-bad" }, el("strong", {}, "Blocked before showing any content"), b.reason),
    checkPills(b.verification),
    (b.verification?.warnings || []).map((w) => el("div", { class: "sub" }, "⚠ " + w)),
    toggle, box);
}

// ---------- Ask + Answer ----------
function renderResults(data) {
  const out = $("results");
  out.replaceChildren();

  out.append(el("h2", {}, `Answers for: "${data.question}"`));

  if (data.sources.length === 0) {
    out.append(el("div", { class: "notice notice-warn" },
      el("strong", {}, "No verified source answered"),
      "Nothing is shown because no verified agent had a matching passage."));
  }
  data.sources.forEach((s) => out.append(sourceCard(s)));

  data.overlaps.forEach((o) => out.append(
    el("div", { class: "notice notice-warn" },
      el("strong", {}, `Possible overlap: ${o.tag}`),
      o.note,
      el("ul", {}, o.statements.map((s) =>
        el("li", {}, `${s.source}, section ${s.section}: "${s.text}"`))))));

  data.gaps.forEach((g) => out.append(
    el("div", { class: "notice notice-warn" }, el("strong", {}, `Not covered: ${g.topic}`), g.message,
      el("div", { class: "sub" }, "A handoff draft to the manufacturers' medical information teams can be added here."))));

  if (data.refused.length) {
    out.append(el("div", { class: "card" }, el("h3", {}, "Verified, but declined to answer"),
      data.refused.map((r) => el("div", { class: "sub" }, `${r.brand}: ${r.reason}`))));
  }
  data.blocked.forEach((b) => out.append(blockedCard(b)));
  data.unreachable.forEach((u) => out.append(
    el("div", { class: "notice notice-warn" }, el("strong", {}, `${u.brand} unreachable`), u.reason)));
  data.skipped.forEach((k) => out.append(
    el("div", { class: "sub" }, `${k.brand} skipped: ${k.reason}`)));

  out.append(el("p", { class: "disclaimer" }, data.disclaimer));
  out.hidden = false;
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("question").value.trim();
  if (!question) return;
  const btn = $("ask-btn");
  btn.disabled = true;
  btn.textContent = "Asking…";
  try {
    renderResults(await api("/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }));
  } catch (err) {
    const out = $("results");
    out.replaceChildren(el("div", { class: "notice notice-bad" }, el("strong", {}, "Could not get an answer"), err.message));
    out.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Ask";
  }
});

document.querySelectorAll(".chip").forEach((c) =>
  c.addEventListener("click", () => { $("question").value = c.dataset.q; $("question").focus(); }));

// ---------- Impostor attacks ----------
document.querySelectorAll("[data-attack]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const box = $("attack-result");
    box.replaceChildren(el("p", { class: "sub" }, "Sending impostor…"));
    try {
      const r = await api(`/attack/${btn.dataset.attack}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      box.replaceChildren(r.status === "blocked"
        ? blockedCard(r)
        : el("div", { class: "notice notice-bad" },
            el("strong", {}, `NOT BLOCKED (status: ${r.status})`),
            "The impostor got through. This is a bug in the assistant."));
    } catch (err) {
      box.replaceChildren(el("div", { class: "notice notice-bad" }, err.message));
    }
  }));

checkHealth();
