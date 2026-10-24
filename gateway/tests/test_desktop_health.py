"""The desktop app polls /health to leave "Starting…": that request must pass the server's Host + token gate."""
import asyncio
import re
from pathlib import Path

from hub_gateway.local import TokenGate

ROOT = Path(__file__).resolve().parents[2]


def _desktop_headers(token: str) -> list[tuple[bytes, bytes]]:
    rs = (ROOT / "desktop/src-tauri/src/main.rs").read_text()
    fmt = re.search(r'fn health_request\(tok: &str\) -> String \{\s*format!\("([^"]+)"', rs).group(1)
    port = re.search(r"const PORT: u16 = (\d+);", rs).group(1)
    raw = fmt.replace("{PORT}", port).replace("{tok}", token).replace("\\r\\n", "\n")
    lines = [l for l in raw.split("\n")[1:] if l]
    assert raw.startswith("GET /health ")
    return [(k.strip().lower().encode(), v.strip().encode()) for k, v in (l.split(":", 1) for l in lines)]


def _call(headers):
    seen = {}

    async def app(scope, receive, send):
        seen["through"] = True

    async def send(msg):
        if msg["type"] == "http.response.start":
            seen["status"] = msg["status"]

    gate = TokenGate(app, "secret", 8765)
    asyncio.run(gate({"type": "http", "path": "/health", "method": "GET", "headers": headers}, None, send))
    return seen


def test_desktop_health_poll_is_authenticated():
    assert _call(_desktop_headers("secret")) == {"through": True}


def test_wrong_token_or_host_is_refused():
    assert _call(_desktop_headers("nope")).get("status") == 401
    assert _call([(b"host", b"127.0.0.1"), (b"authorization", b"Bearer secret")]).get("status") == 403
