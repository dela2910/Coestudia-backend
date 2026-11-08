from datetime import timedelta

import pytest

from app.models import GrupoEstudio, MiembroGrupo, today_in_chile
from tests.test_solicitudes import answer, ask, create_group, my_groups, pending_ids


def join(client, users, group_id, *who):
    """Suma integrantes al grupo de Ana: cada uno pide unirse y Ana lo acepta."""
    for name in who:
        ask(client, users[name], group_id)
        answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")


def edit_body(**fields):
    return {
        "descripcion": "Repaso certamen 2",
        "es_online": True,
        "lugar": None,
        "enlace": "https://meet.google.com/abc-defg-hij",
        "cupo_maximo": 5,
        "vigente_hasta": str(today_in_chile() + timedelta(days=10)),
        "horarios": [{"dia_semana": 0, "es_manana": True}, {"dia_semana": 1, "es_manana": False}],
        "abierto": True,
        **fields,
    }


def edit(client, auth, group_id, **fields):
    return client.put(f"/api/grupos/{group_id}", json=edit_body(**fields), headers=auth)


def detail(client, auth, group_id):
    return client.get(f"/api/grupos/{group_id}", headers=auth)


# --- Editar ---


def test_owner_edits_the_group(client, users):
    group_id = create_group(client, users["ana"])

    response = edit(client, users["ana"], group_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["descripcion"] == "Repaso certamen 2"
    assert (body["es_online"], body["lugar"], body["enlace"]) == (
        True,
        None,
        "https://meet.google.com/abc-defg-hij",
    )
    assert body["cupo_maximo"] == 5
    assert body["horarios"] == [
        {"dia_semana": 0, "es_manana": True},
        {"dia_semana": 1, "es_manana": False},
    ]
    # La asignatura no cambia
    assert body["asignatura"]["sigla"] == "MAT1620"


def test_edit_can_keep_the_same_schedule(client, users):
    group_id = create_group(client, users["ana"])

    response = edit(
        client, users["ana"], group_id, horarios=[{"dia_semana": 1, "es_manana": False}]
    )

    assert response.status_code == 200, response.text
    assert response.json()["horarios"] == [{"dia_semana": 1, "es_manana": False}]


def test_only_the_owner_can_edit(client, users):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis")

    response = edit(client, users["luis"], group_id)

    assert response.status_code == 403
    assert response.json()["detail"] == "Solo el creador puede administrar el grupo."


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"enlace": "meet"}, "Escribe el enlace"),
        ({"es_online": False, "lugar": None}, "Escribe dónde"),
        ({"vigente_hasta": str(today_in_chile())}, "posterior a hoy"),
    ],
)
def test_edit_validates_like_create(client, users, fields, message):
    group_id = create_group(client, users["ana"])

    response = edit(client, users["ana"], group_id, **fields)

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_capacity_cannot_go_below_current_members(client, users):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis", "sofia")

    response = edit(client, users["ana"], group_id, cupo_maximo=2)

    assert response.status_code == 422
    assert "3 integrantes" in response.json()["detail"]


def test_raising_capacity_frees_waiting_requests(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    join(client, users, group_id, "luis")
    ask(client, users["sofia"], group_id)

    edit(client, users["ana"], group_id, cupo_maximo=3)

    (request,) = my_groups(client, users["ana"])["creados"][0]["solicitudes"]
    assert request["estado"] == "pendiente"


def test_closing_stops_new_requests_and_reopening_allows_them(client, users):
    group_id = create_group(client, users["ana"])

    closed = edit(client, users["ana"], group_id, abierto=False).json()

    assert closed["abierto"] is False
    assert ask(client, users["luis"], group_id).json()["detail"] == "Este grupo está cerrado."
    edit(client, users["ana"], group_id, abierto=True)
    assert ask(client, users["luis"], group_id).status_code == 201


# --- Eliminar ---


def test_owner_deletes_the_group(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis")

    response = client.delete(f"/api/grupos/{group_id}", headers=users["ana"])

    assert response.status_code == 204
    assert detail(client, users["luis"], group_id).status_code == 404
    assert my_groups(client, users["luis"]) == {"creados": [], "participo": []}
    with session_factory() as db:
        group = db.get(GrupoEstudio, group_id)
        assert group.eliminado_en is not None
        assert group.eliminado_por_vigencia is False


def test_only_the_owner_can_delete(client, users):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis")

    assert client.delete(f"/api/grupos/{group_id}", headers=users["luis"]).status_code == 403
    assert client.delete(f"/api/grupos/{group_id}", headers=users["sofia"]).status_code == 403
    assert client.delete("/api/grupos/999", headers=users["ana"]).status_code == 404


# --- Expulsar ---


def luis_id(client, users, group_id):
    members = detail(client, users["ana"], group_id).json()["integrantes"]
    return next(m["id"] for m in members if m["nombre"] == "Luis Castro")


def test_owner_removes_a_member(client, users):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis")

    response = client.delete(
        f"/api/grupos/{group_id}/integrantes/{luis_id(client, users, group_id)}",
        headers=users["ana"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["miembros"] == 1
    assert [m["nombre"] for m in body["integrantes"]] == ["Ana Rojas"]
    # Luis ya no ve el lugar ni el grupo en «participo», y puede volver a pedir unirse
    assert detail(client, users["luis"], group_id).json()["es_miembro"] is False
    assert my_groups(client, users["luis"])["participo"] == []
    assert ask(client, users["luis"], group_id).status_code == 201


def test_removing_a_member_frees_waiting_requests(client, users):
    group_id = create_group(client, users["ana"], cupo_maximo=2)
    join(client, users, group_id, "luis")
    ask(client, users["sofia"], group_id)

    body = client.delete(
        f"/api/grupos/{group_id}/integrantes/{luis_id(client, users, group_id)}",
        headers=users["ana"],
    ).json()

    assert body["solicitud_pendiente"] is False
    (request,) = my_groups(client, users["ana"])["creados"][0]["solicitudes"]
    assert request["estado"] == "pendiente"


def test_owner_cannot_remove_themselves(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    with session_factory() as db:
        owner_id = db.get(GrupoEstudio, group_id).creador_id

    response = client.delete(f"/api/grupos/{group_id}/integrantes/{owner_id}", headers=users["ana"])

    assert response.status_code == 409


def test_remove_unknown_member_is_404(client, users):
    group_id = create_group(client, users["ana"])
    assert (
        client.delete(f"/api/grupos/{group_id}/integrantes/999", headers=users["ana"]).status_code
        == 404
    )


def test_only_the_owner_can_remove_members(client, users):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis", "sofia")
    member = luis_id(client, users, group_id)

    response = client.delete(f"/api/grupos/{group_id}/integrantes/{member}", headers=users["sofia"])

    assert response.status_code == 403


# --- Abandonar ---


def test_member_leaves_the_group(client, users, session_factory):
    group_id = create_group(client, users["ana"])
    join(client, users, group_id, "luis")

    response = client.post(f"/api/grupos/{group_id}/abandonar", headers=users["luis"])

    assert response.status_code == 204
    assert my_groups(client, users["luis"])["participo"] == []
    assert detail(client, users["ana"], group_id).json()["miembros"] == 1
    with session_factory() as db:
        assert db.query(MiembroGrupo).filter_by(grupo_id=group_id).count() == 1


def test_owner_cannot_leave(client, users):
    group_id = create_group(client, users["ana"])

    response = client.post(f"/api/grupos/{group_id}/abandonar", headers=users["ana"])

    assert response.status_code == 409
    assert "elimínalo" in response.json()["detail"]


def test_non_members_cannot_leave(client, users):
    group_id = create_group(client, users["ana"])
    assert (
        client.post(f"/api/grupos/{group_id}/abandonar", headers=users["luis"]).status_code == 404
    )
