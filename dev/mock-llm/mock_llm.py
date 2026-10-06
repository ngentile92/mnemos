"""LLM falso compatible con la API de OpenAI (chat completions), SOLO para tests locales.

Permite levantar Cognee sin API keys ni costo: devuelve el JSON mínimo válido para el
schema que pida cada llamada (response_format json_schema o tools). La "extracción" queda
vacía, pero los chunks se indexan con embeddings reales (fastembed) y alcanzan para probar
el aislamiento por dataset. No usar con datos reales.
"""

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def minimal(schema, defs, depth=0):
    if depth > 12 or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return minimal(defs.get(name, {}), defs, depth + 1)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            opts = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return minimal(opts[0], defs, depth + 1)
    if "allOf" in schema:
        return minimal(schema["allOf"][0], defs, depth + 1)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        req = schema.get("required", list(props))
        return {k: minimal(props[k], defs, depth + 1) for k in req if k in props}
    if t == "array":
        return []
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    if t == "null":
        return None
    return "mock"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silencioso
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        return self._send(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": f"mock: ruta no soportada {self.path}"}})
        message = {"role": "assistant", "content": "mock"}
        finish = "stop"
        rf = req.get("response_format") or {}
        if rf.get("type") == "json_schema":
            schema = rf.get("json_schema", {}).get("schema", {})
            message["content"] = json.dumps(minimal(schema, schema.get("$defs", {})))
        elif rf.get("type") == "json_object":
            message["content"] = "{}"
        elif req.get("tools"):
            fn = req["tools"][0]["function"]
            params = fn.get("parameters", {})
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                "function": {"name": fn["name"], "arguments": json.dumps(minimal(params, params.get("$defs", {})))},
            }]}
            finish = "tool_calls"
        self._send(200, {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": req.get("model", "mock"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 11435), Handler).serve_forever()
