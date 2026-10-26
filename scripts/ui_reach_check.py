"""Click-reachability check for the local app: on every tab, at desktop sizes, every visible button, link, field,
select and menu item must be on top at its own center (document.elementFromPoint), so nothing sits behind a header,
a card, a toolbar or a dropdown. Every collapsed "Advanced" section is opened first, so what it hides is checked too.

Usage: python scripts/ui_reach_check.py http://127.0.0.1:8765 <token> [shots_dir]
Needs: pip install playwright && playwright install chromium. Exit 1 lists every unreachable element.
"""
import asyncio
import sys

# the four tabs, then the pages inside Settings
TABS = ["graph", "memoria", "apps", "settings", "spaces", "native", "import", "skills"]
SIZES = [(1280, 800), (1512, 982)]
JS = r"""
() => {
  const sel = 'button, a[href], select, input:not([type=hidden]), textarea, summary, [role=tab], [onclick]';
  const bad = [], seen = [];
  const vis = (el) => {
    if (el.closest('details:not([open]) > :not(summary)')) return false;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') return false;
    const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2 && el.getClientRects().length;
  };
  // an open dialog covers the page on purpose: then only the dialog and the copy coach count
  const modal = document.querySelector('.bk-modal:not(.hidden)');
  for (const el of document.querySelectorAll(sel)) {
    if (modal && !modal.contains(el) && !el.closest('#coach')) continue;
    if (!vis(el) || el.closest('.hidden,[hidden],#g-node,canvas')) continue;
    // check where the user sees it: only scroll when it is outside the window
    // inside a scrolling list (Memory notes): bring the row into that list's view first
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const o = getComputedStyle(a).overflowY;
      if ((o === 'auto' || o === 'scroll') && a.scrollHeight > a.clientHeight) {
        const ar = a.getBoundingClientRect(), er = el.getBoundingClientRect();
        if (er.top < ar.top || er.bottom > ar.bottom) a.scrollTop += er.top - ar.top - a.clientHeight / 2;
        break;
      }
    }
    let r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > innerHeight) { el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); r = el.getBoundingClientRect(); }
    const x = r.left + Math.min(r.width / 2, 40);
    if (x < 0 || r.top < 0 || x > innerWidth || r.bottom > innerHeight) continue;
    const mine = (hit) => hit && (hit === el || el.contains(hit) || (hit.tagName === 'LABEL' && hit.contains(el)) ||
                       (el.labels && [...el.labels].some((l) => l === hit || l.contains(hit))));
    // center, and near the top and bottom edges: a half-clipped control fails too
    let hit = null, ok = true;
    for (const y of [r.top + r.height / 2, r.top + Math.min(3, r.height / 4), r.bottom - Math.min(3, r.height / 4)]) {
      const h = document.elementFromPoint(x, y); if (!mine(h)) { ok = false; hit = h; break; }
    }
    seen.push(1);
    if (!ok) {
      const name = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.id || el.tagName).trim().slice(0, 40);
      const by = hit ? (hit.id ? '#' + hit.id : hit.tagName.toLowerCase() + '.' + [...hit.classList].join('.')) : 'nothing';
      bad.push(`${el.tagName.toLowerCase()} "${name}" covered by ${by}`);
    }
  }
  return {bad, n: seen.length};
}
"""
# the header (with the four tabs) must stay on screen and clickable after scrolling to the bottom of any page
HEADER_JS = r"""
() => {
  scrollTo(0, document.documentElement.scrollHeight);
  const tabs = document.getElementById('tabs');
  if (!tabs || tabs.classList.contains('hidden')) return 'tabs hidden';
  for (const t of tabs.querySelectorAll('.tab')) {
    const r = t.getBoundingClientRect();
    if (r.top < 0 || r.bottom > innerHeight) return `tab "${t.textContent.trim()}" off screen`;
    const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!(h === t || t.contains(h))) return `tab "${t.textContent.trim()}" covered`;
  }
  return '';
}
"""


async def main(base, tok, shots=None):
    from playwright.async_api import async_playwright
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for w, h in SIZES:
            ctx = await b.new_context(viewport={"width": w, "height": h}, permissions=["clipboard-read", "clipboard-write"])
            pg = await ctx.new_page()
            await pg.goto(f"{base}/app#k={tok}")
            await pg.wait_for_timeout(1500)
            for t in TABS:
                await pg.evaluate(f"location.hash='{t}'")
                await pg.wait_for_timeout(2500)
                await pg.evaluate("scrollTo(0,0)")
                await pg.evaluate("document.querySelectorAll('.view:not(.hidden) details').forEach((d) => { d.open = true; })")
                await pg.wait_for_timeout(300)
                if shots:
                    await pg.screenshot(path=f"{shots}/reach-{t}-{w}.png")
                r = await pg.evaluate(JS)
                fails += [f"{w}x{h} #{t}: {x}" for x in r["bad"]]
                hb = await pg.evaluate(HEADER_JS)
                if hb:
                    fails.append(f"{w}x{h} #{t} header after scrolling down: {hb}")
                await pg.evaluate("scrollTo(0,0)")
                if t == "graph":  # two spaces sit side by side, not with a big empty gap between them
                    rings = (await pg.evaluate("window.MnemosGraph._state()")).get("rings") or []
                    for i, a in enumerate(rings):
                        for b2 in rings[i + 1:]:
                            gap = ((a["x"] - b2["x"]) ** 2 + (a["y"] - b2["y"]) ** 2) ** 0.5 - a["r"] - b2["r"]
                            if gap > 0.6 * min(a["r"], b2["r"]):
                                fails.append(f"{w}x{h} #graph: {gap:.0f} empty units between {a['name']} and {b2['name']}")
                print(f"{w}x{h} #{t}: {r['n']} elements, {len(r['bad'])} unreachable", flush=True)
            # Memory → Suggestions opens every decision (0.19): all reachable, and the notes stay right below
            await pg.evaluate("location.hash='memoria'"); await pg.wait_for_timeout(2500)
            await pg.evaluate("scrollTo(0,0)"); await pg.locator("#me-sugg").click(); await pg.wait_for_timeout(800)
            if await pg.evaluate("document.getElementById('me-review').classList.contains('hidden')"):
                fails.append(f"{w}x{h} memoria: Suggestions did not open the review")
            r = await pg.evaluate(JS)
            fails += [f"{w}x{h} memoria review: {x}" for x in r["bad"]]
            if shots:
                await pg.screenshot(path=f"{shots}/reach-memoria-review-{w}.png")
            await pg.evaluate("scrollTo(0,0)"); await pg.locator("#me-sugg").click(); await pg.wait_for_timeout(300)
            # the copy coach: open "Bring what it knows" for a copy-and-paste AI, copy, and check the notice, the
            # pulsing paste box and the dialog are all clickable, then after a paste the review and its Save button
            await pg.evaluate("location.hash='apps'"); await pg.wait_for_timeout(2500)
            if await pg.locator("[data-paste]").count():
                await pg.locator("[data-paste]").last.click(); await pg.wait_for_timeout(2500)
                await pg.locator('[data-bk="copy"]').click(); await pg.wait_for_timeout(800)
                r = await pg.evaluate(JS)
                vis = await pg.evaluate("!document.getElementById('coach').classList.contains('hidden') && !!document.querySelector('.paste-pulse')")
                if not vis:
                    fails.append(f"{w}x{h} coach: no notice or no pulsing paste box after copying")
                if shots:
                    await pg.screenshot(path=f"{shots}/reach-coach-{w}.png")
                fails += [f"{w}x{h} coach: {x}" for x in r["bad"]]
                print(f"{w}x{h} coach: {r['n']} elements, {len(r['bad'])} unreachable", flush=True)
                await pg.evaluate("""() => { const d = new DataTransfer(); d.setData('text/plain', ['- I live in Porto and work as a teacher', '- I prefer short answers in Spanish'].join(String.fromCharCode(10)));
                    document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: d, bubbles: true})); }""")
                await pg.wait_for_timeout(2000)
                r = await pg.evaluate(JS)
                if not await pg.locator('[data-bk="save"]').count():
                    fails.append(f"{w}x{h} coach: a paste anywhere did not reach the review")
                if shots:
                    await pg.screenshot(path=f"{shots}/reach-coach-review-{w}.png")
                fails += [f"{w}x{h} coach review: {x}" for x in r["bad"]]
                await pg.locator("#bk-close").click()
            await pg.close()
        await b.close()
    for f in fails:
        print("UNREACHABLE", f)
    print("reach check:", "OK" if not fails else f"{len(fails)} problems")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:4])))
