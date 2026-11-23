"""Notificaciones de la campana: se arman con las solicitudes y las invitaciones."""

from datetime import timedelta

from app.models import Solicitud, Usuario, now_utc
from tests.test_invitaciones import invitation_id, invite, respond
from tests.test_solicitudes import answer, ask, create_group, pending_ids

URL = "/api/notificaciones"


def notifications(client, auth):
    response = client.get(URL, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def kinds(data):
    return [(n["tipo"], n["persona"], n["aceptada"], n["nueva"]) for n in data["notificaciones"]]


def test_requires_session(client):
    assert client.get(URL).status_code == 401
    assert client.post(f"{URL}/vistas").status_code == 401


def test_starts_empty(client, users):
    assert notifications(client, users["ana"]) == {"sin_leer": 0, "notificaciones": []}


def test_owner_sees_pending_request(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    data = notifications(client, users["ana"])
    assert data["sin_leer"] == 1
    [item] = data["notificaciones"]
    assert item["tipo"] == "solicitud_recibida"
    assert item["grupo_id"] == group_id
    assert item["asignatura"] == "MAT1620 Cálculo II"
    assert item["persona"] == "Luis Castro"
    assert item["id"].startswith("solicitud_recibida-")
    # A quien pidió unirse todavía no le llega nada
    assert notifications(client, users["luis"])["notificaciones"] == []


def test_answered_request_moves_to_the_applicant(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    ask(client, users["sofia"], group_id)
    luis_request, sofia_request = pending_ids(client, users["ana"])
    answer(client, users["ana"], luis_request, "aceptar")
    answer(client, users["ana"], sofia_request, "rechazar")

    # La creadora ya no tiene nada pendiente
    assert notifications(client, users["ana"])["notificaciones"] == []
    assert kinds(notifications(client, users["luis"])) == [
        ("solicitud_respondida", None, True, True)
    ]
    assert kinds(notifications(client, users["sofia"])) == [
        ("solicitud_respondida", None, False, True)
    ]


def test_cancelled_request_disappears(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    client.delete(f"/api/grupos/{group_id}/solicitudes", headers=users["luis"])

    assert notifications(client, users["ana"])["notificaciones"] == []


def test_invitation_and_its_answer(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")

    assert kinds(notifications(client, users["luis"])) == [
        ("invitacion_recibida", "Ana Rojas", None, True)
    ]

    respond(client, users["luis"], invitation_id(client, users["luis"]), "aceptar")

    assert notifications(client, users["luis"])["notificaciones"] == []
    assert kinds(notifications(client, users["ana"])) == [
        ("invitacion_respondida", "Luis Castro", True, True)
    ]


def test_mark_seen(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    assert client.post(f"{URL}/vistas", headers=users["ana"]).status_code == 204
    data = notifications(client, users["ana"])
    # Sigue en la lista porque está pendiente, pero ya no es nueva
    assert data["sin_leer"] == 0
    assert [n["nueva"] for n in data["notificaciones"]] == [False]

    ask(client, users["sofia"], group_id)
    data = notifications(client, users["ana"])
    assert data["sin_leer"] == 1
    # La más reciente primero
    assert [(n["persona"], n["nueva"]) for n in data["notificaciones"]] == [
        ("Sofía Pérez", True),
        ("Luis Castro", False),
    ]


def test_old_answers_and_deleted_groups_are_hidden(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "rechazar")
    other_group = create_group(client, users["sofia"], sigla="IIC2233", nombre_asignatura="Progra")
    ask(client, users["otro"], other_group)

    with session_factory() as db:
        # La respuesta a Luis fue hace más de 30 días
        request = db.query(Solicitud).filter(Solicitud.grupo_id == group_id).one()
        request.respondida_en = now_utc() - timedelta(days=31)
        db.commit()
    client.delete(f"/api/grupos/{other_group}", headers=users["sofia"])

    assert notifications(client, users["luis"])["notificaciones"] == []
    assert notifications(client, users["sofia"])["notificaciones"] == []


def test_requests_from_deleted_accounts_are_hidden(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    with session_factory() as db:
        db.query(Usuario).filter(Usuario.email == "luis@uc.cl").one().eliminado_en = now_utc()
        db.commit()

    assert notifications(client, users["ana"])["notificaciones"] == []
