"""App memories (0.13): each AI app's own memory, shown exactly as that app stores it. Read-only on every file.

Claude Code  ~/.claude/CLAUDE.md and other ~/.claude/*.md, ~/.claude/rules/*.md, ~/.claude/projects/*/memory/*.md,
             CLAUDE.md files in code folders
Codex        ~/.codex/AGENTS.md, ~/.codex/memories/**, AGENTS.md files in code folders
Cursor       ~/.cursor/rules/*.mdc, ~/.cursor/plugins/local/*/rules/*.mdc, .cursorrules / .cursor/rules in code folders
Exports      ChatGPT / Claude.ai data exports (.zip) in ~/Downloads: the conversation list as stored in the export

0.17: a file can also be compared with Mnemos (which lines Mnemos already knows) and edited. Every write needs the
user's explicit confirm, refuses if the file changed since it was shown, and copies the current file to
<home>/data/native-backups/ first, so "Restore" can put any earlier version back.

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


_WORD = re.compile(r"[\w']{3,}", re.UNICODE)


def _words(s: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(s)}


def compare_lines(text: str, notes: list[str], threshold: float = 0.5) -> dict[str, Any]:
    """Each meaningful line (not headings, code fences or very short) → known by Mnemos or only in this file."""
    nsets = [(n, _words(n)) for n in notes if n]
    out, fence = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fence = not fence
            continue
        if fence or not line or _HEAD.match(line) or line.startswith(("---", "<!--")) or "[hidden by Mnemos" in line:
            continue
        clean = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
        w = _words(clean)
        if len(w) < 3:
            continue
        best, score = "", 0.0
        for n, ns in nsets:
            if not ns:
                continue
            sc = len(w & ns) / len(w)  # share of the line's words found in the note
            if sc > score:
                best, score = n, sc
        out.append({"text": clean, "known": score >= threshold, "score": round(score, 2),
                    "match": best[:200] if score >= threshold else ""})
    k = sum(1 for x in out if x["known"])
    return {"lines": out, "known": k, "only_here": len(out) - k}


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
        body = await asyncio.to_thread(_text, Path(path))  # re-read: the file may have changed
        if b.get("line"):  # one line from "Compare with Mnemos": it must still be in the file
            text = str(b["line"]).strip()[:4000]
            if text not in body:
                raise ValueError("that line is gone (refresh)")
        else:
            secs = sections(body)
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

    # ------------------------------------------------ 0.17: compare, edit (backup first), restore
    def _editable(self, path: str) -> Path:
        if path not in self.allowed:
            raise ValueError("list the app memories again first")
        p = Path(path)
        if not p.is_file() or "mnemos" in p.name.lower():
            raise ValueError("this file can't be edited here")
        return p

    def _bdir(self) -> Path:
        d = Path(self.ui.home) / "data" / "native-backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _index(self) -> dict:
        try:
            return json.loads((self._bdir() / "index.json").read_text())
        except (OSError, ValueError):
            return {}

    def _backup(self, p: Path, why: str) -> dict:
        import shutil
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dst = self._bdir() / f"{stamp}-{p.name}"
        shutil.copy2(p, dst)
        idx = self._index()
        rec = {"file": dst.name, "at": dt.datetime.now().isoformat(timespec="seconds"), "why": why}
        idx.setdefault(str(p), []).insert(0, rec)
        idx[str(p)] = idx[str(p)][:20]
        (self._bdir() / "index.json").write_text(json.dumps(idx, indent=1))
        return rec

    @staticmethod
    def _write(p: Path, text: str) -> None:
        import os
        tmp = p.with_name(f".{p.name}.mnemos-tmp")
        tmp.write_text(text)
        os.replace(tmp, p)

    async def save(self, b: dict) -> dict:
        import asyncio
        p = self._editable(str(b.get("path", "")))
        if b.get("confirm") is not True:
            raise ValueError("confirm the change first")
        text = str(b.get("text", ""))
        if len(text) > MAX_FILE_BYTES:
            raise ValueError("too long")
        if "[hidden by Mnemos" in text:
            raise ValueError("the file has hidden secret lines: edit it in its own app")
        if b.get("modified") and _stamp(p) != b["modified"]:
            raise ValueError("the file changed since it was shown: refresh and try again")
        rec = await asyncio.to_thread(self._backup, p, "before edit")
        await asyncio.to_thread(self._write, p, text if text.endswith("\n") else text + "\n")
        return {"saved": True, "backup": rec, "modified": _stamp(p)}

    async def backups(self, b: dict) -> dict:
        return {"backups": self._index().get(str(b.get("path", "")), [])}

    async def restore(self, b: dict) -> dict:
        import asyncio
        p = self._editable(str(b.get("path", "")))
        if b.get("confirm") is not True:
            raise ValueError("confirm the change first")
        rec = next((r for r in self._index().get(str(p), []) if r["file"] == b.get("file")), None)
        if not rec:
            raise ValueError("that backup is gone")
        old = (self._bdir() / rec["file"]).read_text()
        cur = await asyncio.to_thread(self._backup, p, "before restore")
        await asyncio.to_thread(self._write, p, old)
        return {"restored": rec["at"], "backup": cur, "modified": _stamp(p)}

    async def compare(self, b: dict) -> dict:
        """Which lines of this file Mnemos already knows (word overlap with any note in any space)."""
        import asyncio
        path = str(b.get("path", ""))
        if path not in self.allowed:
            raise ValueError("list the app memories again first")
        notes: list[str] = []
        for ctx in self.ui.servers:
            r = await self.ui.action("list", {"context": ctx, "include_shared": True})
            notes += [str(i.get("text", "")) for i in r.get("items", [])]
        text = await asyncio.to_thread(_text, Path(path))
        return compare_lines(text, notes)

    def install(self) -> None:
        self.ui.hooks[("POST", "native/save")] = self.save
        self.ui.hooks[("POST", "native/backups")] = self.backups
        self.ui.hooks[("POST", "native/restore")] = self.restore
        self.ui.hooks[("POST", "native/compare")] = self.compare
        self.ui.hooks[("GET", "native")] = self.get
        self.ui.hooks[("POST", "native/import")] = self.import_one
