from app.models import GrupoEstudio, Usuario
from app.seed import GROUPS, USERS, seed
from tests.test_grupos import auth  # noqa: F401  (fixture)


def test_seed_loads_once(session_factory):
    with session_factory() as db:
        assert seed(db) is True
        assert seed(db) is False
        assert db.query(Usuario).count() == len(USERS)
        assert db.query(GrupoEstudio).count() == len(GROUPS)


def test_seeded_groups_appear_in_search(client, auth, session_factory):  # noqa: F811
    with session_factory() as db:
        seed(db)
    results = client.get("/api/grupos", params={"q": "calculo"}, headers=auth).json()
    assert len(results) == 3
    # El grupo con el cupo lleno queda cerrado y aparece al final
    assert [g["abierto"] for g in results] == [True, True, False]
