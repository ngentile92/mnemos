"""App memories (0.13): each AI app's own memory, shown exactly as that app stores it. Read-only on every file.

Claude Code  ~/.claude/CLAUDE.md and other ~/.claude/*.md, ~/.claude/rules/*.md, ~/.claude/projects/*/memory/*.md,
             CLAUDE.md files in code folders
Codex        ~/.codex/AGENTS.md, ~/.codex/memories/**, AGENTS.md files in code folders
Cursor       ~/.cursor/rules/*.mdc, ~/.cursor/plugins/local/*/rules/*.mdc, .cursorrules / .cursor/rules in code folders
Exports      ChatGPT / Claude.ai data exports (.zip) in ~/Downloads: the conversation list as stored in the export

Files Mnemos writes itself (local_sync) are marked as such. Lines that look like secrets are hidden and files with
secret-like names are never opened. "Import into Mnemos" saves one section as a note (sorted into a space by its
content unless the user picks one); only paths from the last listing can be read.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from .local_discover import CODE_DIRS, MAX_FILE_BYTES, _rules_file, _walk, has_secret, secret_name

APPS = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "exports": "Chat exports"}
MAX_SHOW = 60_000
_HEAD = re.compile(r"^(#{1,4})\s+(.+)$")


def _stamp(p: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")
    except OSError:
        return ""


def _text(p: Path) -> str:
    try:
        raw = p.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join("[hidden by Mnemos: looks like a secret]" if has_secret(x) else x for x in raw.splitlines())


def sections(text: str) -> list[dict]:
    """Split a markdown file at its headings (the file as a whole when it has none)."""
    out: list[dict] = []
    cur = {"title": "", "lines": []}
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m and cur["lines"] and any(x.strip() for x in cur["lines"]):
            out.append(cur)
            cur = {"title": m.group(2).strip(), "lines": [line]}
        else:
            if m and not cur["title"]:
                cur["title"] = m.group(2).strip()
            cur["lines"].append(line)
    if any(x.strip() for x in cur["lines"]):
        out.append(cur)
    return [{"title": s["title"] or "(top)", "text": "\n".join(s["lines"]).strip()} for s in out]


def _files(home: Path, deadline: float) -> dict[str, list[Path]]:
    c, x, cu = home / ".claude", home / ".codex", home / ".cursor"
    got: dict[str, list[Path]] = {"claude-code": [], "codex": [], "cursor": []}
    if c.is_dir():
        got["claude-code"] += sorted(p for p in c.glob("*.md") if p.is_file())
        got["claude-code"] += sorted(p for p in (c / "rules").glob("*.md") if p.is_file())
        if (c / "projects").is_dir():
            got["claude-code"] += sorted(p for d in (c / "projects").iterdir() if d.is_dir()
                                         for p in (d / "memory").glob("*.md") if p.is_file())
    if x.is_dir():
        if (x / "AGENTS.md").is_file():
            got["codex"].append(x / "AGENTS.md")
        if (x / "memories").is_dir():
            got["codex"] += _walk(x / "memories", lambda p: p.suffix.lower() in (".md", ".txt", ".json"), deadline, 3)
    if cu.is_dir():
        got["cursor"] += sorted(p for p in (cu / "rules").glob("*.mdc") if p.is_file())
        if (cu / "plugins" / "local").is_dir():
            got["cursor"] += sorted((cu / "plugins" / "local").glob("*/rules/*.mdc"))
    for root in [home / d for d in CODE_DIRS if (home / d).is_dir()]:
        for p in _walk(root, _rules_file, deadline):
            got["claude-code" if p.name == "CLAUDE.md" else "codex" if p.name == "AGENTS.md" else "cursor"].append(p)
    return {k: [p for p in dict.fromkeys(v) if not secret_name(p)] for k, v in got.items()}


def _exports(home: Path, deadline: float) -> list[dict]:
    from .local_discover import _zip_kind
    out = []
    dl = home / "Downloads"
    for z in sorted(dl.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:50] if dl.is_dir() else []:
        if time.monotonic() > deadline:
            break
        k = _zip_kind(z)
        if not k:
            continue
        convs = []
        try:
            with zipfile.ZipFile(z) as zf:
                name = next((n for n in zf.namelist() if n.endswith("conversations.json")), None)
                if name and zf.getinfo(name).file_size < 200_000_000:
                    data = json.loads(zf.read(name))
                    for cv in data if isinstance(data, list) else []:
                        t = str(cv.get("title") or cv.get("name") or "(untitled)")[:200]
                        when = cv.get("create_time") or cv.get("created_at") or ""
                        if isinstance(when, (int, float)):
                            when = dt.datetime.fromtimestamp(when).isoformat(timespec="minutes")
                        convs.append({"title": t, "text": f"{t} · {str(when)[:16]}"})
        except (OSError, ValueError, zipfile.BadZipFile, KeyError):
            continue
        out.append({"path": str(z), "kind": "ChatGPT export" if k == "chatgpt_zip" else "Claude.ai export",
                    "modified": _stamp(z), "size": z.stat().st_size, "mnemos": False, "readonly_list": True,
                    "sections": [{"title": c["title"], "text": c["text"]} for c in convs[:300]],
                    "note": f"{len(convs)} conversations in this export (titles as stored; import them from Import)"})
    return out


def listing(home: Path | None = None, budget: float = 10.0) -> dict[str, Any]:
    home = Path(home or Path.home())
    deadline = time.monotonic() + budget
    apps = []
    for key, paths in _files(home, deadline).items():
        items = []
        for p in paths:
            t = _text(p)
            mine = "mnemos" in p.name.lower() or "<!-- mnemos:start" in t
            items.append({"path": str(p), "where": str(p).replace(str(home), "~", 1), "modified": _stamp(p),
                          "size": len(t), "mnemos": mine, "kind": p.name, "content": t[:MAX_SHOW],
                          "truncated": len(t) > MAX_SHOW, "sections": sections(t)})
        apps.append({"key": key, "label": APPS[key], "items": items})
    ex = _exports(home, deadline)
    for e in ex:
        e["where"] = e["path"].replace(str(home), "~", 1)
        e["content"] = "\n".join(s["text"] for s in e["sections"])
    apps.append({"key": "exports", "label": APPS["exports"], "items": ex})
    return {"apps": apps, "scanned_at": dt.datetime.now().isoformat(timespec="seconds"),
            "timed_out": time.monotonic() > deadline}


class Native:
    def __init__(self, ui: Any, home_dir: Path | None = None, engine: Any = None) -> None:
        self.ui, self.home_dir, self.engine = ui, home_dir, engine
        self.allowed: dict[str, list[dict]] = {}

    def _home(self) -> Path:
        import os
        return Path(self.home_dir or os.environ.get("MNEMOS_SCAN_HOME") or Path.home())

    async def get(self, _b: dict) -> dict:
        import asyncio
        res = await asyncio.to_thread(listing, self._home())
        self.allowed = {it["path"]: it["sections"] for a in res["apps"] for it in a["items"] if not it.get("readonly_list")}
        return res

    async def import_one(self, b: dict) -> dict:
        import asyncio
        from . import local_route
        path, i = str(b.get("path", "")), int(b.get("section", -1))
        if path not in self.allowed:
            raise ValueError("list the app memories again first")
        secs = sections(await asyncio.to_thread(_text, Path(path)))  # re-read: the file may have changed
        if not 0 <= i < len(secs):
            raise ValueError("that section is gone (refresh)")
        text = secs[i]["text"].strip()[:4000]
        if len(text) < 8 or "[hidden by Mnemos" in text:
            raise ValueError("nothing to import (or it contains a hidden secret)")
        ctx = str(b.get("context") or "auto")
        r = {"space": ctx, "reason": "you chose this space", "by": "you"}
        if ctx == "auto" or ctx not in self.ui.servers:
            llm = await asyncio.to_thread(self.engine.llm, "import") if self.engine is not None else None
            r = await local_route.route(text, local_route.spaces_of(self.ui.home, self.ui.servers),
                                        next(iter(self.ui.servers)), llm=llm, home=self.ui.home, cwd=str(Path(path).parent))
        app = re.sub(r"[^a-z-]", "", str(b.get("app") or "app"))[:20]
        saved = await self.ui.call(r["space"], "memory_save", {"text": text, "tags": ["import-native", f"via-{app}"]})
        return {"saved": saved.get("status") != "duplicate", "id": saved.get("id"), "context": r["space"], "route": r}

    def install(self) -> None:
        self.ui.hooks[("GET", "native")] = self.get
        self.ui.hooks[("POST", "native/import")] = self.import_one
