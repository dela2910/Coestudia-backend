from datetime import timedelta

import pytest

from app.models import (
    Asignatura,
    GrupoEstudio,
    HorarioGrupo,
    MiembroGrupo,
    Solicitud,
    Usuario,
    UsuarioAsignatura,
    now_utc,
    today_in_chile,
)
from tests.conftest import UC


def make_user(db, email, career="Ingeniería Civil", university=UC):
    user = Usuario(
        email=email,
        password_hash="x",
        nombre=email.split("@")[0].title(),
        carrera=career,
        universidad=university,
        terminos_aceptados_en=now_utc(),
        email_verificado_en=now_utc(),
    )
    db.add(user)
    return user


@pytest.fixture
def data(session_factory, users):
    """Asignaturas y grupos de ejemplo. `ana` (de `users`) cursa Cálculo II; sus compañeros de
    carrera cursan Física I. Devuelve los ids por nombre."""
    with session_factory() as db:
        ana = db.query(Usuario).filter_by(email="ana@uc.cl").one()
        # Compañeros de carrera de ana (la carrera se compara sin tildes ni mayúsculas)
        peer1 = make_user(db, "peer1@uc.cl", career="ingenieria civil")
        peer2 = make_user(db, "peer2@uc.cl")
        stranger = make_user(db, "stranger@uc.cl", career="Derecho")

        calculo = Asignatura(sigla="MAT1620", nombre="Cálculo II", universidad=UC)
        fisica = Asignatura(sigla="FIS1513", nombre="Física I", universidad=UC)
        historia = Asignatura(sigla="HIS1001", nombre="Historia", universidad=UC)
        algebra = Asignatura(sigla="MAT1203", nombre="Álgebra", universidad=UC)
        otra_u = Asignatura(sigla="MAT1620", nombre="Cálculo II", universidad="Otra U")
        db.add(algebra)

        ana.asignaturas.append(UsuarioAsignatura(asignatura=calculo))
        peer1.asignaturas.append(UsuarioAsignatura(asignatura=fisica))
        peer2.asignaturas.append(UsuarioAsignatura(asignatura=fisica))
        future = today_in_chile() + timedelta(days=30)
        old = now_utc() - timedelta(days=30)

        def group(subject, creator, schedule=((1, False),), members=(), **fields):
            g = GrupoEstudio(
                asignatura=subject,
                creador=creator,
                es_online=False,
                lugar="Biblioteca",
                cupo_maximo=fields.pop("cupo_maximo", 4),
                vigente_hasta=fields.pop("vigente_hasta", future),
                creado_en=fields.pop("creado_en", old),
                **fields,
            )
            g.horarios = [HorarioGrupo(dia_semana=d, es_manana=m) for d, m in schedule]
            g.miembros = [MiembroGrupo(usuario=u) for u in (creator, *members)]
            db.add(g)
            return g

        groups = {
            "calculo": group(calculo, stranger),
            "calculo_con_peer": group(calculo, stranger, members=[peer1]),
            "fisica": group(fisica, stranger, schedule=[(3, True)]),
            "historia": group(historia, stranger, schedule=[(4, True)]),
            "calculo_lleno": group(calculo, stranger, members=[peer2], cupo_maximo=2),
            "calculo_cerrado": group(calculo, stranger, abierto=False),
            "calculo_vencido": group(
                calculo, stranger, vigente_hasta=today_in_chile() - timedelta(days=1)
            ),
            "calculo_eliminado": group(
                calculo, stranger, eliminado_en=now_utc(), eliminado_por_vigencia=False
            ),
            "otra_universidad": group(otra_u, make_user(db, "x@otra.cl", university="Otra U")),
            # ana ya está en este: no se sugiere
            "mio": group(calculo, stranger, members=[ana], schedule=[(0, True)]),
        }
        db.commit()
        ids = {name: g.id for name, g in groups.items()}
        ids["algebra"] = algebra.id
        ids["calculo_asignatura"] = calculo.id
        return ids


def get(client, headers, path, **params):
    response = client.get(path, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ids(results):
    return [g["id"] for g in results]


def test_requires_session(client):
    assert client.get("/api/sugerencias/grupos").status_code == 401
    assert client.get("/api/sugerencias/asignaturas").status_code == 401


def test_suggests_my_subjects_first_then_peer_subjects(client, users, data):
    results = get(client, users["ana"], "/api/sugerencias/grupos")

    assert ids(results) == [
        # Cursa Cálculo II; el que tiene un compañero de carrera va primero
        data["calculo_con_peer"],
        data["calculo"],
        # Física I la cursan 2 compañeros de su carrera
        data["fisica"],
        # Sin relación con ella: al final
        data["historia"],
    ]
    by_id = {g["id"]: g for g in results}
    assert by_id[data["calculo_con_peer"]]["motivos"][:2] == [
        "Cursas MAT1620",
        "1 integrante de tu carrera",
    ]
    assert by_id[data["fisica"]]["motivos"][0] == "Lo cursan 2 estudiantes de tu carrera"
    assert by_id[data["historia"]]["motivos"] == [
        "No choca con tus otros grupos",
    ]
    # Mismo formato que el buscador, sin lugar ni enlace
    assert by_id[data["calculo"]]["asignatura"]["sigla"] == "MAT1620"
    assert "lugar" not in by_id[data["calculo"]]


def test_excludes_full_closed_expired_deleted_mine_and_other_universities(client, users, data):
    results = ids(get(client, users["ana"], "/api/sugerencias/grupos", limite=20))

    for name in (
        "calculo_lleno",
        "calculo_cerrado",
        "calculo_vencido",
        "calculo_eliminado",
        "otra_universidad",
        "mio",
    ):
        assert data[name] not in results


def test_excludes_groups_with_active_or_rejected_requests(client, users, data, session_factory):
    with session_factory() as db:
        ana = db.query(Usuario).filter_by(email="ana@uc.cl").one()
        db.add(Solicitud(grupo_id=data["calculo"], usuario=ana, estado="pendiente"))
        db.add(
            Solicitud(
                grupo_id=data["fisica"], usuario=ana, estado="rechazada", respondida_en=now_utc()
            )
        )
        db.add(
            Solicitud(
                grupo_id=data["historia"], usuario=ana, estado="cancelada", cancelada_en=now_utc()
            )
        )
        db.commit()

    results = ids(get(client, users["ana"], "/api/sugerencias/grupos"))

    assert data["calculo"] not in results
    assert data["fisica"] not in results
    # La canceló: puede volver a interesarle
    assert data["historia"] in results


def test_schedule_clash_goes_last(client, users, data, session_factory):
    with session_factory() as db:
        # ana ya estudia los martes en la tarde: el mismo horario que «calculo»
        group = db.get(GrupoEstudio, data["mio"])
        group.horarios.append(HorarioGrupo(dia_semana=1, es_manana=False))
        db.commit()

    results = get(client, users["ana"], "/api/sugerencias/grupos")
    by_id = {g["id"]: g for g in results}

    assert ids(results).index(data["calculo"]) > ids(results).index(data["calculo_con_peer"])
    assert "No choca con tus otros grupos" not in by_id[data["calculo"]]["motivos"]
    assert "No choca con tus otros grupos" in by_id[data["fisica"]]["motivos"]


def test_new_groups_are_marked(client, users, data, session_factory):
    with session_factory() as db:
        db.get(GrupoEstudio, data["historia"]).creado_en = now_utc()
        db.commit()

    results = get(client, users["ana"], "/api/sugerencias/grupos")
    historia = next(g for g in results if g["id"] == data["historia"])

    assert "Grupo nuevo" in historia["motivos"]


def test_limit(client, users, data):
    assert len(get(client, users["ana"], "/api/sugerencias/grupos", limite=2)) == 2
    response = client.get("/api/sugerencias/grupos", params={"limite": 0}, headers=users["ana"])
    assert response.status_code == 422


def test_user_without_subjects_or_peers_gets_groups_with_spots(client, users, data):
    # «otro» no cursa ninguna asignatura: igual recibe grupos con cupo
    results = get(client, users["otro"], "/api/sugerencias/grupos", limite=20)

    assert set(ids(results)) >= {data["calculo"], data["historia"]}


def test_suggested_subjects_without_query(client, users, data):
    results = get(client, users["ana"], "/api/sugerencias/asignaturas")

    assert [(s["asignatura"]["sigla"], s["grupos_disponibles"], s["motivo"]) for s in results] == [
        ("MAT1620", 2, "Está en tus asignaturas"),
        ("FIS1513", 1, "La cursan 2 estudiantes de tu carrera"),
        ("HIS1001", 1, None),
    ]


def test_suggested_subjects_autocomplete_by_code_or_name(client, users, data):
    by_name = get(client, users["ana"], "/api/sugerencias/asignaturas", q="algebra")
    by_code = get(client, users["ana"], "/api/sugerencias/asignaturas", q="mat 1")

    # Sin grupos todavía, pero calza con lo escrito: puede crear el primero
    assert [(s["asignatura"]["sigla"], s["grupos_disponibles"]) for s in by_name] == [
        ("MAT1203", 0)
    ]
    # La que cursa primero
    assert [s["asignatura"]["sigla"] for s in by_code] == ["MAT1620", "MAT1203"]


def test_suggested_subjects_only_from_my_university(client, users, data):
    results = get(client, users["ana"], "/api/sugerencias/asignaturas", q="calculo")

    assert [s["asignatura"]["id"] for s in results] == [data["calculo_asignatura"]]
