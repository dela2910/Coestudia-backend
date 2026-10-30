from datetime import timedelta

import pytest

from app.models import GrupoEstudio, Solicitud, Usuario, now_utc, today_in_chile
from app.security import hash_password

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


def create_group(client, auth, **fields):
    body = {
        "sigla": "MAT1620",
        "nombre_asignatura": "Cálculo II",
        "es_online": False,
        "lugar": "Biblioteca",
        "cupo_maximo": 3,
        "vigente_hasta": str(today_in_chile() + timedelta(days=30)),
        "horarios": [{"dia_semana": 1, "es_manana": False}],
        **fields,
    }
    response = client.post("/api/grupos", json=body, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ask(client, auth, group_id, mensaje=None):
    body = {"mensaje": mensaje} if mensaje else None
    return client.post(f"/api/grupos/{group_id}/solicitudes", json=body, headers=auth)


def my_groups(client, auth):
    response = client.get("/api/grupos/mios", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def pending_ids(client, auth):
    return [s["id"] for g in my_groups(client, auth)["creados"] for s in g["solicitudes"]]


def answer(client, auth, request_id, action):
    return client.post(f"/api/solicitudes/{request_id}/{action}", headers=auth)


# --- Mis grupos ---


def test_my_groups_starts_empty(client, users):
    assert my_groups(client, users["ana"]) == {"creados": [], "participo": []}


def test_my_groups_requires_session(client):
    assert client.get("/api/grupos/mios").status_code == 401


def test_created_group_appears_in_my_groups_with_place(client, users):
    group_id = create_group(client, users["ana"])

    groups = my_groups(client, users["ana"])
    assert [g["id"] for g in groups["creados"]] == [group_id]
    assert groups["participo"] == []
    created = groups["creados"][0]
    assert (created["es_creador"], created["lugar"], created["miembros"]) == (True, "Biblioteca", 1)


def test_my_groups_skip_expired_and_deleted(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).vigente_hasta = today_in_chile() - timedelta(days=1)
        db.commit()

    assert my_groups(client, users["ana"])["creados"] == []


# --- Pedir unirse ---


def test_request_to_join(client, users):
    group_id = create_group(client, users["ana"])

    response = ask(client, users["luis"], group_id, "Voy atrasado con series")

    assert response.status_code == 201
    body = response.json()
    assert (body["solicitud_pendiente"], body["es_miembro"], body["lugar"]) == (True, False, None)
    # El creador la ve en «mis grupos», con el nombre y la carrera de quien la envió
    requests = my_groups(client, users["ana"])["creados"][0]["solicitudes"]
    assert [(s["usuario"]["nombre"], s["mensaje"]) for s in requests] == [
        ("Luis Castro", "Voy atrasado con series")
    ]
    assert "email" not in requests[0]["usuario"]


def test_request_without_body(client, users):
    group_id = create_group(client, users["ana"])
    response = client.post(f"/api/grupos/{group_id}/solicitudes", headers=users["luis"])
    assert response.status_code == 201


def test_detail_shows_pending_request(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    detail = client.get(f"/api/grupos/{group_id}", headers=users["luis"]).json()
    assert detail["solicitud_pendiente"] is True
    other = client.get(f"/api/grupos/{group_id}", headers=users["sofia"]).json()
    assert other["solicitud_pendiente"] is False


def test_cannot_request_twice(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    assert ask(client, users["luis"], group_id).status_code == 409


def test_members_cannot_request(client, users):
    group_id = create_group(client, users["ana"])
    assert ask(client, users["ana"], group_id).status_code == 409


def test_cannot_request_full_group(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")

    # Se llenó el cupo y quedó cerrado
    assert ask(client, users["sofia"], group_id).status_code == 409


def test_cannot_request_closed_group_with_spots(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).abierto = False
        db.commit()

    response = ask(client, users["luis"], group_id)
    assert response.status_code == 409
    assert response.json()["detail"] == "Este grupo está cerrado."


def test_cannot_request_full_group_even_if_open(client, users, session_factory):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).abierto = True
        db.commit()

    response = ask(client, users["sofia"], group_id)
    assert response.status_code == 409
    assert response.json()["detail"] == "Este grupo ya no tiene cupos."


def test_simultaneous_requests_keep_one(client, users, monkeypatch):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    # Simula que la segunda llegó antes de que la primera quedara guardada: la frena el índice
    # uq_solicitud_pendiente de la base
    monkeypatch.setattr("app.routers.grupos.pending_request", lambda group, user_id: None)

    response = ask(client, users["luis"], group_id)
    assert response.status_code == 409
    assert len(pending_ids(client, users["ana"])) == 1


def test_cannot_request_unavailable_group(client, users):
    assert ask(client, users["luis"], 9999).status_code == 404


def test_cancel_request(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    response = client.delete(f"/api/grupos/{group_id}/solicitudes", headers=users["luis"])

    assert response.status_code == 200
    assert response.json()["solicitud_pendiente"] is False
    assert pending_ids(client, users["ana"]) == []
    with session_factory() as db:
        request = db.query(Solicitud).one()
        assert request.estado == "cancelada"
        assert request.cancelada_en is not None
    # Puede volver a pedirlo
    assert ask(client, users["luis"], group_id).status_code == 201


def test_cancel_without_request(client, users):
    group_id = create_group(client, users["ana"])
    response = client.delete(f"/api/grupos/{group_id}/solicitudes", headers=users["luis"])
    assert response.status_code == 404


# --- Responder solicitudes ---


def test_accept_request(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    response = answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")

    assert response.status_code == 200
    body = response.json()
    assert (body["miembros"], body["solicitudes"], body["abierto"]) == (2, [], True)
    # Luis ahora es integrante: lo ve en «participo» y ve el lugar
    participo = my_groups(client, users["luis"])["participo"]
    assert [(g["id"], g["es_creador"], g["lugar"]) for g in participo] == [
        (group_id, False, "Biblioteca")
    ]
    detail = client.get(f"/api/grupos/{group_id}", headers=users["luis"]).json()
    assert (detail["es_miembro"], detail["lugar"]) == (True, "Biblioteca")


def test_accepting_the_last_spot_closes_the_group(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["luis"], group_id)
    ask(client, users["sofia"], group_id)
    first, second = pending_ids(client, users["ana"])

    body = answer(client, users["ana"], first, "aceptar").json()

    assert (body["miembros"], body["abierto"]) == (2, False)
    # La otra solicitud queda pendiente, pero ya no se puede aceptar
    assert answer(client, users["ana"], second, "aceptar").status_code == 409
    assert answer(client, users["ana"], second, "rechazar").status_code == 200


def test_cannot_accept_when_full_even_if_open(client, users, session_factory):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["luis"], group_id)
    ask(client, users["sofia"], group_id)
    first, second = pending_ids(client, users["ana"])
    answer(client, users["ana"], first, "aceptar")
    # El creador lo volvió a abrir sin subir el cupo
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).abierto = True
        db.commit()

    response = answer(client, users["ana"], second, "aceptar")
    assert response.status_code == 409
    assert response.json()["detail"] == "El grupo ya no tiene cupos."


def test_reject_request(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    response = answer(client, users["ana"], pending_ids(client, users["ana"])[0], "rechazar")

    assert response.status_code == 200
    assert response.json()["solicitudes"] == []
    assert my_groups(client, users["luis"])["participo"] == []
    with session_factory() as db:
        request = db.query(Solicitud).one()
        assert (request.estado, request.respondida_en is not None) == ("rechazada", True)


def test_cannot_answer_twice(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    request_id = pending_ids(client, users["ana"])[0]
    answer(client, users["ana"], request_id, "rechazar")

    assert answer(client, users["ana"], request_id, "aceptar").status_code == 409


@pytest.mark.parametrize("who", ["luis", "otro"])
def test_only_the_owner_can_answer(client, users, who):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    request_id = pending_ids(client, users["ana"])[0]

    assert answer(client, users[who], request_id, "aceptar").status_code == 404


def test_answer_requires_session(client):
    assert client.post("/api/solicitudes/1/aceptar").status_code == 401
