"""memory_dispute guards: grounded evidence, minimal edits at the evidence, no prepend (regression)."""
from hub_gateway.dispute import check, relevant, word_diff

CORR = "Hoy en día no estoy buscando cambiar de trabajo"
INTERESES = ("Intereses de Ana: fútbol; cocina — sushi, ramen; viajes. Interés de larga data en construir presencia "
             "digital en comunidades técnicas.")
CASA = "Casa y familia: ambos trabajan desde casa todos los días. El lavarropas necesita bisagra a la derecha."
CARRERA = "Carrera de Ana: trabaja en Acme y está buscando cambiar de trabajo activamente."


def test_prepend_regression_rejected():
    # exactly what happened live: the sentence glued in front of an unrelated note
    ok, why = check(CORR, INTERESES, {"evidence": "construir presencia digital", "action": "update",
                                       "proposed_text": CORR + "; " + INTERESES})
    assert not ok


def test_evidence_must_be_in_note():
    assert not check(CORR, INTERESES, {"evidence": "busca trabajo nuevo", "action": "update",
                                       "proposed_text": "x" * 20})[0]


def test_unrelated_edit_elsewhere_rejected():
    # live bug 2: hinge side flipped in a note about home while "evidence" was about working from home
    bad = CASA.replace("derecha", "izquierda")
    assert not check(CORR, CASA, {"evidence": "ambos trabajan desde casa", "action": "update", "proposed_text": bad})[0]


def test_topic_guard():
    assert not relevant(CORR, "construir presencia digital en comunidades técnicas")
    assert relevant(CORR, "está buscando cambiar de trabajo activamente")


def test_good_minimal_update_has_diff():
    new = "Carrera de Ana: trabaja en Acme y hoy no está buscando cambiar de trabajo."
    ok, _ = check(CORR, CARRERA, {"evidence": "está buscando cambiar de trabajo activamente", "action": "update",
                                  "proposed_text": new})
    assert ok
    d = word_diff(CARRERA, new)
    assert any(x["op"] == "del" for x in d) and any(x["op"] == "add" for x in d)


async def test_explained_and_cached():
    import json

    import httpx

    from hub_gateway.dispute import propose_explained
    calls = []

    def h(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps({"matches": [
            {"n": 1, "evidence": "construir presencia digital", "action": "update", "proposed_text": CORR + "; " + INTERESES},
            {"n": 2, "evidence": "está buscando cambiar de trabajo activamente", "action": "update",
             "proposed_text": "Carrera de Ana: trabaja en Acme y hoy no está buscando cambiar de trabajo."}]})}})
    notes = [{"id": "i", "dataset": "p", "text": INTERESES}, {"id": "c", "dataset": "p", "text": CARRERA},
             {"id": "h", "dataset": "p", "text": CASA}]
    t = httpx.MockTransport(h)
    props, checked, cached = await propose_explained("http://o", "m", CORR, notes, transport=t)
    assert [p["id"] for p in props] == ["c"] and not cached
    v = {c["id"]: c["verdict"] for c in checked}
    assert v["c"] == "proposed" and "rejected" in v["i"] and "does not state" in v["h"]
    assert calls[0]["options"]["temperature"] == 0 and calls[0]["options"]["seed"] == 42
    props2, _, cached2 = await propose_explained("http://o", "m", CORR, notes, transport=t)
    assert cached2 and props2 == props and len(calls) == 1
