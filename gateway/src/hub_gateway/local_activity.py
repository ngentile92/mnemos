"""Activity feed (local mode): one line per call an AI app makes through the single connector.

"Cursor saved 'Uses pnpm' in work · 10:42", "ChatGPT searched 'trip' in personal → 3 notes",
"Claude Desktop asked for work: denied". Stored in <home>/data/activity.sqlite (shared by the HTTP server and the
stdio processes AI apps start), trimmed to the last MAX_ROWS events. The Notes screen's own edits are not logged
here: they are the user's, and the ledger already keeps their history. Only short summaries are kept (the first
words of a saved note or a query), never whole notes.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

MAX_ROWS = 5000
READS = {"memory_search", "memory_answer", "memory_list", "memory_entity", "memory_history", "skills_list",
         "skills_get", "hub_whoami", "memory_dispute"}
VERBS = {"memory_search": "searched", "memory_answer": "asked", "memory_list": "listed notes",
         "memory_entity": "looked up", "memory_history": "read the history of a note", "skills_list": "listed skills",
         "skills_get": "read the skill", "hub_whoami": "checked its spaces", "hub_use_context": "switched to",
         "memory_save": "saved", "memory_update": "corrected a note", "memory_delete": "deleted a note",
         "memory_undo": "undid a change", "memory_promote": "copied a note to shared", "memory_pin": "pinned a note",
         "memory_mark_obsolete": "marked a note out of date", "memory_set_validity": "set dates on a note",
         "memory_dispute": "checked a correction"}


def _short(s: Any, n: int = 80) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(tool: str, args: dict, result: dict | None) -> tuple[str, str]:
    """(kind, summary) for one call. kind: read | write."""
    kind = "read" if tool in READS else "write"
    verb = VERBS.get(tool, tool)
    if tool == "memory_save":
        if (result or {}).get("status") == "duplicate":
            return kind, f"tried to save a duplicate: “{_short(args.get('text'))}”"
        flag = " (looks like task detail)" if (result or {}).get("filter") else ""
        return kind, (f"saved “{_short(args.get('text'))}”" + (" to shared" if args.get("target") == "shared" else "")
                      + flag)
    if tool in ("memory_search", "memory_answer", "memory_dispute"):
        q = args.get("query") or args.get("question") or args.get("correction")
        n = len((result or {}).get("results") or (result or {}).get("citations") or (result or {}).get("proposals") or [])
        return kind, f"{verb} “{_short(q, 60)}”" + (f" → {n} notes" if tool == "memory_search" else "")
    if tool == "memory_entity":
        return kind, f"looked up {_short(args.get('name') or 'all names', 40)}"
    if tool == "skills_get":
        return kind, f"read the skill {_short(args.get('name'), 40)}"
    if tool == "memory_update":
        return kind, f"corrected a note: “{_short(args.get('text'))}”"
    return kind, verb


class Activity:
    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "data" / "activity.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, app TEXT, label TEXT,
                          space TEXT, tool TEXT, kind TEXT, summary TEXT, ok INTEGER, note_id TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS ev_app ON events(app, id)")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def record(self, app: str, label: str, space: str | None, tool: str, args: dict, result: dict | None,
               error: str | None) -> None:
        kind, summary = describe(tool, args or {}, result)
        if error:
            kind, summary = "denied", f"{VERBS.get(tool, tool)}: {_short(error, 140)}"
        ts = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        nid = str(args.get("id") or "")[:40] or None
        with self._db() as db:
            db.execute("INSERT INTO events(ts, app, label, space, tool, kind, summary, ok, note_id) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (ts, app, label, space, tool, kind, summary, 0 if error else 1, nid))
            db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (MAX_ROWS,))

    def recent(self, limit: int = 100, app: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT ts, app, label, space, tool, kind, summary, ok, note_id FROM events"
        params: list[Any] = []
        if app:
            q += " WHERE app = ?"
            params.append(app)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._db() as db:
            rows = db.execute(q, params).fetchall()
        keys = ("ts", "app", "label", "space", "tool", "kind", "summary", "ok", "note_id")
        return [dict(zip(keys, r)) for r in rows]

    def stats(self) -> dict[str, dict[str, Any]]:
        """Per app: reads / writes / denied in the last 7 days and the last event time."""
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat(timespec="seconds")
        with self._db() as db:
            rows = db.execute("SELECT app, kind, COUNT(*), MAX(ts) FROM events WHERE ts >= ? GROUP BY app, kind",
                              (since,)).fetchall()
            last = dict(db.execute("SELECT app, MAX(ts) FROM events GROUP BY app").fetchall())
        out: dict[str, dict[str, Any]] = {}
        for app, kind, n, _ts in rows:
            out.setdefault(app, {"read": 0, "write": 0, "denied": 0})[kind] = n
        for app, ts in last.items():
            out.setdefault(app, {"read": 0, "write": 0, "denied": 0})["last"] = ts
        return out

    def capture(self, days: int = 7) -> dict[str, dict[str, int]]:
        """Capture metrics per app (counts only): saves, duplicates, saves that look like task detail, searches."""
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self._db() as db:
            rows = db.execute(
                "SELECT app, "
                "SUM(tool='memory_save' AND ok=1 AND summary LIKE 'saved%'), "
                "SUM(tool='memory_save' AND summary LIKE 'tried to save a duplicate%'), "
                "SUM(tool='memory_save' AND summary LIKE '%(looks like task detail)'), "
                "SUM(tool IN ('memory_search','memory_answer') AND ok=1), "
                "SUM(tool IN ('memory_update','memory_delete','memory_mark_obsolete') AND ok=1) "
                "FROM events WHERE ts >= ? GROUP BY app", (since,)).fetchall()
        keys = ("saves", "duplicates", "task_detail", "searches", "corrections")
        return {r[0]: dict(zip(keys, (int(x or 0) for x in r[1:]))) for r in rows}
