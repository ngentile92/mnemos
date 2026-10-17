"""Which "brain" does the smart parts of local mode, and the built-in model that makes Ollama optional.

Features and the engines that can run them, in order of preference:
  * import  (facts from chats)       → API (if the user enabled it for import) → Ollama → Built-in → Rules
  * hygiene (contradictions)         → API (if enabled for hygiene)          → Ollama → Built-in → Rules
  * search  (embeddings for search/dedupe) → Ollama (HUB_EMBED_URL/HUB_EMBED_MODEL) → Built-in → keyword only
An API key never sees search: only import and hygiene, each behind its own toggle, and the UI warns that the text
leaves the Mac.

Built-in = two small GGUF models run in-process by llama.cpp (llama-cpp-python, bundled in the Desktop app's
Python; Metal on Apple Silicon). Downloaded once, on first run or with "Improve my memory": resumable (HTTP Range
on <file>.part), sha256-checked, stored in <MNEMOS_HOME>/models. Declining or a failed download keeps Rules.

Settings: <home>/engine.json (no secrets). API keys: macOS Keychain through /usr/bin/security (fed on stdin, never
on the command line), service io.mnemos.desktop.apikey; elsewhere (or MNEMOS_NO_KEYCHAIN=1) <home>/apikeys.json 0600.
Env: MNEMOS_MODELS_MANIFEST (JSON list replacing MODELS, for tests/CI), MNEMOS_OLLAMA=0 (ignore Ollama),
MNEMOS_DISCOVER_LLM=0 (rules only for import, tests), MNEMOS_BUILTIN=0 (never load the built-in models).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("hub.engine")

MODELS: list[dict[str, Any]] = [
    {"role": "llm", "name": "Qwen2.5 1.5B Instruct (Q4_K_M)", "license": "Apache-2.0",
     "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf", "size": 1117320736,
     "sha256": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
     "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/"
            "qwen2.5-1.5b-instruct-q4_k_m.gguf"},
    {"role": "embed", "name": "EmbeddingGemma 300M (Q8_0)", "license": "Gemma Terms of Use",
     "file": "embeddinggemma-300M-Q8_0.gguf", "size": 333590944,
     "sha256": "b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63",
     "url": "https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/0f741b5a6585bd53aeb15cd1372c56f2a0f65e12/"
            "embeddinggemma-300M-Q8_0.gguf"},
]
API_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-4-5"}
KEYCHAIN_SERVICE = "io.mnemos.desktop.apikey"
FEATURES = ("import", "hygiene")


def manifest() -> list[dict[str, Any]]:
    p = os.environ.get("MNEMOS_MODELS_MANIFEST")
    return json.loads(Path(p).read_text()) if p else MODELS


def runtime_ok() -> bool:
    try:
        import llama_cpp
        return bool(llama_cpp.__version__)
    except Exception:  # noqa: BLE001 — not bundled (pip install without the [local-ai] extra)
        return False


# ------------------------------------------------------------------ download (resumable, checksummed)
class Downloader:
    def __init__(self, home: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.dir = home / "models"
        self.state: dict[str, Any] = {"state": "idle"}
        self.task: asyncio.Task | None = None
        self.transport = transport

    def path(self, m: dict) -> Path:
        return self.dir / m["file"]

    def present(self, m: dict) -> bool:
        p = self.path(m)
        return p.exists() and p.stat().st_size == m["size"]

    def ready(self) -> bool:
        return all(self.present(m) for m in manifest())

    def start(self) -> dict:
        if self.task and not self.task.done():
            return self.state
        self.task = asyncio.create_task(self.run())
        return {"state": "downloading"}

    async def run(self, chunk: int = 1 << 20) -> None:
        todo = [m for m in manifest() if not self.present(m)]
        total = sum(m["size"] for m in manifest())
        base = sum(m["size"] for m in manifest() if self.present(m))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = {"state": "downloading", "done": base, "total": total, "file": ""}
        try:
            for m in todo:
                await self._one(m, chunk)
                base += m["size"]
                self.state.update(done=base)
            self.state = {"state": "ready", "done": total, "total": total}
        except Exception as e:  # noqa: BLE001 — keep Rules; the .part stays for a resume
            log.warning("model download failed: %s", e)
            self.state = {**self.state, "state": "error", "error": str(e)[:300]}

    async def _one(self, m: dict, chunk: int) -> None:
        part = self.path(m).with_suffix(".part")
        have = part.stat().st_size if part.exists() else 0
        if have > m["size"]:
            part.unlink()
            have = 0
        h = hashlib.sha256()
        if have:
            with part.open("rb") as f:
                for b in iter(lambda: f.read(chunk), b""):
                    h.update(b)
        done0 = self.state["done"]
        self.state.update(file=m["name"], done=done0 + have)
        if have < m["size"]:
            headers = {"Range": f"bytes={have}-"} if have else {}
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), follow_redirects=True,
                                         transport=self.transport) as c:
                async with c.stream("GET", m["url"], headers=headers) as r:
                    if r.status_code == 200 and have:  # server ignored Range: start over
                        have, h = 0, hashlib.sha256()
                        part.write_bytes(b"")
                    elif r.status_code not in (200, 206):
                        raise RuntimeError(f"download failed: HTTP {r.status_code}")
                    with part.open("ab") as f:
                        async for b in r.aiter_bytes(chunk):
                            f.write(b)
                            h.update(b)
                            have += len(b)
                            self.state["done"] = done0 + have
                            if have > m["size"]:
                                raise RuntimeError("download larger than expected")
        self.state["file"] = m["name"] + " (checking)"
        if have != m["size"] or h.hexdigest() != m["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{m['file']}: checksum mismatch, downloaded file removed")
        os.replace(part, self.path(m))


# ------------------------------------------------------------------ engines
def _json(text: str) -> dict:
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        mt = re.search(r"\{.*\}", text or "", re.S)
        try:
            d = json.loads(mt.group(0)) if mt else {}
        except json.JSONDecodeError:
            d = {}
    return d if isinstance(d, dict) else {}


class OllamaLLM:
    kind = "ollama"

    def __init__(self, url: str, model: str) -> None:
        self.url, self.model, self.name = url.rstrip("/"), model, f"Ollama · {model}"

    async def json(self, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as c:
            r = await c.post(self.url + "/api/generate", json={"model": self.model, "prompt": prompt, "stream": False,
                                                              "format": "json", "options": {"temperature": 0}})
            r.raise_for_status()
        return _json(r.json().get("response") or "{}")


class ApiLLM:
    kind = "api"

    def __init__(self, provider: str, key: str, model: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider, self.key, self.transport = provider, key, transport
        self.model = model or API_MODELS[provider]
        self.name = f"{'OpenAI' if provider == 'openai' else 'Anthropic'} · {self.model}"

    async def json(self, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as c:
            if self.provider == "openai":
                r = await c.post("https://api.openai.com/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {self.key}"},
                                 json={"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                                       "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                return _json(r.json()["choices"][0]["message"]["content"])
            r = await c.post("https://api.anthropic.com/v1/messages",
                             headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                             json={"model": self.model, "max_tokens": 2048, "temperature": 0,
                                   "messages": [{"role": "user", "content": prompt + "\nReply with the JSON only."}]})
            r.raise_for_status()
            return _json("".join(b.get("text", "") for b in r.json().get("content") or []))


class _Llama:
    """One llama.cpp model per process, loaded on first use, calls serialised (llama.cpp is not re-entrant)."""

    def __init__(self, path: Path, **kw: Any) -> None:
        self.path, self.kw, self._m, self._lock = path, kw, None, threading.Lock()

    def get(self) -> Any:
        if self._m is None:
            import atexit

            from llama_cpp import Llama
            self._m = Llama(model_path=str(self.path), verbose=False, n_threads=max(2, (os.cpu_count() or 4) - 2),
                            n_gpu_layers=-1 if sys.platform == "darwin" else 0, **self.kw)
            atexit.register(self.close)  # free Metal buffers before the C++ teardown (else a GGML_ASSERT at exit)
        return self._m

    def close(self) -> None:
        m, self._m = self._m, None
        if m is not None and hasattr(m, "close"):
            try:
                m.close()
            except Exception:  # noqa: BLE001
                pass


class BuiltinLLM:
    kind = "builtin"
    mode = "classify"  # import: judge rule-proposed sentences instead of writing facts (better for a 1.5B model)

    def __init__(self, m: dict, path: Path) -> None:
        self.name, self.model = f"Built-in · {m['name']}", m["file"]
        self._l = _Llama(path, n_ctx=4096)

    def _run(self, prompt: str) -> str:
        with self._l._lock:
            out = self._l.get().create_chat_completion(
                messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=1024,
                response_format={"type": "json_object"})
        return out["choices"][0]["message"]["content"]

    async def json(self, prompt: str) -> dict:
        return _json(await asyncio.to_thread(self._run, prompt))


class BuiltinEmbedder:
    """Same interface as local_search.OllamaEmbedder (async embed(texts), .model)."""
    kind = "builtin"

    def __init__(self, m: dict, path: Path) -> None:
        self.name, self.model = f"Built-in · {m['name']}", "builtin:" + m["file"]
        self._l = _Llama(path, embedding=True, n_ctx=2048, n_batch=2048, n_ubatch=2048)

    def _run(self, texts: list[str]) -> list[list[float]]:
        with self._l._lock:
            m = self._l.get()
            out = []
            for t in texts:
                e = m.embed("title: none | text: " + t[:1500])  # EmbeddingGemma document format
                if e and isinstance(e[0], list):  # a model without pooling gives one vector per token: mean-pool
                    e = [sum(col) / len(e) for col in zip(*e)]
                out.append([float(x) for x in e])
            return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._run, texts)


class EmbedProxy:
    """Handed to LocalIndex at startup; follows the engine as models get downloaded. Falsy when there is none, so
    the index stays keyword-only."""

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    def _cur(self) -> Any:
        return self.engine.embedder()

    def __bool__(self) -> bool:
        return self._cur() is not None

    @property
    def model(self) -> str:
        cur = self._cur()
        return cur.model if cur else "none"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        cur = self._cur()
        if cur is None:
            raise LookupError("no embedding engine")
        return await cur.embed(texts)


# ------------------------------------------------------------------ API keys
def _keychain() -> bool:
    return sys.platform == "darwin" and os.environ.get("MNEMOS_NO_KEYCHAIN") != "1" and Path("/usr/bin/security").exists()


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class KeyStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.file = home / "apikeys.json"

    def _acct(self, provider: str) -> str:
        return f"{provider}:{self.home}"

    def get(self, provider: str) -> str | None:
        if _keychain():
            r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a",
                                self._acct(provider), "-w"], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() or None if r.returncode == 0 else None
        try:
            return json.loads(self.file.read_text()).get(provider) or None
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, provider: str, key: str | None) -> None:
        if _keychain():
            acct = self._acct(provider)
            subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", acct],
                           capture_output=True, timeout=10)
            if key:  # interactive mode reads the command from stdin: the key never shows up in `ps`
                cmd = f"add-generic-password -U -s {_q(KEYCHAIN_SERVICE)} -a {_q(acct)} -w {_q(key)}\n"
                r = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
                if r.returncode != 0 or self.get(provider) != key:
                    raise RuntimeError("could not save the key in the Keychain")
            return
        try:
            d = json.loads(self.file.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        if key:
            d[provider] = key
        else:
            d.pop(provider, None)
        self.file.write_text(json.dumps(d))
        os.chmod(self.file, 0o600)


# ------------------------------------------------------------------ the engine
def ollama_model() -> tuple[str, str] | None:
    """(url, model) when a local Ollama answers: HUB_OLLAMA_URL/HUB_ANSWER_MODEL, else autodetect on :11434."""
    if os.environ.get("MNEMOS_OLLAMA") == "0":
        return None
    url = (os.environ.get("HUB_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        tags = httpx.get(url + "/api/tags", timeout=1.5, trust_env=False).json().get("models") or []
    except Exception:  # noqa: BLE001
        return None
    emb_fam = {"bert", "nomic-bert", "xlm-roberta", "xlmroberta", "gemma-embedding", "gemma3-embedding"}
    names = [str(m.get("name", "")) for m in tags if "embed" not in str(m.get("name", ""))
             and str((m.get("details") or {}).get("family", "")).lower() not in emb_fam]  # embedding-only models
    want = os.environ.get("HUB_ANSWER_MODEL")
    if want and want in names:
        return url, want
    for pref in ("llama3.1", "llama3.2", "qwen2.5", "qwen3", "mistral", "gemma"):
        m = next((n for n in names if n.startswith(pref)), None)
        if m:
            return url, m
    return (url, names[0]) if names else None


class Engine:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.cfg_file = self.home / "engine.json"
        self.dl = Downloader(self.home)
        self.keys = KeyStore(self.home)
        self._llm: BuiltinLLM | None = None
        self._emb: BuiltinEmbedder | None = None
        self._ollama: tuple[float, tuple[str, str] | None] = (0.0, None)

    # settings
    def cfg(self) -> dict:
        try:
            d = json.loads(self.cfg_file.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        d.setdefault("builtin", "ask")  # ask | declined | on
        d.setdefault("api", {})
        return d

    def save_cfg(self, d: dict) -> None:
        self.cfg_file.write_text(json.dumps(d, indent=1))

    def ollama(self) -> tuple[str, str] | None:
        t, v = self._ollama
        if time.monotonic() - t > 30:
            v = ollama_model()
            self._ollama = (time.monotonic(), v)
        return v

    def builtin_ready(self) -> bool:
        return os.environ.get("MNEMOS_BUILTIN") != "0" and self.dl.ready() and runtime_ok()

    def _model(self, role: str) -> dict | None:
        return next((m for m in manifest() if m["role"] == role), None)

    # engines
    def llm(self, feature: str) -> Any:
        """The LLM for "import" or "hygiene"; None = rules."""
        if feature == "import" and os.environ.get("MNEMOS_DISCOVER_LLM") == "0":
            return None
        api = self.cfg()["api"]
        if api.get("provider") in API_MODELS and api.get(feature):
            key = self.keys.get(api["provider"])
            if key:
                return ApiLLM(api["provider"], key, api.get("model") or None)
        o = self.ollama()
        if o:
            return OllamaLLM(*o)
        m = self._model("llm")
        if m and self.builtin_ready():
            if self._llm is None:
                self._llm = BuiltinLLM(m, self.dl.path(m))
            return self._llm
        return None

    def embedder(self) -> Any:
        from .local_search import OllamaEmbedder
        o = OllamaEmbedder.from_env()
        if o:
            o.name, o.kind = f"Ollama · {o.model}", "ollama"
            return o
        m = self._model("embed")
        if m and self.builtin_ready():
            if self._emb is None:
                self._emb = BuiltinEmbedder(m, self.dl.path(m))
            return self._emb
        return None

    # UI
    def status(self) -> dict:
        def show(e: Any) -> dict:
            return {"kind": e.kind, "name": e.name} if e else {"kind": "rules", "name": "Rules"}
        c, api = self.cfg(), self.cfg()["api"]
        ms = manifest()
        return {"active": {"import": show(self.llm("import")), "hygiene": show(self.llm("hygiene")),
                           "search": show(self.embedder()) if self.embedder() else {"kind": "rules", "name": "Keywords"}},
                "builtin": {"choice": c["builtin"], "ready": self.dl.ready(), "runtime": runtime_ok(),
                            "download": self.dl.state, "size": sum(m["size"] for m in ms),
                            "models": [{"role": m["role"], "name": m["name"], "size": m["size"], "license": m.get("license"),
                                        "present": self.dl.present(m)} for m in ms]},
                "ollama": (self.ollama() or (None, None))[1],
                "api": {"provider": api.get("provider"), "model": api.get("model") or "",
                        "has_key": bool(api.get("provider") and self.keys.get(api["provider"])),
                        **{f: bool(api.get(f)) for f in FEATURES}, "defaults": API_MODELS}}

    async def h_status(self, _b: dict) -> dict:
        return await asyncio.to_thread(self.status)

    async def h_download(self, _b: dict) -> dict:
        if not runtime_ok():
            raise ValueError("this install has no built-in model runtime (install mnemos-hub[local-ai])")
        c = self.cfg()
        c["builtin"] = "on"
        self.save_cfg(c)
        return self.dl.start()

    async def h_decline(self, _b: dict) -> dict:
        c = self.cfg()
        c["builtin"] = "declined"
        self.save_cfg(c)
        return {"ok": True}

    async def h_remove(self, _b: dict) -> dict:
        if self.dl.task and not self.dl.task.done():
            raise ValueError("a download is running")
        self._llm = self._emb = None
        for m in manifest():
            for p in (self.dl.path(m), self.dl.path(m).with_suffix(".part")):
                p.unlink(missing_ok=True)
        c = self.cfg()
        c["builtin"] = "declined"
        self.save_cfg(c)
        self.dl.state = {"state": "idle"}
        return {"ok": True}

    async def h_api(self, b: dict) -> dict:
        prov = b.get("provider") or None
        if prov not in (None, *API_MODELS):
            raise ValueError("provider: openai or anthropic")
        c = self.cfg()
        old = c["api"].get("provider")
        key = str(b.get("key") or "").strip()
        if key and (len(key) > 300 or not re.fullmatch(r"[\x21-\x7e]+", key)):
            raise ValueError("that does not look like an API key")
        model = str(b.get("model") or "").strip()[:80]
        if model and not re.fullmatch(r"[\w.:/-]+", model):
            raise ValueError("model name: letters, digits, . : / - only")
        if old and old != prov:
            await asyncio.to_thread(self.keys.set, old, None)
        if prov and key:
            await asyncio.to_thread(self.keys.set, prov, key)
        c["api"] = {"provider": prov, "model": model, **{f: bool(b.get(f)) and bool(prov) for f in FEATURES}}
        self.save_cfg(c)
        return await self.h_status({})

    async def h_api_test(self, _b: dict) -> dict:
        api = self.cfg()["api"]
        key = api.get("provider") and await asyncio.to_thread(self.keys.get, api["provider"])
        if not key:
            raise ValueError("no API key saved")
        try:
            d = await ApiLLM(api["provider"], key, api.get("model") or None).json('Reply {"ok": true}')
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": type(e).__name__}
        return {"ok": bool(d.get("ok"))}

    def install(self, ui: Any) -> None:
        h = ui.hooks
        h[("GET", "engine")] = self.h_status
        h[("POST", "engine/download")] = self.h_download
        h[("POST", "engine/decline")] = self.h_decline
        h[("POST", "engine/remove")] = self.h_remove
        h[("POST", "engine/api")] = self.h_api
        h[("POST", "engine/api/test")] = self.h_api_test


_ENGINES: dict[str, Engine] = {}


def get(home: Path) -> Engine:
    k = str(Path(home).resolve())
    if k not in _ENGINES:
        _ENGINES[k] = Engine(Path(home))
    return _ENGINES[k]
