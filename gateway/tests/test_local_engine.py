"""Engine choice (Rules / Built-in / Ollama / API), the built-in model download (resumable, checksummed), API keys
kept out of engine.json, and the semantic parts of import/cleanup. No network: transports are mocked; the
optional real-model test runs only when llama_cpp and MNEMOS_TEST_GGUF are available."""
from __future__ import annotations

import hashlib
import json
import os
import stat

import httpx
import pytest

from hub_gateway import local, local_engine as le, local_hygiene as lh
from hub_gateway.local_ui import UI

BLOB = os.urandom(300_000)


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    ms = [{"role": r, "name": f"Test {r}", "file": f"{r}.gguf", "size": len(BLOB),
           "sha256": hashlib.sha256(BLOB).hexdigest(), "url": f"https://models.test/{r}.gguf"} for r in ("llm", "embed")]
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(ms))
    monkeypatch.setenv("MNEMOS_MODELS_MANIFEST", str(p))
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_NO_KEYCHAIN", "1")
    monkeypatch.delenv("HUB_EMBED_URL", raising=False)
    monkeypatch.delenv("MNEMOS_DISCOVER_LLM", raising=False)
    return ms


def server(blob=BLOB, seen=None):
    def handle(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("range")
        if seen is not None:
            seen.append(rng)
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=blob[start:])
        return httpx.Response(200, content=blob)
    return httpx.MockTransport(handle)


async def test_download_resumes_and_verifies(tmp_path, manifest):
    d = le.Downloader(tmp_path, server(seen=(seen := [])))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "llm.part").write_bytes(BLOB[:123_456])  # an interrupted download
    await d.run(chunk=50_000)
    assert d.state["state"] == "ready" and d.ready()
    assert seen == ["bytes=123456-", None]
    assert (tmp_path / "models" / "llm.gguf").read_bytes() == BLOB and not list((tmp_path / "models").glob("*.part"))


async def test_bad_checksum_is_refused(tmp_path, manifest):
    d = le.Downloader(tmp_path, server(blob=b"x" * len(BLOB)))
    await d.run()
    assert d.state["state"] == "error" and "checksum" in d.state["error"]
    assert not d.ready() and not list((tmp_path / "models").glob("*"))


def _fake_models(home, ms):
    (home / "models").mkdir(exist_ok=True)
    for m in ms:
        (home / "models" / m["file"]).write_bytes(BLOB)


def test_engine_order(tmp_path, manifest, monkeypatch):
    e = le.Engine(tmp_path)
    st = e.status()
    assert {k: v["kind"] for k, v in st["active"].items()} == {"import": "rules", "hygiene": "rules", "search": "rules"}
    assert st["builtin"]["choice"] == "ask" and st["builtin"]["size"] == 2 * len(BLOB)
    _fake_models(tmp_path, manifest)
    monkeypatch.setattr(le, "runtime_ok", lambda: True)
    assert e.llm("import").kind == "builtin" and e.embedder().kind == "builtin"
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    assert e.llm("import") is None
    monkeypatch.delenv("MNEMOS_BUILTIN")
    e._ollama = (1e18, ("http://127.0.0.1:11434", "llama3.2"))  # Ollama running: used before the built-in
    assert e.llm("hygiene").kind == "ollama"
    e.keys.set("openai", "sk-test-123")
    e.save_cfg({"builtin": "on", "api": {"provider": "openai", "import": True, "hygiene": False}})
    assert e.llm("import").kind == "api" and e.llm("hygiene").kind == "ollama"  # per-feature toggle
    assert e.embedder().kind == "builtin"  # search never goes to an API
    monkeypatch.setenv("MNEMOS_DISCOVER_LLM", "0")
    assert e.llm("import") is None


async def test_api_settings_and_key_storage(tmp_path, manifest):
    local.init_home(tmp_path, ["personal"], "Ana")
    ui = UI(local.build_all(tmp_path, 8765), tmp_path, "")
    e = le.Engine(tmp_path)
    e.install(ui)
    with pytest.raises(ValueError):
        await ui.hooks[("POST", "engine/api")]({"provider": "evil"})
    with pytest.raises(ValueError):
        await ui.hooks[("POST", "engine/api")]({"provider": "openai", "key": "sk bad key with spaces"})
    st = await ui.hooks[("POST", "engine/api")]({"provider": "anthropic", "key": "sk-ant-xyz", "hygiene": True})
    assert st["api"]["has_key"] and st["api"]["hygiene"] and not st["api"]["import"]
    assert st["active"]["hygiene"]["kind"] == "api" and st["active"]["import"]["kind"] == "rules"
    assert "sk-ant" not in (tmp_path / "engine.json").read_text()
    assert stat.S_IMODE((tmp_path / "apikeys.json").stat().st_mode) == 0o600
    st = await ui.hooks[("POST", "engine/api")]({"provider": None})
    assert not st["api"]["has_key"] and "sk-ant" not in (tmp_path / "apikeys.json").read_text()
    assert st["active"]["hygiene"]["kind"] == "rules"


async def test_download_hooks(tmp_path, manifest, monkeypatch):
    e = le.Engine(tmp_path)
    monkeypatch.setattr(le, "runtime_ok", lambda: False)
    with pytest.raises(ValueError):
        await e.h_download({})
    monkeypatch.setattr(le, "runtime_ok", lambda: True)
    e.dl.transport = server()
    await e.h_download({})
    await e.dl.task
    st = e.status()
    assert st["builtin"]["ready"] and st["builtin"]["choice"] == "on" and st["active"]["import"]["kind"] == "builtin"
    await e.h_remove({})
    assert not e.dl.ready() and e.cfg()["builtin"] == "declined" and e.status()["active"]["import"]["kind"] == "rules"


async def test_api_clients_parse_json():
    def oa(req):
        assert req.headers["authorization"] == "Bearer k1" and json.loads(req.content)["response_format"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts": ["a b c d e f g h i j"]}'}}]})

    def an(req):
        assert req.headers["x-api-key"] == "k2"
        return httpx.Response(200, json={"content": [{"type": "text", "text": 'Sure: {"contradict": true, "why": "x"}'}]})
    assert (await le.ApiLLM("openai", "k1", transport=httpx.MockTransport(oa)).json("p"))["facts"]
    assert (await le.ApiLLM("anthropic", "k2", transport=httpx.MockTransport(an)).json("p"))["contradict"] is True


class Emb:
    kind, name, model = "builtin", "fake", "fake"

    async def embed(self, texts):
        return [[1.0, 0.0] if "coffee" in t.lower() or "café" in t.lower() else [0.0, 1.0] for t in texts]


class Judge:
    kind, name = "builtin", "Built-in · fake"

    async def json(self, prompt):
        return {"contradict": "tea" in prompt and "coffee" in prompt, "why": "Tea or coffee?"}


async def test_hygiene_uses_meaning(tmp_path):
    local.init_home(tmp_path, ["personal"], "Ana")
    s = local.build_all(tmp_path, 8765)
    ui = UI(s, tmp_path, "")
    for t in ("Ana drinks coffee every morning", "Todas las mañanas Ana toma café", "Ana has a cat called Mia"):
        await ui.call("personal", "memory_save", {"text": t})
    rep = await lh.run(tmp_path, ["personal"], ui.call, judge=None, embedder=Emb())
    assert [i["type"] for i in rep["items"]] == ["merge"]  # same meaning, no shared words
    rep = await lh.run(tmp_path, ["personal"], ui.call, judge=lh.llm_judge(Judge()), embedder=None)
    assert rep["mode"] == "llm"


@pytest.mark.skipif(not (le.runtime_ok() and os.environ.get("MNEMOS_TEST_GGUF")), reason="needs llama_cpp + a GGUF")
async def test_real_tiny_model(tmp_path):
    m = {"role": "llm", "name": "tiny", "file": "t.gguf"}
    llm = le.BuiltinLLM(m, os.environ["MNEMOS_TEST_GGUF"])
    assert isinstance(await llm.json('Reply {"ok": true}'), dict)
    v = await le.BuiltinEmbedder(m, os.environ["MNEMOS_TEST_GGUF"]).embed(["hola", "hello"])
    assert len(v) == 2 and len(v[0]) > 8


async def test_tiny_model_keeps_duplicate_check(tmp_path, monkeypatch):
    """With the built-in embedder on (here: any GGUF), the save check still flags an exact duplicate."""
    if not (le.runtime_ok() and os.environ.get("MNEMOS_TEST_GGUF")):
        pytest.skip("needs llama_cpp + a GGUF")
    import shutil
    ms = [{"role": r, "name": r, "file": f"{r}.gguf", "size": os.path.getsize(os.environ["MNEMOS_TEST_GGUF"]),
           "sha256": "-", "url": "-"} for r in ("llm", "embed")]
    (tmp_path / "m.json").write_text(json.dumps(ms))
    monkeypatch.setenv("MNEMOS_MODELS_MANIFEST", str(tmp_path / "m.json"))
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    home = tmp_path / "M"
    local.init_home(home, ["personal"], "Ana")
    (home / "models").mkdir()
    for m in ms:
        shutil.copy(os.environ["MNEMOS_TEST_GGUF"], home / "models" / m["file"])
    ui = UI(local.build_all(home, 8765), home, "")
    assert le.get(home).embedder().kind == "builtin"
    await ui.call("personal", "memory_save", {"text": "Ana prefers green tea in the morning"})
    r = await ui.call("personal", "memory_save", {"text": "Ana prefers green tea in the morning."})
    assert r.get("status") == "duplicate"


def test_ollama_skips_embedding_models(monkeypatch):
    tags = {"models": [{"name": "paraphrase-multilingual:latest", "details": {"family": "bert"}},
                       {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}}]}

    class R:
        def json(self):
            return tags
    monkeypatch.delenv("MNEMOS_OLLAMA", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    monkeypatch.setattr(le.httpx, "get", lambda *a, **k: R())
    assert le.ollama_model() is None  # only embedding models: not an LLM, the built-in model is used
    tags["models"].append({"name": "llama3.1:8b", "details": {"family": "llama"}})
    assert le.ollama_model()[1] == "llama3.1:8b"
