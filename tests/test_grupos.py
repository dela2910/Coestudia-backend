from datetime import timedelta

import pytest

from app.models import (
    Asignatura,
    GrupoEstudio,
    HorarioGrupo,
    MiembroGrupo,
    Usuario,
    now_utc,
    today_in_chile,
)
from tests.test_auth import register_and_verify

UC = "Pontificia Universidad Católica de Chile"


@pytest.fixture
def auth(client, outbox):
    """Headers de la sesión de un usuario UC verificado."""
    token = register_and_verify(client, outbox)["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def data(session_factory, auth):
    """Asignaturas y grupos de ejemplo. Devuelve los ids de los grupos por nombre."""
    with session_factory() as db:
        owner = Usuario(
            email="ana@uc.cl",
            password_hash="x",
            nombre="Ana",
            universidad=UC,
            terminos_aceptados_en=now_utc(),
            email_verificado_en=now_utc(),
        )
        calculo = Asignatura(sigla="MAT1620", nombre="Cálculo II", universidad=UC)
        fisica = Asignatura(sigla="FIS1513", nombre="Física I", universidad=UC)
        otra_u = Asignatura(sigla="MAT1620", nombre="Cálculo II", universidad="Otra U")
        future = today_in_chile() + timedelta(days=30)

        def group(subject, schedule, **fields):
            g = GrupoEstudio(
                asignatura=subject,
                creador=owner,
                es_online=fields.pop("es_online", False),
                lugar=fields.pop("lugar", "Biblioteca"),
                cupo_maximo=fields.pop("cupo_maximo", 4),
                vigente_hasta=fields.pop("vigente_hasta", future),
                **fields,
            )
            g.horarios = [HorarioGrupo(dia_semana=d, es_manana=m) for d, m in schedule]
            g.miembros = [MiembroGrupo(usuario=owner)]
            db.add(g)
            return g

        groups = {
            # Martes tarde, presencial
            "calculo_presencial": group(calculo, [(1, False)], descripcion="Integrales"),
            # Lunes mañana y miércoles tarde, online
            "calculo_online": group(
                calculo, [(0, True), (2, False)], es_online=True, lugar=None, enlace="https://x"
            ),
            "calculo_cerrado": group(calculo, [(1, True)], abierto=False),
            "fisica": group(fisica, [(4, True)]),
            "vencido": group(calculo, [(1, False)], vigente_hasta=today_in_chile() - timedelta(1)),
            "eliminado": group(
                calculo, [(1, False)], eliminado_en=now_utc(), eliminado_por_vigencia=False
            ),
            "otra_universidad": group(otra_u, [(1, False)]),
        }
        db.commit()
        return {name: g.id for name, g in groups.items()}


def search(client, auth, **params):
    response = client.get("/api/grupos", params=params, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def ids(results):
    return [g["id"] for g in results]


def test_requires_session(client):
    assert client.get("/api/grupos").status_code == 401


def test_lists_visible_groups_open_first(client, auth, data):
    results = search(client, auth)
    assert ids(results) == [
        data["calculo_presencial"],
        data["calculo_online"],
        data["fisica"],
        data["calculo_cerrado"],
    ]


def test_hides_expired_deleted_and_other_universities(client, auth, data):
    found = set(ids(search(client, auth)))
    assert data["vencido"] not in found
    assert data["eliminado"] not in found
    assert data["otra_universidad"] not in found


def test_group_still_visible_on_its_last_day(client, auth, data, session_factory):
    with session_factory() as db:
        db.get(GrupoEstudio, data["fisica"]).vigente_hasta = today_in_chile()
        db.commit()
    assert data["fisica"] in ids(search(client, auth))


@pytest.mark.parametrize("query", ["calculo", "CÁLCULO", "mat 1620", "mat1620", "  Cálculo II "])
def test_search_by_subject_ignores_accents_case_and_spaces(client, auth, data, query):
    results = search(client, auth, q=query)
    assert data["fisica"] not in ids(results)
    assert data["calculo_presencial"] in ids(results)


def test_unknown_subject_returns_empty(client, auth, data):
    assert search(client, auth, q="química") == []


def test_filter_by_modality(client, auth, data):
    assert ids(search(client, auth, q="calculo", es_online=True)) == [data["calculo_online"]]
    assert data["calculo_online"] not in ids(search(client, auth, es_online=False))


def test_filter_by_day_and_block(client, auth, data):
    # Martes (1) en la tarde
    assert ids(search(client, auth, dia=1, es_manana=False)) == [data["calculo_presencial"]]
    # Varios días: lunes o viernes
    assert ids(search(client, auth, dia=[0, 4])) == [data["calculo_online"], data["fisica"]]


def test_day_and_block_must_match_the_same_schedule(client, auth, data):
    # El grupo online es lunes mañana y miércoles tarde: lunes tarde no calza
    assert data["calculo_online"] not in ids(search(client, auth, dia=0, es_manana=False))
    assert data["calculo_online"] in ids(search(client, auth, dia=2, es_manana=False))


def test_result_shape_hides_place_and_link(client, auth, data):
    group = next(g for g in search(client, auth) if g["id"] == data["calculo_online"])
    assert group == {
        "id": data["calculo_online"],
        "asignatura": {"id": group["asignatura"]["id"], "sigla": "MAT1620", "nombre": "Cálculo II"},
        "descripcion": None,
        "es_online": True,
        "cupo_maximo": 4,
        "miembros": 1,
        "abierto": True,
        "vigente_hasta": group["vigente_hasta"],
        "horarios": [{"dia_semana": 0, "es_manana": True}, {"dia_semana": 2, "es_manana": False}],
    }
    assert "lugar" not in group and "enlace" not in group


def test_counts_members(client, auth, data, session_factory):
    with session_factory() as db:
        user = db.query(Usuario).filter_by(email="camila.perez@uc.cl").one()
        db.add(MiembroGrupo(grupo_id=data["fisica"], usuario_id=user.id))
        db.commit()
    group = next(g for g in search(client, auth) if g["id"] == data["fisica"])
    assert group["miembros"] == 2


def test_rejects_invalid_day(client, auth):
    assert client.get("/api/grupos", params={"dia": 6}, headers=auth).status_code == 422


# --- Detalle del grupo (B07) ---


def detail(client, auth, group_id):
    return client.get(f"/api/grupos/{group_id}", headers=auth)


def join(session_factory, group_id, email):
    """Agrega como integrante al usuario con ese correo."""
    with session_factory() as db:
        user = db.query(Usuario).filter_by(email=email).one()
        db.add(MiembroGrupo(grupo_id=group_id, usuario_id=user.id))
        db.commit()


def test_detail_of_group(client, auth, data):
    response = detail(client, auth, data["calculo_presencial"])

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == data["calculo_presencial"]
    assert body["asignatura"]["sigla"] == "MAT1620"
    assert body["descripcion"] == "Integrales"
    assert (body["es_online"], body["cupo_maximo"], body["miembros"]) == (False, 4, 1)
    assert body["horarios"] == [{"dia_semana": 1, "es_manana": False}]
    assert body["creador"] == "Ana"
    assert body["integrantes"] == [
        {"id": body["integrantes"][0]["id"], "nombre": "Ana", "carrera": None, "es_creador": True}
    ]
    assert body["es_miembro"] is False


def test_detail_hides_place_and_link_from_non_members(client, auth, data):
    presencial = detail(client, auth, data["calculo_presencial"]).json()
    online = detail(client, auth, data["calculo_online"]).json()

    assert (presencial["lugar"], presencial["enlace"]) == (None, None)
    assert (online["lugar"], online["enlace"]) == (None, None)
    # Los integrantes no traen correo
    assert "email" not in presencial["integrantes"][0]


def test_members_see_place_and_link(client, auth, data, session_factory):
    join(session_factory, data["calculo_presencial"], "camila.perez@uc.cl")
    join(session_factory, data["calculo_online"], "camila.perez@uc.cl")

    presencial = detail(client, auth, data["calculo_presencial"]).json()
    online = detail(client, auth, data["calculo_online"]).json()

    assert presencial["es_miembro"] is True
    assert presencial["lugar"] == "Biblioteca"
    assert online["enlace"] == "https://x"
    # El creador primero, después el resto en el orden en que entraron
    assert [(m["nombre"], m["es_creador"]) for m in presencial["integrantes"]] == [
        ("Ana", True),
        ("Camila Pérez", False),
    ]
    assert presencial["miembros"] == 2


def test_detail_of_closed_group(client, auth, data):
    response = detail(client, auth, data["calculo_cerrado"])
    assert response.status_code == 200
    assert response.json()["abierto"] is False


@pytest.mark.parametrize("name", ["vencido", "eliminado", "otra_universidad"])
def test_detail_of_unavailable_group(client, auth, data, name):
    response = detail(client, auth, data[name])
    assert response.status_code == 404


def test_detail_of_missing_group(client, auth, data):
    assert detail(client, auth, 9999).status_code == 404


def test_detail_requires_session(client, data):
    assert client.get(f"/api/grupos/{data['fisica']}").status_code == 401


# --- Crear grupo ---


def new_group(**fields):
    """Cuerpo válido para POST /api/grupos: presencial, de Cálculo II, martes en la tarde."""
    return {
        "sigla": "MAT1620",
        "descripcion": "Repaso para el certamen",
        "es_online": False,
        "lugar": "Biblioteca central",
        "cupo_maximo": 4,
        "vigente_hasta": str(today_in_chile() + timedelta(days=30)),
        "horarios": [{"dia_semana": 1, "es_manana": False}],
        **fields,
    }


def create(client, auth, **fields):
    return client.post("/api/grupos", json=new_group(**fields), headers=auth)


def test_create_group_with_existing_subject(client, auth, data):
    response = create(client, auth, sigla="mat 1620")

    assert response.status_code == 201
    body = response.json()
    assert body["asignatura"]["sigla"] == "MAT1620"
    assert body["asignatura"]["nombre"] == "Cálculo II"
    assert body["creador"] == "Camila Pérez"
    assert body["es_miembro"] is True
    assert body["miembros"] == 1
    assert body["abierto"] is True
    assert body["lugar"] == "Biblioteca central"
    assert body["enlace"] is None


def test_created_group_appears_in_search(client, auth, data):
    group_id = create(client, auth, horarios=[{"dia_semana": 5, "es_manana": True}]).json()["id"]

    assert group_id in ids(search(client, auth, q="calculo", dia=5))


def test_other_users_find_the_group_without_place(client, auth, data, session_factory):
    group_id = create(client, auth).json()["id"]

    # Un segundo usuario de la misma universidad
    client.post(
        "/api/auth/registro",
        json={
            "email": "luis.castro@uc.cl",
            "password": "Clave-segura-1",
            "nombre": "Luis Castro",
            "carrera": "Ingeniería Comercial",
            "anio_ingreso": 2022,
            "acepta_terminos": True,
        },
    )
    with session_factory() as db:
        luis = db.query(Usuario).filter_by(email="luis.castro@uc.cl").one()
        luis.email_verificado_en = now_utc()
        db.commit()
    token = client.post(
        "/api/auth/login", json={"email": "luis.castro@uc.cl", "password": "Clave-segura-1"}
    ).json()["access_token"]
    luis_auth = {"Authorization": f"Bearer {token}"}

    assert group_id in ids(search(client, luis_auth, q="MAT1620"))
    body = detail(client, luis_auth, group_id).json()
    assert body["es_miembro"] is False
    assert body["lugar"] is None


def test_create_group_with_new_subject(client, auth, session_factory):
    response = create(client, auth, sigla="iic2233", nombre_asignatura="  Programación   Avanzada ")

    assert response.status_code == 201
    assert response.json()["asignatura"]["nombre"] == "Programación Avanzada"
    with session_factory() as db:
        subject = db.query(Asignatura).filter_by(sigla="IIC2233").one()
        assert subject.universidad == UC


def test_new_subject_needs_a_name(client, auth):
    response = create(client, auth, sigla="IIC2233")

    assert response.status_code == 404
    assert "IIC2233" in response.json()["detail"]


def test_name_is_ignored_when_subject_exists(client, auth, data):
    response = create(client, auth, nombre_asignatura="Otro nombre")
    assert response.json()["asignatura"]["nombre"] == "Cálculo II"


def test_online_group_keeps_only_the_link(client, auth, data):
    response = create(
        client, auth, es_online=True, enlace="https://meet.google.com/abc", lugar="Biblioteca"
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["es_online"], body["enlace"], body["lugar"]) == (
        True,
        "https://meet.google.com/abc",
        None,
    )


def test_schedule_without_duplicates(client, auth, data):
    horarios = [
        {"dia_semana": 3, "es_manana": False},
        {"dia_semana": 0, "es_manana": True},
        {"dia_semana": 3, "es_manana": False},
    ]
    response = create(client, auth, horarios=horarios)

    assert response.json()["horarios"] == [
        {"dia_semana": 0, "es_manana": True},
        {"dia_semana": 3, "es_manana": False},
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"sigla": "AB"},
        {"sigla": "MAT-1620"},
        {"lugar": None},
        {"lugar": "   "},
        {"es_online": True, "enlace": None},
        {"es_online": True, "enlace": "meet.google.com/abc"},
        {"cupo_maximo": 1},
        {"cupo_maximo": 11},
        {"horarios": []},
        {"horarios": [{"dia_semana": 6, "es_manana": True}]},
        {"vigente_hasta": str(today_in_chile())},
        {"vigente_hasta": str(today_in_chile() - timedelta(days=1))},
        {"descripcion": "x" * 281},
    ],
)
def test_create_group_validates_fields(client, auth, data, fields):
    assert create(client, auth, **fields).status_code == 422


def test_create_group_requires_session(client):
    assert client.post("/api/grupos", json=new_group()).status_code == 401
