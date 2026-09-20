"""Browser smoke test of the whole UI against the LIVE stack. Optional; not part of unittest discovery.

    pip install playwright              # once; it drives your installed Edge or Chrome (no download)
    scripts/start_all.ps1 -Restart      # demo mode, so the impostor tests exist
    python tests/ui_smoke.py

Needs the real agents to pass identity verification (see CLAUDE.md, "Keys"). Screenshots go to
logs/ui_smoke/. The test changes Rules through the UI and restores them when it finishes.
"""
import json, os, re, sys, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP = os.path.join(ROOT, "logs", "ui_smoke")
os.makedirs(SP, exist_ok=True)
URL = "http://localhost:5500/"
API = "http://127.0.0.1:8080"
results = []
# the assistant reports verification as "2 of 4 checks live"; a stubbed config reports "simulated"
VERIFIED_CHIP = re.compile(r"Verified \((simulated|2 of 4 checks live)\)")

def check(name, ok, extra=""):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(extra)) if extra and not ok else ""))

def api_get(p):
    with urllib.request.urlopen(API + p) as r:
        return json.load(r)

def api_post(p, body):
    req = urllib.request.Request(API + p, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))

def put_rules(rules):
    urllib.request.urlopen(urllib.request.Request(API + "/rules", data=json.dumps(rules).encode(), method="PUT", headers={"Content-Type": "application/json"})).read()

original_rules = api_get("/rules")

def preflight():
    """Fail early, with the reason, instead of 60 confusing test failures."""
    h = api_get("/health")
    if not h.get("demo"):
        sys.exit("The assistant is not in demo mode, so impostor tests are off. Start it with scripts/start_all.ps1 -Restart.")
    real = [a for a in api_get("/agents") if a["role"] != "attacker"]
    bad = [a["drug"] for a in real if not a["verification"]["ok"]]
    if bad:
        msg = next((c["message"] for a in real for c in a["verification"]["checks"] if not c["pass"]), "")
        sys.exit(f"Real agents failing identity verification ({', '.join(bad)}): {msg}\nFix keys/DNS first; see CLAUDE.md.")

preflight()

def wait_answer(page, n):
    page.wait_for_function(
        """(n) => { const b = document.querySelectorAll('#thread .msg.bot'); const l = b[b.length-1];
                    return b.length === n && l && (l.querySelector('.disc') || l.querySelector('.note.err')) }""",
        arg=n, timeout=30000)
    page.wait_for_timeout(250)

def last_bot(page):
    return page.locator("#thread .msg.bot").last

with sync_playwright() as pw:
    for channel in ("msedge", "chrome"):
        try:
            browser = pw.chromium.launch(channel=channel, headless=True)
            break
        except Exception:
            continue
    else:
        sys.exit("Could not launch Edge or Chrome for Playwright.")
    ctx = browser.new_context(viewport={"width": 1400, "height": 950}, accept_downloads=True)
    page = ctx.new_page()
    errors, failed = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
    page.on("requestfailed", lambda r: failed.append(r.url))

    page.goto(URL)
    page.wait_for_timeout(1500)

    # ---------- product mode: it is just the chat ----------
    body = page.inner_text("body")
    check("empty state greets the doctor", "Ask about a drug" in body)
    check("no marketing sections (hero / beats / FAQ / CTA)", not any(s in body for s in ["Take the pulse", "Four beats", "Straight answers", "Know who's talking"]), body[:200])
    check("no impostor / demo controls visible in product mode", not page.locator("[data-imp]").first.is_visible() and not page.locator("#demo").is_visible())
    check("no 'Immunity' section anywhere visible", "Immunity" not in body and "Try an impostor" not in body)
    chip = page.inner_text("#statusBtn")
    live = api_get("/health")["verification"] == "live-dns"
    check("status chip matches /health (never claims more or less than the assistant reports)", ("2 of 4 checks live" in chip) if live else ("Simulated verification" in chip), chip)
    check("composer warns about patient details", "don't include patient details" in page.inner_text("#composer"))
    page.screenshot(path=f"{SP}/c01_empty.png")

    # ---------- ask by typing + Enter ----------
    page.click("#q")
    page.keyboard.type("Can simvastatin be taken with clarithromycin?")
    page.keyboard.press("Enter")
    check("user bubble appears", page.locator("#thread .msg.user .bubble").count() == 1)
    check("composer clears on send", page.input_value("#q") == "")
    wait_answer(page, 1)
    bot = last_bot(page)
    check("strip: 2 verified", "2 verified" in bot.inner_text(), bot.inner_text()[:150])
    check("two source columns", bot.locator(".src").count() == 2)
    check("overlap note", "Possible overlap" in bot.inner_text())
    check("signed + verified chips", "signed · demo-hmac" in bot.inner_text() and VERIFIED_CHIP.search(bot.inner_text()) is not None, bot.inner_text()[:200])
    api_texts = {a["text"] for s in api_post("/ask", {"question": "Can simvastatin be taken with clarithromycin?"})["sources"] for a in s["answers"]}
    shown = bot.locator("blockquote").evaluate_all("els => els.map(e => e.textContent)")
    check("every quote on screen exactly matches an API passage", len(shown) >= 2 and all(t in api_texts for t in shown), [t[:40] for t in shown if t not in api_texts])
    more = bot.locator(".more").first
    folded = bot.locator("blockquote.clamp").count() >= 1
    more.click()
    check("long passages fold, toggle unfolds", folded and "Fold passage" in more.inner_text())
    check("identity checks are tucked in a details", bot.locator("details.how").count() == 2 and not bot.locator("details.how").first.evaluate("d => d.open"))
    bot.locator("details.how summary").first.click()
    hows = bot.locator("details.how").first.inner_text()
    check("identity details name the simulated checks as simulated", hows.lower().count("(simulated)") >= 2, hows)
    check("card titles use the label's own section title", "§7.1" in bot.locator(".ptitle").first.inner_text() and "Drug Interactions" in bot.locator(".ptitle").first.inner_text(), bot.locator(".ptitle").first.inner_text())
    lh = bot.locator("blockquote .lh").all_inner_texts()
    check("label's own headings are set apart (Clinical Impact / Intervention / Examples)", any("Clinical Impact:" in x for x in lh) and any("Intervention:" in x for x in lh) and len(lh) >= 3, lh)
    check("'You asked' line repeats the question", "You asked: “Can simvastatin be taken with clarithromycin?”" in bot.locator(".asked").inner_text(), bot.locator(".asked").inner_text())
    check("dark colour scheme (no pale scrollbar)", page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark")
    page.screenshot(path=f"{SP}/c02_answer.png")

    # ---------- new chat + suggestion chip (switching) ----------
    page.click("#newChat")
    check("New chat resets the thread", page.locator("#thread .msg").count() == 0 and page.locator("#empty").count() == 1)
    page.click('#ex button:has-text("switch")')
    wait_answer(page, 1)
    bot = last_bot(page)
    t = bot.inner_text()
    check("switching: advice notice", "does not recommend" in t, t[:200])
    check("switching: Not covered for switching AND the drug with no agent", "'switching'" in t and "atorvastatin" in t)
    check("switching: read-your-question chips", "drug · simvastatin" in t)
    sec = bot.locator("details.secondary")
    check("switching: clarithromycin (only mentions simvastatin) is folded away", sec.count() == 1 and not sec.evaluate("d => d.open"), sec.count())
    check("switching: fold says why it appears", "Also mentions simvastatin" in sec.locator(":scope > summary").inner_text() and "Clarithromycin" in sec.locator(":scope > summary").inner_text(), sec.locator(":scope > summary").inner_text())
    check("switching: simvastatin shown as 'Asked about', main column only", bot.locator(".body > .srcs > .src").count() == 1 and "Asked about" in bot.locator(".body > .srcs > .src").first.inner_text())
    page.screenshot(path=f"{SP}/c16_secondary_folded.png")
    sec.locator(":scope > summary").click()
    check("switching: unfolding reveals the clarithromycin passages", sec.locator(".src .ptitle").count() >= 1)
    btns = bot.locator("button[data-t]")
    check("handoff button only on topic gaps", btns.count() == 1, btns.count())
    btns.first.click()
    bot.locator(".draft .hid").wait_for(timeout=10000)
    check("handoff draft appears, marked not sent", "Draft only - not sent" in bot.locator(".draft").inner_text())
    page.screenshot(path=f"{SP}/c03_switching.png")

    # ---------- gap question ----------
    page.fill("#q", "What does it say about pregnancy?"); page.keyboard.press("Enter")
    wait_answer(page, 2)
    t = last_bot(page).inner_text()
    check("gap: Not covered + both verified but declined", "Not covered" in t and "declined" in t, t[:250])

    # ---------- PHI guard ----------
    page.fill("#q", "Patient DOB 04/12/1961 MRN 445566 on simvastatin, what interacts?"); page.keyboard.press("Enter")
    wait_answer(page, 3)
    check("PHI: refusal shown", "patient identifiers" in last_bot(page).inner_text())
    check("PHI: identifiers are not left on screen", "04/12/1961" not in page.inner_text("#thread") and "Message withheld" in page.inner_text("#thread"))
    page.screenshot(path=f"{SP}/c04_phi.png")

    # ---------- panels ----------
    page.click('[data-panel="sources"]')
    page.wait_for_selector("#agents .card")
    check("Sources panel: 2 real agents, no attackers", page.locator("#agents .card").count() == 2)
    linked = any(a.get("ansRegistry") for a in api_get("/agents") if a["role"] != "attacker")
    check("Sources panel mentions GoDaddy ANS only for agents the team linked", ("GoDaddy ANS" in page.inner_text("#agents")) == linked, linked)
    page.screenshot(path=f"{SP}/c05_sources.png")
    page.keyboard.press("Escape")
    check("Escape closes the panel", not page.locator("#panel").is_visible())

    page.click('[data-panel="rules"]')
    page.wait_for_selector('#rl [data-b="Clarithromycin"]')
    check("Rules: real brands + locked always-on rows", page.locator("#rl [data-b]").count() == 2 and page.locator("#rl .sw:disabled").count() == 2)
    page.uncheck('#rl [data-b="Clarithromycin"]')
    page.click("#save")
    page.wait_for_function("document.getElementById('savemsg').textContent.startsWith('Saved')", timeout=10000)
    check("Rules saved through the API", "Clarithromycin" not in api_get("/rules")["allowedBrands"])
    page.screenshot(path=f"{SP}/c06_rules.png")
    page.click("#scrim", position={"x": 20, "y": 300})
    check("clicking outside closes the panel", not page.locator("#panel").is_visible())
    page.fill("#q", "Can simvastatin be taken with clarithromycin?"); page.keyboard.press("Enter")
    wait_answer(page, 4)
    t = last_bot(page).inner_text()
    check("turned-off brand is skipped", "1 skipped" in t and "1 verified" in t and last_bot(page).locator(".src").count() == 1, t[:200])

    page.click('[data-panel="activity"]')
    page.wait_for_timeout(700)
    jt = page.inner_text("#tl")
    check("Activity: counters + events", int(page.inner_text("#nq")) >= 4 and "Question received" in jt and "Verified answer delivered" in jt)
    check("Activity: PHI refusal and rules change recorded", "Patient identifiers refused" in jt and "Rules updated" in jt)
    check("Activity: never shows question text", "04/12/1961" not in jt and "445566" not in jt and "what does it say" not in jt.lower() and "atorvastatin" not in jt)
    n0 = page.locator("#tl > div").count()
    check("Activity: shows at most 40 events at first", n0 <= 40, n0)
    if page.locator("#tl [data-more]").count():
        page.click("#tl [data-more]")
        check("'Show older events' reveals more", page.locator("#tl > div").count() > n0)
    with page.expect_download(timeout=10000) as dl:
        page.click("#dl")
    report = json.load(open(dl.value.path(), encoding="utf-8"))
    check("Export downloads a JSON report that states the verification mode", report.get("verificationMode") == api_get("/health")["verification"] and "live" in report.get("verificationDetail", {}) and len(report.get("events", [])) > 5, list(report.keys()))
    page.screenshot(path=f"{SP}/c07_activity.png")
    page.keyboard.press("Escape")

    page.click("#statusBtn")
    tt = page.inner_text("#clear")
    check("Status panel: simulated + demo key + partly built + cached labels", all(s in tt for s in ["Simulated", "Demo key", "Partly built", "Cached snapshot"]), tt[:300])
    check("Status panel explains what 'Not covered' means", "outside those passages" in tt)
    page.screenshot(path=f"{SP}/c08_status.png")
    page.keyboard.press("Escape")

    # ---------- relevance grouping: "Tell me about Simvastatin" ----------
    page.click("#newChat")
    api = api_post("/ask", {"question": "Tell me about Simvastatin"})
    named = {d["agent"] for d in api["analysis"]["drugsMentioned"]}
    exp_secondary = [x for x in api["sources"] if x["agent"] not in named]
    page.fill("#q", "Tell me about Simvastatin"); page.keyboard.press("Enter")
    wait_answer(page, 1)
    bot = last_bot(page)
    check("relevance: named drug's source is marked 'Asked about'", "Asked about" in bot.inner_text())
    if exp_secondary:
        sec = bot.locator("details.secondary")
        check("relevance: a source that only mentions the drug is folded away", sec.count() == 1 and not sec.evaluate("d => d.open"), sec.count())
        check("relevance: its summary says why it appears", "Also mentions simvastatin" in sec.locator(":scope > summary").inner_text(), sec.locator(":scope > summary").inner_text())
        sec.locator(":scope > summary").click()
        check("relevance: unfolding shows its passages", sec.locator(".src .ptitle").count() >= 1)
        page.screenshot(path=f"{SP}/c14_relevance.png")
    else:
        check("relevance: no secondary source expected, none shown", bot.locator("details.secondary").count() == 0)

    # ---------- "At a glance" on a question that returns several passages ----------
    page.click("#newChat")
    q = "What do I need to know about CYP3A interactions?"
    first_src = api_post("/ask", {"question": q})["sources"][0]
    page.fill("#q", q); page.keyboard.press("Enter")
    wait_answer(page, 1)
    bot = last_bot(page)
    jumps = bot.locator(".src").first.locator(".glance [data-jump]")
    check("'At a glance' lists every passage with a jump link", len(first_src["answers"]) > 1 and jumps.count() == len(first_src["answers"]), (jumps.count(), len(first_src["answers"])))
    jumps.nth(len(first_src["answers"]) - 1).click()
    page.wait_for_timeout(150)
    check("jump link highlights the target passage", bot.locator(".card.flash").count() == 1)
    page.screenshot(path=f"{SP}/c15_glance.png")

    # restore rules now so demo mode starts from the real config
    put_rules(original_rules)
    page.reload(); page.wait_for_timeout(1200)

    # ---------- demo mode ----------
    page.click("#demoToggle")
    check("demo guide opens with 8 steps", page.locator("#demo").is_visible() and page.locator("#steps > li").count() == 8)
    page.screenshot(path=f"{SP}/c09_demo_open.png")

    page.click('[data-step="0"]')
    wait_answer(page, 1)
    check("step 1 runs the interaction question", "2 verified" in last_bot(page).inner_text() and "Possible overlap" in last_bot(page).inner_text())
    check("step 1 marked done", page.locator("#stepn-0").evaluate("e => e.closest('.step').classList.contains('done')"))

    n = 1
    for key, word in {"lookalike": "domain record", "expired": "expired", "replay": "24 hours", "revoked": "revoked"}.items():
        page.click(f'[data-imp="{key}"]')
        n += 1
        wait_answer(page, n)
        bot = last_bot(page)
        t = bot.inner_text().lower()
        check(f"impostor {key}: arrives inside a normal answer (2 verified + 1 blocked)", "2 verified" in t and "1 blocked" in t, t[:160])
        check(f"impostor {key}: blocked with a plain reason", "blocked before display" in t and word in t, t[:300])
        check(f"impostor {key}: its text starts hidden", bot.locator(".alert details").evaluate("d => !d.open"))
    last_bot(page).locator(".alert summary").click()
    check("toggle reveals text labelled UNVERIFIED", "UNVERIFIED" in last_bot(page).locator(".alert .hid").inner_text())
    check("header pulse flatlines on a blocked source", page.evaluate("document.getElementById('ecg').classList.contains('bad')"))
    check("impostor step marked done", page.locator("#stepn-3").evaluate("e => e.closest('.step-static').classList.contains('done')"))
    page.screenshot(path=f"{SP}/c10_impostor.png")

    page.click('[data-step="4"]')
    check("step 5 opens Rules", page.locator("#panel").is_visible() and "Rules" in page.inner_text("#panelTitle"))
    page.keyboard.press("Escape")
    page.click('[data-step="6"]'); n += 1
    wait_answer(page, n)
    check("step 7 refuses patient details and withholds the message", "patient identifiers" in last_bot(page).inner_text() and "01/01/1970" not in page.inner_text("#thread"))
    page.click('[data-step="7"]')
    check("step 8 opens the simulated/live panel", "What's live" in page.inner_text("#panelTitle"))
    page.keyboard.press("Escape")
    page.click("#demoClose")
    check("closing the guide restores the plain product", not page.locator("#demo").is_visible())

    # ?demo=1 opens it directly
    d = ctx.new_page(); d.set_viewport_size({"width": 1400, "height": 950}); d.goto(URL + "?demo=1"); d.wait_for_timeout(800)
    check("?demo=1 opens the guide", d.locator("#demo").is_visible())
    d.screenshot(path=f"{SP}/c11_demo_url.png"); d.close()

    # ---------- mobile ----------
    m = ctx.new_page(); m.set_viewport_size({"width": 400, "height": 850})
    m.goto(URL); m.wait_for_timeout(1200)
    check("mobile 400px: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), m.evaluate("document.documentElement.scrollWidth"))
    m.screenshot(path=f"{SP}/c12_mobile.png")
    m.click("#q"); m.keyboard.type("Can simvastatin be taken with clarithromycin?"); m.keyboard.press("Enter")
    wait_answer(m, 1)
    check("mobile: answer fits without sideways scroll", not m.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    m.screenshot(path=f"{SP}/c13_mobile_answer.png")

    unexpected = [e for e in errors if "400 (Bad Request)" not in e and "ERR_NETWORK_CHANGED" not in e]
    check("no unexpected console/page errors (the PHI 400s are expected)", not unexpected, unexpected[:5])
    check("no failed requests (other than optional fonts)", not [u for u in failed if "fonts.g" not in u], failed[:5])
    browser.close()

put_rules(original_rules)
print("rules restored:", api_get("/rules") == original_rules)
bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
