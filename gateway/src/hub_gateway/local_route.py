"""Content-based space routing (0.13): which of the user's spaces a new note belongs to.

Order, first that decides wins:
  1. an explicit space (the AI app or the user named one)
  2. a folder mapping: the note came from a coding session whose working folder is mapped to a space
     (<home>/routing.json, edited in Spaces → Folders; longest matching prefix wins)
  3. the active engine (local model) reading the note against each space's description
  4. a rules fallback (words of the description + generic hints for common space names)
Low confidence → the app's default space, flagged for review. Every decision carries a short reason so the UI can
show "why here" next to a one-click move. Nothing here is specific to one person: spaces, descriptions and folders
are the user's own; the hints below are generic words in English and Spanish.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REVIEW_BELOW = 0.6
# Default descriptions for common space names, used when the user left a space's description empty.
DEFAULT_DESC = {
    "personal": "Personal life: family, friends, home, health habits, hobbies, travel, personal plans and preferences.",
    "work": "Job and career: employer, clients, colleagues, work projects, meetings, tools and decisions at work.",
    "side": "Side projects: things built on my own time, open source, experiments, ideas to try.",
    "study": "Learning: courses, books, research, things being studied.",
    "family": "Family: partner, kids, parents, home logistics.",
    "shared": "What every space should know: name, language, how answers should be written.",
}
GENERIC_DESC = "Notes that belong to {label}."
# Generic hint words (EN + ES) per common space name; only used by the rules fallback.
HINTS = {
    "work": "work job trabajo empresa company client cliente clientes team equipo colleague colega jefe boss manager "
            "meeting reunión reunion sprint deploy release repo repository pr pull code código codigo api backend "
            "frontend database server servidor production producción staging customer project proyecto product "
            "producto roadmap stakeholder deadline ticket jira slack invoice factura contrato contract salary sueldo",
    "personal": "family familia partner pareja wife esposa husband esposo kid kids hijo hija hijos mom mamá madre dad papá "
                "padre friend amigo amiga amigos home casa house hogar dog perro perra cat gato vacation vacaciones "
                "trip viaje travel hobby gym run correr running cook cocinar cocina music música movie película "
                "birthday cumpleaños weekend finde doctor health salud",
    "side": "side-project open-source opensource experiment experimento prototype prototipo",
    "study": "course curso class clase book libro paper study estudiar university universidad exam examen learn aprender",
}
_W = re.compile(r"[a-záéíóúüñ0-9][a-záéíóúüñ0-9+#.-]*", re.I)
_STOP = set("the a an and or of to in on for with my me i is are be at by from this that it as el la los las un una y o "
            "de del en con para por mi mis es son que lo se su sus al what goes here notes things".split())


def _words(s: str) -> set[str]:
    return {w.lower().strip(".-") for w in _W.findall(s or "") if len(w) > 2 and w.lower() not in _STOP}


def describe(spaces: list[dict]) -> list[dict]:
    """Spaces with a usable description (the user's, else a generic default for the name, else a neutral one)."""
    out = []
    for sp in spaces:
        name, label = str(sp.get("name")), str(sp.get("label") or sp.get("name"))
        own = str(sp.get("description") or "").strip()
        # a description that only repeats the name ("work") says nothing: use the generic one for that name
        custom = len(own.split()) >= 3 and own.lower() not in (name.lower(), label.lower())
        d = own if custom else (DEFAULT_DESC.get(name) or DEFAULT_DESC.get(label.lower()) or own
                                or GENERIC_DESC.format(label=label))
        out.append({"name": name, "label": label, "description": d, "custom": custom})
    return out


# ------------------------------------------------------------------ folder mapping
def _file(home: Path) -> Path:
    return Path(home) / "routing.json"


def folders(home: Path) -> list[dict]:
    try:
        d = json.loads(_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"path": str(x["path"]), "space": str(x["space"])} for x in d.get("folders") or []
            if isinstance(x, dict) and x.get("path") and x.get("space")]


def _norm_path(p: str) -> str:
    p = os.path.expanduser(str(p or "").strip())
    return os.path.normpath(p) if p else ""


def set_folder(home: Path, path: str, space: str | None, spaces: list[str]) -> list[dict]:
    """Map a folder (and everything under it) to a space; space None/"" removes the mapping."""
    p = _norm_path(path)
    if not p or not os.path.isabs(p):
        raise ValueError("use a full folder path, e.g. ~/code/my-project")
    if space and space not in spaces:
        raise ValueError(f"unknown space {space!r}")
    cur = [x for x in folders(home) if _norm_path(x["path"]) != p]
    if space:
        cur.append({"path": p, "space": space})
    f = _file(home)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"folders": sorted(cur, key=lambda x: x["path"])}, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    return folders(home)


def by_folder(home: Path, cwd: str | None, spaces: list[str]) -> dict | None:
    c = _norm_path(cwd or "")
    if not c:
        return None
    best = None
    for x in folders(home):
        p = _norm_path(x["path"])
        if x["space"] in spaces and (c == p or c.startswith(p.rstrip(os.sep) + os.sep)):
            if not best or len(p) > len(best[0]):
                best = (p, x["space"])
    if not best:
        return None
    return {"space": best[1], "confidence": 1.0, "by": "folder", "review": False,
            "reason": f"folder {best[0].replace(str(Path.home()), '~')} is mapped to this space"}


# ------------------------------------------------------------------ rules
def by_rules(text: str, spaces: list[dict], default: str) -> dict:
    ws = _words(text)
    scores = []
    for sp in describe(spaces):
        if sp["name"] == "shared":
            continue
        hint = _words(sp["description"]) | _words(HINTS.get(sp["name"], "") if not sp["custom"] else "") \
            | _words(sp["label"])
        hits = sorted(ws & hint)
        scores.append((len(hits), sp["name"], hits))
    scores.sort(key=lambda x: -x[0])
    if not scores or scores[0][0] == 0:
        return {"space": default, "confidence": 0.3, "by": "rules", "review": True,
                "reason": "no clear clue; put in the default space"}
    top, second = scores[0], (scores[1] if len(scores) > 1 else (0, "", []))
    conf = min(0.85, 0.3 + 0.15 * top[0] + 0.1 * (top[0] - second[0])) if top[0] > second[0] else 0.4
    return {"space": top[1], "confidence": round(conf, 2), "by": "rules", "review": conf < REVIEW_BELOW,
            "reason": "mentions " + ", ".join(top[2][:3])}


# ------------------------------------------------------------------ model
PROMPT = ("Sort each note into ONE of these spaces of the same person's memory, by what the note is about.\n"
          "Spaces:\n{spaces}\n\nNotes:\n{notes}\n\n"
          'Reply JSON only: {{"r": [{{"n": <note number>, "space": "<space key>", "confidence": 0.0-1.0, '
          '"reason": "<max 8 words, in the note\'s language>"}}, ...]}}')


async def by_model(llm: Any, texts: list[str], spaces: list[dict], default: str, per_call: int = 10) -> list[dict | None]:
    """One decision per text (None where the model gave none). Batched."""
    sps = [s for s in describe(spaces) if s["name"] != "shared"]
    keys = {s["name"] for s in sps}
    lines = "\n".join(f"- {s['name']}: {s['description']}" for s in sps)
    out: list[dict | None] = [None] * len(texts)
    for i in range(0, len(texts), per_call):
        part = texts[i:i + per_call]
        notes = "\n".join(f"{j}. {t[:400]}" for j, t in enumerate(part))
        try:
            r = await llm.json(PROMPT.format(spaces=lines, notes=notes))
        except Exception:  # noqa: BLE001 — rules take over
            continue
        for x in (r.get("r") if isinstance(r, dict) else None) or []:
            try:
                j, sp, cf = int(x.get("n")), str(x.get("space")), float(x.get("confidence", 0.5))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= j < len(part) and sp in keys:
                cf = max(0.0, min(1.0, cf))
                out[i + j] = {"space": sp, "confidence": round(cf, 2), "by": "model", "review": cf < REVIEW_BELOW,
                              "reason": str(x.get("reason") or "")[:80]}
    return out


async def route_many(texts: list[str], spaces: list[dict], default: str, llm: Any = None, home: Path | None = None,
                     cwd: str | None = None, explicit: str | None = None) -> list[dict]:
    names = [s["name"] for s in spaces]
    if explicit and explicit in names:
        return [{"space": explicit, "confidence": 1.0, "by": "explicit", "review": False,
                 "reason": "the app chose this space"} for _ in texts]
    f = by_folder(home, cwd, names) if home else None
    if f:
        return [dict(f) for _ in texts]
    got = await by_model(llm, texts, spaces, default) if llm and texts else [None] * len(texts)
    out = []
    for t, g in zip(texts, got):
        r = g or by_rules(t, spaces, default)
        if r["review"] and r["space"] != default:   # not sure: keep it in the default space, suggest the other
            r = {**r, "suggested": r["space"], "space": default}
        out.append(r)
    return out


async def route(text: str, spaces: list[dict], default: str, **kw: Any) -> dict:
    return (await route_many([text], spaces, default, **kw))[0]


def spaces_of(home: Path, servers: Any) -> list[dict]:
    """The user's spaces (with labels/descriptions) that are actually served."""
    from . import local_spaces
    try:
        sps = local_spaces.read(Path(home))["spaces"]
    except Exception:  # noqa: BLE001
        sps = []
    try:  # spaces split into projects need a project on every save: never routed to automatically
        import yaml
        raw = (yaml.safe_load((Path(home) / "contexts.yaml").read_text(encoding="utf-8")) or {}).get("contexts") or {}
        split = {k for k, v in raw.items() if isinstance(v, dict) and v.get("projects")}
    except Exception:  # noqa: BLE001
        split = set()
    have = {s["name"] for s in sps}
    return [s for s in sps if s["name"] in servers and s["name"] not in split] + [{"name": n, "label": n} for n in servers if n not in have]


def install(ui: Any, engine: Any = None) -> None:
    """Spaces → Folders, and "where would this go?" for the UI."""
    import asyncio

    async def _get(_b: dict) -> dict:
        return {"folders": folders(ui.home), "spaces": describe(spaces_of(ui.home, ui.servers))}

    async def _set(b: dict) -> dict:
        set_folder(ui.home, str(b.get("path", "")), str(b.get("space") or "") or None, list(ui.servers))
        return await _get({})

    async def _test(b: dict) -> dict:
        llm = await asyncio.to_thread(engine.llm, "import") if engine is not None and b.get("use_llm", True) else None
        return await route(str(b.get("text", ""))[:2000], spaces_of(ui.home, ui.servers), next(iter(ui.servers)),
                           llm=llm, home=ui.home, cwd=str(b.get("cwd") or "") or None)
    ui.hooks[("GET", "routing")] = _get
    ui.hooks[("POST", "routing/folder")] = _set
    ui.hooks[("POST", "routing/test")] = _test
