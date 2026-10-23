from datetime import timedelta

import jwt
import pytest

from app.models import TokenCorreo, Usuario
from app.security import JWT_ALGORITHM, jwt_secret, now_utc

NEW_USER = {
    "email": "Camila.Perez@UC.cl",
    "password": "Clave-segura-1",
    "nombre": "Camila Pérez",
    "carrera": "Ingeniería Civil",
    "anio_ingreso": 2024,
    "acepta_terminos": True,
}


def register(client, **overrides):
    return client.post("/api/auth/registro", json={**NEW_USER, **overrides})


def register_and_verify(client, outbox):
    register(client)
    token = outbox[-1][2]
    return client.post("/api/auth/verificar", json={"token": token}).json()


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# --- Registro ---


def test_register_creates_user_and_sends_verification(client, outbox, session_factory):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "camila.perez@uc.cl"
    assert body["universidad"] == "Pontificia Universidad Católica de Chile"
    assert body["email_verificado_en"] is None
    assert "password_hash" not in body

    assert len(outbox) == 1
    assert outbox[0][0] == "camila.perez@uc.cl"
    with session_factory() as db:
        user = db.query(Usuario).one()
        assert user.password_hash != NEW_USER["password"]
        assert user.terminos_aceptados_en is not None
        # Se guarda solo el hash del token, nunca el token del enlace
        token = db.query(TokenCorreo).one()
        assert token.tipo == "verificacion"
        assert token.token_hash != outbox[0][2]


def test_register_rejects_other_domains(client, outbox):
    response = register(client, email="camila@gmail.com")
    assert response.status_code == 400
    assert response.json()["detail"] == "Por ahora solo se aceptan correos @uc.cl."
    assert outbox == []


def test_register_requires_terms(client):
    response = register(client, acepta_terminos=False)
    assert response.status_code == 400
    assert "términos" in response.json()["detail"]


def test_register_rejects_duplicate_email(client, outbox):
    register_and_verify(client, outbox)
    response = register(client, email="camila.perez@uc.cl")
    assert response.status_code == 409
    assert response.json()["detail"] == "Ya existe una cuenta con ese correo."


def test_register_keeps_pending_registration(client, outbox):
    # Mientras el enlace siga vigente, nadie puede reemplazar los datos de la cuenta
    register(client)
    response = register(client, nombre="Otra Persona", password="Otra-clave-9")
    assert response.status_code == 409
    assert "registro pendiente" in response.json()["detail"]
    assert len(outbox) == 1


def test_register_replaces_unverified_account_after_link_expires(client, outbox, session_factory):
    register(client)
    with session_factory() as db:
        db.query(TokenCorreo).one().expira_en = now_utc() - timedelta(minutes=1)
        db.commit()

    response = register(client, nombre="Camila Rojas", password="Nueva-clave-9")
    assert response.status_code == 201
    assert response.json()["nombre"] == "Camila Rojas"
    with session_factory() as db:
        assert db.query(Usuario).count() == 1

    session = client.post("/api/auth/verificar", json={"token": outbox[-1][2]}).json()
    login = client.post(
        "/api/auth/login", json={"email": NEW_USER["email"], "password": "Nueva-clave-9"}
    )
    assert session["usuario"]["nombre"] == "Camila Rojas"
    assert login.status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "Corta1"},
        {"password": "Xx1" * 25},
        {"password": "sin-mayuscula-1"},
        {"password": "SIN-MINUSCULA-1"},
        {"password": "Sin-numero"},
        {"nombre": "   "},
        {"anio_ingreso": 1980},
        {"anio_ingreso": 3000},
        {"email": "no-es-correo"},
    ],
)
def test_register_validates_fields(client, overrides):
    assert register(client, **overrides).status_code == 422


# --- Verificación del correo ---


def test_verify_marks_email_and_starts_session(client, outbox):
    register(client)
    response = client.post("/api/auth/verificar", json={"token": outbox[0][2]})

    assert response.status_code == 200
    session = response.json()
    assert session["token_type"] == "bearer"
    assert session["usuario"]["email_verificado_en"] is not None

    me = client.get("/api/auth/me", headers=auth_header(session["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "camila.perez@uc.cl"


def test_verification_token_is_single_use(client, outbox):
    register(client)
    token = outbox[0][2]
    assert client.post("/api/auth/verificar", json={"token": token}).status_code == 200
    assert client.post("/api/auth/verificar", json={"token": token}).status_code == 400


def test_verification_token_expires(client, outbox, session_factory):
    register(client)
    with session_factory() as db:
        db.query(TokenCorreo).one().expira_en = now_utc() - timedelta(minutes=1)
        db.commit()
    response = client.post("/api/auth/verificar", json={"token": outbox[0][2]})
    assert response.status_code == 400


def test_verify_rejects_unknown_token(client):
    assert client.post("/api/auth/verificar", json={"token": "inventado"}).status_code == 400


def make_tokens_older(session_factory, minutes):
    # Simula que los correos anteriores se enviaron hace un rato
    with session_factory() as db:
        for token in db.query(TokenCorreo):
            token.creado_en = now_utc() - timedelta(minutes=minutes)
        db.commit()


def test_resend_invalidates_previous_link(client, outbox, session_factory):
    register(client)
    make_tokens_older(session_factory, minutes=2)
    response = client.post("/api/auth/reenviar-verificacion", json={"email": NEW_USER["email"]})

    assert response.status_code == 202
    assert len(outbox) == 2
    first, second = outbox[0][2], outbox[1][2]
    assert client.post("/api/auth/verificar", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verificar", json={"token": second}).status_code == 200


def test_resend_waits_one_minute(client, outbox):
    register(client)
    response = client.post("/api/auth/reenviar-verificacion", json={"email": NEW_USER["email"]})
    # Misma respuesta, pero no se manda otro correo
    assert response.status_code == 202
    assert len(outbox) == 1


def test_resend_has_daily_limit(client, outbox, session_factory):
    register(client)
    for _ in range(10):
        make_tokens_older(session_factory, minutes=2)
        client.post("/api/auth/reenviar-verificacion", json={"email": NEW_USER["email"]})
    assert len(outbox) == 5


def test_resend_does_not_reveal_unknown_emails(client, outbox):
    response = client.post("/api/auth/reenviar-verificacion", json={"email": "nadie@uc.cl"})
    assert response.status_code == 202
    assert outbox == []


def test_resend_skips_verified_accounts(client, outbox):
    register_and_verify(client, outbox)
    client.post("/api/auth/reenviar-verificacion", json={"email": NEW_USER["email"]})
    assert len(outbox) == 1


# --- Inicio de sesión ---


def test_login_returns_session(client, outbox):
    register_and_verify(client, outbox)
    response = client.post(
        "/api/auth/login", json={"email": "CAMILA.perez@uc.cl", "password": NEW_USER["password"]}
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 200


def test_login_requires_verified_email(client):
    register(client)
    response = client.post(
        "/api/auth/login", json={"email": NEW_USER["email"], "password": NEW_USER["password"]}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "credentials",
    [
        {"email": NEW_USER["email"], "password": "otra-clave"},
        {"email": "nadie@uc.cl", "password": NEW_USER["password"]},
    ],
)
def test_login_rejects_bad_credentials(client, outbox, credentials):
    register_and_verify(client, outbox)
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 401
    assert response.json()["detail"] == "Correo o contraseña incorrectos."


def test_login_rejects_too_long_password(client):
    response = client.post(
        "/api/auth/login", json={"email": NEW_USER["email"], "password": "x" * 100}
    )
    assert response.status_code == 422


def test_verify_rejects_too_long_token(client):
    assert client.post("/api/auth/verificar", json={"token": "x" * 201}).status_code == 422


def test_login_rejects_deleted_account(client, outbox, session_factory):
    register_and_verify(client, outbox)
    with session_factory() as db:
        db.query(Usuario).one().eliminado_en = now_utc()
        db.commit()
    response = client.post(
        "/api/auth/login", json={"email": NEW_USER["email"], "password": NEW_USER["password"]}
    )
    assert response.status_code == 401


# --- Rutas protegidas (current_user) ---


def test_me_requires_session(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_invalid_token(client):
    assert client.get("/api/auth/me", headers=auth_header("no-es-un-jwt")).status_code == 401


def test_me_rejects_expired_token(client, outbox):
    user_id = register_and_verify(client, outbox)["usuario"]["id"]
    past = now_utc() - timedelta(days=30)
    expired = jwt.encode(
        {"sub": str(user_id), "iat": int(past.timestamp()), "exp": int(past.timestamp()) + 60},
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert client.get("/api/auth/me", headers=auth_header(expired)).status_code == 401


def test_password_change_closes_old_sessions(client, outbox, session_factory):
    token = register_and_verify(client, outbox)["access_token"]
    with session_factory() as db:
        # Simula un cambio de contraseña hecho después de iniciar la sesión
        db.query(Usuario).one().contrasena_cambiada_en = now_utc() + timedelta(seconds=5)
        db.commit()
    response = client.get("/api/auth/me", headers=auth_header(token))
    assert response.status_code == 401
    assert "contraseña cambió" in response.json()["detail"]
