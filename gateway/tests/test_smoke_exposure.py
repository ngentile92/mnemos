"""scripts/smoke_test.py step 3 (public DNS) in both exposure modes."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "scripts" / "smoke_test.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def test_funnel_requires_public_record():
    assert st.public_dns_verdict("funnel", 1) == "ok"
    assert st.public_dns_verdict("funnel", 0) == "fail"


def test_tailnet_treats_public_record_as_warning():
    assert st.public_dns_verdict("tailnet", 0) == "ok"
    assert st.public_dns_verdict("tailnet", 2) == "warn"


def test_exposure_from_env(monkeypatch):
    monkeypatch.setenv("MNEMOS_EXPOSURE", "tailnet")
    assert st._exposure_from_env() == "tailnet"
