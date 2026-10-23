import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db, make_engine
from app.main import app


@pytest.fixture
def session_factory(tmp_path):
    # Una base SQLite nueva por test
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def outbox(monkeypatch):
    """Correos que la API habría enviado: (destinatario, nombre, token)."""
    sent = []
    monkeypatch.setattr(
        "app.routers.auth.send_verification_email",
        lambda to, name, token: sent.append((to, name, token)),
    )
    return sent


@pytest.fixture
def client(session_factory, outbox):
    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
