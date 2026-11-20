from datetime import timedelta

import pytest

from app.models import GrupoEstudio, today_in_chile
from tests.test_solicitudes import answer, ask, create_group, pending_ids


@pytest.fixture
def group(client, users):
    """Grupo de Ana con Luis como integrante. Sofía no es integrante."""
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")
    return group_id


def open_topic(client, auth, group_id, titulo="Guía 2 – Ejercicio 3", contenido="¿Cómo parto?"):
    return client.post(
        f"/api/grupos/{group_id}/foro",
        json={"titulo": titulo, "contenido": contenido},
        headers=auth,
    )


def topics(client, auth, group_id):
    return client.get(f"/api/grupos/{group_id}/foro", headers=auth)


def reply(client, auth, topic_id, contenido="Usa integración por partes"):
    return client.post(
        f"/api/foro/{topic_id}/respuestas", json={"contenido": contenido}, headers=auth
    )


def topic(client, auth, topic_id):
    return client.get(f"/api/foro/{topic_id}", headers=auth)


# --- Temas ---


def test_forum_starts_empty(client, users, group):
    response = topics(client, users["ana"], group)

    assert response.status_code == 200
    assert response.json() == []


def test_member_opens_a_topic_and_the_group_sees_it(client, users, group):
    response = open_topic(client, users["luis"], group, "  Guía 2 – Ejercicio 3  ")

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["titulo"] == "Guía 2 – Ejercicio 3"
    assert created["contenido"] == "¿Cómo parto?"
    assert created["autor"] == "Luis Castro"
    assert created["es_mio"] is True
    assert created["respuestas"] == []

    listed = topics(client, users["ana"], group).json()
    assert [(t["titulo"], t["autor"], t["total_respuestas"]) for t in listed] == [
        ("Guía 2 – Ejercicio 3", "Luis Castro", 0)
    ]


def test_dates_include_the_time_zone(client, users, group):
    open_topic(client, users["ana"], group)

    listed = topics(client, users["ana"], group).json()

    # SQLite guarda la fecha sin zona: igual se responde en UTC para que el navegador la convierta
    assert listed[0]["creado_en"].endswith("Z")
    assert listed[0]["ultima_actividad_en"].endswith("Z")


def test_forums_of_different_groups_are_separate(client, users, group):
    other = create_group(client, users["sofia"], sigla="IIC2143", nombre_asignatura="Software")
    open_topic(client, users["sofia"], other, "Tema de otro grupo")
    open_topic(client, users["ana"], group, "Tema de este grupo")

    listed = topics(client, users["ana"], group).json()

    assert [t["titulo"] for t in listed] == ["Tema de este grupo"]


# --- Respuestas ---


def test_replies_stay_under_the_topic_in_order(client, users, group):
    topic_id = open_topic(client, users["luis"], group).json()["id"]

    first = reply(client, users["ana"], topic_id, "Usa integración por partes")
    reply(client, users["luis"], topic_id, "¡Gracias, me resultó!")

    assert first.status_code == 201, first.text
    assert first.json()["autor"] == "Ana Rojas"
    assert first.json()["es_mia"] is True

    detail = topic(client, users["luis"], topic_id).json()
    assert detail["titulo"] == "Guía 2 – Ejercicio 3"
    assert detail["es_mio"] is True
    assert [(r["autor"], r["contenido"], r["es_mia"]) for r in detail["respuestas"]] == [
        ("Ana Rojas", "Usa integración por partes", False),
        ("Luis Castro", "¡Gracias, me resultó!", True),
    ]
    assert topics(client, users["ana"], group).json()[0]["total_respuestas"] == 2


def test_topic_with_a_new_reply_goes_first(client, users, group):
    old = open_topic(client, users["ana"], group, "Ejercicio 1").json()["id"]
    open_topic(client, users["ana"], group, "Ejercicio 2")
    assert [t["titulo"] for t in topics(client, users["ana"], group).json()] == [
        "Ejercicio 2",
        "Ejercicio 1",
    ]

    reply(client, users["luis"], old)

    assert [t["titulo"] for t in topics(client, users["ana"], group).json()] == [
        "Ejercicio 1",
        "Ejercicio 2",
    ]


# --- Validaciones ---


@pytest.mark.parametrize(
    "titulo, contenido",
    [("", "¿Cómo parto?"), ("   ", "¿Cómo parto?"), ("Ejercicio 3", ""), ("a" * 151, "Hola")],
)
def test_topic_needs_title_and_content(client, users, group, titulo, contenido):
    assert open_topic(client, users["ana"], group, titulo, contenido).status_code == 422


def test_reply_has_a_maximum_length(client, users, group):
    topic_id = open_topic(client, users["ana"], group).json()["id"]

    assert reply(client, users["ana"], topic_id, "a" * 5000).status_code == 201
    assert reply(client, users["ana"], topic_id, "a" * 5001).status_code == 422
    assert reply(client, users["ana"], topic_id, "   ").status_code == 422


# --- Permisos ---


def test_forum_requires_session(client, group):
    assert client.get(f"/api/grupos/{group}/foro").status_code == 401
    assert client.get("/api/foro/1").status_code == 401


def test_non_members_cannot_see_or_write(client, users, group):
    topic_id = open_topic(client, users["ana"], group).json()["id"]

    assert topics(client, users["sofia"], group).status_code == 403
    assert open_topic(client, users["sofia"], group).status_code == 403
    # Los temas ajenos responden 404, para no revelar que existen
    assert topic(client, users["sofia"], topic_id).status_code == 404
    assert reply(client, users["sofia"], topic_id).status_code == 404


def test_member_who_left_loses_access_and_their_posts_stay(client, users, group):
    topic_id = open_topic(client, users["luis"], group).json()["id"]
    assert client.post(f"/api/grupos/{group}/abandonar", headers=users["luis"]).status_code == 204

    assert topic(client, users["luis"], topic_id).status_code == 404
    assert topic(client, users["ana"], topic_id).json()["autor"] == "Luis Castro"


def test_unknown_group_or_topic_responds_404(client, users):
    assert topics(client, users["ana"], 999).status_code == 404
    assert topic(client, users["ana"], 999).status_code == 404
    assert reply(client, users["ana"], 999).status_code == 404


def test_expired_group_forum_is_not_available(client, users, group, session_factory):
    topic_id = open_topic(client, users["ana"], group).json()["id"]
    with session_factory() as db:
        db.get(GrupoEstudio, group).vigente_hasta = today_in_chile() - timedelta(days=1)
        db.commit()

    assert topics(client, users["ana"], group).status_code == 404
    assert topic(client, users["ana"], topic_id).status_code == 404
