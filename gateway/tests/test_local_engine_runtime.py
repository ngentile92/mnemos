"""0.19 E: when the built-in model's runtime cannot load, the engine says why (Settings shows it, the log keeps it)."""
from hub_gateway import local_engine as le


def test_runtime_error_is_reported(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "llama_cpp":
            raise ImportError("dlopen(libllama.dylib): code signature not valid")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.setattr(le, "_RUNTIME_ERR", "")
    assert le.runtime_ok() is False
    assert "code signature" in le.runtime_error()
    st = le.Engine(tmp_path).status()
    assert st["builtin"]["runtime"] is False
    assert "code signature" in st["builtin"]["runtime_error"]
