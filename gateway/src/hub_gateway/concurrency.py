"""Guarda ASGI contra ids JSON-RPC duplicados en vuelo dentro de una misma sesión MCP.

El SDK de MCP (streamable HTTP) enruta cada respuesta al stream del POST por `str(id)`. Si un cliente manda
dos requests concurrentes con el MISMO id en la misma sesión, el segundo pisa el stream del primero: la
respuesta de A le llega a B y A se queda esperando hasta el timeout (pasó en la práctica con el conector
de un cliente MCP real, que arranca los ids de cada llamada desde el mismo número reusando la sesión).

Reutilizar ids en vuelo es un error del cliente (JSON-RPC exige ids únicos), pero cruzar respuestas entre tools
es inaceptable. Esta guarda le asigna al duplicado un id interno único antes de pasarlo al SDK y le devuelve al
cliente la respuesta con SU id original, en el stream de SU POST: cada request recibe su propia respuesta.
Si el duplicado viene en un batch (no lo usa ningún cliente MCP actual) se rechaza con un error JSON-RPC.
"""

from __future__ import annotations

import itertools
import json
import logging
import secrets
from typing import Any

log = logging.getLogger("hub.gateway")

MAX_BODY = 2 * 1024 * 1024  # los requests MCP son chicos; secret_http_request limita el body a 256 KB
DUPLICATE_CODE = -32600  # Invalid Request
_seq = itertools.count(1)


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


def _request_ids(data: Any) -> list[Any]:
    msgs = data if isinstance(data, list) else [data]
    return [m["id"] for m in msgs if isinstance(m, dict) and "method" in m and m.get("id") is not None]


class _IdRestorer:
    """Envuelve `send` y reemplaza el token del id interno por el id original del cliente en la respuesta.

    El token es un string JSON único sin saltos de línea; en streams SSE se procesa por líneas completas
    (sin demorar eventos, que siempre terminan en \\n) y con content-length se arma el body entero.
    """

    def __init__(self, send, token: str, original: Any) -> None:
        self.send = send
        self.token = json.dumps(token).encode()
        self.original = json.dumps(original).encode()
        self.buffered: bool = False
        self.pending = b""
        self.start: dict | None = None

    def _sub(self, b: bytes) -> bytes:
        return b.replace(self.token, self.original)

    async def __call__(self, msg: dict) -> None:
        if msg["type"] == "http.response.start":
            headers = list(msg.get("headers", []))
            if any(k.lower() == b"content-length" for k, _ in headers):
                self.buffered = True
                self.start = {**msg, "headers": [(k, v) for k, v in headers if k.lower() != b"content-length"]}
                return
            await self.send(msg)
            return
        if msg["type"] != "http.response.body":
            await self.send(msg)
            return
        data = self.pending + msg.get("body", b"")
        more = msg.get("more_body", False)
        if self.buffered:
            if more:
                self.pending = data
                return
            body = self._sub(data)
            start = self.start or {"type": "http.response.start", "status": 200, "headers": []}
            start["headers"].append((b"content-length", str(len(body)).encode()))
            await self.send(start)
            await self.send({"type": "http.response.body", "body": body, "more_body": False})
            return
        if more:
            cut = data.rfind(b"\n") + 1
            self.pending, data = data[cut:], data[:cut]
            if not data:
                return
        else:
            self.pending = b""
        await self.send({**msg, "body": self._sub(data)})


class DuplicateRequestIdGuard:
    def __init__(self, app, on_duplicate=None) -> None:
        self.app = app
        self.on_duplicate = on_duplicate
        self.inflight: set[tuple[str, str]] = set()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            chunk = msg.get("body", b"")
            size += len(chunk)
            chunks.append(chunk)
            if size > MAX_BODY or not msg.get("more_body", False):
                break
        body = b"".join(chunks)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        session = headers.get("mcp-session-id", "")
        # Sin sesión (initialize o modo stateless) cada POST tiene su propio transporte en el SDK: no hay
        # colisión posible y clientes distintos pueden usar el mismo id. Con sesión: mismo criterio que el SDK,
        # str(id) (así 1 y "1" también chocan).
        data = _parse(body) if session and size <= MAX_BODY else None
        ids = _request_ids(data) if data is not None else []
        keys = {(session, str(i)) for i in ids}
        dup = sorted(k[1] for k in keys if k in self.inflight)
        if dup:
            if self.on_duplicate:
                self.on_duplicate(dup)
            if isinstance(data, list):
                log.warning("id JSON-RPC duplicado en vuelo en un batch (sesión %s…, id %s): rechazado", session[:8], dup)
                await self._reject(send, dup)
                return
            original = data["id"]
            token = f"hub-dup-{next(_seq)}-{secrets.token_hex(6)}"
            log.warning("id JSON-RPC duplicado en vuelo (sesión %s…, id %s): reasignado a un id interno", session[:8], dup)
            body = json.dumps({**data, "id": token}).encode()
            keys = {(session, token)}
            send = _IdRestorer(send, token, original)
        self.inflight |= keys
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, send)
        finally:
            self.inflight -= keys

    @staticmethod
    async def _reject(send, dup: list[str]) -> None:
        err = {"jsonrpc": "2.0", "id": None, "error": {
            "code": DUPLICATE_CODE,
            "message": f"id de request duplicado en vuelo en esta sesión ({', '.join(dup)}): "
                       "el cliente tiene que usar ids únicos para requests concurrentes; reintentá"}}
        payload = json.dumps(err).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
        await send({"type": "http.response.body", "body": payload})
