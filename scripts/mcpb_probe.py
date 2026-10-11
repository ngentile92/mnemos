#!/usr/bin/env python3
"""Launches an unpacked mnemos.mcpb at /tmp/mx the way Claude Desktop does (uv run, stdio) and calls the tools."""
import asyncio, json, os, time
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
async def main():
    t0 = time.time()
    t = StdioTransport(command="uv", args=["run", "--directory", "/tmp/mx", "src/server.py"],
                       env={**os.environ, "MNEMOS_HOME": "/tmp/mx-home", "MNEMOS_CONTEXT": "personal", "UV_CACHE_DIR": "/tmp/mx-uvcache"})
    async with Client(t, timeout=600) as c:
        t1 = time.time()
        tools = [x.name for x in await c.list_tools()]
        await c.call_tool("memory_save", {"text": "Prueba mcpb: Ana usa Claude Desktop"})
        r = await c.call_tool("memory_search", {"query": "Claude Desktop", "mode": "keyword"})
        s = await c.call_tool("skills_list", {})
        print(f"start {t1-t0:.0f}s; {len(tools)} tools; search hits {len(r.structured_content['results'])}; skills {json.dumps(s.structured_content)[:80]}")
asyncio.run(main())
