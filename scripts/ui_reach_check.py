"""Click-reachability check for the local app: on every tab, at desktop sizes, every visible button, link, field,
select and menu item must be on top at its own center (document.elementFromPoint), so nothing sits behind a header,
a card, a toolbar or a dropdown. Also opens the More menu and checks its items.

Usage: python scripts/ui_reach_check.py http://127.0.0.1:8765 <token> [shots_dir]
Needs: pip install playwright && playwright install chromium. Exit 1 lists every unreachable element.
"""
import asyncio
import sys

TABS = ["home", "memoria", "native", "apps", "import", "spaces", "skills", "graph", "settings"]
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
  for (const el of document.querySelectorAll(sel)) {
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


async def main(base, tok, shots=None):
    from playwright.async_api import async_playwright
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for w, h in SIZES:
            pg = await b.new_page(viewport={"width": w, "height": h})
            await pg.goto(f"{base}/app#k={tok}")
            await pg.wait_for_timeout(1500)
            for t in TABS:
                await pg.evaluate(f"location.hash='{t}'")
                await pg.wait_for_timeout(2500)
                await pg.evaluate("scrollTo(0,0)")
                r = await pg.evaluate(JS)
                fails += [f"{w}x{h} #{t}: {x}" for x in r["bad"]]
                print(f"{w}x{h} #{t}: {r['n']} elements, {len(r['bad'])} unreachable", flush=True)
            # the More menu over every tab's content
            for t in ["import", "graph", "home"]:
                await pg.evaluate(f"location.hash='{t}'"); await pg.wait_for_timeout(1500); await pg.evaluate("scrollTo(0,0)")
                await pg.click("#tab-more > summary")
                await pg.wait_for_timeout(300)
                if shots and t == "import":
                    await pg.screenshot(path=f"{shots}/reach-more-{w}.png")
                r = await pg.evaluate("""() => [...document.querySelectorAll('#tab-more .tab-menu a, #tab-more .tab-menu button')].map((el) => {
                    const q = el.getBoundingClientRect(); const hit = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2);
                    return (hit === el || el.contains(hit)) ? '' : (el.innerText.trim() + ' covered by ' + (hit ? hit.tagName + '.' + hit.className : 'nothing')); }).filter(Boolean)""")
                fails += [f"{w}x{h} More menu over #{t}: {x}" for x in r]
                await pg.click("#tab-more > summary")
            await pg.close()
        await b.close()
    for f in fails:
        print("UNREACHABLE", f)
    print("reach check:", "OK" if not fails else f"{len(fails)} problems")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:4])))
