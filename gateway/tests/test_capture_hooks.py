"""Live capture fast lane: hook script banks prompts (no model), installers edit only their own entries."""
import io
import json
import subprocess
import sys
import time

from hub_gateway import hook, local_hooks, local_spool


def test_bank_cleans_and_skips_secrets(tmp_path):
    assert local_spool.bank(tmp_path, "claude_code", "s1", "I lead the Atlas project at Acme.\n```py\nx=1\n```")
    assert not local_spool.bank(tmp_path, "claude_code", "s1", "ok")
    assert not local_spool.bank(tmp_path, "claude_code", "s1", "my key is sk-abcdefghijklmnopqrstuvwx")
    assert not local_spool.bank(tmp_path, "notanapp", "s1", "I lead the Atlas project at Acme.")
    s = local_spool.sessions(tmp_path)
    assert len(s) == 1 and s[0]["turns"] == 1
    assert local_spool.read(s[0]["path"])[0]["text"] == "I lead the Atlas project at Acme."
    assert (tmp_path / "spool" / "claude_code" / "s1.jsonl").stat().st_mode & 0o077 == 0


def _run(monkeypatch, capsys, argv, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main(argv) == 0
    return capsys.readouterr().out


def test_hook_prompt_and_start(tmp_path, monkeypatch, capsys):
    h = tmp_path / "Mnemos"
    out = _run(monkeypatch, capsys, ["--home", str(h), "--app", "cursor", "--event", "prompt"],
               {"prompt": "We decided to use Postgres for Atlas because of JSONB.", "conversation_id": "c1"})
    assert json.loads(out) == {"continue": True}
    assert local_spool.counts(h) == {"cursor": 1}
    (h / "profile").mkdir(parents=True)
    (h / "profile" / "work.md").write_text("Ana is a backend engineer at Acme.")
    out = _run(monkeypatch, capsys, ["--home", str(h), "--app", "claude_code", "--event", "start"], {})
    assert "Ana is a backend engineer" in out
    out = _run(monkeypatch, capsys, ["--home", str(h), "--app", "cursor", "--event", "start"], {})
    assert "Ana" in json.loads(out)["additional_context"]


def test_paused_app_is_not_captured(tmp_path, monkeypatch, capsys):
    h = tmp_path / "Mnemos"
    (h / "data").mkdir(parents=True)
    (h / "data" / "apps.json").write_text(json.dumps({"apps": {"claude-code": {"paused": True}}}))
    out = _run(monkeypatch, capsys, ["--home", str(h), "--app", "claude_code", "--event", "prompt"],
               {"prompt": "I lead the Atlas project at Acme.", "session_id": "x"})
    assert out == "" and local_spool.counts(h) == {}


def test_hook_is_fast_as_a_process(tmp_path):
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "hub_gateway.hook", "--home", str(tmp_path), "--app", "codex",
                        "--event", "prompt"], input=json.dumps({"prompt": "I prefer tabs over spaces always.",
                                                                "session_id": "z"}),
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout == "" and time.monotonic() - t < 2
    assert local_spool.counts(tmp_path) == {"codex": 1}


def test_install_keeps_user_settings_and_uninstalls_cleanly(tmp_path):
    uh, home = tmp_path / "u", tmp_path / "Mnemos"
    cs = uh / ".claude" / "settings.json"
    cs.parent.mkdir(parents=True)
    mine = {"type": "command", "command": "echo mine"}
    cs.write_text(json.dumps({"model": "x", "hooks": {"UserPromptSubmit": [{"hooks": [mine]}]}}))
    local_hooks.install("claude_code", home, uh, python="/py")
    local_hooks.install("claude_code", home, uh, python="/py")  # idempotent
    d = json.loads(cs.read_text())
    assert d["model"] == "x" and len(d["hooks"]["UserPromptSubmit"]) == 2 and "SessionStart" in d["hooks"]
    assert "--event prompt" in d["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"]
    assert (cs.parent / "settings.json.mnemos-backup").exists()
    assert local_hooks.status(uh)["claude_code"]["installed"]
    local_hooks.uninstall("claude_code", uh)
    d = json.loads(cs.read_text())
    assert d["hooks"] == {"UserPromptSubmit": [{"hooks": [mine]}]}
    local_hooks.install("cursor", home, uh, python="/py")
    c = json.loads((uh / ".cursor" / "hooks.json").read_text())
    assert c["version"] == 1 and "--app cursor" in c["hooks"]["beforeSubmitPrompt"][0]["command"]
    local_hooks.install("codex", home, uh, python="/py")
    assert "UserPromptSubmit" in json.loads((uh / ".codex" / "hooks.json").read_text())["hooks"]
