from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.models import (
    Asignatura,
    GrupoEstudio,
    HorarioGrupo,
    MiembroGrupo,
    Solicitud,
    TokenCorreo,
    Usuario,
)

NOW = datetime.now(timezone.utc)


@pytest.fixture
def engine(tmp_path):
    # Se crea la base con las migraciones de Alembic, igual que en producción
    url = f"sqlite:///{tmp_path / 'test.db'}"
    config = Config("alembic.ini")
    config.attributes["url"] = url
    command.upgrade(config, "head")
    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def make_user(db, email="ana@uc.cl"):
    user = Usuario(
        email=email,
        password_hash="hash",
        nombre="Ana",
        universidad="UC",
        terminos_aceptados_en=NOW,
    )
    db.add(user)
    db.flush()
    return user


def make_group(db, creator, **overrides):
    subject = db.query(Asignatura).first() or Asignatura(
        sigla="IIC2143", nombre="Ingeniería de Software", universidad="UC"
    )
    fields = dict(
        asignatura=subject,
        creador=creator,
        es_online=False,
        lugar="Biblioteca San Joaquín",
        cupo_maximo=5,
        vigente_hasta=date.today() + timedelta(days=60),
    )
    fields.update(overrides)
    group = GrupoEstudio(**fields)
    db.add(group)
    db.flush()
    return group


def test_migrations_match_models(engine):
    # Si alguien cambia models.py sin crear una migración, este test falla
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_create_group_with_schedule_and_member(db):
    creator = make_user(db)
    group = make_group(db, creator)
    group.horarios.append(HorarioGrupo(dia_semana=0, es_manana=True))
    group.miembros.append(MiembroGrupo(usuario=creator))
    db.commit()

    saved = db.get(GrupoEstudio, group.id)
    assert saved.abierto is True
    assert saved.eliminado_en is None
    assert saved.creado_en is not None
    assert [m.usuario.email for m in saved.miembros] == ["ana@uc.cl"]


def test_email_is_unique(db):
    make_user(db)
    with pytest.raises(IntegrityError):
        make_user(db)


def test_terms_must_be_accepted(db):
    db.add(Usuario(email="ana@uc.cl", password_hash="hash", nombre="Ana", universidad="UC"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_subject_is_unique_per_university(db):
    db.add(Asignatura(sigla="IIC2143", nombre="IngSoft", universidad="UC"))
    db.add(Asignatura(sigla="IIC2143", nombre="IngSoft", universidad="UChile"))
    db.flush()
    db.add(Asignatura(sigla="IIC2143", nombre="Otra", universidad="UC"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_in_person_group_requires_place(db):
    with pytest.raises(IntegrityError):
        make_group(db, make_user(db), lugar=None)


def test_online_group_requires_link(db):
    with pytest.raises(IntegrityError):
        make_group(db, make_user(db), es_online=True, lugar=None)


def test_deleted_group_requires_reason(db):
    with pytest.raises(IntegrityError):
        make_group(db, make_user(db), eliminado_en=NOW)


def test_group_deleted_by_expiration(db):
    group = make_group(db, make_user(db), eliminado_en=NOW, eliminado_por_vigencia=True)
    assert group.eliminado_por_vigencia is True


def test_only_one_pending_request_per_user_and_group(db):
    group = make_group(db, make_user(db))
    student = make_user(db, "beto@uc.cl")
    db.add(Solicitud(grupo=group, usuario=student))
    db.flush()
    db.add(Solicitud(grupo=group, usuario=student))
    with pytest.raises(IntegrityError):
        db.flush()


def test_can_request_again_after_cancelling(db):
    group = make_group(db, make_user(db))
    student = make_user(db, "beto@uc.cl")
    db.add(Solicitud(grupo=group, usuario=student, estado="cancelada", cancelada_en=NOW))
    db.add(Solicitud(grupo=group, usuario=student))
    db.flush()


def test_cancelled_request_requires_date(db):
    group = make_group(db, make_user(db))
    db.add(Solicitud(grupo=group, usuario=make_user(db, "beto@uc.cl"), estado="cancelada"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_token_type_is_validated(db):
    db.add(
        TokenCorreo(
            usuario=make_user(db),
            token_hash="a" * 64,
            tipo="otro",
            expira_en=NOW + timedelta(hours=1),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
