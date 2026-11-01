"""A new person's first hour, scripted end to end on a fresh memory (no install, no real AI apps):

  welcome (name + memory name) → pick AIs → connect Claude Code (its first message is simulated) → bring what it
  knows (found on this computer) → Grok by copy and paste (question copied, its answer pasted back, saved) → give
  Grok your memory → the map → Memory: correct a note, search, save a new note → add a second space and save a note
  there → the map with both spaces → the memory-quality screen and the private-model download (shown, not clicked)
  → the Apps tab. In English at 1280x800 and in Spanish at 1512x982.

Each step asserts what a person would see and saves a screenshot. Exit 1 lists every failed step.
Usage: python scripts/journey_check.py [shots_dir]      Needs: pip install playwright && playwright install chromium
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PY = sys.executable
SIZES = [(1280, 800, "en-US"), (1512, 982, "es-AR")][:int(os.environ.get("JOURNEY_SIZES", "2"))]  # JOURNEY_SIZES=1: English only
GROK = {"en-US": "Here's what I know about you:\n\n**About you**\n- You live in Porto and work as a teacher.\n- You prefer short answers.\n- Your sister is called Marta.\n\nLet me know if you want me to add anything!",
        "es-AR": "¡Claro! Esto es lo que sé de vos:\n\n**Sobre vos**\n- Vivís en Porto y trabajás de docente.\n- Preferís respuestas cortas.\n- Tu hermana se llama Marta.\n\n¿Querés que agregue algo más?"}


def start(port: int):
    root = Path(tempfile.mkdtemp(prefix="journey-"))
    home, scan = root / "home", root / "scan"
    (scan / ".claude").mkdir(parents=True)
    (scan / ".claude/CLAUDE.md").write_text("# About me\n- I like short answers\n- I work on a small bakery website in my free time\n")
    (scan / ".claude.json").write_text("{}")
    (scan / ".cursor").mkdir()
    subprocess.run([PY, "-m", "hub_gateway.local", "init", "--home", str(home), "--contexts", "main"], check=True, capture_output=True)
    env = {**os.environ, "MNEMOS_SCAN_HOME": str(scan), "MNEMOS_HYGIENE": "0", "MNEMOS_ENGINE_OFFLINE": "1"}
    log = open(root / "serve.log", "w")
    srv = subprocess.Popen([PY, "-m", "hub_gateway.local", "serve", "--home", str(home), "--port", str(port)], env=env, stdout=log, stderr=log)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1); break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return root, home, scan, srv


async def first_message(port: int, token: str, client: str):
    """What an AI app does when you send it a message: connect and call Mnemos once."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from mcp.types import Implementation
    t = StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp", headers={"Authorization": f"Bearer {token}"})
    async with Client(t, client_info=Implementation(name=client, version="1.0")) as c:
        await c.call_tool("hub_whoami", {})


async def journey(b, port, token, w, h, lang, shots, fails, log):
    ctx = await b.new_context(viewport={"width": w, "height": h}, locale=lang, permissions=["clipboard-read", "clipboard-write"])
    pg = await ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    tag = f"{w}-{lang[:2]}"
    n = [0]

    async def shot(name):
        n[0] += 1
        if shots:
            await pg.screenshot(path=f"{shots}/{tag}-{n[0]:02d}-{name}.png")

    def check(ok, what):
        log.append(f"{tag} {'OK ' if ok else 'FAIL'} {what}")
        if not ok:
            fails.append(f"{tag}: {what}")
        return ok

    async def wait_for(js, ms=15000):
        t0 = time.time()
        while (time.time() - t0) * 1000 < ms:
            if await pg.evaluate(js):
                return True
            await pg.wait_for_timeout(300)
        return False

    async def engine_with_runtime(route):
        # CI has no model runtime; show the screen a Mac user sees (runtime there, model not downloaded yet)
        r = await route.fetch()
        j = await r.json()
        j.setdefault("builtin", {})["runtime"] = True
        j["builtin"]["runtime_error"] = None
        await route.fulfill(response=r, json=j)
    await pg.route("**/api/engine", engine_with_runtime)

    await pg.goto(f"http://127.0.0.1:{port}/app#k={token}")
    # 1. welcome: the name is asked once, empty (never guessed), and the memory gets a name
    check(await wait_for("!document.getElementById('wl-p1').classList.contains('hidden')"), "welcome asks for a name")
    check(await pg.input_value("#wl-name") == "", "the name field starts empty (nothing guessed)")
    await shot("welcome")
    await pg.fill("#wl-name", "Ana Souza")
    if await pg.is_visible("#wl-memory"):
        await pg.fill("#wl-memory", "Ana" if lang == "en-US" else "Ana")
    await pg.click("#wl-go")
    # 2. pick AIs: the ones on this computer are pre-marked; add Grok
    check(await wait_for("!document.getElementById('wl-p2').classList.contains('hidden')"), "then: which AIs")
    on = await pg.evaluate("[...document.querySelectorAll('#wl-pick .wl-chip.on')].map((b) => b.dataset.k)")
    check("claude-code" in on, f"Claude Code pre-marked (found on this computer): {on}")
    for k in on:
        if k != "claude-code":
            await pg.click(f'#wl-pick .wl-chip[data-k="{k}"]')
    await pg.click('#wl-pick .wl-chip[data-k="grok"]')
    await shot("pick")
    await pg.click("#wl-go2")
    # 3. Claude Code: connect, the copy-and-paste way out is offered right away, then its first message ticks it
    check(await wait_for("!!document.getElementById('wl-connect')"), "Claude Code: a Connect button")
    await shot("connect")
    await pg.click("#wl-connect")
    check(await wait_for("!!document.getElementById('wl-wait')"), "waiting for its first message")
    check(await wait_for("!!document.getElementById('wl-skipc') && document.getElementById('wl-skipc').offsetParent !== null", 4000),
          "copy and paste offered while waiting (not after 90 seconds)")
    await shot("waiting")
    await first_message(port, token, "claude-code")
    check(await wait_for("document.getElementById('wl-wait') && document.getElementById('wl-wait').classList.contains('ok')", 12000), "turns connected by itself")
    # 4. bring what it knows: found on this computer
    check(await wait_for("!!document.querySelector('[data-bk=auto]')", 20000), "finds what Claude Code keeps on this computer")
    await shot("bring-found")
    await pg.click('[data-bk="auto"]')
    check(await wait_for("!!document.querySelector('[data-bk=next]')", 30000), "saves it")
    await shot("bring-saved")
    await pg.click('[data-bk="next"]')
    check(await wait_for("/Grok/.test(document.getElementById('wl-ai').textContent)", 15000), "on to Grok")
    # 5. Grok: copy the question, paste its answer, review, save
    check(await wait_for("!!document.querySelector('[data-bk=copy]')", 15000), "Grok: copy the question")
    await pg.click('[data-bk="copy"]')
    check(await wait_for("!document.getElementById('coach').classList.contains('hidden')"), "the coach says where to paste it")
    coach = await pg.inner_text("#coach")
    check("grok.com" in (await pg.evaluate("[...document.querySelectorAll('#coach a')].map((a) => a.href).join(' ')")), "the coach opens grok.com")
    await shot("grok-copy")
    await pg.evaluate("(t) => { const d = new DataTransfer(); d.setData('text/plain', t); document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: d, bubbles: true})); }", GROK[lang])
    check(await wait_for("!!document.querySelector('[data-bk=save]')"), "its answer turns into notes to review")
    items = await pg.evaluate("[...document.querySelectorAll('.bk-list li')].map((l) => l.textContent)")
    check(len(items) == 3 and not any("Let me know" in i or "Querés" in i or "**" in i for i in items), f"only the facts, without the chat around them: {items}")
    await shot("grok-review")
    await pg.click('[data-bk="save"]')
    # 6. give Grok your memory
    check(await wait_for("!!document.getElementById('wl-copyuse')", 15000), "Grok: give it your memory")
    await pg.click("#wl-copyuse")
    check(await wait_for("/Grok/.test(document.getElementById('coach').innerText) && /Custom instructions|Instrucciones personalizadas/.test(document.getElementById('coach').innerText)"), "says where in Grok to paste it")
    await shot("grok-give")
    await pg.click("#wl-pasted")
    # 7. the map
    check(await wait_for("location.hash.startsWith('#graph')", 15000), "the first run ends on the map")
    await pg.wait_for_timeout(3000)
    await shot("map-first")
    st = await pg.evaluate("window.MnemosGraph._state()")
    check([r.get("label") for r in st.get("rings", [])] == ["Ana"], f"the map names the memory as you did: {[r.get('label') for r in st.get('rings', [])]}")
    junk = [x for x in st.get("labels", []) if re.fullmatch(r"imp-[0-9a-f]{8}|About|Sobre", str(x))]
    check(not junk, f"no import codes or filler words as topics: {junk}")
    # 8. save a note, search it, correct it
    await pg.evaluate("location.hash='memoria'"); await pg.wait_for_timeout(2500)
    await shot("memory")
    check(await pg.is_visible("#me-q") and await pg.is_visible("#me-new"), "Memory has search and New note (a new memory too)")
    check(await wait_for("document.querySelectorAll('#me-list .me-item').length >= 3"), "Memory lists the notes")
    check("main" not in (await pg.inner_text("#me-ctx")), "spaces are shown by their names, not their keys")
    lst = await pg.inner_text("#me-list")
    check(not re.search(r"\bmcp\b", lst, re.I), "notes say which app wrote them in plain words (no 'mcp 0.1.0')")
    await pg.click("#me-new")
    await pg.fill("#me-dispute", "I live in Lisbon" if lang == "en-US" else "Ya no vivo en Porto: vivo en Lisboa")
    await pg.click("#me-dispute-go")
    check(await wait_for("!!document.querySelector('#me-proposals [data-apply]')", 20000), "a correction finds the note it changes (Porto)")
    await shot("correct")
    await pg.locator("#me-proposals [data-apply]").first.click(); await pg.wait_for_timeout(2500)
    await pg.fill("#me-q", "Lisbon" if lang == "en-US" else "Lisboa"); await pg.wait_for_timeout(1500)
    check(await wait_for("/Lisbon|Lisboa/.test(document.getElementById('me-list').textContent)"), "search finds the corrected note")
    await pg.fill("#me-q", "Porto"); await pg.wait_for_timeout(1500)
    check(await wait_for("!/live in Porto|Vivís en Porto/.test(document.getElementById('me-list').textContent)", 5000), "the old version is gone from the list")
    await pg.fill("#me-q", "")
    # save a brand-new note
    await pg.fill("#me-dispute", "I play the cello on Sundays" if lang == "en-US" else "Toco el chelo los domingos")
    await pg.click("#me-dispute-go")
    check(await wait_for("!!document.getElementById('me-save-new')", 20000), "a new fact can be saved as a new note")
    await pg.click("#me-save-new")
    check(await wait_for("/cello|chelo/.test(document.getElementById('me-list').textContent)", 15000), "the new note shows in the list")
    await shot("search")
    # 9. split into two spaces
    await pg.evaluate("location.hash='spaces'"); await pg.wait_for_timeout(2000)
    await shot("spaces-one")
    await pg.fill("#sp-new-label", "Work" if lang == "en-US" else "Trabajo")
    await pg.fill("#sp-new-desc", "my job: clients, meetings, deadlines" if lang == "en-US" else "mi trabajo: clientes, reuniones, entregas")
    await pg.click("#sp-add")
    check(await wait_for("document.querySelectorAll('#sp-list .sp-row, #sp-list > *').length >= 2", 20000), "a second space appears")
    await pg.wait_for_timeout(1500)
    await shot("spaces-two")
    # a work fact saved into the new space (as an AI would), so the map splits in two
    work = await pg.evaluate("""async () => {
      const sp = await window.MNEMOS_API('/api/spaces');
      const w = (sp.spaces || []).find((x) => x.name !== 'main' && x.name !== 'shared');
      if (!w) return null;
      try { await window.MNEMOS_API('/api/memory/save', { context: w.name, text: 'Client Northwind wants the report by Friday' }); return w.name; }
      catch (e) { return 'save ' + e.message; } }""")
    check(bool(work) and not str(work).startswith("save"), f"a note can be saved into the new space: {work}")
    # 10. the map with two spaces, the memory-quality screen, the model download screen, the Apps tab
    await pg.evaluate("location.hash='memoria'"); await pg.wait_for_timeout(800)
    await pg.evaluate("location.hash='graph'"); await pg.wait_for_timeout(3500)
    st = await pg.evaluate("window.MnemosGraph._state()")
    check(len(st.get("rings", [])) == 2, f"the map shows both spaces: {[r.get('label') for r in st.get('rings', [])]}")
    await shot("map-two")
    await pg.evaluate("location.hash='settings'"); await pg.wait_for_timeout(2000)
    check(await pg.is_visible("#mq-card"), "memory quality is on Settings")
    await pg.locator("#mq-card").scroll_into_view_if_needed()
    await shot("quality")
    check(await pg.is_visible("#st-dl"), "the private-model download is offered (not clicked: a multi-GB download)")
    await pg.locator("#st-dl").scroll_into_view_if_needed()
    await shot("model")
    await pg.evaluate("location.hash='apps'"); await pg.wait_for_timeout(2500)
    check(await pg.locator('.cn-card[data-k="grok"] [data-give]').count() == 1, "the Grok card has both copy-and-paste steps")
    check(await pg.locator('.cn-card[data-k="claude-code"] .pill.ok').count() == 1, "Claude Code shows Connected")
    await shot("apps")
    await pg.locator('.cn-card[data-k="chatgpt"]').scroll_into_view_if_needed() if await pg.locator('.cn-card[data-k="chatgpt"]').count() else None
    check(not errors, f"no script errors: {errors[:3]}")
    await ctx.close()


async def main(shots=None):
    from playwright.async_api import async_playwright
    fails, log = [], []
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for i, (w, h, lang) in enumerate(SIZES):
            port = 8771 + i
            root, home, scan, srv = start(port)
            try:
                await journey(b, port, (home / "token").read_text().strip(), w, h, lang, shots, fails, log)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{w}-{lang}: stopped: {e}")
            finally:
                srv.terminate(); srv.wait(10)
                if fails:
                    print((root / "serve.log").read_text()[-3000:])
                shutil.rmtree(root, ignore_errors=True)
        await b.close()
    print("\n".join(log))
    if fails:
        print("\nJOURNEY FAILED:\n" + "\n".join(fails)); return 1
    print("journey: OK"); return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))
