from datetime import timedelta

import pytest

from app.models import (
    GrupoEstudio,
    Invitacion,
    MiembroGrupo,
    Solicitud,
    Usuario,
    now_utc,
    today_in_chile,
)
from app.routers import invitaciones
from app.security import hash_password
from tests.conftest import PASSWORD
from tests.test_solicitudes import answer, ask, create_group, my_groups, pending_ids


def invite(client, auth, group_id, email):
    return client.post(f"/api/grupos/{group_id}/invitaciones", json={"email": email}, headers=auth)


def my_invitations(client, auth):
    response = client.get("/api/invitaciones/mias", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def invitation_id(client, auth):
    (invitation,) = my_invitations(client, auth)
    return invitation["id"]


def respond(client, auth, invitation_id, action):
    return client.post(f"/api/invitaciones/{invitation_id}/{action}", headers=auth)


def owner_invitations(client, auth):
    return my_groups(client, auth)["creados"][0]["invitaciones"]


def add_user(session_factory, email, **fields):
    with session_factory() as db:
        db.add(
            Usuario(
                email=email,
                password_hash=hash_password(PASSWORD),
                nombre="Invitado",
                universidad=fields.pop("universidad", "Pontificia Universidad Católica de Chile"),
                terminos_aceptados_en=now_utc(),
                **fields,
            )
        )
        db.commit()


# --- Invitar ---


def test_invite(client, users, invitation_outbox):
    group_id = create_group(client, users["ana"])

    response = invite(client, users["ana"], group_id, "luis@uc.cl")

    assert response.status_code == 201, response.text
    invitations = response.json()["invitaciones"]
    assert [i["usuario"]["nombre"] for i in invitations] == ["Luis Castro"]
    # El correo del invitado no se muestra, igual que en las solicitudes
    assert "email" not in invitations[0]["usuario"]
    assert invitation_outbox == [("luis@uc.cl", "Luis Castro", "Ana Rojas", "MAT1620 Cálculo II")]


def test_invite_ignores_case_and_spaces(client, users):
    group_id = create_group(client, users["ana"])

    assert invite(client, users["ana"], group_id, "  LUIS@UC.CL ").status_code == 201


def test_only_the_owner_can_invite(client, users):
    group_id = create_group(client, users["ana"])

    response = invite(client, users["luis"], group_id, "sofia@uc.cl")
    assert response.status_code == 403


def test_invite_requires_session(client, users):
    group_id = create_group(client, users["ana"])
    response = client.post(f"/api/grupos/{group_id}/invitaciones", json={"email": "luis@uc.cl"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "email, fields",
    [
        ("nadie@uc.cl", None),
        ("sinverificar@uc.cl", {"email_verificado_en": None}),
        ("eliminado@uc.cl", {"email_verificado_en": now_utc(), "eliminado_en": now_utc()}),
        ("otra@udec.cl", {"email_verificado_en": now_utc(), "universidad": "Otra U"}),
    ],
)
def test_cannot_invite_who_is_not_a_verified_student_of_the_university(
    client, users, session_factory, invitation_outbox, email, fields
):
    if fields is not None:
        add_user(session_factory, email, **fields)
    group_id = create_group(client, users["ana"])

    response = invite(client, users["ana"], group_id, email)

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "No hay una cuenta verificada de tu universidad con ese correo."
    )
    assert invitation_outbox == []


def test_cannot_invite_a_member(client, users):
    group_id = create_group(client, users["ana"])

    response = invite(client, users["ana"], group_id, "ana@uc.cl")
    assert response.status_code == 409
    assert response.json()["detail"] == "Esa persona ya es integrante del grupo."


def test_cannot_invite_twice(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")

    response = invite(client, users["ana"], group_id, "luis@uc.cl")
    assert response.status_code == 409
    assert len(owner_invitations(client, users["ana"])) == 1


def test_simultaneous_invitations_keep_one(client, users, monkeypatch):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    # Simula que la segunda llegó antes de que la primera quedara guardada: la frena el índice
    # uq_invitacion_pendiente de la base
    monkeypatch.setattr("app.routers.invitaciones.pending_invitation", lambda group, uid: None)

    response = invite(client, users["ana"], group_id, "luis@uc.cl")
    assert response.status_code == 409
    assert len(owner_invitations(client, users["ana"])) == 1


def test_cannot_invite_who_already_asked_to_join(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)

    response = invite(client, users["ana"], group_id, "luis@uc.cl")
    assert response.status_code == 409
    assert "acepta su solicitud" in response.json()["detail"]


def test_cannot_invite_to_a_full_group(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")

    response = invite(client, users["ana"], group_id, "sofia@uc.cl")
    assert response.status_code == 409
    assert response.json()["detail"] == "El grupo está lleno."


def test_can_invite_to_a_closed_group(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).abierto = False
        db.commit()

    # Cerrado significa que no recibe solicitudes; el creador igual puede invitar
    assert invite(client, users["ana"], group_id, "luis@uc.cl").status_code == 201


def test_invited_cannot_also_ask_to_join(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")

    response = ask(client, users["luis"], group_id)
    assert response.status_code == 409
    assert "Ya te invitaron" in response.json()["detail"]


def test_only_the_owner_sees_the_invitations(client, users):
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")
    invite(client, users["ana"], group_id, "sofia@uc.cl")

    assert my_groups(client, users["luis"])["participo"][0]["invitaciones"] == []


# --- Cancelar (el creador) ---


def test_cancel_invitation(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    (invitation,) = owner_invitations(client, users["ana"])

    response = client.delete(
        f"/api/grupos/{group_id}/invitaciones/{invitation['id']}", headers=users["ana"]
    )

    assert response.status_code == 200
    assert response.json()["invitaciones"] == []
    assert my_invitations(client, users["luis"]) == []
    with session_factory() as db:
        assert db.get(Invitacion, invitation["id"]).cancelada_en is not None
    # Después se puede volver a invitar
    assert invite(client, users["ana"], group_id, "luis@uc.cl").status_code == 201


def test_only_the_owner_can_cancel(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    (invitation,) = owner_invitations(client, users["ana"])

    response = client.delete(
        f"/api/grupos/{group_id}/invitaciones/{invitation['id']}", headers=users["luis"]
    )
    assert response.status_code == 403


def test_cannot_cancel_an_answered_invitation(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    (invitation,) = owner_invitations(client, users["ana"])
    respond(client, users["luis"], invitation["id"], "rechazar")

    response = client.delete(
        f"/api/grupos/{group_id}/invitaciones/{invitation['id']}", headers=users["ana"]
    )
    assert response.status_code == 404


# --- Mis invitaciones ---


def test_my_invitations_starts_empty(client, users):
    assert my_invitations(client, users["luis"]) == []


def test_my_invitations_requires_session(client):
    assert client.get("/api/invitaciones/mias").status_code == 401


def test_my_invitations_show_the_group_newest_first(client, users):
    first = create_group(client, users["ana"], lugar="Sala secreta")
    second = create_group(client, users["sofia"], sigla="FIS1513", nombre_asignatura="Física I")
    invite(client, users["ana"], first, "luis@uc.cl")
    invite(client, users["sofia"], second, "luis@uc.cl")

    invitations = my_invitations(client, users["luis"])

    assert [(i["grupo"]["id"], i["invitado_por"]) for i in invitations] == [
        (second, "Sofía Pérez"),
        (first, "Ana Rojas"),
    ]
    # Como en el buscador: el lugar lo ve recién al entrar
    assert "lugar" not in invitations[1]["grupo"]


def test_my_invitations_skip_expired_groups(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    with session_factory() as db:
        db.get(GrupoEstudio, group_id).vigente_hasta = today_in_chile() - timedelta(days=1)
        db.commit()

    assert my_invitations(client, users["luis"]) == []


# --- Aceptar y rechazar (el invitado) ---


def test_accept_invitation(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")

    response = respond(client, users["luis"], invitation_id(client, users["luis"]), "aceptar")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["es_creador"], body["miembros"], body["lugar"]) == (
        group_id,
        False,
        2,
        "Biblioteca",
    )
    assert [g["id"] for g in my_groups(client, users["luis"])["participo"]] == [group_id]
    assert my_invitations(client, users["luis"]) == []
    assert owner_invitations(client, users["ana"]) == []


def test_accepting_the_last_spot_rejects_the_requests(client, users, full_outbox):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["sofia"], group_id)
    invite(client, users["ana"], group_id, "luis@uc.cl")

    respond(client, users["luis"], invitation_id(client, users["luis"]), "aceptar")

    assert my_groups(client, users["ana"])["creados"][0]["solicitudes"] == []
    assert full_outbox == [("sofia@uc.cl", "Sofía Pérez", "MAT1620 Cálculo II")]


def test_cannot_accept_when_the_group_filled_up(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    invite(client, users["ana"], group_id, "luis@uc.cl")
    ask(client, users["sofia"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")

    response = respond(client, users["luis"], invitation_id(client, users["luis"]), "aceptar")

    assert response.status_code == 409
    assert "cuando se libere uno" in response.json()["detail"]
    # Sigue pendiente para cuando se libere un cupo
    assert len(my_invitations(client, users["luis"])) == 1


def test_two_entries_at_once_do_not_exceed_the_spots(client, users, session_factory, monkeypatch):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    ask(client, users["sofia"], group_id)
    (request_id,) = pending_ids(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    real_lock = invitaciones.lock_group

    def accept_the_request_first(db, group):
        """Mientras la invitación espera el bloqueo del grupo, el creador acepta la solicitud
        y se guarda primero."""
        len(group.miembros)  # esta petición ya había leído el grupo antes de esperar
        with session_factory() as other:
            request = other.get(Solicitud, request_id)
            request.estado, request.respondida_en = "aceptada", now_utc()
            other.add(MiembroGrupo(grupo_id=group_id, usuario_id=request.usuario_id))
            other.commit()
        real_lock(db, group)

    monkeypatch.setattr(invitaciones, "lock_group", accept_the_request_first)

    response = respond(client, users["luis"], invitation_id(client, users["luis"]), "aceptar")

    # Después del bloqueo ve el último cupo ocupado: no entra y el grupo no pasa del cupo
    assert response.status_code == 409
    with session_factory() as db:
        assert len(db.get(GrupoEstudio, group_id).miembros) == 2


def test_reject_invitation(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    invitation = invitation_id(client, users["luis"])

    response = respond(client, users["luis"], invitation, "rechazar")

    assert response.status_code == 204
    assert my_invitations(client, users["luis"]) == []
    assert owner_invitations(client, users["ana"]) == []
    with session_factory() as db:
        assert db.get(Invitacion, invitation).estado == "rechazada"
    # Rechazar no impide pedir unirse después
    assert ask(client, users["luis"], group_id).status_code == 201


def test_cannot_answer_twice(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    invitation = invitation_id(client, users["luis"])
    respond(client, users["luis"], invitation, "aceptar")

    response = respond(client, users["luis"], invitation, "rechazar")
    assert response.status_code == 409


@pytest.mark.parametrize("who", ["ana", "sofia"])
def test_only_the_invited_can_answer(client, users, who):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    invitation = invitation_id(client, users["luis"])

    # Ni el creador ni otra persona: 404 para no revelar invitaciones ajenas
    assert respond(client, users[who], invitation, "aceptar").status_code == 404


def test_cannot_accept_an_invitation_to_a_deleted_group(client, users):
    group_id = create_group(client, users["ana"])
    invite(client, users["ana"], group_id, "luis@uc.cl")
    invitation = invitation_id(client, users["luis"])
    client.delete(f"/api/grupos/{group_id}", headers=users["ana"])

    assert respond(client, users["luis"], invitation, "aceptar").status_code == 404
