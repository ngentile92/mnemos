"""Live capture, warm lane (v0.8): once a coding session banked by the hooks (local_spool) goes quiet, read it
with the import model and keep what lasts, without asking the user about each fact.

  - one call per window of the session (memfilter.extract_window: typed labels + the deterministic write filter)
  - already known (words) → skipped; looks like it contradicts a note → Inbox ("conflict")
  - clear durable facts (importance ≥ 4, or ≥ 3 and permanent/long; identity, preference, relationship, goal,
    project_state, decision) → saved straight away and listed in "Recently learned", each with Undo
  - the rest (time-bound, low confidence) → Inbox, ~20 a day at most; accept or dismiss
State: <home>/capture.json (turns read per session, recently learned, inbox). Saved notes carry tags
learned + via-<app> + kind-<kind> so the user can always find and undo them.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import local_import as li
from . import local_spool
from . import memfilter as mf

log = logging.getLogger("hub_gateway.capture")
IDLE_S = 15 * 60
BUSY_TURNS = 20
AUTO_KINDS = {"identity", "preference", "relationship", "goal", "project_state", "decision"}
INBOX_PER_DAY = 20
MAX_RECENT = 300
APP_KEYS = {"claude_code": "claude-code", "cursor": "cursor", "codex": "codex"}
LABELS = {"claude_code": "Claude Code", "cursor": "Cursor", "codex": "Codex"}


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:12]


def decide(f: dict) -> str:
    """auto | inbox for one gated fact."""
    if f.get("durability") == "short" or f.get("unsure"):
        return "inbox"
    if f.get("kind") in AUTO_KINDS and (f["importance"] >= 4 or (f["importance"] >= 3 and
                                                                 f["durability"] in ("permanent", "long"))):
        return "auto"
    return "inbox"


def _conflict(text: str, pool: list[str]) -> str | None:
    from .local_hygiene import lexical_conflict
    tok = li._tokens(text)
    for old in pool:
        ot = li._tokens(old)
        if tok and ot and len(tok & ot) / max(1, min(len(tok), len(ot))) >= 0.5 and lexical_conflict(text, old):
            return old
    return None


class Capture:
    def __init__(self, ui: Any, engine: Any, apps: Any = None) -> None:
        self.ui, self.engine, self.apps = ui, engine, apps
        self.file = Path(ui.home) / "capture.json"
        self.st = self._load()
        self.task: asyncio.Task | None = None
        self.busy = False

    def _load(self) -> dict:
        try:
            d = json.loads(self.file.read_text())
        except (OSError, ValueError):
            d = {}
        d.setdefault("read", {})  # spool path -> turns already read
        d.setdefault("recent", [])  # auto-accepted: {id, note_id, text, context, app, kind, quote, ts, status}
        d.setdefault("inbox", [])  # {id, text, context, app, kind, reason, conflict_with, quote, ts, status}
        d.setdefault("topics", {})
        return d

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st))
        os.replace(tmp, self.file)

    def _space(self, app: str) -> str:
        if self.apps is not None:
            try:
                rec = self.apps.get(APP_KEYS.get(app, app))
                if rec and (rec.get("paused") or rec.get("revoked")):
                    return ""
                sp = self.apps.default_for(rec)
                if sp in self.ui.servers:
                    return sp
            except Exception:  # noqa: BLE001
                pass
        return next(iter(self.ui.servers))

    def due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        out = []
        for s in local_spool.sessions(self.ui.home):
            done = int(self.st["read"].get(s["path"], 0))
            new = s["turns"] - done
            if new > 0 and (now - s["mtime"] >= IDLE_S or new >= BUSY_TURNS):
                out.append({**s, "from": done})
        return out

    def _inbox_today(self) -> int:
        day = time.strftime("%Y-%m-%d")
        return sum(1 for x in self.st["inbox"] if str(x.get("ts", "")).startswith(day))

    async def run_once(self, now: float | None = None) -> dict[str, int]:
        """Process every quiet session once. Returns counts."""
        res = {"sessions": 0, "learned": 0, "inbox": 0, "skipped": 0}
        due = self.due(now)
        if not due or self.busy:
            return res
        llm = await asyncio.to_thread(self.engine.llm, "import")
        if llm is None:
            return res
        self.busy = True
        try:
            for s in due:
                ctx = self._space(s["app"])
                turns = local_spool.read(s["path"])
                if not ctx:
                    self.st["read"][s["path"]] = len(turns)  # paused: drop, don't read later either
                    continue
                pool = await self.ui._existing(ctx)
                topic = self.st["topics"].get(s["path"], "")
                when = time.strftime("%Y-%m-%d", time.localtime(turns[-1]["ts"])) if turns else ""
                for w in mf.windows([t["text"] for t in turns[s["from"]:]]):
                    facts, topic = await mf.extract_window(llm, w, LABELS[s["app"]], when, topic)
                    for f in facts:
                        await self._place(f, ctx, s["app"], pool, res)
                self.st["topics"][s["path"]] = topic
                self.st["read"][s["path"]] = len(turns)
                res["sessions"] += 1
                self._save()
        finally:
            self.busy = False
        if res["learned"]:
            await self.refresh_profile(None)
        return res

    async def _place(self, f: dict, ctx: str, app: str, pool: list[str], res: dict) -> None:
        old = _conflict(f["text"], pool)  # "2 cats" vs "3 cats" looks like a duplicate in words: check first
        if not old and li.preview([f["text"]], pool)["new"] == 0:
            res["skipped"] += 1
            return
        base = {"id": _h(ctx + f["text"]), "text": f["text"], "context": ctx, "app": app, "kind": f.get("kind"),
                "quote": f.get("quote"), "topic": f.get("topic"), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "new"}
        if any(x["id"] == base["id"] for x in self.st["recent"] + self.st["inbox"]):
            res["skipped"] += 1
            return
        if old or decide(f) == "inbox":
            if self._inbox_today() >= INBOX_PER_DAY:
                res["skipped"] += 1
                return
            self.st["inbox"].append({**base, "reason": "conflict" if old else "unsure", "conflict_with": old,
                                     "expires_at": f.get("expires_at")})
            res["inbox"] += 1
            return
        tags = ["learned", f"via-{APP_KEYS.get(app, app)}"] + ([f"kind-{f['kind']}"] if f.get("kind") else [])
        r = await self.ui.call(ctx, "memory_save", {"text": f["text"], "tags": tags})
        if r.get("status") == "duplicate":
            res["skipped"] += 1
            return
        pool.append(f["text"])
        self.st["recent"] = (self.st["recent"] + [{**base, "note_id": r.get("id"), "status": "kept"}])[-MAX_RECENT:]
        res["learned"] += 1

    # ---- profile + nightly consolidation
    async def refresh_profile(self, _b: dict | None) -> dict:
        from . import local_consolidate, local_profile
        out = {}
        for ctx in self.ui.servers:
            txt = ""
            mf_ = getattr(self.ui, "model_for", None)
            if mf_:
                try:
                    m = mf_(ctx)
                    txt, _ids = local_consolidate.build_profile(m)
                except Exception:  # noqa: BLE001
                    txt = ""
            if not txt:
                items = (await self.ui.action("list", {"context": ctx, "include_shared": True, "limit": 500})).get("items", [])
                txt = local_profile.build(items)
            local_profile.write_cache(self.ui.home, ctx, txt)
            out[ctx] = len(txt)
        return {"profiles": out}

    async def consolidate_all(self, force: bool = False) -> dict:
        """Once a day (or on demand): consolidation per space, then refresh the cached profiles."""
        from . import local_consolidate
        last = float(self.st.get("consolidated_at") or 0)
        if not force and time.time() - last < 20 * 3600:
            return {}
        mf_ = getattr(self.ui, "model_for", None)
        if not mf_:
            return {}
        llm = await asyncio.to_thread(self.engine.llm, "hygiene") if hasattr(self.engine, "llm") else None
        out = {}
        for ctx in self.ui.servers:
            out[ctx] = await local_consolidate.consolidate(mf_(ctx), llm)
        self.st["consolidated_at"] = time.time()
        self.st.setdefault("jobs", []).append({ctx: r["job"] for ctx, r in out.items()})
        self.st["jobs"] = self.st["jobs"][-14:]
        self._save()
        await self.refresh_profile(None)
        return out

    # ---- hooks (UI)
    async def h_status(self, _b: dict) -> dict:
        return {"recent": [x for x in reversed(self.st["recent"]) if x["status"] == "kept"][:100],
                "inbox": [x for x in self.st["inbox"] if x["status"] == "new"][:50],
                "waiting": local_spool.counts(self.ui.home), "busy": self.busy}

    def _find(self, lst: str, i: str) -> dict:
        x = next((x for x in self.st[lst] if x["id"] == i), None)
        if not x:
            raise ValueError("not found (refresh)")
        return x

    async def h_undo(self, b: dict) -> dict:
        x = self._find("recent", str(b.get("id")))
        if x["status"] == "kept" and x.get("note_id"):
            await self.ui.call(x["context"], "memory_delete", {"id": x["note_id"]})
        x["status"] = "undone"
        self._save()
        return await self.h_status({})

    async def h_accept(self, b: dict) -> dict:
        x = self._find("inbox", str(b.get("id")))
        if x["status"] == "new":
            tags = ["learned", f"via-{APP_KEYS.get(x['app'], x['app'])}"] + ([f"kind-{x['kind']}"] if x.get("kind") else [])
            text = str(b.get("text") or x["text"]).strip()[:2000]
            r = await self.ui.call(x["context"], "memory_save", {"text": text, "tags": tags, "allow_duplicate": True})
            if x.get("expires_at") and r.get("id"):
                await self.ui.call(x["context"], "memory_set_validity", {"id": r["id"], "valid_until": x["expires_at"]})
            x["status"] = "accepted"
            self._save()
        return await self.h_status({})

    async def h_dismiss(self, b: dict) -> dict:
        x = self._find("inbox", str(b.get("id")))
        x["status"] = "dismissed"
        self._save()
        return await self.h_status({})

    async def h_run(self, _b: dict) -> dict:
        r = await self.run_once(now=time.time() + IDLE_S)  # "read now": treat every session as quiet
        return {**r, **(await self.h_status({}))}

    async def loop(self, every: float = 60.0) -> None:
        while True:
            try:
                await self.run_once()
                if time.localtime().tm_hour in (2, 3, 4, 5) or not self.st.get("consolidated_at"):
                    await self.consolidate_all()
            except Exception as e:  # noqa: BLE001
                log.warning("capture: %s", type(e).__name__)
            await asyncio.sleep(every)

    def start(self) -> None:
        if os.environ.get("MNEMOS_CAPTURE", "1") != "0" and not self.task:
            self.task = asyncio.create_task(self.loop())

    def install(self) -> None:
        h = self.ui.hooks
        h[("GET", "capture")] = self.h_status
        h[("POST", "capture/undo")] = self.h_undo
        h[("POST", "capture/accept")] = self.h_accept
        h[("POST", "capture/dismiss")] = self.h_dismiss
        h[("POST", "capture/run")] = self.h_run
        h[("POST", "capture/profile")] = self.refresh_profile
