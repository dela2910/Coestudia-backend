import pytest

from app.models import Asignatura
from app.routers.perfil import MAX_SUBJECTS
from tests.test_auth import register_and_verify

UC = "Pontificia Universidad Católica de Chile"


@pytest.fixture
def auth(client, outbox):
    """Headers de la sesión de un usuario UC verificado."""
    token = register_and_verify(client, outbox)["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def catalog(session_factory):
    """Asignaturas que ya existen: dos de la UC y una de otra universidad con la misma sigla."""
    with session_factory() as db:
        subjects = {
            "calculo": Asignatura(sigla="MAT1620", nombre="Cálculo II", universidad=UC),
            "fisica": Asignatura(sigla="FIS1513", nombre="Física I", universidad=UC),
            "otra_u": Asignatura(sigla="MAT1620", nombre="Cálculo 2", universidad="Otra U"),
        }
        db.add_all(subjects.values())
        db.commit()
        return {name: s.id for name, s in subjects.items()}


def add(client, auth, sigla, nombre=None):
    body = {"sigla": sigla} if nombre is None else {"sigla": sigla, "nombre": nombre}
    return client.post("/api/perfil/asignaturas", json=body, headers=auth)


def my_codes(client, auth):
    response = client.get("/api/perfil/asignaturas", headers=auth)
    assert response.status_code == 200
    return [s["sigla"] for s in response.json()]


# --- Perfil ---


def test_update_profile(client, auth):
    response = client.put(
        "/api/perfil",
        json={"nombre": "  Camila Rojas ", "carrera": "Física", "anio_ingreso": 2023},
        headers=auth,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nombre"] == "Camila Rojas"
    assert (body["carrera"], body["anio_ingreso"]) == ("Física", 2023)
    # El correo y la universidad no se editan
    assert body["email"] == "camila.perez@uc.cl"
    assert client.get("/api/auth/me", headers=auth).json()["nombre"] == "Camila Rojas"


@pytest.mark.parametrize(
    "changes",
    [{"nombre": "   "}, {"carrera": ""}, {"anio_ingreso": 1980}, {"anio_ingreso": 2999}],
)
def test_update_profile_validates_fields(client, auth, changes):
    data = {"nombre": "Camila", "carrera": "Física", "anio_ingreso": 2023, **changes}
    assert client.put("/api/perfil", json=data, headers=auth).status_code == 422


def test_profile_requires_session(client):
    assert client.get("/api/perfil/asignaturas").status_code == 401
    assert client.put("/api/perfil", json={}).status_code == 401
    assert client.get("/api/asignaturas").status_code == 401


# --- Mis asignaturas ---


def test_new_user_has_no_subjects(client, auth):
    assert my_codes(client, auth) == []


def test_add_existing_subject_normalizes_code(client, auth, catalog):
    response = add(client, auth, " mat 1620 ")

    assert response.status_code == 201
    # La de su universidad, no la de otra con la misma sigla
    assert response.json() == {"id": catalog["calculo"], "sigla": "MAT1620", "nombre": "Cálculo II"}
    assert my_codes(client, auth) == ["MAT1620"]


def test_add_unknown_subject_asks_for_name(client, auth, catalog):
    response = add(client, auth, "IIC2233")

    assert response.status_code == 404
    assert "IIC2233" in response.json()["detail"]
    assert my_codes(client, auth) == []


def test_add_unknown_subject_creates_it(client, auth, catalog, session_factory):
    response = add(client, auth, "iic2233", "  Programación   Avanzada ")

    assert response.status_code == 201
    assert response.json()["nombre"] == "Programación Avanzada"
    with session_factory() as db:
        created = db.get(Asignatura, response.json()["id"])
        assert (created.sigla, created.universidad) == ("IIC2233", UC)
    # Queda en el catálogo para los demás
    codes = [s["sigla"] for s in client.get("/api/asignaturas", headers=auth).json()]
    assert codes == ["FIS1513", "IIC2233", "MAT1620"]


def test_name_is_ignored_when_subject_exists(client, auth, catalog):
    response = add(client, auth, "MAT1620", "Otro nombre")
    assert response.json()["nombre"] == "Cálculo II"


def test_adding_twice_keeps_one(client, auth, catalog):
    add(client, auth, "MAT1620")
    assert add(client, auth, "mat1620").status_code == 201
    assert my_codes(client, auth) == ["MAT1620"]


def test_subjects_are_sorted_by_code(client, auth, catalog):
    add(client, auth, "MAT1620")
    add(client, auth, "FIS1513")
    assert my_codes(client, auth) == ["FIS1513", "MAT1620"]


@pytest.mark.parametrize("sigla", ["", "AB", "MAT-1620", "MAT1620" * 3])
def test_add_rejects_invalid_codes(client, auth, sigla):
    assert add(client, auth, sigla, "Nombre").status_code == 422


def test_subject_limit(client, auth):
    for i in range(MAX_SUBJECTS):
        assert add(client, auth, f"TST{i:04}", "Ramo").status_code == 201

    response = add(client, auth, "TST9999", "Ramo")
    assert response.status_code == 400
    assert len(my_codes(client, auth)) == MAX_SUBJECTS


def test_remove_subject(client, auth, catalog, session_factory):
    add(client, auth, "MAT1620")
    add(client, auth, "FIS1513")

    response = client.delete(f"/api/perfil/asignaturas/{catalog['calculo']}", headers=auth)

    assert response.status_code == 204
    assert my_codes(client, auth) == ["FIS1513"]
    # La asignatura sigue existiendo para los demás
    with session_factory() as db:
        assert db.get(Asignatura, catalog["calculo"]) is not None


def test_remove_subject_not_in_list(client, auth, catalog):
    response = client.delete(f"/api/perfil/asignaturas/{catalog['fisica']}", headers=auth)
    assert response.status_code == 404


# --- Catálogo ---


def test_catalog_only_has_subjects_of_my_university(client, auth, catalog):
    response = client.get("/api/asignaturas", headers=auth)

    assert response.status_code == 200
    assert response.json() == [
        {"id": catalog["fisica"], "sigla": "FIS1513", "nombre": "Física I"},
        {"id": catalog["calculo"], "sigla": "MAT1620", "nombre": "Cálculo II"},
    ]
