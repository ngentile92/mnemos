from hub_gateway import memfilter as mf


def test_tidy_drops_third_person_subject():
    assert mf.tidy("The person is working on the Atlas billing page") == "Working on the Atlas billing page"
    assert mf.tidy("El usuario prefiere respuestas cortas") == "Prefiere respuestas cortas"
    assert mf.tidy("Ana López works at Acme", "Ana López") == "Works at Acme"
    assert mf.tidy("Ana López trabaja en Acme", "Ana") == "Trabaja en Acme"
    assert mf.tidy("Ana López y Juan Pérez son colegas", "Ana") == "Ana López y Juan Pérez son colegas"


def test_vague_and_language_and_near_dup():
    assert mf.vague("Is working on a project.") and mf.vague("Necesita ayuda con cosas.")
    assert not mf.vague("Prefiere respuestas cortas")
    assert mf.language("quiero que me respondas en español") == "es" and mf.language("please answer in English") == "en"
    f = [{"text": "The person prefers short answers with examples", "importance": 4}]
    assert mf.polish(f, "quiero respuestas cortas, con ejemplos, por favor")[0].get("unsure")
    assert mf.near_dup("Prefers short answers with code examples", ["Prefers short answers with code examples."])
    assert not mf.near_dup("Lives in Lisbon", ["Works at Acme"])


def test_capture_eval_improves_precision():
    import importlib.util
    import json
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = json.loads(subprocess.run([sys.executable, str(root / "scripts" / "capture_eval.py")], capture_output=True,
                                    text=True, check=True).stdout)
    assert out["after"]["precision"] >= 0.9 > out["before"]["precision"] and out["after"]["third_person"] == 0
