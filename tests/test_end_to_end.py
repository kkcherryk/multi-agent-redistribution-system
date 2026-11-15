"""Phase 10: the whole story through the three real portals (the same script as `python -m scripts.demo`)."""
from fastapi.testclient import TestClient

from scripts.demo import run_story
from tests.conftest import HOST_EMAIL, HOST_PASSWORD


def test_full_story_end_to_end(portals):
    clients = [TestClient(portals[k].app) for k in ("admin", "restaurant", "ngo")]
    steps = run_story(*clients, host_email=HOST_EMAIL, host_password=HOST_PASSWORD, tag="t1", say=lambda _: None)
    assert len(steps) == 13
