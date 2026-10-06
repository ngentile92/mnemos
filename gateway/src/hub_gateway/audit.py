"""Log de auditoría en JSON lines. Nunca registra valores de secretos ni contenido de memoria."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_logger = logging.getLogger("hub.audit")


class Audit:
    def __init__(self, context: str, path: str | None = None) -> None:
        self.context = context
        self.path = path
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(self, tool: str, login: str | None, outcome: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "context": self.context,
            "login": login,
            "tool": tool,
            "outcome": outcome,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        _logger.info(line)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:  # el log de auditoría nunca tumba el request
                _logger.exception("no pude escribir el log de auditoría")
        return record
