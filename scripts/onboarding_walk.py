"""First-run self-test: a new user with Codex (connected, detected by its first call) and Grok (copy and paste), end to end,
timed, with a screenshot of every screen. Run against a fresh `serve --home H` started with MNEMOS_SCAN_HOME=S and S/.codex/.
Usage: python scripts/onboarding_walk.py <port> <token> <width> <height> <locale> <shots_dir> <S/.codex/config.toml>
"""
import asyncio,sys,time,subprocess,os
from playwright.async_api import async_playwright

port,tok,W,H,lang,out,CFG=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),sys.argv[5],sys.argv[6],sys.argv[7]
PY=sys.executable; HERE=os.path.dirname(os.path.abspath(__file__))
log=[]
async def main():
    t0=time.time()
    def mark(s): log.append((round(time.time()-t0,1),s)); print(log[-1],flush=True)
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"])
        ctx=await b.new_context(viewport={"width":W,"height":H},locale=lang); await ctx.grant_permissions(["clipboard-read","clipboard-write"])
        pg=await ctx.new_page()
        n=[0]
        async def shot(name): n[0]+=1; await pg.screenshot(path=f"{out}/{n[0]:02d}-{name}-{W}-{lang[:2]}.png")
        await pg.goto(f"http://127.0.0.1:{port}/app#k={tok}"); await pg.wait_for_selector("#wl-name",state="visible"); mark("welcome"); await shot("name")
        await pg.fill("#wl-name","Alex"); await pg.click("#wl-go")
        await pg.wait_for_selector("#wl-pick .wl-chip",state="visible"); await shot("pick")
        for k in await pg.eval_on_selector_all("#wl-pick .wl-chip.on","els=>els.map(e=>e.dataset.k)"):
            if k not in ("codex","grok"): await pg.click(f'#wl-pick [data-k="{k}"]')
        for k in ("codex","grok"):
            if not await pg.eval_on_selector(f'#wl-pick [data-k="{k}"]',"e=>e.classList.contains('on')"): await pg.click(f'#wl-pick [data-k="{k}"]')
        await shot("picked"); await pg.click("#wl-go2"); mark("picked codex+grok")
        await pg.wait_for_selector("#wl-connect",state="visible"); await shot("connect-codex"); await pg.click("#wl-connect")
        await pg.wait_for_selector("#wl-wait",state="visible"); await shot("waiting-codex"); mark("connect clicked")
        subprocess.run([PY,os.path.join(HERE,"mcp_firstcall.py"),CFG],capture_output=True,timeout=60); mark("codex first call")
        await pg.wait_for_selector("#wl-wait.ok",timeout=20000); await shot("codex-connected"); mark("detected")
        await pg.wait_for_selector('[data-bk="copy"]',state="visible",timeout=20000); await shot("bring-codex"); await pg.click('[data-bk="copy"]')
        await pg.wait_for_selector("#coach:not(.hidden)",timeout=5000); await shot("coach-codex")
        subprocess.run([PY,os.path.join(HERE,"mcp_firstcall.py"),CFG,"--save","Alex works as a product designer at a small studio","Alex prefers short answers with examples","Alex is learning Rust","Alex's sister Marta lives in Madrid","Alex runs every Saturday morning"],capture_output=True,timeout=60); mark("codex saved 5")
        await pg.wait_for_selector("#wl-ai:has-text('Grok')",timeout=30000); mark("codex done (auto default)")
        await pg.wait_for_selector('[data-bk="copy"]',state="visible",timeout=20000); await shot("bring-grok")
        await pg.click('[data-bk="copy"]'); await pg.wait_for_timeout(500); await shot("coach-grok")
        ans="- I'm Alex, a product designer\n- I play guitar in a band called Low Tide\n- I'm planning a trip to Japan in April\n- My dog is called Pixel\n- I don't eat meat"
        await pg.fill("#bk-paste",ans); await pg.dispatch_event("#bk-paste","input")
        await pg.wait_for_selector('[data-bk="save"]',timeout=15000); await shot("grok-review"); await pg.click('[data-bk="save"]'); mark("grok saved")
        await pg.wait_for_selector("#wl-copyuse",state="visible",timeout=15000); await shot("default-grok"); await pg.click("#wl-copyuse"); await pg.wait_for_timeout(500); await shot("coach-default-grok")
        await pg.click("#wl-pasted"); mark("grok default")
        await pg.wait_for_selector("#reveal:not(.hidden)",timeout=20000); mark("REVEAL"); await pg.wait_for_timeout(1500); await shot("reveal-start")
        await pg.wait_for_timeout(6500); await shot("reveal-grown"); await pg.click("#rv-go"); await pg.wait_for_timeout(800); await shot("map")
        for h in ("#memoria","#apps","#settings"):
            await pg.evaluate(f"location.hash='{h}'"); await pg.wait_for_timeout(1800); await shot(h[1:])
        await b.close()
    print("TOTAL", log[-1][0] if log else None)
asyncio.run(main())
