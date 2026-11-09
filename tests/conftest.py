import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from core_service import accounts
from core_service.app import create_core_app
from portals.admin.app import create_admin_app
from portals.ngo.app import create_ngo_app
from portals.restaurant.app import create_restaurant_app
from shared.core_client import CoreClient

SERVICE_KEYS = {"admin": "key-admin-test", "restaurant": "key-restaurant-test", "ngo": "key-ngo-test"}
JWT = {"admin": "jwt-admin-secret-for-tests-000000000001", "restaurant": "jwt-restaurant-secret-tests-0000000000001",
       "ngo": "jwt-ngo-secret-for-tests-0000000000001"}
HOST_EMAIL, HOST_PASSWORD = "host@frs.test", "host-test-pass-1"


@pytest.fixture
def core_app():
    app = create_core_app("sqlite://", SERVICE_KEYS)
    with sessionmaker(app.state.engine)() as s:
        accounts.create_account(s, role="host", email=HOST_EMAIL, password=HOST_PASSWORD, display_name="Test Host")
        s.commit()
    return app


@pytest.fixture
def core(core_app):
    return TestClient(core_app)


def _core_client_for(core_app, portal):
    return CoreClient("http://core", SERVICE_KEYS[portal], http=TestClient(core_app, base_url="http://core"))


@pytest.fixture
def portals(core_app):
    factories = {"admin": create_admin_app, "restaurant": create_restaurant_app, "ngo": create_ngo_app}
    return {p: TestClient(f(_core_client_for(core_app, p), JWT[p])) for p, f in factories.items()}


def svc(portal):
    return {"X-Service-Key": SERVICE_KEYS[portal]}


def applicant(email="cook@example.org", name="Green Bowl", password="secret-pass-1", phone="+91 98765 43210"):
    return {"email": email, "password": password, "display_name": name, "phone": phone}
