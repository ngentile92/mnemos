"""Simple memory (0.11): the canonical memory is a folder of Markdown files, versioned with git.

    <home>/memory/                       a git repository (dulwich: pure Python, no git binary needed)
      .mnemos/index.json                 derived: which bullet has which note id, per-note metadata (rebuildable)
      .mnemos/lock                       cross-process write lock (the app and every stdio process share the repo)
      .mnemos/active                     present = this home uses the files as its store (see memmigrate)
      <dataset>/profile.md               who the person is, how they like answers: short (PROFILE_MAX bullets)
      <dataset>/topics/<slug>.md         one file per topic (a project, people, how I work, ...)

A *note* is one bullet ("- ...", continuation lines indented by two spaces). Bullets under a heading named
"No longer true" are kept as history: not listed, not exported, still readable by id. Anything else in a file
(headings, paragraphs) is the user's and is never touched.

Every change is one commit whose author is the app that made it ("claude-code", "you", "mnemos"), so the history of
a file is `git log` and "who added this" is `git blame`. A file edited by hand outside Mnemos is committed as "you"
the next time Mnemos reads it. Undo = restore a file as it was at an earlier commit (a new commit: nothing is lost).

FileNoteStore has the same interface as LocalNoteStore (remember / list_data / raw_text / update / delete_data /
recall), so every tool, the ledger, the search index, import and capture work unchanged on top of the files.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
import re
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterator

import yaml

from .memory import MAX_TEXT_CHARS, MemoryError_

PROFILE = "profile.md"
TOPICS = "topics"
PROFILE_MAX = 40          # bullets in profile.md; beyond that new profile facts go to topics/about-me.md
TOPIC_SOFT_MAX = 150      # the UI suggests a split above this
PAST_HEADINGS = ("no longer true", "ya no es cierto", "ya no es verdad", "history", "historial")
_BULLET = re.compile(r"^[-*] (.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK = re.compile(r"\[\[([^\[\]\n|]{1,60})(?:\|[^\[\]\n]{0,60})?\]\]")
_GLOBAL_LOCK = threading.RLock()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, max_len: int = 48) -> str:
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:max_len].strip("-") or "notes"


def title_of(slug: str) -> str:
    return slug.replace("-", " ").strip().capitalize() or "Notes"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"\w+", t))


# ------------------------------------------------------------------ file format
def split_front(text: str) -> tuple[dict[str, Any], list[str]]:
    """(frontmatter, body lines). A file without frontmatter is all body."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 200)):
            if lines[i].strip() == "---":
                try:
                    fm = yaml.safe_load("\n".join(lines[1:i])) or {}
                except yaml.YAMLError:
                    fm = {}
                return (fm if isinstance(fm, dict) else {}), lines[i + 1:]
    return {}, lines


def join_front(fm: dict[str, Any], body: list[str]) -> str:
    out = []
    if fm:
        out += ["---", yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=None).rstrip(), "---"]
    out += body
    text = "\n".join(out).rstrip("\n") + "\n"
    return text


def bullets(body: list[str]) -> list[dict[str, Any]]:
    """Bullets of a body: [{start, end (exclusive), text, past}]; `past` = under a "No longer true" heading."""
    out: list[dict[str, Any]] = []
    past = False
    i, n = 0, len(body)
    while i < n:
        line = body[i]
        h = _HEADING.match(line)
        if h:
            past = h.group(2).strip().lower().rstrip(":") in PAST_HEADINGS
            i += 1
            continue
        m = _BULLET.match(line)
        if not m:
            i += 1
            continue
        parts = [m.group(1)]
        j = i + 1
        while j < n:
            nxt = body[j]
            if nxt.startswith("  ") and nxt.strip():
                parts.append(nxt[2:])
                j += 1
            elif not nxt.strip() and j + 1 < n and body[j + 1].startswith("  ") and body[j + 1].strip():
                parts.append("")
                j += 1
            else:
                break
        text = "\n".join(parts).strip()
        if past:
            text = re.sub(r"^~~(.*)~~(\s*\((?:replaced|reemplazado)[^)]*\))?$", r"\1", text, flags=re.S).strip()
        if text:
            out.append({"start": i, "end": j, "text": text, "past": past})
        i = j
    return out


def bullet_lines(text: str) -> list[str]:
    """A note's text as bullet lines (blank lines inside the note become an empty continuation)."""
    rows = text.strip().splitlines() or [""]
    out = ["- " + rows[0].strip()]
    out += [("  " + r) if r.strip() else "" for r in rows[1:]]
    return out


def past_index(body: list[str]) -> int | None:
    for i, line in enumerate(body):
        h = _HEADING.match(line)
        if h and h.group(2).strip().lower().rstrip(":") in PAST_HEADINGS:
            return i
    return None


def insert_bullet(body: list[str], text: str) -> list[str]:
    """Append a bullet at the end of the current part of the file (before "No longer true")."""
    cut = past_index(body)
    head = body[:cut] if cut is not None else list(body)
    tail = body[cut:] if cut is not None else []
    while head and not head[-1].strip():
        head.pop()
    if head and not _BULLET.match(head[-1]) and not head[-1].startswith("  "):
        head.append("")
    head += bullet_lines(text)
    if tail:
        head.append("")
    return head + tail


def new_file(kind: str, title: str, space: str) -> tuple[dict[str, Any], list[str]]:
    fm = {"title": title, "space": space, "kind": kind, "updated": now_iso(), "sources": []}
    heading = "About me" if kind == "profile" else title
    return fm, [f"# {heading}", ""]


# ------------------------------------------------------------------ git (dulwich)
class Git:
    def __init__(self, root: Path) -> None:
        from dulwich.repo import Repo
        self.root = root
        if not (root / ".git").exists():
            root.mkdir(parents=True, exist_ok=True)
            Repo.init(str(root))
            (root / ".gitignore").write_text(".mnemos/\n")
        self._repo_cls = Repo

    def repo(self):
        return self._repo_cls(str(self.root))

    def head(self) -> str | None:
        r = self.repo()
        try:
            return r.head().decode()
        except KeyError:
            return None
        finally:
            r.close()

    def commit(self, paths: list[str], message: str, author: str = "mnemos") -> str | None:
        """Stage `paths` (relative; deleted files are removed from the index) and commit if anything changed."""
        from dulwich import porcelain
        who = (re.sub(r"[^A-Za-z0-9 ._()-]", "", author or "mnemos").strip() or "mnemos")[:60]
        ident = f"{who} <{slugify(who, 40)}@mnemos.local>".encode()
        r = self.repo()
        try:
            present = [p for p in paths if (self.root / p).exists()]
            gone = [p for p in paths if not (self.root / p).exists()]
            if (self.root / ".gitignore").exists():
                present.append(".gitignore")
            if present:
                porcelain.add(r, paths=[str(self.root / p) for p in present])
            if gone:
                idx = r.open_index()
                for p in gone:
                    with contextlib.suppress(KeyError):
                        del idx[p.encode()]
                idx.write()
            st = porcelain.status(r, untracked_files="no")
            staged = st.staged
            if not any(staged.get(k) for k in ("add", "delete", "modify")):
                return None
            sha = porcelain.commit(r, message=message.encode()[:2000], author=ident, committer=ident)
            return sha.decode() if isinstance(sha, bytes) else str(sha)
        finally:
            r.close()

    def log(self, path: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        r = self.repo()
        try:
            try:
                head = r.head()
            except KeyError:
                return []
            kw = {"paths": [path.encode()]} if path else {}
            out = []
            for entry in r.get_walker(include=[head], max_entries=limit, **kw):
                c = entry.commit
                author = c.author.decode(errors="replace").split(" <")[0]
                at = dt.datetime.fromtimestamp(c.author_time, dt.timezone.utc).replace(microsecond=0).isoformat()
                out.append({"sha": c.id.decode(), "author": author, "at": at,
                            "message": c.message.decode(errors="replace").strip()[:300]})
            return out
        finally:
            r.close()

    def show(self, sha: str, path: str) -> str | None:
        """A file's content at a commit (None if it did not exist there)."""
        r = self.repo()
        try:
            try:
                commit = r[sha.encode()]
                tree = r[commit.tree]
                mode, oid = tree.lookup_path(r.object_store.__getitem__, path.encode())
            except KeyError:
                return None
            return r[oid].data.decode("utf-8", errors="replace")
        finally:
            r.close()

    def parent(self, sha: str) -> str | None:
        r = self.repo()
        try:
            c = r[sha.encode()]
            return c.parents[0].decode() if c.parents else None
        finally:
            r.close()

    def changed(self, sha: str) -> list[str]:
        """Paths a commit changed (relative to its first parent)."""
        from dulwich.diff_tree import tree_changes
        r = self.repo()
        try:
            c = r[sha.encode()]
            old = r[c.parents[0]].tree if c.parents else None
            out = []
            for ch in tree_changes(r.object_store, old, c.tree):
                p = (ch.new.path or ch.old.path)
                if p:
                    out.append(p.decode())
            return out
        finally:
            r.close()


# ------------------------------------------------------------------ the repository
class MemoryRepo:
    """The memory folder: files, git, the derived note-id index, and a cross-process lock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.meta_dir = self.root / ".mnemos"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
        self.git = Git(self.root)
        self._index_path = self.meta_dir / "index.json"

    # -- lock
    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        with _GLOBAL_LOCK:
            with open(self.meta_dir / "lock", "a+") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    # -- index (derived)
    def _load(self) -> dict[str, Any]:
        try:
            d = json.loads(self._index_path.read_text())
            if isinstance(d, dict):
                d.setdefault("files", {})
                d.setdefault("meta", {})
                return d
        except (OSError, ValueError):
            pass
        return {"files": {}, "meta": {}}

    def _save(self, idx: dict[str, Any]) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False))
        os.replace(tmp, self._index_path)

    # -- files
    def files(self, dataset: str) -> list[str]:
        """Relative paths of the dataset's Markdown files: profile first, then topics by name."""
        base = self.root / dataset
        if not base.is_dir():
            return []
        out = []
        if (base / PROFILE).is_file():
            out.append(f"{dataset}/{PROFILE}")
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(self.root).as_posix()
            if rel != f"{dataset}/{PROFILE}" and not any(part.startswith(".") for part in p.relative_to(base).parts):
                out.append(rel)
        return out

    def read(self, rel: str) -> str:
        p = self._path(rel)
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def _path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents or p.suffix != ".md" or ".mnemos" in p.parts or ".git" in p.parts:
            raise MemoryError_("ruta de archivo inválida")
        return p

    @staticmethod
    def version(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

    def _write(self, rel: str, text: str) -> None:
        p = self._path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    # -- reconcile: bullets <-> note ids
    def _reconcile(self, idx: dict[str, Any], rel: str, text: str | None = None) -> tuple[list[dict[str, Any]], bool]:
        """Bullets of one file with their note ids. Ids follow a bullet across edits (same text, else the same
        position with similar text); a bullet nobody has seen gets a new id. Returns (bullets, index changed)."""
        text = self.read(rel) if text is None else text
        h = self.version(text)
        rec = idx["files"].get(rel)
        _fm, body = split_front(text)
        bs = bullets(body)
        if rec and rec.get("v") == h and len(rec.get("ids", [])) == len(bs):
            for b, nid in zip(bs, rec["ids"]):
                b["id"] = nid
            return bs, False
        old_ids: list[str] = list((rec or {}).get("ids") or [])
        old_hashes: list[str] = list((rec or {}).get("hashes") or [])
        by_hash: dict[str, list[int]] = {}
        for k, hh in enumerate(old_hashes):
            by_hash.setdefault(hh, []).append(k)
        used: set[int] = set()
        for b in bs:
            b["h"] = text_hash(b["text"])
            for k in by_hash.get(b["h"], []):
                if k not in used and k < len(old_ids):
                    b["id"] = old_ids[k]
                    used.add(k)
                    break
        free = [k for k in range(len(old_ids)) if k not in used]
        for pos, b in enumerate(bs):
            if "id" in b or not free:
                continue
            best, score = None, 0.0
            for k in free:
                prev = (idx["meta"].get(old_ids[k]) or {}).get("text") or ""
                s = difflib.SequenceMatcher(None, prev, b["text"]).ratio() - 0.02 * abs(k - pos)
                if s > score:
                    best, score = k, s
            if best is not None and score >= 0.55:
                b["id"] = old_ids[best]
                free.remove(best)
        ts = now_iso()
        for b in bs:
            if "id" not in b:
                b["id"] = str(uuid.uuid4())
                idx["meta"][b["id"]] = {"created_at": ts, "source_app": "you", "tags": []}
            m = idx["meta"].setdefault(b["id"], {"created_at": ts, "tags": []})
            if m.get("text") not in (None, b["text"]):
                m["updated_at"] = ts
            m["text"], m["file"] = b["text"], rel
        for k in free:  # bullets that left this file
            nid = old_ids[k]
            if (idx["meta"].get(nid) or {}).get("file") == rel:
                idx["meta"].pop(nid, None)
        idx["files"][rel] = {"v": h, "ids": [b["id"] for b in bs], "hashes": [text_hash(b["text"]) for b in bs]}
        return bs, True

    def scan(self, dataset: str) -> list[dict[str, Any]]:
        """Every bullet of a dataset with id, file and metadata. Files edited by hand are committed as 'you'."""
        with self.locked():
            idx = self._load()
            out, dirty, hand = [], False, []
            rels = self.files(dataset)
            for rel in rels:
                bs, ch = self._reconcile(idx, rel)
                dirty |= ch
                if ch:
                    hand.append(rel)
                for b in bs:
                    out.append({**b, "file": rel, "meta": idx["meta"].get(b["id"], {})})
            known = [r for r in idx["files"] if r.startswith(dataset + "/") and r not in rels]
            for rel in known:  # a file deleted by hand
                for nid in idx["files"][rel].get("ids", []):
                    if (idx["meta"].get(nid) or {}).get("file") == rel:
                        idx["meta"].pop(nid, None)
                idx["files"].pop(rel, None)
                hand.append(rel)
                dirty = True
            if dirty:
                self._save(idx)
            if hand:
                self.git.commit(hand, "Edited by hand", "you")
            return out

    def find(self, dataset: str, nid: str) -> dict[str, Any] | None:
        for b in self.scan(dataset):
            if b["id"] == nid:
                return b
        return None

    # -- writes (each one commit)
    def mutate(self, rel: str, fn, message: str, author: str, *, create: tuple[str, str, str] | None = None,
               expected: str | None = None) -> dict[str, Any]:
        """Apply fn(frontmatter, body, bullets) -> (frontmatter, body) to one file under the lock, then commit.
        `create` = (kind, title, space) for a file that does not exist yet; `expected` = version the caller read."""
        with self.locked():
            idx = self._load()
            text = self.read(rel)
            if expected is not None and self.version(text) != expected:
                raise MemoryError_("el archivo cambió desde que lo leíste: volvé a leerlo (version)")
            if not text:
                if not create:
                    raise MemoryError_("archivo no encontrado (HTTP 404)")
                fm, body = new_file(*create)
            else:
                fm, body = split_front(text)
            bs, _ = self._reconcile(idx, rel, text)
            fm, body, result = fn(fm, body, bs, idx)
            if fm is not None:
                fm["updated"] = now_iso()
                if author and author not in ("you", "mnemos"):
                    src = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
                    if author not in src:
                        fm["sources"] = (src + [author])[-8:]
            new = join_front(fm, body) if fm is not None or body else ""
            if new.strip():
                self._write(rel, new)
            else:
                with contextlib.suppress(FileNotFoundError):
                    self._path(rel).unlink()
            bs2, _ = self._reconcile_after(idx, rel, new, result)
            self._save(idx)
            sha = self.git.commit([rel], message, author)
            return {**(result or {}), "file": rel, "version": self.version(new), "commit": sha}

    def _reconcile_after(self, idx, rel, new, result):
        """After a write the caller says which bullet got which id (result['assign'] = {line index: id})."""
        assign = (result or {}).pop("assign", None) if result else None
        if not new.strip():
            for nid in idx["files"].get(rel, {}).get("ids", []):
                if (idx["meta"].get(nid) or {}).get("file") == rel:
                    idx["meta"].pop(nid, None)
            idx["files"].pop(rel, None)
            return [], True
        _fm, body = split_front(new)
        bs = bullets(body)
        if assign:
            ids = []
            prev = dict(zip(idx["files"].get(rel, {}).get("hashes", []), idx["files"].get(rel, {}).get("ids", [])))
            for b in bs:
                nid = assign.get(b["start"]) or prev.get(text_hash(b["text"]))
                if not nid or (nid in ids):
                    nid = next((i for h, i in prev.items() if h == text_hash(b["text"]) and i not in ids), None) or str(uuid.uuid4())
                ids.append(nid)
            ts = now_iso()
            for b, nid in zip(bs, ids):
                m = idx["meta"].setdefault(nid, {"created_at": ts, "tags": []})
                m["text"], m["file"] = b["text"], rel
            idx["files"][rel] = {"v": self.version(new), "ids": ids, "hashes": [text_hash(b["text"]) for b in bs]}
            return bs, True
        return self._reconcile(idx, rel, new)

    def write_file(self, rel: str, text: str, author: str, expected: str | None = None,
                   message: str = "Edited in Mnemos") -> dict[str, Any]:
        """Replace a whole file (the editor in the app). Ids follow bullets by text/position."""
        with self.locked():
            cur = self.read(rel)
            if expected is not None and self.version(cur) != expected:
                raise MemoryError_("el archivo cambió desde que lo abriste: recargalo")
            if len(text) > 400_000:
                raise MemoryError_("archivo demasiado grande")
            idx = self._load()
            self._reconcile(idx, rel, cur)
            if text.strip():
                self._write(rel, text if text.endswith("\n") else text + "\n")
            else:
                with contextlib.suppress(FileNotFoundError):
                    self._path(rel).unlink()
            self._reconcile(idx, rel, self.read(rel)) if text.strip() else self._reconcile_after(idx, rel, "", None)
            self._save(idx)
            sha = self.git.commit([rel], message, author)
            return {"file": rel, "version": self.version(self.read(rel)), "commit": sha}

    def restore(self, rel: str, sha: str, author: str = "you") -> dict[str, Any]:
        """The file as it was at commit `sha` (a new commit; restoring a commit where the file did not exist
        deletes it)."""
        old = self.git.show(sha, rel)
        return self.write_file(rel, old or "", author, message=f"Restored {rel} as of {sha[:8]}")

    def undo(self, sha: str, author: str = "you") -> dict[str, Any]:
        """Undo one commit: every file it changed goes back to its version just before it."""
        parent = self.git.parent(sha)
        out = []
        for rel in self.git.changed(sha):
            if rel.endswith(".md"):
                old = self.git.show(parent, rel) if parent else None
                out.append(self.write_file(rel, old or "", author, message=f"Undo {sha[:8]} ({rel})"))
        return {"undone": sha, "files": [o["file"] for o in out]}

    def history(self, rel: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.git.log(rel, limit)

    def import_files(self, files: dict[str, str], ids: dict[str, list[str]], meta: dict[str, dict[str, Any]],
                     message: str, author: str = "mnemos") -> str | None:
        """Write many files at once (a migration) as ONE commit. `ids` gives each bullet its existing note id,
        so search, history and links keep working. Bullets that do not match are reconciled normally."""
        with self.locked():
            idx = self._load()
            for rel, text in files.items():
                self._write(rel, text)
                bs = bullets(split_front(text)[1])
                order = list(ids.get(rel) or [])
                while len(order) < len(bs):
                    order.append(str(uuid.uuid4()))
                order = order[:len(bs)]
                idx["files"][rel] = {"v": self.version(text), "ids": order,
                                     "hashes": [text_hash(b["text"]) for b in bs]}
                for nid, b in zip(order, bs):
                    m = meta.get(nid) or {}
                    mm = idx["meta"].setdefault(nid, {"created_at": m.get("created_at") or now_iso(), "tags": []})
                    mm.update({k: v for k, v in m.items() if v not in (None, [], "")})
                    mm["text"], mm["file"] = b["text"], rel
            self._save(idx)
            return self.git.commit(list(files), message, author)


# ------------------------------------------------------------------ routing a new note to a file
KIND_FILE = {"identity": PROFILE, "preference": PROFILE, "habit": PROFILE, "relationship": "topics/people.md",
             "person": "topics/people.md", "goal": "topics/goals.md", "project_state": "topics/projects.md",
             "decision": "topics/projects.md", "skill": "topics/how-i-work.md", "instruction": "topics/how-i-work.md",
             "feedback": "topics/how-i-work.md", "reference": "topics/references.md"}


def route(repo: MemoryRepo, dataset: str, text: str, tags: list[str] | None = None, topic: str | None = None) -> str:
    """Which file a new note goes to (relative to the repo): an explicit topic, else a [[Link]] that names an
    existing topic, else its kind (tags kind-*), else topics/notes.md."""
    tags = [str(t).lower() for t in (tags or [])]
    existing = {Path(r).stem: r for r in repo.files(dataset)}
    if topic and topic.startswith(dataset + "/") and topic.endswith(".md") and ".." not in topic:
        return _profile_or_overflow(repo, dataset) if topic == f"{dataset}/{PROFILE}" else topic
    if topic:
        t = slugify(topic.removesuffix(".md").split("/")[-1])
        if t in ("profile", "about-me-profile", "me", "about-me") and t != "about-me":
            return _profile_or_overflow(repo, dataset)
        return existing.get(t) or f"{dataset}/{TOPICS}/{t}.md"
    for tg in tags:
        if tg.startswith(("topic-", "topic:")):
            return existing.get(slugify(tg[6:])) or f"{dataset}/{TOPICS}/{slugify(tg[6:])}.md"
    for name in _LINK.findall(text):
        s = slugify(name)
        if s in existing and s != "profile":
            return existing[s]
    if "pinned" in tags:
        return _profile_or_overflow(repo, dataset)
    for tg in tags:
        if tg.startswith("kind-") and tg[5:] in KIND_FILE:
            f = KIND_FILE[tg[5:]]
            return _profile_or_overflow(repo, dataset) if f == PROFILE else f"{dataset}/{f}"
    return f"{dataset}/{TOPICS}/notes.md"


def _profile_or_overflow(repo: MemoryRepo, dataset: str) -> str:
    rel = f"{dataset}/{PROFILE}"
    _fm, body = split_front(repo.read(rel))
    if len([b for b in bullets(body) if not b["past"]]) >= PROFILE_MAX:
        return f"{dataset}/{TOPICS}/about-me.md"
    return rel


def kind_for(rel: str) -> tuple[str, str]:
    """(kind, title) for a new file."""
    stem = Path(rel).stem
    if Path(rel).name == PROFILE:
        return "profile", "About me"
    kinds = {"people": "person", "projects": "project", "how-i-work": "preference", "goals": "topic",
             "references": "reference", "about-me": "profile", "notes": "topic"}
    return kinds.get(stem, "topic"), title_of(stem)


# ------------------------------------------------------------------ the store (LocalNoteStore interface)
class FileNoteStore:
    """Notes = bullets of Markdown files, one folder per dataset. Same interface as LocalNoteStore."""
    is_files = True

    def __init__(self, root: str | Path, allowed_ids: set[str] | None, names: dict[str, str] | None = None) -> None:
        self.repo = MemoryRepo(root)
        self.allowed = allowed_ids
        self.names = names or {}           # dataset id -> dataset name (folder)

    def _name(self, ds: str) -> str:
        if self.allowed is not None and ds not in self.allowed:
            raise MemoryError_("dataset fuera del alcance de este contexto (HTTP 403)")
        name = self.names.get(ds)
        if not name or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise MemoryError_("dataset id inválido")
        return name

    def dataset_name(self, ds: str) -> str:
        return self._name(ds)

    async def remember(self, text: str, dataset_id: str, node_set: list[str] | None = None,
                       run_in_background: bool = True, metadata: dict[str, Any] | None = None,
                       topic: str | None = None) -> Any:
        if not text.strip():
            raise MemoryError_("texto vacío")
        if len(text) > MAX_TEXT_CHARS:
            raise MemoryError_(f"texto demasiado largo (máx {MAX_TEXT_CHARS} caracteres)")
        name = self._name(dataset_id)
        meta = {k: v for k, v in (metadata or {}).items() if v not in (None, "", [])}
        tags = list(meta.get("tags") or node_set or [])
        clean = text.strip()
        for b in self.repo.scan(name):
            if b["text"] == clean and not b["past"]:
                return {"status": "completed", "items": [{"id": b["id"]}], "file": b["file"]}
        rel = route(self.repo, name, clean, tags, topic)
        author = str(meta.get("source_app") or "mnemos")
        nid = str(uuid.uuid4())

        def fn(fm, body, bs, idx):
            body2 = insert_bullet(body, clean)
            start = len(body2) - len(bullet_lines(clean))
            cut = past_index(body2)
            if cut is not None:  # inserted before the past section: find our bullet
                for b in bullets(body2):
                    if b["text"] == clean and not b["past"]:
                        start = b["start"]
            idx["meta"][nid] = {"created_at": now_iso(), "source_app": author, "tags": tags,
                                **{k: v for k, v in meta.items() if k in ("hub_context", "saved_at", "links")}}
            return fm, body2, {"assign": {start: nid}}
        kind, title = kind_for(rel)
        self.repo.mutate(rel, fn, f"Remember: {clean[:72]}", author, create=(kind, title, name))
        return {"status": "completed", "items": [{"id": nid}], "file": rel}

    async def list_data(self, dataset_id: str) -> list[dict[str, Any]]:
        name = self._name(dataset_id)
        rows = [b for b in self.repo.scan(name) if not b["past"]]
        out = []
        for b in rows:
            m = b["meta"]
            out.append({"id": b["id"], "createdAt": m.get("created_at"), "updatedAt": m.get("updated_at") or m.get("created_at"),
                        "mimeType": "text/plain",
                        "externalMetadata": {"source_app": m.get("source_app"), "tags": m.get("tags") or [],
                                             "file": b["file"]}})
        out.sort(key=lambda d: str(d.get("createdAt") or ""), reverse=True)
        return out

    async def raw_text(self, dataset_id: str, data_id: str, max_bytes: int = 4 * MAX_TEXT_CHARS) -> str | None:
        try:
            name = self._name(dataset_id)
        except MemoryError_:
            return None
        b = self.repo.find(name, data_id)
        return b["text"][:max_bytes] if b else None

    async def update(self, dataset_id: str, data_id: str, text: str, node_set: list[str] | None = None,
                     author: str | None = None) -> Any:
        if not text.strip():
            raise MemoryError_("texto vacío")
        if len(text) > MAX_TEXT_CHARS:
            raise MemoryError_(f"texto demasiado largo (máx {MAX_TEXT_CHARS} caracteres)")
        name = self._name(dataset_id)
        b = self.repo.find(name, data_id)
        if not b:
            raise MemoryError_("nota no encontrada (HTTP 404)")

        def fn(fm, body, bs, idx):
            cur = next((x for x in bs if x["id"] == data_id), None)
            if not cur:
                raise MemoryError_("nota no encontrada (HTTP 404)")
            new_lines = bullet_lines(text.strip())
            if cur["past"]:
                new_lines[0] = "- ~~" + new_lines[0][2:] + "~~" if len(new_lines) == 1 else new_lines[0]
            body2 = body[:cur["start"]] + new_lines + body[cur["end"]:]
            m = idx["meta"].setdefault(data_id, {"created_at": now_iso(), "tags": []})
            m["updated_at"] = now_iso()
            if node_set is not None:
                m["tags"] = list(node_set)
            return fm, body2, {"assign": {cur["start"]: data_id}}
        self.repo.mutate(b["file"], fn, f"Edit: {text.strip()[:72]}", author or "mnemos")
        return {"status": "completed", "data_id": data_id}

    async def delete_data(self, dataset_id: str, data_id: str, author: str | None = None) -> None:
        name = self._name(dataset_id)
        b = self.repo.find(name, data_id)
        if not b:
            raise MemoryError_("nota no encontrada (HTTP 404)")

        def fn(fm, body, bs, idx):
            cur = next((x for x in bs if x["id"] == data_id), None)
            if not cur:
                raise MemoryError_("nota no encontrada (HTTP 404)")
            end = cur["end"]
            body2 = body[:cur["start"]] + body[end:]
            idx["meta"].pop(data_id, None)
            return fm, body2, {"assign": {}}
        self.repo.mutate(b["file"], fn, f"Forget: {b['text'][:72]}", author or "mnemos")

    async def supersede(self, dataset_id: str, data_id: str, author: str | None = None) -> dict[str, Any]:
        """Move a note under "No longer true" (struck through, dated): kept as history, out of lists and exports."""
        name = self._name(dataset_id)
        b = self.repo.find(name, data_id)
        if not b:
            raise MemoryError_("nota no encontrada (HTTP 404)")
        if b["past"]:
            return {"status": "already", "data_id": data_id}

        def fn(fm, body, bs, idx):
            cur = next((x for x in bs if x["id"] == data_id), None)
            body2 = body[:cur["start"]] + body[cur["end"]:]
            cut = past_index(body2)
            line = f"- ~~{' '.join(cur['text'].split())}~~ (replaced {dt.date.today().isoformat()})"
            if cut is None:
                while body2 and not body2[-1].strip():
                    body2.pop()
                body2 += ["", "## No longer true", line]
                pos = len(body2) - 1
            else:
                j = cut + 1
                while j < len(body2) and (body2[j].startswith(("- ", "* ", "  ")) or not body2[j].strip()):
                    j += 1
                while j > cut + 1 and not body2[j - 1].strip():
                    j -= 1
                body2 = body2[:j] + [line] + body2[j:]
                pos = j
            return fm, body2, {"assign": {pos: data_id}}
        self.repo.mutate(b["file"], fn, f"No longer true: {b['text'][:60]}", author or "mnemos")
        return {"status": "completed", "data_id": data_id}

    async def recall(self, query: str, dataset_ids: list[str], top_k: int = 10, search_type: str | None = None) -> Any:
        if not dataset_ids:
            raise MemoryError_("sin datasets: el gateway nunca consulta sin alcance explícito")
        words = {w for w in re.findall(r"\w{3,}", query.lower())}
        scored = []
        for ds in dataset_ids:
            try:
                name = self._name(ds)
            except MemoryError_:
                continue
            for b in self.repo.scan(name):
                if b["past"]:
                    continue
                s = sum(1 for w in words if w in b["text"].lower())
                if s:
                    scored.append((s, ds, b["text"]))
        scored.sort(key=lambda x: -x[0])
        return [{"dataset_id": ds, "dataset_name": self.names.get(ds), "text": t} for _, ds, t in scored[:top_k]]

    # -- file-level API (new tools, the app's Memory screen)
    def topics(self, dataset_id: str) -> list[dict[str, Any]]:
        name = self._name(dataset_id)
        out = []
        by_file: dict[str, list[dict]] = {}
        for b in self.repo.scan(name):
            by_file.setdefault(b["file"], []).append(b)
        for rel in self.repo.files(name):
            text = self.repo.read(rel)
            fm, _ = split_front(text)
            cur = [b for b in by_file.get(rel, []) if not b["past"]]
            out.append({"file": rel, "topic": Path(rel).stem if Path(rel).name != PROFILE else "profile",
                        "title": str(fm.get("title") or kind_for(rel)[1]), "kind": str(fm.get("kind") or kind_for(rel)[0]),
                        "notes": len(cur), "first": (cur[0]["text"][:140] if cur else ""),
                        "updated": str(fm.get("updated") or ""), "sources": fm.get("sources") or [],
                        "sensitivity": str(fm.get("sensitivity") or "normal"), "version": self.repo.version(text),
                        "long": len(cur) > (PROFILE_MAX if Path(rel).name == PROFILE else TOPIC_SOFT_MAX)})
        return out

    def resolve(self, dataset_id: str, topic: str) -> str:
        """A topic name (profile, people, 'Atlas', or a file path) -> the file path in this dataset."""
        name = self._name(dataset_id)
        t = str(topic or "").strip()
        if t.startswith(name + "/") and t.endswith(".md"):
            return t
        s = slugify(t.removesuffix(".md").split("/")[-1])
        if s in ("profile", "me", "about-me-profile"):
            return f"{name}/{PROFILE}"
        for rel in self.repo.files(name):
            if Path(rel).stem == s:
                return rel
        raise MemoryError_(f"no hay un tema {topic!r} (usá memory_start para ver los temas)")

    def read_file(self, dataset_id: str, rel: str) -> dict[str, Any]:
        name = self._name(dataset_id)
        if not rel.startswith(name + "/"):
            raise MemoryError_("ese archivo no es de este espacio")
        text = self.repo.read(rel)
        if not text:
            raise MemoryError_("archivo no encontrado (HTTP 404)")
        bs = [b for b in self.repo.scan(name) if b["file"] == rel]
        return {"file": rel, "text": text, "version": self.repo.version(text),
                "notes": [{"id": b["id"], "text": b["text"], "past": b["past"]} for b in bs]}

    def write_file(self, dataset_id: str, rel: str, text: str, author: str = "you",
                   expected: str | None = None) -> dict[str, Any]:
        name = self._name(dataset_id)
        if not rel.startswith(name + "/") or not rel.endswith(".md"):
            raise MemoryError_("ese archivo no es de este espacio")
        return self.repo.write_file(rel, text, author, expected)

    def history(self, dataset_id: str, rel: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        name = self._name(dataset_id)
        if rel and not rel.startswith(name + "/"):
            raise MemoryError_("ese archivo no es de este espacio")
        return self.repo.history(rel or name, limit)

    def restore(self, dataset_id: str, rel: str, sha: str, author: str = "you") -> dict[str, Any]:
        name = self._name(dataset_id)
        if not rel.startswith(name + "/") or not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
            raise MemoryError_("archivo o versión inválidos")
        return self.repo.restore(rel, sha, author)


# ------------------------------------------------------------------ which store a local home uses
def active(home: str | Path) -> bool:
    """True when this home uses the files store (new homes, or after `migrate apply`)."""
    return (Path(home) / "memory" / ".mnemos" / "active").exists()


def set_active(home: str | Path, on: bool) -> None:
    f = Path(home) / "memory" / ".mnemos" / "active"
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(now_iso() + "\n")
    elif f.exists():
        f.unlink()
