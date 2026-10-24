"""FTUE #198: the whole app in English or Spanish, with no jargon.

Coverage, all checked against gateway/src/hub_gateway/i18n_es.py (the source the UI files are built from):
- every visible string of the page, placeholder and tooltip; sentences with inline bold/italics as whole HTML;
- every fixed message the scripts show (toast, alert, confirm, textContent) and sample renderings of the templated
  ones (numbers, names, plurals);
- every error the Mnemos server can return to the screen, and every message of the desktop app (Rust).
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "gateway/src/hub_gateway"
STATIC = PKG / "local_ui_static"
spec = importlib.util.spec_from_file_location("i18n_es", PKG / "i18n_es.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
JARGON = ("MCP", "token", "context", "stdio")
INLINE = ("p", "li", "summary", "span", "h3", "label")


def _compile(pat):
    out = []
    for k, v in pat.items():
        src = re.escape(k).replace(r"\(s\)", "s?")
        src = re.sub(r"\\\{([nxyz])\\\}|\{([nxyz])\}", lambda m: r"(\d[\d.,]*)" if (m.group(1) or m.group(2)) == "n" else "(.+?)", src)
        out.append((re.compile("^" + src + "$"), v))
    return out


def tr_known(s, exact, pat, depth=0):
    """True when the Spanish UI translates s (same rules as the generated i18n.js)."""
    k = s.strip()
    if not k or k in exact or not re.search("[A-Za-z]{2}", k):
        return True
    if depth > 3:
        return False
    for rx, _ in pat:
        m = rx.match(k)
        if m:
            return True
    for sep in (" · ", " — "):
        if sep in k:
            return all(tr_known(x, exact, pat, depth + 1) for x in k.split(sep))
    parts = re.split(r"(?<=[.!?✓]) (?=[A-Z])", k)
    return len(parts) > 1 and all(tr_known(x, exact, pat, depth + 1) for x in parts)


WEB_EXACT = {**L.EXACT, **{k: v for k, v in L.SERVER.items() if "{" not in k}}
WEB_PAT = _compile({**L.PAT, **{k: v for k, v in L.SERVER.items() if "{" in k}})
DESK_PAT = _compile(L.DESKTOP_PAT)


def _page():
    html = re.sub(r"<script.*?</script>", "", (STATIC / "index.html").read_text(), flags=re.S)
    sentences = []
    for tag in INLINE:
        for m in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, flags=re.S):
            inner = m.group(1)
            if re.search(r"<(b|i|code)\b", inner) and not re.search(r"<(div|p|input|select|button|section|ul|li|textarea)\b", inner):
                sentences.append(" ".join(inner.split()))
    plain = html
    for snt in sentences:
        plain = plain.replace(snt, "")
    texts = [" ".join(t.split()) for t in re.findall(r">([^<>]+)<", " ".join(plain.split()))]
    texts += re.findall(r'(?:placeholder|title)="([^"]+)"', html)
    return sentences, [t for t in texts if re.search("[A-Za-z]{2}", t)]


def test_ui_files_are_built_from_the_tables():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/i18n_build.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert '<script src="/ui/i18n.js">' in (STATIC / "index.html").read_text()
    assert '<script src="i18n.js">' in (ROOT / "desktop/ui/index.html").read_text()


def test_every_page_string_has_spanish():
    sentences, texts = _page()
    assert sentences, "no inline-bold sentences found"
    for s in sentences:
        assert s in L.HTML, s
    for t in texts:
        assert t in L.EXACT, t


def test_no_jargon_on_any_screen():
    sentences, texts = _page()
    for t in texts + sentences + list(L.HTML.values()) + [v for v in L.EXACT.values()]:
        visible = re.sub(r"<[^>]+>", "", t)
        assert not any(re.search(rf"\b{j}\b", visible, re.I) for j in JARGON), t


def _js():
    return "\n".join(p.read_text() for p in sorted(STATIC.glob("*.js")) if p.name != "i18n.js")


def test_fixed_script_messages_have_spanish():
    js = _js()
    found = set()
    for m in re.finditer(r"(?:toast|msg|alert|confirm)\(\s*\"((?:[^\"\\]|\\.)+)\"\s*[,)]", js):
        found.add(m.group(1))
    for m in re.finditer(r"textContent\s*=\s*\"((?:[^\"\\]|\\.)+)\"", js):
        found.add(m.group(1))
    for m in re.finditer(r"'<p class=\"[^\"]*\">([^<']+)</p>'", js):
        found.add(m.group(1))
    found = {f for f in found if re.search(r"[A-Za-z]{3}", f) and not re.fullmatch(r"[a-z-]+", f) and f not in ("mg-msg", "pf-msg")}
    assert len(found) > 30
    keys = set(L.PAT) | set(L.SERVER)
    missing = [f for f in sorted(found) if not f.startswith("${") and not (
        tr_known(f, WEB_EXACT, WEB_PAT) or (f.endswith(" ") and (f + "{x}" in keys or f + "{n}" in keys)))]
    assert not missing, missing


SAMPLES = [  # renderings of the templated strings the screens build (numbers, names, plurals)
    "Saved 1 ✓", "Saved 12 ✓ · 3 skipped (already there) · 1 failed (bad JSON)", "Saved 4 notes ✓ · 1 skipped (already there)",
    "Found 1 source", "Found 3 sources", "2 new · 5 already known · 1 left out (looked like secrets) · stopped early (time limit)",
    "7 new · 2 duplicates skipped · only the first 2000 shown", "checked 24 Oct 10:00 · 3 to review · 2 done", "1 app", "4 apps",
    "2 files · read-only", "12 found · 4 saved · 1 dismissed", "Reading · 3 of 9 batches", "Paused · 4 batches read",
    "Undo last cleanup (3)", "Undo the last cleanup (3 changes)?", "Undone ✓ (2 changes)", "Undone: 4 links removed.",
    "Apply all 5 “Duplicates” suggestions? A backup is saved first and you can undo it.", "Done: 4 · 1 not done",
    "Claude Desktop now saves to Work by default.", "Cursor may use: Personal, Work.", "Codex paused: it cannot read or save until you resume it.",
    "Remove Cursor's access to your memory? Your notes stay. You can allow it again later.", "New apps will use Personal.",
    "Auto-capture on for Claude Code. Restart it to pick it up.", "Copied ✓ (812 characters) — paste it in the app's settings",
    "Facts are found with Ollama (llama3), on this computer.", "Improve my memory (download 1.2 GB)", "Moved to Work ✓",
    "Saved by Claude Desktop ✓", "Cursor found it ✓ Every connected AI now shares this memory.", "1 AI app uses your memory",
    "3 AI apps use your memory", "last used 24 Oct 10:00 · 7 days: 3 reads, 1 saves, 2 refused", "Not saved: wrong password",
    "Could not start: a download is running", "Scan failed: unknown endpoint", "12 messages waiting to be read",
    "3 of 40 match “horse”", "Saving 3…", "Download failed: HTTP 500 — Mnemos keeps using simple rules.",
]


def test_templated_strings_have_spanish():
    missing = [s for s in SAMPLES if not tr_known(s, WEB_EXACT, WEB_PAT)]
    assert not missing, missing


AI_ONLY = (  # errors only an AI app ever sees (tool results), or setup/config errors with no screen
    "HUB_LOCAL_USER", "client_id", "Requested scopes", "the user removed this app", "the user paused this app",
    "this app may not use", "this app is not allowed", "no MCP session", "unknown space {x}; spaces", "no topic",
    "dataset outside", "invalid dataset id", "no datasets", "archivo", "SKILL.md", "frontmatter", "{x}: ", "skill no", "ruta fuera",
    "unknown context",
)


def _server_errors():
    out = []
    for f in sorted(PKG.glob("local*.py")) + [PKG / n for n in ("memfiles.py", "memmigrate.py", "dispute.py", "skills.py")]:
        s = f.read_text()
        for m in re.finditer(r'(?:raise (?:ValueError|ToolError|SkillError|MemoryError_)\(|"error":\s*)((?:f?"(?:[^"\\]|\\.)*"\s*)+)', s):
            lit = "".join(re.findall(r'f?"((?:[^"\\]|\\.)*)"', m.group(1)))
            lit = re.sub(r"\{[^{}]*\}", "{x}", lit)
            if re.search("[A-Za-z]{3}", lit) and lit not in out:
                out.append(lit)
    return out


def test_server_errors_shown_to_people_have_spanish():
    errs = [e for e in _server_errors() if not any(e.startswith(a) or a in e for a in AI_ONLY)]
    assert len(errs) > 60
    missing = [e for e in errs if e not in L.SERVER]
    assert not missing, missing
    for k, v in L.SERVER.items():
        assert k.count("{x}") == v.count("{x}"), k


def test_desktop_messages_have_spanish():
    rs = (ROOT / "desktop/src-tauri/src/main.rs").read_text()
    msgs = set()
    for m in re.finditer(r'(?:Err|Ok)\((?:format!\()?"((?:[^"\\]|\\.)+)"', rs):
        msgs.add(m.group(1))
    for m in re.finditer(r'map_err\(\|e\| format!\("((?:[^"\\]|\\.)+)"', rs):
        msgs.add(m.group(1))
    for m in re.finditer(r'format!\(" ?((?:Replaced|Also remove)[^"]+)"', rs):
        msgs.add(m.group(1))
    msgs = {re.sub(r"\{[^{}]*\}", "X", m).replace("\\'", "'") for m in msgs if not m.startswith(("http", "cursor://", "1"))}
    msgs = {m.replace("{PORT}", "8765") for m in msgs}
    missing = []
    for m in sorted(msgs):
        sample = m.replace("X", "Cursor").replace("Port Cursor", "Port 8765").strip()
        sample = re.sub(r"\(all your spaces\)\.(Cursor)+ ", "(all your spaces). ", sample)
        if not tr_known(sample, L.DESKTOP_EXACT, DESK_PAT):
            missing.append(sample)
    assert not missing, missing
    js = (ROOT / "desktop/ui/app.js").read_text()
    for t in re.findall(r'T\("((?:[^"\\]|\\.)+)"\)', js) + re.findall(r'"((?:Quit|Click|Start a|Reload)[^"]+)"', js):
        assert t in L.DESKTOP_EXACT, t
