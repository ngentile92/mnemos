from hub_gateway.ledger import Ledger


def test_promoted_origin_in_provenance(tmp_path):
    lg = Ledger(str(tmp_path / "ledger.sqlite"))
    origin = {"dataset": "personal", "data_id": "abc", "context": "personal"}
    lg.record_save(dataset="shared", text="hecho", context="personal", source_app="claude", login="u",
                   tags=[], data_id="new-id", origin=origin)
    assert lg.provenance("new-id", "shared")["promoted_from"] == origin
    lg.record_save(dataset="personal", text="otro", context="personal", source_app=None, login="u", tags=[], data_id="x")
    assert "promoted_from" not in lg.provenance("x", "personal")
