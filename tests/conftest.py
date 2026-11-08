import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db, make_engine
from app.main import app
from app.models import Usuario, now_utc
from app.rate_limit import login_limiter
from app.security import hash_password


@pytest.fixture(autouse=True)
def reset_login_limiter():
    # El límite de intentos vive en memoria: se limpia para que no pase de un test a otro
    login_limiter.reset()
    yield
    login_limiter.reset()


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
def reset_outbox(monkeypatch):
    """Correos de recuperar contraseña que la API habría enviado: (destinatario, nombre, token)."""
    sent = []
    monkeypatch.setattr(
        "app.routers.auth.send_password_reset_email",
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


PASSWORD = "Clave-segura-1"
UC = "Pontificia Universidad Católica de Chile"


@pytest.fixture
def users(client, session_factory):
    """Crea usuarios UC verificados y devuelve los headers de sesión de cada uno por nombre."""
    names = {"ana": "Ana Rojas", "luis": "Luis Castro", "sofia": "Sofía Pérez", "otro": "Otro"}
    with session_factory() as db:
        for key, name in names.items():
            db.add(
                Usuario(
                    email=f"{key}@uc.cl",
                    password_hash=hash_password(PASSWORD),
                    nombre=name,
                    carrera="Ingeniería Civil",
                    universidad=UC,
                    terminos_aceptados_en=now_utc(),
                    email_verificado_en=now_utc(),
                )
            )
        db.commit()
    headers = {}
    for key in names:
        login = client.post("/api/auth/login", json={"email": f"{key}@uc.cl", "password": PASSWORD})
        headers[key] = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return headers
