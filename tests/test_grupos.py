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
