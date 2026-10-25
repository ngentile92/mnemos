"""Act as Codex: start Mnemos exactly as its config says, initialize, list tools, then one read (or save facts).
Usage: python scripts/mcp_firstcall.py <codex config.toml> [--save fact ...]"""
import json, subprocess, sys, tomllib

cfg = tomllib.load(open(sys.argv[1], "rb"))["mcp_servers"]["mnemos"]
p = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def call(m):
    p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
    return json.loads(p.stdout.readline()) if "id" in m else None


call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "codex-mcp-client", "version": "0.50.0"}}})
call({"jsonrpc": "2.0", "method": "notifications/initialized"})
facts = sys.argv[3:] if sys.argv[2:3] == ["--save"] else []
if facts:
    for i, f in enumerate(facts):
        call({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": "memory_remember", "arguments": {"text": f}}})
else:
    names = [t["name"] for t in call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_start" if "memory_start" in names else names[0], "arguments": {}}})
p.stdin.close(); p.wait(timeout=20)
