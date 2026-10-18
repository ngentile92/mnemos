"""Auto-discover import (local mode / Desktop): find what this computer already knows about you and turn it into
reviewed notes. Read-only on every source, bounded in time and size, nothing is written until the user approves.

Sources (macOS paths; Linux equivalents under ~/.config where they exist):
  claude_code   ~/.claude/projects/<project>/*.jsonl  — your own messages in Claude Code sessions
  claude_md     ~/.claude/*.md (CLAUDE.md, preferences) and CLAUDE.md files in code folders
  agents_md     AGENTS.md files in code folders
  cursor_rules  .cursorrules and .cursor/rules/*.mdc in code folders
  cursor_chats  Cursor's state.vscdb databases (your chat messages; opened read-only + immutable)
  obsidian      vaults listed in obsidian.json
  chatgpt_zip / claude_zip   data exports (.zip with conversations.json) in ~/Downloads

Extraction: instruction files and notes become notes per section; chats contribute only durable first-person
facts (same rule as the ChatGPT importer), or what a local Ollama model extracts when one is running. Anything
that looks like a secret is dropped and .env/key files are never read. Commit tags every note with the batch id,
so one click undoes the whole import (each note also keeps its own history).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import local_import as li

log = logging.getLogger("hub.discover")

CODE_DIRS = ("code", "dev", "projects", "src", "repos", "work", "Developer", "Documents/GitHub", "Documents/code")
MAX_DEPTH, MAX_FILES, MAX_FILE_BYTES, MAX_SOURCE_BYTES = 4, 2000, 2_000_000, 60_000_000
MAX_PER_SOURCE, SCAN_BUDGET_S, PREVIEW_BUDGET_S = 400, 15.0, 300.0
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", "Library"}
CODE_KINDS = {"claude_code", "claude_md", "agents_md", "cursor_rules", "cursor_chats"}
LABELS = {"claude_code": "Claude Code conversations", "claude_md": "Claude instructions (CLAUDE.md)",
          "agents_md": "AGENTS.md files", "cursor_rules": "Cursor rules", "cursor_chats": "Cursor chats",
          "obsidian": "Obsidian vault", "chatgpt_zip": "ChatGPT export", "claude_zip": "Claude.ai export"}

# ------------------------------------------------------------------ secrets
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"(?i:(?:api[_-]?key|secret|token|password|passwd|pwd|contraseña|clave|bearer|authorization)\s*[:=]\s*['\"]?[^\s'\"]{6,})|"
    r"\b[A-Fa-f0-9]{40,}\b|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s/]*:[^\s/]*@)")


def has_secret(text: str) -> bool:
    return bool(SECRET_RE.search(text))


def secret_name(p: Path) -> bool:
    n = p.name.lower()
    return n.startswith(".env") or n.endswith((".pem", ".key", ".p12")) or "secret" in n or "credential" in n


# ------------------------------------------------------------------ scan
def _sid(kind: str, key: str) -> str:
    return hashlib.sha1(f"{kind}:{key}".encode()).hexdigest()[:12]


def _walk(root: Path, match: Callable[[Path], bool], deadline: float, depth: int = MAX_DEPTH) -> list[Path]:
    out: list[Path] = []
    stack = [(root, 0)]
    while stack and len(out) < MAX_FILES and time.monotonic() < deadline:
        d, lvl = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    hidden = e.name.startswith(".") and e.name not in (".cursor",)
                    if lvl < depth and e.name not in SKIP_DIRS and not hidden:
                        stack.append((Path(e.path), lvl + 1))
                elif e.is_file(follow_symlinks=False) and match(Path(e.path)):
                    out.append(Path(e.path))
            except OSError:
                continue
    return out


def _zip_kind(p: Path) -> str | None:
    try:
        with zipfile.ZipFile(p) as z:
            name = next((n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json"), None)
            if not name:
                return None
            with z.open(name) as f:
                head = f.read(8192).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return None
    if '"chat_messages"' in head or '"sender"' in head:
        return "claude_zip"
    if '"mapping"' in head:
        return "chatgpt_zip"
    return None


def suggest(kind: str, contexts: list[str]) -> str:
    if not contexts:
        return ""
    want = ("work", "trabajo", "dev", "code") if kind in CODE_KINDS else ("personal", "me", "home", "vida")
    return next((c for c in contexts if c in want), contexts[0])


def _rules_file(p: Path) -> bool:
    return p.name in ("CLAUDE.md", "AGENTS.md", ".cursorrules") or (
        p.parent.name == "rules" and p.parent.parent.name == ".cursor" and p.suffix in (".mdc", ".md"))


def scan(home: Path | None = None, contexts: list[str] | None = None, budget: float = SCAN_BUDGET_S) -> list[dict]:
    """Sources found on this computer, with counts. Reads no file contents except zip headers."""
    home = Path(home or Path.home())
    deadline = time.monotonic() + budget
    found: list[dict] = []

    def add(kind: str, label: str, paths: list[Path], unit: str, key: str) -> None:
        paths = [p for p in dict.fromkeys(paths) if not secret_name(p)][:MAX_FILES]
        if not paths:
            return
        size = 0
        for p in paths:
            try:
                size += p.stat().st_size
            except OSError:
                pass
        found.append({"id": _sid(kind, key), "kind": kind, "label": label, "count": len(paths), "unit": unit,
                      "bytes": size, "where": key.replace(str(home), "~"), "paths": [str(p) for p in paths],
                      "context": suggest(kind, contexts or [])})

    proj = home / ".claude" / "projects"
    if proj.is_dir():
        for d in sorted(proj.iterdir()):
            if d.is_dir() and time.monotonic() < deadline:
                files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
                name = re.sub(r"^-+", "", d.name).replace("-", "/")[-60:]
                add("claude_code", f"{LABELS['claude_code']} · {name}", files, "sessions", str(d))
    roots = [home / c for c in CODE_DIRS if (home / c).is_dir()]
    md: dict[str, list[Path]] = {"claude_md": [], "agents_md": [], "cursor_rules": []}
    if (home / ".claude").is_dir():
        md["claude_md"] += sorted(p for p in (home / ".claude").glob("*.md") if p.is_file())
    for root in roots:
        for p in _walk(root, _rules_file, deadline):
            md["claude_md" if p.name == "CLAUDE.md" else "agents_md" if p.name == "AGENTS.md" else "cursor_rules"].append(p)
    where = ", ".join(str(r) for r in roots) or str(home)
    for k, ps in md.items():
        add(k, LABELS[k], ps, "files", f"{home / '.claude'}, {where}" if k == "claude_md" else where)
    for base in (home / "Library" / "Application Support", home / ".config"):
        cur = base / "Cursor" / "User"
        if cur.is_dir():
            g = cur / "globalStorage" / "state.vscdb"
            dbs = ([g] if g.is_file() else []) + sorted((cur / "workspaceStorage").glob("*/state.vscdb"))
            add("cursor_chats", LABELS["cursor_chats"], dbs, "databases", str(cur))
        obs = base / "obsidian" / "obsidian.json"
        if obs.is_file():
            try:
                vaults = list((json.loads(obs.read_text()).get("vaults") or {}).values())
            except (OSError, ValueError, AttributeError):
                vaults = []
            for v in vaults:
                vp = Path(str((v or {}).get("path", "")))
                if vp.is_dir() and time.monotonic() < deadline:
                    notes = _walk(vp, lambda p: p.suffix.lower() in (".md", ".txt"), deadline, depth=8)
                    add("obsidian", f"{LABELS['obsidian']} · {vp.name}", notes, "notes", str(vp))
    dl = home / "Downloads"
    if dl.is_dir():
        for z in sorted(dl.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            if time.monotonic() > deadline:
                break
            k = _zip_kind(z)
            if k:
                add(k, f"{LABELS[k]} · {z.name}", [z], "export", str(z))
    return found


def public(sources: list[dict]) -> list[dict]:
    """What the UI gets (no file lists)."""
    return [{k: v for k, v in s.items() if k != "paths"} for s in sources]


# ------------------------------------------------------------------ read + extract (read-only)
def _read(p: Path, limit: int = MAX_FILE_BYTES) -> str:
    with open(p, "rb") as f:
        return f.read(limit).decode("utf-8", "replace")


def _claude_code(p: Path) -> list[str]:
    out = []
    for line in _read(p, 8 * MAX_FILE_BYTES).splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
            continue
        c = (d.get("message") or {}).get("content")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith(("<", "Caveat:")):
            out.append(c)
    return out


def _cursor(p: Path) -> list[str]:
    out: list[str] = []
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=2)
    except sqlite3.Error:
        return out
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "cursorDiskKV" in tables:
            q = ("SELECT json_extract(value,'$.text') FROM cursorDiskKV WHERE key LIKE 'bubbleId:%' "
                 "AND json_valid(value) AND json_extract(value,'$.type')=1 LIMIT 20000")
            out += [r[0] for r in c.execute(q) if isinstance(r[0], str) and r[0].strip()]
        if "ItemTable" in tables:
            r = c.execute("SELECT value FROM ItemTable WHERE key='aiService.prompts'").fetchone()
            if r:
                try:
                    out += [str(x.get("text", "")) for x in json.loads(r[0]) if isinstance(x, dict)]
                except (ValueError, TypeError):
                    pass
    except sqlite3.Error:
        pass
    finally:
        c.close()
    return out


def _export(p: Path, kind: str) -> list[str]:
    with zipfile.ZipFile(p) as z:
        name = next(n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json")
        with z.open(name) as f:
            convs = json.loads(f.read(300_000_000).decode("utf-8-sig"))
    out: list[str] = []
    for conv in convs if isinstance(convs, list) else []:
        if not isinstance(conv, dict):
            continue
        if kind == "chatgpt_zip":
            out += [t for role, t in li._messages(conv) if role == "user"]
        else:
            for m in conv.get("chat_messages") or []:
                if isinstance(m, dict) and m.get("sender") == "human":
                    t = m.get("text") or " ".join(b.get("text", "") for b in m.get("content") or [] if isinstance(b, dict))
                    if t:
                        out.append(t)
    return out


# chat sentences that are tasks or code talk, not facts about the person
_TASKY = re.compile(r"(`|\(\)|\{|\}|=>|::|\.(?:py|js|ts|tsx|json|md|yml|yaml|sh|rs|go)\b|https?://|/[\w.-]+/|"
                    r"\b(?:error|bug|fix|issue|test|commit|branch|deploy(?:ed)? to|function|file|line|please|pls|can you|"
                    r"could you|let me|let's|now|this|that|here|it)\b|\b(?:arregl|corrig|prob[aá]|hac[eé]|pod[eé]s|fijate|"
                    r"este|esta|esto|ahora|ac[aá])\w*)", re.I)


def facts_from_messages(msgs: list[str], strict: bool = True) -> list[str]:
    """First-person sentences. strict=False keeps the task-like ones too (for a model to judge)."""
    out = []
    for text in msgs:
        if len(text) > 4000:  # long pastes are documents/code, not facts about the person
            continue
        for s in li._sentences(text):
            s = s.strip()
            if (15 <= len(s) <= 300 and li._FACT.search(s) and not li._QUESTION.search(s)
                    and not (strict and _TASKY.search(s))):
                out.append(s)
    return list(dict.fromkeys(out))


def _md_notes(paths: list[Path], deadline: float) -> list[str]:
    files, total = [], 0
    for p in paths:
        if time.monotonic() > deadline or total > MAX_SOURCE_BYTES:
            break
        try:
            text = _read(p)
        except OSError:
            continue
        total += len(text)
        name = f"{p.parent.name}/{p.name}" if p.name in ("CLAUDE.md", "AGENTS.md", ".cursorrules") else p.name
        files.append({"name": name if name.lower().endswith((".md", ".markdown", ".txt")) else name + ".md", "text": text})
    return [_tidy(n) for n in li.from_markdown(files)]


def _tidy(note: str) -> str:
    """'# Title\nbody' -> 'Title: body' so a note reads as one sentence in the review list."""
    m = re.match(r"#{1,6}\s+([^\n]+)\n+(.+)", note, re.S)
    return f"{m.group(1).strip()}: {m.group(2).strip()}" if m else note


def read_source(src: dict, deadline: float) -> tuple[list[str], list[str]]:
    """(ready notes, user messages to mine for facts)."""
    paths, kind, msgs = [Path(p) for p in src["paths"]], src["kind"], []
    if kind in ("claude_md", "agents_md", "cursor_rules", "obsidian"):
        return _md_notes(paths, deadline), []
    for p in paths:
        if time.monotonic() > deadline:
            break
        try:
            msgs += _claude_code(p) if kind == "claude_code" else _cursor(p) if kind == "cursor_chats" else _export(p, kind)
        except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile, RuntimeError):
            continue
    return [], msgs


# ------------------------------------------------------------------ optional LLM (local_engine picks it)
PROMPT = ("Below are messages a person wrote to AI assistants. Extract only DURABLE facts about the person: who they "
          "are, their work and projects, people they mention, tools they use, lasting preferences. Each fact is one "
          "short sentence that makes sense on its own. No secrets, no one-off tasks, no code, nothing about the "
          'assistant. Reply JSON only: {"facts": ["...", "..."]}, in the language the person writes.\n\n')


async def llm_facts(llm: Any, msgs: list[str], calls: int = 6, size: int = 5000) -> list[str]:
    """llm: an engine with async json(prompt) -> dict (Built-in, Ollama or API)."""
    batches, cur = [], ""
    for m in msgs:
        m = m.strip()[:1200]
        if not m or has_secret(m):  # never hand a secret to any model
            continue
        if len(cur) + len(m) > size:
            batches.append(cur)
            cur = ""
        cur += m + "\n---\n"
    if cur:
        batches.append(cur)
    out: list[str] = []
    for b in batches[-calls:]:  # newest messages last
        out += await batch_facts(llm, b)
    return list(dict.fromkeys(out))


async def batch_facts(llm: Any, batch: str) -> list[str]:
    """Facts from one batch of messages ("...\n---\n" separated); [] when the call fails."""
    try:
        facts = (await llm.json(PROMPT + batch)).get("facts") or []
    except Exception:  # noqa: BLE001 — one bad call does not stop the import
        return []
    from .memfilter import keep_texts  # paths, URLs, errors, code, acks, sensitive topics, time-bound → not notes
    return keep_texts([f.strip() for f in facts if isinstance(f, str)])




async def semantic_dupes(items: list[dict], pool: dict[str, list[str]], embedder: Any) -> int:
    """Mark items that say the same as an existing note (or an earlier item) in their context. Returns how many."""
    import math

    from .local_hygiene import lexical_conflict
    texts = [i["text"] for i in items if not i["duplicate"]]
    if not embedder or not texts:
        return 0
    ctxs = {i["context"] for i in items}
    olds = [(c, t) for c in ctxs for t in pool.get(c, [])][:2000]
    from .local_engine import SEM_DUP, embed_as
    vecs = await embed_as(embedder, [t for _, t in olds] + texts, "sim")

    def unit(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    vecs = [unit(v) for v in vecs]
    seen: list[tuple[str, str, list[float]]] = [(c, t, v) for (c, t), v in zip(olds, vecs)]
    k, marked = len(olds), 0
    for it in items:
        if it["duplicate"]:
            continue
        v = vecs[k]
        k += 1
        best = max(((sum(a * b for a, b in zip(v, w)), t) for c, t, w in seen if c == it["context"]), default=(0, ""))
        if best[0] >= SEM_DUP and not lexical_conflict(it["text"], best[1]):  # "2 dogs" vs "3 dogs"
            it["duplicate"] = best[1][:300]
            marked += 1
        else:
            seen.append((it["context"], it["text"], v))
    return marked


# ------------------------------------------------------------------ preview
async def preview(sources: list[dict], existing: dict[str, list[str]], progress: Callable[[dict], None],
                  llm: Any = None, budget: float = PREVIEW_BUDGET_S, embedder: Any = None) -> dict:
    """sources: scanned entries with the chosen "context"; existing: {context: [note texts]}. Writes nothing."""
    deadline = time.monotonic() + budget
    items: list[dict] = []
    stats = []
    for i, src in enumerate(sources):
        progress({"phase": f"Reading {src['label']}", "done": i, "total": len(sources)})
        notes, msgs = await asyncio.to_thread(read_source, src, deadline)
        facts = facts_from_messages(msgs)
        if llm and msgs and time.monotonic() < deadline:
            progress({"phase": f"Finding facts in {src['label']} ({llm.name})", "done": i, "total": len(sources)})
            facts = (await llm_facts(llm, msgs)) or facts
        from .memfilter import keep_texts
        cands = notes + facts
        kept = [t for t in notes if not has_secret(t)] + keep_texts(facts)
        ctx = src.get("context") or ""
        pool = existing.get(ctx, []) + [it["text"] for it in items if it["context"] == ctx]
        pv = li.preview(kept[:MAX_PER_SOURCE * 3], pool)
        for it in pv["items"][:MAX_PER_SOURCE]:
            items.append({**it, "context": ctx, "source": src["kind"], "source_label": src["label"]})
        stats.append({"id": src["id"], "label": src["label"], "messages": len(msgs), "candidates": len(kept),
                      "new": pv["new"], "secrets_dropped": sum(1 for t in cands if has_secret(t)),
                      "filtered": len(cands) - len(kept)})
    sem = 0
    if embedder:
        progress({"phase": f"Checking for repeats ({embedder.name})", "done": len(sources), "total": len(sources)})
        try:
            sem = await semantic_dupes(items, existing, embedder)
        except Exception as e:  # noqa: BLE001 — lexical dedupe already ran
            log.warning("semantic dedupe skipped: %s", type(e).__name__)
    progress({"phase": "Ready to review", "done": len(sources), "total": len(sources)})
    return {"items": items, "sources": stats, "llm": llm.name if llm else None,
            "engine": {"kind": llm.kind, "name": llm.name} if llm else {"kind": "rules", "name": "Rules"},
            "semantic_duplicates": sem,
            "new": sum(1 for i in items if not i["duplicate"]), "duplicates": sum(1 for i in items if i["duplicate"]),
            "timed_out": time.monotonic() > deadline}


class Discover:
    """Per-hub state: the last scan (the only paths that may ever be read) and one preview job."""

    def __init__(self, ui: Any, home_dir: Path | None = None, engine: Any = None) -> None:
        from . import local_engine
        self.ui, self.home_dir = ui, home_dir
        self.engine = engine or local_engine.get(ui.home)
        self.sources: dict[str, dict] = {}
        self.job: dict[str, Any] = {"state": "idle"}
        self.task: asyncio.Task | None = None

    def _home(self) -> Path:
        return Path(self.home_dir or os.environ.get("MNEMOS_SCAN_HOME") or Path.home())

    async def scan(self, _body: dict) -> dict:
        found = await asyncio.to_thread(scan, self._home(), list(self.ui.servers))
        self.sources = {s["id"]: s for s in found}
        llm = await asyncio.to_thread(self.engine.llm, "import")
        return {"sources": public(found), "contexts": list(self.ui.servers), "llm": llm.name if llm else None,
                "engine": {"kind": llm.kind, "name": llm.name} if llm else {"kind": "rules", "name": "Rules"}}

    async def start(self, body: dict) -> dict:
        if self.task and not self.task.done():
            raise ValueError("already reading the sources")
        chosen = []
        for c in body.get("sources") or []:
            s = self.sources.get(str((c or {}).get("id")))
            ctx = str((c or {}).get("context") or "")
            if s and ctx in self.ui.servers:
                chosen.append({**s, "context": ctx})
        if not chosen:
            raise ValueError("pick at least one source (scan again if the list is old)")
        existing = {c: await self.ui._existing(c) for c in {s["context"] for s in chosen}}
        self.job = {"state": "running", "phase": "Starting", "done": 0, "total": len(chosen)}

        async def run() -> None:
            try:
                llm = await asyncio.to_thread(self.engine.llm, "import") if body.get("use_llm", True) else None
                emb = await asyncio.to_thread(self.engine.embedder)
                res = await preview(chosen, existing, self.job.update, llm, embedder=emb)
                self.job = {**self.job, "state": "done", "result": res}
            except Exception as e:  # noqa: BLE001
                self.job = {"state": "error", "error": str(e)[:300]}
        self.task = asyncio.create_task(run())
        return {"started": True, "total": len(chosen)}

    async def status(self, _body: dict) -> dict:
        return self.job

    async def commit(self, body: dict) -> dict:
        batch = "imp-" + hashlib.sha1(f"{time.time()}".encode()).hexdigest()[:8]
        saved = skipped = 0
        errors: list[str] = []
        for n in (body.get("notes") or [])[:li.MAX_CANDIDATES]:
            n = n if isinstance(n, dict) else {}
            text, ctx = str(n.get("text", "")).strip()[:li.MAX_NOTE], str(n.get("context", ""))
            src = re.sub(r"[^a-z_]", "", str(n.get("source", "auto")))[:20] or "auto"
            if ctx not in self.ui.servers or len(text) < 8 or has_secret(text):
                skipped += 1
                continue
            try:
                r = await self.ui.call(ctx, "memory_save", {"text": text, "tags": ["import-auto", f"import-{src}", batch]})
                if r.get("status") == "duplicate":
                    skipped += 1
                else:
                    saved += 1
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:120])
        return {"saved": saved, "skipped": skipped, "failed": len(errors), "errors": errors[:5], "batch": batch}

    async def undo(self, body: dict) -> dict:
        batch = str(body.get("batch", ""))
        if not re.fullmatch(r"imp-[0-9a-f]{8}", batch):
            raise ValueError("unknown import")
        removed = 0
        for ctx in self.ui.servers:
            for it in (await self.ui.call(ctx, "memory_list", {"limit": 500})).get("items") or []:
                if batch in (it.get("tags") or []) and it.get("editable", True):
                    await self.ui.call(ctx, "memory_delete", {"id": it["id"]})
                    removed += 1
        return {"removed": removed}

    def install(self) -> None:
        h = self.ui.hooks
        h[("GET", "discover/scan")] = self.scan
        h[("POST", "discover/preview")] = self.start
        h[("GET", "discover/status")] = self.status
        h[("POST", "discover/commit")] = self.commit
        h[("POST", "discover/undo")] = self.undo
