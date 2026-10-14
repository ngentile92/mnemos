"""Local app ("Open my memory"): the dashboard's Memory tab, a light entity graph and a skills editor, served by
mnemos-local on 127.0.0.1 for non-technical users (no Docker, no dashboard process).

Every memory edit goes through the hub's own tools in-process (same ledger, history and undo as an assistant
would use). Access: a session cookie obtained with the local token (Mnemos Desktop opens /app#k=<token>) or the
local password (auth.json). POSTs also need the X-Mnemos-Write header (no cross-site form posts) and the Host
check of TokenGate (no DNS rebinding).
"""

from __future__ import annotations

import collections
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

STATIC = Path(__file__).parent / "local_ui_static"
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
TOOLS = {"list": "memory_list", "history": "memory_history", "update": "memory_update",
         "delete": "memory_delete", "undo": "memory_undo", "pin": "memory_pin",
         "obsolete": "memory_mark_obsolete", "dispute": "memory_dispute", "save": "memory_save",
         "related": "memory_search", "promote": "memory_promote", "validity": "memory_set_validity"}
COOKIE = "mnemos_ui"
SESSION_TTL = 12 * 3600
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8"}
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


def tool_args(action: str, body: dict) -> dict:
    """Validated tool arguments for one UI action (mirrors dashboard/memory_client.py)."""
    if action == "list":
        args: dict[str, Any] = {"include_shared": bool(body.get("include_shared", True)), "limit": 500}
        q = str(body.get("contains") or "").strip()[:200]
        if q:
            args["contains"] = q
        return args
    if action == "related":
        q = str(body.get("q") or "").strip()[:500]
        if len(q) < 2:
            raise ValueError("query too short")
        return {"query": q, "mode": "hybrid", "top_k": 10, "include_shared": bool(body.get("include_shared", True))}
    if action == "save":
        text = str(body.get("text", "")).strip()
        if not 3 <= len(text) <= 20000:
            raise ValueError("text must be 3-20000 characters")
        args = {"text": text}
        if body.get("project"):
            args["project"] = str(body["project"])[:80]
        if body.get("tags"):
            args["tags"] = [str(t)[:60] for t in body["tags"]][:5]
        return args
    if action == "dispute":
        corr = str(body.get("correction", "")).strip()
        if not 5 <= len(corr) <= 1000:
            raise ValueError("describe the correction in 5-1000 characters")
        return {"correction": corr}
    nid = str(body.get("id", ""))
    if not _UUID.match(nid):
        raise ValueError("invalid note id")
    args = {"id": nid}
    if action == "update":
        text = str(body.get("text", ""))
        if not 3 <= len(text) <= 20000:
            raise ValueError("text must be 3-20000 characters")
        args["text"] = text
    elif action == "validity":
        for k in ("valid_from", "valid_until"):
            if k in body and body[k] is not None:
                v = str(body[k])[:10]
                if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                    raise ValueError(f"{k}: use YYYY-MM-DD")
                args[k] = v
    elif action == "promote":
        if body.get("confirm") is not True:
            raise ValueError("promote needs explicit confirmation")
        args["confirm"] = True
    elif action == "pin":
        args["pinned"] = bool(body.get("pinned", True))
    elif action == "obsolete":
        args["obsolete"] = bool(body.get("obsolete", True))
        if body.get("reason"):
            args["reason"] = str(body["reason"])[:300]
    return args


# ------------------------------------------------------------------ light entity graph (no Cognee, no LLM)
STOP = set("""a an and are as at be but by de del el en es for from has have he her his i in is it its la las los me
mi my no not of on or our que se she so su that the their there they this to un una was we were what when where
which who will with y yo you your al lo le les con por para como pero muy más mas ya hoy ayer también tambien
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december lunes martes miércoles jueves viernes sábado domingo enero febrero marzo
abril mayo junio julio agosto septiembre octubre noviembre diciembre note notes user usuario""".split())
_CAP = re.compile(r"\b([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü'’.-]*(?:\s+(?:de|del|la|van|von|da)?\s*[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü'’-]*){0,3})")
_TAG = re.compile(r"(?<![\w#])#([\w-]{2,40})")


def entities(text: str) -> set[str]:
    """Capitalised names (people, places, projects, products) and #tags in one note. Heuristic on purpose."""
    out = set()
    for m in _CAP.finditer(text):
        name = re.sub(r"['’]s$", "", re.sub(r"\s+", " ", m.group(1)).strip(" .'’-"))  # Ana's → Ana
        words = name.split()
        while words and words[0].lower() in STOP:
            words = words[1:]
        name = " ".join(words)
        if len(name) < 3 or name.lower() in STOP or name.isupper() and len(name) <= 2:
            continue
        if len(words) == 1 and m.start() > 0 and text[max(0, m.start() - 2):m.start()].strip() in (".", "!", "?", ""):
            # a single capitalised word that starts a sentence is usually not a name; keep it only if it recurs
            out.add("?" + name)
            continue
        out.add(name)
    out.update("#" + t.lower() for t in _TAG.findall(text))
    return out


def build_graph(items: list[dict], max_nodes: int = 60, max_edges: int = 160) -> dict:
    per_note: dict[str, set[str]] = {}
    for it in items:
        if it.get("obsolete"):
            continue
        ents = entities(str(it.get("text", "")))
        ents.update("#" + str(t).lower() for t in (it.get("tags") or []) if t)
        per_note[it["id"]] = ents
    counts: collections.Counter = collections.Counter()
    for ents in per_note.values():
        counts.update({e.lstrip("?") for e in ents})
    tentative = {e.lstrip("?") for ents in per_note.values() for e in ents if e.startswith("?")}
    firm = {e for ents in per_note.values() for e in ents if not e.startswith("?")}
    keep = [e for e, c in counts.most_common() if (e in firm or c >= 2) and (c >= 2 or len(per_note) < 40)]
    keep = keep[:max_nodes]
    ks = set(keep)
    notes_of: dict[str, list[str]] = collections.defaultdict(list)
    pairs: collections.Counter = collections.Counter()
    for nid, ents in per_note.items():
        es = sorted({e.lstrip("?") for e in ents} & ks)
        for e in es:
            notes_of[e].append(nid)
        for i, a in enumerate(es):
            for b in es[i + 1:]:
                pairs[(a, b)] += 1
    used = {n for ns in notes_of.values() for n in ns[:12]}
    texts = {it["id"]: str(it.get("text", ""))[:600] for it in items if it["id"] in used}
    del tentative
    return {"notes": len(per_note),
            "nodes": [{"id": e, "label": e.lstrip("#") if e.startswith("#") else e, "kind": "tag" if e.startswith("#") else "name",
                       "count": counts[e], "notes": notes_of[e][:12]} for e in keep],
            "edges": [{"source": a, "target": b, "weight": w} for (a, b), w in pairs.most_common(max_edges)],
            "texts": texts}


# ------------------------------------------------------------------ skills on disk
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def save_skill(home: Path, ctx: str, name: str, description: str, body: str) -> Path:
    if not NAME_RE.match(name) or len(name) > 64:
        raise ValueError("name: lowercase letters, digits and dashes (e.g. email-style)")
    desc = " ".join(str(description).split())
    if not 3 <= len(desc) <= 300:
        raise ValueError("say in one line when to use it (3-300 characters)")
    if len(body) > 100_000:
        raise ValueError("instructions too long")
    d = home / "skills" / ctx / name
    d.mkdir(parents=True, exist_ok=True)
    fm = json.dumps(desc, ensure_ascii=False)  # JSON string is valid YAML
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {fm}\n---\n{body.strip()}\n", encoding="utf-8")
    return d / "SKILL.md"


# ------------------------------------------------------------------ the Starlette routes
class UI:
    def __init__(self, servers: dict, home: Path, token: str) -> None:
        self.servers, self.home, self.token = servers, home, token
        self.sessions: dict[str, float] = {}
        self.hooks: dict[str, Any] = {}  # extra GET/POST handlers added by other features (import, hygiene)

    # -- auth
    def _authed(self, request) -> bool:
        sid = request.cookies.get(COOKIE, "")
        exp = self.sessions.get(sid)
        if not exp or exp < time.time():
            self.sessions.pop(sid, None)
            return False
        return True

    def _check_password(self, pw: str) -> bool:
        f = self.home / "auth.json"
        if not pw or not f.exists():
            return False
        from .local_auth import verify_password
        try:
            return verify_password(pw, json.loads(f.read_text())["password_hash"])
        except Exception:  # noqa: BLE001
            return False

    async def call(self, ctx: str, tool: str, args: dict) -> dict:
        from fastmcp import Client
        if ctx not in self.servers:
            raise ValueError(f"unknown context {ctx!r}")
        async with Client(self.servers[ctx]) as c:
            r = await c.call_tool(tool, args)
        return r.structured_content or {}

    async def action(self, action: str, body: dict) -> dict:
        return await self.call(str(body.get("context", "")), TOOLS[action], tool_args(action, body))

    async def move(self, body: dict) -> dict:
        src, dst, nid = str(body.get("context", "")), str(body.get("to", "")), str(body.get("id", ""))
        if body.get("confirm") is not True:
            raise ValueError("moving a note needs explicit confirmation")
        if src == dst or dst not in self.servers:
            raise ValueError("pick a different target context")
        text = str(body.get("text", ""))
        found = await self.action("list", {"context": src, "contains": text[:80], "include_shared": False})
        if not any(i.get("id") == nid and str(i.get("text", "")).strip() == text.strip() for i in found.get("items", [])):
            raise ValueError("the note changed or is not in that context — refresh and try again")
        saved = await self.action("save", {"context": dst, "text": text, "tags": [f"moved-from-{src}"]})
        gone = await self.action("delete", {"context": src, "id": nid})
        return {"moved": nid, "from": src, "to": dst, "saved": saved, "deleted": gone}

    async def skills(self, ctx: str) -> dict:
        r = await self.call(ctx, "skills_list", {})
        items = r.get("skills") or r.get("items") or []
        return {"skills": [{**s, "editable": s.get("owner") == ctx} for s in items]}

    def routes(self):
        from starlette.responses import FileResponse, JSONResponse, Response
        from starlette.routing import Route

        def j(code: int, data: dict, **kw) -> JSONResponse:
            return JSONResponse(data, status_code=code, headers={"Cache-Control": "no-store"}, **kw)

        async def page(_r):
            return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store", "Content-Security-Policy": CSP})

        async def static(request):
            rel = request.path_params["path"]
            p = (STATIC / rel).resolve()
            if STATIC.resolve() not in p.parents or not p.is_file() or rel == "index.html":
                return Response("not found", status_code=404)
            return FileResponse(p, media_type=TYPES.get(p.suffix), headers={"Cache-Control": "no-cache"})

        async def login(request):
            if request.headers.get("x-mnemos-write") != "1":
                return j(403, {"error": "missing X-Mnemos-Write"})
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
            tok, pw = str(body.get("token") or ""), str(body.get("password") or "")
            ok = (tok and hmac.compare_digest(tok.encode(), self.token.encode())) or self._check_password(pw)
            if not ok:
                time.sleep(0.5)
                return j(401, {"error": "wrong password"})
            sid = secrets.token_urlsafe(32)
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[sid] = now + SESSION_TTL
            resp = j(200, {"ok": True})
            resp.set_cookie(COOKIE, sid, max_age=SESSION_TTL, httponly=True, samesite="strict", path="/")
            return resp

        async def api(request):
            if not self._authed(request):
                return j(401, {"error": "locked: open it from Mnemos Desktop or type your password"})
            path = request.url.path[len("/api/"):]
            if request.method == "POST":
                if request.headers.get("x-mnemos-write") != "1":
                    return j(403, {"error": "missing X-Mnemos-Write"})
                try:
                    body = await request.json()
                except Exception:  # noqa: BLE001
                    return j(400, {"error": "bad JSON"})
            else:
                body = dict(request.query_params)
                if "include_shared" in body:
                    body["include_shared"] = body["include_shared"] == "1"
            from fastmcp.exceptions import ToolError
            try:
                if path == "session" and request.method == "GET":
                    return j(200, {"contexts": list(self.servers)})
                if path.startswith("memory/") and request.method == "GET" and path[7:] in ("list", "history", "related"):
                    return j(200, await self.action(path[7:], body))
                if path == "memory/move" and request.method == "POST":
                    return j(200, await self.move(body))
                if path.startswith("memory/") and request.method == "POST" and path[7:] in TOOLS:
                    return j(200, await self.action(path[7:], body))
                if path == "graph" and request.method == "GET":
                    ctx = str(body.get("context", ""))
                    items = (await self.action("list", {"context": ctx, "include_shared": True})).get("items", [])
                    return j(200, build_graph(items))
                if path == "skills" and request.method == "GET":
                    return j(200, await self.skills(str(body.get("context", ""))))
                if path == "skill" and request.method == "GET":
                    r = await self.call(str(body.get("context", "")), "skills_get", {"name": str(body.get("name", ""))[:64]})
                    return j(200, {"text": r.get("content") or r.get("text") or ""})
                if path == "skills/save" and request.method == "POST":
                    ctx = str(body.get("context", ""))
                    if ctx not in self.servers:
                        raise ValueError("unknown context")
                    save_skill(self.home, ctx, str(body.get("name", "")), str(body.get("description", "")),
                               str(body.get("body", "")))
                    return j(200, {"saved": True})
                hook = self.hooks.get((request.method, path))
                if hook:
                    return j(200, await hook(body))
                if path == "hygiene" and request.method == "GET":
                    return j(200, {"items": [], "date": None, "total": 0})
                return j(404, {"error": "unknown endpoint"})
            except (ValueError, ToolError) as e:
                return j(400, {"error": str(e)[:300]})

        return [Route("/app", page), Route("/ui/{path:path}", static), Route("/api/login", login, methods=["POST"]),
                Route("/api/{rest:path}", api, methods=["GET", "POST"])]
