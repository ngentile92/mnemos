"""v0.20: ChatGPT's limits are said plainly wherever it shows up: the Apps card (every state), the first-run connect
step, bringing what it knows, the import page and the Apps list."""
from pathlib import Path

ST = Path(__file__).resolve().parents[1] / "src/hub_gateway/local_ui_static"


def test_said_everywhere():
    c = (ST / "connect.js").read_text()
    for s in ("Its Codex and Work modes (in the desktop app)", "Its normal chats can't reach this computer",
              "Its data export has your chats, not what ChatGPT saved", 'a.key === "chatgpt"'):
        assert s in c, s
    assert "Codex and Work modes" in (ST / "onboarding.js").read_text()
    b = (ST / "bring.js").read_text()
    assert 'ai.key !== "chatgpt"' in b and "Its data export does not include it" in b
    h = (ST / "index.html").read_text()
    assert "The export has your chats, not ChatGPT's saved memory." in h and "appear here as Codex" in h


def test_normal_chats_get_the_pasted_memory():
    assert 'a.key !== "chatgpt"' in (ST / "onboarding.js").read_text()  # its "use memory every time" step pastes it
